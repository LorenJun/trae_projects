import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

from app import cli
from data_collector import MatchData


class DummySaveResultManager:
    saved_call = None

    def save_result(self, identifier, home_score, away_score, league=None, date_override=None, force=False):
        self.__class__.saved_call = {
            "identifier": identifier,
            "home_score": home_score,
            "away_score": away_score,
            "league": league,
            "date_override": date_override,
            "force": force,
        }
        return {
            "match_id": identifier,
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "actual_score": f"{home_score}-{away_score}",
            "actual_winner": "home" if home_score > away_score else "away" if away_score > home_score else "draw",
        }


class CliPersistenceTest(unittest.TestCase):
    def test_save_result_passes_force_flag(self):
        captured = {}
        DummySaveResultManager.saved_call = None
        args = Namespace(
            match_id="la_liga_20260511_巴塞罗那_皇家马德里",
            home_score=2,
            away_score=1,
            force=True,
            json=True,
        )

        with patch("result_manager.ResultManager", return_value=DummySaveResultManager()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_save_result(args)

        self.assertEqual(
            DummySaveResultManager.saved_call,
            {
                "identifier": "la_liga_20260511_巴塞罗那_皇家马德里",
                "home_score": 2,
                "away_score": 1,
                "league": None,
                "date_override": None,
                "force": True,
            },
        )
        self.assertEqual(captured["payload"]["command"], "save-result")
        self.assertEqual(
            captured["payload"]["data"]["runtime_profile"]["agent_roles"],
            cli.get_command_runtime_profile("save-result")["agent_roles"],
        )

    def test_command_agent_roles_cover_formal_workflow_commands(self):
        parser = cli.build_parser()
        subcommands = set(parser._subparsers._group_actions[0].choices.keys())
        required_commands = set(cli.FORMAL_COMMANDS)
        self.assertTrue(required_commands.issubset(subcommands))
        self.assertTrue(required_commands.issubset(cli.COMMAND_AGENT_ROLES.keys()))

    def test_legacy_commands_are_explicitly_classified(self):
        parser = cli.build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertEqual(
            set(cli.LEGACY_COMMANDS),
            {"enhanced", "original", "ml-test", "results", "show-accuracy", "update-accuracy"},
        )
        self.assertTrue(set(cli.LEGACY_COMMANDS).issubset(choices.keys()))
        self.assertTrue(set(cli.LEGACY_COMMANDS).isdisjoint(cli.COMMAND_AGENT_ROLES.keys()))
        self.assertTrue(str(choices["enhanced"].description or "").startswith("[legacy]") or str(choices["enhanced"].format_usage()).startswith("usage:"))
        self.assertEqual(choices["enhanced"].prog.split()[-1], "enhanced")

    def test_predict_schedule_returns_runtime_profile_per_update(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def generate_prediction_report(self, league_code, match_date, persist=True, write_teams=True):
                calls.append((league_code, match_date, persist, write_teams))
                return {
                    "teams_file": f"/tmp/{league_code}_{match_date}.md",
                    "teams_updated": True,
                    "prediction_count": 3,
                    "accuracy_refreshed": persist,
                    "persisted": {
                        "enabled": persist,
                        "archived": False,
                        "memory_updated": False,
                        "result_sync_registered": False,
                    },
                }

        args = Namespace(
            league="premier_league",
            date="2026-05-11",
            days=2,
            no_write=False,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "domain.predictor.LEAGUE_CONFIG", {"premier_league": {"name": "英超"}}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_schedule(args)

        updates = captured["payload"]["data"]
        self.assertEqual(len(updates), 2)
        self.assertEqual(calls[0], ("premier_league", "2026-05-11", True, True))
        self.assertEqual(calls[1], ("premier_league", "2026-05-12", True, True))
        self.assertTrue(all(item["updated"] for item in updates))
        self.assertTrue(all(item["prediction_count"] == 3 for item in updates))
        self.assertTrue(all(item["persisted"]["enabled"] for item in updates))
        self.assertTrue(all(item["runtime_profile"]["agent_roles"] == ["data_collector", "match_analyzer", "odds_analyzer"] for item in updates))

    def test_predict_schedule_no_write_disables_batch_persistence(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def generate_prediction_report(self, league_code, match_date, persist=True, write_teams=True):
                calls.append((league_code, match_date, persist, write_teams))
                return {
                    "teams_file": None,
                    "teams_updated": False,
                    "prediction_count": 2,
                    "accuracy_refreshed": False,
                    "persisted": {
                        "enabled": persist,
                        "archived": False,
                        "memory_updated": False,
                        "result_sync_registered": False,
                    },
                }

        args = Namespace(
            league="premier_league",
            date="2026-05-11",
            days=1,
            no_write=True,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "domain.predictor.LEAGUE_CONFIG", {"premier_league": {"name": "英超"}}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_schedule(args)

        self.assertEqual(calls, [("premier_league", "2026-05-11", False, False)])
        update = captured["payload"]["data"][0]
        self.assertFalse(update["updated"])
        self.assertFalse(update["accuracy_refreshed"])
        self.assertEqual(update["persisted"], {"enabled": False, "archived": False, "memory_updated": False, "result_sync_registered": False})

    def test_collect_data_serializes_matches_and_runtime_profile(self):
        captured = {}

        class DummyCollector:
            async def collect_league_data(self, league, date, use_cache=True):
                return [
                    MatchData(
                        home_team="曼联",
                        away_team="切尔西",
                        league=league,
                        match_date=date,
                        match_time="03:00",
                        status="待进行",
                        match_id="m1",
                        sources=["mock"],
                    )
                ]

        args = Namespace(
            league="premier_league",
            date="2026-05-11",
            no_cache=False,
            json=True,
        )

        with patch("collectors.sporttery.DataCollector", return_value=DummyCollector()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_collect_data(args)

        data = captured["payload"]["data"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["matches"][0]["match_id"], "m1")
        self.assertEqual(data["matches"][0]["sources"], ["mock"])
        self.assertEqual(data["runtime_profile"]["agent_roles"], ["data_collector"])

    def test_accuracy_refresh_uses_update_path(self):
        captured = {}

        class DummyAccuracyManager:
            def __init__(self):
                self.accuracy_store = SimpleNamespace(load=lambda: {"overall": {"total_predictions": 99}})

            def update_accuracy_stats(self):
                return {"overall": {"total_predictions": 1}, "refreshed": True}

        args = Namespace(refresh=True, json=True)

        with patch("result_manager.ResultManager", return_value=DummyAccuracyManager()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_accuracy(args)

        data = captured["payload"]["data"]
        self.assertTrue(data["refreshed"])
        self.assertEqual(data["overall"]["total_predictions"], 1)
        self.assertEqual(data["runtime_profile"]["agent_roles"], ["result_tracker"])

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
