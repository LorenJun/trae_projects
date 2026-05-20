"""模块说明：把滚动记忆、预测归档与已完赛结果同步为结构化赔率样本。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from runtime.paths import get_default_paths
from storage import PredictionArchiveStore

logger = logging.getLogger(__name__)

WINNER_TEXT = {
    'home': '主胜',
    'away': '客胜',
    'draw': '平局',
}


def prediction_memory_samples_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file('prediction_memory_odds_samples.json')


def _prediction_memory_key_to_match_id(memory_key: str) -> Optional[str]:
    parts = str(memory_key or '').split('|')
    if len(parts) != 4:
        return None
    league_code, match_date, home_team, away_team = parts
    if not (league_code and match_date and home_team and away_team):
        return None
    return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"


def _extract_prediction_memory_entries(content: str, limit: int) -> List[Dict[str, str]]:
    from domain.persistence import PredictionPersistenceService

    marker = re.search(
        r'<!-- prediction-memory:start -->\n(?P<body>.*?)<!-- prediction-memory:end -->',
        content,
        re.DOTALL,
    )
    if not marker:
        return []
    entries: List[Dict[str, str]] = []
    for block in PredictionPersistenceService._extract_memory_entry_lines(marker.group('body')):
        normalized = PredictionPersistenceService._unescape_memory_entry_text(block)
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if not lines:
            continue
        first_line = lines[0]
        if not first_line.startswith('- ['):
            continue
        memory_key = first_line.split(']', 1)[0][3:]
        memory_id = ''
        for line in lines[1:]:
            memory_id_match = re.search(r'记忆ID:\s*([^|]+?)(?=\s*\||$)', line)
            if memory_id_match:
                memory_id = str(memory_id_match.group(1) or '').strip()
                break
        entries.append({'memory_key': memory_key, 'memory_id': memory_id})
        if len(entries) >= limit:
            break
    return entries


def build_prediction_memory_samples(base_dir: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
    paths = get_default_paths(base_dir)
    memory_path = paths.memory_file
    archive = PredictionArchiveStore(base_dir).load()
    try:
        content = memory_path.read_text(encoding='utf-8')
    except Exception as exc:
        logger.warning('读取 MEMORY.md 失败，无法构建滚动记忆赔率样本: %s', exc)
        content = ''

    try:
        from result_manager import ResultManager
        results = ResultManager(base_dir).load_results()
    except Exception as exc:
        logger.warning('读取比赛结果失败，滚动记忆赔率样本将缺少完赛标签: %s', exc)
        results = []

    results_map = {
        str(item.get('match_id') or ''): item
        for item in results
        if isinstance(item, dict) and item.get('match_id')
    }

    records_by_league: Dict[str, List[Dict[str, Any]]] = {}
    total_candidates = 0
    completed_samples = 0
    seen_ids = set()

    for memory_entry in _extract_prediction_memory_entries(content, limit):
        memory_key = memory_entry.get('memory_key') or ''
        memory_id = str(memory_entry.get('memory_id') or '').strip()
        match_id = memory_id or _prediction_memory_key_to_match_id(memory_key)
        if not match_id or match_id in seen_ids:
            continue
        seen_ids.add(match_id)
        total_candidates += 1

        archived = archive.get(match_id)
        if not isinstance(archived, dict) and memory_id:
            for archive_key, archive_value in archive.items():
                if not isinstance(archive_value, dict):
                    continue
                candidates = {
                    str(archive_key).strip(),
                    str(archive_value.get('match_id') or '').strip(),
                    str(archive_value.get('external_match_id') or '').strip(),
                    str(archive_value.get('internal_match_id') or '').strip(),
                }
                full_prediction = archive_value.get('full_prediction')
                if isinstance(full_prediction, dict):
                    candidates.add(str(full_prediction.get('match_id') or '').strip())
                if memory_id in candidates:
                    archived = archive_value
                    match_id = str(memory_id)
                    break
        if not isinstance(archived, dict):
            continue
        league_code = str(archived.get('league') or '').strip()
        if not league_code:
            continue

        market_snapshot = archived.get('market_snapshot')
        if not isinstance(market_snapshot, dict) and isinstance(archived.get('full_prediction'), dict):
            market_snapshot = archived['full_prediction'].get('market_snapshot')
        if not isinstance(market_snapshot, dict):
            continue

        actual_row = results_map.get(match_id) or {}
        archived_prediction = archived.get('full_prediction') if isinstance(archived.get('full_prediction'), dict) else {}
        actual_winner_code = str(
            actual_row.get('actual_winner')
            or archived.get('actual_winner')
            or archived_prediction.get('actual_winner')
            or ''
        ).strip()
        actual_result = WINNER_TEXT.get(actual_winner_code, '')
        actual_score = str(
            actual_row.get('actual_score')
            or archived.get('actual_score')
            or archived_prediction.get('actual_score')
            or ''
        ).strip()
        if actual_result and actual_score:
            completed_samples += 1

        records_by_league.setdefault(league_code, []).append(
            {
                'match_id': match_id,
                'match_date': archived.get('match_date'),
                'home_team': archived.get('home_team'),
                'away_team': archived.get('away_team'),
                'actual_score': actual_score,
                'actual_result': actual_result,
                '欧赔': market_snapshot.get('欧赔', {}),
                '亚值': market_snapshot.get('亚值', {}),
                '大小球': market_snapshot.get('大小球', {}),
                '凯利': market_snapshot.get('凯利', {}),
                'source': 'prediction_memory',
                'archived_at': archived.get('archived_at'),
                'prediction': archived.get('prediction'),
                'confidence': archived.get('confidence'),
            }
        )

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
