import unittest
from unittest.mock import patch

from domain.persistence import PredictionPersistenceService
from enhanced_prediction_workflow import EnhancedPredictor


class PredictionPersistenceSideEffectTest(unittest.TestCase):
    def test_persist_prediction_batch_only_refreshes_accuracy(self):
        class DummyResultManager:
            def __init__(self):
                self.refresh_count = 0

            def update_accuracy_stats(self):
                self.refresh_count += 1
                return {"overall": {}}

        manager = DummyResultManager()
        service = PredictionPersistenceService(base_dir="/tmp", cache=None, result_manager=manager)

        with patch.object(manager, "update_accuracy_stats", wraps=manager.update_accuracy_stats) as mock_refresh:
            summary = service.persist_prediction_batch([
                {"match_id": "a"},
                {"match_id": "b"},
            ], "premier_league")

        self.assertEqual(manager.refresh_count, 1)
        mock_refresh.assert_called_once_with()
        self.assertTrue(summary["accuracy_refreshed"])
        self.assertTrue(summary["persisted"]["batch_mode"])
        self.assertFalse(summary["persisted"]["archived"])

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
                        "memory_updated": False,
                        "result_sync_registered": False,
                        "batch_mode": True,
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

            def ensure_totals_if_needed(self, **kwargs):
                return kwargs["current_odds"]

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


if __name__ == "__main__":
    unittest.main()
