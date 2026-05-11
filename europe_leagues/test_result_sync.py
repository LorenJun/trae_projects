import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from runtime.result_sync import sync_due_prediction_results


class DummyResultManager:
    saved_calls = []
    accuracy_refreshes = 0

    def __init__(self, base_dir=None):
        self.base_dir = base_dir

    @classmethod
    def reset(cls):
        cls.saved_calls = []
        cls.accuracy_refreshes = 0

    def load_results(self):
        return []

    def save_result(self, identifier, home_score, away_score, league=None, date_override=None):
        self.__class__.saved_calls.append(
            {
                "identifier": identifier,
                "home_score": home_score,
                "away_score": away_score,
                "league": league,
                "date_override": date_override,
            }
        )
        actual_winner = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
        return {
            "match_id": "premier_league_20260510_西汉姆联_阿森纳",
            "actual_score": f"{home_score}-{away_score}",
            "actual_winner": actual_winner,
        }

    def update_accuracy_stats(self):
        self.__class__.accuracy_refreshes += 1
        return {"overall": {}}


class ResultSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        (self.base_dir / "premier_league").mkdir(parents=True, exist_ok=True)
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
                }
            ],
        )
        self.assertEqual(DummyResultManager.accuracy_refreshes, 1)

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


if __name__ == "__main__":
    unittest.main()
