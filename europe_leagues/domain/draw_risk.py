"""模块说明：基于历史完赛样本，评估当前盘口的"走平/不破盘"风险。

核心用途：针对"高平赔 + 大让球盘"这类容易被诱导重仓主胜、实则走平率偏高的盘口，
用 prediction_memory_odds_samples.json 里的真实完赛样本回测同档走平率，
给出与基线对比的风险分级与下注建议。被预测主链每场自动调用。

设计约束：纯计算、对缺失数据与异常完全容错，任何分支都不抛异常。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from runtime.memory_samples import load_prediction_memory_samples

# 判定"高平赔 + 大让球"相似盘的默认门槛
DEFAULT_DRAW_ODDS_FLOOR = 4.0
DEFAULT_HANDICAP_FLOOR = 1.0
# 窄带：贴近目标盘口的让球带宽（±）
HANDICAP_BAND = 0.5
# 触发风险分级所需的最小同档样本量
MIN_SAMPLE_FOR_SIGNAL = 5


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ''):
            return None
        return float(value)
    except Exception:
        return None


def _draw_odds(record: Dict[str, Any]) -> Optional[float]:
    euro = record.get('欧赔') if isinstance(record.get('欧赔'), dict) else {}
    consensus = euro.get('consensus') if isinstance(euro.get('consensus'), dict) else {}
    median = consensus.get('final_median') if isinstance(consensus.get('final_median'), dict) else {}
    value = _to_float(median.get('draw'))
    if value is not None:
        return value
    final = euro.get('final') if isinstance(euro.get('final'), dict) else {}
    return _to_float(final.get('draw'))


def _handicap_abs(record: Dict[str, Any]) -> Optional[float]:
    asia = record.get('亚值') if isinstance(record.get('亚值'), dict) else {}
    for key in ('final', 'initial'):
        block = asia.get(key) if isinstance(asia.get(key), dict) else {}
        hv = _to_float(block.get('handicap_value'))
        if hv is not None:
            return abs(hv)
    consensus = asia.get('consensus') if isinstance(asia.get('consensus'), dict) else {}
    hv = _to_float(consensus.get('final_handicap'))
    return abs(hv) if hv is not None else None


def _is_draw(record: Dict[str, Any]) -> bool:
    return str(record.get('actual_result') or '').strip() == '平局'


def _completed_samples(base_dir: Optional[str]) -> List[Dict[str, Any]]:
    payload = load_prediction_memory_samples(base_dir=base_dir)
    records_by_league = payload.get('records_by_league') if isinstance(payload, dict) else {}
    if not isinstance(records_by_league, dict):
        return []
    out: List[Dict[str, Any]] = []
    for league_records in records_by_league.values():
        if not isinstance(league_records, list):
            continue
        for record in league_records:
            if not isinstance(record, dict):
                continue
            if str(record.get('actual_result') or '').strip() and str(record.get('actual_score') or '').strip():
                out.append(record)
    return out


def _rate(matched: List[Dict[str, Any]]) -> float:
    if not matched:
        return 0.0
    return sum(1 for r in matched if _is_draw(r)) / len(matched)


def _classify(sample_draw_rate: float, baseline: float, sample_size: int) -> Dict[str, str]:
    if sample_size < MIN_SAMPLE_FOR_SIGNAL:
        return {
            'level': 'unknown',
            'label': '◽样本不足·走平率不可靠',
            'advice': '同档历史样本不足，无法给出可靠走平判断，按常规方向处理即可。',
        }
    lift = sample_draw_rate - baseline
    if sample_draw_rate >= 0.40 and lift >= 0.08:
        return {
            'level': 'high',
            'label': '⚠️高平赔+大让球·历史走平率显著偏高',
            'advice': '同档历史走平率明显高于基线，主胜不宜重仓；建议走受让方防平/防不破盘，或回避。',
        }
    if sample_draw_rate >= 0.33 and lift >= 0.03:
        return {
            'level': 'elevated',
            'label': '⚠️走平率偏高·留意不破盘',
            'advice': '同档走平率略高于基线，主胜可保留但降档，注意平局/小胜走盘风险。',
        }
    return {
        'level': 'normal',
        'label': '◽走平率接近基线·无额外防平信号',
        'advice': '同档走平率与基线接近，无需额外防平，按方向常规处理。',
    }


def assess_draw_risk(
    *,
    current_odds: Optional[Dict[str, Any]],
    final_probabilities: Optional[Dict[str, float]] = None,
    base_dir: Optional[str] = None,
    draw_odds_floor: float = DEFAULT_DRAW_ODDS_FLOOR,
    handicap_floor: float = DEFAULT_HANDICAP_FLOOR,
) -> Dict[str, Any]:
    """评估当前盘口的走平风险，返回结构化诊断；任何异常都降级为 available=False。"""
    try:
        market = current_odds if isinstance(current_odds, dict) else {}
        this_draw_odds = _draw_odds(market)
        this_handicap = _handicap_abs(market)

        if this_draw_odds is None or this_handicap is None:
            return {
                'available': False,
                'reason': 'missing_market_line',
                'this_draw_odds': this_draw_odds,
                'this_handicap': this_handicap,
            }

        is_high_draw_big_handicap = (
            this_draw_odds >= draw_odds_floor and this_handicap >= handicap_floor
        )

        samples = _completed_samples(base_dir)
        total = len(samples)
        baseline = _rate(samples)

        # 同档（门槛）样本
        cohort = [
            r for r in samples
            if (_draw_odds(r) or 0.0) >= draw_odds_floor and (_handicap_abs(r) or 0.0) >= handicap_floor
        ]
        # 窄带（贴近本场让球）样本
        band = [
            r for r in samples
            if (_draw_odds(r) or 0.0) >= draw_odds_floor
            and abs((_handicap_abs(r) or -99.0) - this_handicap) <= HANDICAP_BAND
        ]

        cohort_rate = _rate(cohort)
        band_rate = _rate(band)
        # 优先用窄带评估（更贴近本场），样本不足时回退到同档
        eval_pool = band if len(band) >= MIN_SAMPLE_FOR_SIGNAL else cohort
        eval_rate = _rate(eval_pool)

        classification = _classify(eval_rate, baseline, len(eval_pool))

        model_draw = None
        if isinstance(final_probabilities, dict):
            model_draw = _to_float(final_probabilities.get('draw'))
            if model_draw is not None and model_draw <= 1:
                model_draw = round(model_draw * 100, 1)

        examples = [
            {
                'match': f"{r.get('home_team')} vs {r.get('away_team')}",
                'draw_odds': _draw_odds(r),
                'handicap': _handicap_abs(r),
                'score': r.get('actual_score'),
                'result': r.get('actual_result'),
            }
            for r in eval_pool[:6]
        ]

        return {
            'available': True,
            'is_high_draw_big_handicap': is_high_draw_big_handicap,
            'this_draw_odds': this_draw_odds,
            'this_handicap': this_handicap,
            'model_draw_pct': model_draw,
            'baseline_draw_rate': round(baseline * 100, 1),
            'baseline_sample_size': total,
            'cohort_draw_rate': round(cohort_rate * 100, 1),
            'cohort_sample_size': len(cohort),
            'band_draw_rate': round(band_rate * 100, 1),
            'band_sample_size': len(band),
            'eval_draw_rate': round(eval_rate * 100, 1),
            'eval_sample_size': len(eval_pool),
            'eval_basis': 'handicap_band' if len(band) >= MIN_SAMPLE_FOR_SIGNAL else 'cohort_floor',
            'risk_level': classification['level'],
            'risk_label': classification['label'],
            'advice': classification['advice'],
            'examples': examples,
        }
    except Exception as exc:  # 永不影响主预测链
        return {'available': False, 'reason': f'error:{exc}'}


__all__ = ['assess_draw_risk']
