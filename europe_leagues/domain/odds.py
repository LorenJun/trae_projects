"""模块说明：负责盘口解析、历史赔率相似样本、大小球线解析与补抓逻辑。"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from glob import glob
from typing import Any, Dict, List, Optional, Tuple

from collectors.okooo import build_okooo_driver_chain
from collectors.okooo import describe_unavailable_okooo_drivers
from runtime.cache import PredictionCache
from runtime.memory_samples import load_prediction_memory_samples

logger = logging.getLogger(__name__)


def build_odds_runtime_options(driver: str = 'local-chrome', headed: bool = False, no_refresh_odds: bool = False) -> Dict[str, Any]:
    return {
        'okooo_driver': driver,
        'okooo_headed': bool(headed),
        'force_refresh_odds': not bool(no_refresh_odds),
    }


def external_snapshot_root(base_dir: str) -> str:
    return os.path.join(base_dir, '.okooo-scraper', 'snapshots')


def external_snapshot_dirs(base_dir: str, league_code: str) -> List[str]:
    dirs = [
        os.path.join(external_snapshot_root(base_dir), league_code),
        os.path.join(base_dir, 'okooo_snapshots'),
        os.path.join(base_dir, 'okooo_snapshots', league_code),
    ]
    seen = set()
    result = []
    for item in dirs:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def resolve_over_under_line(
    current_odds: Optional[Dict[str, Any]],
    analysis_context: Dict[str, Any],
    to_float,
) -> Tuple[float, str]:
    try:
        if isinstance(analysis_context, dict) and 'ou_line' in analysis_context:
            value = to_float(analysis_context.get('ou_line'))
            if isinstance(value, float) and 0.5 <= value <= 6.5:
                return float(value), 'analysis_context'
    except Exception:
        pass

    try:
        if isinstance(current_odds, dict):
            totals = current_odds.get('大小球')
            if isinstance(totals, dict):
                final = totals.get('final')
                if isinstance(final, dict):
                    value = final.get('line')
                    if value is None:
                        value = final.get('盘口')
                    parsed = to_float(value)
                    if isinstance(parsed, float) and 0.5 <= parsed <= 6.5:
                        return float(parsed), 'snapshot_final'
                initial = totals.get('initial')
                if isinstance(initial, dict):
                    value = initial.get('line')
                    if value is None:
                        value = initial.get('盘口')
                    parsed = to_float(value)
                    if isinstance(parsed, float) and 0.5 <= parsed <= 6.5:
                        return float(parsed), 'snapshot_initial'
    except Exception:
        pass

    return 2.5, 'default_2.5'


def auto_fetch_okooo_totals_if_needed(
    *,
    base_dir: str,
    league_name: str,
    match_date: str,
    home_team: str,
    away_team: str,
    current_odds: Optional[Dict[str, Any]],
    analysis_context: Dict[str, Any],
    to_float,
    okooo_driver: str = 'local-chrome',
    okooo_headed: bool = False,
    match_time: str = '',
    match_id: str = '',
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    diag: Dict[str, Any] = {'attempted': False, 'ok': False}
    enabled = os.environ.get('OKOOO_AUTO_TOTALS', '1').strip() not in ('0', 'false', 'False')
    if not enabled:
        diag['skipped'] = 'OKOOO_AUTO_TOTALS=0'
        return current_odds, diag

    try:
        _line, src = resolve_over_under_line(current_odds=current_odds, analysis_context=analysis_context, to_float=to_float)
        if src in ('snapshot_final', 'snapshot_initial', 'analysis_context'):
            diag['skipped'] = f'ou_line already resolved from {src}'
            return current_odds, diag
    except Exception:
        pass

    if not league_name:
        diag['skipped'] = 'unknown league'
        return current_odds, diag

    script = os.path.join(base_dir, 'okooo_save_snapshot.py')
    out_dir = external_snapshot_root(base_dir)
    drivers = build_okooo_driver_chain(okooo_driver)

    diag['attempted'] = True
    try:
        warnings = describe_unavailable_okooo_drivers(okooo_driver)
        if warnings:
            diag['warnings'] = warnings
        if not drivers:
            diag['error'] = '没有可用的抓取 driver；已跳过大小球自动补抓'
            return current_odds, diag
        last_error = None
        for driver in drivers:
            cmd = [
                sys.executable,
                script,
                '--driver', str(driver),
                '--league', str(league_name),
                '--team1', str(home_team),
                '--team2', str(away_team),
                '--date', str(match_date),
                '--out-dir', str(out_dir),
                '--overwrite',
            ]
            if match_time:
                cmd.extend(['--time', str(match_time)])
            if match_id:
                cmd.extend(['--match-id', str(match_id)])
            if bool(okooo_headed) and driver == 'browser-use':
                cmd.append('--headed')

            diag['driver_tried'] = driver
            diag['cmd'] = ' '.join(str(x) for x in cmd)
            proc = subprocess.run(cmd, cwd=os.path.dirname(script), capture_output=True, text=True, timeout=240)
            diag['returncode'] = proc.returncode
            if proc.stdout:
                diag['stdout_tail'] = proc.stdout.strip().splitlines()[-1][-200:]
            if proc.stderr:
                diag['stderr_tail'] = proc.stderr.strip().splitlines()[-1][-200:]
            if proc.returncode != 0:
                last_error = f'rc={proc.returncode}'
                continue

            out_path = (proc.stdout or '').strip().splitlines()[-1].strip()
            if not out_path or not os.path.exists(out_path):
                last_error = 'snapshot path missing'
                continue

            payload = json.loads(open(out_path, 'r', encoding='utf-8').read())
            totals = payload.get('大小球') if isinstance(payload, dict) else None
            if not isinstance(totals, dict) or not totals.get('found'):
                last_error = 'totals not found in snapshot'
                continue

            merged = dict(current_odds or {})
            merged['大小球'] = {
                'initial': totals.get('initial') or {},
                'final': totals.get('final') or {},
            }
            if isinstance(totals.get('consensus'), dict):
                merged['大小球']['consensus'] = totals.get('consensus') or {}
            if isinstance(totals.get('companies'), list):
                merged['大小球']['companies'] = totals.get('companies') or []
            if totals.get('company_mode'):
                merged['大小球']['company_mode'] = totals.get('company_mode')
            diag['ok'] = True
            diag['source_snapshot'] = out_path
            return merged, diag

        diag['error'] = last_error or 'unknown'
        return current_odds, diag
    except Exception as exc:
        diag['error'] = str(exc)
        return current_odds, diag


class HistoricalOddsReference:
    FEATURE_FIELDS = [
        ('胜平负赔率', 'initial', 'home'),
        ('胜平负赔率', 'initial', 'draw'),
        ('胜平负赔率', 'initial', 'away'),
        ('胜平负赔率', 'final', 'home'),
        ('胜平负赔率', 'final', 'draw'),
        ('胜平负赔率', 'final', 'away'),
        ('欧赔', 'initial', 'home'),
        ('欧赔', 'initial', 'draw'),
        ('欧赔', 'initial', 'away'),
        ('欧赔', 'final', 'home'),
        ('欧赔', 'final', 'draw'),
        ('欧赔', 'final', 'away'),
        ('亚值', 'initial', 'home'),
        ('亚值', 'initial', 'away'),
        ('亚值', 'final', 'home'),
        ('亚值', 'final', 'away'),
        ('凯利', 'initial', 'home'),
        ('凯利', 'initial', 'draw'),
        ('凯利', 'initial', 'away'),
        ('凯利', 'final', 'home'),
        ('凯利', 'final', 'draw'),
        ('凯利', 'final', 'away'),
        ('离散率', 'initial', 'home'),
        ('离散率', 'initial', 'draw'),
        ('离散率', 'initial', 'away'),
        ('离散率', 'final', 'home'),
        ('离散率', 'final', 'draw'),
        ('离散率', 'final', 'away'),
    ]

    def __init__(self, league_codes: List[str], base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.league_codes = list(league_codes)
        self.cache = PredictionCache()
        self.memory_history_counts: Dict[str, int] = {}
        self.records_by_league = self._load_odds_history()
        self.feature_stats = self._build_feature_stats()

    def _load_memory_history(self) -> Dict[str, List[Dict[str, Any]]]:
        payload = load_prediction_memory_samples(self.base_dir, limit=100)
        raw_records = payload.get('records_by_league') if isinstance(payload, dict) else {}
        records_by_league: Dict[str, List[Dict[str, Any]]] = {}
        for league_code in self.league_codes:
            records = raw_records.get(league_code, []) if isinstance(raw_records, dict) else []
            completed = [
                item for item in records
                if isinstance(item, dict) and item.get('actual_result') and item.get('actual_score')
            ]
            records_by_league[league_code] = completed
        return records_by_league

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None

    def _load_odds_history(self) -> Dict[str, List[Dict]]:
        records_by_league: Dict[str, List[Dict]] = {}
        for league_code in self.league_codes:
            odds_dir = os.path.join(self.base_dir, league_code, 'analysis', 'odds')
            records_by_league[league_code] = []
            if not os.path.isdir(odds_dir):
                continue
            for file_path in sorted(glob(os.path.join(odds_dir, '*_odds.json'))):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                    for match in payload.get('matches', []):
                        records_by_league[league_code].append(match)
                except Exception as exc:
                    logger.warning('加载历史赔率文件失败 %s: %s', file_path, exc)
        memory_records = self._load_memory_history()
        for league_code in self.league_codes:
            appended = 0
            existing_ids = {
                str(item.get('match_id') or '')
                for item in records_by_league.get(league_code, [])
                if isinstance(item, dict) and item.get('match_id')
            }
            for item in memory_records.get(league_code, []):
                match_id = str(item.get('match_id') or '')
                if match_id and match_id in existing_ids:
                    continue
                records_by_league.setdefault(league_code, []).append(item)
                if match_id:
                    existing_ids.add(match_id)
                appended += 1
            self.memory_history_counts[league_code] = appended
        return records_by_league

    def _build_feature_stats(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        stats: Dict[str, Dict[str, Dict[str, float]]] = {}
        for league_code, matches in self.records_by_league.items():
            values_by_key: Dict[str, List[float]] = {}
            for match in matches:
                vector = self._extract_feature_vector(match)
                for key, value in vector.items():
                    values_by_key.setdefault(key, []).append(value)
            league_stats: Dict[str, Dict[str, float]] = {}
            for key, vals in values_by_key.items():
                if not vals:
                    continue
                mean = sum(vals) / len(vals)
                variance = sum((x - mean) ** 2 for x in vals) / max(1, len(vals) - 1)
                std = variance ** 0.5 or 1.0
                league_stats[key] = {'mean': mean, 'std': std}
            stats[league_code] = league_stats
        return stats

    def get_league_record_count(self, league_code: str) -> int:
        return len(self.records_by_league.get(league_code, []))

    def _extract_feature_vector(self, odds_snapshot: Dict[str, Any]) -> Dict[str, float]:
        vector: Dict[str, float] = {}
        for group, phase, key in self.FEATURE_FIELDS:
            value = odds_snapshot.get(group, {}).get(phase, {}).get(key)
            numeric = self._safe_float(value)
            if numeric is not None:
                vector[f'{group}.{phase}.{key}'] = numeric
        return vector

    def _build_result_summary(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {'主胜': 0, '平局': 0, '客胜': 0}
        for match in matches:
            actual = match.get('actual_result')
            if actual in summary:
                summary[actual] += 1
        total = len(matches)
        cold_count = 0
        for match in matches:
            win_odds = match.get('胜平负赔率', {}).get('final', {})
            actual = match.get('actual_result')
            if actual == '主胜':
                actual_odds = self._safe_float(win_odds.get('home'))
            elif actual == '平局':
                actual_odds = self._safe_float(win_odds.get('draw'))
            elif actual == '客胜':
                actual_odds = self._safe_float(win_odds.get('away'))
            else:
                actual_odds = None
            if actual_odds is not None and actual_odds >= 3.5:
                cold_count += 1
        return {
            'sample_size': total,
            'result_counts': summary,
            'result_rates': {key: (value / total if total else 0.0) for key, value in summary.items()},
            'cold_result_count': cold_count,
            'cold_result_rate': (cold_count / total if total else 0.0),
        }

    def find_similar_matches(
        self,
        league_code: str,
        current_odds: Dict[str, Any],
        top_k: int = 5,
        exclude_match_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cache_params = {
            'algo_v': 2,
            'league': league_code,
            'odds': current_odds,
            'top_k': top_k,
            'exclude': exclude_match_id,
        }
        cached = self.cache.get('find_similar_odds_matches', cache_params)
        if cached:
            return cached

        current_vector = self._extract_feature_vector(current_odds)
        history = self.records_by_league.get(league_code, [])
        memory_count = self.memory_history_counts.get(league_code, 0)
        if not current_vector or not history:
            result = {
                'available': False,
                'league_history_count': len(history),
                'memory_history_count': memory_count,
                'matched_feature_count': 0,
                'similar_matches': [],
                'summary': self._build_result_summary([]),
                'insights': [],
            }
            self.cache.set('find_similar_odds_matches', cache_params, result)
            return result

        group_weights = {'胜平负赔率': 1.0, '欧赔': 1.0, '亚值': 1.0, '凯利': 0.8, '离散率': 0.6}
        feature_stats = self.feature_stats.get(league_code, {})
        scored_matches = []
        for match in history:
            if exclude_match_id and match.get('match_id') == exclude_match_id:
                continue
            historical_vector = self._extract_feature_vector(match)
            common_keys = set(current_vector) & set(historical_vector)
            if len(common_keys) < 6:
                continue
            distance = 0.0
            weight_sum = 0.0
            for key in common_keys:
                stats = feature_stats.get(key)
                if stats:
                    current_norm = (current_vector[key] - stats['mean']) / stats['std']
                    historical_norm = (historical_vector[key] - stats['mean']) / stats['std']
                    diff = abs(current_norm - historical_norm)
                else:
                    diff = abs(current_vector[key] - historical_vector[key])
                group = key.split('.', 1)[0]
                weight = group_weights.get(group, 1.0)
                distance += diff * weight
                weight_sum += weight
            avg_distance = distance / max(1.0, weight_sum)
            similarity = max(0.0, 1.0 / (1.0 + avg_distance))
            if match.get('source') == 'prediction_memory':
                similarity = min(1.0, similarity * 1.08)
            scored_matches.append({
                'match_id': match.get('match_id'),
                'match_date': match.get('match_date'),
                'home_team': match.get('home_team'),
                'away_team': match.get('away_team'),
                'actual_score': match.get('actual_score'),
                'actual_result': match.get('actual_result'),
                'page_id': match.get('page_id'),
                'source': match.get('source', 'odds_history'),
                'similarity': similarity,
                'distance': avg_distance,
                'matched_feature_count': len(common_keys),
                '胜平负赔率': match.get('胜平负赔率', {}),
                '欧赔': match.get('欧赔', {}),
                '亚值': match.get('亚值', {}),
                '凯利': match.get('凯利', {}),
                '离散率': match.get('离散率', {}),
            })
        scored_matches.sort(key=lambda item: (-item['similarity'], item['distance']))
        top_matches = scored_matches[:top_k]
        summary = self._build_result_summary(top_matches)
        insights = []
        if summary['sample_size'] >= 3:
            if summary['cold_result_rate'] >= 0.4:
                insights.append('相似赔率样本中高赔赛果占比较高，需防范热门方向失真')
            dominant_result, dominant_rate = max(summary['result_rates'].items(), key=lambda item: item[1])
            if dominant_rate >= 0.6:
                insights.append(f'相似赔率样本主要落在{dominant_result}({dominant_rate:.0%})')
        memory_hits = sum(1 for item in top_matches if item.get('source') == 'prediction_memory')
        if memory_hits:
            insights.append(f'近期滚动记忆命中{memory_hits}场已完赛相似盘口样本')
        result = {
            'available': bool(top_matches),
            'league_history_count': len(history),
            'memory_history_count': memory_count,
            'matched_feature_count': max((item['matched_feature_count'] for item in top_matches), default=0),
            'similar_matches': top_matches,
            'summary': summary,
            'insights': insights,
        }
        self.cache.set('find_similar_odds_matches', cache_params, result)
        return result
