"""模块说明：负责胜平负概率后处理、比分分布、大小球和凯利结果装配。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from domain.review_learning import PredictionReviewLearningService


OUTCOME_LABELS = (
    ('主胜', 'home_win', 'home'),
    ('平局', 'draw', 'draw'),
    ('客胜', 'away_win', 'away'),
)


class PredictionPostprocessService:
    def __init__(self, league_config: Dict[str, Dict[str, Any]]):
        self.league_config = league_config

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_prob_triplet(final_probabilities: Dict[str, float]) -> Dict[str, float]:
        home = float(final_probabilities.get('home_win') or 0.0)
        draw = float(final_probabilities.get('draw') or 0.0)
        away = float(final_probabilities.get('away_win') or 0.0)
        total = home + draw + away
        if total <= 0:
            return {'home_win': 0.0, 'draw': 0.0, 'away_win': 0.0}
        return {
            'home_win': home / total,
            'draw': draw / total,
            'away_win': away / total,
        }

    @staticmethod
    def _shift_from_side_to_targets(
        probabilities: Dict[str, float],
        *,
        from_key: str,
        draw_shift: float = 0.0,
        away_shift: float = 0.0,
        home_shift: float = 0.0,
    ) -> Dict[str, float]:
        updated = dict(probabilities)
        total_shift = max(0.0, draw_shift) + max(0.0, away_shift) + max(0.0, home_shift)
        available = max(0.0, float(updated.get(from_key) or 0.0) - 0.02)
        actual_shift = min(total_shift, available)
        if actual_shift <= 0:
            return PredictionPostprocessService._normalize_prob_triplet(updated)
        scale = actual_shift / total_shift if total_shift > 0 else 0.0
        draw_take = max(0.0, draw_shift) * scale
        away_take = max(0.0, away_shift) * scale
        home_take = max(0.0, home_shift) * scale
        updated[from_key] = max(0.0, float(updated.get(from_key) or 0.0) - actual_shift)
        updated['draw'] = float(updated.get('draw') or 0.0) + draw_take
        updated['away_win'] = float(updated.get('away_win') or 0.0) + away_take
        updated['home_win'] = float(updated.get('home_win') or 0.0) + home_take
        return PredictionPostprocessService._normalize_prob_triplet(updated)

    @staticmethod
    def _rescale_score_list(scores: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
        total = sum(max(0.0, float(prob or 0.0)) for _, prob in scores)
        if total <= 0:
            return [(score, float(prob or 0.0)) for score, prob in scores]
        return [(score, float(prob or 0.0) / total) for score, prob in scores]

    @staticmethod
    def build_market_snapshot(current_odds: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(current_odds, dict):
            return {}
        snapshot: Dict[str, Any] = {}
        for key in ('欧赔', '亚值', '大小球', '凯利'):
            value = current_odds.get(key)
            if isinstance(value, dict):
                snapshot[key] = value
        return snapshot

    @staticmethod
    def normalize_probs(p: Dict[str, float]) -> Dict[str, float]:
        h = float(p.get('home_win') or 0.0)
        d = float(p.get('draw') or 0.0)
        a = float(p.get('away_win') or 0.0)
        total = h + d + a
        if total <= 0:
            return {'home_win': 0.0, 'draw': 0.0, 'away_win': 0.0}
        return {'home_win': h / total, 'draw': d / total, 'away_win': a / total}

    @staticmethod
    def rank_outcomes(final_probabilities: Dict[str, float]) -> List[Tuple[str, float]]:
        ranked = [
            ('主胜', float(final_probabilities.get('home_win') or 0.0)),
            ('平局', float(final_probabilities.get('draw') or 0.0)),
            ('客胜', float(final_probabilities.get('away_win') or 0.0)),
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    @staticmethod
    def compute_total_goals_distribution(score_probs: Dict[str, float], max_bucket: int = 7) -> Dict[str, Any]:
        if not isinstance(score_probs, dict) or not score_probs:
            return {'available': False, 'reason': 'no_score_probs'}
        buckets = {str(i): 0.0 for i in range(max_bucket + 1)}
        buckets[f'{max_bucket}+'] = 0.0
        for score, prob in score_probs.items():
            if not isinstance(prob, (int, float)):
                continue
            match = re.match(r'^(\d+)\s*-\s*(\d+)$', str(score).strip())
            if not match:
                continue
            total_goals = int(match.group(1)) + int(match.group(2))
            if total_goals >= max_bucket:
                buckets[f'{max_bucket}+'] += float(prob)
            else:
                buckets[str(total_goals)] += float(prob)

        total_prob = sum(buckets.values())
        if total_prob > 0:
            for key in list(buckets.keys()):
                buckets[key] = buckets[key] / total_prob

        top = sorted(((key, buckets[key]) for key in buckets.keys()), key=lambda item: item[1], reverse=True)[:3]
        return {
            'available': True,
            'buckets': {key: round(float(value), 6) for key, value in buckets.items()},
            'top_totals': [{'total': key, 'prob': round(float(value), 4)} for key, value in top],
            'tail_bucket': f'{max_bucket}+',
        }

    @staticmethod
    def extract_decimal_odds_1x2(current_odds: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        if not isinstance(current_odds, dict):
            return {'home': None, 'draw': None, 'away': None, 'source': 'none'}

        def pick_final(block_name: str) -> Optional[Dict[str, Any]]:
            block = current_odds.get(block_name)
            if isinstance(block, dict):
                final = block.get('final')
                if isinstance(final, dict):
                    return final
            return None

        def parse(value: Any) -> Optional[float]:
            try:
                if value is None:
                    return None
                numeric = float(str(value).strip())
                return numeric if numeric > 1.01 else None
            except Exception:
                return None

        final = pick_final('胜平负赔率')
        if final:
            return {
                'home': parse(final.get('home') if 'home' in final else final.get('主')),
                'draw': parse(final.get('draw') if 'draw' in final else final.get('平')),
                'away': parse(final.get('away') if 'away' in final else final.get('客')),
                'source': '胜平负赔率.final',
            }

        final = pick_final('欧赔')
        if final:
            return {
                'home': parse(final.get('home') if 'home' in final else final.get('主')),
                'draw': parse(final.get('draw') if 'draw' in final else final.get('平')),
                'away': parse(final.get('away') if 'away' in final else final.get('客')),
                'source': '欧赔.final',
            }

        return {'home': None, 'draw': None, 'away': None, 'source': 'none'}

    @staticmethod
    def kelly_fraction(probability: float, odds: float) -> Optional[float]:
        try:
            probability = float(probability)
            odds = float(odds)
            if odds <= 1.01:
                return None
            b = odds - 1.0
            q = 1.0 - probability
            fraction = (b * probability - q) / b
            return max(0.0, min(1.0, float(fraction)))
        except Exception:
            return None

    def build_kelly_staking(
        self,
        final_probabilities: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str] = None,
        cap_half: float = 0.05,
        cap_quarter: float = 0.03,
    ) -> Dict[str, Any]:
        probabilities = self.normalize_probs(final_probabilities or {})
        odds = self.extract_decimal_odds_1x2(current_odds)
        result: Dict[str, Any] = {
            'available': False,
            'odds_source': odds.get('source'),
            'odds': {'home': odds.get('home'), 'draw': odds.get('draw'), 'away': odds.get('away')},
            'probabilities': probabilities,
            'by_outcome': {},
            'recommended': {},
        }
        if not (odds.get('home') and odds.get('draw') and odds.get('away')):
            result['reason'] = 'missing_odds'
            return result

        by_outcome: Dict[str, Dict[str, Optional[float]]] = {}
        for label, prob_key, odds_key in OUTCOME_LABELS:
            full = self.kelly_fraction(probabilities.get(prob_key, 0.0), odds.get(odds_key) or 0.0)
            if full is None:
                by_outcome[label] = {'full': None, 'half_cap5': None, 'quarter_cap3': None}
                continue
            half = min(float(cap_half), 0.5 * full)
            quarter = min(float(cap_quarter), 0.25 * full)
            by_outcome[label] = {
                'full': round(float(full), 6),
                'half_cap5': round(float(half), 6),
                'quarter_cap3': round(float(quarter), 6),
            }

        result['by_outcome'] = by_outcome
        result['available'] = True
        if predicted_outcome and predicted_outcome in by_outcome:
            slot = by_outcome.get(predicted_outcome) or {}
            candidates = []
            if isinstance(slot.get('half_cap5'), (int, float)):
                candidates.append(('half_cap5', float(slot['half_cap5'])))
            if isinstance(slot.get('quarter_cap3'), (int, float)):
                candidates.append(('quarter_cap3', float(slot['quarter_cap3'])))
            if candidates:
                method, fraction = sorted(candidates, key=lambda item: item[1])[0]
                result['recommended'] = {
                    'outcome': predicted_outcome,
                    'method': method,
                    'fraction': round(float(fraction), 6),
                }
        return result

    @staticmethod
    def attach_over_under_context(
        over_under: Dict[str, Any],
        current_odds: Optional[Dict[str, Any]],
        learning_diag: Optional[Dict[str, Any]],
        ou_line_source: str,
        realtime_context_applied: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(over_under, dict):
            return over_under
        over_under['line_source'] = ou_line_source
        if isinstance(learning_diag, dict) and learning_diag.get('applied'):
            over_under['league_learning'] = {
                'sample_size': learning_diag.get('sample_size'),
                'signals': learning_diag.get('signals', []),
                'recent_avg_goals': learning_diag.get('recent_avg_goals'),
                'over25_rate': learning_diag.get('over25_rate'),
                'over35_rate': learning_diag.get('over35_rate'),
                'btts_rate': learning_diag.get('btts_rate'),
                'clean_sheet_rate': learning_diag.get('clean_sheet_rate'),
            }
        try:
            market_ou = None
            if isinstance(current_odds, dict):
                block = current_odds.get('大小球')
                if isinstance(block, dict):
                    final = block.get('final') if isinstance(block.get('final'), dict) else {}
                    initial = block.get('initial') if isinstance(block.get('initial'), dict) else {}
                    market_ou = {'final': final or {}, 'initial': initial or {}}
                    if isinstance(block.get('consensus'), dict):
                        market_ou['consensus'] = block.get('consensus') or {}
                    if isinstance(block.get('companies'), list):
                        market_ou['companies'] = block.get('companies') or []
                    if block.get('company_mode'):
                        market_ou['company_mode'] = block.get('company_mode')
            if market_ou:
                over_under['market'] = market_ou
                if isinstance(realtime_context_applied, dict):
                    realtime_context_applied['ou_market'] = market_ou
        except Exception:
            pass
        return over_under

    @staticmethod
    def build_retrieved_memory_explanation(retrieved_memory: Optional[Dict[str, Any]]) -> str:
        memory = retrieved_memory if isinstance(retrieved_memory, dict) else {}
        summary = memory.get('summary') if isinstance(memory.get('summary'), dict) else {}
        similar_cases = memory.get('similar_cases') if isinstance(memory.get('similar_cases'), list) else []
        market_cases = memory.get('market_cases') if isinstance(memory.get('market_cases'), list) else []
        upset_cases = memory.get('upset_cases') if isinstance(memory.get('upset_cases'), list) else []
        if not similar_cases and not market_cases and not upset_cases:
            return 'RAG记忆暂未召回足够高质量样本，当前结论主要依赖模型推理、实时盘口与球队上下文。'

        def normalize_risk_label(text: str) -> str:
            risk = str(text or '').strip()
            if not risk:
                return ''
            if risk.startswith('控球倾向 '):
                return '控球倾向差异'
            if risk.startswith('资金流向代理偏'):
                return '资金流向偏移'
            if risk.startswith('三盘口整体偏'):
                return '三盘口共振'
            if risk.startswith('三盘口画像:'):
                return '三盘口画像共振'
            if '平局凯利偏低' in risk:
                return '平局凯利偏低'
            if '诱盘风险' in risk:
                return '诱盘风险'
            if '历史同向反打' in risk:
                return '历史同向反打'
            if '历史超级冷门' in risk:
                return '历史超级冷门'
            if '伤病严重' in risk:
                return '伤病风险'
            if '深让>=' in risk or risk.startswith('深让'):
                return '深盘风险'
            return risk

        def summarize_upset_risks(items: List[Dict[str, Any]]) -> List[str]:
            counts: Dict[str, int] = {}
            order: List[str] = []
            for item in items:
                for raw in item.get('risk_points') or []:
                    label = normalize_risk_label(str(raw or ''))
                    if not label:
                        continue
                    counts[label] = counts.get(label, 0) + 1
                    if label not in order:
                        order.append(label)
            return sorted(order, key=lambda key: (-counts.get(key, 0), order.index(key)))[:2]

        fragments: List[str] = []
        completed_count = summary.get('completed_similar_case_count')
        direction_priority = summary.get('direction_priority') if isinstance(summary.get('direction_priority'), dict) else {}
        ou_priority = summary.get('ou_priority') if isinstance(summary.get('ou_priority'), dict) else {}
        direction_ou_priority = summary.get('direction_ou_priority') if isinstance(summary.get('direction_ou_priority'), dict) else {}
        live_market_followup = summary.get('live_market_followup') if isinstance(summary.get('live_market_followup'), dict) else {}
        predicted_outcome = str(direction_priority.get('predicted_outcome') or '').strip()
        current_ou_direction = str(ou_priority.get('current_ou_direction') or '').strip()
        direction_case_count = direction_priority.get('matched_case_count')
        direction_ou_case_count = direction_ou_priority.get('matched_case_count')
        preferred_scores = [str(item) for item in (direction_ou_priority.get('preferred_scores') or []) if str(item).strip()]
        if predicted_outcome:
            fragment = f"历史复盘先按{predicted_outcome}方向匹配"
            if isinstance(direction_case_count, int):
                fragment += f"{direction_case_count}场"
            if current_ou_direction:
                fragment += f"，再按{current_ou_direction}方向筛选"
                if isinstance(direction_ou_case_count, int):
                    fragment += f"{direction_ou_case_count}场"
            if preferred_scores:
                fragment += f"，对应高频比分为{'/'.join(preferred_scores[:3])}"
            fragments.append(fragment)
        if live_market_followup.get('applied'):
            eligible_count = int(live_market_followup.get('eligible_count') or 0)
            recommended_action = str(live_market_followup.get('recommended_action') or '').strip()
            advice = str(live_market_followup.get('advice') or '').strip()
            fragment = f"临场赔率轨迹门槛命中{eligible_count}场"
            if recommended_action:
                fragment += f"，操作建议为{recommended_action}"
            if advice:
                fragment += f"，{advice}"
            fragments.append(fragment)
        if similar_cases:
            fragment = f"RAG召回{len(similar_cases)}场相似比赛"
            if isinstance(completed_count, int) and completed_count > 0:
                rates = []
                for label, key in (('主胜', 'home_win_rate'), ('平局', 'draw_rate'), ('客胜', 'away_win_rate')):
                    value = summary.get(key)
                    if isinstance(value, (int, float)):
                        rates.append(f"{label}{float(value):.0%}")
                if rates:
                    fragment += f"，其中已完赛{completed_count}场，结果分布为{'/'.join(rates)}"
            fragments.append(fragment)
        if market_cases:
            avg_total = summary.get('avg_market_total_goals')
            fragment = f"盘口相似案例{len(market_cases)}场"
            if isinstance(avg_total, (int, float)):
                fragment += f"，历史平均总进球约{float(avg_total):.2f}"
            top_market = market_cases[0]
            if top_market.get('actual_score'):
                fragment += f"，最相近盘口样本赛果为{top_market.get('actual_result') or ''} {top_market.get('actual_score')}".strip()
            fragments.append(fragment)
        if upset_cases:
            fragment = f"爆冷风险参考{len(upset_cases)}场"
            risk_bits = summarize_upset_risks(upset_cases)
            if risk_bits:
                fragment += f"，高频风险包括{'、'.join(risk_bits[:2])}"
            fragments.append(fragment)
        return '；'.join(fragments) + '。'

    def normalize_ou_market_prices(self, prices: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        source = prices if isinstance(prices, dict) else {}
        over_raw = self._safe_float(source.get('over'))
        under_raw = self._safe_float(source.get('under'))
        if over_raw is None or under_raw is None:
            return {'available': False, 'reason': 'missing_prices'}
        if over_raw <= 0 or under_raw <= 0:
            return {'available': False, 'reason': 'invalid_prices'}
        if max(over_raw, under_raw) <= 1.2:
            fmt = 'hong_kong'
            over_decimal = round(over_raw + 1.0, 6)
            under_decimal = round(under_raw + 1.0, 6)
        else:
            fmt = 'decimal'
            over_decimal = round(over_raw, 6)
            under_decimal = round(under_raw, 6)
        implied_over = 1.0 / over_decimal if over_decimal > 1.01 else 0.0
        implied_under = 1.0 / under_decimal if under_decimal > 1.01 else 0.0
        total = implied_over + implied_under
        return {
            'available': total > 0,
            'format': fmt,
            'over_raw': over_raw,
            'under_raw': under_raw,
            'over_decimal': over_decimal,
            'under_decimal': under_decimal,
            'over_prob': round(implied_over / total, 6) if total > 0 else None,
            'under_prob': round(implied_under / total, 6) if total > 0 else None,
        }

    def extract_over_under_market_signal(self, current_odds: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        totals = current_odds.get('大小球') if isinstance(current_odds, dict) and isinstance(current_odds.get('大小球'), dict) else {}
        initial = totals.get('initial') if isinstance(totals.get('initial'), dict) else {}
        final = totals.get('final') if isinstance(totals.get('final'), dict) else {}
        initial_line = self._safe_float(initial.get('line'))
        final_line = self._safe_float(final.get('line'))
        initial_prices = self.normalize_ou_market_prices(initial)
        final_prices = self.normalize_ou_market_prices(final)
        if initial_line is None or final_line is None or not initial_prices.get('available') or not final_prices.get('available'):
            return {'available': False, 'reason': 'missing_ou_market'}
        bias_initial = float(initial_prices.get('over_prob') or 0.0) - float(initial_prices.get('under_prob') or 0.0)
        bias_final = float(final_prices.get('over_prob') or 0.0) - float(final_prices.get('under_prob') or 0.0)
        line_delta = final_line - initial_line
        bias_delta = bias_final - bias_initial
        signals: List[str] = []
        if line_delta >= 0.24:
            signals.append('ou_line_up')
        elif line_delta <= -0.24:
            signals.append('ou_line_down')
        over_raw_initial = self._safe_float(initial.get('over'))
        over_raw_final = self._safe_float(final.get('over'))
        under_raw_initial = self._safe_float(initial.get('under'))
        under_raw_final = self._safe_float(final.get('under'))
        if over_raw_initial is not None and over_raw_final is not None and over_raw_final + 1e-9 < over_raw_initial:
            signals.append('over_water_drop')
        if under_raw_initial is not None and under_raw_final is not None and under_raw_final + 1e-9 < under_raw_initial:
            signals.append('under_water_drop')
        goal_pressure = 'balanced'
        if bias_final >= 0.03:
            goal_pressure = 'over'
        elif bias_final <= -0.03:
            goal_pressure = 'under'
        direction = 1.0 if goal_pressure == 'over' else -1.0 if goal_pressure == 'under' else 0.0
        pace_shift = direction * (abs(line_delta) * 0.08 + abs(bias_delta) * 0.25 + abs(bias_final) * 0.06)
        return {
            'available': True,
            'goal_pressure': goal_pressure,
            'signals': signals,
            'initial_line': initial_line,
            'final_line': final_line,
            'line_delta': round(line_delta, 4),
            'bias_initial': round(bias_initial, 6),
            'bias_final': round(bias_final, 6),
            'bias_delta': round(bias_delta, 6),
            'pace_shift': round(pace_shift, 6),
            'initial_price_format': initial_prices.get('format'),
            'final_price_format': final_prices.get('format'),
            'initial_prices': initial_prices,
            'final_prices': final_prices,
        }

    def build_three_layer_runtime_context(
        self,
        *,
        predicted_outcome_label: str,
        strength_diff: Any,
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        _ = review_learning
        winner_map = {'主胜': 'home', '平局': 'draw', '客胜': 'away'}
        predicted_winner = winner_map.get(str(predicted_outcome_label or '').strip(), '')
        asian = current_odds.get('亚值') if isinstance(current_odds, dict) and isinstance(current_odds.get('亚值'), dict) else {}
        asian_final = asian.get('final') if isinstance(asian.get('final'), dict) else {}
        asian_line = (
            asian_final.get('handicap_value')
            if 'handicap_value' in asian_final
            else asian_final.get('handicap')
            if 'handicap' in asian_final
            else asian_final.get('盘口值')
        )
        handicap_depth_bucket = PredictionReviewLearningService._classify_handicap_depth(asian_line)
        strength_value = self._safe_float(strength_diff)
        strength_gap_bucket = (
            PredictionReviewLearningService._classify_strength_gap_bucket(strength_value)
            if strength_value is not None
            else 'unknown'
        )
        euro = current_odds.get('欧赔') if isinstance(current_odds, dict) and isinstance(current_odds.get('欧赔'), dict) else {}
        euro_final = euro.get('final') if isinstance(euro.get('final'), dict) else {}
        euro_home = self._safe_float(euro_final.get('home') if 'home' in euro_final else euro_final.get('主'))
        euro_draw = self._safe_float(euro_final.get('draw') if 'draw' in euro_final else euro_final.get('平'))
        euro_away = self._safe_float(euro_final.get('away') if 'away' in euro_final else euro_final.get('客'))
        euro_support_bucket = PredictionReviewLearningService._classify_euro_support_bucket(
            predicted_winner=predicted_winner,
            euro_home=euro_home,
            euro_draw=euro_draw,
            euro_away=euro_away,
        ) if predicted_winner else 'unknown'
        if predicted_winner in {'home', 'away'} and euro_home and euro_draw and euro_away and euro_support_bucket == 'support':
            implied_home = 1.0 / euro_home
            implied_draw = 1.0 / euro_draw
            implied_away = 1.0 / euro_away
            implied_total = implied_home + implied_draw + implied_away
            if implied_total > 0:
                implied_home /= implied_total
                implied_draw /= implied_total
                implied_away /= implied_total
                predicted_prob = implied_home if predicted_winner == 'home' else implied_away
                if predicted_prob - implied_draw <= 0.07:
                    euro_support_bucket = 'draw_guarded'
        scenario_name = ''
        if predicted_winner == 'home' and handicap_depth_bucket in {'level_ball', 'level_shallow'} and strength_value is not None and strength_value >= 18:
            scenario_name = 'strong_home_shallow_line'
        elif predicted_winner == 'away' and handicap_depth_bucket in {'level_ball', 'level_shallow'}:
            if strength_value is not None and strength_value <= -12:
                scenario_name = 'away_shallow_market_doubt'
        elif predicted_winner in {'home', 'away'} and handicap_depth_bucket in {'level_ball', 'level_shallow'}:
            if strength_value is not None and abs(strength_value) <= 8 and euro_support_bucket in {'draw_guarded', 'draw_live', 'draw_soft'}:
                scenario_name = 'balanced_draw_guard'
        elif predicted_winner == 'draw' and handicap_depth_bucket in {'level_ball', 'level_shallow'}:
            if strength_value is not None and abs(strength_value) <= 8:
                scenario_name = 'balanced_draw_guard'
        return {
            'predicted_winner': predicted_winner,
            'predicted_outcome_label': predicted_outcome_label,
            'handicap_depth_bucket': handicap_depth_bucket,
            'strength_gap_bucket': strength_gap_bucket,
            'euro_support_bucket': euro_support_bucket,
            'scenario_name': scenario_name,
            'asian_line': self._safe_float(asian_line),
            'strength_diff': strength_value,
            'stratified_key': f'{predicted_winner}:{handicap_depth_bucket}' if predicted_winner else '',
            'three_layer_key': f'{predicted_winner}:{handicap_depth_bucket}:{euro_support_bucket}' if predicted_winner else '',
        }

    def apply_review_outcome_adjustment(
        self,
        *,
        final_probabilities: Dict[str, float],
        strength_diff: Any,
        asian_handicap: Optional[Dict[str, Any]],
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        probabilities = self._normalize_prob_triplet(final_probabilities or {})
        ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
        top_key = ranked[0][0] if ranked else 'draw'
        label_map = {'home_win': '主胜', 'draw': '平局', 'away_win': '客胜'}
        predicted_outcome_label = label_map.get(top_key, '平局')
        odds_payload = dict(current_odds or {})
        if isinstance(asian_handicap, dict) and '亚值' not in odds_payload:
            odds_payload['亚值'] = asian_handicap
        context = self.build_three_layer_runtime_context(
            predicted_outcome_label=predicted_outcome_label,
            strength_diff=strength_diff,
            current_odds=odds_payload,
            review_learning=review_learning,
        )
        learning = review_learning if isinstance(review_learning, dict) else {}
        league_review = learning.get('league_review') if isinstance(learning.get('league_review'), dict) else {}
        stratified_review = learning.get('outcome_stratified_review') if isinstance(learning.get('outcome_stratified_review'), dict) else {}
        three_layer_review = learning.get('three_layer_outcome_review') if isinstance(learning.get('three_layer_outcome_review'), dict) else {}
        matched_stratified = stratified_review.get(context.get('stratified_key')) if isinstance(stratified_review.get(context.get('stratified_key')), dict) else {}
        matched_three_layer = three_layer_review.get(context.get('three_layer_key')) if isinstance(three_layer_review.get(context.get('three_layer_key')), dict) else {}
        signals: List[str] = []
        draw_shift = 0.0
        away_shift = 0.0
        home_shift = 0.0
        top_prob = float(probabilities.get(top_key) or 0.0)
        runner_up = float(sorted((value for key, value in probabilities.items() if key != top_key), reverse=True)[0] if len(probabilities) > 1 else 0.0)
        top_lead = top_prob - runner_up
        home_bias_gate = {'qualified': False, 'evidence': []}
        if top_key == 'home_win' and '主胜偏置' in ' '.join(str(item) for item in (league_review.get('league_tags') or [])):
            if top_lead < 0.14:
                home_bias_gate['evidence'].append('limited_probability_edge')
            if context.get('strength_diff') is None or abs(float(context.get('strength_diff') or 0.0)) < 14:
                home_bias_gate['evidence'].append('limited_strength_gap')
            if context.get('handicap_depth_bucket') in {'level_ball', 'level_shallow'}:
                home_bias_gate['evidence'].append('shallow_market')
            if top_lead >= 0.18:
                home_bias_gate['qualified'] = True
            if home_bias_gate['qualified']:
                draw_shift = max(draw_shift, 0.01)
                away_shift = max(away_shift, 0.008)
                signals.append('review-home-bias-correction')
        if '平局防守不足' in ' '.join(str(item) for item in (league_review.get('league_tags') or [])) and top_key in {'home_win', 'away_win'}:
            draw_shift = max(draw_shift, 0.01)
            signals.append('review-draw-gap-correction')
        if '客胜冷门敏感度不足' in ' '.join(str(item) for item in (league_review.get('league_tags') or [])) and top_key == 'home_win':
            away_shift = max(away_shift, 0.008)
            signals.append('review-away-upset-correction')
        if matched_stratified:
            draw_shift = max(draw_shift, float(matched_stratified.get('recommended_draw_shift') or 0.0))
            if top_key == 'home_win':
                away_shift = max(away_shift, float(matched_stratified.get('recommended_upset_shift') or 0.0))
            elif top_key == 'away_win':
                home_shift = max(home_shift, float(matched_stratified.get('recommended_upset_shift') or 0.0))
            if float(matched_stratified.get('recommended_draw_shift') or 0.0) > 0 or float(matched_stratified.get('recommended_upset_shift') or 0.0) > 0:
                signals.append('review-stratified-handicap-strength-correction')
        if matched_three_layer:
            draw_shift = max(draw_shift, float(matched_three_layer.get('recommended_draw_shift') or 0.0))
            if top_key == 'home_win':
                away_shift = max(away_shift, float(matched_three_layer.get('recommended_upset_shift') or 0.0))
            elif top_key == 'away_win':
                home_shift = max(home_shift, float(matched_three_layer.get('recommended_upset_shift') or 0.0))
        if context.get('scenario_name'):
            signals.append(f"review-scenario-{context.get('scenario_name')}")
        heuristic_applied = False
        if context.get('scenario_name') == 'strong_home_shallow_line' and top_key == 'home_win':
            draw_shift = max(draw_shift, 0.012)
            away_shift = max(away_shift, 0.008)
            heuristic_applied = not bool(matched_three_layer)
        elif context.get('scenario_name') == 'away_shallow_market_doubt' and top_key == 'away_win':
            draw_shift = max(draw_shift, 0.014)
            home_shift = max(home_shift, 0.008)
            heuristic_applied = not bool(matched_three_layer)
        elif context.get('scenario_name') == 'balanced_draw_guard' and top_key in {'home_win', 'away_win'}:
            draw_shift = max(draw_shift, 0.014)
            heuristic_applied = not bool(matched_three_layer)
        if heuristic_applied:
            signals.append('review-three-layer-heuristic')
        adjusted = dict(probabilities)
        if top_key == 'home_win':
            adjusted = self._shift_from_side_to_targets(adjusted, from_key='home_win', draw_shift=draw_shift, away_shift=away_shift)
        elif top_key == 'away_win':
            adjusted = self._shift_from_side_to_targets(adjusted, from_key='away_win', draw_shift=draw_shift, home_shift=home_shift)
        diag = {
            'applied': adjusted != probabilities,
            'signals': list(dict.fromkeys(signals)),
            'reason': 'applied' if adjusted != probabilities else 'three_layer_evaluated_no_adjustment',
            'three_layer_evaluated': True,
            'home_bias_gate': home_bias_gate,
            'stratified_review': {
                'handicap_depth_bucket': context.get('handicap_depth_bucket'),
                'strength_gap_bucket': context.get('strength_gap_bucket'),
                'matched_key': context.get('stratified_key'),
                'matched': matched_stratified,
            },
            'three_layer_context': context,
        }
        return adjusted, diag

    @staticmethod
    def _parse_score_components(score: str) -> Optional[Dict[str, int]]:
        match = re.match(r'^(\d+)\s*-\s*(\d+)$', str(score).strip())
        if not match:
            return None
        home_goals = int(match.group(1))
        away_goals = int(match.group(2))
        return {
            'home_goals': home_goals,
            'away_goals': away_goals,
            'total_goals': home_goals + away_goals,
            'goal_diff': home_goals - away_goals,
        }

    @staticmethod
    def _clamp_score_factor(factor: float) -> float:
        return max(0.82, min(1.18, float(factor or 1.0)))

    @classmethod
    def _score_matches_outcome(cls, score: str, predicted_outcome_label: str) -> bool:
        components = cls._parse_score_components(score)
        if not components:
            return False
        goal_diff = int(components['goal_diff'])
        if predicted_outcome_label == '主胜':
            return goal_diff > 0
        if predicted_outcome_label == '客胜':
            return goal_diff < 0
        if predicted_outcome_label == '平局':
            return goal_diff == 0
        return True

    @classmethod
    def _is_low_conservative_template(cls, score: str, predicted_outcome_label: str) -> bool:
        components = cls._parse_score_components(score)
        if not components:
            return False
        total_goals = int(components['total_goals'])
        if predicted_outcome_label == '平局':
            return total_goals <= 2
        winner_goals = int(components['home_goals']) if predicted_outcome_label == '主胜' else int(components['away_goals'])
        return winner_goals <= 2 and total_goals <= 3

    @classmethod
    def _apply_review_score_correction(
        cls,
        adjusted: Dict[str, float],
        *,
        predicted_outcome_label: str,
        score_bias: Dict[str, Any],
        open_match: bool,
    ) -> bool:
        conservative_home = float(score_bias.get('conservative_home_win_rate') or 0.0)
        conservative_away = float(score_bias.get('conservative_away_win_rate') or 0.0)
        low_total = float(score_bias.get('low_total_underestimate_rate') or 0.0)
        home_ceiling = float(score_bias.get('home_goal_ceiling_underestimate_rate') or 0.0)
        away_ceiling = float(score_bias.get('away_goal_ceiling_underestimate_rate') or 0.0)
        home_boost = float(score_bias.get('recommended_home_goal_boost') or 0.0)
        away_boost = float(score_bias.get('recommended_away_goal_boost') or 0.0)
        changed = False

        for score, base_prob in list(adjusted.items()):
            components = cls._parse_score_components(score)
            if not components or float(base_prob or 0.0) <= 0:
                continue
            factor = 1.0
            home_goals = int(components['home_goals'])
            away_goals = int(components['away_goals'])
            total_goals = int(components['total_goals'])

            if predicted_outcome_label == '主胜':
                signal_strength = max(conservative_home, home_ceiling, low_total)
                if signal_strength >= 0.18 or home_boost >= 0.03:
                    if score == '1-0':
                        factor *= 1.0 - min(0.18, 0.05 + conservative_home * 0.16 + low_total * 0.08)
                    elif score == '2-0':
                        factor *= 1.0 - min(0.14, 0.03 + max(conservative_home, home_ceiling) * 0.12)
                    elif score == '2-1':
                        factor *= 1.0 + min(0.18, 0.04 + max(conservative_home, low_total) * 0.12 + home_boost * 0.7)
                    elif score == '3-0':
                        factor *= 1.0 + min(0.18, 0.03 + max(home_ceiling, home_boost) * 0.14)
                    elif score == '3-1':
                        factor *= 1.0 + min(0.18, 0.04 + max(conservative_home, home_ceiling, low_total) * 0.14 + home_boost * 0.6)
                    elif home_goals >= 3 and total_goals >= 4:
                        factor *= 1.0 + min(0.12, 0.02 + home_ceiling * 0.08 + low_total * 0.05)
            elif predicted_outcome_label == '客胜':
                signal_strength = max(conservative_away, away_ceiling, low_total)
                if signal_strength >= 0.18 or away_boost >= 0.03:
                    if score == '0-1':
                        factor *= 1.0 - min(0.18, 0.05 + conservative_away * 0.16 + low_total * 0.08)
                    elif score == '0-2':
                        factor *= 1.0 + min(0.16, 0.04 + max(conservative_away, away_ceiling) * 0.12 + away_boost * 0.7)
                    elif score == '1-2':
                        factor *= 1.0 + min(0.18, 0.05 + max(conservative_away, low_total) * 0.12 + away_boost * 0.7)
                    elif score == '0-3':
                        factor *= 1.0 + min(0.18, 0.04 + max(away_ceiling, away_boost) * 0.16)
                    elif away_goals >= 3 and total_goals >= 3:
                        factor *= 1.0 + min(0.12, 0.02 + away_ceiling * 0.08 + low_total * 0.05)
            elif predicted_outcome_label == '平局' and open_match and low_total >= 0.2:
                if score == '0-0':
                    factor *= 1.0 - min(0.16, 0.04 + low_total * 0.12)
                elif score == '1-1':
                    factor *= 1.0 - min(0.1, 0.02 + low_total * 0.06)
                elif score == '2-2':
                    factor *= 1.0 + min(0.18, 0.05 + low_total * 0.12)
                elif total_goals >= 5:
                    factor *= 1.0 + min(0.12, 0.02 + low_total * 0.08)

            factor = cls._clamp_score_factor(factor)
            if abs(factor - 1.0) > 1e-9:
                adjusted[score] = float(base_prob or 0.0) * factor
                changed = True
        return changed

    @classmethod
    def _build_score_selection_profile(
        cls,
        scores: List[Tuple[str, float]],
        *,
        predicted_outcome_label: str,
    ) -> Dict[str, Any]:
        totals = set()
        goal_diffs = set()
        low_template_count = 0
        winner_goals = []
        total_goal_values = []
        for score, _prob in scores:
            components = cls._parse_score_components(score)
            if not components:
                continue
            total_goals = int(components['total_goals'])
            totals.add(total_goals)
            goal_diffs.add(abs(int(components['goal_diff'])))
            total_goal_values.append(total_goals)
            if predicted_outcome_label == '主胜':
                winner_goals.append(int(components['home_goals']))
            elif predicted_outcome_label == '客胜':
                winner_goals.append(int(components['away_goals']))
            else:
                winner_goals.append(int(components['home_goals']))
            if cls._is_low_conservative_template(score, predicted_outcome_label):
                low_template_count += 1
        score_count = len(scores)
        all_low_templates = bool(score_count and low_template_count == score_count)
        same_total_layer = len(totals) <= 1 if score_count else False
        same_goal_margin = len(goal_diffs) <= 1 if score_count else False
        return {
            'score_count': score_count,
            'totals': totals,
            'goal_diffs': goal_diffs,
            'all_low_templates': all_low_templates,
            'same_total_layer': same_total_layer,
            'same_goal_margin': same_goal_margin,
            'needs_expansion': bool(score_count >= 2 and (same_total_layer or same_goal_margin or all_low_templates)),
            'max_winner_goals': max(winner_goals) if winner_goals else 0,
            'max_total_goals': max(total_goal_values) if total_goal_values else 0,
            'avg_total_goals': (sum(total_goal_values) / len(total_goal_values)) if total_goal_values else 0.0,
        }

    @classmethod
    def _pick_coverage_expansion_candidate(
        cls,
        ranked_scores: List[Tuple[str, float]],
        selected_scores: List[Tuple[str, float]],
        *,
        predicted_outcome_label: str,
        expansion_strength: float,
    ) -> Tuple[Optional[Tuple[str, float]], Dict[str, Any]]:
        profile = cls._build_score_selection_profile(selected_scores, predicted_outcome_label=predicted_outcome_label)
        if not profile.get('needs_expansion'):
            return None, profile
        selected_names = {score for score, _prob in selected_scores}
        min_selected_prob = min((float(prob or 0.0) for _score, prob in selected_scores), default=0.0)
        threshold_ratio = max(0.42, 0.62 - float(expansion_strength or 0.0) * 2.5)
        threshold = min_selected_prob * threshold_ratio
        best_candidate: Optional[Tuple[str, float]] = None
        best_value = -1.0

        for index, (score, prob) in enumerate(ranked_scores):
            if score in selected_names or float(prob or 0.0) < threshold:
                continue
            components = cls._parse_score_components(score)
            if not components:
                continue
            total_goals = int(components['total_goals'])
            goal_diff = abs(int(components['goal_diff']))
            winner_goals = int(components['home_goals']) if predicted_outcome_label == '主胜' else int(components['away_goals']) if predicted_outcome_label == '客胜' else int(components['home_goals'])
            bonus = 0.0
            if total_goals not in profile['totals']:
                bonus += 1.4
            if goal_diff not in profile['goal_diffs']:
                bonus += 1.1
            if profile['all_low_templates'] and not cls._is_low_conservative_template(score, predicted_outcome_label):
                bonus += 1.6
            if winner_goals > int(profile['max_winner_goals'] or 0):
                bonus += 1.1
            if total_goals > int(profile['max_total_goals'] or 0):
                bonus += 0.8
            if abs(total_goals - float(profile['avg_total_goals'] or 0.0)) <= 2:
                bonus += 0.4
            elif abs(total_goals - float(profile['avg_total_goals'] or 0.0)) >= 4:
                bonus -= 0.25
            candidate_value = bonus + float(prob or 0.0) * 8.0 - index * 0.01
            if bonus >= 1.5 and candidate_value > best_value:
                best_candidate = (score, float(prob or 0.0))
                best_value = candidate_value
        return best_candidate, profile

    def rerank_top_scores(
        self,
        top_scores: List[Tuple[str, float]],
        predicted_outcome_label: str,
        *,
        ranked_probabilities: Optional[List[Tuple[str, float]]] = None,
        home_lambda: float,
        away_lambda: float,
        over_under: Optional[Dict[str, Any]],
        strength_diff: Any,
        confidence: float,
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]],
        return_diag: bool = False,
        limit: int = 3,
    ) -> Any:
        scores = [(str(score), float(prob or 0.0)) for score, prob in (top_scores or []) if str(score).strip()]
        ranked_outcomes = [
            (str(label), float(prob or 0.0))
            for label, prob in (ranked_probabilities or [])
            if str(label).strip()
        ]
        primary_probability = 0.0
        secondary_probability = 0.0
        secondary_gap = 1.0
        if ranked_outcomes:
            primary_probability = float(ranked_outcomes[0][1] or 0.0)
            if len(ranked_outcomes) > 1:
                secondary_probability = float(ranked_outcomes[1][1] or 0.0)
                secondary_gap = max(0.0, primary_probability - secondary_probability)
        double_pick = bool(
            ranked_outcomes
            and len(ranked_outcomes) > 1
            and secondary_probability >= 0.28
            and secondary_gap <= 0.06
        )
        allowed_outcomes = [predicted_outcome_label]
        if double_pick:
            secondary_label = str(ranked_outcomes[1][0]).strip()
            if secondary_label and secondary_label not in allowed_outcomes:
                allowed_outcomes.append(secondary_label)
        directional_scores = [
            (score, prob)
            for score, prob in scores
            if any(self._score_matches_outcome(score, outcome) for outcome in allowed_outcomes)
        ]
        adjusted = dict(directional_scores)
        context = self.build_three_layer_runtime_context(
            predicted_outcome_label=predicted_outcome_label,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        learning = review_learning if isinstance(review_learning, dict) else {}
        score_bias = learning.get('score_bias') if isinstance(learning.get('score_bias'), dict) else {}
        line = self._safe_float((over_under or {}).get('line')) if isinstance(over_under, dict) else None
        over_prob = self._safe_float((over_under or {}).get('over')) if isinstance(over_under, dict) else None
        under_prob = self._safe_float((over_under or {}).get('under')) if isinstance(over_under, dict) else None
        open_match = bool((line is not None and line >= 2.75) or (over_prob is not None and under_prob is not None and over_prob > under_prob) or (home_lambda + away_lambda >= 2.8))
        signals: List[str] = []
        filtered_out_count = len(scores) - len(directional_scores)
        if filtered_out_count > 0:
            signals.append('score-direction-filter')
        if double_pick:
            signals.append('score-double-pick')
        if context.get('scenario_name') == 'strong_home_shallow_line' and predicted_outcome_label == '主胜':
            for score, factor in {'1-0': 0.92, '2-0': 0.88, '3-0': 0.8, '2-1': 1.16, '3-1': 1.08}.items():
                if score in adjusted:
                    adjusted[score] *= factor
            signals.append('score-three-layer-strong_home_shallow_line')
        low_total_penalty = float(score_bias.get('recommended_low_total_penalty') or 0.0)
        if predicted_outcome_label == '主胜' and open_match:
            for score, factor in {
                '1-0': max(0.78, 0.88 - low_total_penalty),
                '2-0': max(0.84, 0.94 - low_total_penalty * 0.5),
                '2-1': 1.07 + low_total_penalty,
                '3-1': 1.05 + low_total_penalty * 0.7,
                '3-0': 0.94,
            }.items():
                if score in adjusted:
                    adjusted[score] *= factor
            signals.append('score-template-cap')
        if predicted_outcome_label == '平局' and open_match:
            for score, factor in {'0-0': 0.82, '1-1': 0.9, '2-2': 1.14, '3-3': 1.04}.items():
                if score in adjusted:
                    adjusted[score] *= factor
            signals.append('score-template-cap')
        low_tempo_guard = bool(
            line is not None and line <= 2.75 and over_prob is not None and under_prob is not None and under_prob - over_prob >= 0.06
        )
        shallow_market_guard = bool(
            context.get('handicap_depth_bucket') in {'level_ball', 'level_shallow'}
            and float(confidence or 0.0) <= 0.52
            and primary_probability <= 0.52
            and secondary_gap <= 0.08
        )
        if low_tempo_guard:
            if predicted_outcome_label == '主胜':
                for score, factor in {'3-1': 0.86, '3-0': 0.9, '2-1': 0.96, '1-0': 1.04}.items():
                    if score in adjusted:
                        adjusted[score] *= factor
            elif predicted_outcome_label == '客胜':
                for score, factor in {'1-3': 0.86, '0-3': 0.9, '1-2': 0.96, '0-1': 1.04}.items():
                    if score in adjusted:
                        adjusted[score] *= factor
            elif predicted_outcome_label == '平局' and not open_match:
                for score, factor in {'2-2': 0.84, '3-3': 0.76, '1-1': 1.04, '0-0': 1.03}.items():
                    if score in adjusted:
                        adjusted[score] *= factor
            signals.append('score-market-low-tempo-guard')
        if shallow_market_guard:
            if predicted_outcome_label == '主胜':
                for score, factor in {'3-0': 0.84, '3-1': 0.88, '2-0': 0.95, '2-1': 1.03, '1-0': 1.04}.items():
                    if score in adjusted:
                        adjusted[score] *= factor
            elif predicted_outcome_label == '客胜':
                for score, factor in {'0-3': 0.84, '1-3': 0.88, '0-2': 0.95, '1-2': 1.03, '0-1': 1.04}.items():
                    if score in adjusted:
                        adjusted[score] *= factor
            signals.append('score-market-shallow-cap')
        if self._apply_review_score_correction(
            adjusted,
            predicted_outcome_label=predicted_outcome_label,
            score_bias=score_bias,
            open_match=open_match,
        ):
            signals.append('score-review-conservative-correction')
        ranked_adjusted = sorted(self._rescale_score_list(list(adjusted.items())), key=lambda item: item[1], reverse=True)
        target_limit = max(2 if double_pick else 1, int(limit or 3))
        reranked = list(ranked_adjusted[:target_limit])
        coverage_profile = self._build_score_selection_profile(reranked, predicted_outcome_label=predicted_outcome_label)
        coverage_expansion_strength = float(score_bias.get('recommended_score_coverage_expansion') or 0.0)
        coverage_expansion_rate = float(score_bias.get('coverage_expansion_rate') or 0.0)
        coverage_expansion_applied = False
        coverage_candidate = None
        if (
            target_limit >= 3
            and coverage_profile.get('needs_expansion')
            and (coverage_expansion_rate >= 0.18 or coverage_expansion_strength >= 0.03)
        ):
            coverage_candidate, coverage_profile = self._pick_coverage_expansion_candidate(
                ranked_adjusted,
                reranked,
                predicted_outcome_label=predicted_outcome_label,
                expansion_strength=coverage_expansion_strength,
            )
            if coverage_candidate:
                if len(reranked) >= target_limit:
                    reranked = reranked[:-1]
                reranked.append(coverage_candidate)
                reranked = sorted(self._rescale_score_list(reranked), key=lambda item: item[1], reverse=True)[:target_limit]
                coverage_expansion_applied = True
                signals.append('score-review-coverage-expansion')
        diag = {
            'applied': reranked != scores[: len(reranked)],
            'signals': list(dict.fromkeys(signals)),
            'scenario_name': context.get('scenario_name'),
            'open_match': open_match,
            'confidence': float(confidence or 0.0),
            'filtered_out_count': filtered_out_count,
            'allowed_outcomes': allowed_outcomes,
            'double_pick': double_pick,
            'secondary_gap': secondary_gap,
            'secondary_probability': secondary_probability,
            'coverage_profile': {
                'same_total_layer': bool(coverage_profile.get('same_total_layer')),
                'same_goal_margin': bool(coverage_profile.get('same_goal_margin')),
                'all_low_templates': bool(coverage_profile.get('all_low_templates')),
                'needs_expansion': bool(coverage_profile.get('needs_expansion')),
            },
            'coverage_expansion_applied': coverage_expansion_applied,
            'coverage_expansion_candidate': coverage_candidate[0] if coverage_candidate else None,
        }
        if return_diag:
            return reranked, diag
        return reranked

    def apply_three_layer_total_goals_adjustment(
        self,
        total_goals: Dict[str, Any],
        *,
        predicted_outcome_label: str,
        strength_diff: Any,
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]],
        total_lambda: float,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(total_goals, dict) or not total_goals.get('available'):
            return total_goals, {'applied': False, 'reason': 'unavailable_total_goals'}
        buckets = {
            str(key): float(value or 0.0)
            for key, value in (total_goals.get('buckets') or {}).items()
        }
        context = self.build_three_layer_runtime_context(
            predicted_outcome_label=predicted_outcome_label,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        signals: List[str] = []
        if predicted_outcome_label == '平局' and context.get('scenario_name') == 'balanced_draw_guard' and float(total_lambda or 0.0) <= 2.4:
            take = min(0.02, buckets.get('4', 0.0) * 0.18)
            buckets['4'] = max(0.0, buckets.get('4', 0.0) - take)
            buckets['1'] = buckets.get('1', 0.0) + take * 0.7
            buckets['2'] = buckets.get('2', 0.0) + take * 0.3
            signals.append('total-goals-three-layer-draw_market_balance')
        total = sum(buckets.values())
        if total > 0:
            for key in list(buckets.keys()):
                buckets[key] = buckets[key] / total
        top_totals = sorted(((key, value) for key, value in buckets.items()), key=lambda item: item[1], reverse=True)[:3]
        adjusted = dict(total_goals)
        adjusted['buckets'] = {key: round(float(value), 6) for key, value in buckets.items()}
        adjusted['top_totals'] = [{'total': key, 'prob': round(float(value), 4)} for key, value in top_totals]
        diag = {
            'applied': bool(signals),
            'signals': signals,
            'scenario_name': context.get('scenario_name'),
        }
        return adjusted, diag

    def build_prediction_result(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str,
        match_time: str,
        ranked_probabilities: List[Tuple[str, float]],
        confidence: float,
        top_scores: List[Tuple[str, float]],
        total_goals: Dict[str, Any],
        home_lambda: float,
        away_lambda: float,
        over_under: Dict[str, Any],
        staking: Dict[str, Any],
        strength_diff: float,
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
        upset_potential: Dict[str, Any],
        match_intelligence: Dict[str, Any],
        historical_odds_reference: Dict[str, Any],
        fusion_result: Dict[str, Any],
        final_probabilities: Dict[str, float],
        applied_model_weights: Dict[str, Any],
        realtime: Dict[str, Any],
        analysis_context: Dict[str, Any],
        runtime_profile: Dict[str, Any],
        retrieved_memory: Optional[Dict[str, Any]] = None,
        current_odds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        league_name = self.league_config[league_code]['name']
        memory_explanation = self.build_retrieved_memory_explanation(retrieved_memory)
        live_betting_advice = ''
        if isinstance(retrieved_memory, dict):
            summary = retrieved_memory.get('summary') if isinstance(retrieved_memory.get('summary'), dict) else {}
            live_market_followup = summary.get('live_market_followup') if isinstance(summary.get('live_market_followup'), dict) else {}
            live_betting_advice = str(live_market_followup.get('advice') or '').strip()
        return {
            'match_id': str(match_id or ''),
            'home_team': home_team,
            'away_team': away_team,
            'league_code': league_code,
            'league_name': league_name,
            'match_date': match_date,
            'match_time': match_time,
            'prediction': ranked_probabilities[0][0] if ranked_probabilities else '平局',
            'confidence': confidence,
            'all_probabilities': dict(ranked_probabilities),
            'top_scores': top_scores,
            'total_goals': total_goals,
            'expected_goals': {
                'home': home_lambda,
                'away': away_lambda,
                'total': home_lambda + away_lambda,
            },
            'over_under': over_under,
            'staking': staking,
            'league_over_under_learning': realtime.get('context_applied', {}).get('league_over_under_learning'),
            'strength_diff': strength_diff,
            'home_strength': home_strength,
            'away_strength': away_strength,
            'upset_potential': upset_potential,
            'match_intelligence': match_intelligence,
            'historical_odds_reference': historical_odds_reference,
            'model_predictions': fusion_result['all_models'],
            'final_probabilities': final_probabilities,
            'applied_model_weights': applied_model_weights,
            'realtime': realtime,
            'analysis_context': analysis_context,
            'retrieved_memory': retrieved_memory or {},
            'retrieved_memory_explanation': memory_explanation,
            'live_betting_advice': live_betting_advice,
            'market_snapshot': self.build_market_snapshot(current_odds),
            'runtime_profile': runtime_profile,
            'timestamp': datetime.now().isoformat(),
        }
