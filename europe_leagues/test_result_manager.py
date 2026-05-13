#!/usr/bin/env python3
"""模块说明：验证 ResultManager 赛果写回与准确率统计逻辑的测试脚本。

ResultManager 和 DataCollector 的回归测试。"""

import asyncio
import contextlib
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli import _parse_memory_completed_entry
from data_collector import DataCollector
from result_manager import ResultManager
from upset_case_library import 创建爆冷案例, 爆冷案例库


@contextlib.contextmanager
def quiet_test_output():
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            yield
    finally:
        logging.disable(previous_disable)


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
        self.memory_path = self.base_dir.parent / "MEMORY.md"
        self.memory_path.write_text(
            "\n".join(
                [
                    "### 预测结果滚动记忆",
                    "",
                    "<!-- prediction-memory:start -->",
                    "> 滚动预测准确率： 暂无已完赛样本",
                    "",
                    "#### 未完赛",
                    "",
                    "- [la_liga|2026-05-10|巴塞罗那|皇家马德里] 2026-05-10 西甲 巴塞罗那 vs 皇家马德里",
                    "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  ▲ 风险: 低(10) 测试样本",
                    "  · 记忆ID: la_liga|2026-05-10|巴塞罗那|皇家马德里 | 更新时间: 2026-05-10 01:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "prediction_archive.json").write_text(
            json.dumps(
                {
                    "la_liga_20260511_巴塞罗那_皇家马德里": {
                        "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                        "league": "la_liga",
                        "league_name": "西甲联赛",
                        "match_date": "2026-05-11",
                        "home_team": "巴塞罗那",
                        "away_team": "皇家马德里",
                        "prediction": "主胜",
                        "predicted_winner": "home",
                        "predicted_scores": ["2-1", "1-0"],
                        "top_scores": [["2-1", 0.2], ["1-0", 0.1]],
                        "over_under": {"available": False, "reason": "missing_real_market_line"},
                        "market_snapshot": {},
                        "confidence": 0.61,
                        "upset_potential": {"level": "低", "index": 10, "factors": ["测试样本"]},
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.manager = ResultManager(base_dir=str(self.base_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_pending_matches_reads_from_teams_file(self):
        with quiet_test_output():
            pending = self.manager.get_pending_matches(days_back=30)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(pending[0]["predicted_winner"], "home")

    def test_save_result_updates_score_and_correctness_marker(self):
        with quiet_test_output():
            result = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
        self.assertEqual(result["actual_score"], "2-1")
        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 | 已完赛；预测:主胜 信心:0.61 爆冷:低；赛果:主胜 2-1；复盘:胜平负命中 比分缺失 大小球缺失 |", content)

    def test_save_result_requires_force_to_override_existing_score(self):
        with quiet_test_output():
            self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
            skipped = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 3, 2)
        self.assertTrue(skipped["already_exists"])
        self.assertEqual(skipped["actual_score"], "2-1")

        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 |", content)
        self.assertNotIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 3-2 | 皇家马德里 |", content)

        with quiet_test_output():
            forced = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 3, 2, force=True)
        self.assertTrue(forced["overwritten"])
        self.assertEqual(forced["previous_score"], "2-1")
        self.assertEqual(forced["actual_score"], "3-2")

        updated_content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 3-2 | 皇家马德里 | 已完赛；预测:主胜 信心:0.61 爆冷:低；赛果:主胜 3-2；复盘:胜平负命中 比分缺失 大小球缺失 |", updated_content)

    def test_save_result_reconciles_stale_memory_pending_entry(self):
        with quiet_test_output():
            self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertNotIn("#### 未完赛", memory_text)
        self.assertIn("#### 已完赛", memory_text)
        self.assertIn("■ 赛果: 主胜 2-1", memory_text)
        self.assertIn("- [la_liga|2026-05-11|巴塞罗那|皇家马德里] 2026-05-11 西甲联赛 巴塞罗那 vs 皇家马德里", memory_text)

    def test_save_prediction_from_enhanced_archives_review_and_market_fields(self):
        enhanced_pred = {
            "match_id": "external-123",
            "match_date": "2026-05-11",
            "match_time": "03:00",
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "prediction": "主胜",
            "confidence": 0.61,
            "final_probabilities": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
            "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
            "over_under": {"available": True, "line": 2.75, "over": 0.44, "under": 0.56},
            "market_snapshot": {
                "欧赔": {
                    "initial": {"home": 2.12, "draw": 3.3, "away": 3.45},
                    "final": {"home": 2.25, "draw": 3.12, "away": 3.18},
                },
                "亚值": {
                    "initial": {"handicap_value": -0.5},
                    "final": {"handicap_value": -0.25},
                },
                "大小球": {
                    "initial": {"line": 2.5},
                    "final": {"line": 2.75},
                },
            },
            "strength_diff": 14.5,
            "rag_decision": {
                "available": True,
                "risk_bonus": 7,
                "confidence_penalty": 0.018,
                "scenario_tags": ["upset_case_cluster", "market_case_opposes_pick"],
            },
            "runtime_profile": {"mode": "unit-test"},
            "realtime": {
                "context_applied": {
                    "posterior_outcome_pipeline": {
                        "baseline": {"home_win": 0.55, "draw": 0.24, "away_win": 0.21},
                        "final": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
                        "stages_applied": ["review_outcome_adjustment"],
                    },
                    "posterior_outcome_guard": {"applied": False, "reason": "within_limit"},
                    "review_outcome_adjustment": {
                        "applied": True,
                        "stratified_review": {"bucket_key": "home:level_shallow"},
                        "three_layer_review": {"bucket_key": "home:level_shallow:draw_guarded"},
                    },
                }
            },
        }
        with patch.object(self.manager.teams_writeback, "write_prediction", return_value=None):
            with quiet_test_output():
                self.manager.save_prediction_from_enhanced(enhanced_pred, "la_liga")

        archive = self.manager.prediction_archive_store.load()
        archived = archive["la_liga_20260511_巴塞罗那_皇家马德里"]
        self.assertEqual(archived["strength_diff"], 14.5)
        self.assertEqual(archived["asian_line"], -0.25)
        self.assertEqual(archived["asian_line_initial"], -0.5)
        self.assertEqual(archived["ou_line"], 2.75)
        self.assertEqual(archived["euro_home"], 2.25)
        self.assertEqual(archived["euro_home_initial"], 2.12)
        self.assertEqual(archived["review_bucket_key"], "home:level_shallow")
        self.assertEqual(archived["three_layer_bucket_key"], "home:level_shallow:draw_guarded")
        self.assertEqual(archived["rag_risk_bonus"], 7)
        self.assertEqual(archived["rag_confidence_penalty"], 0.018)
        self.assertEqual(archived["rag_scenario_tags"], ["upset_case_cluster", "market_case_opposes_pick"])
        self.assertEqual(archived["posterior_outcome_pipeline"]["stages_applied"], ["review_outcome_adjustment"])

    def test_save_prediction_from_enhanced_preserves_zero_final_market_values(self):
        enhanced_pred = {
            "match_id": "external-456",
            "match_date": "2026-05-12",
            "match_time": "03:00",
            "home_team": "奥萨苏纳",
            "away_team": "马德里竞技",
            "prediction": "平局",
            "confidence": 0.52,
            "final_probabilities": {"home_win": 0.3, "draw": 0.4, "away_win": 0.3},
            "top_scores": [("1-1", 0.18), ("0-0", 0.12)],
            "over_under": {"available": True, "line": 0.0, "over": 0.48, "under": 0.52},
            "market_snapshot": {
                "欧赔": {
                    "initial": {"home": 2.35, "draw": 3.1, "away": 2.88},
                    "final": {"home": 0.0, "draw": 3.0, "away": 2.9},
                },
                "亚值": {
                    "initial": {"handicap_value": -0.25},
                    "final": {"handicap_value": 0.0},
                },
                "大小球": {
                    "initial": {"line": 2.5},
                    "final": {"line": 0.0},
                },
            },
            "runtime_profile": {"mode": "unit-test"},
            "realtime": {"context_applied": {}},
        }
        with patch.object(self.manager.teams_writeback, "write_prediction", return_value=None):
            with quiet_test_output():
                self.manager.save_prediction_from_enhanced(enhanced_pred, "la_liga")

        archive = self.manager.prediction_archive_store.load()
        archived = next(item for item in archive.values() if item.get("external_match_id") == "external-456")
        self.assertEqual(archived["asian_line"], 0.0)
        self.assertEqual(archived["asian_line_initial"], -0.25)
        self.assertEqual(archived["ou_line"], 0.0)
        self.assertEqual(archived["ou_line_initial"], 2.5)
        self.assertEqual(archived["euro_home"], 0.0)
        self.assertEqual(archived["euro_home_initial"], 2.35)

    def test_review_entry_upset_sync_uses_review_sample_fields(self):
        entry_text = "\n".join(
            [
                "- [la_liga|2026-05-11|巴塞罗那|皇家马德里] 2026-05-11 西甲联赛 巴塞罗那 vs 皇家马德里",
                "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 小球 2.75 (60.0%)",
                "  ■ 赛果: 客胜 1-2",
                "  · 记忆ID: la_liga_20260511_巴塞罗那_皇家马德里 | 更新时间: 2026-05-11 12:00:00",
            ]
        )
        parsed_entry = _parse_memory_completed_entry(entry_text)
        self.assertEqual(parsed_entry["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(parsed_entry["league"], "la_liga")
        self.assertEqual(parsed_entry["confidence_pct"], 61.0)

        with patch.object(self.manager, "_auto_sync_upset_case", return_value={"status": "added", "case_id": "case-1"}) as mocked:
            result = self.manager.sync_upset_cases_from_review_entries([parsed_entry])

        self.assertEqual(result["total_entries"], 1)
        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(result["added_count"], 1)
        payload = mocked.call_args.args[0]
        self.assertEqual(payload["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(payload["predicted_winner"], "home")
        self.assertEqual(payload["actual_winner"], "away")
        self.assertEqual(payload["actual_score"], "1-2")
        self.assertEqual(payload["note"], "信心:0.61")

    def test_parse_memory_completed_entry_keeps_pipe_style_memory_id(self):
        entry_text = "\n".join(
            [
                "- [bundesliga|2026-05-09|多特蒙德|法兰克福] 2026-05-09 德甲 多特蒙德 vs 法兰克福",
                "  预测: 主胜 (37.1%) | 比分: 2-1 > 2-0 > 1-0 | 大小球: 小球 3.5 (77.5%)",
                "  ■ 赛果: 主胜 3-2",
                "  · 记忆ID: bundesliga|2026-05-09|多特蒙德|法兰克福 | 更新时间: 2026-05-11 11:48:42",
            ]
        )
        parsed_entry = _parse_memory_completed_entry(entry_text)
        self.assertEqual(parsed_entry["memory_id"], "bundesliga|2026-05-09|多特蒙德|法兰克福")
        self.assertEqual(parsed_entry["match_id"], "bundesliga|2026-05-09|多特蒙德|法兰克福")


class DataCollectorTest(unittest.TestCase):
    def test_degrades_to_mock_when_browser_use_missing(self):
        with patch.dict(
            os.environ,
            {"ENABLE_MOCK_SCRAPER": "1", "OKOOO_COLLECT_SCHEDULE": "0"},
            clear=False,
        ), patch("importlib.util.find_spec", return_value=None):
            with quiet_test_output():
                collector = DataCollector()

        self.assertEqual([scraper.name for scraper in collector.scrapers], ["mock"])
        with quiet_test_output():
            matches = asyncio.run(collector.collect_league_data("la_liga", "2026-05-11", use_cache=False))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].sources, ["mock"])


class UpsetCaseLibraryTest(unittest.TestCase):
    def test_duplicate_case_is_silent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library_path = Path(temp_dir) / "爆冷案例库.json"
            library = 爆冷案例库(str(library_path))
            case = 创建爆冷案例(
                比赛日期="2026-05-11",
                联赛="西甲",
                轮次="自动同步",
                主队="巴塞罗那",
                客队="皇家马德里",
                预测结果="主胜",
                实际结果="客胜",
                预测比分="2-1/1-0",
                实际比分="1-2",
                预测概率=61.0,
                主队排名=1,
                客队排名=2,
                主队积分=80,
                客队积分=78,
                伤病影响="自动同步，待人工补充",
                战术变化="测试",
                心理因素="测试",
                赔率变化="测试",
                凯利指数="测试",
                盘口异常="测试",
                爆冷原因分析="测试",
                改进建议="测试",
            )
            self.assertTrue(library.添加案例(case))
            stdout_buffer = io.StringIO()
            with contextlib.redirect_stdout(stdout_buffer):
                added = library.添加案例(case)
            self.assertFalse(added)
            self.assertEqual(stdout_buffer.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
