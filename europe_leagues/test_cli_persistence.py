import unittest
from argparse import Namespace
from unittest.mock import patch

from app import cli


class CliPersistenceTest(unittest.TestCase):
    def test_predict_match_passes_persist_flag_and_reuses_predictor_persisted_metadata(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def predict_match(self, **kwargs):
                calls.append(kwargs)
                return {
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "league_name": "英超",
                    "match_date": kwargs["match_date"],
                    "prediction": "主胜",
                    "confidence": 0.61,
                    "final_probabilities": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
                    "over_under": {"available": False, "reason": "missing_real_market_line"},
                    "persisted": {
                        "enabled": True,
                        "archived": True,
                        "memory_updated": True,
                        "result_sync_registered": True,
                    },
                }

        args = Namespace(
            home_team="曼联",
            away_team="布伦特福德",
            league="premier_league",
            date="2026-04-28",
            match_id="",
            no_refresh_odds=False,
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="",
            league_hint=None,
            context_file="",
            no_write=False,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "app.cli.load_analysis_context_file", return_value={}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_match(args)

        self.assertTrue(calls[0]["persist"])
        self.assertTrue(captured["payload"]["data"]["persisted"]["archived"])
        self.assertTrue(captured["payload"]["data"]["persisted"]["memory_updated"])
        self.assertTrue(captured["payload"]["data"]["persisted"]["result_sync_registered"])

    def test_predict_match_no_write_disables_predictor_persistence(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def predict_match(self, **kwargs):
                calls.append(kwargs)
                return {
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "league_name": "英超",
                    "match_date": kwargs["match_date"],
                    "prediction": "主胜",
                    "confidence": 0.61,
                    "final_probabilities": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
                    "over_under": {"available": False, "reason": "missing_real_market_line"},
                }

        args = Namespace(
            home_team="曼联",
            away_team="布伦特福德",
            league="premier_league",
            date="2026-04-28",
            match_id="",
            no_refresh_odds=False,
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="",
            league_hint=None,
            context_file="",
            no_write=True,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "app.cli.load_analysis_context_file", return_value={}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_match(args)

        self.assertFalse(calls[0]["persist"])
        self.assertEqual(
            captured["payload"]["data"]["persisted"],
            {"enabled": False, "archived": False, "memory_updated": False},
        )

    def test_predict_match_blocked_defaults_to_non_archived_persisted_metadata(self):
        captured = {}

        class DummyPredictor:
            def predict_match(self, **kwargs):
                return {
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "league_name": "英超",
                    "match_date": kwargs["match_date"],
                    "prediction_blocked": True,
                    "blocked_reason": "missing_real_market_line",
                    "final_probabilities": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
                    "over_under": {"available": False, "reason": "missing_real_market_line"},
                }

        args = Namespace(
            home_team="曼联",
            away_team="布伦特福德",
            league="premier_league",
            date="2026-04-28",
            match_id="",
            no_refresh_odds=False,
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="",
            league_hint=None,
            context_file="",
            no_write=False,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "app.cli.load_analysis_context_file", return_value={}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_match(args)

        self.assertTrue(captured["payload"]["data"]["prediction_blocked"])
        self.assertEqual(
            captured["payload"]["data"]["persisted"],
            {"enabled": True, "archived": False, "memory_updated": False},
        )

    def test_predict_match_lite_uses_memory_only_persistence_service(self):
        captured = {}
        persistence_calls = []

        raw_result = {
            "league_code": "championship",
            "league_name": "英冠",
            "home_team": "米尔沃尔",
            "away_team": "赫尔城",
            "match_date": "2026-05-12",
            "prediction": "主胜",
            "confidence": 0.55,
            "all_probabilities": {"主胜": 0.55, "平局": 0.25, "客胜": 0.20},
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        class DummyPersistenceService:
            def __init__(self, base_dir, cache, result_manager):
                self.base_dir = base_dir
                self.cache = cache
                self.result_manager = result_manager

            def persist_memory_only_prediction(self, result, league_code):
                persistence_calls.append((result["home_team"], result["away_team"], league_code))
                persisted = dict(result)
                persisted["persisted"] = {
                    "enabled": True,
                    "archived": False,
                    "memory_updated": True,
                }
                return persisted

        args = Namespace(
            league_name="英冠",
            league="championship",
            home_team="米尔沃尔",
            away_team="赫尔城",
            date="2026-05-12",
            match_id="1309999",
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="03:00",
            no_write=False,
            json=True,
        )

        with patch("domain.lightweight_prediction.predict_lightweight_match", return_value=raw_result), patch(
            "domain.persistence.PredictionPersistenceService", DummyPersistenceService
        ), patch("result_manager.ResultManager", return_value=object()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_predict_match_lite(args)

        self.assertEqual(persistence_calls, [("米尔沃尔", "赫尔城", "championship")])
        self.assertTrue(captured["payload"]["data"]["persisted"]["memory_updated"])
        self.assertFalse(captured["payload"]["data"]["persisted"]["archived"])

    def test_predict_match_lite_no_write_skips_memory_only_persistence(self):
        captured = {}

        raw_result = {
            "league_code": "championship",
            "league_name": "英冠",
            "home_team": "米尔沃尔",
            "away_team": "赫尔城",
            "match_date": "2026-05-12",
            "prediction": "主胜",
            "confidence": 0.55,
            "all_probabilities": {"主胜": 0.55, "平局": 0.25, "客胜": 0.20},
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        args = Namespace(
            league_name="英冠",
            league="championship",
            home_team="米尔沃尔",
            away_team="赫尔城",
            date="2026-05-12",
            match_id="1309999",
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="03:00",
            no_write=True,
            json=True,
        )

        with patch("domain.lightweight_prediction.predict_lightweight_match", return_value=raw_result), patch(
            "domain.persistence.PredictionPersistenceService"
        ) as mock_service_cls, patch("result_manager.ResultManager") as mock_manager_cls, patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_predict_match_lite(args)

        mock_service_cls.assert_not_called()
        mock_manager_cls.assert_not_called()
        self.assertEqual(
            captured["payload"]["data"]["persisted"],
            {"enabled": False, "archived": False, "memory_updated": False},
        )


if __name__ == "__main__":
    unittest.main()
