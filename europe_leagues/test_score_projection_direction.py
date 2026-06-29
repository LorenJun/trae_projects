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

    def test_weak_ou_signal_rescues_direction_mode_score(self):
        # 大小球接近五五开（|over-under| < 0.10）：硬切不应丢掉方向上的众数比分。
        # λ_home=3.03/λ_away=0.74、大3.5 仅 0.527：原始众数 3-0(total=3≤3.5) 会被大球硬切剔除。
        d = _base(
            all_probabilities={"主胜": 0.67, "平局": 0.18, "客胜": 0.15},
            expected_goals={"home": 3.03, "away": 0.74},
            over_under={"over": 0.527, "under": 0.473, "line": 3.5},
            prediction="主胜",
        )
        scores = project_scores_for_side(d)
        self.assertTrue(scores)
        # 弱信号软化应救回被硬切的众数比分 3-0，且其概率最高位列首选。
        self.assertEqual(scores[0][0], "3-0")
        # 比分方向仍须为主胜（软化只放宽大小球，不动方向）。
        for sc, _ in scores:
            h, a = map(int, sc.split("-"))
            self.assertGreater(h, a, f"{sc} 应为主胜比分")

    def test_strong_ou_signal_keeps_hard_cut(self):
        # 大小球信号强（|over-under| >= 0.10）：硬切保持决定性，不救回反向大小球比分。
        d = _base(
            all_probabilities={"主胜": 0.46, "平局": 0.27, "客胜": 0.27},
            expected_goals={"home": 1.82, "away": 1.25},
            over_under={"over": 0.70, "under": 0.30, "line": 2.25},
            prediction="主胜",
        )
        scores = project_scores_for_side(d)
        self.assertTrue(scores)
        for sc, _ in scores:
            h, a = map(int, sc.split("-"))
            self.assertGreater(h + a, 2.25, f"{sc} 强信号判大球应 > 2.25")

    def test_draw_heavy_home_keeps_draw_hedge_in_top3(self):
        d = _base(
            all_probabilities={"主胜": 0.42, "平局": 0.32, "客胜": 0.26},
            expected_goals={"home": 1.82, "away": 1.02},
            over_under={"over": 0.58, "under": 0.42, "line": 2.25},
            prediction="主胜",
        )
        scores = project_scores_for_side(d)
        names = [score for score, _ in scores]
        self.assertTrue(any(score in {"1-1", "0-0"} for score in names), names)
        self.assertNotEqual(names[0], "1-1")

    def test_open_home_high_total_keeps_ceiling_score_in_top3(self):
        d = _base(
            all_probabilities={"主胜": 0.48, "平局": 0.27, "客胜": 0.25},
            expected_goals={"home": 1.95, "away": 1.13},
            over_under={"over": 0.56, "under": 0.44, "line": 2.75},
            prediction="主胜",
        )
        scores = project_scores_for_side(d)
        names = [score for score, _ in scores]
        self.assertTrue(any(score in {"3-1", "3-0", "4-1", "4-0"} for score in names), names)

    def test_review_draw_heavy_home_corridor_allows_draw_without_flipping_top1(self):
        d = _base(
            all_probabilities={"主胜": 0.44, "平局": 0.31, "客胜": 0.25},
            expected_goals={"home": 1.72, "away": 0.98},
            over_under={"over": 0.52, "under": 0.48, "line": 2.25},
            prediction="主胜",
        )
        d["realtime"] = {
            "context_applied": {
                "review_outcome_adjustment": {
                    "applied": True,
                    "applied_shift": {"draw_shift": 0.024},
                    "stratified_review": {
                        "matched": {"draw_miss_rate": 0.5556}
                    },
                }
            }
        }
        self.assertEqual(direction_of(d), "主胜")
        self.assertIn("平局", allowed_outcomes(d))
        scores = project_scores_for_side(d)
        self.assertTrue(any(score in {"1-1", "0-0"} for score, _ in scores), scores)

    def test_strict_home_verdict_can_still_admit_draw_under_strong_review_signal(self):
        d = _base(
            all_probabilities={"主胜": 0.41, "平局": 0.31, "客胜": 0.28},
            expected_goals={"home": 1.68, "away": 1.02},
            over_under={"over": 0.5, "under": 0.5, "line": 2.25},
            prediction="主胜",
        )
        d["tri_axis_consistency"] = {
            "direction_handicap": {"verdict_dir": "block_up_home_genuine", "fav_side": "home"}
        }
        d["realtime"] = {
            "context_applied": {
                "review_outcome_adjustment": {
                    "applied": True,
                    "applied_shift": {"draw_shift": 0.024},
                    "stratified_review": {
                        "matched": {"draw_miss_rate": 0.5556}
                    },
                }
            }
        }
        self.assertIn("平局", allowed_outcomes(d))
        scores = project_scores_for_side(d)
        self.assertTrue(any(score in {"1-1", "0-0"} for score, _ in scores), scores)

    def test_strong_home_btts_blowout_keeps_four_one_in_top3(self):
        d = _base(
            all_probabilities={"主胜": 0.47, "平局": 0.28, "客胜": 0.25},
            expected_goals={"home": 1.9, "away": 1.12},
            over_under={"over": 0.55, "under": 0.45, "line": 2.75},
            prediction="主胜",
        )
        scores = project_scores_for_side(d)
        names = [score for score, _ in scores]
        self.assertTrue(any(score in {"3-1", "4-1"} for score in names), names)

    def test_low_confidence_away_pick_is_downgraded_to_draw_hedge(self):
        # 低置信客胜 + 平局只小幅落后：比分层应转为防平口径，避免弱客胜单边输出。
        d = _base(
            all_probabilities={"主胜": 0.303, "平局": 0.324, "客胜": 0.373},
            expected_goals={"home": 1.15, "away": 1.25},
            over_under={"over": 0.51, "under": 0.49, "line": 2.25, "ou_neutral": True},
            prediction="客胜",
        )
        self.assertEqual(direction_of(d), "平局")
        self.assertEqual(allowed_outcomes(d), {"平局", "客胜"})
        names = [score for score, _ in project_scores_for_side(d)]
        self.assertIn(names[0], {"0-0", "1-1"}, names)

    def test_draw_direction_forces_draw_score_consistency(self):
        # 最终方向为平局时，top_scores 不能继续全是主胜/客胜比分。
        d = _base(
            all_probabilities={"主胜": 0.336, "平局": 0.358, "客胜": 0.306},
            expected_goals={"home": 1.55, "away": 1.05},
            over_under={"over": 0.50, "under": 0.50, "line": 1.75, "ou_neutral": True},
            prediction="平局",
        )
        scores = project_scores_for_side(d)
        self.assertTrue(scores)
        h, a = map(int, scores[0][0].split("-"))
        self.assertEqual(h, a, scores)

    def test_final_draw_direction_overrides_strict_home_handicap_for_scores(self):
        # 严格主队盘口口诀可能仍认为主真赢，但最终 1X2 若已改为平局，比分候选必须以平局开头。
        d = _with_verdict(
            "block_up_home_genuine", "home",
            all_probabilities={"主胜": 0.375, "平局": 0.376, "客胜": 0.249},
            expected_goals={"home": 1.94, "away": 0.93},
            over_under={"over": 0.62, "under": 0.38, "line": 2.25, "ou_neutral": True},
            prediction="平局",
        )
        scores = project_scores_for_side(d)
        self.assertTrue(scores)
        h, a = map(int, scores[0][0].split("-"))
        self.assertEqual(h, a, scores)

    def test_strong_away_favorite_neutral_ou_keeps_blowout_tail(self):
        # 强客 + 大盘/高λ，即便大小球被转中性，也要把 0-3/0-4/1-4 这类穿透比分放入候选。
        d = _base(
            all_probabilities={"主胜": 0.10, "平局": 0.22, "客胜": 0.68},
            expected_goals={"home": 0.72, "away": 3.05},
            over_under={"over": 0.50, "under": 0.50, "line": 4.0, "ou_neutral": True},
            prediction="客胜",
        )
        names = [score for score, _ in project_scores_for_side(d)]
        self.assertTrue(any(score in {"0-3", "0-4", "1-4", "0-5"} for score in names), names)

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


