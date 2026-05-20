import unittest

from update_five_leagues_schedule_times import (
    MarkdownRow,
    SourceMatch,
    build_round_table,
    choose_later_schedule,
    merge_fallback_schedule,
)


class UpdateFiveLeaguesScheduleTimesTest(unittest.TestCase):
    def test_choose_later_schedule_prefers_later_time_on_same_date(self):
        chosen = choose_later_schedule("2026-05-18", "20:00", "2026-05-18", "22:00")
        self.assertEqual(chosen, ("2026-05-18", "22:00"))

    def test_choose_later_schedule_prefers_later_date_even_if_time_earlier(self):
        chosen = choose_later_schedule("2026-05-18", "23:30", "2026-05-19", "00:30")
        self.assertEqual(chosen, ("2026-05-19", "00:30"))

    def test_choose_later_schedule_preserves_existing_valid_time_when_fetched_time_missing(self):
        chosen = choose_later_schedule("2026-05-18", "21:00", "2026-05-18", "")
        self.assertEqual(chosen, ("2026-05-18", "21:00"))

    def test_merge_fallback_schedule_only_replaces_when_fallback_is_later(self):
        rounds = {
            37: [
                SourceMatch(37, "2026-05-18", "20:00", "埃尔切", "赫塔费", "-", False),
                SourceMatch(37, "2026-05-18", "22:00", "皇家马德里", "巴塞罗那", "-", False),
            ]
        }
        fallback_rows = {
            (37, "埃尔切", "赫塔费"): ("2026-05-18", "22:00"),
            (37, "皇家马德里", "巴塞罗那"): ("2026-05-18", "21:00"),
        }

        merged = merge_fallback_schedule(rounds, fallback_rows)

        self.assertEqual(merged[37][0].time, "22:00")
        self.assertEqual(merged[37][1].time, "22:00")

    def test_build_round_table_keeps_later_existing_markdown_schedule(self):
        source_rows = [
            SourceMatch(37, "2026-05-18", "20:00", "埃尔切", "赫塔费", "-", False),
        ]
        existing_rows = {
            ("埃尔切", "赫塔费"): MarkdownRow(
                date="2026-05-18",
                time="22:00",
                home_team="埃尔切",
                score="-",
                away_team="赫塔费",
                remark="进行中；预测:平局",
            )
        }

        lines = build_round_table(source_rows, existing_rows)

        self.assertIn("| 2026-05-18 | 22:00 | 埃尔切 | - | 赫塔费 | 进行中；预测:平局 |\n", lines)

    def test_build_round_table_uses_later_fetched_schedule_when_it_exceeds_markdown(self):
        source_rows = [
            SourceMatch(37, "2026-05-19", "00:30", "埃尔切", "赫塔费", "-", False),
        ]
        existing_rows = {
            ("埃尔切", "赫塔费"): MarkdownRow(
                date="2026-05-18",
                time="22:00",
                home_team="埃尔切",
                score="-",
                away_team="赫塔费",
                remark="进行中",
            )
        }

        lines = build_round_table(source_rows, existing_rows)

        self.assertIn("| 2026-05-19 | 00:30 | 埃尔切 | - | 赫塔费 | 进行中 |\n", lines)


if __name__ == "__main__":
    unittest.main()
