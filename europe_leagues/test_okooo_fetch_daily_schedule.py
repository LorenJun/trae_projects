import unittest

from okooo_fetch_daily_schedule import _parse_row_text


class OkoooFetchDailyScheduleTest(unittest.TestCase):
    def test_parse_row_text_ignores_operation_labels(self):
        parsed = _parse_row_text("05-24 伯恩利 23:00 狼队 盈亏 亚指 欧指 分析 预测 AI 积分 阵容")
        self.assertEqual(parsed["home_team"], "伯恩利")
        self.assertEqual(parsed["away_team"], "狼队")
        self.assertEqual(parsed["kickoff_time"], "23:00")

    def test_parse_row_text_handles_full_date_and_round_prefix(self):
        parsed = _parse_row_text("2026-05-24 第38轮 伯恩利 23:00 狼队")
        self.assertEqual(parsed["home_team"], "伯恩利")
        self.assertEqual(parsed["away_team"], "狼队")
        self.assertEqual(parsed["kickoff_time"], "23:00")


if __name__ == "__main__":
    unittest.main()
