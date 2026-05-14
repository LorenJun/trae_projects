"""模块说明：负责核心推理链、盘口校准、联赛学习与概率融合。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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
    def _rerank_scores_for_under_three(
        score_probs: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
    ) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
        ranked = sorted(
            ((str(score), float(prob or 0.0)) for score, prob in (score_probs or {}).items()),
            key=lambda item: item[1],
            reverse=True,
        )
        diag: Dict[str, Any] = {
            'applied': False,
            'reason': 'guard_not_triggered',
            'line': None,
            'over': None,
            'under': None,
            'penalties': {},
        }
        if not ranked or not isinstance(over_under, dict):
            diag['reason'] = 'missing_score_probs_or_over_under'
            return ranked[:3], diag

        line = InferencePipelineService._to_float(over_under.get('line'))
        over_prob = InferencePipelineService._to_float(over_under.get('over'))
        under_prob = InferencePipelineService._to_float(over_under.get('under'))
        diag['line'] = line
        diag['over'] = over_prob
        diag['under'] = under_prob
        if line is None or over_prob is None or under_prob is None:
            diag['reason'] = 'invalid_over_under_payload'
            return ranked[:3], diag
        if line > 3.0 or under_prob <= over_prob:
            diag['reason'] = 'not_under_three'
            return ranked[:3], diag

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
            diag['penalties'] = penalties
        else:
            diag['reason'] = 'target_scores_missing'
        return reranked[:3], diag

    @staticmethod
    def _apply_draw_confirmation_guard(
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
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
            diag['ou_under_edge'] = round(ou_under - ou_over, 4)
            if ou_line is not None and ou_line <= 2.5 and ou_under > ou_over:
                diag['qualified'] = True
                diag['evidence'].append('under_supports_draw')
            elif ou_under <= ou_over and ou_line is not None and ou_line >= 2.75:
                diag['signals'].append('open_total_not_support_draw')

        scenario_tags = match_intelligence.get('scenario_tags', []) if isinstance(match_intelligence, dict) else []
        contextual_rules = match_intelligence.get('contextual_rules', {}) if isinstance(match_intelligence, dict) else {}
        if 'recent_form_volatility_high' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('double_volatility_supports_draw')
        if 'la_liga_mid_table_home_flat' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('la_liga_mid_table_home_flat')
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags:
            diag['signals'].append('relegation_home_motivation_conflicts_draw')
        volatility = contextual_rules.get('volatility') if isinstance(contextual_rules, dict) else {}
        if isinstance(volatility, dict):
            diag['volatility'] = {
                'home': (volatility.get('home') or {}).get('label'),
                'away': (volatility.get('away') or {}).get('label'),
            }

        if diag['qualified']:
            diag['reason'] = 'draw_confirmation_passed'
            return final_prob, diag

        draw_excess = min(0.026, max(0.0, p_d - runner_up + 0.006))
        if draw_excess <= 0.0:
            diag['reason'] = 'draw_confirmation_no_shift'
            return final_prob, diag
        favored_side = 'home_win' if p_h >= p_a else 'away_win'
        favored_ratio = 0.68 if favored_side == 'home_win' else 0.32
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags and favored_side == 'home_win':
            favored_ratio = 0.78
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

    def apply_dynamic_weights(self, league_code: str) -> Dict[str, Any]:
        try:
            diag = self.weight_adjuster.get_adjustment_diagnostics(league_code)
            weights = diag.get('final_weights')
            if weights and hasattr(self.model_fusion, 'set_model_weights'):
                self.model_fusion.set_model_weights(weights)
            return diag
        except Exception as exc:
            return {'league_code': league_code, 'error': str(exc)}

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
            rho_map = {'premier_league': -0.08, 'la_liga': -0.10, 'serie_a': -0.12, 'bundesliga': -0.06, 'ligue_1': -0.10}
            dc = DixonColesModel(rho=rho_map.get(league_code, -0.10))
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

        rho_map = {'premier_league': -0.08, 'la_liga': -0.10, 'serie_a': -0.12, 'bundesliga': -0.06, 'ligue_1': -0.10}
        dc = DixonColesModel(rho=rho_map.get(league_code, -0.10))
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

    def build_real_market_over_under(
        self,
        *,
        home_lambda: float,
        away_lambda: float,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        match_intelligence: Optional[Dict[str, Any]],
        realtime_context_applied: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        diag: Dict[str, Any] = {
            'available': False,
            'requires_real_market_line': True,
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
        asian_handicap = current_odds.get('亚值') if isinstance(current_odds, dict) else None
        european_odds = current_odds.get('欧赔') if isinstance(current_odds, dict) else None
        league_avg_goals = self.league_config[league_code]['avg_goals']
        home_form = int(analysis_context.get('home_form', 3))
        away_form = int(analysis_context.get('away_form', 3))
        home_motivation = float(analysis_context.get('home_motivation', 75))
        away_motivation = float(analysis_context.get('away_motivation', 75))
        realtime['context_applied'].update({'home_form': home_form, 'away_form': away_form, 'home_motivation': home_motivation, 'away_motivation': away_motivation})

        base_home_lambda = home_strength['attack'] * away_strength['defense'] * league_avg_goals * 1.12
        base_away_lambda = away_strength['attack'] * home_strength['defense'] * league_avg_goals
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
        )
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
        final_prob, match_intel_diag = self.match_intelligence_engine._apply_match_intelligence_adjustment(final_prob=final_prob, match_intelligence=match_intelligence)
        if isinstance(match_intel_diag, dict):
            realtime['context_applied']['match_intelligence_adjustment'] = match_intel_diag
        over_under, over_under_diag = self.build_real_market_over_under(
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            current_odds=current_odds,
            analysis_context=analysis_context,
            match_intelligence=match_intelligence,
            realtime_context_applied=realtime['context_applied'],
        )
        realtime['context_applied']['over_under_guard'] = over_under_diag
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
        )
        realtime['context_applied']['draw_confirmation_guard'] = draw_guard_diag
        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        main_prediction = ranked_probabilities[0][0]
        confidence = ranked_probabilities[0][1]

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
        match_intelligence = self.match_intelligence_engine._finalize_match_intelligence(
            match_intelligence=match_intelligence,
            historical_odds_reference=historical_odds_reference,
            upset_potential=upset_potential,
        )

        rho_map = {'premier_league': -0.08, 'la_liga': -0.10, 'serie_a': -0.12, 'bundesliga': -0.06, 'ligue_1': -0.10}
        dc_model = DixonColesModel(rho=rho_map.get(league_code, -0.10))
        score_result = dc_model.predict_with_dixon_coles(home_lambda, away_lambda)
        top_scores, score_guard_diag = self._rerank_scores_for_under_three(
            score_result.get('score_probs'),
            over_under,
        )
        realtime['context_applied']['score_rerank_guard'] = score_guard_diag
        total_goals = self.postprocess_service.compute_total_goals_distribution(score_result.get('score_probs', {}), max_bucket=7)

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
            'away_lambda': away_lambda,
            'over_under': over_under,
        }
