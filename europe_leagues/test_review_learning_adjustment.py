import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from domain.inference import InferencePipelineService
from domain.intelligence import MatchIntelligenceEngine
from domain.odds import HistoricalOddsReference, build_market_context
from domain.postprocess import PredictionPostprocessService
from domain.review_bias import ReviewBiasService
from domain.rag import HybridRAGService
from domain.review_learning import PredictionReviewLearningService
from domain.upset import UpsetAnalyzer
from domain.writeback import build_prediction_note, normalize_existing_prediction_note


class _DummyMatchIntelligenceEngine:
    @staticmethod
    def _apply_market_resonance_to_over_under(over_under, match_intelligence):
        return over_under, {"applied": False}


class _DummyPoissonModel:
    @staticmethod
    def predict_over_under(home_lambda, away_lambda, line):
        return {"line": line, "over": 0.5, "under": 0.5, "push": 0.0}


class ReviewLearningAdjustmentTest(unittest.TestCase):
    def setUp(self):
        self.service = PredictionPostprocessService({})

    @staticmethod
    def _custom_service(outcome_override):
        base_config = ReviewBiasService.DEFAULT_REVIEW_BIAS_CONFIG
        merged_outcome = ReviewBiasService._deep_merge_dict(base_config.get("outcome") or {}, outcome_override)
        custom_config = dict(base_config)
        custom_config["outcome"] = merged_outcome
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "review_bias_config.json"
            config_path.write_text(json.dumps(custom_config, ensure_ascii=False), encoding="utf-8")
            return PredictionPostprocessService({}, base_dir=temp_dir)

    def test_apply_review_outcome_adjustment_reduces_home_bias(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.56, "draw": 0.24, "away_win": 0.20},
            league_code="premier_league",
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
            league_code="premier_league",
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
            league_code="premier_league",
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
            league_code="la_liga",
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
            league_code="premier_league",
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

    def test_apply_review_outcome_adjustment_premier_league_strengthens_balanced_draw_guard(self):
        final_probabilities = {"home_win": 0.4, "draw": 0.27, "away_win": 0.33}
        current_odds = {"欧赔": {"final": {"home": 2.52, "draw": 3.02, "away": 2.88}}}
        asian_handicap = {"final": {"handicap_value": 0.0}}
        review_learning = {}

        premier_adjusted, premier_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="premier_league",
            strength_diff=4,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        la_liga_adjusted, la_liga_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="la_liga",
            strength_diff=4,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        self.assertTrue(premier_diag["applied"])
        self.assertTrue(la_liga_diag["applied"])
        self.assertGreater(premier_diag["applied_shift"]["draw_shift"], la_liga_diag["applied_shift"]["draw_shift"])
        self.assertGreater(premier_adjusted["draw"], la_liga_adjusted["draw"])

    def test_apply_review_outcome_adjustment_serie_a_strengthens_away_shallow_market_doubt(self):
        final_probabilities = {"home_win": 0.24, "draw": 0.28, "away_win": 0.48}
        current_odds = {"欧赔": {"final": {"home": 3.1, "draw": 2.95, "away": 2.88}}}
        asian_handicap = {"final": {"handicap_value": -0.25}}
        review_learning = {}

        serie_a_adjusted, serie_a_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="serie_a",
            strength_diff=-14,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        bundesliga_adjusted, bundesliga_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="bundesliga",
            strength_diff=-14,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        self.assertTrue(serie_a_diag["applied"])
        self.assertTrue(bundesliga_diag["applied"])
        self.assertGreater(serie_a_diag["applied_shift"]["home_shift"], bundesliga_diag["applied_shift"]["home_shift"])
        self.assertGreater(serie_a_adjusted["home_win"], bundesliga_adjusted["home_win"])

    def test_apply_review_outcome_adjustment_ligue1_strengthens_away_upset_bias_from_home_favorite(self):
        final_probabilities = {"home_win": 0.58, "draw": 0.23, "away_win": 0.19}
        review_learning = {
            "league_review": {
                "league_tags": ["法甲-主胜偏置", "法甲-客胜冷门敏感度不足"],
            }
        }

        ligue_adjusted, ligue_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="ligue_1",
            strength_diff=20,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.24, "away": 3.62}}},
            review_learning=review_learning,
        )
        la_liga_adjusted, la_liga_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities=final_probabilities,
            league_code="la_liga",
            strength_diff=20,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.24, "away": 3.62}}},
            review_learning=review_learning,
        )
        self.assertTrue(ligue_diag["applied"])
        self.assertTrue(la_liga_diag["applied"])
        self.assertGreater(ligue_diag["applied_shift"]["away_shift"], la_liga_diag["applied_shift"]["away_shift"])
        self.assertGreater(ligue_adjusted["away_win"], la_liga_adjusted["away_win"])

    def test_apply_review_outcome_adjustment_uses_configured_scenario_shift(self):
        service = self._custom_service(
            {
                "scenario_shifts": {
                    "balanced_draw_guard": {"draw_shift": 0.02}
                }
            }
        )
        adjusted, diag = service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4, "draw": 0.27, "away_win": 0.33},
            league_code="la_liga",
            strength_diff=4,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.52, "draw": 3.02, "away": 2.88}}},
            review_learning={},
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["applied_shift"]["draw_shift"], 0.02)
        self.assertGreater(adjusted["draw"], 0.27)

    def test_apply_review_outcome_adjustment_uses_configured_league_tag_bias(self):
        service = self._custom_service(
            {
                "league_tag_bias": {
                    "home_bias": {"draw_shift": 0.016, "upset_shift": 0.014},
                    "away_upset_bias": {"upset_shift": 0.014}
                }
            }
        )
        adjusted, diag = service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.58, "draw": 0.23, "away_win": 0.19},
            league_code="premier_league",
            strength_diff=20,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.12, "draw": 3.28, "away": 3.84}}},
            review_learning={
                "league_review": {
                    "league_tags": ["英超-主胜偏置", "英超-客胜冷门敏感度不足"]
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["applied_shift"]["draw_shift"], 0.016)
        self.assertEqual(diag["applied_shift"]["away_shift"], 0.014)
        self.assertGreater(adjusted["away_win"], 0.19)

    def test_apply_review_outcome_adjustment_marks_three_layer_evaluated_even_without_shift(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.34, "draw": 0.31, "away_win": 0.35},
            league_code="la_liga",
            strength_diff=14,
            asian_handicap={"final": {"handicap_value": -0.75}},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.22, "away": 3.35}}},
            review_learning={},
        )
        self.assertEqual(adjusted, {"home_win": 0.34, "draw": 0.31, "away_win": 0.35})
        self.assertTrue(diag["three_layer_evaluated"])
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "three_layer_evaluated_no_adjustment")

    def test_apply_review_outcome_adjustment_keeps_marginal_home_lead_from_overcorrecting(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.429085, "draw": 0.327522, "away_win": 0.243393},
            league_code="la_liga",
            strength_diff=12,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.18, "away": 3.62}}},
            review_learning={
                "league_review": {
                    "league_tags": ["西甲-主胜偏置"],
                    "prediction_coverage_rate": 0.82,
                    "unpredicted_completed_count": 0,
                }
            },
        )
        self.assertEqual(adjusted, {"home_win": 0.429085, "draw": 0.327522, "away_win": 0.243393})
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["home_bias_gate"]["qualified"], False)
        self.assertIn("limited_strength_gap", diag["home_bias_gate"]["evidence"])
        self.assertIn("shallow_market", diag["home_bias_gate"]["evidence"])
        self.assertEqual(diag["reason"], "three_layer_evaluated_no_adjustment")

    def test_apply_review_outcome_adjustment_uses_motivation_risk_to_reduce_favorite(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.54, "draw": 0.24, "away_win": 0.22},
            league_code="serie_a",
            strength_diff=8,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.32, "draw": 3.15, "away": 3.08}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 18.0,
                        "favored_side": "home",
                        "pressure_side": "away",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-motivation-risk-correction", diag["signals"])
        self.assertLess(adjusted["home_win"], 0.54)
        self.assertGreater(adjusted["draw"], 0.24)
        self.assertGreater(adjusted["away_win"], 0.22)

    def test_apply_review_over_under_adjustment_reduces_under_bias_near_key_line(self):
        adjusted, diag = self.service.apply_review_over_under_adjustment(
            over_under={"available": True, "line": 2.5, "over": 0.42, "under": 0.58},
            league_code="bundesliga",
            review_learning={"over_under_bias": {"recommended_over_shift": 0.04}},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-ou-reduce-under-bias", diag["signals"])
        self.assertGreater(adjusted["over"], 0.42)
        self.assertLess(adjusted["under"], 0.58)

    def test_apply_review_over_under_adjustment_applies_under_protection_for_towards_low(self):
        adjusted, diag = self.service.apply_review_over_under_adjustment(
            over_under={"available": True, "line": 2.75, "over": 0.56, "under": 0.44},
            league_code="serie_a",
            review_learning={"over_under_bias": {"recommended_under_shift": 0.012}},
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["reason"], "review-bias-under")
        self.assertIn("review-ou-under-protection", diag["signals"])
        self.assertLess(adjusted["over"], 0.56)
        self.assertGreater(adjusted["under"], 0.44)

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

    def test_rerank_top_scores_caps_default_home_template(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.2), ("2-1", 0.19), ("3-1", 0.18), ("2-0", 0.17), ("3-0", 0.16)],
            "主胜",
            home_lambda=1.92,
            away_lambda=1.08,
            over_under={"over": 0.61, "under": 0.39, "line": 2.75},
            strength_diff=12,
            confidence=0.5,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.22, "draw": 3.2, "away": 3.22}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "conservative_home_win_rate": 0.52,
                    "low_total_underestimate_rate": 0.58,
                    "home_goal_ceiling_underestimate_rate": 0.46,
                    "recommended_low_total_penalty": 0.022,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["applied"])
        self.assertIn("score-template-cap", diag["signals"])
        self.assertIn(reranked[0][0], {"2-1", "3-1"})
        self.assertNotEqual(reranked[0][0], "1-0")

    def test_rerank_top_scores_caps_default_draw_template_in_open_match(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.21), ("2-2", 0.19), ("0-0", 0.18), ("3-3", 0.14)],
            "平局",
            home_lambda=1.48,
            away_lambda=1.42,
            over_under={"over": 0.6, "under": 0.4, "line": 3.0},
            strength_diff=2,
            confidence=0.47,
            current_odds={"亚值": {"final": {"handicap_value": 0.0}}, "欧赔": {"final": {"home": 2.7, "draw": 3.02, "away": 2.74}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["applied"])
        self.assertIn("score-template-cap", diag["signals"])
        self.assertEqual(reranked[0][0], "2-2")

    def test_rerank_top_scores_filters_scores_that_conflict_with_prediction_direction(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.24), ("0-1", 0.22), ("1-0", 0.2), ("2-1", 0.18), ("2-0", 0.16)],
            "主胜",
            ranked_probabilities=[("主胜", 0.48), ("平局", 0.28), ("客胜", 0.24)],
            home_lambda=1.74,
            away_lambda=0.96,
            over_under={"over": 0.46, "under": 0.54, "line": 2.5},
            strength_diff=10,
            confidence=0.48,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.18, "draw": 3.18, "away": 3.36}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-direction-filter", diag["signals"])
        self.assertEqual(diag["filtered_out_count"], 2)
        self.assertEqual([score for score, _ in reranked], ["1-0", "2-1", "2-0"])

    def test_rerank_top_scores_adds_second_direction_when_match_is_double_pick(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.24), ("1-0", 0.21), ("0-0", 0.18), ("2-1", 0.15), ("0-1", 0.12)],
            "主胜",
            ranked_probabilities=[("主胜", 0.41), ("平局", 0.38), ("客胜", 0.21)],
            home_lambda=1.52,
            away_lambda=1.1,
            over_under={"over": 0.43, "under": 0.57, "line": 2.5},
            strength_diff=6,
            confidence=0.41,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.32, "draw": 3.01, "away": 3.34}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["double_pick"])
        self.assertIn("score-double-pick", diag["signals"])
        self.assertEqual(diag["allowed_outcomes"], ["主胜", "平局"])
        self.assertEqual([score for score, _ in reranked], ["1-1", "1-0", "0-0"])

    def test_rerank_top_scores_applies_review_conservative_correction_for_home_win(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.26), ("2-0", 0.22), ("2-1", 0.21), ("3-1", 0.18), ("3-0", 0.13)],
            "主胜",
            ranked_probabilities=[("主胜", 0.52), ("平局", 0.27), ("客胜", 0.21)],
            home_lambda=1.88,
            away_lambda=1.02,
            over_under={"over": 0.58, "under": 0.42, "line": 2.75},
            strength_diff=14,
            confidence=0.52,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.24, "draw": 3.18, "away": 3.28}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "conservative_home_win_rate": 0.46,
                    "low_total_underestimate_rate": 0.5,
                    "home_goal_ceiling_underestimate_rate": 0.42,
                    "recommended_home_goal_boost": 0.04,
                    "recommended_low_total_penalty": 0.025,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-review-conservative-correction", diag["signals"])
        self.assertNotEqual(reranked[0][0], "1-0")
        self.assertTrue(any(score in {"2-1", "3-1", "3-0"} for score, _ in reranked[:2]))

    def test_rerank_top_scores_applies_review_conservative_correction_for_away_win(self):
        reranked, diag = self.service.rerank_top_scores(
            [("0-1", 0.29), ("0-2", 0.23), ("1-2", 0.2), ("0-3", 0.16), ("1-3", 0.12)],
            "客胜",
            ranked_probabilities=[("客胜", 0.47), ("平局", 0.29), ("主胜", 0.24)],
            home_lambda=0.92,
            away_lambda=1.74,
            over_under={"over": 0.57, "under": 0.43, "line": 2.75},
            strength_diff=-13,
            confidence=0.47,
            current_odds={"亚值": {"final": {"handicap_value": 0.25}}, "欧赔": {"final": {"home": 3.08, "draw": 3.16, "away": 2.2}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "conservative_away_win_rate": 0.48,
                    "low_total_underestimate_rate": 0.44,
                    "away_goal_ceiling_underestimate_rate": 0.4,
                    "recommended_away_goal_boost": 0.05,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-review-conservative-correction", diag["signals"])
        self.assertNotEqual(reranked[0][0], "0-1")
        self.assertTrue(any(score in {"0-2", "1-2", "0-3"} for score, _ in reranked[:2]))

    def test_rerank_top_scores_expands_coverage_when_templates_are_too_homogeneous(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.24), ("2-0", 0.23), ("2-1", 0.22), ("3-1", 0.15), ("3-0", 0.14), ("4-1", 0.02)],
            "主胜",
            ranked_probabilities=[("主胜", 0.51), ("平局", 0.27), ("客胜", 0.22)],
            home_lambda=1.94,
            away_lambda=0.98,
            over_under={"over": 0.55, "under": 0.45, "line": 2.75},
            strength_diff=16,
            confidence=0.5,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.18, "draw": 3.24, "away": 3.42}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.5,
                    "recommended_score_coverage_expansion": 0.05,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["coverage_profile"]["needs_expansion"])
        self.assertTrue(diag["coverage_expansion_applied"])
        self.assertIn("score-review-coverage-expansion", diag["signals"])
        self.assertIn(diag["coverage_expansion_candidate"], {"3-1", "3-0"})
        self.assertTrue(any(score in {"3-1", "3-0"} for score, _ in reranked))

    def test_rerank_top_scores_keeps_direction_stable_after_review_correction(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.25), ("2-0", 0.21), ("2-1", 0.19), ("0-0", 0.18), ("0-1", 0.17), ("3-1", 0.12)],
            "主胜",
            ranked_probabilities=[("主胜", 0.49), ("平局", 0.3), ("客胜", 0.21)],
            home_lambda=1.82,
            away_lambda=0.94,
            over_under={"over": 0.56, "under": 0.44, "line": 2.75},
            strength_diff=15,
            confidence=0.49,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.2, "draw": 3.22, "away": 3.36}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "conservative_home_win_rate": 0.45,
                    "home_goal_ceiling_underestimate_rate": 0.41,
                    "coverage_expansion_rate": 0.44,
                    "recommended_home_goal_boost": 0.04,
                    "recommended_score_coverage_expansion": 0.04,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["applied"])
        self.assertTrue(all(score not in {"0-0", "0-1"} for score, _ in reranked))
        self.assertEqual(diag["allowed_outcomes"], ["主胜"])

    def test_rerank_top_scores_applies_market_low_tempo_guard_for_home_win(self):
        reranked, diag = self.service.rerank_top_scores(
            [("3-1", 0.24), ("2-1", 0.22), ("1-0", 0.2), ("2-0", 0.18)],
            "主胜",
            ranked_probabilities=[("主胜", 0.5), ("平局", 0.27), ("客胜", 0.23)],
            home_lambda=1.56,
            away_lambda=0.86,
            over_under={"over": 0.44, "under": 0.56, "line": 2.5},
            strength_diff=10,
            confidence=0.5,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.16, "draw": 3.18, "away": 3.48}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-market-low-tempo-guard", diag["signals"])
        self.assertNotEqual(reranked[0][0], "3-1")
        self.assertIn(reranked[0][0], {"2-1", "1-0"})

    def test_rerank_top_scores_applies_market_low_tempo_guard_for_draw(self):
        reranked, diag = self.service.rerank_top_scores(
            [("2-2", 0.25), ("1-1", 0.22), ("0-0", 0.2), ("3-3", 0.1)],
            "平局",
            ranked_probabilities=[("平局", 0.42), ("主胜", 0.31), ("客胜", 0.27)],
            home_lambda=1.14,
            away_lambda=1.02,
            over_under={"over": 0.43, "under": 0.57, "line": 2.5},
            strength_diff=2,
            confidence=0.42,
            current_odds={"亚值": {"final": {"handicap_value": 0.0}}, "欧赔": {"final": {"home": 2.64, "draw": 2.98, "away": 2.82}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-market-low-tempo-guard", diag["signals"])
        self.assertNotEqual(reranked[0][0], "2-2")
        self.assertIn(reranked[0][0], {"1-1", "0-0"})

    def test_rerank_top_scores_caps_big_win_template_when_market_is_shallow(self):
        reranked, diag = self.service.rerank_top_scores(
            [("3-0", 0.23), ("3-1", 0.22), ("2-1", 0.2), ("1-0", 0.19), ("2-0", 0.16)],
            "主胜",
            ranked_probabilities=[("主胜", 0.5), ("平局", 0.44), ("客胜", 0.06)],
            home_lambda=1.66,
            away_lambda=1.02,
            over_under={"over": 0.5, "under": 0.5, "line": 2.75},
            strength_diff=12,
            confidence=0.5,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.26, "draw": 3.05, "away": 3.38}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-market-shallow-cap", diag["signals"])
        self.assertNotIn(reranked[0][0], {"3-0", "3-1"})

    def test_rerank_top_scores_keeps_double_pick_behavior_after_market_guards(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.24), ("1-0", 0.21), ("0-0", 0.18), ("2-1", 0.15), ("0-1", 0.12)],
            "主胜",
            ranked_probabilities=[("主胜", 0.41), ("平局", 0.38), ("客胜", 0.21)],
            home_lambda=1.52,
            away_lambda=1.1,
            over_under={"over": 0.43, "under": 0.57, "line": 2.5},
            strength_diff=6,
            confidence=0.41,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.32, "draw": 3.01, "away": 3.34}}},
            review_learning={},
            return_diag=True,
            limit=3,
        )
        self.assertTrue(diag["double_pick"])
        self.assertEqual(diag["allowed_outcomes"], ["主胜", "平局"])
        self.assertTrue(any(score == "1-1" for score, _ in reranked))

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

    def test_apply_three_layer_total_goals_adjustment_uses_review_bias_config(self):
        adjusted, diag = self.service.apply_three_layer_total_goals_adjustment(
            {
                "available": True,
                "buckets": {"0": 0.08, "1": 0.17, "2": 0.26, "3": 0.2, "4": 0.14, "5": 0.08, "6": 0.04, "7+": 0.03},
                "top_totals": [{"total": "2", "prob": 0.26}, {"total": "3", "prob": 0.2}, {"total": "1", "prob": 0.17}],
                "tail_bucket": "7+",
            },
            league_code="bundesliga",
            predicted_outcome_label="主胜",
            strength_diff=12,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.22, "draw": 3.2, "away": 3.24}}},
            review_learning={"over_under_bias": {"recommended_over_shift": 0.04}},
            total_lambda=2.86,
            over_under={"available": True, "line": 2.5, "over": 0.43, "under": 0.57},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 0.24,
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-total-goals-reduce-low-bias", diag["signals"])
        self.assertTrue(diag["review_bias"]["applied"])
        self.assertLess(adjusted["buckets"]["1"], 0.17)
        self.assertGreater(adjusted["buckets"]["3"], 0.2)

    def test_apply_three_layer_total_goals_adjustment_applies_towards_low_mock_payload(self):
        adjusted, diag = self.service.apply_three_layer_total_goals_adjustment(
            {
                "available": True,
                "buckets": {"0": 0.05, "1": 0.12, "2": 0.19, "3": 0.26, "4": 0.18, "5": 0.11, "6": 0.06, "7+": 0.03},
                "top_totals": [{"total": "3", "prob": 0.26}, {"total": "2", "prob": 0.19}, {"total": "4", "prob": 0.18}],
                "tail_bucket": "7+",
            },
            league_code="premier_league",
            predicted_outcome_label="主胜",
            strength_diff=9,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.18, "draw": 3.45, "away": 3.26}}},
            review_learning={"over_under_bias": {"recommended_under_shift": 0.015}},
            total_lambda=1.34,
            over_under={"available": True, "line": 2.75, "over": 0.58, "under": 0.42},
            match_intelligence={},
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["review_bias"]["reason"], "towards_low")
        self.assertIn("review-total-goals-under-protection", diag["signals"])
        self.assertGreater(adjusted["buckets"]["1"], 0.12)
        self.assertGreater(adjusted["buckets"]["2"], 0.19)
        self.assertLess(adjusted["buckets"]["3"], 0.26)
        self.assertLess(adjusted["buckets"]["4"], 0.18)

    def test_extract_over_under_market_signal_detects_over_pressure(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 2.5, "over": 0.98, "under": 0.88},
                    "final": {"line": 2.75, "over": 0.84, "under": 1.02},
                }
            }
        )
        self.assertTrue(signal["available"])
        self.assertEqual(signal["goal_pressure"], "over")
        self.assertGreater(signal["pace_shift"], 0.02)
        self.assertIn("ou_line_up", signal["signals"])
        self.assertIn("over_water_drop", signal["signals"])
        self.assertEqual(signal["final_price_format"], "hong_kong")

    def test_normalize_ou_market_prices_keeps_low_decimal_odds(self):
        normalized = self.service.normalize_ou_market_prices({"over": 1.44, "under": 2.7})
        self.assertTrue(normalized["available"])
        self.assertEqual(normalized["format"], "decimal")
        self.assertEqual(normalized["over_decimal"], 1.44)
        self.assertEqual(normalized["under_decimal"], 2.7)

    def test_extract_over_under_market_signal_preserves_low_decimal_bias(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.5, "under": 2.55},
                    "final": {"line": 2.5, "over": 1.44, "under": 2.7},
                }
            }
        )
        self.assertEqual(signal["final_price_format"], "decimal")
        self.assertGreater(signal["bias_final"], 0.25)

    def test_market_ou_calibration_uses_water_movement_for_total_lambda(self):
        inference = InferencePipelineService(
            league_config={},
            team_manager=None,
            match_intelligence_engine=None,
            odds_reference=None,
            upset_analyzer=None,
            model_fusion=None,
            poisson_model=None,
            weight_adjuster=None,
            league_ou_learning=None,
            postprocess_service=self.service,
        )
        new_home, new_away, diag = inference.apply_market_ou_calibration(
            home_lambda=1.18,
            away_lambda=0.96,
            current_odds={
                "大小球": {
                    "initial": {"line": 2.5, "over": 0.98, "under": 0.88},
                    "final": {"line": 2.75, "over": 0.84, "under": 1.02},
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["market_signal"]["goal_pressure"], "over")
        self.assertGreater(diag["market_pace_shift"], 0.02)
        self.assertGreater(new_home + new_away, 2.14)
        self.assertLess(diag["target_total"], 2.95)
        self.assertEqual(diag["market_pressure_source"], "market_signal")

    def test_real_market_over_under_skips_duplicate_pace_shift_when_market_lambda_applied(self):
        inference = InferencePipelineService(
            league_config={},
            team_manager=None,
            match_intelligence_engine=_DummyMatchIntelligenceEngine(),
            odds_reference=None,
            upset_analyzer=None,
            model_fusion=None,
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=None,
            league_ou_learning=None,
            postprocess_service=self.service,
        )
        over_under, diag = inference.build_real_market_over_under(
            home_lambda=1.4,
            away_lambda=1.35,
            predicted_outcome="平局",
            strength_diff=0,
            current_odds={
                "大小球": {
                    "initial": {"line": 2.5, "over": 0.98, "under": 0.88},
                    "final": {"line": 2.75, "over": 0.84, "under": 1.02},
                }
            },
            analysis_context={},
            match_intelligence={},
            realtime_context_applied={"market_ou_lambda_calibration": {"applied": True}},
        )
        self.assertTrue(over_under["available"])
        self.assertTrue(diag["refined"]["market_lambda_applied"])
        self.assertTrue(diag["refined"]["reused_market_signal"])
        self.assertEqual(diag["refined"]["market_signal_shift"], 0.0)
        self.assertLess(over_under["over"], 0.54)

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


class ReviewLearningGenerationTest(unittest.TestCase):
    def test_build_outcome_stratified_review_applies_premier_league_draw_multiplier(self):
        service = PredictionReviewLearningService(str(Path(__file__).resolve().parent))
        samples = [
            {
                "predicted_winner": "home",
                "actual_winner": "draw" if i < 2 else "home",
                "asian_line": -0.25,
                "home_team": f"H{i}",
                "away_team": f"A{i}",
                "actual_score": "1-1" if i < 2 else "2-1",
            }
            for i in range(4)
        ]
        premier = service._build_outcome_stratified_review(samples, league_code="premier_league")
        la_liga = service._build_outcome_stratified_review(samples, league_code="la_liga")
        self.assertGreater(
            premier["home:level_shallow"]["recommended_draw_shift"],
            la_liga["home:level_shallow"]["recommended_draw_shift"],
        )
        self.assertGreater(
            premier["home:level_shallow"]["learning_multiplier"]["draw"],
            la_liga["home:level_shallow"]["learning_multiplier"]["draw"],
        )

    def test_build_three_layer_outcome_review_applies_serie_a_upset_multiplier(self):
        service = PredictionReviewLearningService(str(Path(__file__).resolve().parent))
        samples = [
            {
                "predicted_winner": "home",
                "actual_winner": "away",
                "asian_line": -0.25,
                "euro_home": 2.7,
                "euro_draw": 3.0,
                "euro_away": 2.45,
            },
            {
                "predicted_winner": "home",
                "actual_winner": "home",
                "asian_line": -0.25,
                "euro_home": 2.68,
                "euro_draw": 3.02,
                "euro_away": 2.46,
            },
        ]
        serie_a = service._build_three_layer_outcome_review(samples, league_code="serie_a")
        bundesliga = service._build_three_layer_outcome_review(samples, league_code="bundesliga")
        key = "home:level_shallow:market_opposes"
        self.assertGreater(
            serie_a[key]["recommended_upset_shift"],
            bundesliga[key]["recommended_upset_shift"],
        )
        self.assertGreater(
            serie_a[key]["learning_multiplier"]["upset"],
            bundesliga[key]["learning_multiplier"]["upset"],
        )


class ReviewLearningContextSelectionTest(unittest.TestCase):
    def test_build_prediction_context_prefers_league_specific_bias(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = PredictionReviewLearningService(temp_dir)
            payload = {
                "updated_at": "2026-05-13T00:00:00",
                "days": 30,
                "reviewed_sample_count": 8,
                "learning_context": {
                    "score_bias": {
                        "available": True,
                        "recommended_low_total_penalty": 0.012,
                    },
                    "over_under_bias": {
                        "available": True,
                        "recommended_over_shift": 0.014,
                    },
                    "recommendations": ["overall-rec"],
                    "by_league": {
                        "premier_league": {
                            "reviewed_sample_count": 4,
                            "score_bias": {
                                "available": True,
                                "recommended_low_total_penalty": 0.032,
                            },
                            "over_under_bias": {
                                "available": True,
                                "recommended_over_shift": 0.026,
                            },
                            "recommendations": ["league-rec"],
                        }
                    },
                },
                "league_overview": {"premier_league": {"completed_count": 4}},
                "outcome_stratified_review": {"overall": {}, "by_league": {"premier_league": {}}},
                "three_layer_outcome_review": {"overall": {}, "by_league": {"premier_league": {}}},
            }
            service.summary_path().parent.mkdir(parents=True, exist_ok=True)
            service.summary_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with patch.object(service, "_load_recent_league_review", return_value={}):
                context = service.build_prediction_context(league_code="premier_league", days=30, sample_limit=12)
            self.assertTrue(context["available"])
            self.assertEqual(context["score_bias_scope"], "league")
            self.assertEqual(context["over_under_bias_scope"], "league")
            self.assertEqual(context["score_bias"]["recommended_low_total_penalty"], 0.032)
            self.assertEqual(context["over_under_bias"]["recommended_over_shift"], 0.026)
            self.assertEqual(context["recommendations"][:2], ["league-rec", "overall-rec"])

    def test_build_prediction_context_exposes_league_learning_multipliers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = PredictionReviewLearningService(temp_dir)
            payload = {
                "updated_at": "2026-05-13T00:00:00",
                "days": 30,
                "reviewed_sample_count": 8,
                "learning_context": {
                    "score_bias": {"available": True},
                    "over_under_bias": {"available": True},
                    "recommendations": [],
                    "learning_multipliers": {
                        "draw_multiplier": 1.0,
                        "upset_multiplier": 1.0,
                        "stratified_max_draw_shift": 0.02,
                        "stratified_max_upset_shift": 0.016,
                        "three_layer_max_draw_shift": 0.024,
                        "three_layer_max_upset_shift": 0.02,
                    },
                    "by_league": {
                        "premier_league": {
                            "reviewed_sample_count": 4,
                            "score_bias": {"available": True},
                            "over_under_bias": {"available": True},
                            "recommendations": [],
                            "learning_multipliers": {
                                "draw_multiplier": 1.18,
                                "upset_multiplier": 1.02,
                                "stratified_max_draw_shift": 0.024,
                                "stratified_max_upset_shift": 0.016,
                                "three_layer_max_draw_shift": 0.028,
                                "three_layer_max_upset_shift": 0.02,
                            },
                        }
                    },
                },
                "league_overview": {"premier_league": {"completed_count": 4}},
                "outcome_stratified_review": {"overall": {}, "by_league": {"premier_league": {}}},
                "three_layer_outcome_review": {"overall": {}, "by_league": {"premier_league": {}}},
            }
            service.summary_path().parent.mkdir(parents=True, exist_ok=True)
            service.summary_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with patch.object(service, "_load_recent_league_review", return_value={}):
                context = service.build_prediction_context(league_code="premier_league", days=30, sample_limit=12)
            self.assertEqual(context["learning_multiplier_scope"], "league")
            self.assertEqual(context["learning_multipliers"]["draw_multiplier"], 1.18)
            self.assertEqual(context["learning_multipliers"]["three_layer_max_draw_shift"], 0.028)

    def test_build_summary_exposes_learning_multipliers(self):
        service = PredictionReviewLearningService(str(Path(__file__).resolve().parent))
        with patch.object(service, "_load_completed_samples", return_value=[]):
            payload = service.build_summary(days=30, sample_limit=12)
        self.assertIn("learning_multipliers", payload["learning_context"])
        self.assertEqual(payload["learning_context"]["learning_multipliers"]["draw_multiplier"], 1.0)


class ReviewBiasServiceTest(unittest.TestCase):
    def test_extract_motivation_risk_prefers_upset_payload(self):
        service = ReviewBiasService({})
        result = service.extract_motivation_risk(
            upset_potential={"motivation_risk": {"available": True, "score": 12.0}},
            match_intelligence={"motivation": {"risk_signal": {"available": True, "score": 0.2}}},
        )
        self.assertEqual(result["score"], 12.0)


class UpsetAnalyzerExplainabilityTest(unittest.TestCase):
    def test_assess_upset_potential_outputs_risk_breakdown_and_score_detail(self):
        analyzer = UpsetAnalyzer(league_config={"serie_a": {"name": "意甲"}})
        result = analyzer.assess_upset_potential(
            home_team="卡利亚里",
            away_team="都灵",
            league_code="serie_a",
            strength_diff=-12,
            home_strength={"injured_count": 1, "key_players_available": True},
            away_strength={"injured_count": 3, "key_players_available": False},
            predicted_outcome="客胜",
            confidence=0.74,
            historical_odds_reference={
                "available": True,
                "summary": {
                    "sample_size": 5,
                    "cold_result_rate": 0.44,
                    "result_rates": {"客胜": 0.28},
                },
            },
            asian_handicap={"final": {"handicap_value": 0.25, "away_water": 1.04}},
            european_odds={"final": {"home": 3.15, "draw": 3.0, "away": 2.28}},
            match_intelligence={
                "motivation": {
                    "home": {"objective": "保级抢分", "urgency": 0.86, "is_must_take_points": True},
                    "away": {"objective": "中游收官", "urgency": 0.42, "tier": "mid_table_flat"},
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 0.24,
                        "favored_side": "away",
                        "pressure_side": "home",
                        "flags": ["pressure_side_relegation", "favorite_mid_table_flat"],
                        "summary": "主队抢分战意强于客队",
                    },
                }
            },
        )
        self.assertIn("risk_breakdown", result)
        self.assertIn("risk_score_detail", result)
        self.assertGreater(result["risk_score_detail"]["context_score"], 0.0)
        self.assertGreater(result["risk_score_detail"]["market_score"], 0.0)
        self.assertIn("motivation", result["risk_breakdown"]["modules"])
        self.assertTrue(result["risk_breakdown"]["top_drivers"])


class RagLightweightDecisionTest(unittest.TestCase):
    def test_build_lightweight_decision_extracts_risk_and_penalty(self):
        decision = HybridRAGService.build_lightweight_decision(
            summary={
                "completed_similar_case_count": 5,
                "home_win_rate": 0.32,
                "avg_market_total_goals": 3.3,
            },
            similar_cases=[{"match_id": "s1"}],
            market_cases=[{"actual_result": "客胜", "similarity_score": 0.82}],
            upset_cases=[{"match_id": "u1"}],
            predicted_outcome="主胜",
        )
        self.assertTrue(decision["available"])
        self.assertGreaterEqual(decision["risk_bonus"], 10)
        self.assertGreater(decision["confidence_penalty"], 0.03)
        self.assertIn("upset_case_cluster", decision["scenario_tags"])
        self.assertIn("market_case_opposes_pick", decision["scenario_tags"])
        self.assertIn("similar_cases_low_hit_rate", decision["scenario_tags"])


class RagDirectionalPriorityTest(unittest.TestCase):
    def test_build_directional_summary_prefers_outcome_then_ou_then_scores(self):
        summary = HybridRAGService._build_directional_summary(
            summary={},
            similar_cases=[
                {
                    "actual_result": "主胜",
                    "actual_score": "1-0",
                    "predicted_ou_direction": "小球",
                    "ou_line": 2.5,
                    "predicted_scores": ["1-0", "2-0"],
                },
                {
                    "actual_result": "主胜",
                    "actual_score": "2-0",
                    "predicted_ou_direction": "小球",
                    "ou_line": 2.75,
                    "predicted_scores": ["2-0", "1-0"],
                },
                {
                    "actual_result": "客胜",
                    "actual_score": "1-2",
                    "predicted_ou_direction": "大球",
                    "ou_line": 2.5,
                    "predicted_scores": ["1-2"],
                },
            ],
            market_cases=[],
            predicted_outcome="主胜",
            current_ou_direction="小球",
            current_scores=["1-0", "2-0", "1-1"],
        )
        self.assertEqual(summary["direction_priority"]["matched_case_count"], 2)
        self.assertEqual(summary["ou_priority"]["current_ou_direction"], "小球")
        self.assertEqual(summary["direction_ou_priority"]["matched_case_count"], 2)
        self.assertEqual(summary["direction_ou_priority"]["preferred_scores"][:2], ["1-0", "2-0"])

    def test_memory_explanation_includes_direction_ou_and_scores(self):
        text = PredictionPostprocessService.build_retrieved_memory_explanation(
            {
                "summary": {
                    "direction_priority": {"predicted_outcome": "主胜", "matched_case_count": 3},
                    "ou_priority": {"current_ou_direction": "小球", "matched_case_count": 4},
                    "direction_ou_priority": {"matched_case_count": 2, "preferred_scores": ["1-0", "2-0"]},
                },
                "similar_cases": [{"actual_result": "主胜", "actual_score": "1-0"}],
                "market_cases": [],
                "upset_cases": [],
            }
        )
        self.assertIn("先按主胜方向匹配3场", text)
        self.assertIn("再按小球方向筛选2场", text)
        self.assertIn("对应高频比分为1-0/2-0", text)

    def test_live_market_followup_requires_1x2_close_and_consistent_totals(self):
        followup = HybridRAGService._build_live_market_followup(
            market_snapshot={
                "胜平负赔率": {
                    "final": {"home": 1.60, "draw": 4.20, "away": 5.10},
                },
                "大小球": {
                    "initial": {"line": 3.25, "over": 2.00, "under": 1.80},
                    "final": {"line": 3.5, "over": 1.80, "under": 2.00},
                },
            },
            historical_odds_reference={
                "similar_matches": [
                    {
                        "match_id": "m1",
                        "match_date": "2026-05-10",
                        "home_team": "曼城",
                        "away_team": "布伦特福德",
                        "actual_result": "主胜",
                        "actual_score": "3-1",
                        "similarity": 0.81,
                        "胜平负赔率": {"final": {"home": 1.64, "draw": 4.15, "away": 5.05}},
                        "大小球": {
                            "initial": {"line": 3.25, "over": 2.05, "under": 1.78},
                            "final": {"line": 3.5, "over": 1.83, "under": 1.98},
                        },
                    },
                    {
                        "match_id": "m2",
                        "match_date": "2026-05-09",
                        "home_team": "布莱顿",
                        "away_team": "狼队",
                        "actual_result": "平局",
                        "actual_score": "1-1",
                        "similarity": 0.79,
                        "胜平负赔率": {"final": {"home": 2.40, "draw": 3.05, "away": 2.95}},
                        "大小球": {
                            "initial": {"line": 2.5, "over": 1.88, "under": 1.96},
                            "final": {"line": 2.25, "over": 1.98, "under": 1.82},
                        },
                    },
                ]
            },
            predicted_outcome="主胜",
            current_ou_direction="大球",
        )
        self.assertTrue(followup["applied"])
        self.assertEqual(followup["eligible_count"], 1)
        self.assertEqual(followup["eligible_match_ids"], ["m1"])
        self.assertEqual(followup["recommended_action"], "follow")

    def test_live_market_followup_accepts_close_totals_change(self):
        followup = HybridRAGService._build_live_market_followup(
            market_snapshot={
                "胜平负赔率": {
                    "final": {"home": 1.66, "draw": 4.05, "away": 4.95},
                },
                "大小球": {
                    "initial": {"line": 3.25, "over": 2.04, "under": 1.78},
                    "final": {"line": 3.5, "over": 1.86, "under": 1.94},
                },
            },
            historical_odds_reference={
                "similar_matches": [
                    {
                        "match_id": "m-close",
                        "match_date": "2026-05-11",
                        "home_team": "阿森纳",
                        "away_team": "伯恩茅斯",
                        "actual_result": "主胜",
                        "actual_score": "2-1",
                        "similarity": 0.77,
                        "胜平负赔率": {"final": {"home": 1.72, "draw": 4.00, "away": 4.90}},
                        "大小球": {
                            "initial": {"line": 3.0, "over": 1.96, "under": 1.84},
                            "final": {"line": 3.25, "over": 1.84, "under": 1.98},
                        },
                    },
                ]
            },
            predicted_outcome="主胜",
            current_ou_direction="小球",
        )
        self.assertTrue(followup["applied"])
        self.assertEqual(followup["eligible_match_ids"], ["m-close"])
        self.assertGreater(followup["avg_path_score"], 0.6)

    def test_memory_explanation_includes_live_market_followup(self):
        text = PredictionPostprocessService.build_retrieved_memory_explanation(
            {
                "summary": {
                    "live_market_followup": {
                        "applied": True,
                        "eligible_count": 2,
                        "recommended_action": "follow",
                        "advice": "临场建议: 可顺当前主胜方向轻中仓跟进。",
                    }
                },
                "similar_cases": [],
                "market_cases": [{"actual_result": "主胜", "actual_score": "2-0"}],
                "upset_cases": [],
            }
        )
        self.assertIn("临场赔率轨迹门槛命中2场", text)
        self.assertIn("操作建议为follow", text)
        self.assertIn("临场建议: 可顺当前主胜方向轻中仓跟进", text)


class MarketContextCacheTest(unittest.TestCase):
    def test_build_market_context_reuses_analysis_cache(self):
        service = PredictionPostprocessService({})
        analysis_context = {}
        current_odds = {
            "欧赔": {"final": {"home": 2.15, "draw": 3.2, "away": 3.4}},
            "亚值": {"final": {"handicap": "平手/半球"}},
            "大小球": {
                "initial": {"line": 2.5, "over": 0.96, "under": 0.9},
                "final": {"line": 2.75, "over": 0.84, "under": 1.02},
            },
        }
        context1 = build_market_context(
            current_odds=current_odds,
            analysis_context=analysis_context,
            to_float=float,
            postprocess_service=service,
            cache=None,
        )
        context2 = build_market_context(
            current_odds=current_odds,
            analysis_context=analysis_context,
            to_float=float,
            postprocess_service=service,
            cache=None,
        )
        self.assertIs(context1, context2)
        self.assertEqual(context1["asian_line"], -0.25)
        self.assertEqual(context1["over_under_line_source"], "snapshot_final")
        self.assertEqual(context1["euro_odds"]["source"], "欧赔.final")


class HistoricalOddsAlignmentTest(unittest.TestCase):
    def test_market_movement_alignment_detects_same_direction_psychology_and_flow(self):
        current = {
            "欧赔": {
                "initial": {"home": 2.10, "draw": 3.30, "away": 3.60},
                "final": {"home": 1.92, "draw": 3.42, "away": 4.10},
            },
            "亚值": {
                "initial": {"handicap": -0.75, "home_water": 0.88, "away_water": 0.98},
                "final": {"handicap": -0.5, "home_water": 0.95, "away_water": 0.90},
            },
            "凯利": {"final": {"home": 0.91, "draw": 0.99, "away": 1.03}},
            "大小球": {
                "initial": {"line": 2.75, "over": 0.96, "under": 0.90},
                "final": {"line": 2.5, "over": 1.00, "under": 0.84},
            },
        }
        historical = {
            "欧赔": {
                "initial": {"home": 2.18, "draw": 3.28, "away": 3.52},
                "final": {"home": 1.98, "draw": 3.40, "away": 4.02},
            },
            "亚值": {
                "initial": {"handicap": -0.75, "home_water": 0.87, "away_water": 0.99},
                "final": {"handicap": -0.5, "home_water": 0.94, "away_water": 0.91},
            },
            "凯利": {"final": {"home": 0.92, "draw": 1.00, "away": 1.02}},
            "大小球": {
                "initial": {"line": 2.75, "over": 0.98, "under": 0.89},
                "final": {"line": 2.5, "over": 1.02, "under": 0.83},
            },
        }
        current_profile = HistoricalOddsReference._extract_market_movement_profile(current)
        historical_profile = HistoricalOddsReference._extract_market_movement_profile(historical)
        alignment = HistoricalOddsReference._compare_market_movement_profiles(current_profile, historical_profile)
        self.assertTrue(alignment["same_direction"])
        self.assertTrue(alignment["same_psychology"])
        self.assertTrue(alignment["same_capital_flow"])
        self.assertGreaterEqual(alignment["score"], 0.75)

    def test_live_outcome_adjustment_uses_historical_market_alignment(self):
        service = object.__new__(InferencePipelineService)
        adjusted, diag = service.apply_live_outcome_adjustment(
            league_code="la_liga",
            final_prob={"home_win": 0.42, "draw": 0.30, "away_win": 0.28},
            current_odds={
                "欧赔": {"final": {"home": 1.96, "draw": 3.30, "away": 4.10}},
                "亚值": {
                    "initial": {"handicap": -0.75, "home_water": 0.88, "away_water": 0.98},
                    "final": {"handicap": -0.5, "home_water": 0.95, "away_water": 0.90},
                },
                "凯利": {"final": {"draw": 0.97}},
            },
            historical_odds_reference={
                "summary": {"result_rates": {"平局": 0.22}},
                "market_alignment": {
                    "aligned_count": 3,
                    "avg_alignment_score": 0.78,
                    "dominant_direction": "home",
                    "same_psychology_count": 2,
                    "same_capital_flow_count": 3,
                    "same_totals_direction_count": 2,
                    "aligned_match_ids": ["a", "b", "c"],
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("历史盘口轨迹同向加权", diag["signals"])
        self.assertIn("历史盘口资金走向一致", diag["signals"])
        self.assertGreater(adjusted["home_win"], 0.42)
        self.assertIn("historical_market_alignment", diag)

    def test_build_prediction_note_includes_match_id(self):
        note = build_prediction_note(
            {
                "prediction": "主胜",
                "confidence": 0.57,
                "match_id": "1302909",
                "top_scores": [("2-1", 0.2), ("1-0", 0.18)],
                "over_under": {"line": 2.5, "over": 0.42, "under": 0.58},
                "upset_potential": {"level": "低", "index": 18},
            }
        )
        self.assertIn("MatchID:1302909", note)

    def test_build_prediction_note_filters_scores_against_single_direction(self):
        note = build_prediction_note(
            {
                "prediction": "主胜",
                "confidence": 0.39,
                "top_scores": [("1-1", 0.24), ("1-0", 0.22), ("0-0", 0.2)],
                "over_under": {"line": 2.75, "over": 0.4, "under": 0.6},
                "upset_potential": {"level": "中", "index": 63},
            }
        )
        self.assertIn("比分:1-0", note)
        self.assertNotIn("比分:1-1/1-0", note)
        self.assertNotIn("0-0", note)

    def test_build_prediction_note_sanitizes_case_hint_and_closes_parenthesis(self):
        note = build_prediction_note(
            {
                "prediction": "主胜",
                "confidence": 0.41,
                "top_scores": [("2-1", 0.2), ("1-0", 0.18)],
                "upset_potential": {
                    "level": "中",
                    "index": 63,
                    "case_knowledge": {
                        "available": True,
                        "hint": "诺丁汉森林vs纽卡斯尔联(中度爆冷,平局大师 | ",
                    },
                },
            }
        )
        self.assertIn("案例:诺丁汉森林vs纽卡斯尔联(中度爆冷,平局大师)", note)
        self.assertNotIn("|", note)

    def test_normalize_existing_prediction_note_repairs_historical_score_and_case_fragments(self):
        normalized = normalize_existing_prediction_note(
            "已完赛；预测:主胜 信心:0.42 比分:1-0/0-1 大小:小2.5(0.72) 爆冷:中(46) 案例:切尔西vs曼联(中度爆冷,强队胜强队 动态调权:样本不足"
        )
        self.assertIn("比分:1-0", normalized)
        self.assertNotIn("0-1", normalized)
        self.assertIn("案例:切尔西vs曼联(中度爆冷,强队胜强队)", normalized)
        self.assertIn("动态调权:样本不足", normalized)


class InferenceScoreRerankGuardTest(unittest.TestCase):
    def test_under_three_penalizes_high_total_scores(self):
        top_scores, diag = InferencePipelineService._rerank_scores_for_under_three(
            {
                "3-1": 0.21,
                "2-2": 0.2,
                "2-0": 0.195,
                "1-0": 0.19,
            },
            {"line": 3.0, "over": 0.44, "under": 0.56},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("under3-score-consistency-guard", diag["signals"])
        self.assertEqual(diag["line_bucket"], "<=3.0")
        self.assertEqual(diag["factors"]["3-1"], 0.72)
        self.assertEqual(top_scores[0][0], "2-0")
        self.assertNotEqual(top_scores[0][0], "3-1")
        self.assertIn("3-1", diag["penalties"])
        self.assertIn("2-2", diag["penalties"])

    def test_under_two_point_seven_five_uses_stronger_penalty(self):
        top_scores, diag = InferencePipelineService._rerank_scores_for_under_three(
            {
                "3-1": 0.21,
                "2-2": 0.205,
                "2-0": 0.195,
                "1-0": 0.19,
            },
            {"line": 2.75, "over": 0.46, "under": 0.54},
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["line_bucket"], "<=2.75")
        self.assertEqual(diag["factors"]["3-1"], 0.72)
        self.assertEqual(diag["factors"]["2-2"], 0.8)
        self.assertEqual(top_scores[0][0], "2-0")
        self.assertEqual(top_scores[1][0], "1-0")


class MatchIntelligenceScenarioRuleTest(unittest.TestCase):
    def test_contextual_rules_compress_la_liga_mid_table_home_bias(self):
        rules = MatchIntelligenceEngine._derive_contextual_rule_adjustments(
            league_code="la_liga",
            home_motivation={"score": 71.0, "tags": ["中游战意一般"], "table_row": {"rank": 10}},
            away_motivation={"score": 74.0, "tags": ["上半区竞争"], "table_row": {"rank": 8}},
            home_volatility={"available": True, "score": 0.28, "label": "low"},
            away_volatility={"available": True, "score": 0.31, "label": "low"},
            total_teams=20,
        )
        self.assertLess(rules["home_delta"], 0.0)
        self.assertGreater(rules["draw_delta"], 0.0)
        self.assertIn("la_liga_mid_table_home_flat", rules["scenario_tags"])

    def test_contextual_rules_boost_premier_league_relegation_home_motivation(self):
        rules = MatchIntelligenceEngine._derive_contextual_rule_adjustments(
            league_code="premier_league",
            home_motivation={"score": 86.0, "tags": ["保级压力"], "table_row": {"rank": 18}},
            away_motivation={"score": 72.0, "tags": ["中游战意一般"], "table_row": {"rank": 12}},
            home_volatility={"available": True, "score": 0.24, "label": "low"},
            away_volatility={"available": True, "score": 0.22, "label": "low"},
            total_teams=20,
        )
        self.assertGreater(rules["home_delta"], 0.0)
        self.assertIn("premier_league_relegation_home_motivation_bonus", rules["scenario_tags"])

    def test_recent_form_volatility_penalizes_single_side_and_raises_draw(self):
        rules = MatchIntelligenceEngine._derive_contextual_rule_adjustments(
            league_code="ligue_1",
            home_motivation={"score": 78.0, "tags": ["上半区竞争"], "table_row": {"rank": 7}},
            away_motivation={"score": 74.0, "tags": ["中游战意一般"], "table_row": {"rank": 11}},
            home_volatility={"available": True, "score": 0.63, "label": "high"},
            away_volatility={"available": True, "score": 0.18, "label": "low"},
            total_teams=18,
        )
        self.assertLess(rules["home_delta"], 0.0)
        self.assertGreater(rules["draw_delta"], 0.0)
        self.assertIn("recent_form_home_volatility_high", rules["scenario_tags"])


class DrawConfirmationGuardTest(unittest.TestCase):
    def test_draw_confirmation_guard_shifts_unconfirmed_draw_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.34, "draw": 0.355, "away_win": 0.305},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.48, "away": 3.42}}},
            over_under={"line": 2.75, "over": 0.56, "under": 0.44},
            match_intelligence={"scenario_tags": ["premier_league_relegation_home_motivation_bonus"]},
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertLess(adjusted["draw"], 0.355)
        self.assertGreater(adjusted["home_win"], 0.34)

    def test_draw_confirmation_guard_keeps_market_confirmed_draw(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.31, "draw": 0.36, "away_win": 0.33},
            current_odds={"欧赔": {"final": {"home": 2.72, "draw": 2.86, "away": 2.84}}},
            over_under={"line": 2.25, "over": 0.44, "under": 0.56},
            match_intelligence={"scenario_tags": ["la_liga_mid_table_home_flat"]},
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertEqual(diag["reason"], "draw_confirmation_passed")
        self.assertEqual(adjusted["draw"], 0.36)


if __name__ == "__main__":
    unittest.main()
