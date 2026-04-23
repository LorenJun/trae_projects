#!/usr/bin/env python3
"""
ResultManager 和 DataCollector 的回归测试。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_collector import DataCollector
from result_manager import ResultManager


class ResultManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | - | 皇家马德里 | 预测:主胜 信心:0.61 爆冷:低 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        self.manager = ResultManager()
        self.manager.base_dir = str(self.base_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_pending_matches_reads_from_teams_file(self):
        pending = self.manager.get_pending_matches(days_back=30)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(pending[0]["predicted_winner"], "home")

    def test_save_result_updates_score_and_correctness_marker(self):
        result = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
        self.assertEqual(result["actual_score"], "2-1")
        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 | 预测:主胜 信心:0.61 爆冷:低 ✅ |", content)


class DataCollectorTest(unittest.TestCase):
    def test_degrades_to_mock_when_browser_use_missing(self):
        with patch("importlib.util.find_spec", return_value=None):
            collector = DataCollector()

        self.assertEqual([scraper.name for scraper in collector.scrapers], ["mock"])
        matches = asyncio.run(collector.collect_league_data("la_liga", "2026-05-11", use_cache=False))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].sources, ["mock"])


if __name__ == "__main__":
    unittest.main()
