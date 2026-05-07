"""模块说明：负责胜平负概率后处理、比分分布、大小球和凯利结果装配。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


OUTCOME_LABELS = (
    ('主胜', 'home_win', 'home'),
    ('平局', 'draw', 'draw'),
    ('客胜', 'away_win', 'away'),
)


class PredictionPostprocessService:
    def __init__(self, league_config: Dict[str, Dict[str, Any]]):
        self.league_config = league_config

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

    def build_prediction_result(
        self,
        *,
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
        current_odds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        league_name = self.league_config[league_code]['name']
        return {
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
            'market_snapshot': self.build_market_snapshot(current_odds),
            'runtime_profile': runtime_profile,
            'timestamp': datetime.now().isoformat(),
        }
