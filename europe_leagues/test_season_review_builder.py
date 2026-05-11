import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_recent_five_leagues_review import build_recent_five_leagues_review
from scripts.build_season_master_review import build_season_master_review


class SeasonMasterReviewBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "premier_league").mkdir(parents=True, exist_ok=True)
        (self.base_dir / "la_liga").mkdir(parents=True, exist_ok=True)

        cases = {
            "cases": [
                {
                    "case_type": "prediction_case",
                    "league_code": "premier_league",
                    "match_id": "pl_rule_1",
                    "match_date": "2026-05-10",
                    "home_team": "利物浦",
                    "away_team": "切尔西",
                    "prediction": "主胜",
                    "actual_result": "平局",
                    "actual_score": "1-1",
                    "predicted_scores": ["2-1", "1-0"],
                    "predicted_ou_direction": "小球",
                    "ou_line": 2.5,
                    "review_tags": ["主胜高估", "平局低估", "比分未命中"],
                    "league_review_tags": ["英超-主胜偏置", "英超-平局防守不足"],
                    "asian_line": -0.25,
                    "euro_home": 2.05,
                    "euro_draw": 3.3,
                    "euro_away": 3.9,
                    "completed": True,
                },
                {
                    "case_type": "prediction_case",
                    "league_code": "la_liga",
                    "match_id": "ll_rule_1",
                    "match_date": "2026-05-10",
                    "home_team": "皇家社会",
                    "away_team": "皇家贝蒂斯",
                    "prediction": "主胜",
                    "actual_result": "主胜",
                    "actual_score": "2-1",
                    "predicted_scores": ["2-1", "1-0"],
                    "predicted_ou_direction": "大球",
                    "ou_line": 2.5,
                    "review_tags": [],
                    "league_review_tags": [],
                    "asian_line": -0.25,
                    "euro_home": 2.1,
                    "euro_draw": 3.25,
                    "euro_away": 3.7,
                    "completed": True,
                },
                {
                    "case_type": "prediction_case",
                    "league_code": "la_liga",
                    "match_id": "ll_miss_strength",
                    "match_date": "2026-05-11",
                    "home_team": "赫塔菲",
                    "away_team": "奥萨苏纳",
                    "prediction": "主胜",
                    "actual_result": "主胜",
                    "actual_score": "1-0",
                    "predicted_scores": ["1-0", "2-0"],
                    "predicted_ou_direction": "小球",
                    "ou_line": 2.25,
                    "review_tags": [],
                    "league_review_tags": [],
                    "asian_line": -0.25,
                    "euro_home": 2.18,
                    "euro_draw": 3.18,
                    "euro_away": 3.52,
                    "completed": True,
                },
                {
                    "case_type": "market_case",
                    "league_code": "premier_league",
                    "match_date": "2026-05-10",
                    "home_team": "阿森纳",
                    "away_team": "曼城",
                    "actual_result": "主胜",
                    "actual_score": "2-0",
                    "completed": True,
                },
            ]
        }
        (runtime_dir / "rag_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        archive = {
            "pl_rule_1": {
                "match_id": "pl_rule_1",
                "league": "premier_league",
                "match_date": "2026-05-10",
                "home_team": "利物浦",
                "away_team": "切尔西",
                "strength_diff": 22.0,
            },
            "ll_rule_1": {
                "match_id": "ll_rule_1",
                "league": "la_liga",
                "match_date": "2026-05-10",
                "home_team": "皇家社会",
                "away_team": "皇家贝蒂斯",
                "strength_diff": 6.0,
            },
        }
        (runtime_dir / "prediction_archive.json").write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
        (self.base_dir / "premier_league" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "## 赛程信息",
                    "",
                    "### 第35轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 20:00 | 利物浦 | 1-1 | 切尔西 | 已完赛 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.base_dir / "la_liga" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "## 赛程信息",
                    "",
                    "### 第34轮",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-10 | 22:00 | 皇家社会 | 2-1 | 皇家贝蒂斯 | 已完赛 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_build_season_master_review_is_idempotent_for_same_window(self):
        recent_review = build_recent_five_leagues_review(
            str(self.base_dir),
            recent_days=7,
            as_of_date="2026-05-11",
        )
        recent_review_content = Path(recent_review["output_path"]).read_text(encoding="utf-8")
        first = build_season_master_review(
            base_dir=str(self.base_dir),
            season="2025-26",
            recent_days=7,
            recent_review_path=recent_review["output_path"],
            as_of_date="2026-05-11",
        )
        second = build_season_master_review(
            base_dir=str(self.base_dir),
            season="2025-26",
            recent_days=7,
            recent_review_path=recent_review["output_path"],
            as_of_date="2026-05-11",
        )

        master_path = Path(second["output_path"])
        content = master_path.read_text(encoding="utf-8")

        self.assertEqual(first["action"], "appended")
        self.assertEqual(second["action"], "replaced")
        self.assertEqual(content.count("### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）"), 1)
        self.assertIn("## 总体快照", recent_review_content)
        self.assertIn("## 联赛 Top 问题一览", recent_review_content)
        self.assertIn("### 英超", recent_review_content)
        self.assertIn("Top 问题：`覆盖：", recent_review_content)
        self.assertIn("方向：主胜高估 x1 / 平局低估 x1", recent_review_content)
        self.assertIn("比分：比分未命中 x1", recent_review_content)
        self.assertIn("盘口：缺实力差数据 x1", recent_review_content)
        self.assertIn("## 三层校准观察", recent_review_content)
        self.assertIn("主让浅盘但实力差大", recent_review_content)
        self.assertIn("缺实力差数据", recent_review_content)
        self.assertIn("## 统一整改优先级", recent_review_content)
        self.assertIn("优先补齐 `prediction_archive.json` 中的 `strength_diff` 留存", recent_review_content)

        self.assertIn("## 最近窗口基线", content)
        self.assertIn("#### 英超", content)
        self.assertEqual(second["batch_heading"], "### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）")
        self.assertIn("- 核心战绩：已完赛 `2`", content)
        self.assertIn("无预测 `1`", content)
        self.assertIn("- Top 问题：`覆盖：预测归档覆盖不足 x1；方向：主胜高估 x1 / 平局低估 x1；比分：比分未命中 x1；盘口：无显著风险`", content)
        self.assertIn("- 下一步动作：优先补齐 `prediction_archive.json` 中的 `strength_diff` 留存", content)
        self.assertIn("- 无预测样本：`阿森纳 2-0 曼城`", content)
        self.assertIn("- 代表样本：`利物浦 1-1 切尔西`", content)
        self.assertIn("- 当前窗口结论：`覆盖：预测归档覆盖不足 x1；方向：主胜高估 x1 / 平局低估 x1；比分：比分未命中 x1；盘口：缺实力差数据 x1`", content)

    def test_existing_generic_batch_heading_is_upgraded_to_round_heading(self):
        master_path = self.base_dir / "runtime" / "season_reviews" / "2025-26_five_leagues_master_review.md"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_text(
            "\n".join(
                [
                    "# 2025-26 五大联赛赛季统一复盘数据源",
                    "",
                    "## 最新批次",
                    "",
                    "### 第 1 批次（2026-05-04 至 2026-05-11）",
                    "",
                    "> 统计窗口：2026-05-04 至 2026-05-11",
                    "",
                    "#### 英超",
                    "",
                    "- 已完赛：`0`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = build_season_master_review(
            base_dir=str(self.base_dir),
            season="2025-26",
            recent_days=7,
            as_of_date="2026-05-11",
        )
        content = master_path.read_text(encoding="utf-8")

        self.assertEqual(result["action"], "replaced")
        self.assertEqual(result["batch_heading"], "### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）")
        self.assertNotIn("### 第 1 批次（2026-05-04 至 2026-05-11）", content)
        self.assertIn("### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）", content)

    def test_build_recent_review_tolerates_list_payload_and_datetime_as_of_date(self):
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        (runtime_dir / "rag_cases.json").write_text(
            json.dumps(
                [
                    {
                        "case_type": "prediction_case",
                        "league_code": "premier_league",
                        "match_id": "pl_list_payload",
                        "match_date": "2026-05-10T20:00:00",
                        "home_team": "热刺",
                        "away_team": "维拉",
                        "prediction": "主胜",
                        "actual_result": "主胜",
                        "actual_score": "2:1",
                        "predicted_scores": ["2-1"],
                        "predicted_ou_direction": "大球",
                        "ou_line": 2.5,
                        "completed": True,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (runtime_dir / "prediction_archive.json").write_text("{}", encoding="utf-8")

        payload = build_recent_five_leagues_review(
            str(self.base_dir),
            recent_days=7,
            as_of_date="2026-05-11T12:00:00Z",
        )

        self.assertEqual(payload["completed_match_count"], 1)
        self.assertEqual(payload["prediction_count"], 1)
        content = Path(payload["output_path"]).read_text(encoding="utf-8")
        self.assertIn("## 联赛 Top 问题一览", content)
        self.assertIn("### 英超", content)
        self.assertIn("- 核心表现：已完赛 `1`，可复盘 `1`，无预测 `0`", content)
        self.assertIn("Top 问题：`覆盖：无明显缺口；方向：无显著偏差；比分：无显著偏差；盘口：盘口不属于浅盘或均势盘 x1 / 缺亚盘数据 x1`", content)

    def test_build_season_master_review_recovers_from_invalid_existing_document(self):
        master_path = self.base_dir / "runtime" / "season_reviews" / "2025-26_five_leagues_master_review.md"
        master_path.parent.mkdir(parents=True, exist_ok=True)
        master_path.write_bytes(b"\xff\xfe\x00broken")

        result = build_season_master_review(
            base_dir=str(self.base_dir),
            season="2025-26",
            recent_days=7,
            as_of_date="2026-05-11T08:30:00",
        )

        self.assertEqual(result["batch_heading"], "### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）")
        content = master_path.read_text(encoding="utf-8")
        self.assertIn("# 2025-26 五大联赛赛季统一复盘数据源", content)
        self.assertIn("### 英超第35轮 / 西甲第34轮（2026-05-04 至 2026-05-11）", content)


if __name__ == "__main__":
    unittest.main()
