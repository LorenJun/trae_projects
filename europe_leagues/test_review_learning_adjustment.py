import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enhanced_prediction_workflow import EnhancedPredictor
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
        self.assertGreater(premier_diag["applied_shift"]["away_shift"], la_liga_diag["applied_shift"]["away_shift"])
        self.assertGreater(premier_adjusted["away_win"], la_liga_adjusted["away_win"])

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

    def test_apply_review_outcome_adjustment_ligue1_adds_narrow_home_away_relief(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.387, "draw": 0.343, "away_win": 0.27},
            league_code="ligue_1",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.04, "draw": 3.36, "away": 3.58}}},
            review_learning={},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-ligue1-home-away-relief", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.021)
        self.assertGreater(adjusted["away_win"], 0.28)
        self.assertLess(adjusted["away_win"], adjusted["home_win"])

    def test_apply_review_outcome_adjustment_ligue1_trims_narrow_home_edge_into_away(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.404058104763599, "draw": 0.3310465390392664, "away_win": 0.26489535619713464},
            league_code="ligue_1",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.75}},
            current_odds={
                "欧赔": {"final": {"home": 1.74, "draw": 4.1, "away": 4.08}},
                "大小球": {
                    "initial": {"over": 1.985, "line": 3.0, "under": 1.8525},
                    "final": {"over": 1.89, "line": 3.0, "under": 1.9233},
                },
            },
            review_learning={},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-ligue1-home-away-relief", diag["signals"])
        self.assertIn("review-league-ligue1-home-edge-trim", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_trim"], 0.0)
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.056)
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

    def test_apply_review_outcome_adjustment_la_liga_retains_low_tempo_level_ball_draw_shape(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.41317541496053667, "draw": 0.3545388278072925, "away_win": 0.23228575723217095},
            league_code="la_liga",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={
                "欧赔": {"final": {"home": 2.4839, "draw": 3.2008, "away": 2.8466}},
                "大小球": {
                    "initial": {"line": 2.75, "over": 2.02, "under": 1.8},
                    "final": {"line": 2.5, "over": 2.08, "under": 1.76},
                },
            },
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 14.22,
                    "favored_side": "home",
                    "pressure_side": "away",
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-fragile-home-favorite-correction", diag["signals"])
        self.assertIn("review-league-la-liga-low-tempo-draw-retention", diag["signals"])
        self.assertGreater(adjusted["draw"], adjusted["home_win"])
        self.assertGreater(adjusted["away_win"], 0.26)

    def test_apply_review_outcome_adjustment_la_liga_retains_medium_home_draw_shape(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4012317581582923, "draw": 0.3217074983205519, "away_win": 0.27706074352115586},
            league_code="la_liga",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.5}},
            current_odds={
                "欧赔": {"final": {"home": 1.9874, "draw": 3.6782, "away": 3.3744}},
                "大小球": {
                    "initial": {"line": 2.75, "over": 1.95, "under": 1.95},
                    "final": {"line": 2.75, "over": 1.91, "under": 1.99},
                },
            },
            review_learning={},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-bias-config-floor", diag["signals"])
        self.assertIn("review-league-la-liga-medium-home-draw-retention", diag["signals"])
        self.assertGreater(adjusted["draw"], adjusted["home_win"])
        self.assertGreater(adjusted["away_win"], 0.29)

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

    def test_apply_review_outcome_adjustment_triggers_fragile_home_favorite_correction(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.46, "draw": 0.28, "away_win": 0.26},
            league_code="premier_league",
            strength_diff=12,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.42, "draw": 3.08, "away": 2.86}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 10.0,
                    "favored_side": "home",
                    "pressure_side": "away",
                },
                "handicap_strength_mismatch": {"mismatch_detected": True, "mismatch_level": "中"},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-fragile-home-favorite-correction", diag["signals"])
        self.assertLess(adjusted["home_win"], 0.46)
        self.assertGreater(adjusted["away_win"], 0.28)
        self.assertGreater(adjusted["away_win"], adjusted["draw"] - 0.02)
        self.assertIn("away_motivation_pressure", diag["home_bias_gate"]["evidence"])
        self.assertIn("handicap_strength_mismatch", diag["home_bias_gate"]["evidence"])

    def test_apply_review_outcome_adjustment_triggers_fragile_home_favorite_correction_for_away_supported_home_pressure_case(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.39, "draw": 0.33, "away_win": 0.28},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 14.22,
                    "favored_side": "away",
                    "pressure_side": "home",
                },
                "handicap_strength_mismatch": {"mismatch_detected": False},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-fragile-home-favorite-correction", diag["signals"])
        self.assertIn("away_motivation_pressure", diag["home_bias_gate"]["evidence"])
        self.assertGreater(diag["applied_shift"]["away_shift"], 0.02)
        self.assertLess(adjusted["home_win"], 0.39)
        self.assertGreater(adjusted["away_win"], 0.30)

    def test_apply_review_outcome_adjustment_keeps_strong_supported_home_favorite_stable(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.59, "draw": 0.23, "away_win": 0.18},
            league_code="premier_league",
            strength_diff=24,
            asian_handicap={"final": {"handicap_value": -1.0}},
            current_odds={"欧赔": {"final": {"home": 1.72, "draw": 3.9, "away": 5.0}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "score": 4.0,
                    "favored_side": "home",
                    "pressure_side": "away",
                },
                "handicap_strength_mismatch": {"mismatch_detected": False},
            },
        )
        self.assertNotIn("review-fragile-home-favorite-correction", diag["signals"])
        self.assertLess(diag["applied_shift"]["away_shift"], 0.03)
        self.assertGreater(adjusted["home_win"], 0.54)

    def test_apply_review_outcome_adjustment_adds_narrow_away_bump_for_known_home_overcall_bucket(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.43, "draw": 0.29, "away_win": 0.28},
            league_code="serie_a",
            strength_diff=6,
            asian_handicap={"final": {}},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:unknown": {
                        "sample_count": 7,
                        "miss_rate": 0.57,
                        "recommended_draw_shift": 0.0,
                        "recommended_upset_shift": 0.02,
                    }
                }
            },
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-narrow-away-bump", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.025)
        self.assertGreater(adjusted["away_win"], 0.30)

    def test_apply_review_outcome_adjustment_skips_narrow_away_bump_when_draw_guarded(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.43, "draw": 0.29, "away_win": 0.28},
            league_code="serie_a",
            strength_diff=6,
            asian_handicap={"final": {"handicap_value": -0.5}},
            current_odds={"欧赔": {"final": {"home": 2.48, "draw": 2.54, "away": 2.62}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertNotIn("review-narrow-away-bump", diag["signals"])
        self.assertLess(diag["applied_shift"]["away_shift"], 0.025)

    def test_apply_review_outcome_adjustment_trims_draw_into_away_for_narrow_near_tie(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.41, "draw": 0.303, "away_win": 0.287},
            league_code="serie_a",
            strength_diff=6,
            asian_handicap={"final": {}},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:unknown": {
                        "sample_count": 7,
                        "miss_rate": 0.57,
                        "recommended_draw_shift": 0.0,
                        "recommended_upset_shift": 0.02,
                    }
                }
            },
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-narrow-away-bump", diag["signals"])
        self.assertIn("review-narrow-away-near-tie-trim", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_trim"], 0.0)
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

    def test_apply_review_outcome_adjustment_skips_near_tie_trim_when_under_supports_draw(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.41, "draw": 0.303, "away_win": 0.287},
            league_code="serie_a",
            strength_diff=6,
            asian_handicap={"final": {}},
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.96, "under": 1.84},
                    "final": {"line": 2.25, "over": 2.06, "under": 1.76},
                },
            },
            review_learning={
                "outcome_stratified_review": {
                    "home:unknown": {
                        "sample_count": 7,
                        "miss_rate": 0.57,
                        "recommended_draw_shift": 0.0,
                        "recommended_upset_shift": 0.02,
                    }
                }
            },
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-narrow-away-bump", diag["signals"])
        self.assertNotIn("review-narrow-away-near-tie-trim", diag["signals"])
        self.assertEqual(diag["applied_shift"]["draw_to_away_trim"], 0.0)
        self.assertGreater(adjusted["draw"], adjusted["away_win"])

    def test_apply_review_outcome_adjustment_relaxes_premier_league_fragile_home_into_draw(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3744446917, "draw": 0.3328760689, "away_win": 0.2926792393},
            league_code="premier_league",
            strength_diff=7,
            asian_handicap={"final": {"handicap_value": -0.25}},
            current_odds={"欧赔": {"final": {"home": 2.58, "draw": 3.38, "away": 2.74}}},
            review_learning={},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-premier-home-draw-relief", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_shift"], 0.024)
        self.assertGreater(adjusted["draw"], adjusted["home_win"])
        self.assertGreater(diag["applied_shift"]["away_shift"], 0.02)

    def test_apply_review_outcome_adjustment_adds_premier_league_away_support_for_strong_upset_signal(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4066487870, "draw": 0.3425485029, "away_win": 0.2508027101},
            league_code="premier_league",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.38, "draw": 3.34, "away": 3.04}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 14.22,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-premier-home-draw-relief", diag["signals"])
        self.assertIn("review-league-premier-away-upset-support", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.026)
        self.assertGreater(adjusted["away_win"], 0.26)

    def test_apply_review_outcome_adjustment_adds_premier_league_balanced_away_floor_without_strong_upset_signal(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3901655340, "draw": 0.3128176575, "away_win": 0.2970168084},
            league_code="premier_league",
            strength_diff=0,
            asian_handicap={"final": {}},
            current_odds={"欧赔": {"final": {"home": 2.72, "draw": 3.16, "away": 2.88}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:unknown": {
                        "sample_count": 10,
                        "miss_rate": 0.8,
                        "recommended_draw_shift": 0.024,
                        "recommended_upset_shift": 0.0082,
                    }
                }
            },
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": False,
                        "score": 3.0,
                        "favored_side": "home",
                        "pressure_side": "away",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-premier-home-draw-relief", diag["signals"])
        self.assertIn("review-league-premier-balanced-away-floor", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.024)
        self.assertGreater(adjusted["away_win"], 0.30)

    def test_apply_review_outcome_adjustment_premier_league_follows_through_from_draw_to_away(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.397, "draw": 0.327, "away_win": 0.276},
            league_code="premier_league",
            strength_diff=2,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.58, "draw": 3.16, "away": 2.94}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 13.4,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-premier-home-draw-relief", diag["signals"])
        self.assertIn("review-league-premier-away-upset-support", diag["signals"])
        self.assertIn("review-league-premier-away-follow-through", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_trim"], 0.0)
        self.assertGreater(adjusted["away_win"], 0.32)

    def test_apply_review_outcome_adjustment_premier_league_keeps_draw_above_away_when_gap_is_tiny(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.392, "draw": 0.333, "away_win": 0.275},
            league_code="premier_league",
            strength_diff=0,
            asian_handicap={"final": {}},
            current_odds={"欧赔": {"final": {"home": 2.54, "draw": 3.42, "away": 2.82}}},
            review_learning={
                "outcome_stratified_review": {
                    "home:unknown": {
                        "sample_count": 10,
                        "miss_rate": 0.8,
                        "recommended_draw_shift": 0.024,
                        "recommended_upset_shift": 0.0082,
                    }
                }
            },
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.68,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-premier-home-draw-relief", diag["signals"])
        self.assertIn("review-league-premier-away-upset-support", diag["signals"])
        self.assertNotIn("review-league-premier-away-follow-through", diag["signals"])
        self.assertEqual(diag["applied_shift"]["draw_to_away_trim"], 0.0)
        self.assertGreater(adjusted["draw"], adjusted["away_win"])
        self.assertGreater(adjusted["away_win"], 0.30)

    def test_apply_review_outcome_adjustment_premier_league_does_not_only_raise_draw(self):
        premier_adjusted, premier_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.403, "draw": 0.319, "away_win": 0.278},
            league_code="premier_league",
            strength_diff=2,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.66, "draw": 3.18, "away": 2.92}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 12.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        la_liga_adjusted, la_liga_diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.403, "draw": 0.319, "away_win": 0.278},
            league_code="la_liga",
            strength_diff=2,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.66, "draw": 3.18, "away": 2.92}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 12.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(premier_diag["applied"])
        self.assertTrue(la_liga_diag["applied"])
        self.assertGreater(premier_diag["applied_shift"]["away_shift"], la_liga_diag["applied_shift"]["away_shift"])
        self.assertGreater(premier_adjusted["away_win"], la_liga_adjusted["away_win"])
        self.assertGreater(premier_adjusted["away_win"], 0.30)

    def test_apply_review_outcome_adjustment_relaxes_serie_a_draw_into_away(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3336958657, "draw": 0.3732840088, "away_win": 0.2930201256},
            league_code="serie_a",
            strength_diff=5,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_relief"], 0.0)
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

    def test_apply_review_outcome_adjustment_promotes_serie_a_draw_guarded_home_case_into_narrow_fragility_entry(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4112078931, "draw": 0.3037017170, "away_win": 0.2850903899},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.5}},
            current_odds={
                "欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}},
                "大小球": {
                    "initial": {"line": 2.25, "over": 1.9457, "under": 1.8329},
                    "final": {"line": 2.25, "over": 1.9557, "under": 1.82},
                },
            },
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 14.22,
                    "favored_side": "away",
                    "pressure_side": "home",
                },
                "handicap_strength_mismatch": {"mismatch_detected": False},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-home-draw-guard-entry", diag["signals"])
        self.assertIn("review-league-serie-a-home-draw-guard-near-tie", diag["signals"])
        self.assertNotIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.03)
        self.assertGreater(adjusted["away_win"], 0.2850903899)

    def test_apply_review_outcome_adjustment_skips_serie_a_draw_relief_when_under_supports_draw(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3336958657, "draw": 0.3732840088, "away_win": 0.2930201256},
            league_code="serie_a",
            strength_diff=5,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.96, "under": 1.84},
                    "final": {"line": 2.25, "over": 2.06, "under": 1.76},
                },
            },
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 15.6,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
        )
        self.assertFalse(diag["applied"])
        self.assertNotIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertEqual(diag["applied_shift"]["draw_to_away_relief"], 0.0)
        self.assertAlmostEqual(adjusted["draw"], 0.3732840088)

    def test_apply_review_outcome_adjustment_allows_serie_a_draw_relief_from_under_water_drop_without_upset_support(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3330668835, "draw": 0.3731054089, "away_win": 0.2938277076},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": 0.0}},
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.8944, "under": 1.88},
                    "final": {"line": 2.5, "over": 1.9144, "under": 1.8511},
                },
            },
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": False,
                        "score": 3.0,
                        "favored_side": "home",
                        "pressure_side": "away",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_relief"], 0.0)
        self.assertGreater(adjusted["away_win"], 0.28)

    def test_apply_review_outcome_adjustment_opens_serie_a_soft_draw_into_away_without_under_water_drop(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3540668835, "draw": 0.4011054089, "away_win": 0.2448277076},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.75}},
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.92, "under": 1.82},
                    "final": {"line": 2.5, "over": 1.95, "under": 1.8},
                },
            },
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": False,
                        "score": 3.0,
                        "favored_side": "home",
                        "pressure_side": "away",
                    }
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertIn("review-league-serie-a-soft-draw-away-entry", diag["signals"])
        self.assertGreater(diag["applied_shift"]["draw_to_away_relief"], 0.0)
        self.assertGreater(adjusted["away_win"], 0.28)

    def test_apply_review_outcome_adjustment_blocks_serie_a_soft_draw_relief_when_home_would_remain_top(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.359, "draw": 0.4011054089, "away_win": 0.2448277076},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.75}},
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.92, "under": 1.82},
                    "final": {"line": 2.5, "over": 1.95, "under": 1.8},
                },
            },
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": False,
                        "score": 3.0,
                        "favored_side": "home",
                        "pressure_side": "away",
                    }
                }
            },
        )
        self.assertFalse(diag["applied"])
        self.assertNotIn("review-league-serie-a-draw-to-away-relief", diag["signals"])
        self.assertIn("review-league-serie-a-soft-draw-away-blocked-home-top", diag["signals"])
        self.assertEqual(diag["applied_shift"]["draw_to_away_relief"], 0.0)
        self.assertAlmostEqual(adjusted["home_win"], 0.3572377047841074)
        self.assertAlmostEqual(adjusted["draw"], 0.39913642242876574)
        self.assertAlmostEqual(adjusted["away_win"], 0.24362587278712694)

    def test_apply_review_outcome_adjustment_opens_serie_a_home_draw_guard_near_tie_case(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.3786026988, "draw": 0.3509137509, "away_win": 0.2704835503},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.5}},
            current_odds={
                "欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}},
                "大小球": {
                    "initial": {"line": 2.25, "over": 1.9457, "under": 1.8329},
                    "final": {"line": 2.25, "over": 1.9557, "under": 1.82},
                },
            },
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 14.22,
                    "favored_side": "away",
                    "pressure_side": "home",
                },
                "handicap_strength_mismatch": {"mismatch_detected": False},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-home-draw-guard-entry", diag["signals"])
        self.assertIn("review-league-serie-a-home-draw-guard-near-tie", diag["signals"])
        self.assertGreaterEqual(diag["applied_shift"]["away_shift"], 0.03)
        self.assertGreater(adjusted["away_win"], 0.29)

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

    def test_rerank_top_scores_breaks_deep_favorite_draw_templates_towards_home_relief(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.24), ("1-0", 0.22), ("2-0", 0.2), ("0-0", 0.18), ("2-1", 0.16)],
            "平局",
            ranked_probabilities=[("平局", 0.4449), ("主胜", 0.3244), ("客胜", 0.2306)],
            home_lambda=1.76,
            away_lambda=1.18,
            over_under={"over": 0.2213, "under": 0.7787, "line": 3.0},
            strength_diff=0,
            confidence=0.4449,
            current_odds={"亚值": {"final": {"handicap_value": -1.5}}, "欧赔": {"final": {"home": 1.66, "draw": 4.988, "away": 4.79}}},
            review_learning={},
            match_intelligence={
                "motivation": {
                    "risk_signal": {
                        "available": True,
                        "supports_upset": True,
                        "score": 14.22,
                        "favored_side": "away",
                        "pressure_side": "home",
                    }
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-deep-home-draw-relief", diag["signals"])
        self.assertIn("主胜", diag["allowed_outcomes"])
        self.assertNotEqual(reranked[0][0], "1-1")
        self.assertIn(reranked[0][0], {"1-0", "2-0"})

    def test_rerank_top_scores_rebalances_fragile_home_templates(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.22), ("2-0", 0.2), ("2-1", 0.19), ("1-1", 0.17), ("1-2", 0.1)],
            "主胜",
            ranked_probabilities=[("主胜", 0.46), ("平局", 0.31), ("客胜", 0.23)],
            home_lambda=1.68,
            away_lambda=1.14,
            over_under={"over": 0.54, "under": 0.46, "line": 2.75},
            strength_diff=11,
            confidence=0.46,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.44, "draw": 3.08, "away": 2.9}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 10.0,
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-fragile-home-template-rebalance", diag["signals"])
        self.assertNotEqual(reranked[0][0], "1-0")
        self.assertTrue(any(score in {"2-1", "1-1", "1-2"} for score, _ in reranked[:2]))
        self.assertNotIn("1-0", [score for score, _ in reranked[:2]])

    def test_rerank_top_scores_keeps_draw_direction_for_non_deep_draw_with_away_motivation(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-1", 0.24), ("0-1", 0.22), ("0-0", 0.2), ("1-0", 0.18), ("1-2", 0.16)],
            "平局",
            ranked_probabilities=[("平局", 0.43), ("客胜", 0.31), ("主胜", 0.26)],
            home_lambda=1.31,
            away_lambda=1.19,
            over_under={"over": 0.47, "under": 0.53, "line": 2.5},
            strength_diff=-2,
            confidence=0.43,
            current_odds={"亚值": {"final": {"handicap_value": 0.0}}, "欧赔": {"final": {"home": 2.66, "draw": 3.02, "away": 2.72}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 12.0,
                    "favored_side": "away",
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertNotIn("score-deep-home-draw-relief", diag["signals"])
        self.assertNotIn("客胜", diag["allowed_outcomes"])
        self.assertEqual(reranked[0][0], "1-1")

    def test_rerank_top_scores_keeps_low_tempo_home_template_when_under_supported(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.24), ("2-0", 0.21), ("2-1", 0.19), ("1-1", 0.18), ("1-2", 0.08)],
            "主胜",
            ranked_probabilities=[("主胜", 0.45), ("平局", 0.33), ("客胜", 0.22)],
            home_lambda=1.42,
            away_lambda=0.94,
            over_under={"over": 0.43, "under": 0.57, "line": 2.5},
            strength_diff=9,
            confidence=0.45,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.38, "draw": 3.02, "away": 3.12}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 10.0,
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertNotIn("score-fragile-home-template-rebalance", diag["signals"])
        self.assertEqual(reranked[0][0], "1-0")

    def test_rerank_top_scores_promotes_away_scores_for_serie_a_draw_guarded_away_relief(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.24), ("0-0", 0.22), ("0-1", 0.2), ("0-2", 0.18), ("1-2", 0.16), ("1-1", 0.14)],
            "客胜",
            league_code="serie_a",
            ranked_probabilities=[("客胜", 0.3091), ("平局", 0.3037), ("主胜", 0.2872)],
            home_lambda=0.92,
            away_lambda=1.08,
            over_under={"over": 0.44, "under": 0.56, "line": 2.25},
            strength_diff=0,
            confidence=0.3091,
            current_odds={
                "亚值": {"final": {"handicap_value": -0.5}},
                "欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}},
                "大小球": {
                    "initial": {"line": 2.25, "over": 1.9457, "under": 1.8329},
                    "final": {"line": 2.25, "over": 1.9557, "under": 1.82},
                },
            },
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.42,
                    "recommended_score_coverage_expansion": 0.05,
                }
            },
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "score": 14.22,
                    "favored_side": "away",
                    "pressure_side": "home",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-serie-a-away-relief-template-rebalance", diag["signals"])
        self.assertEqual(diag["allowed_outcomes"], ["客胜", "平局"])
        self.assertEqual(reranked[0][0], "0-1")
        self.assertIn("0-1", [score for score, _ in reranked[:2]])
        self.assertNotIn("1-0", [score for score, _ in reranked[:2]])
        self.assertNotEqual(reranked[0][0], "0-0")

    def test_rerank_top_scores_preserves_low_tempo_templates_without_serie_a_away_relief(self):
        reranked, diag = self.service.rerank_top_scores(
            [("0-0", 0.24), ("0-1", 0.22), ("1-1", 0.2), ("0-2", 0.17), ("1-2", 0.13)],
            "客胜",
            league_code="serie_a",
            ranked_probabilities=[("客胜", 0.3091), ("平局", 0.3037), ("主胜", 0.2872)],
            home_lambda=0.92,
            away_lambda=1.08,
            over_under={"over": 0.44, "under": 0.56, "line": 2.25},
            strength_diff=0,
            confidence=0.3091,
            current_odds={
                "亚值": {"final": {"handicap_value": -0.5}},
                "欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}},
                "大小球": {
                    "initial": {"line": 2.25, "over": 1.9457, "under": 1.8329},
                    "final": {"line": 2.25, "over": 1.9557, "under": 1.82},
                },
            },
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "score": 6.0,
                    "favored_side": "away",
                    "pressure_side": "home",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertNotIn("score-serie-a-away-relief-template-rebalance", diag["signals"])
        self.assertNotIn("score-serie-a-soft-away-template-rebalance", diag["signals"])
        self.assertEqual(reranked[0][0], "0-0")

    def test_rerank_top_scores_rebalances_serie_a_soft_away_templates_without_changing_direction_pool(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.24), ("2-0", 0.21), ("0-1", 0.2), ("1-1", 0.19), ("0-2", 0.17), ("1-2", 0.16)],
            "客胜",
            league_code="serie_a",
            ranked_probabilities=[("客胜", 0.3533), ("主胜", 0.3515), ("平局", 0.2952)],
            home_lambda=1.34,
            away_lambda=1.31,
            over_under={"over": 0.3181, "under": 0.6819, "line": 2.5},
            strength_diff=0,
            confidence=0.3533,
            current_odds={
                "亚值": {"final": {"handicap_value": -0.75}},
                "欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}},
            },
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "score": 3.0,
                    "favored_side": "home",
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-serie-a-soft-away-template-rebalance", diag["signals"])

    def test_rerank_top_scores_adds_away_coverage_for_serie_a_soft_draw_case(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.29), ("0-0", 0.2), ("2-0", 0.18), ("1-1", 0.17), ("0-1", 0.14), ("0-2", 0.11), ("1-2", 0.09)],
            "平局",
            league_code="serie_a",
            ranked_probabilities=[("平局", 0.4006), ("主胜", 0.3622), ("客胜", 0.2371)],
            home_lambda=0.95,
            away_lambda=0.88,
            over_under={"over": 0.3213, "under": 0.6787, "line": 2.5},
            strength_diff=0,
            confidence=0.4006,
            current_odds={
                "亚值": {"final": {"handicap_value": -0.75}},
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "大小球": {
                    "initial": {"line": 2.5, "over": 1.92, "under": 1.82},
                    "final": {"line": 2.5, "over": 1.95, "under": 1.8},
                },
            },
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.42,
                    "recommended_score_coverage_expansion": 0.05,
                }
            },
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "score": 3.0,
                    "favored_side": "home",
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-serie-a-soft-draw-coverage-rebalance", diag["signals"])
        self.assertEqual(diag["allowed_outcomes"], ["平局", "客胜"])
        self.assertIn("score-serie-a-soft-draw-coverage-priority", diag["signals"])
        self.assertTrue(any(score in {"0-1", "0-2", "1-2"} for score, _ in reranked))
        self.assertNotIn("1-0", [score for score, _ in reranked[:2]])

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

    def test_rerank_top_scores_allows_serie_a_soft_away_coverage_expansion(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.2082), ("0-0", 0.1404), ("2-0", 0.1282), ("1-1", 0.1110), ("0-1", 0.1016), ("2-1", 0.0727), ("3-0", 0.0526), ("1-2", 0.0355)],
            "客胜",
            league_code="serie_a",
            ranked_probabilities=[("客胜", 0.3533), ("主胜", 0.3515), ("平局", 0.2952)],
            home_lambda=0.92,
            away_lambda=0.91,
            over_under={"over": 0.3181, "under": 0.6819, "line": 2.5},
            strength_diff=0,
            confidence=0.3533,
            current_odds={"亚值": {"final": {"handicap_value": -0.75}}, "欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}}},
            review_learning={},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "score": 3.0,
                    "favored_side": "home",
                    "pressure_side": "away",
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-serie-a-soft-away-template-rebalance", diag["signals"])
        self.assertTrue(diag["coverage_expansion_applied"])
        self.assertIn(diag["coverage_expansion_candidate"], {"0-1", "1-2", "0-2"})
        self.assertEqual(reranked[0][0], "0-1")
        self.assertTrue(any(score in {"1-2", "0-2"} for score, _ in reranked[:2]))

    def test_rerank_top_scores_prioritizes_open_home_coverage_for_premier_league(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.181646), ("2-0", 0.138971), ("1-1", 0.107004), ("0-0", 0.105014), ("2-1", 0.085276), ("0-1", 0.075759), ("3-0", 0.070882), ("3-1", 0.041694)],
            "主胜",
            league_code="premier_league",
            ranked_probabilities=[("主胜", 0.41), ("平局", 0.38), ("客胜", 0.21)],
            home_lambda=1.93,
            away_lambda=0.97,
            over_under={"over": 0.57, "under": 0.43, "line": 2.75},
            strength_diff=12,
            confidence=0.4,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.18, "draw": 3.28, "away": 3.44}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.44,
                    "recommended_score_coverage_expansion": 0.04,
                    "recommended_home_goal_boost": 0.04,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-home-open-coverage-priority", diag["signals"])
        self.assertTrue(diag["coverage_expansion_applied"])
        self.assertIn(diag["coverage_expansion_candidate"], {"3-0", "3-1"})
        self.assertEqual(reranked[0][0], "2-1")
        self.assertTrue(any(score in {"3-0", "3-1"} for score, _ in reranked[:2]))
        self.assertNotIn("1-1", [score for score, _ in reranked[:2]])

    def test_rerank_top_scores_replaces_draw_template_with_open_home_coverage_for_premier_league(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.181646), ("2-0", 0.138971), ("1-1", 0.107004), ("0-0", 0.105014), ("2-1", 0.085276), ("0-1", 0.075759), ("3-0", 0.070882), ("3-1", 0.041694)],
            "主胜",
            league_code="premier_league",
            ranked_probabilities=[("主胜", 0.38), ("平局", 0.37), ("客胜", 0.25)],
            home_lambda=1.9,
            away_lambda=1.0,
            over_under={"over": 0.56, "under": 0.44, "line": 2.75},
            strength_diff=10,
            confidence=0.38,
            current_odds={"亚值": {"final": {"handicap_value": -0.25}}, "欧赔": {"final": {"home": 2.2, "draw": 3.24, "away": 3.46}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.44,
                    "recommended_score_coverage_expansion": 0.04,
                    "recommended_home_goal_boost": 0.04,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-home-open-coverage-priority", diag["signals"])
        self.assertEqual([score for score, _ in reranked], ["2-1", "3-0"])
        self.assertNotIn("1-1", [score for score, _ in reranked])

    def test_rerank_top_scores_prefers_clean_sheet_ceiling_in_very_deep_premier_league_home_win(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.18), ("2-0", 0.13), ("1-1", 0.109), ("0-0", 0.108), ("2-1", 0.084), ("0-1", 0.079), ("3-0", 0.05), ("3-1", 0.07)],
            "主胜",
            league_code="premier_league",
            ranked_probabilities=[("主胜", 0.395), ("平局", 0.361), ("客胜", 0.244)],
            home_lambda=2.0,
            away_lambda=1.02,
            over_under={"over": 0.42, "under": 0.58, "line": 3.5},
            strength_diff=14,
            confidence=0.395,
            current_odds={"亚值": {"final": {"handicap_value": -1.5}}, "欧赔": {"final": {"home": 1.46, "draw": 4.9, "away": 6.9}}},
            review_learning={
                "score_bias": {
                    "available": True,
                    "coverage_expansion_rate": 0.44,
                    "recommended_score_coverage_expansion": 0.04,
                    "recommended_home_goal_boost": 0.04,
                }
            },
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-home-open-clean-sheet-preference", diag["signals"])
        self.assertIn("score-home-open-coverage-priority", diag["signals"])
        self.assertEqual([score for score, _ in reranked[:2]], ["2-1", "3-0"])
        self.assertNotIn("1-1", [score for score, _ in reranked[:2]])

    def test_rerank_top_scores_retains_zero_zero_for_premier_league_draw_guard(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.18), ("1-1", 0.16), ("0-0", 0.15), ("2-1", 0.13), ("0-1", 0.11)],
            "平局",
            league_code="premier_league",
            ranked_probabilities=[("平局", 0.3523), ("主胜", 0.3406), ("客胜", 0.3071)],
            home_lambda=1.52,
            away_lambda=1.43,
            over_under={"over": 0.27735, "under": 0.72265, "line": 3.0},
            strength_diff=0,
            confidence=0.3523,
            current_odds={"亚值": {"final": {"handicap_value": -0.5}}, "欧赔": {"final": {"home": 2.08, "draw": 4.04, "away": 3.22}}},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "pressure_side": "home",
                    "favored_side": "away",
                    "score": 15.68,
                }
            },
            review_learning={"score_bias": {"available": True, "coverage_expansion_rate": 0.4, "recommended_score_coverage_expansion": 0.04}},
            return_diag=True,
            limit=3,
        )
        self.assertIn("score-premier-draw-zero-zero-retention", diag["signals"])
        self.assertEqual([score for score, _ in reranked[:2]], ["1-1", "0-0"])
        self.assertNotIn("1-0", [score for score, _ in reranked[:2]])

    def test_rerank_top_scores_retains_premier_league_home_ceiling_for_low_tempo_learning_case(self):
        reranked, diag = self.service.rerank_top_scores(
            [("1-0", 0.26), ("2-1", 0.23), ("2-0", 0.2), ("3-1", 0.16), ("3-2", 0.09), ("1-1", 0.06)],
            "主胜",
            league_code="premier_league",
            ranked_probabilities=[("主胜", 0.4008), ("平局", 0.3346), ("客胜", 0.2646)],
            home_lambda=1.3149,
            away_lambda=1.1879,
            over_under={
                "over": 0.4628,
                "under": 0.5372,
                "line": 2.5,
                "league_learning": {
                    "recent_avg_goals": 2.95,
                    "over25_rate": 0.65,
                    "over35_rate": 0.35,
                    "btts_rate": 0.6,
                },
                "market": {
                    "initial": {"line": 3.25},
                    "final": {"line": 2.5},
                },
            },
            strength_diff=0,
            confidence=0.4008,
            current_odds={"亚值": {"final": {"handicap_value": None}}, "欧赔": {"final": {"home": 2.6936, "draw": 3.3649, "away": 2.5574}}},
            upset_potential={
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "pressure_side": "away",
                    "favored_side": "home",
                    "score": 3.0,
                }
            },
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
        self.assertIn("score-market-low-tempo-guard", diag["signals"])
        self.assertIn("score-review-conservative-correction", diag["signals"])
        self.assertIn("score-premier-home-ceiling-retention", diag["signals"])
        self.assertEqual([score for score, _ in reranked], ["2-1", "3-2", "1-0"])
        self.assertNotIn("2-0", [score for score, _ in reranked])

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

    def test_extract_over_under_market_signal_adds_high_line_balanced_over_nudge(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 3.25, "over": 1.95, "under": 1.95},
                    "final": {"line": 3.25, "over": 1.91, "under": 1.99},
                }
            }
        )
        self.assertTrue(signal["available"])
        self.assertEqual(signal["goal_pressure"], "balanced")
        self.assertTrue(signal["balanced_high_line_over_nudge"])
        self.assertIn("ou_high_line_balanced_over_nudge", signal["signals"])
        self.assertGreaterEqual(signal["pace_shift"], 0.008)

    def test_extract_over_under_market_signal_detects_line_down_over_backed_divergence(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 2.5, "over": 2.0, "under": 1.8175},
                    "final": {"line": 2.0, "over": 1.81, "under": 2.0525},
                }
            }
        )
        self.assertTrue(signal["available"])
        self.assertEqual(signal["goal_pressure"], "over")
        self.assertIn("ou_line_down", signal["signals"])
        self.assertIn("over_water_drop", signal["signals"])
        self.assertIn("ou_line_down_over_backed_divergence", signal["signals"])
        self.assertEqual(signal["ou_line_water_divergence"]["side"], "over")
        self.assertEqual(signal["ou_line_water_divergence"]["water_tier"], "low")
        self.assertGreater(signal["ou_line_water_divergence"]["boost"], 0.0)

    def test_line_down_static_low_water_flags_over_trap_and_pushes_pace_up(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 3.0, "over": 0.82, "under": 0.98},
                    "final": {"line": 2.5, "over": 0.82, "under": 0.98},
                }
            }
        )
        self.assertTrue(signal["available"])
        self.assertIn("ou_line_down", signal["signals"])
        self.assertIn("ou_line_down_low_water_over_trap", signal["signals"])
        self.assertIsNotNone(signal["ou_line_down_low_water_trap"])
        self.assertEqual(signal["ou_line_down_low_water_trap"]["side"], "over")
        self.assertIn(signal["ou_line_down_low_water_trap"]["water_tier"], ("low", "mid"))
        self.assertGreater(signal["pace_shift"], 0.0)

    def test_line_down_high_water_does_not_flag_over_trap(self):
        signal = self.service.extract_over_under_market_signal(
            {
                "大小球": {
                    "initial": {"line": 3.0, "over": 1.05, "under": 0.80},
                    "final": {"line": 2.5, "over": 1.08, "under": 0.78},
                }
            }
        )
        self.assertTrue(signal["available"])
        self.assertNotIn("ou_line_down_low_water_over_trap", signal["signals"])
        self.assertIsNone(signal["ou_line_down_low_water_trap"])

    def test_flat_line_over_water_drop_adds_weak_over_nudge(self):
        signal = self.service.extract_over_under_market_signal(
            {"大小球": {"initial": {"line": 2.25, "over": 1.95, "under": 1.85},
                        "final": {"line": 2.25, "over": 1.88, "under": 1.92}}}
        )
        self.assertTrue(signal["available"])
        self.assertNotIn("ou_line_down", signal["signals"])
        self.assertNotIn("ou_line_up", signal["signals"])
        self.assertIn("ou_flat_line_over_backed_weak", signal["signals"])
        self.assertEqual(signal["ou_flat_water_nudge"]["side"], "over")
        self.assertGreater(signal["ou_flat_water_nudge"]["boost"], 0.0)
        self.assertGreater(signal["pace_shift"], 0.0)

    def test_flat_line_weak_nudge_not_triggered_when_line_moves(self):
        signal = self.service.extract_over_under_market_signal(
            {"大小球": {"initial": {"line": 2.5, "over": 2.0, "under": 1.8175},
                        "final": {"line": 2.0, "over": 1.81, "under": 2.0525}}}
        )
        self.assertIsNone(signal["ou_flat_water_nudge"])
        self.assertNotIn("ou_flat_line_over_backed_weak", signal["signals"])

    def test_classify_water_tier_matches_hong_kong_bands(self):
        self.assertEqual(self.service.classify_water_tier(1.85)["tier"], "low")
        self.assertEqual(self.service.classify_water_tier(1.86)["tier"], "mid")
        self.assertEqual(self.service.classify_water_tier(1.95)["tier"], "mid")
        self.assertEqual(self.service.classify_water_tier(1.96)["tier"], "high")
        self.assertFalse(self.service.classify_water_tier(1.0)["available"])

    def test_high_water_suppresses_line_down_over_divergence_boost(self):
        low = self.service.extract_over_under_market_signal(
            {"大小球": {"initial": {"line": 2.5, "over": 2.0, "under": 1.8175},
                        "final": {"line": 2.0, "over": 1.81, "under": 2.0525}}}
        )
        high = self.service.extract_over_under_market_signal(
            {"大小球": {"initial": {"line": 2.5, "over": 2.1, "under": 1.8175},
                        "final": {"line": 2.0, "over": 1.96, "under": 2.30}}}
        )
        self.assertEqual(low["ou_line_water_divergence"]["water_tier"], "low")
        self.assertEqual(high["ou_line_water_divergence"]["water_tier"], "high")
        self.assertGreater(
            low["ou_line_water_divergence"]["boost"],
            high["ou_line_water_divergence"]["boost"],
        )

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

    def test_market_ou_calibration_uses_high_line_balanced_over_nudge(self):
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
            home_lambda=1.35,
            away_lambda=1.25,
            current_odds={
                "大小球": {
                    "initial": {"line": 3.25, "over": 1.95, "under": 1.95},
                    "final": {"line": 3.25, "over": 1.91, "under": 1.99},
                }
            },
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["market_signal"]["goal_pressure"], "balanced")
        self.assertTrue(diag["market_signal"]["balanced_high_line_over_nudge"])
        self.assertGreaterEqual(diag["market_pace_shift"], 0.008)
        self.assertGreater(new_home + new_away, 2.6)

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