class EndToEndVerdictFromRealClassifierTest(unittest.TestCase):
    """端到端：真实分类器 classify_market_operation_pattern 从模拟盘口产出口诀，
    再喂给 score_projection，确认诱导类口诀把比分方向从「主胜」真正翻走。
    """

    def _classify(self, asian_handicap, market_sentiment, european_odds):
        from domain.inference import InferencePipelineService
        svc = object.__new__(InferencePipelineService)  # 分类器只用静态辅助方法
        pat = svc.classify_market_operation_pattern(
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            ou_signal={"available": True, "signals": []},
            market_sentiment=market_sentiment,
        )
        return pat.get("direction_handicap_matrix")

    def _scores_payload(self, dh, probs, over_under, eg):
        return {
            "tri_axis_consistency": {"direction_handicap": dh},
            "all_probabilities": probs,
            "over_under": over_under,
            "expected_goals": eg,
            "prediction": "主胜",
        }

    def test_lure_up_home_fade_flips_direction_off_home_win(self):
        # 升盘(让球加深) + 上盘(主)高水 + 欧赔主队升(被看衰) → 诱上·主难赢
        dh = self._classify(
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 0.95, "away_water": 0.95},
                "final": {"handicap": -0.75, "home_water": 1.97, "away_water": 1.85},
            },
            market_sentiment={
                "market_favorite": "home", "handicap_initial": -0.5, "handicap_final": -0.75,
                "fav_odds_move": +0.05, "dog_odds_move": -0.03, "signals": [],
            },
            european_odds={"home": 2.10, "draw": 3.30, "away": 3.60},
        )
        self.assertIsNotNone(dh)
        self.assertEqual(dh.get("verdict_dir"), "lure_up_home_fade")

        d = self._scores_payload(
            dh,
            probs={"主胜": 0.42, "平局": 0.33, "客胜": 0.25},  # 模型本来看主胜
            over_under={"over": 0.58, "under": 0.42, "line": 2.5},
            eg={"home": 1.5, "away": 1.2},
        )
        self.assertNotEqual(direction_of(d), "主胜")  # 方向被翻走
        self.assertNotIn("主胜", allowed_outcomes(d))
        for sc, _ in project_scores_for_side(d):
            h, a = map(int, sc.split("-"))
            self.assertFalse(h > a, f"{sc} 不应为主胜比分（强侧不赢）")

    def test_lure_up_hot_death_flips_direction_off_home_win(self):
        # 升盘(让球加深) + 上盘(主)低水 + 欧赔主队未降 → 大热必死
        dh = self._classify(
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 0.90, "away_water": 0.90},
                "final": {"handicap": -0.75, "home_water": 1.80, "away_water": 2.05},
            },
            market_sentiment={
                "market_favorite": "home", "handicap_initial": -0.5, "handicap_final": -0.75,
                "fav_odds_move": +0.01, "dog_odds_move": -0.01, "signals": [],
            },
            european_odds={"home": 1.65, "draw": 3.60, "away": 5.00},  # 主队大热
        )
        self.assertIsNotNone(dh)
        self.assertEqual(dh.get("verdict_dir"), "lure_up_hot_death")

        d = self._scores_payload(
            dh,
            probs={"主胜": 0.55, "平局": 0.27, "客胜": 0.18},  # 模型强烈看主胜(大热)
            over_under={"over": 0.46, "under": 0.54, "line": 2.5},  # 6规则判小球
            eg={"home": 1.5, "away": 1.0},
        )
        self.assertNotEqual(direction_of(d), "主胜")  # 大热被做死，方向翻走
        self.assertNotIn("主胜", allowed_outcomes(d))
        scores = project_scores_for_side(d)
        self.assertTrue(scores)
        for sc, _ in scores:
            h, a = map(int, sc.split("-"))
            self.assertFalse(h > a, f"{sc} 不应为主胜比分（大热必死）")
            self.assertLessEqual(h + a, 2, f"{sc} 判小球总进球应≤2")


if __name__ == "__main__":
    unittest.main()
