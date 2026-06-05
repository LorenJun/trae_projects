import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from okooo_live_snapshot import refresh_snapshot
from okooo_save_snapshot import (
    _candidate_date_hints,
    _find_existing_snapshot_by_match_id,
    _find_match_id,
    _find_match_id_from_schedule_cache,
    _mobile_league_url,
    _navigate_schedule_to_month,
    _normalize_okooo_league_name,
    _parse_desktop_avg_row,
    _pick_preferred_europe_result,
    _select_best_schedule_row,
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


class OkoooSaveSnapshotTest(unittest.TestCase):
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

    def test_parse_desktop_avg_row_supports_compact_average_triplets(self):
        parsed = _parse_desktop_avg_row(["99家平均", "2.243.093.27", "2.332.903.55", ">"])
        self.assertTrue(parsed["found"])
        self.assertEqual(parsed["initial"], {"home": 2.24, "draw": 3.09, "away": 3.27})
        self.assertEqual(parsed["final"], {"home": 2.33, "draw": 2.9, "away": 3.55})
        self.assertEqual(parsed["delta"], {"home": 0.09, "draw": -0.19, "away": 0.28})

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

    def test_mobile_league_url_supports_non_major_leagues(self):
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