class InferenceConfidenceCalibrationTest(unittest.TestCase):
    def test_calibrate_confidence_with_league_learning_applies_positive_adjustment(self):
        adjusted, diag = InferencePipelineService._calibrate_confidence_with_league_learning(
            confidence=0.44,
            applied_weights={
                "league_weight_factor": 1.031,
                "league_total_predictions": 18,
                "confidence_adjustment": 0.012,
                "weight_reason": "联赛近30天命中率高于全局基线，放大联赛学习调权",
            },
        )
        self.assertAlmostEqual(adjusted, 0.452, places=6)
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["reason"], "league_confidence_boost")
        self.assertEqual(diag["base_confidence"], 0.44)
        self.assertEqual(diag["adjusted_confidence"], 0.452)

    def test_calibrate_confidence_with_league_learning_applies_negative_adjustment(self):
        adjusted, diag = InferencePipelineService._calibrate_confidence_with_league_learning(
            confidence=0.44,
            applied_weights={
                "league_weight_factor": 0.986,
                "league_total_predictions": 9,
                "confidence_adjustment": -0.018,
                "weight_reason": "联赛近30天命中率低于全局基线，收缩信心",
            },
        )
        self.assertAlmostEqual(adjusted, 0.422, places=6)
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["reason"], "league_confidence_trim")
        self.assertEqual(diag["adjusted_confidence"], 0.422)

    def test_refresh_cached_confidence_uses_final_probabilities_as_raw_baseline(self):
        cached = {
            "confidence": 0.452,
            "final_probabilities": {"home_win": 0.44, "draw": 0.31, "away_win": 0.25},
            "applied_model_weights": {
                "league_weight_factor": 1.031,
                "league_total_predictions": 18,
                "confidence_adjustment": 0.012,
                "weight_reason": "联赛近30天命中率高于全局基线，放大联赛学习调权",
            },
            "realtime": {"context_applied": {}},
        }
        refreshed = EnhancedPredictor._refresh_cached_confidence(cached)
        self.assertAlmostEqual(refreshed["confidence"], 0.452, places=6)
        self.assertEqual(
            refreshed["realtime"]["context_applied"]["league_confidence_adjustment"]["base_confidence"],
            0.44,
        )
        self.assertTrue(refreshed["realtime"]["context_applied"]["league_confidence_adjustment"]["applied"])

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

    def test_market_operation_pattern_flags_handicap_flat_water_drop_as_deceptive(self):
        service = object.__new__(InferencePipelineService)
        sentiment = service.detect_market_movement_sentiment(
            european_odds={
                "initial": {"home": 1.61, "draw": 3.60, "away": 5.50},
                "final": {"home": 1.55, "draw": 3.70, "away": 6.50},
            },
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 1.00, "away_water": 0.85},
                "final": {"handicap": -0.5, "home_water": 0.72, "away_water": 1.10},
            },
        )
        pattern = service.classify_market_operation_pattern(
            european_odds={
                "initial": {"home": 1.61, "draw": 3.60, "away": 5.50},
                "final": {"home": 1.55, "draw": 3.70, "away": 6.50},
            },
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 1.00, "away_water": 0.85},
                "final": {"handicap": -0.5, "home_water": 0.72, "away_water": 1.10},
            },
            ou_signal={"available": False},
            kelly={"final": {"draw": 0.90}},
            market_sentiment=sentiment,
        )
        self.assertEqual(pattern["verdict"], "deceptive")
        self.assertEqual(pattern["luring_side"], "home")
        self.assertTrue(any("诱" in lbl or "虚" in lbl or "背书" in lbl for lbl in pattern["warning_labels"]))
        self.assertGreater(pattern["recommended_extra_retreat"], 0.0)

        adjusted, diag = service.apply_market_operation_adjustment(
            final_prob={"home_win": 0.55, "draw": 0.27, "away_win": 0.18},
            pattern_diag=pattern,
            delta_vector={"fav": "home", "euro_fav_imp_move": 0.02},
            weights={},
        )
        # 两层动态调权：系数全0(样本不足)→不改概率，仅透传标签
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "no_reliable_signal_label_only")
        self.assertEqual(adjusted["home_win"], 0.55)
        self.assertEqual(adjusted["draw"], 0.27)

    def test_market_operation_pattern_marks_genuine_when_axes_corroborate(self):
        service = object.__new__(InferencePipelineService)
        sentiment = service.detect_market_movement_sentiment(
            european_odds={
                "initial": {"home": 1.80, "draw": 3.50, "away": 4.50},
                "final": {"home": 1.70, "draw": 3.55, "away": 4.55},
            },
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 0.95, "away_water": 0.90},
                "final": {"handicap": -0.75, "home_water": 0.85, "away_water": 1.00},
            },
        )
        pattern = service.classify_market_operation_pattern(
            european_odds={
                "initial": {"home": 1.80, "draw": 3.50, "away": 4.50},
                "final": {"home": 1.70, "draw": 3.55, "away": 4.55},
            },
            asian_handicap={
                "initial": {"handicap": -0.5, "home_water": 0.95, "away_water": 0.90},
                "final": {"handicap": -0.75, "home_water": 0.85, "away_water": 1.00},
            },
            ou_signal={"available": True, "signals": [], "ou_line_water_divergence": None, "ou_flat_water_nudge": None},
            kelly={"final": {"draw": 1.00}},
            market_sentiment=sentiment,
        )
        self.assertEqual(pattern["verdict"], "genuine")
        self.assertTrue(any(lbl.startswith("✅") for lbl in pattern["warning_labels"]))

        adjusted, diag = service.apply_market_operation_adjustment(
            final_prob={"home_win": 0.50, "draw": 0.28, "away_win": 0.22},
            pattern_diag=pattern,
            delta_vector={"fav": "home", "euro_fav_imp_move": 0.03},
            weights={},
        )
        # 两层动态调权：系数全0(样本不足)→不改概率，仅透传标签
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "no_reliable_signal_label_only")
        self.assertEqual(adjusted["home_win"], 0.50)
        self.assertEqual(adjusted["draw"], 0.28)

    def _dir_matrix(self, euro, asian):
        service = object.__new__(InferencePipelineService)
        sentiment = service.detect_market_movement_sentiment(
            european_odds=euro, asian_handicap=asian,
        )
        pattern = service.classify_market_operation_pattern(
            european_odds=euro, asian_handicap=asian,
            ou_signal={"available": True, "signals": [], "ou_line_water_divergence": None, "ou_flat_water_nudge": None},
            kelly={"final": {"draw": 1.00}}, market_sentiment=sentiment,
        )
        return pattern, pattern.get("direction_handicap_matrix")

    def test_dir_matrix_line_up_high_water_euro_fav_down_blocks_up_genuine(self):
        # 升盘+上盘高水(港水0.98)+欧赔主降 → 阻上(主真赢)
        pattern, dm = self._dir_matrix(
            {"initial": {"home": 1.80, "draw": 3.5, "away": 4.5}, "final": {"home": 1.70, "draw": 3.55, "away": 4.6}},
            {"initial": {"handicap": -0.5, "home_water": 1.90, "away_water": 1.95},
             "final": {"handicap": -0.75, "home_water": 1.98, "away_water": 1.88}},
        )
        self.assertIsNotNone(dm)
        self.assertEqual(dm["verdict_dir"], "block_up_home_genuine")
        self.assertEqual(dm["water_side"], "favorite")
        self.assertEqual(dm["water_tier"], "high")
        self.assertAlmostEqual(dm["water_final"], 0.98, places=4)

    def test_dir_matrix_line_up_high_water_euro_fav_up_lures_up(self):
        # 升盘+上盘高水+欧赔主升 → 诱上(主难赢)
        pattern, dm = self._dir_matrix(
            {"initial": {"home": 1.70, "draw": 3.4, "away": 4.5}, "final": {"home": 1.80, "draw": 3.4, "away": 4.3}},
            {"initial": {"handicap": -0.5, "home_water": 1.90, "away_water": 1.95},
             "final": {"handicap": -0.75, "home_water": 1.99, "away_water": 1.87}},
        )
        self.assertIsNotNone(dm)
        self.assertEqual(dm["verdict_dir"], "lure_up_home_fade")
        self.assertEqual(dm["water_tier"], "high")

    def test_dir_matrix_line_up_low_water_lures_hot_death(self):
        # 升盘+上盘低水(港水0.83)+欧赔主升 → 诱上(大热必死)
        pattern, dm = self._dir_matrix(
            {"initial": {"home": 1.70, "draw": 3.4, "away": 4.5}, "final": {"home": 1.78, "draw": 3.4, "away": 4.3}},
            {"initial": {"handicap": -0.5, "home_water": 1.90, "away_water": 1.95},
             "final": {"handicap": -0.75, "home_water": 1.83, "away_water": 2.02}},
        )
        self.assertIsNotNone(dm)
        self.assertEqual(dm["verdict_dir"], "lure_up_hot_death")
        self.assertEqual(dm["water_tier"], "low")

    def test_dir_matrix_line_down_low_water_euro_dog_down_protects_dog(self):
        # 降盘+下盘低水(港水0.82)+客赔降 → 防客(客拿分)
        pattern, dm = self._dir_matrix(
            {"initial": {"home": 1.70, "draw": 3.4, "away": 4.8}, "final": {"home": 1.74, "draw": 3.4, "away": 4.4}},
            {"initial": {"handicap": -0.75, "home_water": 1.95, "away_water": 1.95},
             "final": {"handicap": -0.5, "home_water": 2.0, "away_water": 1.82}},
        )
        self.assertIsNotNone(dm)
        self.assertEqual(dm["verdict_dir"], "protect_dog_genuine")
        self.assertEqual(dm["water_side"], "underdog")
        self.assertEqual(dm["water_tier"], "low")
        self.assertAlmostEqual(dm["water_final"], 0.82, places=4)

    def test_dir_matrix_line_down_high_water_blocks_down(self):
        # 降盘+下盘高水(港水0.99) → 阻下(客难打出)
        pattern, dm = self._dir_matrix(
            {"initial": {"home": 1.70, "draw": 3.4, "away": 4.5}, "final": {"home": 1.70, "draw": 3.4, "away": 4.5}},
            {"initial": {"handicap": -0.75, "home_water": 1.92, "away_water": 1.92},
             "final": {"handicap": -0.5, "home_water": 1.88, "away_water": 1.99}},
        )
        self.assertIsNotNone(dm)
        self.assertEqual(dm["verdict_dir"], "block_down_dog_hard")
        self.assertEqual(dm["water_tier"], "high")

    def test_dir_matrix_label_surfaces_into_tri_axis_verdict_summary(self):
        # 让球口诀必须透传到三轴研判文本(临场提示)，而非内部丢弃
        euro = {"initial": {"home": 1.80, "draw": 3.5, "away": 4.5},
                "final": {"home": 1.70, "draw": 3.55, "away": 4.6}}
        asian = {"initial": {"handicap": -0.5, "home_water": 1.90, "away_water": 1.95},
                 "final": {"handicap": -0.75, "home_water": 1.98, "away_water": 1.88}}
        pattern, dm = self._dir_matrix(euro, asian)
        self.assertEqual(dm["verdict_dir"], "block_up_home_genuine")
        tri = InferencePipelineService.compute_tri_axis_consistency(
            final_prob={"home_win": 0.55, "draw": 0.27, "away_win": 0.18},
            over_under={"available": True, "over": 0.52, "under": 0.48, "line": 2.5},
            operation_pattern=pattern,
            european_odds=euro,
            asian_handicap=asian,
        )
        self.assertEqual(tri["direction_handicap"]["verdict_dir"], "block_up_home_genuine")
        self.assertIn("让球口诀", tri["verdict_summary"])
        self.assertIn("阻上(主真赢)", tri["verdict_summary"])

    def test_market_operation_pattern_light_euro_pump_not_flagged_deceptive(self):
        """真实回归：墨西哥2-0南非——热门仅轻度走热(-4.3%)+盘不动，正常强队被看好，不应误判诱导。"""
        service = object.__new__(InferencePipelineService)
        european_odds = {
            "initial": {"home": 1.48, "draw": 4.0379, "away": 6.5953},
            "final": {"home": 1.4159, "draw": 4.291, "away": 8.3429},
        }
        asian_handicap = {
            "initial": {"handicap": -1, "home_water": 1.91, "away_water": 1.91},
            "final": {"handicap": -1, "home_water": 1.80, "away_water": 2.13},
        }
        sentiment = service.detect_market_movement_sentiment(
            european_odds=european_odds,
            asian_handicap=asian_handicap,
        )
        pattern = service.classify_market_operation_pattern(
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            ou_signal={"available": True, "signals": [], "ou_line_water_divergence": None, "ou_flat_water_nudge": None},
            kelly={"final": {"draw": 0.9451}},
            market_sentiment=sentiment,
        )
        self.assertNotEqual(pattern["verdict"], "deceptive")
        self.assertIsNone(pattern["luring_side"])
        self.assertTrue(any("正常被看好" in lbl for lbl in pattern["warning_labels"]))

    def test_market_operation_pattern_deep_euro_pump_flags_deceptive(self):
        """真实回归：加拿大1-1波黑——欧赔深压(-9.8%)+升盘，热门没赢，应判诱导。"""
        service = object.__new__(InferencePipelineService)
        european_odds = {
            "initial": {"home": 2.0294, "draw": 3.4003, "away": 3.6626},
            "final": {"home": 1.831, "draw": 3.438, "away": 4.5237},
        }
        asian_handicap = {
            "initial": {"handicap": -0.25, "home_water": 1.93, "away_water": 1.89},
            "final": {"handicap": -0.5, "home_water": 1.91, "away_water": 2.03},
        }
        sentiment = service.detect_market_movement_sentiment(
            european_odds=european_odds,
            asian_handicap=asian_handicap,
        )
        pattern = service.classify_market_operation_pattern(
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            ou_signal={"available": True, "signals": [], "ou_line_water_divergence": None, "ou_flat_water_nudge": None},
            kelly={"final": {"draw": 0.9439}},
            market_sentiment=sentiment,
        )
        self.assertEqual(pattern["verdict"], "deceptive")
        self.assertEqual(pattern["luring_side"], "home")
        self.assertTrue(any("深压" in lbl for lbl in pattern["warning_labels"]))
        self.assertFalse(any("升盘背书" in lbl for lbl in pattern["warning_labels"]))

    def test_market_operation_pattern_neutral_labels_when_signals_insufficient(self):
        service = object.__new__(InferencePipelineService)
        pattern = service.classify_market_operation_pattern(
            european_odds={
                "initial": {"home": 2.00, "draw": 3.30, "away": 3.60},
                "final": {"home": 2.00, "draw": 3.30, "away": 3.60},
            },
            asian_handicap={
                "initial": {"handicap": -0.25, "home_water": 0.92, "away_water": 0.92},
                "final": {"handicap": -0.25, "home_water": 0.92, "away_water": 0.92},
            },
            ou_signal={"available": True, "signals": [], "ou_line_water_divergence": None, "ou_flat_water_nudge": None},
            kelly={"final": {"draw": 1.10}},
            market_sentiment={"available": True, "signals": [], "market_favorite": "home"},
        )
        self.assertEqual(pattern["verdict"], "neutral")
        self.assertTrue(any(lbl.startswith("◽") for lbl in pattern["warning_labels"]))
        _, diag = service.apply_market_operation_adjustment(
            final_prob={"home_win": 0.45, "draw": 0.30, "away_win": 0.25},
            pattern_diag=pattern,
            delta_vector={"fav": "home", "euro_fav_imp_move": 0.0},
            weights={},
        )
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "no_reliable_signal_label_only")

    def test_operation_weight_learner_below_sample_threshold_yields_zero_reliability(self):
        """样本不足 → reliability=0（仅提示不调权，防过拟合的自动退化）。"""
        from domain.operation_weight_learner import OperationWeightLearner
        learner = OperationWeightLearner(min_samples=80, min_auc_gap=0.07)
        samples = [
            {"euro_fav_imp_move": 0.05 if i % 2 else -0.05, "_fav_won": i % 2}
            for i in range(40)
        ]
        weights = learner.learn(samples)
        self.assertIn("euro_fav_imp_move", weights)
        self.assertEqual(weights["euro_fav_imp_move"]["reliability"], 0.0)

    def test_operation_weight_learner_learns_sign_and_reliability_for_strong_signal(self):
        """构造强信号(特征值与强侧赢完全同向)+足量样本 → 学出正sign与正reliability。"""
        from domain.operation_weight_learner import OperationWeightLearner
        learner = OperationWeightLearner(min_samples=80, min_auc_gap=0.07)
        samples = []
        for i in range(120):
            won = 1 if i % 2 == 0 else 0
            samples.append({"hcp_deepen": 1.0 + i * 0.01 if won else -1.0 - i * 0.01, "_fav_won": won})
        weights = learner.learn(samples)
        self.assertIn("hcp_deepen", weights)
        self.assertEqual(weights["hcp_deepen"]["sign"], 1)
        self.assertGreater(weights["hcp_deepen"]["reliability"], 0.0)
        self.assertGreaterEqual(weights["hcp_deepen"]["auc"], 0.9)

    def test_score_delta_zero_when_weights_empty(self):
        """系数全0 → score=0、无激活信号（调权退化为仅提示）。"""
        from domain.operation_weight_learner import score_delta
        score, active = score_delta({"fav": "home", "hcp_deepen": 0.3}, {})
        self.assertEqual(score, 0.0)
        self.assertEqual(active, [])

    def test_two_layer_adjustment_applies_when_reliable_weight_present(self):
        """有可靠系数 + 逐场Δ → 按方向连续调权（印证强侧时强侧概率上升）。"""
        service = object.__new__(InferencePipelineService)
        pattern = {"verdict": "neutral", "pattern": "static_static", "favored_side": "home", "warning_labels": []}
        weights = {
            "hcp_deepen": {"reliability": 1.0, "sign": 1, "auc": 0.95, "n": 120, "mean": 0.0, "std": 0.2},
        }
        delta_vector = {"fav": "home", "hcp_deepen": 0.4}
        adjusted, diag = service.apply_market_operation_adjustment(
            final_prob={"home_win": 0.50, "draw": 0.28, "away_win": 0.22},
            pattern_diag=pattern,
            delta_vector=delta_vector,
            weights=weights,
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["direction"], "endorse")
        self.assertGreater(adjusted["home_win"], 0.50)
        self.assertLess(adjusted["draw"], 0.28)
        self.assertAlmostEqual(sum(adjusted.values()), 1.0, places=6)

    def test_two_layer_adjustment_deceptive_direction_retreats_favorite(self):
        """跨轴背离(欧赔热但盘不升)可靠系数为利空强侧 → 回撤强侧概率。"""
        service = object.__new__(InferencePipelineService)
        pattern = {"verdict": "neutral", "pattern": "static_static", "favored_side": "home", "warning_labels": []}
        weights = {
            "div_euro_hot_hcp_flat": {"reliability": 1.0, "sign": -1, "auc": 0.10, "n": 120, "mean": 0.0, "std": 0.02},
        }
        delta_vector = {"fav": "home", "div_euro_hot_hcp_flat": 0.06}
        adjusted, diag = service.apply_market_operation_adjustment(
            final_prob={"home_win": 0.55, "draw": 0.27, "away_win": 0.18},
            pattern_diag=pattern,
            delta_vector=delta_vector,
            weights=weights,
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["direction"], "deceptive")
        self.assertLess(adjusted["home_win"], 0.55)
        self.assertAlmostEqual(sum(adjusted.values()), 1.0, places=6)

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

    def test_build_prediction_note_keeps_cross_direction_scores(self):
        note = build_prediction_note(
            {
                "prediction": "主胜",
                "confidence": 0.39,
                "top_scores": [("1-1", 0.24), ("1-0", 0.22), ("0-0", 0.2)],
                "over_under": {"line": 2.75, "over": 0.4, "under": 0.6},
                "upset_potential": {"level": "中", "index": 63},
            }
        )
        self.assertIn("比分:1-1/1-0/0-0", note)

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
        self.assertIn("比分:1-0/0-1", normalized)
        self.assertIn("案例:切尔西vs曼联(中度爆冷,强队胜强队)", normalized)
        self.assertIn("动态调权:样本不足", normalized)


