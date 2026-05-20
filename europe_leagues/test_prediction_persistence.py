import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domain.live import LiveRefreshService
from domain.persistence import PredictionPersistenceService
from enhanced_prediction_workflow import EnhancedPredictor
from import_players_from_csv import import_csv
from runtime.memory_samples import build_prediction_memory_samples
from runtime.paths import EuropeLeaguesPaths


class PredictionPersistenceSideEffectTest(unittest.TestCase):
    def test_persist_prediction_batch_archives_updates_memory_registers_result_sync_and_refreshes_accuracy(self):
        class DummyResultManager:
            def __init__(self):
                self.refresh_count = 0

            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                events.append(("archive", result["home_team"], result["away_team"], league_code))
                return {}

            def update_accuracy_stats(self):
                self.refresh_count += 1
                return {"overall": {}}

        manager = DummyResultManager()
        service = PredictionPersistenceService(base_dir="/tmp", cache=None, result_manager=manager)
        events = []

        def fake_update_memory(result):
            events.append(("memory", result["home_team"], result["away_team"]))

        def fake_register(base_dir, result):
            events.append(("sync", base_dir, result["home_team"], result["away_team"]))
            return dict(result)

        with patch.object(service, "update_prediction_memory", side_effect=fake_update_memory) as mock_memory, patch(
            "domain.persistence.register_prediction_result_sync", side_effect=fake_register
        ) as mock_register, patch.object(manager, "update_accuracy_stats", wraps=manager.update_accuracy_stats) as mock_refresh:
            summary = service.persist_prediction_batch(
                [
                    {"match_id": "a", "match_date": "2026-05-18", "home_team": "阿森纳", "away_team": "切尔西", "prediction": "主胜"},
                    {"match_id": "b", "match_date": "2026-05-18", "home_team": "曼联", "away_team": "利物浦", "prediction": "平局"},
                ],
                "premier_league",
            )

        self.assertEqual(manager.refresh_count, 1)
        mock_refresh.assert_called_once_with()
        self.assertEqual(mock_memory.call_count, 2)
        self.assertEqual(mock_register.call_count, 2)
        self.assertEqual(
            events,
            [
                ("archive", "阿森纳", "切尔西", "premier_league"),
                ("memory", "阿森纳", "切尔西"),
                ("sync", "/tmp", "阿森纳", "切尔西"),
                ("archive", "曼联", "利物浦", "premier_league"),
                ("memory", "曼联", "利物浦"),
                ("sync", "/tmp", "曼联", "利物浦"),
            ],
        )
        self.assertTrue(summary["accuracy_refreshed"])
        self.assertTrue(summary["persisted"]["batch_mode"])
        self.assertTrue(summary["persisted"]["archived"])
        self.assertTrue(summary["persisted"]["memory_updated"])
        self.assertTrue(summary["persisted"]["result_sync_registered"])
        self.assertEqual(summary["persisted"]["archive_count"], 2)
        self.assertEqual(summary["persisted"]["memory_update_count"], 2)
        self.assertEqual(summary["persisted"]["result_sync_registration_count"], 2)

    def test_persist_prediction_batch_skips_invalid_items_and_counts_runtime_only_archives(self):
        class DummyResultManager:
            def __init__(self):
                self.refresh_count = 0

            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                archived_ids.append(result["internal_match_id"])
                return {}

            def update_accuracy_stats(self):
                self.refresh_count += 1
                return {"overall": {}}

        manager = DummyResultManager()
        service = PredictionPersistenceService(base_dir="/tmp", cache=None, result_manager=manager)
        archived_ids = []
        memory_calls = []
        sync_calls = []

        def fake_update_memory(result):
            memory_calls.append(result["internal_match_id"])

        def fake_register(base_dir, result):
            sync_calls.append(result["internal_match_id"])
            return dict(result)

        with patch.object(service, "update_prediction_memory", side_effect=fake_update_memory), patch(
            "domain.persistence.register_prediction_result_sync", side_effect=fake_register
        ):
            summary = service.persist_prediction_batch(
                [
                    None,
                    {"prediction_blocked": True, "home_team": "跳过主队", "away_team": "跳过客队", "prediction": "主胜"},
                    {"home_team": "缺日期主队", "away_team": "缺日期客队"},
                    {"match_id": "runtime-1", "match_date": "2026-05-19", "home_team": "罗马", "away_team": "勒沃库森", "prediction": "平局"},
                ],
                "europa_league",
            )

        self.assertEqual(manager.refresh_count, 1)
        self.assertEqual(archived_ids, ["runtime-1"])
        self.assertEqual(memory_calls, ["runtime-1"])
        self.assertEqual(sync_calls, ["runtime-1"])
        self.assertTrue(summary["persisted"]["archived"])
        self.assertEqual(summary["persisted"]["archive_count"], 1)
        self.assertEqual(summary["persisted"]["memory_update_count"], 1)
        self.assertEqual(summary["persisted"]["result_sync_registration_count"], 1)

    def test_generate_prediction_report_uses_batch_level_side_effects_only(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)

        class DummySnapshotRepository:
            def get_matches_from_odds_snapshots(self, league_code, match_date):
                return [{"home_team": "阿斯顿维拉", "away_team": "利物浦", "current_odds": {}}]

            def get_matches_from_odds_history(self, league_code, match_date):
                return []

            def fill_missing_current_odds(self, league_code, match_date, matches):
                return matches

        class DummyReportingService:
            def get_sample_matches(self, league_code):
                return []

        class DummyLiveRefreshService:
            def refresh_report_match_odds(self, **kwargs):
                return kwargs["current_odds"] or {}

        class DummyPersistenceService:
            def __init__(self):
                self.batch_calls = []

            def persist_prediction_batch(self, predictions, league_code):
                self.batch_calls.append((league_code, len(predictions)))
                return {
                    "prediction_count": len(predictions),
                    "accuracy_refreshed": True,
                    "persisted": {
                        "enabled": True,
                        "archived": False,
                        "memory_updated": True,
                        "result_sync_registered": True,
                        "batch_mode": True,
                        "memory_update_count": len(predictions),
                        "result_sync_registration_count": len(predictions),
                    },
                }

        class DummyWriteback:
            def __init__(self):
                self.calls = []

            def teams_file_path(self, league_code):
                self.calls.append(("path", league_code))
                return "/tmp/premier_league_teams.md"

            def write_predictions(self, league_code, match_date, predictions):
                self.calls.append(("write_batch", league_code, match_date, len(predictions)))
                return 1

        predict_calls = []

        def fake_predict_match(home_team, away_team, league_code, match_date, **kwargs):
            predict_calls.append(kwargs)
            return {"home_team": home_team, "away_team": away_team, "prediction": "主胜"}

        predictor.snapshot_repository = DummySnapshotRepository()
        predictor.reporting_service = DummyReportingService()
        predictor.live_refresh_service = DummyLiveRefreshService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()
        predictor.predict_match = fake_predict_match
        predictor._load_schedule_matches = lambda _league_code, _match_date: []

        with patch("enhanced_prediction_workflow.os.path.exists", return_value=True):
            report = predictor.generate_prediction_report(
                "premier_league",
                "2026-05-16",
                persist=True,
                write_teams=True,
            )

        self.assertEqual(len(predict_calls), 1)
        self.assertFalse(predict_calls[0]["persist"])
        self.assertEqual(predictor.writeback.calls, [("path", "premier_league"), ("write_batch", "premier_league", "2026-05-16", 1)])
        self.assertEqual(predictor.persistence_service.batch_calls, [("premier_league", 1)])
        self.assertTrue(report["teams_updated"])
        self.assertEqual(report["teams_file"], "/tmp/premier_league_teams.md")
        self.assertTrue(report["accuracy_refreshed"])
        self.assertTrue(report["persisted"]["memory_updated"])
        self.assertTrue(report["persisted"]["result_sync_registered"])
        self.assertEqual(report["persisted"]["memory_update_count"], 1)
        self.assertEqual(report["persisted"]["result_sync_registration_count"], 1)

    def test_generate_prediction_report_does_not_mark_teams_updated_when_writeback_changes_zero_rows(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)

        class DummySnapshotRepository:
            def get_matches_from_odds_snapshots(self, league_code, match_date):
                return [{"home_team": "阿斯顿维拉", "away_team": "利物浦", "current_odds": {}}]

            def get_matches_from_odds_history(self, league_code, match_date):
                return []

            def fill_missing_current_odds(self, league_code, match_date, matches):
                return matches

        class DummyReportingService:
            def get_sample_matches(self, league_code):
                return []

        class DummyLiveRefreshService:
            def refresh_report_match_odds(self, **kwargs):
                return kwargs["current_odds"] or {}

        class DummyPersistenceService:
            def persist_prediction_batch(self, predictions, league_code):
                return {
                    "prediction_count": len(predictions),
                    "accuracy_refreshed": True,
                    "persisted": {
                        "enabled": True,
                        "archived": False,
                        "memory_updated": True,
                        "result_sync_registered": True,
                    },
                }

        class DummyWriteback:
            def __init__(self):
                self.calls = []

            def teams_file_path(self, league_code):
                self.calls.append(("path", league_code))
                return "/tmp/premier_league_teams.md"

            def write_predictions(self, league_code, match_date, predictions):
                self.calls.append(("write_batch", league_code, match_date, len(predictions)))
                return 0

        predictor.snapshot_repository = DummySnapshotRepository()
        predictor.reporting_service = DummyReportingService()
        predictor.live_refresh_service = DummyLiveRefreshService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()
        predictor.predict_match = lambda home_team, away_team, league_code, match_date, **kwargs: {
            "home_team": home_team,
            "away_team": away_team,
            "prediction": "主胜",
        }
        predictor._load_schedule_matches = lambda _league_code, _match_date: []

        with patch("enhanced_prediction_workflow.os.path.exists", return_value=True):
            report = predictor.generate_prediction_report(
                "premier_league",
                "2026-05-16",
                persist=True,
                write_teams=True,
            )

        self.assertEqual(predictor.writeback.calls, [("path", "premier_league"), ("write_batch", "premier_league", "2026-05-16", 1)])
        self.assertFalse(report["teams_updated"])
        self.assertIsNone(report["teams_file"])
        self.assertTrue(report["accuracy_refreshed"])

    def test_generate_prediction_report_no_write_skips_batch_side_effects(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)

        class DummySnapshotRepository:
            def get_matches_from_odds_snapshots(self, league_code, match_date):
                return [{"home_team": "阿斯顿维拉", "away_team": "利物浦", "current_odds": {}}]

            def get_matches_from_odds_history(self, league_code, match_date):
                return []

            def fill_missing_current_odds(self, league_code, match_date, matches):
                return matches

        class DummyReportingService:
            def get_sample_matches(self, league_code):
                return []

        class DummyLiveRefreshService:
            def refresh_report_match_odds(self, **kwargs):
                return kwargs["current_odds"] or {}

        class DummyPersistenceService:
            def persist_prediction_batch(self, predictions, league_code):
                raise AssertionError("should not persist batch when no_write")

        class DummyWriteback:
            def teams_file_path(self, league_code):
                return "/tmp/premier_league_teams.md"

            def write_predictions(self, league_code, match_date, predictions):
                raise AssertionError("should not write teams when no_write")

        predict_calls = []

        def fake_predict_match(home_team, away_team, league_code, match_date, **kwargs):
            predict_calls.append(kwargs)
            return {"home_team": home_team, "away_team": away_team, "prediction": "主胜"}

        predictor.snapshot_repository = DummySnapshotRepository()
        predictor.reporting_service = DummyReportingService()
        predictor.live_refresh_service = DummyLiveRefreshService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()
        predictor.predict_match = fake_predict_match
        predictor._load_schedule_matches = lambda _league_code, _match_date: []

        with patch("enhanced_prediction_workflow.os.path.exists", return_value=True):
            report = predictor.generate_prediction_report(
                "premier_league",
                "2026-05-16",
                persist=False,
                write_teams=False,
            )

        self.assertEqual(len(predict_calls), 1)
        self.assertFalse(predict_calls[0]["persist"])
        self.assertFalse(report["teams_updated"])
        self.assertFalse(report["accuracy_refreshed"])
        self.assertEqual(report["persisted"], {"enabled": False, "archived": False, "memory_updated": False, "result_sync_registered": False})

    def test_generate_prediction_report_prefers_schedule_matches_over_snapshot_count(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)

        class DummySnapshotRepository:
            def __init__(self):
                self.fill_calls = []
                self.snapshot_calls = 0
                self.history_calls = 0

            def get_matches_from_odds_snapshots(self, league_code, match_date):
                self.snapshot_calls += 1
                return [{"home_team": "仅快照场", "away_team": "不应被使用", "current_odds": {}}]

            def get_matches_from_odds_history(self, league_code, match_date):
                self.history_calls += 1
                return []

            def fill_missing_current_odds(self, league_code, match_date, matches):
                self.fill_calls.append((league_code, match_date, len(matches)))
                enriched = []
                for index, match in enumerate(matches, start=1):
                    clone = dict(match)
                    clone["current_odds"] = {"source": f"filled-{index}"}
                    enriched.append(clone)
                return enriched

        class DummyReportingService:
            def get_sample_matches(self, league_code):
                raise AssertionError("sample matches should not be used when schedule exists")

        class DummyLiveRefreshService:
            def refresh_report_match_odds(self, **kwargs):
                return kwargs["current_odds"] or {}

        class DummyPersistenceService:
            def persist_prediction_batch(self, predictions, league_code):
                return {
                    "prediction_count": len(predictions),
                    "accuracy_refreshed": True,
                    "persisted": {
                        "enabled": True,
                        "archived": False,
                        "memory_updated": True,
                        "result_sync_registered": True,
                        "batch_mode": True,
                        "memory_update_count": len(predictions),
                        "result_sync_registration_count": len(predictions),
                    },
                }

        class DummyWriteback:
            def teams_file_path(self, league_code):
                return "/tmp/la_liga_teams.md"

            def write_predictions(self, league_code, match_date, predictions):
                return len(predictions)

        predict_calls = []

        def fake_predict_match(home_team, away_team, league_code, match_date, **kwargs):
            predict_calls.append(
                {
                    "home_team": home_team,
                    "away_team": away_team,
                    "match_time": kwargs.get("match_time"),
                    "match_id": kwargs.get("match_id"),
                    "current_odds": kwargs.get("current_odds"),
                    "persist": kwargs.get("persist"),
                }
            )
            return {"home_team": home_team, "away_team": away_team, "prediction": "主胜"}

        predictor.snapshot_repository = DummySnapshotRepository()
        predictor.reporting_service = DummyReportingService()
        predictor.live_refresh_service = DummyLiveRefreshService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()
        predictor.predict_match = fake_predict_match
        predictor._load_schedule_matches = lambda _league_code, _match_date: [
            {"home_team": f"主队{i}", "away_team": f"客队{i}", "match_time": f"0{i}:00", "match_id": f"m{i}"}
            for i in range(1, 11)
        ]

        with patch("enhanced_prediction_workflow.os.path.exists", return_value=True):
            report = predictor.generate_prediction_report(
                "la_liga",
                "2026-05-18",
                persist=True,
                write_teams=True,
            )

        self.assertEqual(report["prediction_count"], 10)
        self.assertEqual(len(predict_calls), 10)
        self.assertEqual(predictor.snapshot_repository.fill_calls, [("la_liga", "2026-05-18", 10)])
        self.assertEqual(predictor.snapshot_repository.snapshot_calls, 0)
        self.assertEqual(predictor.snapshot_repository.history_calls, 0)
        self.assertEqual(predict_calls[0]["current_odds"], {"source": "filled-1"})
        self.assertEqual(predict_calls[-1]["match_id"], "m10")
        self.assertTrue(all(call["persist"] is False for call in predict_calls))

    def test_load_schedule_matches_reads_runtime_schedule_before_snapshot_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir).resolve()
            predictor = EnhancedPredictor.__new__(EnhancedPredictor)
            predictor.base_dir = str(base_dir)
            predictor.paths = EuropeLeaguesPaths.from_base_dir(base_dir)
            schedule_dir = predictor.paths.schedules_dir / "la_liga"
            schedule_dir.mkdir(parents=True, exist_ok=True)
            (schedule_dir / "2026-05-18.json").write_text(
                json.dumps(
                    {
                        "matches": [
                            {
                                "match_id": "1326947",
                                "home_team": "巴塞罗那",
                                "away_team": "皇家贝蒂斯",
                                "kickoff_time": "03:15",
                            },
                            {
                                "match_id": "1326948",
                                "home_team": "埃尔切",
                                "away_team": "赫塔费",
                                "time": "01:00",
                            },
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            matches = predictor._load_schedule_matches("la_liga", "2026-05-18")

        self.assertEqual(
            matches,
            [
                {
                    "home_team": "巴塞罗那",
                    "away_team": "皇家贝蒂斯",
                    "match_time": "03:15",
                    "match_id": "1326947",
                    "current_odds": None,
                    "_source": "okooo_schedule",
                },
                {
                    "home_team": "埃尔切",
                    "away_team": "赫塔费",
                    "match_time": "01:00",
                    "match_id": "1326948",
                    "current_odds": None,
                    "_source": "okooo_schedule",
                },
            ],
        )

    def test_load_schedule_matches_falls_back_to_unfinished_teams_rows_for_match_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir).resolve()
            predictor = EnhancedPredictor.__new__(EnhancedPredictor)
            predictor.base_dir = str(base_dir)
            predictor.paths = EuropeLeaguesPaths.from_base_dir(base_dir)
            teams_dir = base_dir / "la_liga"
            teams_dir.mkdir(parents=True, exist_ok=True)
            predictor.paths.teams_file("la_liga").write_text(
                "\n".join(
                    [
                        "### 第37轮",
                        "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                        "|-----|------|-----|------|-----|------|",
                        "| 2026-05-18 | 01:00 | 埃尔切 | - | 赫塔费 | 进行中 |",
                        "| 2026-05-18 | 03:15 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中；预测:主胜 |",
                        "| 2026-05-18 | 22:00 | 已完赛主队 | 2-1 | 已完赛客队 | 已结束 |",
                        "",
                        "### 第38轮",
                        "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                        "|-----|------|-----|------|-----|------|",
                        "| 2026-05-25 | 03:00 | 比利亚雷亚尔 | - | 马德里竞技 | 进行中 |",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            matches = predictor._load_schedule_matches("la_liga", "2026-05-18")

        self.assertEqual(
            matches,
            [
                {
                    "home_team": "埃尔切",
                    "away_team": "赫塔费",
                    "match_time": "01:00",
                    "current_odds": None,
                    "_source": "teams_markdown",
                },
                {
                    "home_team": "巴塞罗那",
                    "away_team": "皇家贝蒂斯",
                    "match_time": "03:15",
                    "current_odds": None,
                    "_source": "teams_markdown",
                },
            ],
        )

    def test_predict_match_writes_single_league_sot_prediction_to_teams_file(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)
        predictor.runtime_profile = {"profile": "test"}

        class DummyCache:
            def get(self, *_args, **_kwargs):
                return None

        class DummyLiveRefreshService:
            def prepare_prediction_inputs(self, **_kwargs):
                return {
                    "match_date": "2026-05-16",
                    "analysis_context": {},
                    "current_odds": {},
                    "realtime": {"context_applied": {}},
                    "cache_params": {"k": "v"},
                }

        class DummyReviewLearningService:
            def build_prediction_context(self, **_kwargs):
                return {"available": False, "league_review": {"league_tags": []}}

        class DummyInferenceService:
            def run(self, **_kwargs):
                return {
                    "applied_weights": {},
                    "home_strength": {},
                    "away_strength": {},
                    "match_intelligence": {},
                    "strength_diff": 0.1,
                    "final_probabilities": {"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
                    "ranked_probabilities": [("主胜", 0.5), ("平局", 0.3), ("客胜", 0.2)],
                    "main_prediction": "主胜",
                    "confidence": 0.5,
                    "historical_odds_reference": {},
                    "upset_potential": {},
                    "top_scores": [("1-0", 0.12), ("1-1", 0.11)],
                    "total_goals": {},
                    "home_lambda": 1.4,
                    "away_lambda": 0.9,
                    "over_under": {"available": True, "line": 2.5, "over": 0.45, "under": 0.55},
                    "fusion_result": {},
                    "lightweight_rag_decision": {},
                }

        class DummyRagService:
            def retrieve_match_memory(self, **_kwargs):
                return {"summary": {}, "similar_cases": [], "market_cases": [], "upset_cases": []}

            def build_lightweight_decision(self, **_kwargs):
                return {}

        class DummyPostprocessService:
            def build_market_snapshot(self, _current_odds):
                return {}

            def build_prediction_result(self, **kwargs):
                return {
                    "match_id": "1296096",
                    "league_code": kwargs["league_code"],
                    "league_name": "英超",
                    "match_date": kwargs["match_date"],
                    "match_time": kwargs["match_time"],
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "prediction": "主胜",
                    "predicted_winner": "home",
                    "confidence": kwargs["confidence"],
                    "final_probabilities": kwargs["final_probabilities"],
                    "top_scores": kwargs["top_scores"],
                    "over_under": kwargs["over_under"],
                    "realtime": kwargs["realtime"],
                    "analysis_context": kwargs["analysis_context"],
                    "runtime_profile": kwargs["runtime_profile"],
                }

        class DummyPersistenceService:
            def __init__(self):
                self.calls = []

            def persist_prediction(self, cache_name, cache_params, result, league_code):
                self.calls.append((cache_name, cache_params, league_code, result["home_team"], result["away_team"]))
                result["storage_mode"] = "league_sot"
                result["teams_match_id"] = "premier_league_20260516_阿斯顿维拉_利物浦"
                result["persisted"] = {"enabled": True, "archived": True, "memory_updated": True}
                return result

        class DummyWriteback:
            def __init__(self):
                self.calls = []

            def teams_file_path(self, league_code):
                self.calls.append(("path", league_code))
                return "/tmp/premier_league_teams.md"

            def write_prediction(self, league_code, result):
                self.calls.append(("write", league_code, result["home_team"], result["away_team"]))
                return 1

        predictor.cache = DummyCache()
        predictor.live_refresh_service = DummyLiveRefreshService()
        predictor.review_learning_service = DummyReviewLearningService()
        predictor.inference_service = DummyInferenceService()
        predictor.rag_service = DummyRagService()
        predictor.postprocess_service = DummyPostprocessService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()

        with patch("enhanced_prediction_workflow.os.path.exists", return_value=True):
            result = predictor.predict_match(
                home_team="阿斯顿维拉",
                away_team="利物浦",
                league_code="premier_league",
                match_date="2026-05-16",
                force_refresh_odds=False,
                persist=True,
            )

        self.assertEqual(result["storage_mode"], "league_sot")
        self.assertEqual(
            predictor.writeback.calls,
            [
                ("path", "premier_league"),
                ("write", "premier_league", "阿斯顿维拉", "利物浦"),
            ],
        )

    def test_predict_match_only_calls_prepare_prediction_inputs_once_for_totals_handling(self):
        predictor = EnhancedPredictor.__new__(EnhancedPredictor)
        predictor.runtime_profile = {"profile": "test"}

        class DummyCache:
            def get(self, *_args, **_kwargs):
                return None

        class CountingLiveRefreshService:
            def __init__(self):
                self.prepare_calls = 0

            def prepare_prediction_inputs(self, **_kwargs):
                self.prepare_calls += 1
                return {
                    "match_date": "2026-05-16",
                    "analysis_context": {},
                    "current_odds": {"大小球": {"final": {"line": 2.5}}},
                    "realtime": {"context_applied": {"okooo_totals_fetch": {"attempted": True, "ok": True}}},
                    "cache_params": {"k": "v"},
                }

        class DummyReviewLearningService:
            def build_prediction_context(self, **_kwargs):
                return {"available": False, "league_review": {"league_tags": []}}

        class DummyInferenceService:
            def run(self, **_kwargs):
                return {
                    "applied_weights": {},
                    "home_strength": {},
                    "away_strength": {},
                    "match_intelligence": {},
                    "strength_diff": 0.1,
                    "final_probabilities": {"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
                    "ranked_probabilities": [("主胜", 0.5), ("平局", 0.3), ("客胜", 0.2)],
                    "main_prediction": "主胜",
                    "confidence": 0.5,
                    "historical_odds_reference": {},
                    "upset_potential": {},
                    "top_scores": [("1-0", 0.12), ("1-1", 0.11)],
                    "total_goals": {},
                    "home_lambda": 1.4,
                    "away_lambda": 0.9,
                    "over_under": {"available": True, "line": 2.5, "over": 0.45, "under": 0.55},
                    "fusion_result": {},
                    "lightweight_rag_decision": {},
                }

        class DummyRagService:
            def retrieve_match_memory(self, **_kwargs):
                return {"summary": {}, "similar_cases": [], "market_cases": [], "upset_cases": []}

            def build_lightweight_decision(self, **_kwargs):
                return {}

        class DummyPostprocessService:
            def build_market_snapshot(self, _current_odds):
                return {}

            def build_prediction_result(self, **kwargs):
                return {
                    "match_id": "1296096",
                    "league_code": kwargs["league_code"],
                    "league_name": "英超",
                    "match_date": kwargs["match_date"],
                    "match_time": kwargs["match_time"],
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "prediction": "主胜",
                    "predicted_winner": "home",
                    "confidence": kwargs["confidence"],
                    "final_probabilities": kwargs["final_probabilities"],
                    "top_scores": kwargs["top_scores"],
                    "over_under": kwargs["over_under"],
                    "realtime": kwargs["realtime"],
                    "analysis_context": kwargs["analysis_context"],
                    "runtime_profile": kwargs["runtime_profile"],
                }

        class DummyPersistenceService:
            def persist_prediction(self, *_args, **_kwargs):
                raise AssertionError("persist should not be called when persist=False")

        class DummyWriteback:
            def teams_file_path(self, _league_code):
                raise AssertionError("teams writeback should not be used when persist=False")

        predictor.cache = DummyCache()
        predictor.live_refresh_service = CountingLiveRefreshService()
        predictor.review_learning_service = DummyReviewLearningService()
        predictor.inference_service = DummyInferenceService()
        predictor.rag_service = DummyRagService()
        predictor.postprocess_service = DummyPostprocessService()
        predictor.persistence_service = DummyPersistenceService()
        predictor.writeback = DummyWriteback()

        result = predictor.predict_match(
            home_team="阿斯顿维拉",
            away_team="利物浦",
            league_code="premier_league",
            match_date="2026-05-16",
            force_refresh_odds=True,
            persist=False,
        )

        self.assertEqual(predictor.live_refresh_service.prepare_calls, 1)
        self.assertIn("okooo_totals_fetch", result["realtime"]["context_applied"])

    def test_paths_use_world_cup_specific_teams_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir).resolve()
            paths = EuropeLeaguesPaths.from_base_dir(base_dir)

            self.assertEqual(
                paths.teams_file("world_cup"),
                base_dir / "world_cup" / "teams_2026.md",
            )
            self.assertEqual(
                paths.teams_file("premier_league"),
                base_dir / "premier_league" / "teams_2025-26.md",
            )

    def test_import_csv_supports_world_cup_roster_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "world_cup_players.csv"
            csv_path.write_text(
                "\n".join(
                    [
                        "league_code,team,player_name,player_name_cn,position,age,nationality,market_value,transfer_status,appearances,goals,assists,yellow_cards,red_cards,number,club,caps,announcement_date,source",
                        "world_cup,日本,Wataru Endo,远藤航,中场,33,日本,0,current,0,0,0,0,0,6,利物浦,68,2026-05-15,https://example.com/japan-squad",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            team_file = Path("/Users/bytedance/trae_projects/europe_leagues/world_cup/players/日本.json")
            original = team_file.read_text(encoding="utf-8") if team_file.exists() else None
            try:
                written = import_csv(csv_path, None)
                self.assertEqual(written["world_cup/日本"], 1)
                payload = json.loads(team_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["league"], "世界杯")
                self.assertEqual(payload["season"], "2026")
                self.assertEqual(payload["players"][0]["name"], "远藤航")
                self.assertEqual(payload["players"][0]["english_name"], "Wataru Endo")
                self.assertEqual(payload["players"][0]["number"], "6")
                self.assertEqual(payload["players"][0]["club"], "利物浦")
                self.assertEqual(payload["players"][0]["caps"], "68")
                self.assertEqual(payload["players"][0]["announcement_date"], "2026-05-15")
                self.assertEqual(payload["players"][0]["source"], "https://example.com/japan-squad")
            finally:
                if original is None:
                    if team_file.exists():
                        team_file.unlink()
                else:
                    team_file.write_text(original, encoding="utf-8")

    def test_world_cup_schedule_rows_keep_six_columns(self):
        teams_file = Path("/Users/bytedance/trae_projects/europe_leagues/world_cup/teams_2026.md")
        lines = teams_file.read_text(encoding="utf-8").splitlines()
        schedule_rows = [
            line for line in lines
            if line.startswith("|") and line.count("|") >= 7 and "日期" not in line and "-----" not in line
        ]
        self.assertGreater(len(schedule_rows), 50)
        for row in schedule_rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            self.assertEqual(len(cells), 6)


class PredictionMemoryCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.base_dir.parent / "MEMORY.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_prediction_memory_cleans_same_day_alias_split_duplicates(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴萨|贝蒂斯] 2026-05-18 西甲 巴萨 vs 贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 3-1 > 2-0 | 大小球: 大球 2.25 (61.8%)",
                    "  · MatchID: 1302913 | 记忆ID: la_liga|2026-05-18|巴萨|贝蒂斯 | 更新时间: 2026-05-18 01:36:00",
                    "",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯",
                    "  预测: 平局 (42.6%) | 比分: 0-0 > 1-1 | 大小球: 小球 3.5 (85.5%)",
                    "  · MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯 | 记忆ID: la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯 | 更新时间: 2026-05-17 20:48:17",
                    "<!-- prediction-memory:end -->",
                    "",
                    "tail text",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("<!-- prediction-memory:start -->", content)
        self.assertIn("<!-- prediction-memory:end -->", content)
        headlines = [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("- [la_liga|2026-05-18|")
        ]
        self.assertEqual(len(headlines), 1)
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        self.assertNotIn("la_liga|2026-05-18|巴萨|贝蒂斯", content)
        self.assertIn("巴塞罗那 vs 皇家贝蒂斯", content)
        self.assertNotIn("巴萨 vs 贝蒂斯", content)

    def test_update_prediction_memory_prefers_real_match_id_over_synthetic_duplicate(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: 1302913 | 更新时间: 2026-05-18 10:00:00",
                    "",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯 | 记忆ID: la_liga_20260518_巴塞罗那_皇家贝蒂斯 | 更新时间: 2026-05-18 10:30:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        headlines = [
            line.strip()
            for line in content.splitlines()
            if line.strip().startswith("- [la_liga|2026-05-18|")
        ]
        self.assertEqual(len(headlines), 1)
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        self.assertIn("巴塞罗那 vs 皇家贝蒂斯", content)
        self.assertIn("MatchID: 1302913", content)
        self.assertNotIn("MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯", content)

    def test_update_prediction_memory_keeps_same_teams_on_different_dates(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: 1302913 | 更新时间: 2026-05-18 10:00:00",
                    "",
                    "- [la_liga|2026-05-25|巴塞罗那|皇家贝蒂斯] 2026-05-25 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1303999",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1303999 | 记忆ID: 1303999 | 更新时间: 2026-05-25 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True, exist_ok=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-18 | 03:15 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中 |",
                    "| 2026-05-25 | 03:00 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        self.assertIn("la_liga|2026-05-25|巴塞罗那|皇家贝蒂斯", content)
        headlines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [la_liga|")]
        self.assertEqual(len(headlines), 2)

    def test_update_prediction_memory_removes_entries_with_shifted_schedule_date(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-17|巴塞罗那|皇家贝蒂斯] 2026-05-17 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: 1302913 | 更新时间: 2026-05-17 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True, exist_ok=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-18 | 03:15 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertNotIn("la_liga|2026-05-17|巴塞罗那|皇家贝蒂斯", content)
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        headlines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [la_liga|")]
        self.assertEqual(len(headlines), 1)

    def test_update_prediction_memory_removes_entries_not_in_schedule_truth(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-25|巴塞罗那|皇家贝蒂斯] 2026-05-25 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1303999",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1303999 | 记忆ID: 1303999 | 更新时间: 2026-05-25 10:00:00",
                    "",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: 1302913 | 更新时间: 2026-05-18 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True, exist_ok=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-18 | 03:15 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        self.assertNotIn("la_liga|2026-05-25|巴塞罗那|皇家贝蒂斯", content)
        headlines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [la_liga|")]
        self.assertEqual(len(headlines), 1)

    def test_update_prediction_memory_keeps_alias_form_when_fixture_exists_in_truth(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴萨|贝蒂斯] 2026-05-18 西甲 巴萨 vs 贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: la_liga|2026-05-18|巴萨|贝蒂斯 | 更新时间: 2026-05-18 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True, exist_ok=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-18 | 03:15 | 巴塞罗那 | - | 皇家贝蒂斯 | 进行中 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯", content)
        self.assertNotIn("la_liga|2026-05-18|巴萨|贝蒂斯", content)
        headlines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [la_liga|")]
        self.assertEqual(len(headlines), 1)

    def test_update_prediction_memory_does_not_prune_runtime_only_competition_by_schedule_truth(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [champions_league|2026-05-25|阿森纳|巴黎圣日耳曼] 2026-05-25 欧冠 阿森纳 vs 巴黎圣日耳曼 | MatchID: 5001",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 5001 | 记忆ID: 5001 | 更新时间: 2026-05-25 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "champions_league",
                "league_name": "欧冠",
                "match_date": "2026-05-25",
                "home_team": "阿森纳",
                "away_team": "巴黎圣日耳曼",
                "prediction": "主胜",
                "match_id": "5001",
                "memory_id": "5001",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("champions_league|2026-05-25|阿森纳|巴黎圣日耳曼", content)
        headlines = [line.strip() for line in content.splitlines() if line.strip().startswith("- [champions_league|")]
        self.assertEqual(len(headlines), 1)

    def test_build_prediction_memory_samples_extracts_multiline_memory_id(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 1302913 | 记忆ID: 1302913 | 更新时间: 2026-05-18 10:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        archive_path = runtime_dir / "prediction_archive.json"
        archive_path.write_text(
            json.dumps(
                {
                    "1302913": {
                        "match_id": "1302913",
                        "league": "la_liga",
                        "match_date": "2026-05-18",
                        "home_team": "巴塞罗那",
                        "away_team": "皇家贝蒂斯",
                        "market_snapshot": {"欧赔": {}, "亚值": {}, "大小球": {}, "凯利": {}},
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        payload = build_prediction_memory_samples(base_dir=str(self.base_dir))
        records = payload["records_by_league"]["la_liga"]
        sample_ids = {str(item.get("match_id") or "") for item in records}
        self.assertIn("1302913", sample_ids)
        self.assertNotIn("la_liga_20260518_巴塞罗那_皇家贝蒂斯", sample_ids)

    def test_update_prediction_memory_only_rewrites_prediction_memory_block(self):
        before_prefix = "\n".join([
            "# Test Memory",
            "",
            "prefix line 1",
            "prefix line 2",
            "",
        ])
        after_suffix = "\n".join([
            "",
            "suffix line 1",
            "suffix line 2",
            "",
        ])
        self.memory_path.write_text(
            before_prefix
            + "\n".join(
                [
                    "<!-- prediction-memory:start -->",
                    "- [la_liga|2026-05-18|巴萨|贝蒂斯] 2026-05-18 西甲 巴萨 vs 贝蒂斯 | MatchID: 1302913",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 3-1 > 2-0 | 大小球: 大球 2.25 (61.8%)",
                    "  · MatchID: 1302913 | 记忆ID: la_liga|2026-05-18|巴萨|贝蒂斯 | 更新时间: 2026-05-18 01:36:00",
                    "",
                    "- [la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯] 2026-05-18 西甲 巴塞罗那 vs 皇家贝蒂斯 | MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯",
                    "  预测: 平局 (42.6%) | 比分: 0-0 > 1-1 | 大小球: 小球 3.5 (85.5%)",
                    "  · MatchID: la_liga_20260518_巴塞罗那_皇家贝蒂斯 | 记忆ID: la_liga|2026-05-18|巴塞罗那|皇家贝蒂斯 | 更新时间: 2026-05-17 20:48:17",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + after_suffix,
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-18",
                "home_team": "巴塞罗那",
                "away_team": "皇家贝蒂斯",
                "prediction": "主胜",
                "match_id": "1302913",
                "memory_id": "1302913",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("prefix line 1", content)
        self.assertIn("prefix line 2", content)
        self.assertIn("suffix line 1", content)
        self.assertIn("suffix line 2", content)

    def test_update_prediction_memory_canonicalizes_cup_aliases_via_global_alias_fallback(self):
        self.memory_path.write_text(
            "\n".join(
                [
                    "# Test Memory",
                    "",
                    "<!-- prediction-memory:start -->",
                    "- [champions_league|2026-05-06|阿森纳|马竞] 2026-05-06 欧冠 阿森纳 vs 马竞 | MatchID: 5001",
                    "  预测: 主胜 (45.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  · MatchID: 5001 | 记忆ID: champions_league|2026-05-06|阿森纳|马竞 | 更新时间: 2026-05-08 18:53:27",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        service = PredictionPersistenceService(base_dir=str(self.base_dir), cache=None, result_manager=None)
        service.update_prediction_memory(
            {
                "league_code": "champions_league",
                "league_name": "欧冠",
                "match_date": "2026-05-06",
                "home_team": "阿森纳",
                "away_team": "马德里竞技",
                "prediction": "主胜",
                "match_id": "5001",
                "memory_id": "5001",
                "confidence": 0.62,
                "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
                "over_under": {"available": False, "reason": "missing_real_market_line"},
            }
        )

        content = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("champions_league|2026-05-06|阿森纳|马德里竞技", content)
        self.assertIn("阿森纳 vs 马德里竞技", content)
        self.assertNotIn("champions_league|2026-05-06|阿森纳|马竞", content)
        self.assertNotIn("阿森纳 vs 马竞", content)


if __name__ == "__main__":
    unittest.main()
