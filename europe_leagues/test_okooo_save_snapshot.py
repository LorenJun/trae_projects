import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from okooo_mobile_access import available_mobile_profiles, fresh_mobile_profile
from runtime.okooo_access import open_okooo_verification_breaker, read_okooo_verification_breaker
from okooo_live_snapshot import refresh_snapshot
from okooo_save_snapshot import (
    _candidate_date_hints,
    _extract_all_markets_from_hub,
    _extract_all_markets_with_fallback,
    _extract_odds_only_from_hub,
    _find_existing_snapshot_by_match_id,
    _find_match_id,
    _find_match_id_from_schedule_cache,
    _is_blocked_text,
    _is_verification_required_payload,
    _mobile_league_url,
    _navigate_schedule_to_month,
    _normalize_okooo_league_name,
    _page_blocked_now,
    _pick_preferred_europe_result,
    _run_with_retries,
    _run_with_verification_reentry,
    _select_best_schedule_row,
    _team_aliases,
    _time_tokens,
)


class _DummyBrowser:
    def open(self, _url):
        return None

    def eval_json(self, _expr):
        return {"clicked": True}


class _SequencedBrowser:
    def __init__(self, responses):
        self._responses = list(responses)

    def eval_json(self, _expr):
        if not self._responses:
            raise AssertionError("unexpected eval_json call")
        return self._responses.pop(0)


class _DummyRetryClient:
    def __init__(self, profile):
        self.mobile_profile = profile

    def close(self):
        return None


class OkoooSaveSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fresh_mobile_profile_prefers_new_device_pool(self):
        current = available_mobile_profiles()[0]
        rotated = fresh_mobile_profile(current)
        self.assertNotEqual(rotated.profile_id, current.profile_id)
        self.assertNotEqual(rotated.device_pool_id, current.device_pool_id)

    def test_is_blocked_text_detects_slider_verification_dom(self):
        html = """
        <html>
          <body>
            <div>为了更好的访问体验，请进行验证</div>
            <div>请按住滑块，拖动到最右边</div>
            <canvas id="aliyunCaptcha"></canvas>
            <iframe src="https://verify.example/captcha"></iframe>
            <img src="/verify/slider.png" width="320" height="180" />
          </body>
        </html>
        """
        self.assertTrue(_is_blocked_text(html))

    def test_run_with_retries_marks_verification_required_and_stops_after_first_blocked(self):
        profiles = available_mobile_profiles()
        factory_calls = []

        def client_factory(_session):
            profile = profiles[len(factory_calls) % len(profiles)]
            factory_calls.append(profile.profile_id)
            return _DummyRetryClient(profile)

        def extractor(_client, _match_id):
            return {"blocked": True, "url": "https://m.okooo.com/match/odds.php?MatchID=1315851"}

        result = _run_with_retries("europe_mobile", "unit_blocked", client_factory, extractor, "1315851")

        self.assertTrue(_is_verification_required_payload(result))
        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["error"], "verification_required")
        self.assertEqual(result["retry_strategy"], "stop_after_verification")
        self.assertEqual(len(result["_attempts"]), 1)
        self.assertEqual(result["_attempts"][0]["stop_reason"], "verification_required")
        self.assertEqual(factory_calls, [result["mobile_profile"]["profile_id"]])

    def test_run_with_verification_reentry_returns_success_from_fresh_pool(self):
        profiles = available_mobile_profiles()
        blocked_profile = profiles[0]
        recovered_profile = fresh_mobile_profile(blocked_profile)
        factory_profiles = []

        def client_factory(_session):
            client = _DummyRetryClient(None)
            factory_profiles.append(client)
            return client

        calls = []

        def runner(factory, session_prefix):
            client = factory(f"{session_prefix}_1")
            calls.append((session_prefix, getattr(client.mobile_profile, "profile_id", None)))
            if len(calls) == 1:
                return {
                    "blocked": True,
                    "verification_required": True,
                    "status": "verification_required",
                    "error": "verification_required",
                    "mobile_profile": {
                        "profile_id": blocked_profile.profile_id,
                        "device_pool_id": blocked_profile.device_pool_id,
                        "device_name": blocked_profile.device_name,
                        "user_agent": blocked_profile.user_agent,
                    },
                }
            client.mobile_profile = recovered_profile
            return {
                "found": True,
                "parsed": True,
                "mobile_profile": {
                    "profile_id": recovered_profile.profile_id,
                    "device_pool_id": recovered_profile.device_pool_id,
                    "device_name": recovered_profile.device_name,
                    "user_agent": recovered_profile.user_agent,
                },
                "reentry_mobile_profile": {
                    "profile_id": recovered_profile.profile_id,
                    "device_pool_id": recovered_profile.device_pool_id,
                    "device_name": recovered_profile.device_name,
                    "user_agent": recovered_profile.user_agent,
                },
            }

        result = _run_with_verification_reentry(runner, client_factory, "eu")

        self.assertTrue(result["reentered_after_verification"])
        self.assertEqual(result["verification_reentry_count"], 1)
        self.assertEqual(result["reentry_from_mobile_profile"]["profile_id"], blocked_profile.profile_id)
        self.assertEqual(result["reentry_mobile_profile"]["profile_id"], recovered_profile.profile_id)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][1], blocked_profile.profile_id)

    def test_page_blocked_now_detects_slider_wall(self):
        browser = _SequencedBrowser([{"blocked": True}])
        self.assertTrue(_page_blocked_now(browser))

    def test_page_blocked_now_passes_clean_page(self):
        browser = _SequencedBrowser([{"blocked": False}])
        self.assertFalse(_page_blocked_now(browser))

    def test_page_blocked_now_is_safe_on_eval_failure(self):
        class _Boom:
            def eval_json(self, _expr):
                raise RuntimeError("cdp gone")

        self.assertFalse(_page_blocked_now(_Boom()))

    def test_extract_all_markets_from_hub_escalates_blocked_after_navigation(self):
        with patch("okooo_save_snapshot._open_ready", return_value="ok"), patch(
            "okooo_save_snapshot._click_visible_text", return_value={"clicked": True}
        ), patch("okooo_save_snapshot._page_blocked_now", side_effect=[True]), patch(
            "okooo_save_snapshot.time.sleep", return_value=None
        ):
            result = _extract_all_markets_from_hub(_DummyBrowser(), "https://m.okooo.com/match/history.php?MatchID=1")

        self.assertTrue(result["blocked"])
        self.assertEqual(result["_blocked_at"], "hub_nav_yazhi")

    def test_extract_all_markets_from_hub_preserves_asian_when_odds_blocked(self):
        asian = {"found": True, "parsed": True, "average_row": {"home": "0.90"}}
        totals = {"found": True, "parsed": True, "line": "2.5"}

        with patch("okooo_save_snapshot._open_ready", return_value="ok"), patch(
            "okooo_save_snapshot._click_visible_text", return_value={"clicked": True}
        ), patch(
            "okooo_save_snapshot.OUZHI_RETRY_WAITS", []
        ), patch(
            "okooo_save_snapshot._page_blocked_now", side_effect=[False, True, True]
        ), patch("okooo_save_snapshot.time.sleep", return_value=None), patch(
            "okooo_save_snapshot._parse_asian_on_current_page", return_value=dict(asian)
        ), patch(
            "okooo_save_snapshot._parse_totals_on_current_page", return_value=dict(totals)
        ):
            result = _extract_all_markets_from_hub(_DummyBrowser(), "https://m.okooo.com/match/history.php?MatchID=1")

        self.assertNotIn("blocked", result)
        self.assertTrue(result["found"])
        self.assertEqual(result["_partial_blocked_at"], "europe,kelly")
        self.assertTrue(result["asian"]["found"])
        self.assertTrue(result["totals"]["found"])
        self.assertTrue(result["europe"]["blocked"])
        self.assertTrue(result["kelly"]["blocked"])

    def test_extract_all_markets_from_hub_retries_ouzhi_then_recovers(self):
        asian = {"found": True, "parsed": True}
        totals = {"found": True, "parsed": True}
        europe = {"found": True, "parsed": True, "company_mode": "multi_company_consensus"}
        kelly = {"found": True, "parsed": True}

        # asian leg clean; ouzhi first landing walled, then clean after one retry.
        with patch("okooo_save_snapshot._open_ready", return_value="ok"), patch(
            "okooo_save_snapshot._click_visible_text", return_value={"clicked": True}
        ), patch(
            "okooo_save_snapshot.OUZHI_RETRY_WAITS", [3.0, 5.0, 10.0]
        ), patch(
            "okooo_save_snapshot._page_blocked_now", side_effect=[False, True, False, False]
        ), patch("okooo_save_snapshot.time.sleep", return_value=None), patch(
            "okooo_save_snapshot._parse_asian_on_current_page", return_value=dict(asian)
        ), patch(
            "okooo_save_snapshot._parse_totals_on_current_page", return_value=dict(totals)
        ), patch(
            "okooo_save_snapshot._parse_europe_on_current_page", return_value=dict(europe)
        ), patch(
            "okooo_save_snapshot._parse_kelly_on_current_page", return_value=dict(kelly)
        ), patch(
            "okooo_save_snapshot._is_success_payload", return_value=True
        ), patch(
            "okooo_save_snapshot._score_europe_payload", return_value=(3, 2, 2)
        ):
            result = _extract_all_markets_from_hub(_DummyBrowser(), "https://m.okooo.com/match/history.php?MatchID=1")

        self.assertNotIn("blocked", result)
        self.assertTrue(result["europe"]["found"])
        self.assertEqual(result["europe"]["_ouzhi_retries"], 1)

    def test_extract_all_markets_from_hub_escalates_when_nothing_salvaged(self):
        asian = {"found": False}
        totals = {"found": False}

        with patch("okooo_save_snapshot._open_ready", return_value="ok"), patch(
            "okooo_save_snapshot._click_visible_text", return_value={"clicked": True}
        ), patch(
            "okooo_save_snapshot.OUZHI_RETRY_WAITS", []
        ), patch(
            "okooo_save_snapshot._page_blocked_now", side_effect=[False, True, True]
        ), patch("okooo_save_snapshot.time.sleep", return_value=None), patch(
            "okooo_save_snapshot._parse_asian_on_current_page", return_value=dict(asian)
        ), patch(
            "okooo_save_snapshot._parse_totals_on_current_page", return_value=dict(totals)
        ):
            result = _extract_all_markets_from_hub(_DummyBrowser(), "https://m.okooo.com/match/history.php?MatchID=1")

        self.assertTrue(result["blocked"])
        self.assertEqual(result["_blocked_at"], "europe,kelly")

    def test_extract_odds_only_from_hub_returns_blocked_when_walled(self):
        with patch("okooo_save_snapshot._open_ready", return_value="ok"), patch(
            "okooo_save_snapshot._is_blocked_text", return_value=False
        ), patch(
            "okooo_save_snapshot._click_visible_text", return_value={"clicked": True}
        ), patch(
            "okooo_save_snapshot.OUZHI_RETRY_WAITS", []
        ), patch(
            "okooo_save_snapshot._page_blocked_now", return_value=True
        ), patch("okooo_save_snapshot.time.sleep", return_value=None):
            result = _extract_odds_only_from_hub(_DummyBrowser(), "https://m.okooo.com/match/history.php?MatchID=1")

        self.assertTrue(result["blocked"])
        self.assertEqual(result["_blocked_at"], "europe,kelly")

    def test_fallback_recovers_odds_on_fresh_session_keeps_handicap(self):
        # Main hub run: 亚值/大小球 ok, 欧赔/凯利 walled.
        hub_result = {
            "found": True,
            "asian": {"found": True, "parsed": True, "average_row": {"home": "0.90"}},
            "totals": {"found": True, "parsed": True, "line": "2.5"},
            "europe": {"blocked": True, "_blocked_at": "hub_nav_ouzhi"},
            "kelly": {"blocked": True, "_blocked_at": "hub_nav_ouzhi"},
            "_partial_blocked_at": "europe,kelly",
        }
        # Fresh odds-only pass: 欧赔/凯利 recovered on a new fingerprint.
        odds_result = {
            "found": True,
            "europe": {"found": True, "parsed": True, "company_mode": "multi_company_consensus"},
            "kelly": {"found": True, "parsed": True},
        }

        def fake_run_with_retries(_label, _sp, _cf, extractor, *_a, **_k):
            if extractor is _extract_all_markets_from_hub:
                return dict(hub_result)
            return dict(odds_result)

        with patch("okooo_save_snapshot._run_with_retries", side_effect=fake_run_with_retries), patch(
            "okooo_save_snapshot._run_with_verification_reentry",
            side_effect=lambda runner, cf, sp: runner(cf, sp),
        ):
            merged = _extract_all_markets_with_fallback(
                "1315851", "https://m.okooo.com/match/history.php?MatchID=1315851",
                lambda s: _DummyBrowser(), "sess",
            )

        # 亚值/大小球 preserved from the first run, 欧赔/凯利 recovered from fresh session.
        self.assertTrue(merged["asian"]["found"])
        self.assertTrue(merged["totals"]["found"])
        self.assertTrue(merged["europe"]["found"])
        self.assertTrue(merged["kelly"]["found"])
        self.assertEqual(merged["europe"]["_recovered_via"], "odds_fresh_session")

    def test_fallback_skips_fresh_session_when_disabled(self):
        hub_result = {
            "found": True,
            "asian": {"found": True, "parsed": True},
            "totals": {"found": True, "parsed": True},
            "europe": {"blocked": True},
            "kelly": {"blocked": True},
        }

        reentry_called = {"n": 0}

        def fake_reentry(runner, cf, sp):
            reentry_called["n"] += 1
            return runner(cf, sp)

        with patch("okooo_save_snapshot._run_with_retries", return_value=dict(hub_result)), patch(
            "okooo_save_snapshot._run_with_verification_reentry", side_effect=fake_reentry
        ):
            merged = _extract_all_markets_with_fallback(
                "1", "https://m.okooo.com/match/history.php?MatchID=1",
                lambda s: _DummyBrowser(), "sess", odds_fresh_session_recovery=False,
            )

        self.assertEqual(reentry_called["n"], 0)
        self.assertTrue(merged["europe"]["blocked"])
        self.assertTrue(merged["asian"]["found"])

    def test_run_with_verification_reentry_stops_after_one_failed_reentry(self):
        blocked_profile = available_mobile_profiles()[0]
        calls = []

        def client_factory(_session):
            return _DummyRetryClient(None)

        def runner(factory, session_prefix):
            client = factory(f"{session_prefix}_1")
            calls.append((session_prefix, getattr(client.mobile_profile, "profile_id", None)))
            return {
                "blocked": True,
                "verification_required": True,
                "status": "verification_required",
                "error": "verification_required",
                "mobile_profile": {
                    "profile_id": blocked_profile.profile_id,
                    "device_pool_id": blocked_profile.device_pool_id,
                    "device_name": blocked_profile.device_name,
                    "user_agent": blocked_profile.user_agent,
                },
            }

        result = _run_with_verification_reentry(runner, client_factory, "eu")

        self.assertEqual(result["status"], "verification_required")
        self.assertEqual(result["verification_reentry_count"], 1)
        self.assertEqual(len(calls), 2)

    def test_run_with_retries_short_circuits_when_verification_breaker_is_open(self):
        open_okooo_verification_breaker(str(self.base_dir), "1315851", "odds_family")

        result = _run_with_retries(
            "odds_bundle",
            "unit_breaker",
            lambda _s: _DummyRetryClient(None),
            lambda _client, _match_id: {"found": True},
            "1315851",
            market_family="odds_family",
            base_dir=str(self.base_dir),
            breaker_match_id="1315851",
        )

        self.assertTrue(result["breaker_open"])
        self.assertEqual(result["error"], "ttl_circuit_open")
        self.assertEqual(result["_attempts"], [])

    def test_run_with_retries_opens_verification_breaker_on_blocked_result(self):
        result = _run_with_retries(
            "odds_bundle",
            "unit_breaker_open",
            lambda _s: _DummyRetryClient(available_mobile_profiles()[0]),
            lambda _client, _match_id: {"blocked": True, "url": "https://m.okooo.com/match/odds.php?MatchID=1315851"},
            "1315851",
            market_family="odds_family",
            base_dir=str(self.base_dir),
            breaker_match_id="1315851",
        )

        breaker = read_okooo_verification_breaker(str(self.base_dir), "1315851", "odds_family")
        self.assertTrue(_is_verification_required_payload(result))
        self.assertTrue(breaker["open"])

    def test_pick_preferred_europe_result_prefers_multi_company_consensus(self):
        average = {
            "found": True,
            "parsed": True,
            "company_mode": "average_row_fallback",
            "consensus": {"mode": "average_row_fallback", "company_count": 0, "filtered_company_count": 0},
            "companies": [],
        }
        consensus = {
            "found": True,
            "parsed": True,
            "company_mode": "multi_company_consensus",
            "consensus": {"mode": "multi_company_consensus", "company_count": 6, "filtered_company_count": 4},
            "companies": [{"company": "Bet365"}, {"company": "皇冠"}],
        }
        picked = _pick_preferred_europe_result(average, consensus)
        self.assertEqual(picked["company_mode"], "multi_company_consensus")
        self.assertEqual(len(picked["companies"]), 2)

    def test_time_tokens_support_midnight_24_hour_display(self):
        tokens = _time_tokens("00:00")
        self.assertIn("00:00", tokens)
        self.assertIn("0:00", tokens)
        self.assertIn("24:00", tokens)

    def test_candidate_date_hints_strict_identity_keeps_only_requested_date(self):
        self.assertEqual(_candidate_date_hints("2026-05-18", "00:00", strict_identity=True), ["2026-05-18"])

    def test_normalize_okooo_league_name_maps_runtime_labels(self):
        self.assertEqual(_normalize_okooo_league_name("瑞超"), "瑞典超")
        self.assertEqual(_normalize_okooo_league_name("allsvenskan"), "瑞典超")
        self.assertEqual(_normalize_okooo_league_name("eliteserien"), "挪超")
        self.assertEqual(_normalize_okooo_league_name("veikkausliiga"), "芬超")
        self.assertEqual(_normalize_okooo_league_name("friendly"), "友谊赛")

    def test_mobile_league_url_supports_non_major_leagues(self):
        self.assertEqual(_mobile_league_url("世界杯"), "https://m.okooo.com/saishi/16/")
        self.assertEqual(_mobile_league_url("瑞超"), "https://m.okooo.com/saishi/40/")
        self.assertEqual(_mobile_league_url("挪超"), "https://m.okooo.com/saishi/20/")
        self.assertEqual(_mobile_league_url("芬超"), "https://m.okooo.com/saishi/41/")

    def test_navigate_schedule_to_month_clicks_until_target_month(self):
        browser = _SequencedBrowser(
            [
                {"found": True, "year": 2025, "month": 8, "text": "2025年08月"},
                {"clicked": True, "direction": "下月", "text": "下月"},
                {"found": True, "year": 2025, "month": 9, "text": "2025年09月"},
                {"clicked": True, "direction": "下月", "text": "下月"},
                {"found": True, "year": 2025, "month": 10, "text": "2025年10月"},
            ]
        )

        with patch("okooo_save_snapshot.time.sleep", return_value=None):
            result = _navigate_schedule_to_month(browser, "2025-10-24", max_steps=4)

        self.assertTrue(result["matched"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["final_state"]["month"], 10)

    def test_find_match_id_relaxes_time_hint_after_exact_match_failure(self):
        browser = _DummyBrowser()
        fuzzy_calls = []

        def fake_find_rows_fuzzy(
            _bu,
            team1,
            team2,
            date_hint="",
            time_hint="",
            league="",
            alias_table=None,
            limit=5,
        ):
            fuzzy_calls.append((team1, team2, date_hint, time_hint, league, limit))
            if time_hint == "":
                return {
                    "count": 1,
                    "rows": [
                        {
                            "mid": "1302999",
                            "href": "https://m.okooo.com/match/history.php?MatchID=1302999",
                            "text": "05-18 埃尔切 赫塔费",
                            "score": 21.0,
                        }
                    ],
                }
            return {"count": 0, "rows": []}

        with patch("okooo_save_snapshot._find_rows_fuzzy", side_effect=fake_find_rows_fuzzy), patch(
            "okooo_save_snapshot._find_rows_anywhere_on_current_page",
            return_value={"count": 0, "rows": []},
        ), patch("okooo_save_snapshot.time.sleep", return_value=None), patch(
            "okooo_save_snapshot._mobile_league_url",
            return_value="https://m.okooo.com/soccer/league/Spain-LaLiga-2025-2026/",
        ):
            result = _find_match_id(
                browser,
                league="西甲",
                team1="埃尔切",
                team2="赫塔费",
                date_hint="2026-05-18",
                time_hint="00:00",
                alias_table={},
            )

        self.assertEqual(result["match_id"], "1302999")
        self.assertTrue(any(call[3] == "00:00" for call in fuzzy_calls))
        self.assertTrue(any(call[3] == "" for call in fuzzy_calls))

    def test_find_match_id_falls_back_to_daily_schedule_cache(self):
        browser = _DummyBrowser()
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_path = Path(temp_dir) / "2026-05-18.json"
            schedule_path.write_text(
                json.dumps(
                    {
                        "league": "西甲",
                        "date": "2026-05-18",
                        "date_click": {"clicked": True},
                        "matches": [
                            {
                                "match_id": "1302914",
                                "history_url": "https://m.okooo.com/match/history.php?MatchID=1302914",
                                "home_team": "埃尔切",
                                "away_team": "赫塔费",
                                "kickoff_time": "01:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            with patch("okooo_save_snapshot._find_rows_fuzzy", return_value={"count": 0, "rows": []}), patch(
                "okooo_save_snapshot._find_rows_anywhere_on_current_page",
                return_value={"count": 0, "rows": []},
            ), patch("okooo_save_snapshot.time.sleep", return_value=None), patch(
                "okooo_save_snapshot._mobile_league_url",
                return_value="https://m.okooo.com/soccer/league/Spain-LaLiga-2025-2026/",
            ), patch(
                "okooo_save_snapshot._ensure_daily_schedule_cache",
                return_value=schedule_path,
            ):
                result = _find_match_id(
                    browser,
                    league="西甲",
                    team1="埃尔切",
                    team2="赫塔费",
                    date_hint="2026-05-18",
                    time_hint="00:00",
                    alias_table={},
                )

        self.assertEqual(result["match_id"], "1302914")
        self.assertEqual(result.get("_source"), "daily_schedule_cache")
        self.assertEqual((result.get("schedule_row") or {}).get("home_team"), "埃尔切")
        self.assertEqual(
            (result.get("schedule_row") or {}).get("href"),
            "https://m.okooo.com/match/history.php?MatchID=1302914",
        )

    def test_find_match_id_falls_back_to_online_match_finder(self):
        browser = _DummyBrowser()

        class _Finder:
            def find_match_id(self, team1, team2, league_hint=None):
                if team1 == "库普斯" and team2 == "国际图尔库" and league_hint == "芬超":
                    return "1319001"
                return None

        with patch("okooo_save_snapshot._find_rows_fuzzy", return_value={"count": 0, "rows": []}), patch(
            "okooo_save_snapshot._find_rows_anywhere_on_current_page",
            return_value={"count": 0, "rows": []},
        ), patch(
            "okooo_save_snapshot._find_match_id_from_schedule_cache",
            return_value={},
        ), patch(
            "okooo_match_finder.OkoooMatchFinder",
            return_value=_Finder(),
        ), patch("okooo_save_snapshot.time.sleep", return_value=None):
            result = _find_match_id(
                browser,
                league="芬超",
                team1="库奥皮奥",
                team2="国际图尔库",
                date_hint="2026-05-30",
                time_hint="",
                alias_table={
                    "veikkausliiga": {
                        "库奥皮奥": ["库普斯"],
                        "国际图尔库": ["国际图尔库"],
                    }
                },
            )

        self.assertEqual(result["match_id"], "1319001")
        self.assertEqual(result.get("_source"), "online_match_finder")

    def test_find_match_id_for_friendly_prefers_schedule_page_before_online_search(self):
        browser = _DummyBrowser()
        row_result = {
            "count": 1,
            "rows": [
                {
                    "mid": "7721606",
                    "href": "https://m.okooo.com/match/history.php?MatchID=7721606",
                    "text": "06-06 比利时 21:00 突尼斯",
                    "score": 28.0,
                }
            ],
        }

        with patch("okooo_save_snapshot._find_rows_in_date_section", return_value=row_result) as mock_section, patch(
            "okooo_save_snapshot._find_rows_fuzzy", return_value=row_result
        ), patch("okooo_save_snapshot._find_match_id_via_online_search") as mock_online, patch(
            "okooo_save_snapshot._navigate_schedule_to_month", return_value={"matched": True}
        ), patch("okooo_save_snapshot._mobile_league_url", return_value="https://m.okooo.com/saishi/851/"), patch(
            "okooo_save_snapshot.time.sleep", return_value=None
        ):
            result = _find_match_id(
                browser,
                league="友谊赛",
                team1="比利时",
                team2="突尼斯",
                date_hint="2026-06-06",
                time_hint="21:00",
                alias_table={},
                strict_identity=True,
            )

        self.assertEqual(result["match_id"], "7721606")
        # 赛程页优先：先经 in-date-section 命中即短路，不应回落到在线搜索。
        mock_section.assert_called()
        mock_online.assert_not_called()

    def test_find_match_id_for_friendly_falls_back_to_schedule_cache_when_online_misses(self):
        browser = _DummyBrowser()
        cached = {
            "match_id": "7721607",
            "schedule_row": {
                "mid": "7721607",
                "href": "https://m.okooo.com/match/history.php?MatchID=7721607",
                "text": "葡萄牙 vs 智利",
            },
            "_source": "daily_schedule_cache",
        }

        with patch("okooo_save_snapshot._find_match_id_via_online_search", return_value={}) as mock_online, patch(
            "okooo_save_snapshot._find_rows_fuzzy", return_value={}
        ) as mock_fuzzy, patch("okooo_save_snapshot._find_rows_in_date_section", return_value={}), patch(
            "okooo_save_snapshot._mobile_league_url", return_value=None
        ), patch("okooo_save_snapshot._find_match_id_from_schedule_cache", return_value=cached) as mock_cache:
            result = _find_match_id(
                browser,
                league="友谊赛",
                team1="葡萄牙",
                team2="智利",
                date_hint="2026-06-06",
                time_hint="21:00",
                alias_table={},
                strict_identity=True,
            )

        self.assertEqual(result["match_id"], "7721607")
        self.assertEqual(result.get("_source"), "daily_schedule_cache")
        mock_online.assert_called()
        self.assertGreaterEqual(mock_fuzzy.call_count, 1)
        mock_cache.assert_called_once()

    def test_team_aliases_for_friendly_can_borrow_cross_bucket_aliases(self):
        aliases = _team_aliases(
            {
                "ligue_1": {"摩纳哥": ["Monaco", "AS Monaco"]},
                "world_cup": {"挪威": ["Norway", "NOR"]},
                "世界杯": {"挪威": ["挪威队"]},
            },
            "友谊赛",
            "摩纳哥",
        )
        self.assertIn("Monaco", aliases)
        self.assertIn("AS Monaco", aliases)

    def test_find_match_id_for_world_cup_prefers_schedule_page_before_online_search(self):
        browser = _DummyBrowser()
        row_result = {
            "count": 1,
            "rows": [
                {
                    "mid": "1606601",
                    "href": "https://m.okooo.com/match/history.php?MatchID=1606601",
                    "text": "06-18 阿根廷 21:00 墨西哥",
                    "score": 28.0,
                }
            ],
        }

        with patch("okooo_save_snapshot._find_rows_in_date_section", return_value=row_result) as mock_section, patch(
            "okooo_save_snapshot._find_rows_fuzzy", return_value=row_result
        ), patch("okooo_save_snapshot._find_match_id_via_online_search") as mock_online, patch(
            "okooo_save_snapshot._navigate_schedule_to_month", return_value={"matched": True}
        ), patch("okooo_save_snapshot._mobile_league_url", return_value="https://m.okooo.com/saishi/16/"), patch(
            "okooo_save_snapshot.time.sleep", return_value=None
        ):
            result = _find_match_id(
                browser,
                league="世界杯",
                team1="阿根廷",
                team2="墨西哥",
                date_hint="2026-06-18",
                time_hint="21:00",
                alias_table={},
            )

        self.assertEqual(result["match_id"], "1606601")
        # 赛程页优先：先经 in-date-section 命中即短路，不应回落到在线搜索。
        mock_section.assert_called()
        mock_online.assert_not_called()

    def test_find_match_id_for_non_big_five_league_prefers_schedule_page_before_online_search(self):
        browser = _DummyBrowser()
        row_result = {
            "count": 1,
            "rows": [
                {
                    "mid": "1319001",
                    "href": "https://m.okooo.com/match/history.php?MatchID=1319001",
                    "text": "05-30 库普斯 国际图尔库",
                    "score": 28.0,
                }
            ],
        }

        with patch("okooo_save_snapshot._find_rows_in_date_section", return_value=row_result) as mock_section, patch(
            "okooo_save_snapshot._find_rows_fuzzy", return_value=row_result
        ), patch("okooo_save_snapshot._find_match_id_via_online_search") as mock_online, patch(
            "okooo_save_snapshot._navigate_schedule_to_month", return_value={"matched": True}
        ), patch("okooo_save_snapshot._mobile_league_url", return_value="https://m.okooo.com/saishi/41/"), patch(
            "okooo_save_snapshot.time.sleep", return_value=None
        ):
            result = _find_match_id(
                browser,
                league="芬超",
                team1="库奥皮奥",
                team2="国际图尔库",
                date_hint="2026-05-30",
                time_hint="",
                alias_table={"veikkausliiga": {"库奥皮奥": ["库普斯"]}},
            )

        self.assertEqual(result["match_id"], "1319001")
        # 赛程页优先：先经 in-date-section 命中即短路，不应回落到在线搜索。
        mock_section.assert_called()
        mock_online.assert_not_called()

    def test_find_match_id_from_schedule_cache_skips_invalid_clicked_false_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            schedule_path = Path(temp_dir) / "2026-05-18.json"
            schedule_path.write_text(
                json.dumps(
                    {
                        "league": "西甲",
                        "date": "2026-05-18",
                        "date_click": {"clicked": False},
                        "matches": [
                            {
                                "match_id": "1302914",
                                "history_url": "https://m.okooo.com/match/history.php?MatchID=1302914",
                                "home_team": "埃尔切",
                                "away_team": "赫塔费",
                                "kickoff_time": "01:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch("okooo_save_snapshot._ensure_daily_schedule_cache", return_value=schedule_path):
                result = _find_match_id_from_schedule_cache(
                    league="西甲",
                    team1="埃尔切",
                    team2="赫塔费",
                    candidate_dates=["2026-05-18"],
                    alias_table={},
                )

        self.assertEqual(result, {})

    def test_select_best_schedule_row_prefers_tighter_team_pair_over_round_summary(self):
        rows = [
            {
                "mid": "1300078",
                "text": "2026-05-16第34轮拜仁完5:1科隆第34轮勒沃库森完1:1汉堡第34轮门兴完4:0霍芬海姆第34轮柏林联合完4:0奥格斯堡",
                "score": 28.0,
            },
            {
                "mid": "1300083",
                "text": "05-16门兴格拉德巴赫vs霍芬海姆",
                "score": 26.0,
            },
        ]

        best = _select_best_schedule_row(
            rows,
            team1="门兴格拉德巴赫",
            team2="霍芬海姆",
        )

        self.assertEqual(best["mid"], "1300083")

    def test_find_match_id_prefers_best_local_candidate_over_first_row(self):
        browser = _DummyBrowser()
        with patch(
            "okooo_save_snapshot._find_rows_fuzzy",
            return_value={
                "count": 2,
                "rows": [
                    {
                        "mid": "1300078",
                        "href": "https://m.okooo.com/match/history.php?MatchID=1300078",
                        "text": "2026-05-16第34轮拜仁完5:1科隆第34轮勒沃库森完1:1汉堡第34轮门兴完4:0霍芬海姆第34轮柏林联合完4:0奥格斯堡",
                        "score": 28.0,
                    },
                    {
                        "mid": "1300083",
                        "href": "https://m.okooo.com/match/history.php?MatchID=1300083",
                        "text": "05-16门兴格拉德巴赫vs霍芬海姆",
                        "score": 26.0,
                    },
                ],
            },
        ), patch(
            "okooo_save_snapshot._find_rows_anywhere_on_current_page",
            return_value={"count": 0, "rows": []},
        ), patch("okooo_save_snapshot.time.sleep", return_value=None):
            result = _find_match_id(
                browser,
                league="德甲",
                team1="门兴格拉德巴赫",
                team2="霍芬海姆",
                date_hint="2026-05-17",
                time_hint="",
                alias_table={},
            )

        self.assertEqual(result["match_id"], "1300083")

    def test_find_match_id_skips_broad_round_summary_and_uses_schedule_cache(self):
        browser = _DummyBrowser()
        cached = {
            "match_id": "1300083",
            "schedule_row": {
                "mid": "1300083",
                "href": "https://m.okooo.com/match/history.php?MatchID=1300083",
                "text": "05-17 门兴格拉德巴赫 霍芬海姆",
            },
            "_source": "daily_schedule_cache",
        }
        with patch(
            "okooo_save_snapshot._find_rows_fuzzy",
            return_value={
                "count": 1,
                "rows": [
                    {
                        "mid": "1300078",
                        "href": "https://m.okooo.com/match/history.php?MatchID=1300078",
                        "text": "2026-05-16第34轮拜仁完5:1科隆第34轮勒沃库森完1:1汉堡第34轮门兴完4:0霍芬海姆第34轮柏林联合完4:0奥格斯堡",
                        "score": 28.0,
                    }
                ],
            },
        ), patch(
            "okooo_save_snapshot._find_rows_anywhere_on_current_page",
            return_value={"count": 0, "rows": []},
        ), patch(
            "okooo_save_snapshot._find_match_id_from_schedule_cache",
            return_value=cached,
        ), patch("okooo_save_snapshot.time.sleep", return_value=None):
            result = _find_match_id(
                browser,
                league="德甲",
                team1="门兴格拉德巴赫",
                team2="霍芬海姆",
                date_hint="2026-05-17",
                time_hint="",
                alias_table={},
            )

        self.assertEqual(result["match_id"], "1300083")
        self.assertEqual(result.get("_source"), "daily_schedule_cache")

    def test_find_existing_snapshot_by_match_id_rejects_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            wrong_path = out_dir / "拜仁慕尼黑vs科隆.json"
            wrong_path.write_text(
                json.dumps(
                    {
                        "match_id": "1300078",
                        "home_team": "柏林联合",
                        "away_team": "奥格斯堡",
                        "match_date": "2026-05-17",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            found = _find_existing_snapshot_by_match_id(
                out_dir,
                "1300078",
                home_team="拜仁慕尼黑",
                away_team="科隆",
                match_date="2026-05-17",
            )

        self.assertIsNone(found)

    def test_find_existing_snapshot_by_match_id_accepts_identity_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            snapshot_path = out_dir / "拜仁慕尼黑vs科隆.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "match_id": "1300078",
                        "home_team": "拜仁慕尼黑",
                        "away_team": "科隆",
                        "match_date": "2026-05-17",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            found = _find_existing_snapshot_by_match_id(
                out_dir,
                "1300078",
                home_team="拜仁慕尼黑",
                away_team="科隆",
                match_date="2026-05-17",
            )

        self.assertEqual(found, snapshot_path)

    def test_refresh_snapshot_retries_without_match_id_once_when_initial_payload_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            script_path = base_dir / "okooo_save_snapshot.py"
            script_path.write_text("# stub", encoding="utf-8")
            first_path = base_dir / "first.json"
            second_path = base_dir / "second.json"
            first_path.write_text(
                json.dumps(
                    {
                        "match_id": "wrong-id",
                        "home_team": "其他主队",
                        "away_team": "其他客队",
                        "match_date": "2026-05-16",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            second_path.write_text(
                json.dumps(
                    {
                        "match_id": "1296096",
                        "home_team": "阿斯顿维拉",
                        "away_team": "利物浦",
                        "match_date": "2026-05-16",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = []

            class _Completed:
                def __init__(self, stdout):
                    self.returncode = 0
                    self.stdout = stdout
                    self.stderr = ""

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(list(cmd))
                if "--match-id" in cmd:
                    return _Completed(f"{first_path}\n")
                return _Completed(f"{second_path}\n")

            with patch("okooo_live_snapshot.subprocess.run", side_effect=fake_run):
                result = refresh_snapshot(
                    str(base_dir),
                    "premier_league",
                    "阿斯顿维拉",
                    "利物浦",
                    "2026-05-16",
                    driver="local-chrome",
                    match_id="1296096",
                )

        self.assertEqual(len(calls), 2)
        self.assertIn("--match-id", calls[0])
        self.assertNotIn("--match-id", calls[1])
        self.assertEqual(Path(result[0]).name, "阿斯顿维拉vs利物浦.json")
        self.assertEqual(result[1]["match_id"], "1296096")

    def test_refresh_snapshot_strict_identity_does_not_retry_without_match_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            script_path = base_dir / "okooo_save_snapshot.py"
            script_path.write_text("# stub", encoding="utf-8")
            first_path = base_dir / "first.json"
            first_path.write_text(
                json.dumps(
                    {
                        "match_id": "wrong-id",
                        "home_team": "其他主队",
                        "away_team": "其他客队",
                        "match_date": "2026-05-16",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = []

            class _Completed:
                def __init__(self, stdout):
                    self.returncode = 0
                    self.stdout = stdout
                    self.stderr = ""

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(list(cmd))
                return _Completed(f"{first_path}\n")

            with patch("okooo_live_snapshot.subprocess.run", side_effect=fake_run):
                result = refresh_snapshot(
                    str(base_dir),
                    "premier_league",
                    "阿斯顿维拉",
                    "利物浦",
                    "2026-05-16",
                    driver="local-chrome",
                    match_id="1296096",
                    strict_identity=True,
                )

        self.assertEqual(len(calls), 1)
        self.assertIn("--strict-identity", calls[0])
        self.assertIn("--match-id", calls[0])
        self.assertEqual(result[0], str(first_path))
        self.assertEqual(result[1]["match_id"], "wrong-id")

    def test_refresh_snapshot_uses_league_name_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            script_path = base_dir / "okooo_save_snapshot.py"
            script_path.write_text("# stub", encoding="utf-8")
            first_path = base_dir / "friendly.json"
            first_path.write_text(
                json.dumps(
                    {
                        "match_id": "7721606",
                        "home_team": "比利时",
                        "away_team": "突尼斯",
                        "match_date": "2026-06-06",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = []

            class _Completed:
                def __init__(self, stdout):
                    self.returncode = 0
                    self.stdout = stdout
                    self.stderr = ""

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(list(cmd))
                return _Completed(f"{first_path}\n")

            with patch("okooo_live_snapshot.subprocess.run", side_effect=fake_run):
                result = refresh_snapshot(
                    str(base_dir),
                    "world_cup",
                    "比利时",
                    "突尼斯",
                    "2026-06-06",
                    driver="local-chrome",
                    league_name_override="友谊赛",
                )

        self.assertEqual(Path(result[0]).name, "比利时vs突尼斯.json")
        self.assertEqual(result[1]["match_id"], "7721606")
        self.assertIn("--league", calls[0])
        self.assertIn("友谊赛", calls[0])

    def test_find_match_id_strict_identity_skips_schedule_cache_fallback(self):
        browser = _DummyBrowser()
        with patch("okooo_save_snapshot._find_rows_fuzzy", return_value={"count": 0, "rows": []}), patch(
            "okooo_save_snapshot._find_rows_anywhere_on_current_page",
            return_value={"count": 0, "rows": []},
        ), patch("okooo_save_snapshot._find_match_id_from_schedule_cache") as mock_cache, patch(
            "okooo_save_snapshot.time.sleep", return_value=None
        ), patch(
            "okooo_save_snapshot._mobile_league_url",
            return_value="https://m.okooo.com/soccer/league/England-PremierLeague-2025-2026/",
        ):
            with self.assertRaises(RuntimeError):
                _find_match_id(
                    browser,
                    league="英超",
                    team1="阿斯顿维拉",
                    team2="利物浦",
                    date_hint="2026-05-16",
                    time_hint="00:00",
                    alias_table={},
                    strict_identity=True,
                )

        mock_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