class InferencePipelineReviewRetryTest(unittest.TestCase):
    def test_run_applies_league_confidence_adjustment_to_final_confidence(self):
        postprocess = PredictionPostprocessService({"premier_league": {"avg_goals": 2.7}})
        inference = InferencePipelineService(
            league_config={"premier_league": {"name": "英超", "avg_goals": 2.7}},
            team_manager=type("TM", (), {
                "analyze_team_strength": staticmethod(lambda league_code, team: {"strength": 1.0, "attack": 1.0, "defense": 1.0, "injured_count": 0})
            })(),
            match_intelligence_engine=type("MI", (), {
                "_build_match_intelligence": staticmethod(lambda **kwargs: {"available": True, "signals": [], "market": {"signals": []}}),
                "_apply_match_intelligence_adjustment": staticmethod(lambda final_prob, match_intelligence: (final_prob, {"applied": False, "signals": []})),
                "_finalize_match_intelligence": staticmethod(lambda **kwargs: kwargs.get("match_intelligence") or {}),
            })(),
            odds_reference=type("OR", (), {
                "find_similar_matches": staticmethod(lambda **kwargs: {"available": False, "similar_matches": [], "summary": {}}),
                "get_league_record_count": staticmethod(lambda league_code: 0),
            })(),
            upset_analyzer=type("UA", (), {
                "assess_upset_potential": staticmethod(lambda **kwargs: {"level": "低", "similar_cases_count": 0, "risk_score_detail": {}, "case_knowledge": {}})
            })(),
            model_fusion=type("MF", (), {
                "predict": staticmethod(lambda **kwargs: {"final": {"home_win": 0.44, "draw": 0.31, "away_win": 0.25}, "all_models": {}})
            })(),
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=type("WA", (), {"adjust_weights": staticmethod(lambda *args, **kwargs: {})})(),
            league_ou_learning=type("LOU", (), {})(),
            postprocess_service=postprocess,
        )
        inference.apply_dynamic_weights = lambda league_code: {
            "league_weight_factor": 1.031,
            "league_total_predictions": 18,
            "confidence_adjustment": 0.012,
            "weight_reason": "联赛近30天命中率高于全局基线，放大联赛学习调权",
        }
        inference.apply_live_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._build_preliminary_upset_potential = lambda **kwargs: {
            "available": False,
            "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
            "handicap_strength_mismatch": {"mismatch_detected": False},
        }
        inference.build_real_market_over_under = lambda **kwargs: ({"available": True, "line": 2.75, "over": 0.5, "under": 0.5}, {"available": True})
        inference.apply_real_totals_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._apply_draw_confirmation_guard = lambda **kwargs: (kwargs["final_prob"], {"applied": False, "qualified": False, "reason": "not_draw_top1", "signals": [], "evidence": []})

        realtime = {"context_applied": {}}
        result = inference.run(
            home_team="切尔西",
            away_team="诺丁汉森林",
            league_code="premier_league",
            match_date="2026-05-04",
            current_odds={
                "欧赔": {"final": {"home": 2.38, "draw": 3.34, "away": 3.04}},
                "亚值": {"final": {"handicap_value": 0.0}},
                "大小球": {"final": {"line": 2.75, "over": 1.9, "under": 1.9}},
            },
            analysis_context={"home_form": 1, "away_form": 5, "home_motivation": 86.0, "away_motivation": 84.0},
            realtime=realtime,
            review_learning={},
        )
        self.assertAlmostEqual(result["confidence"], 0.452, places=6)
        self.assertEqual(realtime["context_applied"]["league_confidence_adjustment"]["base_confidence"], 0.44)
        self.assertEqual(realtime["context_applied"]["league_confidence_adjustment"]["adjusted_confidence"], 0.452)

    def test_retry_review_outcome_adjustment_after_match_intelligence(self):
        postprocess = PredictionPostprocessService({"premier_league": {"avg_goals": 2.7}})
        inference = InferencePipelineService(
            league_config={"premier_league": {"avg_goals": 2.7}},
            team_manager=type("TM", (), {
                "analyze_team_strength": staticmethod(lambda league_code, team: {"strength": 1.0, "attack": 1.0, "defense": 1.0, "injured_count": 0})
            })(),
            match_intelligence_engine=type("MI", (), {
                "_build_match_intelligence": staticmethod(lambda **kwargs: {
                    "available": True,
                    "signals": ["战意差异"],
                    "market": {"signals": ["draw_guard"]},
                }),
                "_apply_match_intelligence_adjustment": staticmethod(lambda final_prob, match_intelligence: (
                    {"home_win": 0.40664878699423324, "draw": 0.3425485028967692, "away_win": 0.25080271010899763},
                    {
                        "applied": True,
                        "delta": {"home": 0.0121, "draw": 0.025, "away": -0.0138},
                        "signals": ["战意差异", "三盘口画像:draw_guard"],
                    },
                )),
                "_finalize_match_intelligence": staticmethod(lambda **kwargs: kwargs.get("match_intelligence") or {}),
            })(),
            odds_reference=type("OR", (), {
                "find_similar_matches": staticmethod(lambda **kwargs: {"available": False, "similar_matches": [], "summary": {}}),
                "get_league_record_count": staticmethod(lambda league_code: 0),
            })(),
            upset_analyzer=type("UA", (), {
                "assess_upset_potential": staticmethod(lambda **kwargs: {
                    "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
                    "handicap_strength_mismatch": {"mismatch_detected": False},
                })
            })(),
            model_fusion=type("MF", (), {
                "predict": staticmethod(lambda **kwargs: {"final": {"home_win": 0.45, "draw": 0.3, "away_win": 0.25}})
            })(),
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=type("WA", (), {"adjust_weights": staticmethod(lambda *args, **kwargs: {})})(),
            league_ou_learning=type("LOU", (), {})(),
            postprocess_service=postprocess,
        )
        inference.apply_dynamic_weights = lambda league_code: {}
        inference.apply_live_outcome_adjustment = lambda **kwargs: (
            {"home_win": 0.43254878699423323, "draw": 0.3166485028967692, "away_win": 0.25080271010899763},
            {"applied": True},
        )
        inference._build_preliminary_upset_potential = lambda **kwargs: {
            "available": True,
            "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
            "handicap_strength_mismatch": {"mismatch_detected": False},
        }
        inference.build_real_market_over_under = lambda **kwargs: ({"available": True, "line": 2.75, "over": 0.5, "under": 0.5}, {"available": True})
        inference.apply_real_totals_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._apply_draw_confirmation_guard = lambda **kwargs: (kwargs["final_prob"], {"applied": False, "qualified": False, "reason": "not_draw_top1", "signals": [], "evidence": []})

        realtime = {"context_applied": {}}
        result = inference.run(
            home_team="切尔西",
            away_team="诺丁汉森林",
            league_code="premier_league",
            match_date="2026-05-04",
            current_odds={
                "欧赔": {"final": {"home": 2.38, "draw": 3.34, "away": 3.04}},
                "亚值": {"final": {"handicap_value": 0.0}},
                "大小球": {"final": {"line": 2.75, "over": 1.9, "under": 1.9}},
            },
            analysis_context={"home_form": 1, "away_form": 5, "home_motivation": 86.0, "away_motivation": 84.0},
            realtime=realtime,
            review_learning={},
        )
        self.assertTrue(result["final_probabilities"]["draw"] > 0.34)
        self.assertTrue(result["final_probabilities"]["away_win"] > 0.25)
        self.assertTrue(realtime["context_applied"]["review_outcome_retry_gate"]["eligible"])
        self.assertTrue(realtime["context_applied"]["review_outcome_retry_gate"]["strong_away_motivation"])

    def test_retry_review_outcome_adjustment_retries_balanced_premier_home_top_without_first_pass_adjustment(self):
        postprocess = PredictionPostprocessService({"premier_league": {"avg_goals": 2.7}})
        inference = InferencePipelineService(
            league_config={"premier_league": {"avg_goals": 2.7}},
            team_manager=type("TM", (), {
                "analyze_team_strength": staticmethod(lambda league_code, team: {"strength": 1.0, "attack": 1.0, "defense": 1.0, "injured_count": 0})
            })(),
            match_intelligence_engine=type("MI", (), {
                "_build_match_intelligence": staticmethod(lambda **kwargs: {"available": True, "signals": [], "market": {"signals": []}}),
                "_apply_match_intelligence_adjustment": staticmethod(lambda final_prob, match_intelligence: (
                    {"home_win": 0.389, "draw": 0.324, "away_win": 0.287},
                    {"applied": True, "delta": {"home": -0.011, "draw": 0.012, "away": -0.001}, "signals": ["balanced_retry_case"]},
                )),
                "_finalize_match_intelligence": staticmethod(lambda **kwargs: kwargs.get("match_intelligence") or {}),
            })(),
            odds_reference=type("OR", (), {
                "find_similar_matches": staticmethod(lambda **kwargs: {"available": False, "similar_matches": [], "summary": {}}),
                "get_league_record_count": staticmethod(lambda league_code: 0),
            })(),
            upset_analyzer=type("UA", (), {
                "assess_upset_potential": staticmethod(lambda **kwargs: {
                    "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
                    "handicap_strength_mismatch": {"mismatch_detected": False},
                })
            })(),
            model_fusion=type("MF", (), {
                "predict": staticmethod(lambda **kwargs: {"final": {"home_win": 0.42, "draw": 0.31, "away_win": 0.27}})
            })(),
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=type("WA", (), {"adjust_weights": staticmethod(lambda *args, **kwargs: {})})(),
            league_ou_learning=type("LOU", (), {})(),
            postprocess_service=postprocess,
        )
        inference.apply_dynamic_weights = lambda league_code: {}
        inference.apply_live_outcome_adjustment = lambda **kwargs: (
            {"home_win": 0.42, "draw": 0.31, "away_win": 0.27},
            {"applied": False},
        )
        inference._build_preliminary_upset_potential = lambda **kwargs: {
            "available": True,
            "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
            "handicap_strength_mismatch": {"mismatch_detected": False},
        }
        inference.build_real_market_over_under = lambda **kwargs: ({"available": True, "line": 2.5, "over": 0.5, "under": 0.5}, {"available": True})
        inference.apply_real_totals_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._apply_draw_confirmation_guard = lambda **kwargs: (kwargs["final_prob"], {"applied": False, "qualified": False, "reason": "not_draw_top1", "signals": [], "evidence": []})

        realtime = {"context_applied": {}}
        result = inference.run(
            home_team="富勒姆",
            away_team="伯恩茅斯",
            league_code="premier_league",
            match_date="2026-05-09",
            current_odds={
                "欧赔": {"final": {"home": 2.64, "draw": 3.22, "away": 2.92}},
                "亚值": {"final": {"handicap_value": -0.25}},
                "大小球": {"final": {"line": 2.5, "over": 1.9, "under": 1.9}},
            },
            analysis_context={"home_form": 3, "away_form": 4, "home_motivation": 78.0, "away_motivation": 77.0},
            realtime=realtime,
            review_learning={},
        )
        self.assertTrue(realtime["context_applied"]["review_outcome_retry_gate"]["eligible"])
        self.assertTrue(realtime["context_applied"]["review_outcome_retry_gate"]["balanced_home_top_case"])
        self.assertIn("review_outcome_adjustment_retry", realtime["context_applied"])
        self.assertGreater(result["final_probabilities"]["away_win"], 0.287)

    def test_retry_review_outcome_adjustment_skips_non_premier_league(self):
        postprocess = PredictionPostprocessService({"serie_a": {"avg_goals": 2.7}})
        inference = InferencePipelineService(
            league_config={"serie_a": {"avg_goals": 2.7}},
            team_manager=type("TM", (), {
                "analyze_team_strength": staticmethod(lambda league_code, team: {"strength": 1.0, "attack": 1.0, "defense": 1.0, "injured_count": 0})
            })(),
            match_intelligence_engine=type("MI", (), {
                "_build_match_intelligence": staticmethod(lambda **kwargs: {"available": True, "signals": [], "market": {"signals": []}}),
                "_apply_match_intelligence_adjustment": staticmethod(lambda final_prob, match_intelligence: (
                    {"home_win": 0.41, "draw": 0.34, "away_win": 0.25},
                    {"applied": True, "delta": {"home": 0.01, "draw": 0.01, "away": -0.02}, "signals": []},
                )),
                "_finalize_match_intelligence": staticmethod(lambda **kwargs: kwargs.get("match_intelligence") or {}),
            })(),
            odds_reference=type("OR", (), {
                "find_similar_matches": staticmethod(lambda **kwargs: {"available": False, "similar_matches": [], "summary": {}}),
                "get_league_record_count": staticmethod(lambda league_code: 0),
            })(),
            upset_analyzer=type("UA", (), {
                "assess_upset_potential": staticmethod(lambda **kwargs: {"motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22}, "handicap_strength_mismatch": {"mismatch_detected": False}})
            })(),
            model_fusion=type("MF", (), {
                "predict": staticmethod(lambda **kwargs: {"final": {"home_win": 0.42, "draw": 0.33, "away_win": 0.25}})
            })(),
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=type("WA", (), {"adjust_weights": staticmethod(lambda *args, **kwargs: {})})(),
            league_ou_learning=type("LOU", (), {})(),
            postprocess_service=postprocess,
        )
        inference.apply_dynamic_weights = lambda league_code: {}
        inference.apply_live_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._build_preliminary_upset_potential = lambda **kwargs: {
            "available": True,
            "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
            "handicap_strength_mismatch": {"mismatch_detected": False},
        }
        inference.build_real_market_over_under = lambda **kwargs: ({"available": True, "line": 2.75, "over": 0.5, "under": 0.5}, {"available": True})
        inference.apply_real_totals_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._apply_draw_confirmation_guard = lambda **kwargs: (kwargs["final_prob"], {"applied": False, "qualified": False, "reason": "not_draw_top1", "signals": [], "evidence": []})

        realtime = {"context_applied": {}}
        result = inference.run(
            home_team="卡利亚里",
            away_team="乌迪内斯",
            league_code="serie_a",
            match_date="2026-05-09",
            current_odds={
                "欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}},
                "亚值": {"final": {"handicap_value": -0.5}},
                "大小球": {"final": {"line": 2.25, "over": 1.9, "under": 1.8}},
            },
            analysis_context={"home_form": 3, "away_form": 3, "home_motivation": 80.0, "away_motivation": 82.0},
            realtime=realtime,
            review_learning={},
        )
        self.assertEqual(result["final_probabilities"]["home_win"], 0.41)
        self.assertFalse(realtime["context_applied"]["review_outcome_retry_gate"]["eligible"])
        self.assertNotIn("review_outcome_adjustment_retry", realtime["context_applied"])

    def test_serie_a_full_upset_knowledge_retry_restores_away_path_after_blocked_soft_draw(self):
        postprocess = PredictionPostprocessService({"serie_a": {"avg_goals": 2.7}})
        inference = InferencePipelineService(
            league_config={"serie_a": {"avg_goals": 2.7}},
            team_manager=type("TM", (), {
                "analyze_team_strength": staticmethod(lambda league_code, team: {"strength": 1.0, "attack": 1.0, "defense": 1.0, "injured_count": 0})
            })(),
            match_intelligence_engine=type("MI", (), {
                "_build_match_intelligence": staticmethod(lambda **kwargs: {"available": True, "signals": [], "market": {"signals": []}}),
                "_apply_match_intelligence_adjustment": staticmethod(lambda final_prob, match_intelligence: (final_prob, {"applied": False, "signals": []})),
                "_finalize_match_intelligence": staticmethod(lambda **kwargs: kwargs.get("match_intelligence") or {}),
            })(),
            odds_reference=type("OR", (), {
                "find_similar_matches": staticmethod(lambda **kwargs: {
                    "available": True,
                    "summary": {
                        "sample_size": 5,
                        "result_rates": {"主胜": 0.2, "平局": 0.2, "客胜": 0.6},
                        "cold_result_rate": 0.6,
                    },
                }),
                "get_league_record_count": staticmethod(lambda league_code: 0),
            })(),
            upset_analyzer=type("UA", (), {
                "assess_upset_potential": staticmethod(lambda **kwargs: {
                    "level": "中",
                    "similar_cases_count": 1,
                    "risk_score_detail": {"knowledge_score": 18.0},
                    "historical_odds_reference": {
                        "available": True,
                        "summary": {
                            "sample_size": 5,
                            "result_rates": {"主胜": 0.2, "平局": 0.2, "客胜": 0.6},
                            "cold_result_rate": 0.6,
                        },
                    },
                    "case_knowledge": {"available": True, "hint": "那不勒斯vs拉齐奥(中度爆冷)"},
                    "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                    "handicap_strength_mismatch": {"mismatch_detected": False},
                })
            })(),
            model_fusion=type("MF", (), {
                "predict": staticmethod(lambda **kwargs: {"final": {"home_win": 0.36, "draw": 0.401, "away_win": 0.239}})
            })(),
            poisson_model=_DummyPoissonModel(),
            weight_adjuster=type("WA", (), {"adjust_weights": staticmethod(lambda *args, **kwargs: {})})(),
            league_ou_learning=type("LOU", (), {})(),
            postprocess_service=postprocess,
        )
        inference.apply_dynamic_weights = lambda league_code: {}
        inference.apply_live_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})
        inference._build_preliminary_upset_potential = lambda **kwargs: {
            "available": False,
            "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
            "handicap_strength_mismatch": {"mismatch_detected": False},
        }
        inference.build_real_market_over_under = lambda **kwargs: ({"available": True, "line": 2.5, "over": 0.3181, "under": 0.6819}, {"available": True})
        inference.apply_real_totals_outcome_adjustment = lambda **kwargs: (kwargs["final_prob"], {"applied": False})

        original_apply_review_outcome_adjustment = postprocess.apply_review_outcome_adjustment
        review_call_count = {"count": 0}

        def fake_apply_review_outcome_adjustment(**kwargs):
            review_call_count["count"] += 1
            if review_call_count["count"] == 1:
                return (
                    {"home_win": 0.3622434699, "draw": 0.4006465768, "away_win": 0.2371099533},
                    {
                        "applied": False,
                        "signals": ["review-league-serie-a-soft-draw-away-blocked-home-top"],
                        "reason": "three_layer_evaluated_no_adjustment",
                        "three_layer_evaluated": True,
                        "home_bias_gate": {"qualified": False, "evidence": []},
                        "applied_shift": {"draw_shift": 0.0, "away_shift": 0.0, "home_shift": 0.0, "draw_to_away_relief": 0.0, "draw_to_away_trim": 0.0},
                        "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "draw_soft"},
                        "motivation_risk": {"available": False, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                    },
                )
            return original_apply_review_outcome_adjustment(**kwargs)

        inference.postprocess_service.apply_review_outcome_adjustment = fake_apply_review_outcome_adjustment

        original_draw_guard = InferencePipelineService._apply_draw_confirmation_guard

        def fake_draw_guard(**kwargs):
            diag = kwargs.get("review_outcome_diag") or {}
            signals = set(str(item).strip() for item in (diag.get("signals") or []) if str(item).strip())
            if "review-league-serie-a-upset-knowledge-retry" in signals:
                return (
                    {"home_win": 0.334, "draw": 0.321, "away_win": 0.345},
                    {
                        "applied": True,
                        "qualified": False,
                        "reason": "draw_confirmation_failed_shifted",
                        "signals": [
                            "draw_market_not_confirmed",
                            "serie_a_soft_draw_confirmation_override",
                            "serie_a_soft_redirect_draw_to_away",
                            "away_upset_redirect_draw_to_away",
                        ],
                        "evidence": ["under_supports_draw"],
                        "favored_side": "away_win",
                    },
                )
            if "review-league-serie-a-soft-draw-away-blocked-home-top" in signals:
                return (
                    {"home_win": 0.3540668835, "draw": 0.4011054089, "away_win": 0.2448277076},
                    {
                        "applied": False,
                        "qualified": True,
                        "reason": "draw_confirmation_passed",
                        "signals": [
                            "draw_market_not_confirmed",
                        ],
                        "evidence": ["draw_prob_clear_lead", "under_supports_draw"],
                        "favored_side": None,
                    },
                )
            return original_draw_guard(**kwargs)

        inference._apply_draw_confirmation_guard = fake_draw_guard

        realtime = {"context_applied": {}}
        result = inference.run(
            home_team="那不勒斯",
            away_team="博洛尼亚",
            league_code="serie_a",
            match_date="2026-05-12",
            current_odds={
                "欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}},
                "亚值": {"final": {"handicap_value": -0.75}},
                "大小球": {"final": {"line": 2.5, "over": 1.9144, "under": 1.8511}},
            },
            analysis_context={"home_form": 3, "away_form": 3, "home_motivation": 75.0, "away_motivation": 74.0},
            realtime=realtime,
            review_learning={},
        )
        self.assertEqual(result["main_prediction"], "客胜")
        self.assertGreater(result["final_probabilities"]["away_win"], result["final_probabilities"]["home_win"])
        self.assertTrue(realtime["context_applied"]["serie_a_upset_knowledge_retry_gate"]["eligible"])
        self.assertIn("review-league-serie-a-upset-knowledge-retry", realtime["context_applied"]["review_outcome_adjustment"]["signals"])
        self.assertTrue(realtime["context_applied"]["review_outcome_adjustment_full_upset_retry"]["retry_from_full_upset_knowledge"])
        self.assertEqual(realtime["context_applied"]["draw_confirmation_guard"]["favored_side"], "away_win")

    def test_serie_a_draw_guarded_home_path_can_enter_narrow_review_without_retry(self):
        postprocess = PredictionPostprocessService({"serie_a": {"avg_goals": 2.7}})
        adjusted, diag = postprocess.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.4112078931, "draw": 0.3037017170, "away_win": 0.2850903899},
            league_code="serie_a",
            strength_diff=0,
            asian_handicap={"final": {"handicap_value": -0.5}},
            current_odds={
                "欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}},
                "大小球": {
                    "initial": {"line": 2.25, "over": 1.9457, "under": 1.8329},
                    "final": {"line": 2.25, "over": 1.9557, "under": 1.82},
                },
            },
            review_learning={"league_review": {"league_tags": ["意甲联赛-主胜偏置"]}},
            upset_potential={
                "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
                "handicap_strength_mismatch": {"mismatch_detected": False},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("review-league-serie-a-home-draw-guard-entry", diag["signals"])
        self.assertGreater(adjusted["away_win"], 0.2850903899)


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

    def test_under_three_keeps_deeper_candidates_for_postprocess(self):
        top_scores, diag = InferencePipelineService._rerank_scores_for_under_three(
            {
                "1-0": 0.2082,
                "0-0": 0.1404,
                "2-0": 0.1282,
                "1-1": 0.1110,
                "0-1": 0.1016,
                "2-1": 0.0727,
                "3-0": 0.0526,
                "1-2": 0.0355,
                "0-2": 0.0305,
                "3-1": 0.0298,
            },
            {"line": 2.5, "over": 0.3181, "under": 0.6819},
            limit=8,
        )
        self.assertTrue(diag["applied"])
        self.assertGreaterEqual(len(top_scores), 8)
        self.assertIn("0-1", [score for score, _ in top_scores])
        self.assertIn("1-2", [score for score, _ in top_scores])
        self.assertNotIn("0-2", [score for score, _ in top_scores])

    def test_under_three_retains_open_learning_tail_candidates(self):
        top_scores, diag = InferencePipelineService._rerank_scores_for_under_three(
            {
                "1-1": 0.12263,
                "1-0": 0.111834,
                "0-1": 0.101031,
                "2-1": 0.083985,
                "1-2": 0.075872,
                "0-0": 0.075236,
                "2-0": 0.073527,
                "0-2": 0.060008,
                "2-2": 0.049883,
                "3-1": 0.036812,
                "3-0": 0.032228,
                "1-3": 0.030043,
                "0-3": 0.023762,
                "3-2": 0.021864,
            },
            {
                "line": 2.5,
                "over": 0.4628,
                "under": 0.5372,
                "league_learning": {
                    "recent_avg_goals": 2.95,
                    "over25_rate": 0.65,
                    "over35_rate": 0.35,
                    "btts_rate": 0.6,
                },
                "market": {
                    "initial": {"line": 3.25},
                    "final": {"line": 2.5},
                },
            },
            limit=8,
        )
        self.assertTrue(diag["applied"])
        self.assertIn("under3-open-learning-tail-retention", diag["signals"])
        self.assertEqual(diag["target_limit"], 20)
        self.assertGreaterEqual(len(top_scores), 14)
        self.assertIn("3-1", [score for score, _ in top_scores])
        self.assertIn("3-2", [score for score, _ in top_scores])
        self.assertNotIn("4-2", [score for score, _ in top_scores])


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

    def test_draw_confirmation_guard_rebounds_moderate_unconfirmed_draw_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3431850768, "draw": 0.4140136888, "away_win": 0.2428012344},
            current_odds={"欧赔": {"final": {"home": 2.18, "draw": 3.852, "away": 4.35}}},
            over_under={"line": 3.0, "over": 0.2739, "under": 0.7261},
            match_intelligence={"scenario_tags": []},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("home_rebound_draw_confirmation_override", diag["signals"])
        self.assertIn("home_rebound_draw_excess_boost", diag["signals"])
        self.assertIn("home_rebound_redirect_draw_to_home", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_skips_home_rebound_when_draw_has_volatility_support(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3333184011, "draw": 0.3347094250, "away_win": 0.3319721739},
            current_odds={"欧赔": {"final": {"home": 2.32, "draw": 4.1261, "away": 3.85}}},
            over_under={"line": 2.75, "over": 0.45145, "under": 0.54855},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high", "premier_league_relegation_home_motivation_bonus"],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "high"}}},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertNotIn("home_rebound_draw_confirmation_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.3347094250)

    def test_draw_confirmation_guard_trims_near_tie_home_rebound_into_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3408472779, "draw": 0.4320708255, "away_win": 0.2270818966},
            current_odds={"欧赔": {"final": {"home": 1.73, "draw": 5.2213, "away": 5.52}}},
            over_under={"line": 3.5, "over": 0.2027, "under": 0.7973},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "high"}}},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("home_rebound_draw_confirmation_override", diag["signals"])
        self.assertIn("home_rebound_near_tie_trim", diag["signals"])
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_redirects_fragile_home_draw_to_away(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.324, "draw": 0.355, "away_win": 0.321},
            current_odds={"欧赔": {"final": {"home": 2.28, "draw": 3.46, "away": 3.06}}},
            over_under={"line": 2.75, "over": 0.57, "under": 0.43},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-fragile-home-favorite-correction"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "away"},
                "home_bias_gate": {"evidence": ["away_motivation_pressure", "handicap_strength_mismatch"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertEqual(diag["favored_side"], "away_win")
        self.assertIn("fragile_home_redirect_draw_to_away", diag["signals"])
        self.assertLess(adjusted["draw"], 0.355)
        self.assertGreater(adjusted["away_win"], 0.321)
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_overrides_fragile_home_draw_when_only_gap_confirms(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.321, "draw": 0.371, "away_win": 0.308},
            current_odds={"欧赔": {"final": {"home": 2.26, "draw": 3.46, "away": 3.04}}},
            over_under={"line": 2.75, "over": 0.56, "under": 0.44},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-fragile-home-favorite-correction"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "away"},
                "home_bias_gate": {"evidence": ["away_motivation_pressure", "handicap_strength_mismatch"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("fragile_home_draw_confirmation_override", diag["signals"])
        self.assertIn("fragile_home_redirect_draw_to_away", diag["signals"])
        self.assertLess(adjusted["draw"], 0.371)
        self.assertGreater(adjusted["away_win"], 0.308)

    def test_draw_confirmation_guard_keeps_fragile_home_when_market_really_confirms_draw(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.324, "draw": 0.355, "away_win": 0.321},
            current_odds={"欧赔": {"final": {"home": 3.18, "draw": 3.02, "away": 3.22}}},
            over_under={"line": 2.25, "over": 0.42, "under": 0.58},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-fragile-home-favorite-correction"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "away"},
                "home_bias_gate": {"evidence": ["away_motivation_pressure"]},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertNotIn("fragile_home_draw_confirmation_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.355)

    def test_draw_confirmation_guard_rebounds_fragile_home_medium_strong_support_case_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3334944443, "draw": 0.3869122622, "away_win": 0.2795932935},
            current_odds={"欧赔": {"final": {"home": 1.493, "draw": 3.711, "away": 7.636}}},
            over_under={"line": 3.0, "over": 0.19095, "under": 0.80905},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "low"}}},
            },
            review_outcome_diag={
                "signals": [
                    "review-bias-config-floor",
                    "review-motivation-risk-correction",
                    "review-fragile-home-favorite-correction",
                ],
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "pressure_side": "away",
                    "favored_side": "home",
                    "score": 14.22,
                },
                "home_bias_gate": {"qualified": True, "evidence": ["limited_probability_edge", "limited_strength_gap", "away_motivation_pressure"]},
                "applied_shift": {"draw_shift": 0.0174, "away_shift": 0.03, "home_shift": 0.0},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("fragile_home_strong_support_rebound_override", diag["signals"])
        self.assertIn("fragile_home_strong_support_redirect_draw_to_home", diag["signals"])
        self.assertNotIn("fragile_home_redirect_draw_to_away", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])
        self.assertGreater(adjusted["home_win"], adjusted["away_win"])

    def test_draw_confirmation_guard_redirects_away_upset_signal_when_draw_only_leads_by_gap(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.33, "draw": 0.368, "away_win": 0.336},
            current_odds={"欧赔": {"final": {"home": 2.62, "draw": 3.46, "away": 2.66}}},
            over_under={"line": 2.75, "over": 0.56, "under": 0.44},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.6},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.024},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("near_tie_away_promotion", diag["signals"])
        self.assertIn("near_tie_redirect_draw_to_away", diag["signals"])
        self.assertIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertLess(adjusted["draw"], 0.368)
        self.assertGreater(adjusted["away_win"], 0.336)
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_skips_near_tie_promotion_when_under_supports_draw(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.314, "draw": 0.368, "away_win": 0.318},
            current_odds={"欧赔": {"final": {"home": 2.62, "draw": 3.46, "away": 2.66}}},
            over_under={"line": 2.5, "over": 0.44, "under": 0.56},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.6},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.024},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertNotIn("near_tie_away_promotion", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.368)

    def test_draw_confirmation_guard_allows_serie_a_soft_override_with_review_relief(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3336958657, "draw": 0.3732840088, "away_win": 0.2930201256},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.25, "over": 0.27685, "under": 0.72315},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "high"}}},
            },
            review_outcome_diag={
                "signals": ["review-league-serie-a-draw-to-away-relief", "review-narrow-away-bump"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
                "applied_shift": {"draw_shift": 0.02, "away_shift": 0.033, "draw_to_away_relief": 0.046},
                "three_layer_context": {"handicap_depth_bucket": "level_medium"},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_soft_draw_confirmation_override", diag["signals"])
        self.assertIn("serie_a_soft_redirect_draw_to_away", diag["signals"])
        self.assertIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

    def test_draw_confirmation_guard_allows_serie_a_soft_override_for_low_risk_home_favored_relief(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3540668835, "draw": 0.4011054089, "away_win": 0.2448277076},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.5, "over": 0.3181, "under": 0.6819},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": ["review-league-serie-a-draw-to-away-relief", "review-league-serie-a-soft-draw-away-entry"],
                "motivation_risk": {"supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                "applied_shift": {"draw_shift": 0.0, "away_shift": 0.046, "draw_to_away_relief": 0.046},
                "three_layer_context": {"handicap_depth_bucket": "level_medium"},
                "home_bias_gate": {"evidence": []},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_soft_draw_confirmation_override", diag["signals"])
        self.assertIn("serie_a_soft_redirect_draw_to_away", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

    def test_draw_confirmation_guard_adds_serie_a_soft_final_margin_trim_when_away_just_trails_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.355, "draw": 0.37, "away_win": 0.275},
            current_odds={"欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}}},
            over_under={"line": 2.5, "over": 0.491595, "under": 0.508405},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": ["review-league-serie-a-draw-to-away-relief", "review-league-serie-a-soft-draw-away-entry"],
                "motivation_risk": {"supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                "applied_shift": {"draw_shift": 0.0, "away_shift": 0.046, "draw_to_away_relief": 0.046},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "draw_soft"},
                "home_bias_gate": {"evidence": []},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_soft_final_margin_trim", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_keeps_ligue1_near_away_top_stable(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3176168262, "draw": 0.3369166348, "away_win": 0.3454665390},
            current_odds={"欧赔": {"final": {"home": 1.74, "draw": 4.1, "away": 4.08}}},
            over_under={"line": 3.0, "over": 0.1896, "under": 0.8737},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": [
                    "review-bias-config-floor",
                    "review-league-ligue1-home-away-relief",
                    "review-league-ligue1-home-edge-trim",
                ],
                "motivation_risk": {"supports_upset": False, "pressure_side": "", "favored_side": "", "score": 0.0},
                "applied_shift": {"draw_shift": 0.0151, "away_shift": 0.056, "draw_to_away_trim": 0.0092},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
                "home_bias_gate": {"evidence": []},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "not_draw_top1")
        self.assertGreater(adjusted["away_win"], adjusted["draw"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_redirects_ligue1_home_edge_false_draw_to_away(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.316858, "draw": 0.36298, "away_win": 0.320162},
            current_odds={"欧赔": {"final": {"home": 1.74, "draw": 4.1, "away": 4.08}}},
            over_under={"line": 3.0, "over": 0.157964, "under": 0.842036},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": [
                    "review-bias-config-floor",
                    "review-league-ligue1-home-away-relief",
                    "review-league-ligue1-home-edge-trim",
                ],
                "motivation_risk": {"available": True, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
                "applied_shift": {"draw_shift": 0.0151, "away_shift": 0.056, "draw_to_away_trim": 0.0091},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
                "home_bias_gate": {"evidence": ["limited_probability_edge", "limited_strength_gap"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("ligue1_home_edge_redirect_draw_to_away", diag["signals"])
        self.assertIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertEqual(diag["favored_side"], "away_win")
        self.assertGreater(adjusted["away_win"], adjusted["draw"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_rebounds_blocked_serie_a_soft_draw_back_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3622434699, "draw": 0.4006465768, "away_win": 0.2371099533},
            current_odds={"欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}}},
            over_under={"line": 2.5, "over": 0.3181, "under": 0.6819},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": ["review-league-serie-a-soft-draw-away-blocked-home-top"],
                "motivation_risk": {"supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                "applied_shift": {"draw_shift": 0.0, "away_shift": 0.0, "draw_to_away_relief": 0.0},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "draw_soft"},
                "home_bias_gate": {"evidence": []},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_soft_blocked_home_draw_confirmation_override", diag["signals"])
        self.assertIn("serie_a_soft_blocked_home_redirect_draw_to_home", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])
        self.assertGreater(adjusted["home_win"], adjusted["away_win"])

    def test_draw_confirmation_guard_adds_serie_a_home_fragility_final_trim_for_cagliari_shape(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.34044946855031734, "draw": 0.3729137508748933, "away_win": 0.2866367805747895},
            current_odds={"欧赔": {"final": {"home": 2.5844, "draw": 2.9995, "away": 2.9031}}},
            over_under={"line": 2.25, "over": 0.27775, "under": 0.72225},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "high"}}},
            },
            review_outcome_diag={
                "signals": [
                    "review-bias-config-floor",
                    "review-league-serie-a-home-draw-guard-entry",
                    "review-league-serie-a-home-draw-guard-near-tie",
                ],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
                "applied_shift": {"draw_shift": 0.02, "away_shift": 0.03},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "draw_guarded"},
                "home_bias_gate": {"evidence": ["limited_probability_edge", "limited_strength_gap"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_home_fragility_draw_excess_boost", diag["signals"])
        self.assertIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertIn("serie_a_home_fragility_final_trim", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_overrides_serie_a_upset_knowledge_draw_to_away(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.2939234699, "draw": 0.3994065768, "away_win": 0.3066699533},
            current_odds={"欧赔": {"final": {"home": 1.5377, "draw": 4.0433, "away": 6.1386}}},
            over_under={"line": 2.5, "over": 0.3181, "under": 0.6819},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "high"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": [
                    "review-fragile-home-favorite-correction",
                    "review-league-serie-a-draw-to-away-relief",
                    "review-league-serie-a-soft-draw-away-entry",
                    "review-league-serie-a-upset-knowledge-retry",
                ],
                "motivation_risk": {"supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 3.0},
                "applied_shift": {"draw_shift": 0.0128, "away_shift": 0.046},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
                "home_bias_gate": {"evidence": ["limited_probability_edge", "limited_strength_gap", "runner_up_close"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("serie_a_upset_knowledge_draw_confirmation_override", diag["signals"])
        self.assertIn("serie_a_upset_knowledge_draw_excess_boost", diag["signals"])
        self.assertIn("serie_a_upset_knowledge_redirect_draw_to_away", diag["signals"])
        self.assertEqual(diag["favored_side"], "away_win")
        self.assertGreater(adjusted["away_win"], adjusted["draw"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_uses_narrow_away_bump_as_near_tie_redirect_signal(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3211217112, "draw": 0.3403209648, "away_win": 0.3385573239},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.75, "over": 0.2882, "under": 0.7118},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-narrow-away-bump"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.68},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.033},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("near_tie_away_promotion", diag["signals"])
        self.assertIn("narrow_away_strong_redirect_boost", diag["signals"])
        self.assertIn("narrow_away_strong_redirect", diag["signals"])
        self.assertIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_extends_near_tie_window_for_strong_narrow_away_bump(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3211217112, "draw": 0.3851209648, "away_win": 0.3385573239},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.75, "over": 0.2882, "under": 0.7118},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-narrow-away-bump"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.68},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.033},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("near_tie_away_promotion", diag["signals"])
        self.assertIn("narrow_away_strong_redirect_boost", diag["signals"])
        self.assertIn("narrow_away_strong_redirect", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_flips_strong_narrow_away_bump_case_to_away(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3135217112, "draw": 0.3783209648, "away_win": 0.3081573239},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.75, "over": 0.2882, "under": 0.7118},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": ["review-narrow-away-bump"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.68},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.033},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("narrow_away_strong_redirect_boost", diag["signals"])
        self.assertIn("narrow_away_strong_redirect", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])

    def test_draw_confirmation_guard_trims_near_tie_strong_away_redirect_into_away(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3332430249, "draw": 0.3784668883, "away_win": 0.2882900868},
            current_odds={"欧赔": {"final": {"home": 2.92, "draw": 3.12, "away": 2.46}}},
            over_under={"line": 2.75, "over": 0.28505, "under": 0.71495},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "low"}}},
            },
            review_outcome_diag={
                "signals": ["review-narrow-away-bump"],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 15.68},
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.033},
                "home_bias_gate": {"evidence": ["limited_probability_edge"]},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("away_upset_near_tie_trim", diag["signals"])
        self.assertIn("narrow_away_strong_redirect", diag["signals"])
        self.assertGreater(adjusted["away_win"], adjusted["home_win"])
        self.assertGreater(adjusted["away_win"], adjusted["draw"])

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

    def test_draw_confirmation_guard_rebounds_la_liga_flat_false_draw_to_home_when_market_disagrees(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.347, "draw": 0.409, "away_win": 0.244},
            current_odds={"欧赔": {"final": {"home": 2.08, "draw": 3.86, "away": 4.54}}},
            over_under={"line": 3.0, "over": 0.274, "under": 0.726},
            match_intelligence={"scenario_tags": ["la_liga_mid_table_home_flat"]},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("home_rebound_draw_confirmation_override", diag["signals"])
        self.assertIn("home_rebound_redirect_draw_to_home", diag["signals"])
        self.assertNotIn("draw_confirmation_passed", diag["reason"])
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_rebounds_la_liga_fragile_home_false_draw_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3348, "draw": 0.3872, "away_win": 0.278},
            current_odds={"欧赔": {"final": {"home": 1.52, "draw": 3.92, "away": 7.05}}},
            over_under={"line": 3.0, "over": 0.191, "under": 0.809},
            match_intelligence={"scenario_tags": ["la_liga_mid_table_home_flat"]},
            review_outcome_diag={
                "signals": [
                    "review-bias-config-floor",
                    "review-motivation-risk-correction",
                    "review-fragile-home-favorite-correction",
                ],
                "motivation_risk": {
                    "available": True,
                    "supports_upset": True,
                    "pressure_side": "away",
                    "favored_side": "home",
                    "score": 14.22,
                },
                "home_bias_gate": {"qualified": True, "evidence": ["limited_probability_edge", "away_motivation_pressure"]},
                "applied_shift": {"draw_shift": 0.0174, "away_shift": 0.03, "home_shift": 0.0},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("fragile_home_strong_support_rebound_override", diag["signals"])
        self.assertIn("fragile_home_strong_support_redirect_draw_to_home", diag["signals"])
        self.assertNotIn("fragile_home_redirect_draw_to_away", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_keeps_la_liga_weak_gap_draw_without_support_evidence(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.361955983234495, "draw": 0.3669740570443156, "away_win": 0.27106995972118946},
            current_odds={"欧赔": {"final": {"home": 2.67, "draw": 4.3608, "away": 3.8894}}},
            over_under={"line": 2.75, "over": 0.37075, "under": 0.62925},
            match_intelligence={
                "scenario_tags": [],
                "contextual_rules": {"volatility": {"home": {"label": "low"}, "away": {"label": "low"}}},
            },
            review_outcome_diag={
                "signals": ["review-bias-config-floor"],
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "pressure_side": "away",
                    "favored_side": "home",
                    "score": 3.0,
                },
                "applied_shift": {"draw_shift": 0.011, "away_shift": 0.0, "home_shift": 0.0},
                "three_layer_context": {"handicap_depth_bucket": "level_medium", "euro_support_bucket": "strong_support"},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertEqual(diag["reason"], "la_liga_weak_gap_draw_retained")
        self.assertIn("draw_confirmation_gap_weak", diag["signals"])
        self.assertIn("draw_market_not_confirmed", diag["signals"])
        self.assertIn("la_liga_weak_gap_draw_retention", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.3669740570443156)
        self.assertGreater(adjusted["draw"], adjusted["home_win"])

    def test_draw_confirmation_guard_overrides_extreme_under_draw_without_market_support(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3490907957, "draw": 0.4266856709, "away_win": 0.2242235334},
            current_odds={"欧赔": {"final": {"home": 1.42, "draw": 4.0065, "away": 6.593}}},
            over_under={"line": 3.0, "over": 0.1801, "under": 0.8199},
            match_intelligence={"scenario_tags": []},
        )
        self.assertTrue(diag["applied"])
        self.assertIn("extreme_under_not_draw_confirmed", diag["signals"])
        self.assertIn("extreme_under_draw_confirmation_override", diag["signals"])
        self.assertLess(adjusted["draw"], 0.4266856709)
        self.assertGreater(adjusted["home_win"], 0.3490907957)

    def test_draw_confirmation_guard_keeps_extreme_under_draw_when_away_remains_close(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3450253485, "draw": 0.3922639777, "away_win": 0.2627106738},
            current_odds={"欧赔": {"final": {"home": 1.43, "draw": 6.2982, "away": 6.9232}}},
            over_under={"line": 3.75, "over": 0.27185, "under": 0.72815},
            match_intelligence={"scenario_tags": []},
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertIn("extreme_under_not_draw_confirmed", diag["signals"])
        self.assertNotIn("extreme_under_draw_confirmation_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.3922639777)

    def test_draw_confirmation_guard_overrides_deep_soft_extreme_under_false_draw(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3234520396, "draw": 0.4281410287, "away_win": 0.2484069317},
            current_odds={"欧赔": {"final": {"home": 1.74, "draw": 4.32, "away": 5.28}}},
            over_under={"line": 3.0, "over": 0.19, "under": 0.81},
            match_intelligence={"scenario_tags": ["recent_form_volatility_high"]},
            review_outcome_diag={
                "signals": [],
                "three_layer_context": {
                    "handicap_depth_bucket": "level_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("deep_soft_extreme_under_draw_confirmation_override", diag["signals"])
        self.assertIn("deep_soft_extreme_under_redirect_draw_to_home", diag["signals"])
        self.assertLess(adjusted["draw"], 0.4281410287)
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_keeps_medium_bucket_extreme_under_draw_without_deep_soft_override(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3234520396, "draw": 0.4281410287, "away_win": 0.2484069317},
            current_odds={"欧赔": {"final": {"home": 1.74, "draw": 4.32, "away": 5.28}}},
            over_under={"line": 3.0, "over": 0.19, "under": 0.81},
            match_intelligence={"scenario_tags": ["recent_form_volatility_high"]},
            review_outcome_diag={
                "signals": [],
                "three_layer_context": {
                    "handicap_depth_bucket": "level_medium",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertIn("extreme_under_not_draw_confirmed", diag["signals"])
        self.assertNotIn("deep_soft_extreme_under_draw_confirmation_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.4281410287)

    def test_draw_confirmation_guard_rebounds_deep_soft_extreme_under_without_upset_signal(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3275410198, "draw": 0.4146231841, "away_win": 0.2578357961},
            current_odds={"欧赔": {"final": {"home": 1.62, "draw": 4.33, "away": 6.0}}},
            over_under={"line": 3.0, "over": 0.2701, "under": 0.7299},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("deep_soft_extreme_under_rebound_override", diag["signals"])
        self.assertIn("deep_soft_extreme_under_rebound_redirect_draw_to_home", diag["signals"])
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_skips_deep_soft_extreme_under_rebound_when_upset_signal_exists(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3275410198, "draw": 0.4146231841, "away_win": 0.2578357961},
            current_odds={"欧赔": {"final": {"home": 1.62, "draw": 4.33, "away": 6.0}}},
            over_under={"line": 3.0, "over": 0.2701, "under": 0.7299},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "away", "favored_side": "home", "score": 14.22},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertNotIn("deep_soft_extreme_under_rebound_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.4146231841)

    def test_draw_confirmation_guard_rebounds_very_deep_extreme_under_false_draw_even_with_home_favored_pressure(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3076518962, "draw": 0.4336967655, "away_win": 0.2586513382},
            current_odds={"欧赔": {"final": {"home": 1.48, "draw": 6.38, "away": 7.8}}},
            over_under={"line": 3.25, "over": 0.1961, "under": 0.8039},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "away", "favored_side": "home", "score": 14.22},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_very_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("deep_soft_extreme_under_rebound_override", diag["signals"])
        self.assertIn("deep_soft_extreme_under_rebound_redirect_draw_to_home", diag["signals"])
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_rebounds_deep_extreme_under_false_draw_with_home_favored_pressure(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.307900195, "draw": 0.4322791888, "away_win": 0.2598206162},
            current_odds={"欧赔": {"final": {"home": 1.56, "draw": 6.18, "away": 8.12}}},
            over_under={"line": 3.0, "over": 0.24935, "under": 0.75065},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "away", "favored_side": "home", "score": 14.22},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("deep_soft_extreme_under_rebound_override", diag["signals"])
        self.assertIn("deep_soft_extreme_under_rebound_redirect_draw_to_home", diag["signals"])
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_rebounds_very_deep_extreme_under_false_draw_without_upset_signal_at_high_line(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3324718684, "draw": 0.4265236835, "away_win": 0.2410044481},
            current_odds={"欧赔": {"final": {"home": 1.66, "draw": 5.8675, "away": 8.075}}},
            over_under={"line": 3.5, "over": 0.2085, "under": 0.7915},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": False, "pressure_side": "away", "favored_side": "home", "score": 0.0},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_very_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("deep_soft_extreme_under_rebound_override", diag["signals"])
        self.assertIn("deep_soft_extreme_under_rebound_redirect_draw_to_home", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_rebounds_very_deep_extreme_under_with_away_pressure_at_high_line(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3684906423, "draw": 0.4173145866, "away_win": 0.2141947710},
            current_odds={"欧赔": {"final": {"home": 1.42, "draw": 8.7324, "away": 9.8571}}},
            over_under={"line": 4.75, "over": 0.27, "under": 0.73},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "high"}}},
            },
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"available": True, "supports_upset": True, "pressure_side": "away", "favored_side": "home", "score": 14.22},
                "three_layer_context": {
                    "handicap_depth_bucket": "level_very_deep",
                    "euro_support_bucket": "draw_soft",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("very_deep_extreme_under_pressure_rebound_override", diag["signals"])
        self.assertIn("very_deep_extreme_under_pressure_draw_excess_boost", diag["signals"])
        self.assertIn("very_deep_extreme_under_pressure_redirect_draw_to_home", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_rebounds_volatility_only_false_draw_to_home(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.349, "draw": 0.361, "away_win": 0.29},
            current_odds={"欧赔": {"final": {"home": 2.22, "draw": 3.92, "away": 3.05}}},
            over_under={"line": 3.5, "over": 0.29, "under": 0.71},
            match_intelligence={
                "scenario_tags": ["recent_form_volatility_high"],
                "contextual_rules": {"volatility": {"home": {"label": "medium"}, "away": {"label": "medium"}}},
            },
            review_outcome_diag={
                "signals": ["review-bias-config-floor"],
                "motivation_risk": {
                    "available": True,
                    "supports_upset": False,
                    "pressure_side": "away",
                    "favored_side": "home",
                    "score": 0.0,
                },
                "applied_shift": {"draw_shift": 0.012, "away_shift": 0.022, "home_shift": 0.0},
                "three_layer_context": {
                    "handicap_depth_bucket": "unknown",
                    "euro_support_bucket": "market_opposes",
                },
            },
        )
        self.assertTrue(diag["applied"])
        self.assertIn("volatility_only_home_rebound_override", diag["signals"])
        self.assertIn("volatility_only_home_rebound_redirect_draw_to_home", diag["signals"])
        self.assertNotIn("away_upset_redirect_draw_to_away", diag["signals"])
        self.assertEqual(diag["favored_side"], "home_win")
        self.assertGreater(adjusted["home_win"], adjusted["draw"])

    def test_draw_confirmation_guard_skips_away_upset_redirect_for_deep_handicap_draw(self):
        adjusted, diag = InferencePipelineService._apply_draw_confirmation_guard(
            final_prob={"home_win": 0.3345271216, "draw": 0.4089065553, "away_win": 0.2565663231},
            current_odds={"欧赔": {"final": {"home": 1.66, "draw": 4.988, "away": 4.79}}},
            over_under={"line": 3.0, "over": 0.46, "under": 0.54},
            match_intelligence={"scenario_tags": []},
            review_outcome_diag={
                "signals": [],
                "motivation_risk": {"supports_upset": True, "pressure_side": "home", "favored_side": "away", "score": 14.22},
                "applied_shift": {"draw_shift": 0.0, "away_shift": 0.0},
                "three_layer_context": {"handicap_depth_bucket": "level_very_deep"},
            },
        )
        self.assertFalse(diag["applied"])
        self.assertTrue(diag["qualified"])
        self.assertNotIn("away_upset_draw_confirmation_override", diag["signals"])
        self.assertEqual(adjusted["draw"], 0.4089065553)


if __name__ == "__main__":
    unittest.main()
