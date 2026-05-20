import unittest
from unittest.mock import patch

from domain.live import LiveRefreshService
from domain.odds import auto_fetch_okooo_totals_if_needed


class _DummyEwmaLearning:
    def get_team_ewma_features(self, **_kwargs):
        return {"available": False}


class LiveRefreshServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = LiveRefreshService(
            base_dir="/tmp",
            league_config={"premier_league": {"name": "英超"}},
            team_ewma_learning=_DummyEwmaLearning(),
        )

    def test_refresh_live_snapshot_does_not_pollute_realtime_when_snapshot_mismatch(self):
        realtime = self.service.build_realtime("1296096", "driver-a", False)
        current_odds = {"欧赔": {"final": {"home": 1.8}}}
        mismatch_payload = {
            "match_id": "wrong-id",
            "home_team": "其他主队",
            "away_team": "其他客队",
            "match_date": "2026-05-16",
            "欧赔": {"final": {"home": 2.1, "draw": 3.2, "away": 3.6}},
        }
        matching_payload = {
            "match_id": "1296096",
            "home_team": "阿斯顿维拉",
            "away_team": "利物浦",
            "match_date": "2026-05-16",
            "欧赔": {"final": {"home": 1.9, "draw": 3.5, "away": 3.9}},
            "亚值": {"final": {"home": 0.92, "away": 0.96}},
            "大小球": {"found": True, "final": {"line": 2.5}},
        }

        with patch("domain.live.build_okooo_driver_chain", return_value=["driver-a", "driver-b"]), patch(
            "domain.live.describe_unavailable_okooo_drivers", return_value=[]
        ), patch(
            "domain.live.refresh_okooo_snapshot",
            side_effect=[("/tmp/mismatch.json", mismatch_payload), ("/tmp/match.json", matching_payload)],
        ):
            merged = self.service.refresh_live_snapshot(
                league_code="premier_league",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                match_date="2026-05-16",
                current_odds=current_odds,
                realtime=realtime,
                force_refresh_odds=True,
                okooo_driver="driver-a",
                okooo_headed=False,
                match_time="",
                match_id="1296096",
            )

        self.assertTrue(realtime["okooo"]["refreshed"])
        self.assertEqual(realtime["okooo"]["snapshot_path"], "/tmp/match.json")
        self.assertEqual(realtime["okooo"]["match_id"], "1296096")
        self.assertEqual(realtime["okooo"]["driver"], "driver-b")
        self.assertEqual(realtime["okooo"]["errors"][0]["error"], "snapshot_payload_mismatch")
        self.assertEqual(merged["snapshot_path"], "/tmp/match.json")
        self.assertEqual(merged["match_id"], "1296096")

    def test_prepare_prediction_inputs_allows_snapshot_hydrate_when_force_refresh_odds_false(self):
        payload = {
            "match_id": "1296096",
            "home_team": "阿斯顿维拉",
            "away_team": "利物浦",
            "match_date": "2026-05-16",
            "大小球": {"found": True, "final": {"line": 2.5}},
            "欧赔": {"final": {"home": 1.9, "draw": 3.4, "away": 3.8}},
        }

        with patch("domain.live.find_snapshot_for_match", return_value=("/tmp/existing.json", payload)), patch(
            "domain.live.auto_enrich_team_context_if_enabled", return_value=None
        ):
            prepared = self.service.prepare_prediction_inputs(
                home_team="阿斯顿维拉",
                away_team="利物浦",
                league_code="premier_league",
                match_date="2026-05-16",
                current_odds={},
                match_id="1296096",
                force_refresh_odds=False,
                okooo_driver="local-chrome",
                okooo_headed=False,
                match_time="",
                analysis_context={},
            )

        self.assertEqual(prepared["current_odds"]["match_id"], "1296096")
        self.assertEqual(prepared["current_odds"]["snapshot_path"], "/tmp/existing.json")
        self.assertEqual(prepared["realtime"]["okooo"]["snapshot_path"], "/tmp/existing.json")
        self.assertEqual(prepared["realtime"]["context_applied"]["existing_snapshot_odds"]["ok"], True)
        self.assertEqual(prepared["realtime"]["context_applied"]["okooo_totals_fetch"]["skipped"], "force_refresh_odds=False")

    def test_prepare_prediction_inputs_skips_network_refresh_and_totals_fetch_when_force_refresh_odds_false(self):
        with patch.object(self.service, "refresh_live_snapshot", wraps=self.service.refresh_live_snapshot) as mock_refresh, patch.object(
            self.service, "ensure_totals_if_needed", wraps=self.service.ensure_totals_if_needed
        ) as mock_totals, patch("domain.live.find_snapshot_for_match", return_value=None), patch(
            "domain.live.auto_enrich_team_context_if_enabled", return_value=None
        ):
            prepared = self.service.prepare_prediction_inputs(
                home_team="阿斯顿维拉",
                away_team="利物浦",
                league_code="premier_league",
                match_date="2026-05-16",
                current_odds={"欧赔": {"final": {"home": 1.9}}},
                match_id="1296096",
                force_refresh_odds=False,
                okooo_driver="local-chrome",
                okooo_headed=False,
                match_time="",
                analysis_context={},
            )

        self.assertEqual(mock_refresh.call_count, 1)
        self.assertEqual(mock_totals.call_count, 1)
        self.assertFalse(prepared["realtime"]["okooo"]["attempted"])
        self.assertFalse(prepared["realtime"]["context_applied"]["okooo_totals_fetch"]["attempted"])
        self.assertEqual(prepared["realtime"]["context_applied"]["okooo_totals_fetch"]["skipped"], "force_refresh_odds=False")

    def test_refresh_report_match_odds_uses_driver_chain_instead_of_hardcoded_local_chrome(self):
        payload = {
            "match_id": "1296096",
            "home_team": "阿斯顿维拉",
            "away_team": "利物浦",
            "match_date": "2026-05-16",
            "欧赔": {"final": {"home": 1.9, "draw": 3.4, "away": 3.8}},
        }
        driver_calls = []

        def fake_refresh(*args, **kwargs):
            driver_calls.append(kwargs["driver"])
            if kwargs["driver"] == "driver-a":
                return None
            return "/tmp/report.json", payload

        with patch("domain.live.build_okooo_driver_chain", return_value=["driver-a", "driver-b"]), patch(
            "domain.live.refresh_okooo_snapshot", side_effect=fake_refresh
        ):
            result = self.service.refresh_report_match_odds(
                league_code="premier_league",
                match_date="2026-05-16",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                current_odds={},
                okooo_driver="browser-use",
                okooo_headed=True,
            )

        self.assertEqual(driver_calls, ["driver-a", "driver-b"])
        self.assertEqual(result["match_id"], "1296096")

    def test_refresh_report_match_odds_prefer_existing_only_skips_when_market_content_is_real(self):
        with patch("domain.live.build_okooo_driver_chain", return_value=["driver-a"]), patch(
            "domain.live.refresh_okooo_snapshot", return_value=(
                "/tmp/report.json",
                {
                    "match_id": "1296096",
                    "home_team": "阿斯顿维拉",
                    "away_team": "利物浦",
                    "match_date": "2026-05-16",
                    "欧赔": {"final": {"home": 1.9, "draw": 3.4, "away": 3.8}},
                },
            )
        ) as mock_refresh:
            empty_shell = {"欧赔": {"initial": {}, "final": {}}}
            refreshed = self.service.refresh_report_match_odds(
                league_code="premier_league",
                match_date="2026-05-16",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                current_odds=empty_shell,
                prefer_existing=True,
            )
            real_market = {"欧赔": {"final": {"home": 1.85, "draw": 3.5, "away": 4.1}}}
            reused = self.service.refresh_report_match_odds(
                league_code="premier_league",
                match_date="2026-05-16",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                current_odds=real_market,
                prefer_existing=True,
            )

        self.assertEqual(mock_refresh.call_count, 1)
        self.assertEqual(refreshed["match_id"], "1296096")
        self.assertIs(reused, real_market)

    def test_auto_fetch_okooo_totals_if_needed_rejects_mismatched_snapshot_payload(self):
        current_odds = {"欧赔": {"final": {"home": 1.9}}}
        payload = {
            "match_id": "wrong-id",
            "home_team": "其他主队",
            "away_team": "其他客队",
            "match_date": "2026-05-16",
            "大小球": {"found": True, "final": {"line": 3.5}},
        }

        with patch("domain.odds.build_okooo_driver_chain", return_value=["driver-a"]), patch(
            "domain.odds.describe_unavailable_okooo_drivers", return_value=[]
        ), patch("domain.odds.refresh_okooo_snapshot", return_value=("/tmp/mismatch.json", payload)):
            merged, diag = auto_fetch_okooo_totals_if_needed(
                base_dir="/tmp",
                league_code="premier_league",
                league_name="英超",
                match_date="2026-05-16",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                current_odds=current_odds,
                analysis_context={},
                to_float=LiveRefreshService.to_float,
                okooo_driver="local-chrome",
                okooo_headed=False,
                match_time="",
                match_id="1296096",
            )

        self.assertIs(merged, current_odds)
        self.assertFalse(diag["ok"])
        self.assertEqual(diag["error"], "snapshot payload mismatch")
        self.assertEqual(diag["payload_match_id"], "wrong-id")

    def test_auto_fetch_okooo_totals_if_needed_accepts_matching_snapshot_payload(self):
        current_odds = {"欧赔": {"final": {"home": 1.9}}}
        payload = {
            "match_id": "1296096",
            "home_team": "阿斯顿维拉",
            "away_team": "利物浦",
            "match_date": "2026-05-16",
            "大小球": {
                "found": True,
                "initial": {"line": 2.25},
                "final": {"line": 2.5},
                "companies": [{"name": "A"}],
            },
        }

        with patch("domain.odds.build_okooo_driver_chain", return_value=["driver-a"]), patch(
            "domain.odds.describe_unavailable_okooo_drivers", return_value=[]
        ), patch("domain.odds.refresh_okooo_snapshot", return_value=("/tmp/match.json", payload)):
            merged, diag = auto_fetch_okooo_totals_if_needed(
                base_dir="/tmp",
                league_code="premier_league",
                league_name="英超",
                match_date="2026-05-16",
                home_team="阿斯顿维拉",
                away_team="利物浦",
                current_odds=current_odds,
                analysis_context={},
                to_float=LiveRefreshService.to_float,
                okooo_driver="local-chrome",
                okooo_headed=False,
                match_time="",
                match_id="1296096",
            )

        self.assertTrue(diag["ok"])
        self.assertEqual(diag["source_snapshot"], "/tmp/match.json")
        self.assertEqual(merged["大小球"]["final"]["line"], 2.5)
        self.assertEqual(merged["大小球"]["initial"]["line"], 2.25)
        self.assertEqual(merged["大小球"]["companies"][0]["name"], "A")


if __name__ == "__main__":
    unittest.main()
