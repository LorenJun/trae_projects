import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from okooo_live_snapshot import refresh_snapshot
from okooo_save_snapshot import _find_match_id, _parse_desktop_avg_row, _pick_preferred_europe_result, _time_tokens


class _DummyBrowser:
    def open(self, _url):
        return None

    def eval_json(self, _expr):
        return {"clicked": True}


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


if __name__ == "__main__":
    unittest.main()
