"""模块说明：负责把预测结果写回 teams_2025-26.md 等文本事实源。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from storage.teams_md import TeamsMarkdownStore


def _normalize_team_name(value: str) -> str:
    return (value or '').strip().replace(' ', '')


def format_upset_note(upset: Any) -> str:
    if isinstance(upset, str):
        return f"爆冷:{upset or '-'}".strip()
    if not isinstance(upset, dict):
        return '爆冷:-'

    level = (upset.get('level') or '').strip() or '-'
    idx = upset.get('index')
    idx_text = f"({int(round(float(idx)))})" if isinstance(idx, (int, float)) else ''
    parts = [f'爆冷:{level}{idx_text}']

    mismatch = upset.get('handicap_strength_mismatch')
    if isinstance(mismatch, dict) and mismatch.get('mismatch_detected'):
        mismatch_level = (mismatch.get('mismatch_level') or '').strip() or '是'
        parts.append(f'错配:{mismatch_level}')
        suggestion = (mismatch.get('suggested_outcome') or '').strip()
        if suggestion:
            parts.append(f'建议:{suggestion}')
        factors = mismatch.get('warning_factors') or []
        if isinstance(factors, list) and factors:
            compact = ';'.join(str(item).strip() for item in factors[:2] if str(item).strip())
            if compact:
                parts.append(f'因子:{compact}')

    knowledge = upset.get('case_knowledge')
    if isinstance(knowledge, dict) and knowledge.get('available'):
        hint = (knowledge.get('hint') or '').strip()
        if hint:
            parts.append(f'案例:{hint}')

    return ' '.join(parts).strip()


def format_score_ou_note(prediction: Dict[str, Any]) -> str:
    top_scores = prediction.get('top_scores') or []
    score_parts = []
    if isinstance(top_scores, list):
        for item in top_scores[:2]:
            if isinstance(item, (list, tuple)) and item:
                score_parts.append(str(item[0]).strip())
    score_note = f"比分:{'/'.join(score_parts)}" if score_parts else ''

    over_under = prediction.get('over_under') or {}
    over_under_note = ''
    if isinstance(over_under, dict):
        line = over_under.get('line')
        over_prob = over_under.get('over')
        under_prob = over_under.get('under')
        if isinstance(line, (int, float)) and isinstance(over_prob, (int, float)) and isinstance(under_prob, (int, float)):
            side = '大' if over_prob >= under_prob else '小'
            over_under_note = f'大小:{side}{line:g}({max(over_prob, under_prob):.2f})'

    total_goals_note = ''
    total_goals = prediction.get('total_goals') or {}
    if isinstance(total_goals, dict) and total_goals.get('available'):
        top_totals = total_goals.get('top_totals') or []
        parts = []
        for item in top_totals[:2]:
            if not isinstance(item, dict):
                continue
            total = item.get('total')
            prob = item.get('prob')
            if total is None or prob is None:
                continue
            try:
                parts.append(f'{total}({float(prob):.2f})')
            except Exception:
                continue
        if parts:
            tail_key = total_goals.get('tail_bucket')
            tail_prob = None
            buckets = total_goals.get('buckets') or {}
            if isinstance(buckets, dict) and tail_key in buckets:
                try:
                    tail_prob = float(buckets.get(tail_key))
                except Exception:
                    tail_prob = None
            tail = f' {tail_key}({tail_prob:.2f})' if isinstance(tail_prob, float) else ''
            total_goals_note = f"进球数:{'/'.join(parts)}{tail}"

    stake_note = ''
    staking = prediction.get('staking') or {}
    recommended = staking.get('recommended') if isinstance(staking, dict) else None
    if isinstance(recommended, dict):
        fraction = recommended.get('fraction')
        if isinstance(fraction, (int, float)) and fraction > 0:
            stake_note = f'仓位:{fraction:.0%}'

    return ' '.join(part for part in (score_note, over_under_note, total_goals_note, stake_note) if part).strip()


def strip_existing_prediction_fragments(note: str) -> str:
    if not note:
        return ''
    base = note
    if '预测:' in base:
        base = base.split('预测:')[0]
    base = re.sub(r'预测\s*(主胜|平局|客胜)\s*[✅❌]?', '', base)
    base = re.sub(r'\s+', ' ', base).strip()
    return base.rstrip('；; /／').strip()


def update_teams_md_with_enhanced_predictions(teams_path: str, match_date: str, predictions: List[Dict[str, Any]]) -> int:
    try:
        lines = open(teams_path, 'r', encoding='utf-8').read().splitlines(True)
    except Exception:
        return 0

    prediction_index = {}
    for prediction in predictions:
        home = _normalize_team_name(prediction.get('home_team'))
        away = _normalize_team_name(prediction.get('away_team'))
        if home and away:
            prediction_index[(match_date, home, away)] = prediction

    changed = 0
    out_lines = []
    for line in lines:
        if not line.lstrip().startswith('|'):
            out_lines.append(line)
            continue
        raw = line.strip('\n')
        if raw.count('|') < 6:
            out_lines.append(line)
            continue
        cells = [cell.strip() for cell in raw.strip().strip('|').split('|')]
        if len(cells) != 6:
            out_lines.append(line)
            continue
        date, _time, home, score, away, note = cells
        prediction = prediction_index.get((date, _normalize_team_name(home), _normalize_team_name(away)))
        if not prediction:
            out_lines.append(line)
            continue
        if re.match(r'^\d+\s*-\s*\d+$', score or ''):
            out_lines.append(line)
            continue

        confidence = float(prediction.get('confidence') or 0.0)
        upset_note = format_upset_note(prediction.get('upset_potential'))
        score_ou_note = format_score_ou_note(prediction)
        applied_weights = prediction.get('applied_model_weights')
        dyn = ''
        if isinstance(applied_weights, dict) and 'has_enough_samples' in applied_weights:
            dyn = '动态调权:已生效' if applied_weights.get('has_enough_samples') else '动态调权:样本不足'
        merged = strip_existing_prediction_fragments(note).rstrip('；; ').strip()
        pred_note = f"预测:{prediction.get('prediction')} 信心:{confidence:.2f} {score_ou_note} {upset_note}{(' ' + dyn) if dyn else ''}".strip()
        cells[5] = f'{merged}；{pred_note}' if merged else pred_note
        out_lines.append('| ' + ' | '.join(cells) + ' |\n')
        changed += 1

    if changed:
        try:
            with open(teams_path, 'w', encoding='utf-8') as f:
                f.writelines(out_lines)
        except Exception:
            return 0
    return changed


class TeamsWritebackGateway:
    def __init__(self, base_dir: Optional[str] = None):
        self.store = TeamsMarkdownStore(base_dir)

    def teams_file_path(self, league_code: str) -> str:
        return self.store.path_for_league(league_code)

    def write_predictions(self, league_code: str, match_date: str, predictions: List[Dict[str, Any]]) -> int:
        return update_teams_md_with_enhanced_predictions(self.teams_file_path(league_code), match_date, predictions)
