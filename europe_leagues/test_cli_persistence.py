import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
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
    def test_validate_leagues_rejects_friendly_alias_as_non_formal_league(self):
        with patch("domain.predictor.LEAGUE_CONFIG", {"world_cup": {"name": "世界杯"}}):
            with self.assertRaises(ValueError):
                cli.validate_leagues("友谊赛")

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

    def test_collect_data_rejects_reference_only_friendly_entry(self):
        args = Namespace(
            league="友谊赛",
            date="2026-06-06",
            no_cache=False,
            json=True,
        )

        with self.assertRaises(ValueError):
            cli.run_openclaw_collect_data(args)

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

    def test_accuracy_plain_output_includes_reanalysis_summary_when_available(self):
        class DummyAccuracyManager:
            def __init__(self):
                self.accuracy_store = SimpleNamespace(load=lambda: {})

            def update_accuracy_stats(self):
                return {
                    "overall": {
                        "win_accuracy": 43.06,
                        "correct_predictions": 31,
                        "total_predictions": 72,
                        "ou_accuracy": 0.0,
                        "correct_ou_predictions": 0,
                        "total_ou_predictions": 0,
                    },
                    "reanalysis_report": {
                        "available": True,
                        "overall": {
                            "win_accuracy": 57.14,
                            "correct_predictions": 8,
                            "total_predictions": 14,
                        },
                        "by_league": {
                            "premier_league": {
                                "win_accuracy": 57.14,
                                "correct_predictions": 8,
                                "total_predictions": 14,
                            }
                        },
                        "delta_summary": {
                            "baseline_win_accuracy": 28.57,
                            "replay_win_accuracy": 57.14,
                            "win_accuracy_delta": 28.57,
                            "top_win_accuracy_improvements": [
                                {
                                    "league": "premier_league",
                                    "baseline_win_accuracy": 28.57,
                                    "replay_win_accuracy": 57.14,
                                    "win_accuracy_delta": 28.57,
                                }
                            ],
                        },
                    },
                    "over_under_report": {"by_line_source": {}},
                }

        args = Namespace(refresh=True, json=False)
        buffer = io.StringIO()

        with patch("result_manager.ResultManager", return_value=DummyAccuracyManager()), redirect_stdout(buffer):
            cli.run_openclaw_accuracy(args)

        output = buffer.getvalue()
        self.assertIn("总体准确率: 43.06% (31/72)", output)
        self.assertIn("当前模型重放胜平负准确率: 57.14% (8/14)", output)
        self.assertIn("  基线对比: 28.57% -> 57.14% (+28.57pt)", output)
        self.assertIn("  - premier_league: 28.57% -> 57.14% (+28.57pt)", output)

    def test_apply_reanalysis_passes_filters_and_flags(self):
        captured = {}
        calls = {}

        class DummyApplyManager:
            def apply_reanalysis_predictions(self, **kwargs):
                calls.update(kwargs)
                return {"applied_count": 1, "skipped_count": 0}

        args = Namespace(
            league="ligue_1",
            input="reanalysis_results_ligue_1.json",
            match_id=["ligue_1_20260518_里昂_朗斯"],
            only_improved=True,
            dry_run=True,
            refresh_accuracy=True,
            json=True,
        )

        with patch("result_manager.ResultManager", return_value=DummyApplyManager()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_apply_reanalysis(args)

        self.assertEqual(
            calls,
            {
                "league": "ligue_1",
                "input_path": "reanalysis_results_ligue_1.json",
                "match_ids": ["ligue_1_20260518_里昂_朗斯"],
                "only_improved": True,
                "dry_run": True,
                "refresh_accuracy": True,
            },
        )
        self.assertEqual(captured["payload"]["command"], "apply-reanalysis")
        self.assertEqual(
            captured["payload"]["data"]["runtime_profile"]["agent_roles"],
            cli.get_command_runtime_profile("apply-reanalysis")["agent_roles"],
        )

    def test_rag_replay_eval_passes_filters_and_flags(self):
        captured = {}
        calls = {}

        class DummyReplayManager:
            def evaluate_rag_replay(self, **kwargs):
                calls.update(kwargs)
                return {
                    "generated_at": "2026-05-28T10:00:00",
                    "read_only": True,
                    "overall": {"sample_count": 3, "replayed_count": 2, "skipped_count": 1, "decision_changed_count": 1},
                    "by_league": {},
                    "delta_summary": {},
                    "matches": [],
                }

        args = Namespace(
            league="la_liga",
            since="2026-05-01",
            until="2026-05-31",
            limit=5,
            match_id=["la_liga_20260511_巴塞罗那_皇家马德里"],
            include_matches=True,
            sample_source="archive",
            strict=True,
            json=True,
        )

        with patch("result_manager.ResultManager", return_value=DummyReplayManager()), patch(
            "app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)
        ):
            cli.run_openclaw_rag_replay_eval(args)

        self.assertEqual(
            calls,
            {
                "league": "la_liga",
                "since": "2026-05-01",
                "until": "2026-05-31",
                "limit": 5,
                "match_ids": ["la_liga_20260511_巴塞罗那_皇家马德里"],
                "include_matches": True,
                "sample_source": "archive",
                "strict": True,
            },
        )
        self.assertEqual(captured["payload"]["command"], "rag-replay-eval")
        self.assertTrue(captured["payload"]["data"]["read_only"])
        self.assertEqual(
            captured["payload"]["data"]["runtime_profile"]["agent_roles"],
            cli.get_command_runtime_profile("rag-replay-eval")["agent_roles"],
        )

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

    def test_predict_match_friendly_alias_uses_dedicated_friendly_runtime_bucket(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def predict_match(self, **kwargs):
                calls.append(kwargs)
                return {
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "league_name": "世界杯",
                    "match_date": kwargs["match_date"],
                    "prediction": "主胜",
                    "confidence": 0.57,
                    "final_probabilities": {"home_win": 0.48, "draw": 0.30, "away_win": 0.22},
                    "over_under": {"available": False, "reason": "missing_real_market_line"},
                }

        args = Namespace(
            home_team="比利时",
            away_team="突尼斯",
            league="友谊赛",
            date="2026-06-06",
            match_id="",
            no_refresh_odds=False,
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="21:00",
            league_hint=None,
            context_file="",
            no_write=False,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "app.cli.load_analysis_context_file", return_value={}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_match(args)

        self.assertEqual(calls[0]["league_code"], "friendly")
        self.assertFalse(calls[0]["persist"])
        self.assertEqual(calls[0]["analysis_context"]["competition_type"], "friendly")
        self.assertEqual(calls[0]["analysis_context"]["okooo_league_name_override"], "友谊赛")
        self.assertTrue(captured["payload"]["data"]["reference_only"])
        self.assertEqual(captured["payload"]["data"]["league_code"], "friendly")
        self.assertEqual(captured["payload"]["data"]["league_name"], "友谊赛")
        self.assertEqual(captured["payload"]["data"]["runtime_league_code"], "friendly")
        self.assertEqual(captured["payload"]["data"]["league_request"], "友谊赛")
        self.assertEqual(
            captured["payload"]["data"]["persisted"],
            {
                "enabled": False,
                "archived": False,
                "memory_updated": False,
                "skipped_reason": "non_sot_league_output_only",
            },
        )
        self.assertEqual(
            captured["payload"]["data"]["reference_only_live_market_notice"]["reason"],
            "friendly_match_requires_explicit_match_id_for_live_market",
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

    def test_predict_match_non_sot_league_outputs_without_persisting(self):
        captured = {}
        calls = []

        class DummyPredictor:
            def predict_match(self, **kwargs):
                calls.append(kwargs)
                return {
                    "home_team": kwargs["home_team"],
                    "away_team": kwargs["away_team"],
                    "league_name": "英冠",
                    "match_date": kwargs["match_date"],
                    "prediction": "主胜",
                    "confidence": 0.55,
                    "final_probabilities": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
                    "over_under": {"available": False, "reason": "missing_real_market_line"},
                }

        args = Namespace(
            home_team="米尔沃尔",
            away_team="赫尔城",
            league="championship",
            date="2026-05-12",
            match_id="1309999",
            no_refresh_odds=False,
            okooo_driver="local-chrome",
            okooo_headed=False,
            match_time="03:00",
            league_hint=None,
            context_file="",
            no_write=False,
            json=True,
        )

        with patch("domain.predictor.DomainPredictor", return_value=DummyPredictor()), patch(
            "app.cli.load_analysis_context_file", return_value={}
        ), patch("app.cli.emit_response", side_effect=lambda payload, as_json: captured.setdefault("payload", payload)):
            cli.run_openclaw_predict_match(args)

        self.assertFalse(calls[0]["persist"])
        self.assertEqual(
            captured["payload"]["data"]["persisted"],
            {
                "enabled": False,
                "archived": False,
                "memory_updated": False,
                "skipped_reason": "non_sot_league_output_only",
            },
        )

    def test_predict_match_sot_league_persists_by_default(self):
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
        self.assertNotIn("skipped_reason", captured["payload"]["data"]["persisted"])


if __name__ == "__main__":
    unittest.main()
