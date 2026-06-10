"""模块说明：负责核心推理链、盘口校准、联赛学习与概率融合。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from domain.constants import resolve_home_advantage, resolve_rho
from domain.odds import resolve_over_under_line
from models import DixonColesModel


class InferencePipelineService:
    REAL_OU_LINE_SOURCES = {'snapshot_final', 'snapshot_initial'}

    def __init__(
        self,
        *,
        league_config: Dict[str, Dict[str, Any]],
        team_manager: Any,
        match_intelligence_engine: Any,
        odds_reference: Any,
        upset_analyzer: Any,
        model_fusion: Any,
        poisson_model: Any,
        weight_adjuster: Any,
        league_ou_learning: Any,
        postprocess_service: Any,
        rating_service: Any = None,
    ):
        self.league_config = league_config
        self.team_manager = team_manager
        self.match_intelligence_engine = match_intelligence_engine
        self.odds_reference = odds_reference
        self.upset_analyzer = upset_analyzer
        self.model_fusion = model_fusion
        self.poisson_model = poisson_model
        self.weight_adjuster = weight_adjuster
        self.league_ou_learning = league_ou_learning
        self.postprocess_service = postprocess_service
        self.rating_service = rating_service

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            s = str(value).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    @staticmethod
    def _clamp_probability(value: float, lower: float = 0.0, upper: float = 0.99) -> float:
        return max(lower, min(upper, float(value)))

    @classmethod
    def _calibrate_confidence_with_league_learning(
        cls,
        confidence: float,
        applied_weights: Optional[Dict[str, Any]],
    ) -> Tuple[float, Dict[str, Any]]:
        raw_confidence = cls._clamp_probability(cls._to_float(confidence) or 0.0)
        diag: Dict[str, Any] = {
            'applied': False,
            'reason': 'missing_applied_weights',
            'base_confidence': round(raw_confidence, 4),
            'adjusted_confidence': round(raw_confidence, 4),
            'confidence_adjustment': 0.0,
            'league_weight_factor': 1.0,
            'league_total_predictions': 0,
            'weight_reason': '',
        }
        if not isinstance(applied_weights, dict) or not applied_weights:
            return raw_confidence, diag

        league_weight_factor = cls._to_float(applied_weights.get('league_weight_factor'))
        if league_weight_factor is not None:
            diag['league_weight_factor'] = round(league_weight_factor, 4)
        diag['league_total_predictions'] = int(applied_weights.get('league_total_predictions', 0) or 0)
        diag['weight_reason'] = str(applied_weights.get('weight_reason') or '').strip()

        confidence_adjustment = cls._to_float(applied_weights.get('confidence_adjustment'))
        if confidence_adjustment is None:
            diag['reason'] = 'missing_confidence_adjustment'
            return raw_confidence, diag

        confidence_adjustment = cls._clamp_probability(confidence_adjustment, -0.08, 0.08)
        diag['confidence_adjustment'] = round(confidence_adjustment, 4)
        if abs(confidence_adjustment) < 0.0005:
            diag['reason'] = 'neutral_confidence_adjustment'
            return raw_confidence, diag

        adjusted_confidence = cls._clamp_probability(raw_confidence + confidence_adjustment)
        diag['adjusted_confidence'] = round(adjusted_confidence, 4)
        diag['applied'] = True
        diag['reason'] = 'league_confidence_boost' if confidence_adjustment > 0 else 'league_confidence_trim'
        if adjusted_confidence in (0.0, 0.99):
            diag['reason'] += '_clamped'
        return adjusted_confidence, diag

    @staticmethod
    def _rerank_scores_for_under_three(
        score_probs: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        limit: int = 8,
    ) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
        ranked = sorted(
            ((str(score), float(prob or 0.0)) for score, prob in (score_probs or {}).items()),
            key=lambda item: item[1],
            reverse=True,
        )
        target_limit = max(3, int(limit or 8))
        diag: Dict[str, Any] = {
            'applied': False,
            'reason': 'guard_not_triggered',
            'line': None,
            'over': None,
            'under': None,
            'penalties': {},
            'target_limit': target_limit,
        }
        if not ranked or not isinstance(over_under, dict):
            diag['reason'] = 'missing_score_probs_or_over_under'
            return ranked[: target_limit], diag

        line = InferencePipelineService._to_float(over_under.get('line'))
        over_prob = InferencePipelineService._to_float(over_under.get('over'))
        under_prob = InferencePipelineService._to_float(over_under.get('under'))
        diag['line'] = line
        diag['over'] = over_prob
        diag['under'] = under_prob
        if line is None or over_prob is None or under_prob is None:
            diag['reason'] = 'invalid_over_under_payload'
            return ranked[: target_limit], diag
        if line > 3.0 or under_prob <= over_prob:
            diag['reason'] = 'not_under_three'
            return ranked[: target_limit], diag

        league_learning = over_under.get('league_learning') if isinstance(over_under.get('league_learning'), dict) else {}
        market_learning_case = False
        market_payload = over_under.get('market') if isinstance(over_under.get('market'), dict) else {}
        market_initial = market_payload.get('initial') if isinstance(market_payload.get('initial'), dict) else {}
        market_final = market_payload.get('final') if isinstance(market_payload.get('final'), dict) else {}
        initial_line = InferencePipelineService._to_float(market_initial.get('line'))
        final_line = InferencePipelineService._to_float(market_final.get('line'))
        if (
            line <= 2.5
            and float(under_prob - over_prob) <= 0.1
            and float(league_learning.get('recent_avg_goals') or 0.0) >= 2.85
            and float(league_learning.get('over25_rate') or 0.0) >= 0.62
            and float(league_learning.get('over35_rate') or 0.0) >= 0.3
            and float(league_learning.get('btts_rate') or 0.0) >= 0.58
            and initial_line is not None
            and final_line is not None
            and initial_line >= 3.25
            and final_line <= 2.5
            and (initial_line - final_line) >= 0.5
        ):
            market_learning_case = True
            target_limit = max(target_limit, 20)
            diag['target_limit'] = target_limit
            diag['tail_retention'] = 'open_learning'
            diag['market_line_drop'] = round(initial_line - final_line, 4)

        if line <= 2.5:
            factors = {'3-1': 0.64, '2-2': 0.74}
            diag['line_bucket'] = '<=2.5'
        elif line <= 2.75:
            factors = {'3-1': 0.72, '2-2': 0.8}
            diag['line_bucket'] = '<=2.75'
        else:
            factors = {'3-1': 0.72, '2-2': 0.86}
            diag['line_bucket'] = '<=3.0'
        adjusted = dict(ranked)
        penalties: Dict[str, float] = {}
        diag['factors'] = factors
        for score, factor in factors.items():
            if score not in adjusted:
                continue
            original = float(adjusted[score])
            adjusted[score] = original * factor
            penalties[score] = round(original - adjusted[score], 6)

        reranked = sorted(adjusted.items(), key=lambda item: item[1], reverse=True)
        if penalties:
            diag['applied'] = True
            diag['reason'] = 'under_three_score_penalty'
            diag['signals'] = ['under3-score-consistency-guard']
            if diag.get('tail_retention') == 'open_learning':
                diag['signals'].append('under3-open-learning-tail-retention')
            diag['penalties'] = penalties
        else:
            diag['reason'] = 'target_scores_missing'
            if diag.get('tail_retention') == 'open_learning':
                diag['signals'] = ['under3-open-learning-tail-retention']
        return reranked[: target_limit], diag

    @staticmethod
    def _apply_draw_confirmation_guard(
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
        review_outcome_diag: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {
            'applied': False,
            'qualified': False,
            'reason': 'not_draw_top1',
            'signals': [],
            'evidence': [],
        }
        if not isinstance(final_prob, dict):
            diag['reason'] = 'invalid_final_prob'
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        top_side = max((('home_win', p_h), ('draw', p_d), ('away_win', p_a)), key=lambda item: item[1])[0]
        if top_side != 'draw':
            return final_prob, diag

        diag['reason'] = 'draw_confirmation_evaluated'
        sorted_probs = sorted((p_h, p_a), reverse=True)
        runner_up = sorted_probs[0] if sorted_probs else 0.0
        top_gap = p_d - runner_up
        diag['top_gap'] = round(top_gap, 6)
        if top_gap >= 0.045:
            diag['qualified'] = True
            diag['evidence'].append('draw_prob_clear_lead')
        elif top_gap <= 0.018:
            diag['signals'].append('draw_confirmation_gap_weak')

        euro_final = None
        if isinstance(current_odds, dict):
            euro = current_odds.get('胜平负赔率') or current_odds.get('欧赔')
            if isinstance(euro, dict) and isinstance(euro.get('final'), dict):
                euro_final = euro.get('final')
        draw_odds = home_odds = away_odds = None
        if isinstance(euro_final, dict):
            draw_odds = InferencePipelineService._to_float(euro_final.get('draw') if 'draw' in euro_final else euro_final.get('平'))
            home_odds = InferencePipelineService._to_float(euro_final.get('home') if 'home' in euro_final else euro_final.get('主'))
            away_odds = InferencePipelineService._to_float(euro_final.get('away') if 'away' in euro_final else euro_final.get('客'))
        if draw_odds is not None and home_odds is not None and away_odds is not None:
            market_min = min(home_odds, draw_odds, away_odds)
            draw_market_gap = draw_odds - market_min
            diag['draw_market_gap'] = round(draw_market_gap, 4)
            if draw_market_gap <= 0.18:
                diag['qualified'] = True
                diag['evidence'].append('draw_market_supported')
            elif draw_market_gap >= 0.38:
                diag['signals'].append('draw_market_not_confirmed')

        ou_line = InferencePipelineService._to_float(over_under.get('line')) if isinstance(over_under, dict) else None
        ou_over = InferencePipelineService._to_float(over_under.get('over')) if isinstance(over_under, dict) else None
        ou_under = InferencePipelineService._to_float(over_under.get('under')) if isinstance(over_under, dict) else None
        if ou_line is not None:
            diag['ou_line'] = ou_line
        if ou_under is not None and ou_over is not None:
            ou_under_edge = ou_under - ou_over
            diag['ou_under_edge'] = round(ou_under_edge, 4)
            if ou_line is not None and ou_line <= 2.5 and ou_under > ou_over:
                diag['qualified'] = True
                diag['evidence'].append('under_supports_draw')
            elif ou_under <= ou_over and ou_line is not None and ou_line >= 2.75:
                diag['signals'].append('open_total_not_support_draw')
            if (
                ou_line is not None
                and ou_line >= 3.0
                and ou_under_edge >= 0.45
                and 'draw_market_not_confirmed' in diag['signals']
            ):
                diag['signals'].append('extreme_under_not_draw_confirmed')

        scenario_tags = match_intelligence.get('scenario_tags', []) if isinstance(match_intelligence, dict) else []
        contextual_rules = match_intelligence.get('contextual_rules', {}) if isinstance(match_intelligence, dict) else {}
        review_diag = review_outcome_diag if isinstance(review_outcome_diag, dict) else {}
        review_signals = set(str(item).strip() for item in (review_diag.get('signals') or []) if str(item).strip())
        review_motivation = review_diag.get('motivation_risk') if isinstance(review_diag.get('motivation_risk'), dict) else {}
        review_home_bias_gate = review_diag.get('home_bias_gate') if isinstance(review_diag.get('home_bias_gate'), dict) else {}
        review_evidence = set(str(item).strip() for item in (review_home_bias_gate.get('evidence') or []) if str(item).strip())
        if 'recent_form_volatility_high' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('double_volatility_supports_draw')
        if 'la_liga_mid_table_home_flat' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('la_liga_mid_table_home_flat')
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags:
            diag['signals'].append('relegation_home_motivation_conflicts_draw')
        if 'review-fragile-home-favorite-correction' in review_signals:
            diag['signals'].append('fragile_home_outcome_review_present')
        if review_motivation.get('supports_upset') and str(review_motivation.get('pressure_side') or '').strip() == 'away':
            diag['signals'].append('away_pressure_review_signal')
        volatility = contextual_rules.get('volatility') if isinstance(contextual_rules, dict) else {}
        if isinstance(volatility, dict):
            diag['volatility'] = {
                'home': (volatility.get('home') or {}).get('label'),
                'away': (volatility.get('away') or {}).get('label'),
            }

        review_favored_side = str(review_motivation.get('favored_side') or '').strip()
        review_score = float(review_motivation.get('score') or 0.0) if isinstance(review_motivation, dict) else 0.0
        review_applied_shift = review_diag.get('applied_shift') if isinstance(review_diag.get('applied_shift'), dict) else {}
        away_review_shift = float(review_applied_shift.get('away_shift') or 0.0)
        review_three_layer_context = review_diag.get('three_layer_context') if isinstance(review_diag.get('three_layer_context'), dict) else {}
        review_handicap_depth_bucket = str(review_three_layer_context.get('handicap_depth_bucket') or '').strip()
        review_euro_support_bucket = str(review_three_layer_context.get('euro_support_bucket') or '').strip()
        narrow_away_bump_present = 'review-narrow-away-bump' in review_signals
        away_upset_bucket_ok = bool(
            not review_handicap_depth_bucket
            or review_handicap_depth_bucket in {'level_ball', 'level_shallow', 'level_medium', 'unknown'}
            or away_review_shift >= 0.02
            or narrow_away_bump_present
        )
        soft_draw_context_evidence = {'la_liga_mid_table_home_flat'}
        draw_support_evidence = {
            'draw_market_supported',
            'under_supports_draw',
            'double_volatility_supports_draw',
        }.union(soft_draw_context_evidence)
        home_rebound_blocking_evidence = draw_support_evidence.difference(soft_draw_context_evidence)
        soft_draw_support_evidence = {
            'under_supports_draw',
            'double_volatility_supports_draw',
        }
        extreme_under_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and ou_line is not None
            and ou_line <= 3.25
            and top_gap >= 0.06
            and top_gap <= 0.12
            and p_h >= 0.32
            and (p_h - p_a) >= 0.10
        )
        deep_soft_extreme_under_rebound = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and ou_line is not None
            and (
                ou_line <= 3.25
                or (
                    review_handicap_depth_bucket == 'level_very_deep'
                    and not bool(review_motivation.get('supports_upset'))
                    and ou_line <= 3.5
                )
            )
            and top_gap >= 0.08
            and top_gap <= 0.13
            and p_h >= 0.305
            and (p_h - p_a) >= 0.045
            and (
                not bool(review_motivation.get('supports_upset'))
                or (
                    review_handicap_depth_bucket == 'level_very_deep'
                    and ou_line >= 3.25
                    and top_gap >= 0.12
                    and review_favored_side == 'home'
                    and str(review_motivation.get('pressure_side') or '').strip() == 'away'
                    and review_score >= 14.0
                )
                or (
                    review_handicap_depth_bucket == 'level_deep'
                    and ou_line <= 3.0
                    and top_gap >= 0.12
                    and review_favored_side == 'home'
                    and str(review_motivation.get('pressure_side') or '').strip() == 'away'
                    and review_score >= 14.0
                )
            )
        )
        very_deep_extreme_under_pressure_rebound = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'away_pressure_review_signal' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket == 'level_very_deep'
            and review_euro_support_bucket == 'draw_soft'
            and ou_line is not None
            and ou_line >= 4.25
            and ou_line <= 5.0
            and ou_under_edge >= 0.44
            and top_gap >= 0.045
            and top_gap <= 0.07
            and p_h >= 0.35
            and (p_h - p_a) >= 0.12
            and bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and str(review_motivation.get('pressure_side') or '').strip() == 'away'
            and review_score >= 14.0
            and home_odds is not None
            and home_odds <= 1.5
            and away_odds is not None
            and away_odds >= 6.0
        )
        fragile_home_strong_support_rebound = bool(
            'review-fragile-home-favorite-correction' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and review_favored_side == 'home'
            and str(review_motivation.get('pressure_side') or '').strip() == 'away'
            and review_score >= 12.0
            and 'away_motivation_pressure' in review_evidence
            and 'handicap_strength_mismatch' not in review_evidence
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
            and 'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and ou_line is not None
            and ou_line <= 3.0
            and top_gap >= 0.045
            and top_gap <= 0.065
            and p_h >= 0.33
            and p_h > p_a
            and (p_h - p_a) <= 0.06
        )
        fragile_home_unconfirmed_draw = bool(
            'review-fragile-home-favorite-correction' in review_signals
            and not fragile_home_strong_support_rebound
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
        )
        away_upset_unconfirmed_draw = bool(
            bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'away'
            and review_score >= 12.0
            and not draw_support_evidence.intersection(diag['evidence'])
            and away_upset_bucket_ok
            and ('draw_market_not_confirmed' in diag['signals'] or top_gap <= 0.13)
        )
        serie_a_soft_draw_away_rescue = bool(
            away_upset_bucket_ok
            and 'review-league-serie-a-draw-to-away-relief' in review_signals
            and (
                (
                    review_favored_side == 'away'
                    and review_score >= 14.0
                )
                or (
                    review_favored_side == 'home'
                    and review_score <= 3.5
                    and away_review_shift >= 0.04
                )
            )
            and 'draw_market_not_confirmed' in diag['signals']
            and set(diag['evidence']).issubset(soft_draw_support_evidence.union({'draw_prob_clear_lead'}))
            and top_gap <= 0.09
            and p_a >= 0.24
        )
        serie_a_soft_blocked_home_rebound = bool(
            'review-league-serie-a-soft-draw-away-blocked-home-top' in review_signals
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'draw_soft'
            and 'draw_market_not_confirmed' in diag['signals']
            and set(diag['evidence']).issubset(soft_draw_support_evidence)
            and ou_line is not None
            and ou_line <= 2.5
            and top_gap <= 0.04
            and p_h >= 0.35
            and (p_h - p_a) >= 0.11
            and home_odds is not None
            and home_odds <= 1.65
            and away_odds is not None
            and away_odds >= 5.0
        )
        serie_a_home_fragility_final_follow_through = bool(
            'review-league-serie-a-home-draw-guard-entry' in review_signals
            and 'review-league-serie-a-home-draw-guard-near-tie' in review_signals
            and review_favored_side == 'away'
            and review_score >= 14.0
            and away_review_shift >= 0.03
            and 'draw_market_not_confirmed' in diag['signals']
            and 'under_supports_draw' in diag['evidence']
            and top_gap <= 0.08
            and p_a >= 0.27
        )
        ligue1_home_edge_final_follow_through = bool(
            'review-league-ligue1-home-edge-trim' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and away_review_shift >= 0.05
            and float(review_applied_shift.get('draw_to_away_trim') or 0.0) >= 0.008
            and 'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and top_gap <= 0.05
            and p_d >= p_a
            and p_a >= 0.31
            and home_odds is not None
            and home_odds <= 1.8
            and draw_odds is not None
            and draw_odds >= 4.0
            and away_odds is not None
            and away_odds >= 4.0
        )
        serie_a_upset_knowledge_draw_override = bool(
            'review-league-serie-a-upset-knowledge-retry' in review_signals
            and 'review-league-serie-a-draw-to-away-relief' in review_signals
            and 'draw_market_not_confirmed' in diag['signals']
            and 'under_supports_draw' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket in {'strong_support', 'draw_soft'}
            and away_review_shift >= 0.04
            and top_gap <= 0.1
            and p_a >= 0.3
            and p_h <= 0.31
            and away_odds is not None
            and away_odds >= 5.0
            and home_odds is not None
            and home_odds <= 1.7
        )
        home_rebound_unconfirmed_draw = bool(
            not review_signals.intersection({'review-fragile-home-favorite-correction', 'review-narrow-away-bump'})
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_not_confirmed' in diag['signals']
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
            and top_gap <= 0.095
            and p_h >= 0.34
            and (p_h - p_a) >= 0.09
        )
        volatility_only_home_rebound = bool(
            not bool(review_motivation.get('supports_upset'))
            and set(diag['evidence']) == {'double_volatility_supports_draw'}
            and 'draw_market_not_confirmed' in diag['signals']
            and review_handicap_depth_bucket == 'unknown'
            and review_euro_support_bucket == 'market_opposes'
            and ou_line is not None
            and ou_line >= 3.25
            and ou_under_edge >= 0.35
            and top_gap <= 0.018
            and p_h >= 0.34
            and (p_h - p_a) >= 0.05
        )
        deep_soft_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'under_supports_draw' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and top_gap <= 0.12
            and p_h >= 0.32
            and (p_h - p_a) >= 0.10
        )
        deep_soft_extreme_under_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'under_supports_draw' not in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and top_gap >= 0.09
            and top_gap <= 0.125
            and p_h >= 0.32
            and (p_h - p_a) >= 0.07
        )
        market_opposed_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and not draw_support_evidence.intersection(diag['evidence'])
            and review_euro_support_bucket == 'market_opposes'
            and top_gap >= 0.045
            and top_gap <= 0.07
            and p_h >= 0.31
            and abs(p_h - p_a) <= 0.02
        )
        near_tie_draw_away_gap_max = 0.066 if narrow_away_bump_present and away_review_shift >= 0.03 else 0.038
        near_tie_home_gap_max = 0.07 if narrow_away_bump_present and away_review_shift >= 0.03 else 0.05
        near_tie_away_promotion = bool(
            away_upset_unconfirmed_draw
            and away_review_shift >= 0.02
            and (
                'draw_market_not_confirmed' in diag['signals']
                or narrow_away_bump_present
            )
            and 'under_supports_draw' not in diag['evidence']
            and abs(p_d - p_a) <= near_tie_draw_away_gap_max
            and abs(p_d - p_h) <= near_tie_home_gap_max
        )
        narrow_away_strong_redirect = bool(
            away_upset_unconfirmed_draw
            and narrow_away_bump_present
            and away_review_shift >= 0.03
            and 'under_supports_draw' not in diag['evidence']
            and top_gap <= 0.07
        )
        if (fragile_home_strong_support_rebound or fragile_home_unconfirmed_draw or away_upset_unconfirmed_draw or home_rebound_unconfirmed_draw or volatility_only_home_rebound or deep_soft_home_unconfirmed_draw or deep_soft_extreme_under_home_unconfirmed_draw or deep_soft_extreme_under_rebound or very_deep_extreme_under_pressure_rebound or market_opposed_home_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_soft_blocked_home_rebound or serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override or extreme_under_unconfirmed_draw) and diag['qualified']:
            removable_evidence = {'draw_prob_clear_lead'}
            if serie_a_soft_draw_away_rescue or serie_a_soft_blocked_home_rebound:
                removable_evidence = removable_evidence.union(soft_draw_support_evidence)
            if serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override:
                removable_evidence = removable_evidence.union({'under_supports_draw', 'double_volatility_supports_draw'})
            if deep_soft_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if deep_soft_extreme_under_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if volatility_only_home_rebound:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if home_rebound_unconfirmed_draw or fragile_home_strong_support_rebound or fragile_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union(soft_draw_context_evidence)
            if set(diag['evidence']).issubset(removable_evidence):
                diag['qualified'] = False
                if fragile_home_strong_support_rebound:
                    diag['signals'].append('fragile_home_strong_support_rebound_override')
                if volatility_only_home_rebound:
                    diag['signals'].append('volatility_only_home_rebound_override')
                if fragile_home_unconfirmed_draw:
                    diag['signals'].append('fragile_home_draw_confirmation_override')
                if away_upset_unconfirmed_draw:
                    diag['signals'].append('away_upset_draw_confirmation_override')
                if home_rebound_unconfirmed_draw:
                    diag['signals'].append('home_rebound_draw_confirmation_override')
                if deep_soft_home_unconfirmed_draw:
                    diag['signals'].append('deep_soft_home_draw_confirmation_override')
                if deep_soft_extreme_under_home_unconfirmed_draw:
                    diag['signals'].append('deep_soft_extreme_under_draw_confirmation_override')
                if deep_soft_extreme_under_rebound:
                    diag['signals'].append('deep_soft_extreme_under_rebound_override')
                if very_deep_extreme_under_pressure_rebound:
                    diag['signals'].append('very_deep_extreme_under_pressure_rebound_override')
                if market_opposed_home_unconfirmed_draw:
                    diag['signals'].append('market_opposed_home_draw_confirmation_override')
                if serie_a_soft_draw_away_rescue:
                    diag['signals'].append('serie_a_soft_draw_confirmation_override')
                if serie_a_soft_blocked_home_rebound:
                    diag['signals'].append('serie_a_soft_blocked_home_draw_confirmation_override')
                if serie_a_home_fragility_final_follow_through:
                    diag['signals'].append('serie_a_home_fragility_draw_confirmation_override')
                if serie_a_upset_knowledge_draw_override:
                    diag['signals'].append('serie_a_upset_knowledge_draw_confirmation_override')
                if extreme_under_unconfirmed_draw:
                    diag['signals'].append('extreme_under_draw_confirmation_override')

        la_liga_weak_gap_draw_retention = bool(
            'review-bias-config-floor' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and 'draw_confirmation_gap_weak' in diag['signals']
            and 'draw_market_not_confirmed' in diag['signals']
            and not diag['evidence']
            and ou_line is not None
            and ou_line <= 2.75
            and ou_under_edge is not None
            and ou_under_edge <= 0.3
            and top_gap <= 0.008
            and p_h >= 0.35
            and p_d >= p_h
            and str((diag.get('volatility') or {}).get('home') or '').strip() == 'low'
            and str((diag.get('volatility') or {}).get('away') or '').strip() == 'low'
        )
        if la_liga_weak_gap_draw_retention:
            diag['reason'] = 'la_liga_weak_gap_draw_retained'
            diag['signals'].append('la_liga_weak_gap_draw_retention')
            return final_prob, diag

        if diag['qualified']:
            diag['reason'] = 'draw_confirmation_passed'
            return final_prob, diag

        draw_excess = min(0.026, max(0.0, p_d - runner_up + 0.006))
        if deep_soft_extreme_under_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.07, draw_excess + 0.04)
            diag['signals'].append('deep_soft_extreme_under_draw_excess_boost')
        elif volatility_only_home_rebound and draw_excess > 0:
            diag['signals'].append('volatility_only_home_rebound_draw_excess_base')
        elif deep_soft_extreme_under_rebound and draw_excess > 0:
            draw_excess = min(0.07, draw_excess + 0.04)
            diag['signals'].append('deep_soft_extreme_under_rebound_draw_excess_boost')
        elif very_deep_extreme_under_pressure_rebound and draw_excess > 0:
            draw_excess = min(0.076, draw_excess + 0.046)
            diag['signals'].append('very_deep_extreme_under_pressure_draw_excess_boost')
        elif fragile_home_strong_support_rebound and draw_excess > 0:
            draw_excess = min(0.05, draw_excess + 0.016)
            diag['signals'].append('fragile_home_strong_support_draw_excess_boost')
        elif deep_soft_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.072, draw_excess + 0.044)
            diag['signals'].append('deep_soft_home_draw_excess_boost')
        elif market_opposed_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.052, draw_excess + 0.022)
            diag['signals'].append('market_opposed_home_draw_excess_boost')
        elif home_rebound_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.05, draw_excess + 0.022)
            diag['signals'].append('home_rebound_draw_excess_boost')
        elif serie_a_home_fragility_final_follow_through and draw_excess > 0:
            draw_excess = min(0.04, draw_excess + 0.014)
            diag['signals'].append('serie_a_home_fragility_draw_excess_boost')
        elif serie_a_upset_knowledge_draw_override and draw_excess > 0:
            draw_excess = min(0.084, draw_excess + 0.054)
            diag['signals'].append('serie_a_upset_knowledge_draw_excess_boost')
        if 'review-fragile-home-favorite-correction' in review_signals and not fragile_home_strong_support_rebound and draw_excess > 0:
            draw_excess = min(0.052, draw_excess + 0.014)
            diag['signals'].append('fragile_home_draw_excess_boost')
        elif away_upset_unconfirmed_draw and draw_excess > 0:
            boost = 0.012 if away_review_shift >= 0.02 else 0.01
            if near_tie_away_promotion:
                boost += 0.008
                diag['signals'].append('near_tie_away_promotion')
            if narrow_away_strong_redirect:
                boost += 0.01
                diag['signals'].append('narrow_away_strong_redirect_boost')
            draw_excess = min(0.052, draw_excess + boost)
            diag['signals'].append('away_upset_draw_excess_boost')
        elif serie_a_soft_draw_away_rescue and draw_excess > 0:
            if review_favored_side == 'home' and review_score <= 3.5 and away_review_shift >= 0.04:
                draw_excess = min(0.084, draw_excess + 0.054)
                diag['signals'].append('serie_a_soft_low_risk_draw_excess_boost')
            else:
                draw_excess = min(0.056, draw_excess + 0.016)
            diag['signals'].append('serie_a_soft_draw_excess_boost')
        if draw_excess <= 0.0:
            diag['reason'] = 'draw_confirmation_no_shift'
            return final_prob, diag
        favored_side = 'home_win' if p_h >= p_a else 'away_win'
        favored_ratio = 0.68 if favored_side == 'home_win' else 0.32
        if deep_soft_extreme_under_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.96
            diag['signals'].append('deep_soft_extreme_under_redirect_draw_to_home')
        elif volatility_only_home_rebound and favored_side == 'home_win':
            favored_ratio = 0.88
            diag['signals'].append('volatility_only_home_rebound_redirect_draw_to_home')
        elif deep_soft_extreme_under_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('deep_soft_extreme_under_rebound_redirect_draw_to_home')
        elif very_deep_extreme_under_pressure_rebound and favored_side == 'home_win':
            favored_ratio = 0.98
            diag['signals'].append('very_deep_extreme_under_pressure_redirect_draw_to_home')
        elif fragile_home_strong_support_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('fragile_home_strong_support_redirect_draw_to_home')
        elif deep_soft_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.98
            diag['signals'].append('deep_soft_redirect_draw_to_home')
        elif market_opposed_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.92
            diag['signals'].append('market_opposed_redirect_draw_to_home')
        elif home_rebound_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.9
            diag['signals'].append('home_rebound_redirect_draw_to_home')
        elif serie_a_soft_blocked_home_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('serie_a_soft_blocked_home_redirect_draw_to_home')
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags and favored_side == 'home_win':
            favored_ratio = max(favored_ratio, 0.78)
        if 'review-fragile-home-favorite-correction' in review_signals and not fragile_home_strong_support_rebound and not serie_a_upset_knowledge_draw_override:
            favored_side = 'away_win'
            favored_ratio = 0.78
            if 'away_motivation_pressure' in review_evidence or 'handicap_strength_mismatch' in review_evidence:
                favored_ratio = 0.86
            diag['signals'].append('fragile_home_redirect_draw_to_away')
        elif away_upset_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_home_fragility_final_follow_through or ligue1_home_edge_final_follow_through:
            favored_side = 'away_win'
            favored_ratio = 0.72
            if away_review_shift >= 0.02 or review_score >= 15.0:
                favored_ratio = 0.8
            if near_tie_away_promotion:
                favored_ratio = max(favored_ratio, 0.94 if away_review_shift >= 0.024 else 0.9)
                diag['signals'].append('near_tie_redirect_draw_to_away')
            if narrow_away_strong_redirect:
                favored_ratio = max(favored_ratio, 0.96)
                diag['signals'].append('narrow_away_strong_redirect')
            if serie_a_soft_draw_away_rescue:
                if review_favored_side == 'home' and review_score <= 3.5 and away_review_shift >= 0.04:
                    favored_ratio = max(favored_ratio, 1.0)
                    diag['signals'].append('serie_a_soft_low_risk_full_redirect')
                else:
                    favored_ratio = max(favored_ratio, 0.96)
                diag['signals'].append('serie_a_soft_redirect_draw_to_away')
            if serie_a_home_fragility_final_follow_through:
                favored_ratio = max(favored_ratio, 0.94)
                diag['signals'].append('serie_a_home_fragility_redirect_draw_to_away')
            if ligue1_home_edge_final_follow_through:
                favored_ratio = max(favored_ratio, 0.94)
                diag['signals'].append('ligue1_home_edge_redirect_draw_to_away')
            diag['signals'].append('away_upset_redirect_draw_to_away')
        elif serie_a_upset_knowledge_draw_override:
            favored_side = 'away_win'
            favored_ratio = 1.0
            diag['signals'].append('serie_a_upset_knowledge_redirect_draw_to_away')
        p_d -= draw_excess
        if favored_side == 'home_win':
            p_h += draw_excess * favored_ratio
            p_a += draw_excess * (1.0 - favored_ratio)
        else:
            p_a += draw_excess * favored_ratio
            p_h += draw_excess * (1.0 - favored_ratio)
        total = p_h + p_d + p_a
        if total > 0:
            p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
        if (
            (home_rebound_unconfirmed_draw or fragile_home_strong_support_rebound or volatility_only_home_rebound)
            and favored_side == 'home_win'
            and p_d >= p_h
            and (p_d - p_h) <= 0.002
        ):
            home_rebound_trim = min(0.0022, (p_d - p_h) + 0.0008, max(0.0, p_d - 0.02))
            if home_rebound_trim > 0:
                p_d -= home_rebound_trim
                p_h += home_rebound_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                if fragile_home_strong_support_rebound:
                    diag['signals'].append('fragile_home_strong_support_near_tie_trim')
                elif volatility_only_home_rebound:
                    diag['signals'].append('volatility_only_home_rebound_near_tie_trim')
                else:
                    diag['signals'].append('home_rebound_near_tie_trim')
        away_near_tie_trim_limit = 0.006 if serie_a_home_fragility_final_follow_through else (0.0045 if (narrow_away_strong_redirect or serie_a_soft_draw_away_rescue) else 0.0025)
        if (
            favored_side == 'away_win'
            and (away_upset_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override)
            and p_h >= p_a
            and (p_h - p_a) <= away_near_tie_trim_limit
        ):
            away_rebound_trim = min(0.0028, (p_h - p_a) + 0.0008, max(0.0, p_h - 0.02))
            if away_rebound_trim > 0:
                p_h -= away_rebound_trim
                p_a += away_rebound_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('away_upset_near_tie_trim')
                if serie_a_soft_draw_away_rescue:
                    diag['signals'].append('serie_a_soft_near_tie_trim')
                if serie_a_home_fragility_final_follow_through:
                    diag['signals'].append('serie_a_home_fragility_near_tie_trim')
        if (
            serie_a_soft_draw_away_rescue
            and review_favored_side == 'home'
            and review_score <= 3.5
            and away_review_shift >= 0.04
            and favored_side == 'away_win'
            and p_h >= p_a
            and (p_h - p_a) <= 0.007
        ):
            serie_a_soft_final_trim = min(0.0038, (p_h - p_a) + 0.0006, max(0.0, p_h - 0.02))
            if serie_a_soft_final_trim > 0:
                p_h -= serie_a_soft_final_trim
                p_a += serie_a_soft_final_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('serie_a_soft_final_margin_trim')
        if (
            serie_a_home_fragility_final_follow_through
            and favored_side == 'away_win'
            and p_h >= p_a
            and (p_h - p_a) <= 0.02
        ):
            serie_a_home_fragility_final_trim = min(0.0106, ((p_h - p_a) * 0.5) + 0.0009, max(0.0, p_h - 0.02))
            if serie_a_home_fragility_final_trim > 0:
                p_h -= serie_a_home_fragility_final_trim
                p_a += serie_a_home_fragility_final_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('serie_a_home_fragility_final_trim')
        diag.update(
            {
                'applied': True,
                'reason': 'draw_confirmation_failed_shifted',
                'shift': round(draw_excess, 4),
                'favored_side': favored_side,
                'adjusted_probabilities': {
                    'home_win': round(p_h, 6),
                    'draw': round(p_d, 6),
                    'away_win': round(p_a, 6),
                },
            }
        )
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    @staticmethod
    def _parse_handicap_value(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            numeric = float(value)
            return numeric if abs(numeric) > 1e-9 else 0.0
        raw = str(value).strip()
        if not raw:
            return None
        try:
            if '/' in raw and not any(ch in raw for ch in '球平受让'):
                parts = [float(item) for item in raw.split('/') if item]
                if parts:
                    return sum(parts) / len(parts)
            return float(raw)
        except Exception:
            pass
        mapping = {
            '平手': 0.0,
            '平手/半球': -0.25,
            '平/半': -0.25,
            '半球': -0.5,
            '半球/一球': -0.75,
            '半/一': -0.75,
            '一球': -1.0,
            '一球/球半': -1.25,
            '一/球半': -1.25,
            '球半': -1.5,
            '球半/两球': -1.75,
            '两球': -2.0,
            '两球/两球半': -2.25,
            '两球半': -2.5,
            '受让平手': 0.0,
            '受让平手/半球': 0.25,
            '受让平/半': 0.25,
            '受让半球': 0.5,
            '受让半球/一球': 0.75,
            '受让半/一': 0.75,
            '受让一球': 1.0,
            '受让一球/球半': 1.25,
            '受让一/球半': 1.25,
            '受让球半': 1.5,
            '受让球半/两球': 1.75,
            '受让两球': 2.0,
            '受让两球/两球半': 2.25,
            '受让两球半': 2.5,
        }
        return mapping.get(raw.replace(' ', ''))

    def _build_preliminary_upset_potential(
        self,
        *,
        home_team: str,
        away_team: str,
        league_code: str,
        strength_diff: float,
        predicted_outcome: str,
        asian_handicap: Optional[Dict[str, Any]],
        european_odds: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        preliminary: Dict[str, Any] = {
            'available': False,
            'motivation_risk': {},
            'handicap_strength_mismatch': {},
        }
        analyzer = self.upset_analyzer
        if analyzer is None:
            return preliminary
        try:
            if hasattr(analyzer, '_score_motivation_risk'):
                motivation_risk = analyzer._score_motivation_risk(
                    match_intelligence=match_intelligence,
                    strength_diff=strength_diff,
                    predicted_outcome=predicted_outcome,
                )
                if isinstance(motivation_risk, dict):
                    preliminary['motivation_risk'] = motivation_risk
        except Exception:
            preliminary['motivation_risk'] = {}
        try:
            if hasattr(analyzer, 'analyze_handicap_vs_strength'):
                mismatch = analyzer.analyze_handicap_vs_strength(
                    home_team=home_team,
                    away_team=away_team,
                    strength_diff=strength_diff,
                    asian_handicap=asian_handicap,
                    european_odds=european_odds,
                )
                if isinstance(mismatch, dict):
                    preliminary['handicap_strength_mismatch'] = mismatch
        except Exception:
            preliminary['handicap_strength_mismatch'] = {}
        preliminary['available'] = bool(
            (isinstance(preliminary.get('motivation_risk'), dict) and preliminary['motivation_risk'].get('available'))
            or (
                isinstance(preliminary.get('handicap_strength_mismatch'), dict)
                and preliminary['handicap_strength_mismatch'].get('mismatch_detected')
            )
        )
        return preliminary

    @staticmethod
    def _build_serie_a_upset_knowledge_retry_gate(
        *,
        final_prob: Dict[str, float],
        review_outcome_diag: Optional[Dict[str, Any]],
        draw_guard_diag: Optional[Dict[str, Any]],
        upset_potential: Optional[Dict[str, Any]],
        main_prediction: str,
        league_code: str,
    ) -> Dict[str, Any]:
        gate: Dict[str, Any] = {
            'eligible': False,
            'reason': 'not_serie_a',
            'knowledge_score': 0.0,
            'similar_cases_count': 0,
            'case_knowledge_available': False,
            'reverse_rate': 0.0,
            'cold_rate': 0.0,
            'draw_shift': 0.0,
            'away_shift': 0.0,
        }
        if league_code != 'serie_a':
            return gate
        if not isinstance(final_prob, dict):
            gate['reason'] = 'invalid_final_prob'
            return gate

        review_diag = review_outcome_diag if isinstance(review_outcome_diag, dict) else {}

        if main_prediction not in {'主胜', '平局'}:
            gate['reason'] = 'not_home_or_draw_top'
            return gate
        review_signals = {
            str(item).strip()
            for item in (review_diag.get('signals') or [])
            if str(item).strip()
        }
        if 'review-league-serie-a-soft-draw-away-blocked-home-top' not in review_signals:
            gate['reason'] = 'blocked_soft_draw_signal_missing'
            return gate

        draw_diag = draw_guard_diag if isinstance(draw_guard_diag, dict) else {}
        draw_guard_signals = {
            str(item).strip()
            for item in (draw_diag.get('signals') or [])
            if str(item).strip()
        }
        draw_guard_evidence = {
            str(item).strip()
            for item in (draw_diag.get('evidence') or [])
            if str(item).strip()
        }
        three_layer_context = review_diag.get('three_layer_context') if isinstance(review_diag.get('three_layer_context'), dict) else {}
        retryable_blocked_soft_draw = bool(
            'draw_market_not_confirmed' in draw_guard_signals
            and 'under_supports_draw' in draw_guard_evidence
            and str(three_layer_context.get('handicap_depth_bucket') or '').strip() == 'level_medium'
            and str(three_layer_context.get('euro_support_bucket') or '').strip() == 'draw_soft'
        )
        if (
            'serie_a_soft_blocked_home_redirect_draw_to_home' not in draw_guard_signals
            and not retryable_blocked_soft_draw
        ):
            gate['reason'] = 'blocked_home_rebound_missing'
            return gate

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        if p_d < 0.36 or p_a < 0.22 or (p_h - p_d) > 0.025:
            gate['reason'] = 'probability_shape_not_retryable'
            return gate

        knowledge = upset_potential if isinstance(upset_potential, dict) else {}
        risk_score_detail = knowledge.get('risk_score_detail') if isinstance(knowledge.get('risk_score_detail'), dict) else {}
        knowledge_score = float(risk_score_detail.get('knowledge_score') or 0.0)
        similar_cases_count = int(knowledge.get('similar_cases_count') or 0)
        case_knowledge = knowledge.get('case_knowledge') if isinstance(knowledge.get('case_knowledge'), dict) else {}
        case_knowledge_available = bool(case_knowledge.get('available'))
        historical_reference = knowledge.get('historical_odds_reference') if isinstance(knowledge.get('historical_odds_reference'), dict) else {}
        historical_summary = historical_reference.get('summary') if isinstance(historical_reference.get('summary'), dict) else {}
        result_rates = historical_summary.get('result_rates') if isinstance(historical_summary.get('result_rates'), dict) else {}
        reverse_rate = 1.0 - float(result_rates.get('主胜') or 0.0) if result_rates else 0.0
        cold_rate = float(historical_summary.get('cold_result_rate') or 0.0)
        gate.update(
            {
                'knowledge_score': round(knowledge_score, 4),
                'similar_cases_count': similar_cases_count,
                'case_knowledge_available': case_knowledge_available,
                'reverse_rate': round(reverse_rate, 4),
                'cold_rate': round(cold_rate, 4),
            }
        )
        if knowledge_score < 16.0 and not (similar_cases_count >= 1 and case_knowledge_available):
            gate['reason'] = 'knowledge_signal_weak'
            return gate
        if reverse_rate > 0 and reverse_rate < 0.55 and cold_rate < 0.35 and knowledge_score < 18.0:
            gate['reason'] = 'historical_reverse_rate_weak'
            return gate

        gate.update(
            {
                'eligible': True,
                'reason': 'eligible',
                'draw_shift': 0.012,
                'away_shift': 0.046,
            }
        )
        return gate

    def apply_dynamic_weights(self, league_code: str) -> Dict[str, Any]:
        try:
            diag = self.weight_adjuster.get_adjustment_diagnostics(league_code)
            weights = diag.get('final_weights')
            if weights and hasattr(self.model_fusion, 'set_model_weights'):
                self.model_fusion.set_model_weights(weights)
            return diag
        except Exception as exc:
            return {'league_code': league_code, 'error': str(exc)}

    def _market_implied_1x2(self, european_odds: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        """从欧赔 final 反推归一化市场隐含 1X2 概率；无有效赔率返回 None。"""
        if not isinstance(european_odds, dict):
            return None
        final = european_odds.get('final')
        if not isinstance(final, dict):
            return None
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            return None
        ph, pd, pa = 1.0 / oh, 1.0 / od, 1.0 / oa
        total = ph + pd + pa
        return {'home_win': ph / total, 'draw': pd / total, 'away_win': pa / total}

    def _resolve_market_alpha(
        self,
        market_probs: Optional[Dict[str, float]],
        applied_weights: Dict[str, Any],
        expert_signals: Dict[str, Any],
        strength_quality: str = 'real',
        odds_anomaly: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按实战口径动态选 α：常规 0.35 / 热门强队 0.25 / 小联赛-数据少 0.5 / 伤停换帅 0.65。

        无市场概率时 α=0（等价只用模型）。
        当球队强度无真实数据支撑（national_fallback / flat_default）时，模型硬数据可信度低，
        进一步提高市场权重，避免无数据场景预测趋同。

        热门强队的"防诱盘"降权只在盘口本身可疑时才触发：复用 detect_market_odds_anomaly 的
        可信度（level/trusted），区分"诱盘 vs 真实调整"——多公司一致、亚欧同向的强主/强客
        是真实信号，应继续信任市场而非压低市场权重；只有盘口异常（少公司/亚欧背离）才防诱盘。
        """
        if not isinstance(market_probs, dict):
            return {'alpha': 0.0, 'reason': 'no_market_probs', 'regime': 'model_only',
                    'strength_quality': strength_quality}

        alpha = getattr(self.model_fusion, 'DEFAULT_MARKET_ALPHA', 0.35)
        regime = 'normal'
        reason = '常规场景，模型为主市场为辅'

        # 小联赛 / 数据少：市场更可信
        has_enough = bool(applied_weights.get('has_enough_samples', False))
        if not has_enough:
            alpha = 0.5
            regime = 'data_poor'
            reason = '联赛样本不足/小联赛，提高市场权重'

        # 球队强度无真实球员数据支撑：模型硬数据不可信，市场主导
        if strength_quality in ('fallback', 'flat'):
            floor = 0.6 if strength_quality == 'flat' else 0.5
            if alpha < floor:
                alpha = floor
            if regime in ('normal', 'data_poor'):
                regime = 'no_strength_data'
                reason = '球队无真实球员数据（国家队兜底/默认值），提高市场权重防趋同'

        # 突发消息（核心伤停 / 多伤停 / 换帅）：市场资金已反映消息
        out_gap = abs(int(expert_signals.get('home_suspensions', 0)) + int(expert_signals.get('away_suspensions', 0)))
        heavy_absence = (
            expert_signals.get('home_key_available') is False
            or expert_signals.get('away_key_available') is False
            or bool(expert_signals.get('coach_change'))
            or out_gap >= 2
        )
        if heavy_absence:
            alpha = max(alpha, 0.65)
            regime = 'news_shock'
            reason = '核心伤停/停赛或换帅等突发消息，市场资金已反映'

        # 热门强队（市场强烈看好一方）：只有盘口本身可疑时才降权防诱盘。
        # fav_prob 用 max(home_win, away_win) 不分主客，强主/强客走同一规则。
        # 复用 detect_market_odds_anomaly 的可信度：多公司一致、亚欧同向（level=none/low）
        # 是真实强主/强客调整，应继续信市场；只有盘口异常（level=medium/high，少公司/亚欧
        # 背离）才认定诱盘并压低市场权重。注意 detect 里 trusted 仅在 level=='none' 时为 True，
        # 故判别只按 level（low 也属可信，不降权）。
        fav_prob = max(market_probs.get('home_win', 0.0), market_probs.get('away_win', 0.0))
        anomaly_level = (odds_anomaly or {}).get('level', 'none')
        odds_suspicious = anomaly_level in ('medium', 'high')
        if fav_prob >= 0.60 and regime in ('normal', 'data_poor'):
            if odds_suspicious:
                alpha = min(alpha, 0.25)
                regime = 'hot_favorite'
                reason = '市场强烈看好一方且盘口可疑，降低市场权重防诱盘'
            else:
                regime = 'hot_favorite_trusted'
                reason = '市场强烈看好一方但盘口可信（多公司一致），维持市场权重信任真实调整'

        return {'alpha': round(float(alpha), 4), 'reason': reason, 'regime': regime,
                'market_favorite_prob': round(float(fav_prob), 4),
                'odds_anomaly_level': anomaly_level,
                'strength_quality': strength_quality}

    def _build_expert_signals(
        self,
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
        home_motivation: float,
        away_motivation: float,
        match_intelligence: Optional[Dict[str, Any]],
        market_probs: Optional[Dict[str, float]],
    ) -> Dict[str, Any]:
        """组装 expert 模型的真实情报输入。"""
        signals: Dict[str, Any] = {
            'home_suspensions': int(home_strength.get('suspended_count', 0) or 0),
            'away_suspensions': int(away_strength.get('suspended_count', 0) or 0),
            'home_key_available': bool(home_strength.get('key_players_available', True)),
            'away_key_available': bool(away_strength.get('key_players_available', True)),
            'home_motivation': float(home_motivation),
            'away_motivation': float(away_motivation),
        }
        # 情报净倾向：用 quant_adjustment 的 λ 缩放差折算主队净利好（-1~1）
        if isinstance(match_intelligence, dict):
            quant = match_intelligence.get('quant_adjustment') or {}
            try:
                home_scale = float(quant.get('home_lambda_scale') or 1.0)
                away_scale = float(quant.get('away_lambda_scale') or 1.0)
                signals['intel_home_bias'] = max(-1.0, min(1.0, (home_scale - away_scale)))
            except (TypeError, ValueError):
                signals['intel_home_bias'] = 0.0
            if match_intelligence.get('coach_change') or match_intelligence.get('head_coach_change'):
                signals['coach_change'] = True
        # 市场热门方向
        if isinstance(market_probs, dict):
            ph = market_probs.get('home_win', 0.0)
            pa = market_probs.get('away_win', 0.0)
            if ph >= pa:
                signals['market_favorite'] = 'home'
                signals['market_favorite_strength'] = max(0.0, min(1.0, (ph - 0.4) / 0.4))
            else:
                signals['market_favorite'] = 'away'
                signals['market_favorite_strength'] = max(0.0, min(1.0, (pa - 0.4) / 0.4))
        return signals

    def detect_market_odds_anomaly(
        self,
        league_code: str,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        base_home_lambda: Optional[float] = None,
        base_away_lambda: Optional[float] = None,
    ) -> Dict[str, Any]:
        diag: Dict[str, Any] = {
            'available': False,
            'trusted': True,
            'score': 0.0,
            'level': 'none',
            'reasons': [],
            'signals': [],
            'calibration_weight': 1.0,
        }
        if not isinstance(european_odds, dict):
            diag['reason'] = 'missing_european_odds'
            return diag
        final = european_odds.get('final')
        if not isinstance(final, dict):
            diag['reason'] = 'missing_euro_final'
            return diag
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            diag['reason'] = 'invalid_euro_final'
            return diag

        diag['available'] = True
        ph = 1.0 / oh
        pd = 1.0 / od
        pa = 1.0 / oa
        total = ph + pd + pa
        ph, pd, pa = ph / total, pd / total, pa / total
        fav_side = 'home' if ph >= pa else 'away'
        fav_prob = ph if fav_side == 'home' else pa
        diag['market_implied_probs'] = {'home': round(ph, 4), 'draw': round(pd, 4), 'away': round(pa, 4)}
        diag['market_favorite'] = fav_side

        score = 0.0
        reasons: List[str] = []
        signals: List[str] = []

        company_mode = str(european_odds.get('company_mode') or '')
        consensus = european_odds.get('consensus') if isinstance(european_odds.get('consensus'), dict) else {}
        filtered_company_count = int(consensus.get('filtered_company_count') or 0)
        company_count = int(consensus.get('company_count') or 0)
        diag['company_mode'] = company_mode or 'unknown'
        diag['filtered_company_count'] = filtered_company_count
        diag['company_count'] = company_count

        if company_mode == 'average_row_fallback':
            score += 0.18
            reasons.append('欧赔仅拿到平均值回退')
            signals.append('average_row_fallback')
        elif filtered_company_count and filtered_company_count < 4:
            score += 0.12
            reasons.append(f'欧赔共识公司过少({filtered_company_count})')
            signals.append('low_company_count')

        if isinstance(asian_handicap, dict):
            fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
            ini = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}
            hcp_raw = (
                fin.get('handicap') if 'handicap' in fin else fin.get('handicap_value') if 'handicap_value' in fin else fin.get('盘口值') if '盘口值' in fin else fin.get('handicap_text') if 'handicap_text' in fin else fin.get('盘口')
            )
            hcp_final = self._parse_handicap_value(hcp_raw)
            hw_f = self._to_float(fin.get('home_water'))
            aw_f = self._to_float(fin.get('away_water'))
            hcp_initial = self._parse_handicap_value(
                ini.get('handicap') if 'handicap' in ini else ini.get('handicap_value') if 'handicap_value' in ini else ini.get('盘口值') if '盘口值' in ini else ini.get('handicap_text') if 'handicap_text' in ini else ini.get('盘口')
            )
            giver = None
            if hcp_final is not None:
                if hcp_final < -0.06:
                    giver = 'home'
                elif hcp_final > 0.06:
                    giver = 'away'
            diag['asian_final_handicap'] = hcp_final
            diag['asian_giver'] = giver
            strong_market = fav_prob >= 0.56
            very_strong_market = fav_prob >= 0.62
            hcp_mag = abs(hcp_final) if hcp_final is not None else None
            if strong_market and giver and giver != fav_side:
                score += 0.55
                reasons.append('欧赔强侧与亚值让步方向相反')
                signals.append('favorite_direction_mismatch')
            elif very_strong_market and (giver is None or hcp_mag is None or hcp_mag < 0.75):
                score += 0.42
                reasons.append('欧赔强侧明显但亚值让步不足')
                signals.append('favorite_depth_mismatch')
            elif strong_market and hcp_mag is not None and hcp_mag < 0.25:
                score += 0.22
                reasons.append('欧赔偏强但亚值接近平手')
                signals.append('near_level_handicap')
            if fav_side == 'home' and hw_f and hw_f >= 2.15 and very_strong_market:
                score += 0.18
                reasons.append('主强侧赔率低但主队终水偏高')
                signals.append('home_water_high_vs_low_odds')
            if fav_side == 'away' and aw_f and aw_f >= 2.15 and very_strong_market:
                score += 0.18
                reasons.append('客强侧赔率低但客队终水偏高')
                signals.append('away_water_high_vs_low_odds')
            if hcp_initial is not None and hcp_final is not None and abs(hcp_final) + 0.24 < abs(hcp_initial) and very_strong_market:
                score += 0.12
                reasons.append('亚值明显退盘但欧赔仍保持强侧')
                signals.append('retreat_vs_strong_odds')

        if base_home_lambda and base_away_lambda:
            dc = DixonColesModel(rho=resolve_rho(league_code))
            base_probs = dc.predict_with_dixon_coles(max(0.15, float(base_home_lambda)), max(0.15, float(base_away_lambda)))
            base_home = float(base_probs.get('home_win') or 0.0)
            base_away = float(base_probs.get('away_win') or 0.0)
            base_fav_side = 'home' if base_home >= base_away else 'away'
            base_fav_prob = base_home if base_fav_side == 'home' else base_away
            market_base_gap = fav_prob - base_fav_prob if fav_side == base_fav_side else fav_prob + base_fav_prob - 0.5
            diag['base_model_probs'] = {'home': round(base_home, 4), 'draw': round(float(base_probs.get('draw') or 0.0), 4), 'away': round(base_away, 4)}
            diag['base_model_favorite'] = base_fav_side
            if fav_side != base_fav_side and fav_prob >= 0.54:
                score += 0.28
                reasons.append('欧赔强侧与基础模型方向相反')
                signals.append('base_model_direction_mismatch')
            elif market_base_gap >= 0.16 and fav_prob >= 0.58:
                score += 0.22
                reasons.append('欧赔强度显著高于基础模型')
                signals.append('base_model_gap_large')

        level = 'none'
        weight = 1.0
        if score >= 0.72:
            level = 'high'
            weight = 0.0
        elif score >= 0.42:
            level = 'medium'
            weight = 0.35
        elif score >= 0.18:
            level = 'low'
            weight = 0.7
        diag['score'] = round(score, 4)
        diag['level'] = level
        diag['trusted'] = level == 'none'
        diag['reasons'] = reasons
        diag['signals'] = signals
        diag['calibration_weight'] = weight
        return diag

    def _resolve_home_advantage(self, league_code: str) -> float:
        """主场优势系数：联赛主客场制用 1.12；中立/弱主场赛事（友谊赛、世界杯、洲际杯赛）降低。

        友谊赛/国家队比赛常在中立或弱主场环境进行，固定 1.12 会让主队 λ 恒定虚高、
        比分恒为 X-1，因此这类赛事用接近中立的系数。
        """
        return resolve_home_advantage(league_code)

    def _resolve_historical_ratings(
        self,
        league_code: str,
        home_team: str,
        away_team: str,
    ) -> Optional[Dict[str, Dict[str, float]]]:
        """组装持久化的历史 ELO/Glicko 评分供模型融合使用。

        - 无 rating_service 或某队无历史记录时返回 None，让模型融合回退到 strength 派生值（SoT 零回归）；
        - 仅当两队都有历史评分时才下发，避免一队历史/一队派生的混合口径。
        """
        if self.rating_service is None:
            return None
        try:
            home_hist = self.rating_service.get_ratings(league_code, home_team)
            away_hist = self.rating_service.get_ratings(league_code, away_team)
        except Exception:
            return None
        if not home_hist or not away_hist:
            return None
        return {'home': home_hist, 'away': away_hist}

    def calibrate_lambdas_from_market(
        self,
        league_code: str,
        base_home_lambda: float,
        base_away_lambda: float,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, float, Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False}
        if not isinstance(european_odds, dict):
            return base_home_lambda, base_away_lambda, diag
        final = european_odds.get('final')
        if not isinstance(final, dict):
            return base_home_lambda, base_away_lambda, diag
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            return base_home_lambda, base_away_lambda, diag

        anomaly_diag = self.detect_market_odds_anomaly(
            league_code=league_code,
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            base_home_lambda=base_home_lambda,
            base_away_lambda=base_away_lambda,
        )
        diag['odds_anomaly'] = anomaly_diag
        weight = float(anomaly_diag.get('calibration_weight') or 1.0)
        if anomaly_diag.get('level') == 'high':
            diag.update({'applied': False, 'reason': 'market_odds_anomaly_high', 'kept_base_lambda': {'home': round(float(base_home_lambda), 3), 'away': round(float(base_away_lambda), 3), 'total': round(float(base_home_lambda + base_away_lambda), 3)}})
            return base_home_lambda, base_away_lambda, diag

        ph = 1.0 / oh
        pd = 1.0 / od
        pa = 1.0 / oa
        total = ph + pd + pa
        ph, pd, pa = ph / total, pd / total, pa / total

        dc = DixonColesModel(rho=resolve_rho(league_code))
        league_avg = float(self.league_config.get(league_code, {}).get('avg_goals') or 2.6)
        base_total = max(0.8, float(base_home_lambda) + float(base_away_lambda))
        best = None
        best_cost = 1e9
        total_min = max(1.2, league_avg - 0.8)
        total_max = min(3.8, league_avg + 0.8)
        for total_tick in range(int(total_min * 20), int(total_max * 20) + 1):
            total_goals = total_tick / 20.0
            for share_tick in range(20, 81, 2):
                share = share_tick / 100.0
                hl = max(0.15, total_goals * share)
                al = max(0.15, total_goals * (1 - share))
                probs = dc.predict_with_dixon_coles(hl, al)
                cost_prob = (probs['home_win'] - ph) ** 2 + (probs['draw'] - pd) ** 2 + (probs['away_win'] - pa) ** 2
                cost_base = 0.08 * ((hl - base_home_lambda) ** 2 + (al - base_away_lambda) ** 2)
                cost_total = 0.04 * ((total_goals - league_avg) ** 2 + (total_goals - base_total) ** 2)
                cost = cost_prob + cost_base + cost_total
                if cost < best_cost:
                    best_cost = cost
                    best = (hl, al, probs)
        if not best:
            return base_home_lambda, base_away_lambda, diag

        hl, al, probs = best
        if weight < 0.999:
            hl = float(base_home_lambda) * (1.0 - weight) + float(hl) * weight
            al = float(base_away_lambda) * (1.0 - weight) + float(al) * weight
            probs = dc.predict_with_dixon_coles(hl, al)
        diag = {
            'applied': True,
            'source': 'euro_final_1x2',
            'odds_final': {'home': oh, 'draw': od, 'away': oa},
            'implied_probs': {'home': round(ph, 4), 'draw': round(pd, 4), 'away': round(pa, 4)},
            'model_probs': {'home': round(float(probs['home_win']), 4), 'draw': round(float(probs['draw']), 4), 'away': round(float(probs['away_win']), 4)},
            'lambda_base': {'home': round(float(base_home_lambda), 3), 'away': round(float(base_away_lambda), 3), 'total': round(float(base_total), 3)},
            'lambda_calibrated': {'home': round(float(hl), 3), 'away': round(float(al), 3), 'total': round(float(hl + al), 3)},
            'cost': round(float(best_cost), 6),
            'odds_anomaly': anomaly_diag,
            'blend_weight': round(weight, 4),
        }
        return float(hl), float(al), diag

    def apply_league_ou_learning(
        self,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        home_lambda: float,
        away_lambda: float,
        strength_diff: float,
    ) -> Tuple[float, float, Dict[str, Any]]:
        learning = self.league_ou_learning.get_recent_learning(league_code=league_code, match_date=match_date)
        diag: Dict[str, Any] = {'applied': False, 'league_code': league_code, 'home_team': home_team, 'away_team': away_team, 'learning': learning}
        if not learning.get('available'):
            diag['reason'] = learning.get('reason', 'unavailable')
            return home_lambda, away_lambda, diag
        sample_size = int(learning.get('sample_size', 0) or 0)
        if sample_size < self.league_ou_learning.MIN_SAMPLE_SIZE:
            diag['reason'] = f'sample_size<{self.league_ou_learning.MIN_SAMPLE_SIZE}'
            return home_lambda, away_lambda, diag
        base_total = max(0.6, float(home_lambda) + float(away_lambda))
        league_avg = float(self.league_config.get(league_code, {}).get('avg_goals') or 2.6)
        recent_avg = float(learning.get('avg_goals') or league_avg)
        over25_rate = float(learning.get('over25_rate') or 0.0)
        over35_rate = float(learning.get('over35_rate') or 0.0)
        btts_rate = float(learning.get('btts_rate') or 0.0)
        clean_sheet_rate = float(learning.get('clean_sheet_rate') or 0.0)
        sample_weight = min(0.22, 0.08 + sample_size * 0.008)
        target_total = base_total * (1.0 - sample_weight) + recent_avg * sample_weight
        total_scale = 1.0
        signals: List[str] = []
        if over25_rate >= 0.60:
            total_scale += 0.04
            signals.append('high_over25')
        elif over25_rate <= 0.35:
            total_scale -= 0.04
            signals.append('low_over25')
        if over35_rate >= 0.30:
            total_scale += 0.02
            signals.append('high_over35')
        elif over35_rate <= 0.12:
            total_scale -= 0.02
            signals.append('low_over35')
        target_total *= total_scale
        target_total = max(0.8, min(4.2, target_total))
        current_share = float(home_lambda) / base_total if base_total > 0 else 0.5
        adjusted_share = current_share
        if btts_rate >= 0.65 and abs(strength_diff) <= 18:
            adjusted_share = 0.5 + (current_share - 0.5) * 0.88
            signals.append('high_btts_balance')
        elif clean_sheet_rate >= 0.55 and abs(strength_diff) >= 12:
            adjusted_share = 0.5 + (current_share - 0.5) * 1.08
            signals.append('high_clean_sheet_skew')
        adjusted_share = max(0.18, min(0.82, adjusted_share))
        new_home_lambda = max(0.15, target_total * adjusted_share)
        new_away_lambda = max(0.15, target_total * (1.0 - adjusted_share))
        diag.update({
            'applied': True,
            'signals': signals,
            'sample_size': sample_size,
            'base_lambda': {'home': round(float(home_lambda), 3), 'away': round(float(away_lambda), 3), 'total': round(base_total, 3)},
            'adjusted_lambda': {'home': round(float(new_home_lambda), 3), 'away': round(float(new_away_lambda), 3), 'total': round(float(new_home_lambda + new_away_lambda), 3)},
            'league_avg_goals': round(league_avg, 3),
            'recent_avg_goals': round(recent_avg, 3),
            'over25_rate': round(over25_rate, 4),
            'over35_rate': round(over35_rate, 4),
            'btts_rate': round(btts_rate, 4),
            'clean_sheet_rate': round(clean_sheet_rate, 4),
            'sample_weight': round(sample_weight, 4),
            'target_total_scale': round(total_scale, 4),
        })
        return new_home_lambda, new_away_lambda, diag

    def apply_live_outcome_adjustment(
        self,
        league_code: str,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'signals': [], 'delta': {}}
        if not isinstance(final_prob, dict) or not isinstance(current_odds, dict):
            return final_prob, diag

        def _boost_side(
            home_prob: float,
            draw_prob: float,
            away_prob: float,
            *,
            side: str,
            amount: float,
        ) -> Tuple[float, float, float, float]:
            if amount <= 0:
                return home_prob, draw_prob, away_prob, 0.0
            pools = {
                'home': max(0.0, home_prob - 0.05),
                'draw': max(0.0, draw_prob - 0.05),
                'away': max(0.0, away_prob - 0.05),
            }
            donors = [name for name in ('home', 'draw', 'away') if name != side and pools[name] > 0]
            available = sum(pools[name] for name in donors)
            take = min(amount, available)
            if take <= 0:
                return home_prob, draw_prob, away_prob, 0.0
            probs = {'home': home_prob, 'draw': draw_prob, 'away': away_prob}
            for donor in donors:
                share = take * (pools[donor] / available) if available > 0 else 0.0
                probs[donor] -= share
            probs[side] += take
            return probs['home'], probs['draw'], probs['away'], take

        def _pick(d: Dict[str, Any], *keys: str) -> Any:
            current: Any = d
            for key in keys:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            return current

        def _parse_euro_final(eu: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
            if not isinstance(eu, dict):
                return None, None, None
            final = eu.get('final')
            if isinstance(final, dict):
                return self._to_float(final.get('home')), self._to_float(final.get('draw')), self._to_float(final.get('away'))
            final = eu.get('最新指数')
            if isinstance(final, dict):
                return self._to_float(final.get('主')), self._to_float(final.get('平')), self._to_float(final.get('客'))
            return None, None, None

        def _parse_asian(asian: Any) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
            if not isinstance(asian, dict):
                return None, None, None, None, None, None
            initial = asian.get('initial')
            final = asian.get('final')
            if isinstance(initial, dict) and isinstance(final, dict):
                hcp_i = self._to_float(initial.get('handicap') if 'handicap' in initial else initial.get('盘口值'))
                hcp_f = self._to_float(final.get('handicap') if 'handicap' in final else final.get('盘口值'))
                hw_i = self._to_float(initial.get('home_water') if 'home_water' in initial else initial.get('主水'))
                aw_i = self._to_float(initial.get('away_water') if 'away_water' in initial else initial.get('客水'))
                hw_f = self._to_float(final.get('home_water') if 'home_water' in final else final.get('主水'))
                aw_f = self._to_float(final.get('away_water') if 'away_water' in final else final.get('客水'))
                return hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f
            return None, None, None, None, None, None

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
        original_probs = {'home_win': p_h, 'draw': p_d, 'away_win': p_a}

        euro = current_odds.get('欧赔')
        asian = current_odds.get('亚值')
        kelly = current_odds.get('凯利')
        oh, od, oa = _parse_euro_final(euro)
        hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f = _parse_asian(asian)
        kd = self._to_float(_pick(kelly, 'final', 'draw')) if isinstance(kelly, dict) else None

        fav_side = None
        fav_odds = None
        if isinstance(oh, float) and isinstance(oa, float):
            if oh <= oa:
                fav_side, fav_odds = 'home', oh
            else:
                fav_side, fav_odds = 'away', oa
        _ = od
        _ = league_code
        deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 0.75
        very_deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 1.0

        giver = None
        if isinstance(hcp_f, float):
            if hcp_f < -0.06:
                giver = 'home'
            elif hcp_f > 0.06:
                giver = 'away'
        retreat = isinstance(hcp_i, float) and isinstance(hcp_f, float) and abs(hcp_f) + 0.12 < abs(hcp_i)
        water_drift = False
        if fav_side == 'home' and isinstance(hw_i, float) and isinstance(hw_f, float) and (hw_f - hw_i) >= 0.04:
            water_drift = True
        if fav_side == 'away' and isinstance(aw_i, float) and isinstance(aw_f, float) and (aw_f - aw_i) >= 0.04:
            water_drift = True

        draw_boost = 0.0
        if isinstance(fav_odds, float) and fav_odds <= 1.60:
            draw_boost += 0.04
            diag['signals'].append('低赔强侧(<=1.60)')
        if deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag['signals'].append('深让>=0.75')
        if very_deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag['signals'].append('强让>=1.0')
        if retreat and giver == fav_side and very_deep_handicap:
            draw_boost += 0.03
            diag['signals'].append('强让退盘')
        if water_drift and giver == fav_side and very_deep_handicap:
            draw_boost += 0.02
            diag['signals'].append('强侧水位走高')
        if isinstance(kd, float) and kd <= 0.95:
            draw_boost += 0.01
            diag['signals'].append('平局凯利偏低')
        try:
            if isinstance(historical_odds_reference, dict):
                summary = historical_odds_reference.get('summary') or {}
                rates = summary.get('result_rates') or {}
                draw_rate = rates.get('平局')
                if isinstance(draw_rate, (int, float)) and draw_rate >= 0.33:
                    draw_boost += 0.02
                    diag['signals'].append('相似盘路平局率偏高')
        except Exception:
            pass

        market_alignment_diag: Dict[str, Any] = {}
        try:
            if isinstance(historical_odds_reference, dict):
                market_alignment = historical_odds_reference.get('market_alignment') or {}
                aligned_count = int(market_alignment.get('aligned_count') or 0)
                avg_alignment_score = float(market_alignment.get('avg_alignment_score') or 0.0)
                dominant_direction = str(market_alignment.get('dominant_direction') or 'balanced')
                same_psychology_count = int(market_alignment.get('same_psychology_count') or 0)
                same_capital_flow_count = int(market_alignment.get('same_capital_flow_count') or 0)
                same_totals_direction_count = int(market_alignment.get('same_totals_direction_count') or 0)
                side_boost = 0.0
                if dominant_direction in ('home', 'draw', 'away') and aligned_count >= 2 and avg_alignment_score >= 0.45:
                    side_boost += min(0.024, 0.008 + avg_alignment_score * 0.02)
                    if same_psychology_count >= 2:
                        side_boost += 0.006
                        diag['signals'].append('历史盘口操盘手法一致')
                    if same_capital_flow_count >= 2:
                        side_boost += 0.008
                        diag['signals'].append('历史盘口资金走向一致')
                    if same_totals_direction_count >= 2:
                        side_boost += 0.004
                        diag['signals'].append('历史盘口节奏方向一致')
                    if aligned_count >= 3:
                        side_boost += 0.004
                    side_boost = min(0.05, side_boost)
                    p_h, p_d, p_a, applied_take = _boost_side(
                        p_h,
                        p_d,
                        p_a,
                        side=dominant_direction,
                        amount=side_boost,
                    )
                    if applied_take > 0:
                        diag['signals'].append('历史盘口轨迹同向加权')
                        market_alignment_diag = {
                            'applied': True,
                            'dominant_direction': dominant_direction,
                            'aligned_count': aligned_count,
                            'avg_alignment_score': round(avg_alignment_score, 4),
                            'same_psychology_count': same_psychology_count,
                            'same_capital_flow_count': same_capital_flow_count,
                            'same_totals_direction_count': same_totals_direction_count,
                            'applied_boost': round(applied_take, 6),
                            'aligned_match_ids': market_alignment.get('aligned_match_ids') or [],
                        }
        except Exception:
            market_alignment_diag = {}

        draw_boost = min(0.10, max(0.0, draw_boost))
        if draw_boost <= 0:
            if market_alignment_diag.get('applied'):
                total2 = p_h + p_d + p_a
                if total2 > 0:
                    p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2
                diag['applied'] = True
                diag['delta'] = {
                    'home_win': round(p_h - original_probs['home_win'], 6),
                    'draw': round(p_d - original_probs['draw'], 6),
                    'away_win': round(p_a - original_probs['away_win'], 6),
                }
                diag['fav'] = {'side': fav_side, 'odds': fav_odds}
                diag['asian'] = {'handicap_initial': hcp_i, 'handicap_final': hcp_f, 'giver': giver, 'retreat': retreat, 'water_drift': water_drift}
                diag['historical_market_alignment'] = market_alignment_diag
                return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag
            return final_prob, diag
        if fav_side == 'home':
            take = min(draw_boost, max(0.0, p_h - 0.05))
            p_h -= take
            p_d += take
        elif fav_side == 'away':
            take = min(draw_boost, max(0.0, p_a - 0.05))
            p_a -= take
            p_d += take
        elif p_h >= p_a:
            take = min(draw_boost, max(0.0, p_h - 0.05))
            p_h -= take
            p_d += take
        else:
            take = min(draw_boost, max(0.0, p_a - 0.05))
            p_a -= take
            p_d += take

        total2 = p_h + p_d + p_a
        if total2 > 0:
            p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2

        diag['applied'] = True
        diag['delta'] = {
            'home_win': round(p_h - original_probs['home_win'], 6),
            'draw': round(p_d - original_probs['draw'], 6),
            'away_win': round(p_a - original_probs['away_win'], 6),
        }
        diag['fav'] = {'side': fav_side, 'odds': fav_odds}
        diag['asian'] = {'handicap_initial': hcp_i, 'handicap_final': hcp_f, 'giver': giver, 'retreat': retreat, 'water_drift': water_drift}
        if market_alignment_diag.get('applied'):
            diag['historical_market_alignment'] = market_alignment_diag
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    def apply_market_ou_calibration(
        self,
        *,
        home_lambda: float,
        away_lambda: float,
        current_odds: Optional[Dict[str, Any]],
    ) -> Tuple[float, float, Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'market_pressure_source': 'market_signal'}
        if not self.postprocess_service:
            diag['reason'] = 'missing_postprocess_service'
            return home_lambda, away_lambda, diag
        market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
        diag['market_signal'] = market_signal
        if not isinstance(market_signal, dict) or not market_signal.get('available'):
            diag['reason'] = 'market_signal_unavailable'
            return home_lambda, away_lambda, diag
        pace_shift = float(market_signal.get('pace_shift') or 0.0)
        if abs(pace_shift) < 1e-9:
            diag['reason'] = 'market_signal_flat'
            diag['market_pace_shift'] = 0.0
            return home_lambda, away_lambda, diag
        current_total = max(0.6, float(home_lambda) + float(away_lambda))
        target_total = max(0.8, min(4.2, current_total + pace_shift * 3.0))
        share = float(home_lambda) / current_total if current_total > 0 else 0.5
        new_home = max(0.15, target_total * share)
        new_away = max(0.15, target_total * (1.0 - share))
        diag.update(
            {
                'applied': True,
                'market_pace_shift': round(pace_shift, 6),
                'target_total': round(float(target_total), 6),
                'base_total': round(float(current_total), 6),
                'adjusted_lambda': {
                    'home': round(float(new_home), 6),
                    'away': round(float(new_away), 6),
                    'total': round(float(new_home + new_away), 6),
                },
            }
        )
        return new_home, new_away, diag

    def build_real_market_over_under(
        self,
        *,
        home_lambda: float,
        away_lambda: float,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        match_intelligence: Optional[Dict[str, Any]],
        realtime_context_applied: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str] = None,
        strength_diff: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        _ = (predicted_outcome, strength_diff)
        diag: Dict[str, Any] = {
            'available': False,
            'requires_real_market_line': True,
            'refined': {
                'market_lambda_applied': bool(isinstance(realtime_context_applied, dict) and isinstance(realtime_context_applied.get('market_ou_lambda_calibration'), dict) and realtime_context_applied.get('market_ou_lambda_calibration', {}).get('applied')),
                'reused_market_signal': False,
                'market_signal_shift': None,
            },
        }
        ou_line, ou_line_source = resolve_over_under_line(
            current_odds=current_odds,
            analysis_context=analysis_context,
            to_float=self._to_float,
        )
        diag['line_source'] = ou_line_source
        if ou_line_source not in self.REAL_OU_LINE_SOURCES or not isinstance(ou_line, float):
            diag['reason'] = 'missing_real_market_line'
            return {
                'available': False,
                'requires_real_market_line': True,
                'line_source': ou_line_source,
                'reason': 'missing_real_market_line',
            }, diag

        over_under = self.poisson_model.predict_over_under(home_lambda, away_lambda, line=ou_line)
        if not isinstance(over_under, dict):
            diag['reason'] = 'poisson_predict_over_under_failed'
            return {
                'available': False,
                'requires_real_market_line': True,
                'line_source': ou_line_source,
                'reason': 'poisson_predict_over_under_failed',
            }, diag

        if diag['refined']['market_lambda_applied'] and self.postprocess_service:
            market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
            diag['refined']['reused_market_signal'] = bool(market_signal.get('available'))
            diag['refined']['market_signal_shift'] = 0.0 if market_signal.get('available') else None
            if market_signal.get('available'):
                final_bias = float(market_signal.get('bias_final') or 0.0)
                if final_bias >= 0.03:
                    over_under['over'] = max(0.0, float(over_under.get('over') or 0.0) - min(0.03, final_bias * 0.12))
                    over_under['under'] = min(1.0, float(over_under.get('under') or 0.0) + min(0.03, final_bias * 0.12))
                elif final_bias <= -0.03:
                    shift = min(0.03, abs(final_bias) * 0.12)
                    over_under['over'] = min(1.0, float(over_under.get('over') or 0.0) + shift)
                    over_under['under'] = max(0.0, float(over_under.get('under') or 0.0) - shift)

        over_under, ou_resonance_diag = self.match_intelligence_engine._apply_market_resonance_to_over_under(
            over_under=over_under,
            match_intelligence=match_intelligence,
        )
        if isinstance(realtime_context_applied, dict):
            realtime_context_applied['market_resonance_over_under_adjustment'] = ou_resonance_diag
            learning_diag = realtime_context_applied.get('league_over_under_learning', {})
        else:
            learning_diag = {}

        over_under = self.postprocess_service.attach_over_under_context(
            over_under=over_under,
            current_odds=current_odds,
            learning_diag=learning_diag,
            ou_line_source=ou_line_source,
            realtime_context_applied=realtime_context_applied,
        )
        over_under['available'] = True
        over_under['requires_real_market_line'] = True
        over_under['used_real_market_line'] = True
        diag.update(
            {
                'available': True,
                'line': round(float(ou_line), 3),
                'line_source': ou_line_source,
            }
        )
        return over_under, diag

    def apply_real_totals_outcome_adjustment(
        self,
        *,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'source': 'real_market_over_under'}
        if not isinstance(final_prob, dict) or not isinstance(over_under, dict):
            diag['reason'] = 'missing_inputs'
            return final_prob, diag
        if not over_under.get('available'):
            diag['reason'] = str(over_under.get('reason') or 'over_under_unavailable')
            return final_prob, diag
        line_source = str(over_under.get('line_source') or '').strip()
        if line_source not in self.REAL_OU_LINE_SOURCES:
            diag['reason'] = f'unsupported_line_source:{line_source or "unknown"}'
            return final_prob, diag

        line = self._to_float(over_under.get('line'))
        over_prob = self._to_float(over_under.get('over'))
        under_prob = self._to_float(over_under.get('under'))
        if line is None or over_prob is None or under_prob is None:
            diag['reason'] = 'invalid_over_under_payload'
            return final_prob, diag

        final_totals = {}
        if isinstance(current_odds, dict):
            totals = current_odds.get('大小球')
            if isinstance(totals, dict) and isinstance(totals.get('final'), dict):
                final_totals = totals.get('final') or {}
        market_over_odds = self._to_float(final_totals.get('over'))
        market_under_odds = self._to_float(final_totals.get('under'))
        market_bias = 0.0
        if (
            isinstance(market_over_odds, float)
            and isinstance(market_under_odds, float)
            and market_over_odds > 1.01
            and market_under_odds > 1.01
        ):
            implied_over = 1.0 / market_over_odds
            implied_under = 1.0 / market_under_odds
            total_implied = implied_over + implied_under
            if total_implied > 0:
                market_bias = implied_under / total_implied - implied_over / total_implied

        model_bias = float(under_prob) - float(over_prob)
        combined_bias = model_bias * 0.65 + market_bias * 0.35
        if line <= 2.25:
            combined_bias += 0.025
        elif line <= 2.5 and combined_bias > 0:
            combined_bias += 0.01
        elif line >= 3.25 and combined_bias < 0:
            combined_bias -= 0.025
        elif line >= 3.0 and combined_bias < 0:
            combined_bias -= 0.01
        combined_bias = max(-0.18, min(0.18, combined_bias))

        draw_delta = max(-0.02, min(0.03, combined_bias * 0.18))
        if abs(draw_delta) < 0.004:
            diag['reason'] = 'bias_below_threshold'
            diag['line'] = round(float(line), 3)
            diag['combined_bias'] = round(float(combined_bias), 4)
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            diag['reason'] = 'invalid_probability_mass'
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

        side_mass = max(1e-9, p_h + p_a)
        home_share = p_h / side_mass
        away_share = p_a / side_mass

        if draw_delta > 0:
            available_take = max(0.0, p_h - 0.02) + max(0.0, p_a - 0.02)
            actual_delta = min(draw_delta, available_take)
            take_home = min(max(0.0, p_h - 0.02), actual_delta * home_share)
            take_away = min(max(0.0, p_a - 0.02), actual_delta - take_home)
            remainder = actual_delta - take_home - take_away
            if remainder > 1e-9:
                extra_home = min(max(0.0, p_h - 0.02) - take_home, remainder)
                take_home += extra_home
                remainder -= extra_home
            if remainder > 1e-9:
                extra_away = min(max(0.0, p_a - 0.02) - take_away, remainder)
                take_away += extra_away
            p_h -= take_home
            p_a -= take_away
            p_d += take_home + take_away
        else:
            actual_delta = min(-draw_delta, max(0.0, p_d - 0.02))
            p_d -= actual_delta
            p_h += actual_delta * home_share
            p_a += actual_delta * away_share
            actual_delta = -actual_delta

        total2 = p_h + p_d + p_a
        if total2 > 0:
            p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2

        diag.update(
            {
                'applied': True,
                'line': round(float(line), 3),
                'line_source': line_source,
                'model_bias': round(float(model_bias), 4),
                'market_bias': round(float(market_bias), 4),
                'combined_bias': round(float(combined_bias), 4),
                'draw_delta': round(float(actual_delta), 4),
                'effect': 'under_to_draw' if actual_delta > 0 else 'over_reduce_draw',
                'adjusted_probabilities': {
                    'home_win': round(float(p_h), 6),
                    'draw': round(float(p_d), 6),
                    'away_win': round(float(p_a), 6),
                },
            }
        )
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    def run(
        self,
        *,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        realtime: Dict[str, Any],
        review_learning: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        applied_weights = self.apply_dynamic_weights(league_code)
        home_strength = self.team_manager.analyze_team_strength(league_code, home_team)
        away_strength = self.team_manager.analyze_team_strength(league_code, away_team)
        match_intelligence = self.match_intelligence_engine._build_match_intelligence(
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            analysis_context=analysis_context,
            current_odds=current_odds,
            home_strength=home_strength,
            away_strength=away_strength,
        )
        realtime['context_applied']['match_intelligence'] = {
            'available': bool(match_intelligence.get('available')),
            'signals': match_intelligence.get('signals', []),
            'market_signals': (match_intelligence.get('market', {}) or {}).get('signals', []),
        }
        try:
            motivation = match_intelligence.get('motivation') if isinstance(match_intelligence, dict) else {}
            if 'home_motivation' not in analysis_context and isinstance(motivation, dict):
                suggested_scores = motivation.get('suggested_scores') or {}
                if suggested_scores.get('home') is not None:
                    analysis_context['home_motivation'] = float(suggested_scores['home'])
                if suggested_scores.get('away') is not None:
                    analysis_context['away_motivation'] = float(suggested_scores['away'])
            h2h_context = match_intelligence.get('head_to_head') if isinstance(match_intelligence, dict) else {}
            if isinstance(h2h_context, dict) and h2h_context.get('available'):
                analysis_context.setdefault('h2h_home_wins', int(h2h_context.get('home_wins', 0)))
                analysis_context.setdefault('h2h_away_wins', int(h2h_context.get('away_wins', 0)))
                analysis_context.setdefault('h2h_draws', int(h2h_context.get('draws', 0)))
        except Exception as exc:
            realtime['context_applied']['match_intelligence_bind_error'] = str(exc)

        strength_diff = home_strength['strength'] - away_strength['strength']
        # 强度数据质量：双方都有真实球员数据=real；任一退回兜底/默认=fallback/flat。
        sources = {home_strength.get('strength_source', 'real'), away_strength.get('strength_source', 'real')}
        if 'flat_default' in sources:
            strength_quality = 'flat'
        elif 'national_fallback' in sources:
            strength_quality = 'fallback'
        else:
            strength_quality = 'real'
        realtime['context_applied']['strength_quality'] = strength_quality
        asian_handicap = current_odds.get('亚值') if isinstance(current_odds, dict) else None
        european_odds = current_odds.get('欧赔') if isinstance(current_odds, dict) else None
        league_avg_goals = self.league_config[league_code]['avg_goals']
        home_form = int(analysis_context.get('home_form', 3))
        away_form = int(analysis_context.get('away_form', 3))
        home_motivation = float(analysis_context.get('home_motivation', 75))
        away_motivation = float(analysis_context.get('away_motivation', 75))
        realtime['context_applied'].update({'home_form': home_form, 'away_form': away_form, 'home_motivation': home_motivation, 'away_motivation': away_motivation})

        # league_avg_goals 是“全场总进球”均值，需除以 2 才是单队进球基准，
        # 否则两队 λ 各自≈整场总进球、合计翻倍，导致预期进球与大球概率系统性偏高。
        per_team_baseline = league_avg_goals / 2.0
        home_advantage = self._resolve_home_advantage(league_code)
        base_home_lambda = home_strength['attack'] * away_strength['defense'] * per_team_baseline * home_advantage
        base_away_lambda = away_strength['attack'] * home_strength['defense'] * per_team_baseline
        realtime['context_applied']['home_advantage_factor'] = round(float(home_advantage), 4)
        home_lambda = base_home_lambda
        away_lambda = base_away_lambda
        try:
            home_lambda, away_lambda, cal_diag = self.calibrate_lambdas_from_market(
                league_code=league_code,
                base_home_lambda=base_home_lambda,
                base_away_lambda=base_away_lambda,
                european_odds=european_odds,
                asian_handicap=asian_handicap,
            )
            realtime['context_applied']['lambda_calibration'] = cal_diag
        except Exception as exc:
            realtime['context_applied']['lambda_calibration'] = {'applied': False, 'error': str(exc)}
        try:
            home_lambda, away_lambda, market_ou_lambda_diag = self.apply_market_ou_calibration(
                home_lambda=home_lambda,
                away_lambda=away_lambda,
                current_odds=current_odds,
            )
            realtime['context_applied']['market_ou_lambda_calibration'] = market_ou_lambda_diag
        except Exception as exc:
            realtime['context_applied']['market_ou_lambda_calibration'] = {'applied': False, 'error': str(exc)}
        try:
            home_lambda, away_lambda, ou_learning_diag = self.apply_league_ou_learning(
                league_code=league_code,
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                home_lambda=home_lambda,
                away_lambda=away_lambda,
                strength_diff=strength_diff,
            )
            realtime['context_applied']['league_over_under_learning'] = ou_learning_diag
        except Exception as exc:
            realtime['context_applied']['league_over_under_learning'] = {'applied': False, 'error': str(exc)}
        try:
            quant_adjustment = match_intelligence.get('quant_adjustment') if isinstance(match_intelligence, dict) else {}
            if isinstance(quant_adjustment, dict):
                home_scale = float(quant_adjustment.get('home_lambda_scale') or 1.0)
                away_scale = float(quant_adjustment.get('away_lambda_scale') or 1.0)
                home_lambda = max(0.15, home_lambda * home_scale)
                away_lambda = max(0.15, away_lambda * away_scale)
                realtime['context_applied']['match_intelligence_lambda_scale'] = {'home_lambda_scale': home_scale, 'away_lambda_scale': away_scale}
        except Exception as exc:
            realtime['context_applied']['match_intelligence_lambda_scale'] = {'error': str(exc)}

        home_xg = home_lambda * 0.8
        away_xg = away_lambda * 0.8
        h2h_home_wins = int(analysis_context.get('h2h_home_wins', 0))
        h2h_away_wins = int(analysis_context.get('h2h_away_wins', 0))
        h2h_draws = int(analysis_context.get('h2h_draws', 0))
        realtime['context_applied'].update({'h2h_home_wins': h2h_home_wins, 'h2h_away_wins': h2h_away_wins, 'h2h_draws': h2h_draws})

        # 市场（赔率隐含）概率 + 动态 α + 专家情报信号 + 各模型历史准确率
        market_probs = self._market_implied_1x2(european_odds)
        expert_signals = self._build_expert_signals(
            home_strength=home_strength,
            away_strength=away_strength,
            home_motivation=home_motivation,
            away_motivation=away_motivation,
            match_intelligence=match_intelligence,
            market_probs=market_probs,
        )
        odds_anomaly_diag = (realtime.get('context_applied', {})
                             .get('lambda_calibration', {})
                             .get('odds_anomaly'))
        alpha_diag = self._resolve_market_alpha(market_probs, applied_weights, expert_signals, strength_quality, odds_anomaly_diag)
        model_accuracy = applied_weights.get('model_accuracy') if isinstance(applied_weights, dict) else None

        historical_ratings = self._resolve_historical_ratings(
            league_code, home_team, away_team
        )

        fusion_result = self.model_fusion.predict(
            home_team=home_team,
            away_team=away_team,
            home_strength=home_strength['strength'],
            away_strength=away_strength['strength'],
            home_form=home_form,
            away_form=away_form,
            home_injuries=home_strength['injured_count'],
            away_injuries=away_strength['injured_count'],
            h2h_home_wins=h2h_home_wins,
            h2h_away_wins=h2h_away_wins,
            h2h_draws=h2h_draws,
            home_motivation=home_motivation,
            away_motivation=away_motivation,
            home_xg=home_xg,
            away_xg=away_xg,
            home_attack=home_strength['attack'],
            home_defense=home_strength['defense'],
            away_attack=away_strength['attack'],
            away_defense=away_strength['defense'],
            market_probs=market_probs,
            market_alpha=alpha_diag.get('alpha'),
            expert_signals=expert_signals,
            model_accuracy=model_accuracy,
            historical_ratings=historical_ratings,
            per_team_baseline=per_team_baseline,
            home_advantage=home_advantage,
        )
        realtime['context_applied']['market_fusion'] = fusion_result.get('market_fusion')
        realtime['context_applied']['market_alpha'] = alpha_diag
        realtime['context_applied']['expert_signals'] = expert_signals
        final_prob = fusion_result['final']
        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)

        if isinstance(current_odds, dict) and current_odds:
            exclude_match_id = current_odds.get('match_id')
            historical_odds_reference = self.odds_reference.find_similar_matches(
                league_code=league_code,
                current_odds=current_odds,
                top_k=5,
                exclude_match_id=exclude_match_id,
            )
        else:
            historical_odds_reference = {
                'available': False,
                'league_history_count': self.odds_reference.get_league_record_count(league_code),
                'matched_feature_count': 0,
                'similar_matches': [],
                'summary': {'sample_size': 0, 'result_counts': {'主胜': 0, '平局': 0, '客胜': 0}, 'result_rates': {'主胜': 0.0, '平局': 0.0, '客胜': 0.0}, 'cold_result_count': 0, 'cold_result_rate': 0.0},
                'insights': ['当前未传入赔率快照，历史赔率参考已就绪但未参与匹配'],
            }

        adjusted_prob, live_adj_diag = self.apply_live_outcome_adjustment(
            league_code=league_code,
            final_prob=final_prob,
            current_odds=current_odds,
            historical_odds_reference=historical_odds_reference,
        )
        if isinstance(live_adj_diag, dict) and live_adj_diag.get('applied'):
            final_prob = adjusted_prob
            ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        realtime['context_applied']['live_outcome_adjustment'] = live_adj_diag
        preliminary_upset_potential = self._build_preliminary_upset_potential(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            strength_diff=strength_diff,
            predicted_outcome=ranked_probabilities[0][0] if ranked_probabilities else '平局',
            asian_handicap=asian_handicap,
            european_odds=european_odds,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['preliminary_upset_signal'] = {
            'available': bool(preliminary_upset_potential.get('available')),
            'motivation_risk': preliminary_upset_potential.get('motivation_risk') or {},
            'handicap_strength_mismatch': preliminary_upset_potential.get('handicap_strength_mismatch') or {},
        }
        final_prob, review_outcome_diag = self.postprocess_service.apply_review_outcome_adjustment(
            final_probabilities=final_prob,
            league_code=league_code,
            strength_diff=strength_diff,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
            upset_potential=preliminary_upset_potential,
        )
        realtime['context_applied']['review_outcome_adjustment'] = review_outcome_diag
        if isinstance(review_outcome_diag, dict) and review_outcome_diag.get('applied'):
            ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        final_prob, match_intel_diag = self.match_intelligence_engine._apply_match_intelligence_adjustment(final_prob=final_prob, match_intelligence=match_intelligence)
        if isinstance(match_intel_diag, dict):
            realtime['context_applied']['match_intelligence_adjustment'] = match_intel_diag
            retry_motivation = preliminary_upset_potential.get('motivation_risk') if isinstance(preliminary_upset_potential, dict) and isinstance(preliminary_upset_potential.get('motivation_risk'), dict) else {}
            retry_ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
            retry_top_label = retry_ranked_probabilities[0][0] if retry_ranked_probabilities else ''
            retry_home = float(final_prob.get('home_win', 0.0))
            retry_draw = float(final_prob.get('draw', 0.0))
            retry_away = float(final_prob.get('away_win', 0.0))
            retry_top_lead = retry_home - max(retry_draw, retry_away) if retry_top_label == '主胜' else 0.0
            retry_signals = {
                str(item).strip()
                for item in ((review_outcome_diag or {}).get('signals') or [])
                if str(item).strip()
            }
            retry_has_premier_away_support = bool(
                retry_signals.intersection({
                    'review-league-premier-away-upset-support',
                    'review-league-premier-balanced-away-floor',
                    'review-league-premier-away-follow-through',
                })
            )
            retry_strong_away_motivation = bool(
                bool(retry_motivation.get('supports_upset'))
                and str(retry_motivation.get('favored_side') or '').strip() == 'away'
                and str(retry_motivation.get('pressure_side') or '').strip() == 'home'
                and float(retry_motivation.get('score') or 0.0) >= 12.0
            )
            retry_balanced_home_top_case = bool(
                retry_top_label == '主胜'
                and retry_home <= 0.392
                and retry_draw >= 0.318
                and retry_away >= 0.272
                and retry_top_lead <= 0.07
            )
            should_retry_review_after_match_intelligence = bool(
                league_code == 'premier_league'
                and retry_top_label == '主胜'
                and not retry_has_premier_away_support
                and (retry_strong_away_motivation or retry_balanced_home_top_case)
            )
            realtime['context_applied']['review_outcome_retry_gate'] = {
                'eligible': should_retry_review_after_match_intelligence,
                'league_code': league_code,
                'top_label': retry_top_label,
                'motivation_risk': retry_motivation,
                'strong_away_motivation': retry_strong_away_motivation,
                'balanced_home_top_case': retry_balanced_home_top_case,
                'top_lead': round(float(retry_top_lead), 4),
                'has_premier_away_support': retry_has_premier_away_support,
            }
            if should_retry_review_after_match_intelligence:
                review_retry_upset_signal = dict(preliminary_upset_potential) if isinstance(preliminary_upset_potential, dict) else {}
                if retry_balanced_home_top_case and not retry_strong_away_motivation:
                    review_retry_upset_signal['motivation_risk'] = {
                        'available': True,
                        'supports_upset': True,
                        'favored_side': 'away',
                        'pressure_side': 'home',
                        'score': 12.0,
                    }
                review_retry_prob, review_retry_diag = self.postprocess_service.apply_review_outcome_adjustment(
                    final_probabilities=final_prob,
                    league_code=league_code,
                    strength_diff=strength_diff,
                    asian_handicap=asian_handicap,
                    current_odds=current_odds,
                    review_learning=review_learning,
                    match_intelligence=match_intelligence,
                    upset_potential=review_retry_upset_signal,
                )
                if isinstance(review_retry_diag, dict):
                    review_retry_diag = dict(review_retry_diag)
                    review_retry_diag['retry_after_match_intelligence'] = True
                    realtime['context_applied']['review_outcome_adjustment_retry'] = review_retry_diag
                    if review_retry_diag.get('applied'):
                        final_prob = review_retry_prob
                        review_outcome_diag = review_retry_diag
                        realtime['context_applied']['review_outcome_adjustment'] = review_retry_diag
                        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        over_under, over_under_diag = self.build_real_market_over_under(
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            current_odds=current_odds,
            analysis_context=analysis_context,
            match_intelligence=match_intelligence,
            realtime_context_applied=realtime['context_applied'],
        )
        realtime['context_applied']['over_under_guard'] = over_under_diag
        over_under, review_ou_diag = self.postprocess_service.apply_review_over_under_adjustment(
            over_under=over_under,
            league_code=league_code,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['review_over_under_adjustment'] = review_ou_diag
        final_prob, real_ou_outcome_diag = self.apply_real_totals_outcome_adjustment(
            final_prob=final_prob,
            current_odds=current_odds,
            over_under=over_under,
        )
        realtime['context_applied']['real_market_over_under_outcome_adjustment'] = real_ou_outcome_diag
        final_prob, draw_guard_diag = self._apply_draw_confirmation_guard(
            final_prob=final_prob,
            current_odds=current_odds,
            over_under=over_under,
            match_intelligence=match_intelligence,
            review_outcome_diag=review_outcome_diag,
        )
        realtime['context_applied']['draw_confirmation_guard'] = draw_guard_diag
        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        main_prediction = ranked_probabilities[0][0]
        confidence = ranked_probabilities[0][1]

        lightweight_rag_decision = {}
        upset_potential = self.upset_analyzer.assess_upset_potential(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            strength_diff=strength_diff,
            home_strength=home_strength,
            away_strength=away_strength,
            predicted_outcome=main_prediction,
            confidence=confidence,
            historical_odds_reference=historical_odds_reference,
            asian_handicap=asian_handicap,
            european_odds=european_odds,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['full_upset_signal'] = {
            'available': bool(isinstance(upset_potential, dict) and upset_potential),
            'level': upset_potential.get('level') if isinstance(upset_potential, dict) else None,
            'similar_cases_count': int(upset_potential.get('similar_cases_count') or 0) if isinstance(upset_potential, dict) else 0,
            'risk_score_detail': upset_potential.get('risk_score_detail') if isinstance(upset_potential, dict) else {},
            'case_knowledge': upset_potential.get('case_knowledge') if isinstance(upset_potential, dict) else {},
        }
        serie_a_upset_knowledge_retry_gate = self._build_serie_a_upset_knowledge_retry_gate(
            final_prob=final_prob,
            review_outcome_diag=review_outcome_diag,
            draw_guard_diag=draw_guard_diag,
            upset_potential=upset_potential,
            main_prediction=main_prediction,
            league_code=league_code,
        )
        realtime['context_applied']['serie_a_upset_knowledge_retry_gate'] = serie_a_upset_knowledge_retry_gate
        if serie_a_upset_knowledge_retry_gate.get('eligible'):
            review_retry_signal = {
                'available': True,
                'motivation_risk': review_outcome_diag.get('motivation_risk') if isinstance(review_outcome_diag, dict) and isinstance(review_outcome_diag.get('motivation_risk'), dict) else {},
                'handicap_strength_mismatch': review_outcome_diag.get('handicap_strength_mismatch') if isinstance(review_outcome_diag, dict) and isinstance(review_outcome_diag.get('handicap_strength_mismatch'), dict) else {},
                'risk_score_detail': upset_potential.get('risk_score_detail') if isinstance(upset_potential, dict) else {},
                'historical_odds_reference': upset_potential.get('historical_odds_reference') if isinstance(upset_potential, dict) else {},
                'case_knowledge': upset_potential.get('case_knowledge') if isinstance(upset_potential, dict) else {},
                'similar_cases_count': int(upset_potential.get('similar_cases_count') or 0) if isinstance(upset_potential, dict) else 0,
            }
            review_retry_prob, review_retry_diag = self.postprocess_service.apply_review_outcome_adjustment(
                final_probabilities=final_prob,
                league_code=league_code,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                current_odds=current_odds,
                review_learning=review_learning,
                match_intelligence=match_intelligence,
                upset_potential=review_retry_signal,
            )
            if isinstance(review_retry_diag, dict):
                review_retry_diag = dict(review_retry_diag)
                review_retry_diag['retry_from_full_upset_knowledge'] = True
                retry_shift = review_retry_diag.get('applied_shift') if isinstance(review_retry_diag.get('applied_shift'), dict) else {}
                retry_shift['draw_shift'] = max(
                    float(retry_shift.get('draw_shift') or 0.0),
                    float(serie_a_upset_knowledge_retry_gate.get('draw_shift') or 0.0),
                )
                retry_shift['away_shift'] = max(
                    float(retry_shift.get('away_shift') or 0.0),
                    float(serie_a_upset_knowledge_retry_gate.get('away_shift') or 0.0),
                )
                review_retry_diag['applied_shift'] = retry_shift
                retry_signals = [str(item).strip() for item in (review_retry_diag.get('signals') or []) if str(item).strip()]
                for signal_name in (
                    'review-league-serie-a-draw-to-away-relief',
                    'review-league-serie-a-soft-draw-away-entry',
                    'review-league-serie-a-upset-knowledge-retry',
                ):
                    if signal_name not in retry_signals:
                        retry_signals.append(signal_name)
                review_retry_diag['signals'] = retry_signals
                realtime['context_applied']['review_outcome_adjustment_full_upset_retry'] = review_retry_diag
                knowledge_draw_shift = float(serie_a_upset_knowledge_retry_gate.get('draw_shift') or 0.0)
                knowledge_away_shift = float(serie_a_upset_knowledge_retry_gate.get('away_shift') or 0.0)
                final_prob = self.postprocess_service._shift_from_side_to_targets(
                    review_retry_prob,
                    from_key='home_win',
                    draw_shift=knowledge_draw_shift,
                    away_shift=knowledge_away_shift,
                )
                final_prob, draw_guard_diag = self._apply_draw_confirmation_guard(
                    final_prob=final_prob,
                    current_odds=current_odds,
                    over_under=over_under,
                    match_intelligence=match_intelligence,
                    review_outcome_diag=review_retry_diag,
                )
                realtime['context_applied']['draw_confirmation_guard'] = draw_guard_diag
                review_outcome_diag = review_retry_diag
                realtime['context_applied']['review_outcome_adjustment'] = review_retry_diag
                ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
                main_prediction = ranked_probabilities[0][0]
                confidence = ranked_probabilities[0][1]
        confidence, confidence_diag = self._calibrate_confidence_with_league_learning(
            confidence=confidence,
            applied_weights=applied_weights,
        )
        realtime['context_applied']['league_confidence_adjustment'] = confidence_diag

        match_intelligence = self.match_intelligence_engine._finalize_match_intelligence(
            match_intelligence=match_intelligence,
            historical_odds_reference=historical_odds_reference,
            upset_potential=upset_potential,
        )

        dc_model = DixonColesModel(rho=resolve_rho(league_code))
        score_result = dc_model.predict_with_dixon_coles(home_lambda, away_lambda)
        top_scores, score_guard_diag = self._rerank_scores_for_under_three(
            score_result.get('score_probs'),
            over_under,
            limit=8,
        )
        realtime['context_applied']['score_rerank_guard'] = score_guard_diag
        top_scores, review_score_diag = self.postprocess_service.rerank_top_scores(
            top_scores,
            main_prediction,
            league_code=league_code,
            ranked_probabilities=ranked_probabilities,
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            over_under=over_under,
            strength_diff=strength_diff,
            confidence=confidence,
            current_odds=current_odds,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
            upset_potential=upset_potential,
            return_diag=True,
            limit=3,
        )
        realtime['context_applied']['review_score_rerank'] = review_score_diag
        total_goals = self.postprocess_service.compute_total_goals_distribution(score_result.get('score_probs', {}), max_bucket=7)
        total_goals, review_total_goals_diag = self.postprocess_service.apply_three_layer_total_goals_adjustment(
            total_goals,
            league_code=league_code,
            predicted_outcome_label=main_prediction,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
            total_lambda=home_lambda + away_lambda,
            over_under=over_under,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['review_total_goals_adjustment'] = review_total_goals_diag

        return {
            'applied_weights': applied_weights,
            'home_strength': home_strength,
            'away_strength': away_strength,
            'match_intelligence': match_intelligence,
            'strength_diff': strength_diff,
            'asian_handicap': asian_handicap,
            'european_odds': european_odds,
            'fusion_result': fusion_result,
            'final_probabilities': final_prob,
            'ranked_probabilities': ranked_probabilities,
            'main_prediction': main_prediction,
            'confidence': confidence,
            'historical_odds_reference': historical_odds_reference,
            'upset_potential': upset_potential,
            'top_scores': top_scores,
            'total_goals': total_goals,
            'home_lambda': home_lambda,
            'lightweight_rag_decision': lightweight_rag_decision,
            'away_lambda': away_lambda,
            'over_under': over_under,
        }
