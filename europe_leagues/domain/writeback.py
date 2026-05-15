"""模块说明：负责把预测结果写回 teams_2025-26.md 等文本事实源。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from storage.teams_md import TeamsMarkdownStore


def _normalize_team_name(value: str) -> str:
    return (value or '').strip().replace(' ', '')


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def resolve_prediction_summary(prediction: Dict[str, Any]) -> tuple[str, float]:
    final_probabilities = prediction.get('final_probabilities')
    if isinstance(final_probabilities, dict):
        ranked = [
            ('主胜', _safe_float(final_probabilities.get('home_win')) or 0.0),
            ('平局', _safe_float(final_probabilities.get('draw')) or 0.0),
            ('客胜', _safe_float(final_probabilities.get('away_win')) or 0.0),
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        if ranked and ranked[0][1] > 0:
            return ranked[0][0], float(ranked[0][1])

    all_probabilities = prediction.get('all_probabilities')
    if isinstance(all_probabilities, dict):
        ranked = [
            ('主胜', _safe_float(all_probabilities.get('主胜')) or 0.0),
            ('平局', _safe_float(all_probabilities.get('平局')) or 0.0),
            ('客胜', _safe_float(all_probabilities.get('客胜')) or 0.0),
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        if ranked and ranked[0][1] > 0:
            return ranked[0][0], float(ranked[0][1])

    return str(prediction.get('prediction') or '平局').strip() or '平局', _safe_float(prediction.get('confidence')) or 0.0


def _sanitize_note_fragment(value: Any) -> str:
    text = str(value or '').replace('|', '/').strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(' ;；,/')
    missing_right_paren = text.count('(') - text.count(')')
    if missing_right_paren > 0:
        text = text.rstrip(' /,;；') + (')' * missing_right_paren)
    return text.strip(' ;；,/')


def _score_matches_prediction(score: str, prediction_text: str) -> bool:
    normalized = str(score or '').strip()
    if not normalized or '-' not in normalized:
        return False
    try:
        home_goals, away_goals = [int(part.strip()) for part in normalized.split('-', 1)]
    except Exception:
        return False

    prediction_label = str(prediction_text or '').strip()
    allowed = set()
    if '主胜' in prediction_label:
        allowed.add('home')
    if '平局' in prediction_label:
        allowed.add('draw')
    if '客胜' in prediction_label:
        allowed.add('away')
    if not allowed:
        return True

    if home_goals > away_goals:
        return 'home' in allowed
    if home_goals < away_goals:
        return 'away' in allowed
    return 'draw' in allowed


def _filter_score_candidates(raw_scores: Any, prediction_text: str, limit: int | None = None) -> List[str]:
    score_parts: List[str] = []
    for raw_part in str(raw_scores or '').split('/'):
        score = raw_part.strip()
        if not score or not _score_matches_prediction(score, prediction_text):
            continue
        if score in score_parts:
            continue
        score_parts.append(score)
        if limit is not None and len(score_parts) >= limit:
            break
    return score_parts


def normalize_existing_prediction_note(note: str) -> str:
    text = str(note or '').strip()
    if not text or '预测:' not in text:
        return text

    prediction_match = re.search(r'预测:(主胜|平局|客胜)', text)
    prediction_label = prediction_match.group(1) if prediction_match else ''

    def _replace_score_fragment(match: re.Match[str]) -> str:
        filtered = _filter_score_candidates(match.group(1), prediction_label)
        return f"比分:{'/'.join(filtered)}" if filtered else '比分:-'

    text = re.sub(r'比分:([0-9]+-[0-9]+(?:/[0-9]+-[0-9]+)*)', _replace_score_fragment, text)

    def _replace_case_fragment(match: re.Match[str]) -> str:
        hint = _sanitize_note_fragment(match.group(1))
        return f'案例:{hint}' if hint else ''

    text = re.sub(r'案例:(.+?)(?=(?: 动态调权:| MatchID:|；赛果:|；复盘:|$))', _replace_case_fragment, text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def format_upset_note(upset: Any) -> str:
    if isinstance(upset, str):
        return f"爆冷:{_sanitize_note_fragment(upset) or '-'}".strip()
    if not isinstance(upset, dict):
        return '爆冷:-'

    level = _sanitize_note_fragment(upset.get('level')) or '-'
    idx = upset.get('index')
    idx_text = f"({int(round(float(idx)))})" if isinstance(idx, (int, float)) else ''
    parts = [f'爆冷:{level}{idx_text}']

    motivation_risk = upset.get('motivation_risk')
    if isinstance(motivation_risk, dict) and motivation_risk.get('available'):
        factors = motivation_risk.get('factors') or []
        if isinstance(factors, list) and factors:
            compact = ';'.join(_sanitize_note_fragment(item) for item in factors[:2] if _sanitize_note_fragment(item))
            if compact:
                parts.append(f'战意:{compact}')
        pressure_side = str(motivation_risk.get('pressure_side') or '').strip()
        if pressure_side in {'home', 'away'} and not any(part.startswith('战意方:') for part in parts):
            side_label = '主队' if pressure_side == 'home' else '客队'
            parts.append(f'战意方:{side_label}')

    mismatch = upset.get('handicap_strength_mismatch')
    if isinstance(mismatch, dict) and mismatch.get('mismatch_detected'):
        mismatch_level = (mismatch.get('mismatch_level') or '').strip() or '是'
        parts.append(f'错配:{mismatch_level}')
        suggestion = (mismatch.get('suggested_outcome') or '').strip()
        if suggestion:
            parts.append(f'建议:{suggestion}')
        factors = mismatch.get('warning_factors') or []
        if isinstance(factors, list) and factors:
            compact = ';'.join(_sanitize_note_fragment(item) for item in factors[:2] if _sanitize_note_fragment(item))
            if compact:
                parts.append(f'因子:{compact}')

    knowledge = upset.get('case_knowledge')
    if isinstance(knowledge, dict) and knowledge.get('available'):
        hint = _sanitize_note_fragment(knowledge.get('hint'))
        if hint:
            parts.append(f'案例:{hint}')

    return ' '.join(parts).strip()


def format_score_ou_note(prediction: Dict[str, Any]) -> str:
    prediction_text, _ = resolve_prediction_summary(prediction)
    top_scores = prediction.get('top_scores') or []
    score_parts = []
    if isinstance(top_scores, list):
        score_parts = _filter_score_candidates(
            '/'.join(str(item[0]).strip() for item in top_scores if isinstance(item, (list, tuple)) and item),
            prediction_text,
            limit=2,
        )
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


def build_prediction_note(prediction: Dict[str, Any]) -> str:
    prediction_text, confidence = resolve_prediction_summary(prediction)
    score_ou_note = format_score_ou_note(prediction)
    upset_note = format_upset_note(prediction.get('upset_potential'))
    applied_weights = prediction.get('applied_model_weights')
    match_id = str(
        prediction.get('match_id')
        or prediction.get('external_match_id')
        or prediction.get('internal_match_id')
        or prediction.get('teams_match_id')
        or ''
    ).strip()
    match_id_note = f'MatchID:{match_id}' if match_id else ''
    dyn = ''
    if isinstance(applied_weights, dict) and 'has_enough_samples' in applied_weights:
        dyn = '动态调权:已生效' if applied_weights.get('has_enough_samples') else '动态调权:样本不足'
    return f"预测:{prediction_text} 信心:{confidence:.2f} {score_ou_note} {upset_note}{(' ' + dyn) if dyn else ''}{(' ' + match_id_note) if match_id_note else ''}".strip()


def update_teams_md_prediction_notes(
    teams_path: str,
    predictions: List[Dict[str, Any]],
    match_date: Optional[str] = None,
) -> int:
    try:
        lines = open(teams_path, 'r', encoding='utf-8').read().splitlines(True)
    except Exception:
        return 0

    prediction_index = {}
    for prediction in predictions:
        home = _normalize_team_name(str(prediction.get('home_team') or ''))
        away = _normalize_team_name(str(prediction.get('away_team') or ''))
        date = str(prediction.get('match_date') or match_date or '').strip()
        if home and away and date:
            prediction_index[(date, home, away)] = prediction

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
        current_note = normalize_existing_prediction_note(note)

        if prediction and not re.match(r'^\d+\s*-\s*\d+$', score or ''):
            merged = strip_existing_prediction_fragments(current_note).rstrip('；; ').strip()
            pred_note = build_prediction_note(prediction)
            current_note = f'{merged}；{pred_note}' if merged else pred_note
            current_note = normalize_existing_prediction_note(current_note)

        if current_note == note:
            out_lines.append(line)
            continue
        cells[5] = current_note
        out_lines.append('| ' + ' | '.join(cells) + ' |\n')
        changed += 1

    if changed:
        try:
            with open(teams_path, 'w', encoding='utf-8') as f:
                f.writelines(out_lines)
        except Exception:
            return 0
    return changed


def update_teams_md_with_enhanced_predictions(teams_path: str, match_date: str, predictions: List[Dict[str, Any]]) -> int:
    return update_teams_md_prediction_notes(teams_path, predictions, match_date=match_date)


class TeamsWritebackGateway:
    def __init__(self, base_dir: Optional[str] = None):
        self.store = TeamsMarkdownStore(base_dir)

    def teams_file_path(self, league_code: str) -> str:
        return self.store.path_for_league(league_code)

    def write_predictions(self, league_code: str, match_date: str, predictions: List[Dict[str, Any]]) -> int:
        return update_teams_md_with_enhanced_predictions(self.teams_file_path(league_code), match_date, predictions)

    def write_prediction(self, league_code: str, prediction: Dict[str, Any]) -> int:
        return update_teams_md_prediction_notes(self.teams_file_path(league_code), [prediction])
