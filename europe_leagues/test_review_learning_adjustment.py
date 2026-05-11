import unittest

from domain.postprocess import PredictionPostprocessService


class ReviewLearningAdjustmentTest(unittest.TestCase):
    def setUp(self):
        self.service = PredictionPostprocessService({})

    def test_apply_review_outcome_adjustment_reduces_home_bias(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.56, "draw": 0.24, "away_win": 0.20},
            strength_diff=10,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.05, "draw": 3.25, "away": 3.85}}},
            review_learning={
                "league_review": {
                    "league_tags": ["英超-主胜偏置", "英超-平局防守不足", "英超-客胜冷门敏感度不足"],
                    "prediction_coverage_rate": 0.52,
                    "unpredicted_completed_count": 4,
                },
                "outcome_stratified_review": {
                    "home:level_shallow": {
                        "sample_count": 5,
                        "miss_rate": 0.6,
                        "recommended_draw_shift": 0.012,
                        "recommended_upset_shift": 0.01,
                    }
                },
                "three_layer_outcome_review": {
                    "home:level_shallow:support": {
                        "sample_count": 4,
                        "recommended_draw_shift": 0.012,
                        "recommended_upset_shift": 0.008,
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertLess(adjusted["home_win"], 0.56)
        self.assertGreater(adjusted["draw"], 0.24)
        self.assertGreater(adjusted["away_win"], 0.20)
        self.assertIn("review-home-bias-correction", diag["signals"])
        self.assertIn("review-draw-gap-correction", diag["signals"])
        self.assertIn("review-away-upset-correction", diag["signals"])
        self.assertIn("review-stratified-handicap-strength-correction", diag["signals"])
        self.assertEqual(diag["stratified_review"]["handicap_depth_bucket"], "level_shallow")

    def test_apply_review_outcome_adjustment_supports_stratified_only_signal(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.52, "draw": 0.26, "away_win": 0.22},
            strength_diff=22,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.08, "draw": 3.28, "away": 3.72}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:level_shallow": {
                        "sample_count": 6,
                        "miss_rate": 0.5,
                        "recommended_draw_shift": 0.01,
                        "recommended_upset_shift": 0.012,
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-stratified-handicap-strength-correction", diag["signals"])
        self.assertIn("review-scenario-strong_home_shallow_line", diag["signals"])
        self.assertLess(adjusted["home_win"], 0.52)
        self.assertGreater(adjusted["away_win"], 0.22)

    def test_apply_review_outcome_adjustment_handles_away_shallow_market_doubt(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.24, "draw": 0.28, "away_win": 0.48},
            strength_diff=-14,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 3.1, "draw": 2.95, "away": 2.88}}},
            review_learning={
                "three_layer_outcome_review": {
                    "away:level_shallow:draw_guarded": {
                        "sample_count": 5,
                        "recommended_draw_shift": 0.014,
                        "recommended_upset_shift": 0.008,
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-scenario-away_shallow_market_doubt", diag["signals"])
        self.assertLess(adjusted["away_win"], 0.48)
        self.assertGreater(adjusted["draw"], 0.28)

    def test_apply_review_outcome_adjustment_handles_balanced_draw_guard(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4, "draw": 0.27, "away_win": 0.33},
            strength_diff=4,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.52, "draw": 3.02, "away": 2.88}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:level_ball": {
                        "sample_count": 6,
                        "miss_rate": 0.45,
                        "recommended_draw_shift": 0.014,
                        "recommended_upset_shift": 0.0,
                    }
                },
                "three_layer_outcome_review": {
                    "home:level_ball:draw_guarded": {
                        "sample_count": 4,
                        "recommended_draw_shift": 0.015,
                        "recommended_upset_shift": 0.0,
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-scenario-balanced_draw_guard", diag["signals"])
        self.assertLess(adjusted["home_win"], 0.4)
        self.assertGreater(adjusted["draw"], 0.27)

    def test_apply_review_outcome_adjustment_uses_three_layer_heuristics_without_history(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.53, "draw": 0.25, "away_win": 0.22},
            strength_diff=21,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.46, "draw": 3.08, "away": 2.84}}},
            review_learning={},
        )
        self.assertTrue(diag["three_layer_evaluated"])
        self.assertTrue(diag["applied"])
        self.assertIn("review-scenario-strong_home_shallow_line", diag["signals"])
        self.assertIn("review-three-layer-heuristic", diag["signals"])
        self.assertLess(adjusted["home_win"], 0.53)
        self.assertGreater(adjusted["draw"], 0.25)

    def test_apply_review_outcome_adjustment_marks_three_layer_evaluated_even_without_shift(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.34, "draw": 0.31, "away_win": 0.35},
            strength_diff=14,
            asian_handicap={"final": {"handicap_value": -0.75}},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.22, "away": 3.35}}},
            review_learning={},
        )
        self.assertEqual(adjusted, {"home_win": 0.34, "draw": 0.31, "away_win": 0.35})
        self.assertTrue(diag["three_layer_evaluated"])
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "three_layer_evaluated_no_adjustment")

    def test_rerank_top_scores_applies_three_layer_score_adjustment(self):
        reranked, diag = self.service.rerank_top_scores(
            [("3-0", 0.18), ("2-1", 0.17), ("1-0", 0.16), ("2-0", 0.15)],
            "主胜",
            home_lambda=1.92,
            away_lambda=0.96,
            over_under={"over": 0.49, "under": 0.51, "line": 2.75},
            strength_diff=22,
            confidence=0.51,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.48, "draw": 3.06, "away": 2.82}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["applied"])
        self.assertIn("score-three-layer-strong_home_shallow_line", diag["signals"])
        self.assertEqual(reranked[0][0], "2-1")

    def test_apply_three_layer_total_goals_adjustment_rebalances_buckets(self):
        adjusted, diag = self.service.apply_three_layer_total_goals_adjustment(
            {
                "available": True,
                "buckets": {"0": 0.06, "1": 0.12, "2": 0.19, "3": 0.21, "4": 0.17, "5": 0.11, "6": 0.08, "7": 0.06},
                "top_totals": [{"total": "3", "prob": 0.21}, {"total": "2", "prob": 0.19}, {"total": "4", "prob": 0.17}],
                "tail_bucket": "7+",
            },
            predicted_outcome_label="平局",
            strength_diff=4,
            current_odds={"亚值": {"final": {"handicap_value": 0.0}}, "欧赔": {"final": {"home": 2.58, "draw": 2.98, "away": 2.86}}},
            review_learning={},
            total_lambda=2.22,
        )
        self.assertTrue(diag["applied"])
        self.assertIn("total-goals-three-layer-draw_market_balance", diag["signals"])
        self.assertGreater(adjusted["buckets"]["1"], 0.12)
        self.assertLess(adjusted["buckets"]["4"], 0.17)

    def test_build_three_layer_runtime_context_tolerates_invalid_strength_diff(self):
        context = self.service.build_three_layer_runtime_context(
            predicted_outcome_label="主胜",
            strength_diff="bad-strength",
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.42, "draw": 3.12, "away": 2.9}}},
            review_learning={},
        )
        self.assertEqual(context["handicap_depth_bucket"], "level_shallow")
        self.assertEqual(context["strength_gap_bucket"], "unknown")
        self.assertEqual(context["scenario_name"], "")


if __name__ == "__main__":
    unittest.main()
