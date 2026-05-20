import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from runtime.result_sync import (
    _match_finished_item,
    _merge_registry_entry,
    migrate_result_sync_registry_match_ids,
    register_prediction_result_sync,
    sync_due_prediction_results,
)


class DummyResultManager:
    saved_calls = []

    def __init__(self, base_dir=None):
        self.base_dir = base_dir

    @classmethod
    def reset(cls):
        cls.saved_calls = []

    def load_results(self):
        return []

    def save_result(self, identifier, home_score, away_score, league=None, date_override=None, force=False):
        self.__class__.saved_calls.append(
            {
                "identifier": identifier,
                "home_score": home_score,
                "away_score": away_score,
                "league": league,
                "date_override": date_override,
                "force": force,
            }
        )
        actual_winner = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        return {
            "match_id": "premier_league_20260510_西汉姆联_阿森纳",
            "actual_score": f"{home_score}-{away_score}",
            "actual_winner": actual_winner,
            "refresh": {"accuracy_refreshed": True, "review_learning_refreshed": True},
        }


class DummyExistingResultManager(DummyResultManager):
    def load_results(self):
        return [{"match_id": "premier_league_20260510_西汉姆联_阿森纳"}]


class ResultSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        (self.base_dir / "premier_league").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "world_cup").mkdir(parents=True, exist_ok=True)
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "premier_league" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 23:30 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        registry_payload = {
            "premier_league_20260510_西汉姆联_阿森纳": {
                "match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "external_match_id": "",
                "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
                "registered_at": "2026-05-09T20:09:05.111041",
                "kickoff_source": "fallback_end_of_day",
                "kickoff_at": "2026-05-10T23:59:00",
                "due_at": "2026-05-11T03:59:00",
                "status": "pending",
                "last_prediction_timestamp": "2026-05-09T20:09:04.790761",
                "check_count": 0,
                "last_checked_at": "",
                "result_synced_at": "",
                "last_error": "",
            }
        }
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_due_results_refreshes_schedule_from_teams_and_auto_updates(self):
        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None), patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[
                {
                    "league_code": "premier_league",
                    "date": "2026-05-10",
                    "home_team": "西汉姆联",
                    "away_team": "阿森纳",
                    "home_score": 0,
                    "away_score": 1,
                    "status": "已结束",
                }
            ],
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["due_count"], 1)
        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(
            DummyResultManager.saved_calls,
            [
                {
                    "identifier": "西汉姆联 vs 阿森纳",
                    "home_score": 0,
                    "away_score": 1,
                    "league": "premier_league",
                    "date_override": "2026-05-10",
                    "force": False,
                }
            ],
        )
        registry = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json").read_text(encoding="utf-8")
        )
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["match_time"], "23:30")
        self.assertEqual(entry["kickoff_source"], "scheduled_time")
        self.assertEqual(entry["due_at"], "2026-05-11T03:30:00")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["actual_score"], "0-1")

    def test_sync_prefers_team_date_match_over_conflicting_external_match_id(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["external_match_id"] = "wrong-external-id"
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[
                {
                    "match_id": "day-match",
                    "league_code": "premier_league",
                    "date": "2026-05-10",
                    "home_team": "西汉姆联",
                    "away_team": "阿森纳",
                    "home_score": 0,
                    "away_score": 1,
                    "status": "已结束",
                }
            ],
        ), patch(
            "runtime.result_sync._fetch_match_by_match_id",
            return_value={
                "match_id": "wrong-external-id",
                "league_code": "premier_league",
                "date": "2026-05-10",
                "home_team": "别的主队",
                "away_team": "别的客队",
                "home_score": 9,
                "away_score": 9,
                "status": "已结束",
            },
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(DummyResultManager.saved_calls[0]["home_score"], 0)
        self.assertEqual(DummyResultManager.saved_calls[0]["away_score"], 1)

    def test_match_finished_item_rejects_match_id_hit_with_wrong_teams(self):
        entry = {
            "external_match_id": "wrong-external-id",
            "league_code": "premier_league",
            "home_team": "西汉姆联",
            "away_team": "阿森纳",
        }
        finished = [
            {
                "match_id": "wrong-external-id",
                "league_code": "premier_league",
                "home_team": "别的主队",
                "away_team": "别的客队",
                "home_score": 9,
                "away_score": 9,
                "status": "已结束",
            }
        ]

        matched = _match_finished_item(entry, finished)

        self.assertIsNone(matched)

    def test_sync_prefers_direct_match_result_before_wrong_day_feed(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["external_match_id"] = "1326947"
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch(
            "runtime.result_sync._fetch_match_by_match_id",
            return_value={
                "match_id": "1326947",
                "league_code": "premier_league",
                "date": "2026-05-10",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "home_score": 2,
                "away_score": 0,
                "status": "已结束",
            },
        ) as direct_fetch, patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[
                {
                    "match_id": "wrong-day-match",
                    "league_code": "premier_league",
                    "date": "2026-05-10",
                    "home_team": "别的主队",
                    "away_team": "别的客队",
                    "home_score": 9,
                    "away_score": 9,
                    "status": "已结束",
                }
            ],
        ) as day_fetch, patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(DummyResultManager.saved_calls[0]["home_score"], 2)
        self.assertEqual(DummyResultManager.saved_calls[0]["away_score"], 0)
        direct_fetch.assert_called_once()
        day_fetch.assert_not_called()

    def test_register_prediction_result_sync_prefers_canonical_teams_match_id(self):
        entry = register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "1326947",
                "external_match_id": "1326947",
                "internal_match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
            },
        )

        self.assertEqual(entry["match_id"], "premier_league_20260510_西汉姆联_阿森纳")
        self.assertEqual(entry["teams_match_id"], "premier_league_20260510_西汉姆联_阿森纳")
        self.assertEqual(entry["external_match_id"], "1326947")

        registry = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json").read_text(encoding="utf-8")
        )
        self.assertIn("premier_league_20260510_西汉姆联_阿森纳", registry)
        self.assertNotIn("1326947", registry)

    def test_refresh_registry_schedule_from_teams_falls_back_to_team_match_when_date_shifted_later(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 00:30 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
            },
        )

        registry = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json").read_text(encoding="utf-8")
        )
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["match_date"], "2026-05-11")
        self.assertEqual(entry["match_time"], "00:30")
        self.assertEqual(entry["kickoff_at"], "2026-05-11T00:30:00")
        self.assertEqual(entry["due_at"], "2026-05-11T04:30:00")

    def test_refresh_registry_schedule_from_teams_rejects_multiple_nearby_candidates(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-09 | 20:00 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                    "| 2026-05-11 | 00:30 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
            },
        )

        registry = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json").read_text(encoding="utf-8")
        )
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["match_date"], "2026-05-10")
        self.assertEqual(entry["match_time"], "23:30")

    def test_refresh_registry_schedule_from_teams_rejects_large_date_gap(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-12 | 00:30 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
            },
        )

        registry = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json").read_text(encoding="utf-8")
        )
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["match_date"], "2026-05-10")
        self.assertEqual(entry["match_time"], "23:30")

    def test_merge_registry_entry_prefers_later_schedule_source(self):
        primary = {
            "match_date": "2026-05-10",
            "match_time": "20:00",
            "kickoff_at": "2026-05-10T20:00:00",
            "due_at": "2026-05-11T00:00:00",
            "league_code": "premier_league",
            "home_team": "西汉姆联",
            "away_team": "阿森纳",
            "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
            "check_count": 1,
        }
        secondary = {
            "match_date": "2026-05-10",
            "match_time": "23:30",
            "kickoff_at": "2026-05-10T23:30:00",
            "due_at": "2026-05-11T03:30:00",
            "league_code": "premier_league",
            "home_team": "西汉姆联",
            "away_team": "阿森纳",
            "teams_match_id": "premier_league_20260510_西汉姆联_阿森纳",
            "check_count": 0,
        }

        merged = _merge_registry_entry(primary, secondary)

        self.assertEqual(merged["match_time"], "23:30")
        self.assertEqual(merged["kickoff_at"], "2026-05-10T23:30:00")
        self.assertEqual(merged["due_at"], "2026-05-11T03:30:00")

    def test_migrate_result_sync_registry_match_ids_repairs_invalid_teams_match_id(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = {
            "1326947": {
                "match_id": "1326947",
                "external_match_id": "1326947",
                "teams_match_id": "1326947",
                "internal_match_id": "1326947",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
                "registered_at": "2026-05-09T20:09:05.111041",
                "kickoff_source": "scheduled_time",
                "kickoff_at": "2026-05-10T23:30:00",
                "due_at": "2026-05-11T03:30:00",
                "status": "pending",
                "last_prediction_timestamp": "2026-05-09T20:09:04.790761",
                "check_count": 0,
                "last_checked_at": "",
                "result_synced_at": "",
                "last_error": "",
            }
        }
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report = migrate_result_sync_registry_match_ids(str(self.base_dir))

        self.assertGreaterEqual(report["migrated_entries"], 1)
        registry = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        self.assertIn("premier_league_20260510_西汉姆联_阿森纳", registry)
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["teams_match_id"], "premier_league_20260510_西汉姆联_阿森纳")
        self.assertEqual(entry["external_match_id"], "1326947")
        self.assertNotIn("1326947", registry)

    def test_sync_due_results_recanonicalizes_stale_teams_match_id_and_completes(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 00:30 | 西汉姆联 | - | 阿森纳 | 进行中；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = {
            "premier_league_20260510_西汉姆联_切尔西": {
                "match_id": "premier_league_20260510_西汉姆联_切尔西",
                "external_match_id": "",
                "teams_match_id": "premier_league_20260510_西汉姆联_切尔西",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "阿森纳",
                "prediction": "主胜",
                "confidence": 0.41,
                "registered_at": "2026-05-09T20:09:05.111041",
                "kickoff_source": "scheduled_time",
                "kickoff_at": "2026-05-10T23:30:00",
                "due_at": "2026-05-11T03:30:00",
                "status": "pending",
                "last_prediction_timestamp": "2026-05-09T20:09:04.790761",
                "check_count": 0,
                "last_checked_at": "",
                "result_synced_at": "",
                "last_error": "",
            }
        }
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None), patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[
                {
                    "league_code": "premier_league",
                    "date": "2026-05-11",
                    "home_team": "西汉姆联",
                    "away_team": "阿森纳",
                    "home_score": 0,
                    "away_score": 1,
                    "status": "已结束",
                }
            ],
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertTrue(report["updates"][0]["rekeyed"])
        registry = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        self.assertIn("premier_league_20260511_西汉姆联_阿森纳", registry)
        self.assertNotIn("premier_league_20260510_西汉姆联_切尔西", registry)
        entry = registry["premier_league_20260511_西汉姆联_阿森纳"]
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["teams_match_id"], "premier_league_20260511_西汉姆联_阿森纳")
        self.assertEqual(entry["actual_score"], "0-1")

    def test_sync_due_results_marks_missing_teams_row_as_mismatch(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = {
            "premier_league_20260510_西汉姆联_切尔西": {
                "match_id": "premier_league_20260510_西汉姆联_切尔西",
                "external_match_id": "",
                "teams_match_id": "premier_league_20260510_西汉姆联_切尔西",
                "league_code": "premier_league",
                "league_name": "英超",
                "match_date": "2026-05-10",
                "match_time": "23:30",
                "home_team": "西汉姆联",
                "away_team": "切尔西",
                "prediction": "主胜",
                "confidence": 0.41,
                "registered_at": "2026-05-09T20:09:05.111041",
                "kickoff_source": "scheduled_time",
                "kickoff_at": "2026-05-10T23:30:00",
                "due_at": "2026-05-11T03:30:00",
                "status": "pending",
                "last_prediction_timestamp": "2026-05-09T20:09:04.790761",
                "check_count": 0,
                "last_checked_at": "",
                "result_synced_at": "",
                "last_error": "",
            }
        }
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None), patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 0)
        registry = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        entry = registry["premier_league_20260510_西汉姆联_切尔西"]
        self.assertEqual(entry["status"], "mismatch")
        self.assertEqual(entry["last_error"], "teams_row_missing")
        self.assertEqual(DummyResultManager.saved_calls, [])

    def test_sync_due_results_marks_external_match_id_team_mismatch(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["external_match_id"] = "wrong-external-id"
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch(
            "runtime.result_sync._fetch_match_by_match_id",
            return_value={
                "match_id": "wrong-external-id",
                "league_code": "premier_league",
                "date": "2026-05-10",
                "home_team": "别的主队",
                "away_team": "别的客队",
                "home_score": 9,
                "away_score": 9,
                "status": "已结束",
            },
        ), patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 0)
        registry = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["status"], "mismatch")
        self.assertEqual(entry["last_error"], "external_match_id_team_mismatch")
        self.assertEqual(DummyResultManager.saved_calls, [])

    def test_sync_due_results_closes_from_finished_teams_row_without_external_fetch(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 23:30 | 西汉姆联 | 0-1 | 阿森纳 | 已完赛；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None) as direct_fetch, patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ) as day_fetch, patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(DummyResultManager.saved_calls[0]["home_score"], 0)
        self.assertEqual(DummyResultManager.saved_calls[0]["away_score"], 1)
        direct_fetch.assert_not_called()
        day_fetch.assert_not_called()

    def test_sync_due_results_retries_mismatch_when_teams_row_now_finished(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 23:30 | 西汉姆联 | 2-0 | 阿森纳 | 已完赛；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["status"] = "mismatch"
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["last_error"] = "external_match_id_team_mismatch"
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None), patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ), patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        registry = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        entry = registry["premier_league_20260510_西汉姆联_阿森纳"]
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["actual_score"], "2-0")

    def test_sync_due_results_prefers_finished_teams_row_over_wrong_external_match_id(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 23:30 | 西汉姆联 | 1-1 | 阿森纳 | 已完赛；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        registry_payload = json.loads((runtime_dir / "result_sync_registry.json").read_text(encoding="utf-8"))
        registry_payload["premier_league_20260510_西汉姆联_阿森纳"]["external_match_id"] = "wrong-external-id"
        (runtime_dir / "result_sync_registry.json").write_text(
            json.dumps(registry_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DummyResultManager.reset()

        with patch(
            "runtime.result_sync._fetch_match_by_match_id",
            return_value={
                "match_id": "wrong-external-id",
                "league_code": "premier_league",
                "date": "2026-05-10",
                "home_team": "别的主队",
                "away_team": "别的客队",
                "home_score": 9,
                "away_score": 9,
                "status": "已结束",
            },
        ) as direct_fetch, patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ) as day_fetch, patch("result_manager.ResultManager", DummyResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(DummyResultManager.saved_calls[0]["home_score"], 1)
        self.assertEqual(DummyResultManager.saved_calls[0]["away_score"], 1)
        direct_fetch.assert_not_called()
        day_fetch.assert_not_called()

    def test_sync_due_results_existing_result_still_closes_from_finished_teams_row(self):
        teams_file = self.base_dir / "premier_league" / "teams_2025-26.md"
        teams_file.write_text(
            "\n".join(
                [
                    "### 第36轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 23:30 | 西汉姆联 | 3-1 | 阿森纳 | 已完赛；预测:主胜 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        DummyExistingResultManager.reset()

        with patch("runtime.result_sync._fetch_match_by_match_id", return_value=None) as direct_fetch, patch(
            "runtime.result_sync._fetch_finished_matches",
            return_value=[],
        ) as day_fetch, patch("result_manager.ResultManager", DummyExistingResultManager):
            report = sync_due_prediction_results(
                base_dir=str(self.base_dir),
                now=datetime(2026, 5, 11, 11, 0, 0),
                limit=20,
            )

        self.assertEqual(report["updated_count"], 1)
        self.assertEqual(DummyExistingResultManager.saved_calls[0]["home_score"], 3)
        self.assertEqual(DummyExistingResultManager.saved_calls[0]["away_score"], 1)
        direct_fetch.assert_not_called()
        day_fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
