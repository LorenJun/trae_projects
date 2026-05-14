import json
import tempfile
import unittest
from unittest.mock import patch

from domain.inference import InferencePipelineService
from domain.intelligence import MatchIntelligenceEngine
from domain.odds import HistoricalOddsReference, build_market_context
from domain.postprocess import PredictionPostprocessService
from domain.rag import HybridRAGService
from domain.review_learning import PredictionReviewLearningService
from domain.writeback import build_prediction_note


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

    def test_apply_review_outcome_adjustment_keeps_marginal_home_lead_from_overcorrecting(self):
        adjusted, diag = self.service.apply_review_outcome_adjustment(
            final_probabilities={"home_win": 0.429085, "draw": 0.327522, "away_win": 0.243393},
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
