"""模块说明：把预测归档与已完赛结果同步为结构化赔率样本。"""

from __future__ import annotations

import heapq
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from runtime.paths import get_default_paths
from storage import PredictionArchiveStore

logger = logging.getLogger(__name__)

WINNER_TEXT = {
    'home': '主胜',
    'away': '客胜',
    'draw': '平局',
}

RESULT_TEXT_ALIASES = {
    '主胜': '主胜',
    '客胜': '客胜',
    '平局': '平局',
    'home': '主胜',
    'away': '客胜',
    'draw': '平局',
}


def prediction_memory_samples_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file('prediction_memory_odds_samples.json')


def _load_team_alias_map(base_dir: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    path = get_default_paths(base_dir).alias_map_path
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for league, mapping in raw.items():
        if not isinstance(mapping, dict):
            continue
        league_key = str(league or '').strip()
        if not league_key:
            continue
        league_map: Dict[str, str] = {}
        for canonical, aliases in mapping.items():
            canonical_name = str(canonical or '').strip()
            if not canonical_name:
                continue
            league_map[canonical_name] = canonical_name
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_name = str(alias or '').strip()
                    if alias_name:
                        league_map[alias_name] = canonical_name
        out[league_key] = league_map
    return out


def _normalize_team_name(league_code: str, name: str, alias_map: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    raw_name = str(name or '').strip()
    if not raw_name:
        return ''
    mapping = alias_map or {}
    league_map = mapping.get(str(league_code or '').strip(), {})
    if isinstance(league_map, dict) and raw_name in league_map:
        return league_map.get(raw_name, raw_name)
    return raw_name


def _parse_datetime(value: Any) -> datetime:
    raw = str(value or '').strip()
    if not raw:
        return datetime.min
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except Exception:
        try:
            dt = datetime.strptime(raw[:19], '%Y-%m-%dT%H:%M:%S')
        except Exception:
            try:
                dt = datetime.strptime(raw[:10], '%Y-%m-%d')
            except Exception:
                return datetime.min
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _normalize_actual_result(value: Any) -> str:
    return RESULT_TEXT_ALIASES.get(str(value or '').strip(), '')


def _resolve_archive_match_id(archive_key: str, entry: Dict[str, Any]) -> str:
    full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
    return str(
        entry.get('match_id')
        or entry.get('internal_match_id')
        or entry.get('teams_match_id')
        or entry.get('external_match_id')
        or full_prediction.get('match_id')
        or full_prediction.get('internal_match_id')
        or full_prediction.get('teams_match_id')
        or archive_key
        or ''
    ).strip()


def _extract_market_snapshot(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market_snapshot = entry.get('market_snapshot')
    if isinstance(market_snapshot, dict):
        return market_snapshot
    full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
    market_snapshot = full_prediction.get('market_snapshot')
    return market_snapshot if isinstance(market_snapshot, dict) else None


def _candidate_sort_key(match_id: str, entry: Dict[str, Any]) -> Tuple[datetime, datetime, str]:
    full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
    archived_at = _parse_datetime(
        entry.get('archived_at')
        or entry.get('saved_at')
        or full_prediction.get('timestamp')
        or full_prediction.get('archived_at')
    )
    match_date = _parse_datetime(entry.get('match_date') or full_prediction.get('match_date'))
    return archived_at, match_date, match_id


def _result_identity(
    league_code: Any,
    match_date: Any,
    home_team: Any,
    away_team: Any,
    alias_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[str, str, str, str]:
    normalized_league = str(league_code or '').strip()
    return (
        normalized_league,
        str(match_date or '').strip(),
        _normalize_team_name(normalized_league, str(home_team or '').strip(), alias_map or {}),
        _normalize_team_name(normalized_league, str(away_team or '').strip(), alias_map or {}),
    )


def _build_sample_row(
    match_id: str,
    entry: Dict[str, Any],
    results_by_match_id: Dict[str, Dict[str, Any]],
    results_by_identity: Dict[Tuple[str, str, str, str], Dict[str, Any]],
    alias_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
    league_code = str(entry.get('league') or full_prediction.get('league_code') or '').strip()
    if not league_code:
        return None

    market_snapshot = _extract_market_snapshot(entry)
    if not isinstance(market_snapshot, dict):
        return None

    match_date = entry.get('match_date') or full_prediction.get('match_date')
    home_team = entry.get('home_team') or full_prediction.get('home_team')
    away_team = entry.get('away_team') or full_prediction.get('away_team')
    actual_row = results_by_match_id.get(match_id) or results_by_identity.get(
        _result_identity(league_code, match_date, home_team, away_team, alias_map)
    ) or {}
    actual_winner_code = str(
        actual_row.get('actual_winner')
        or entry.get('actual_winner')
        or full_prediction.get('actual_winner')
        or ''
    ).strip()
    actual_result = WINNER_TEXT.get(actual_winner_code, '') or _normalize_actual_result(
        actual_row.get('actual_result')
        or entry.get('actual_result')
        or full_prediction.get('actual_result')
    )
    actual_score = str(
        actual_row.get('actual_score')
        or entry.get('actual_score')
        or full_prediction.get('actual_score')
        or ''
    ).strip()

    prediction = str(entry.get('prediction') or full_prediction.get('prediction') or '').strip()
    confidence = entry.get('confidence') if entry.get('confidence') is not None else full_prediction.get('confidence')
    archived_at = entry.get('archived_at') or entry.get('saved_at') or full_prediction.get('timestamp') or ''

    return {
        'match_id': match_id,
        'league_code': league_code,
        'match_date': match_date,
        'home_team': home_team,
        'away_team': away_team,
        'actual_score': actual_score,
        'actual_result': actual_result,
        '欧赔': market_snapshot.get('欧赔', {}),
        '亚值': market_snapshot.get('亚值', {}),
        '大小球': market_snapshot.get('大小球', {}),
        '凯利': market_snapshot.get('凯利', {}),
        'source': 'prediction_memory',
        'archived_at': archived_at,
        'prediction': prediction,
        'confidence': confidence,
        '_sort_key': _candidate_sort_key(match_id, entry),
    }


def build_prediction_memory_samples(base_dir: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    archive = PredictionArchiveStore(base_dir).load()
    alias_map = _load_team_alias_map(base_dir)

    try:
        from result_manager import ResultManager
        results = ResultManager(base_dir).load_results()
    except Exception as exc:
        logger.warning('读取比赛结果失败，滚动记忆赔率样本将缺少完赛标签: %s', exc)
        results = []

    results_by_match_id = {
        str(item.get('match_id') or ''): item
        for item in results
        if isinstance(item, dict) and item.get('match_id')
    }
    results_by_identity = {
        _result_identity(item.get('league'), item.get('match_date'), item.get('home_team'), item.get('away_team'), alias_map): item
        for item in results
        if isinstance(item, dict)
    }

    sample_limit = max(0, int(limit))
    top_candidates: List[Tuple[Tuple[datetime, datetime, str], Dict[str, Any]]] = []
    total_candidates = 0
    seen_ids = set()
    for archive_key, archived in archive.items():
        if not isinstance(archived, dict):
            continue
        match_id = _resolve_archive_match_id(str(archive_key), archived)
        if not match_id or match_id in seen_ids:
            continue
        sample_row = _build_sample_row(match_id, archived, results_by_match_id, results_by_identity, alias_map)
        if not isinstance(sample_row, dict):
            continue
        seen_ids.add(match_id)
        total_candidates += 1
        sort_key = sample_row['_sort_key']
        if sample_limit <= 0:
            continue
        if len(top_candidates) < sample_limit:
            heapq.heappush(top_candidates, (sort_key, sample_row))
            continue
        if sort_key > top_candidates[0][0]:
            heapq.heapreplace(top_candidates, (sort_key, sample_row))

    selected = [item for _sort_key, item in sorted(top_candidates, key=lambda pair: pair[0], reverse=True)]

    records_by_league: Dict[str, List[Dict[str, Any]]] = {}
    completed_samples = 0
    for item in selected:
        if item['actual_result'] and item['actual_score']:
            completed_samples += 1
        league_code = item.pop('league_code')
        item.pop('_sort_key', None)
        records_by_league.setdefault(league_code, []).append(item)

    return {
        'updated_at': datetime.now().isoformat(),
        'limit': limit,
        'total_candidates': total_candidates,
        'completed_samples': completed_samples,
        'records_by_league': records_by_league,
    }


def sync_prediction_memory_samples(base_dir: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    payload = build_prediction_memory_samples(base_dir=base_dir, limit=limit)
    path = prediction_memory_samples_path(base_dir)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return payload


def load_prediction_memory_samples(base_dir: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    path = prediction_memory_samples_path(base_dir)
    if not path.exists():
        return sync_prediction_memory_samples(base_dir=base_dir, limit=limit)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return sync_prediction_memory_samples(base_dir=base_dir, limit=limit)
    if not isinstance(payload, dict) or 'records_by_league' not in payload:
        return sync_prediction_memory_samples(base_dir=base_dir, limit=limit)
    return payload
