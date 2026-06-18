"""比分方向由亚值让球口诀驱动、OU 侧由 6 规则驱动的回归测试。"""
import unittest

from domain.score_projection import (
    direction_of,
    allowed_outcomes,
    project_scores_for_side,
)


def _base(**over):
    d = {
        "all_probabilities": {"主胜": 0.40, "平局": 0.33, "客胜": 0.27},
        "expected_goals": {"home": 1.6, "away": 1.2},
        "over_under": {"over": 0.55, "under": 0.45, "line": 2.5},
        "prediction": "主胜",
    }
    d.update(over)
    return d


def _with_verdict(verdict_dir, fav_side, **kw):
    d = _base(**kw)
    d["tri_axis_consistency"] = {
        "direction_handicap": {"verdict_dir": verdict_dir, "fav_side": fav_side}
    }
    return d


class DirectionFromAsianHandicapTest(unittest.TestCase):
    def test_genuine_home_verdict_directs_home_win(self):
        # 印证类(阻上·主真赢) + fav=home → 比分方向只取主胜
        d = _with_verdict("block_up_home_genuine", "home")
        self.assertEqual(direction_of(d), "主胜")
        self.assertEqual(allowed_outcomes(d), {"主胜"})
        for sc, _ in project_scores_for_side(d):
            h, a = map(int, sc.split("-"))
            self.assertGreater(h, a, f"{sc} 应为主胜比分")

    def test_genuine_away_favorite_directs_away_win(self):
        # 印证类 + fav=away → 客胜
        d = _with_verdict("lure_dog_no_point", "away")
        self.assertEqual(direction_of(d), "客胜")
        self.assertEqual(allowed_outcomes(d), {"客胜"})

    def test_fade_home_verdict_allows_draw_and_underdog(self):
        # 诱导类(诱上·主难赢) + fav=home → 放行 平局 + 客胜
        d = _with_verdict(
            "lure_up_home_fade", "home",
            all_probabilities={"主胜": 0.29, "平局": 0.39, "客胜": 0.32},
            prediction="平局",
        )
        self.assertEqual(direction_of(d), "平局")  # 平局概率 > 客胜概率
        self.assertEqual(allowed_outcomes(d), {"平局", "客胜"})
        self.assertNotIn("主胜", allowed_outcomes(d))
        for sc, _ in project_scores_for_side(d):
            h, a = map(int, sc.split("-"))
            self.assertFalse(h > a, f"{sc} 不应为主胜（强侧不赢）")

    def test_fade_picks_underdog_when_dog_prob_higher(self):
        # 诱导类 + 弱侧概率 > 平局 → 主方向取弱侧
        d = _with_verdict(
            "lure_up_hot_death", "home",
            all_probabilities={"主胜": 0.30, "平局": 0.28, "客胜": 0.42},
        )
        self.assertEqual(direction_of(d), "客胜")
        self.assertEqual(allowed_outcomes(d), {"平局", "客胜"})

    def test_protect_dog_fade_for_away_favorite(self):
        # 防客(客拿分) + fav=away → 强侧(客)不赢 → 放行 平局 + 主胜
        d = _with_verdict(
            "protect_dog_genuine", "away",
            all_probabilities={"主胜": 0.36, "平局": 0.34, "客胜": 0.30},
        )
        self.assertEqual(allowed_outcomes(d), {"平局", "主胜"})
        self.assertEqual(direction_of(d), "主胜")

    def test_no_verdict_falls_back_to_model_1x2(self):
        # 无口诀(verdict_dir 缺失) → 回退模型 1X2 方向
        d = _base(all_probabilities={"主胜": 0.52, "平局": 0.28, "客胜": 0.20}, prediction="主胜")
        self.assertEqual(direction_of(d), "主胜")
        self.assertIn("主胜", allowed_outcomes(d))
        self.assertNotIn("客胜", allowed_outcomes(d))

    def test_ou_six_rules_side_constrains_scores(self):
        # OU 6 规则判大球 → 比分总进球须 > 盘线（大球+主胜候选格充足，全部满足）
        d_over = _with_verdict(
            "block_up_home_genuine", "home",
            over_under={"over": 0.70, "under": 0.30, "line": 2.5},
        )
        for sc, _ in project_scores_for_side(d_over):
            h, a = map(int, sc.split("-"))
            self.assertGreater(h + a, 2.5, f"{sc} 判大球应 > 2.5")
        # 判小球 → 最高概率比分须遵守小球（候选不足 3 个时尾部允许放宽凑满 top3，属既有契约）
        d_under = _with_verdict(
            "block_up_home_genuine", "home",
            over_under={"over": 0.30, "under": 0.70, "line": 2.5},
        )
        scores = project_scores_for_side(d_under)
        self.assertTrue(scores)
        h, a = map(int, scores[0][0].split("-"))
        self.assertLess(h + a, 2.5, f"{scores[0][0]} 首选应遵守小球 < 2.5")

    def test_realtime_matrix_fallback_path(self):
        # 口诀来源回退：tri_axis 缺失时从 realtime.context_applied 取
        d = _base(prediction="平局")
        d["realtime"] = {
            "context_applied": {
                "market_operation_pattern": {
                    "direction_handicap_matrix": {
                        "verdict_dir": "block_down_dog_hard", "fav_side": "home",
                    }
                }
            }
        }
        self.assertEqual(direction_of(d), "主胜")
        self.assertEqual(allowed_outcomes(d), {"主胜"})


if __name__ == "__main__":
    unittest.main()
