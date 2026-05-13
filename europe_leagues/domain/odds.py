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

EXTERNAL_SNAPSHOT_DIR_ALIASES = {
    'europa_league': ['europa_league', '欧联', '欧罗巴'],
    'champions_league': ['champions_league', '欧冠'],
    'conference_league': ['conference_league', '欧协联'],
}


def parse_handicap_value(value: Any) -> Optional[float]:
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


def build_market_context(
    *,
    current_odds: Optional[Dict[str, Any]],
    analysis_context: Optional[Dict[str, Any]],
    to_float,
    postprocess_service: Any = None,
    cache: Any = None,
) -> Dict[str, Any]:
    analysis_context = analysis_context if isinstance(analysis_context, dict) else {}
    cached = analysis_context.get('market_context')
    if isinstance(cached, dict):
        return cached

    euro_odds: Dict[str, Any] = {'values': None, 'source': 'missing'}
    for market_key in ('欧赔', '胜平负赔率'):
        market_block = current_odds.get(market_key) if isinstance(current_odds, dict) else None
        if not isinstance(market_block, dict):
            continue
        final = market_block.get('final')
        if isinstance(final, dict):
            euro_odds = {'values': final, 'source': f'{market_key}.final'}
            break
        initial = market_block.get('initial')
        if isinstance(initial, dict):
            euro_odds = {'values': initial, 'source': f'{market_key}.initial'}
            break

    asian_block = current_odds.get('亚值') if isinstance(current_odds, dict) else None
    asian_final = asian_block.get('final') if isinstance(asian_block, dict) and isinstance(asian_block.get('final'), dict) else {}
    asian_initial = asian_block.get('initial') if isinstance(asian_block, dict) and isinstance(asian_block.get('initial'), dict) else {}
    asian_raw = (
        asian_final.get('handicap')
        if 'handicap' in asian_final
        else asian_final.get('handicap_value')
        if 'handicap_value' in asian_final
        else asian_final.get('盘口值')
        if '盘口值' in asian_final
        else asian_final.get('handicap_text')
        if 'handicap_text' in asian_final
        else asian_initial.get('handicap')
        if 'handicap' in asian_initial
        else asian_initial.get('handicap_value')
        if 'handicap_value' in asian_initial
        else asian_initial.get('盘口值')
        if '盘口值' in asian_initial
        else asian_initial.get('handicap_text')
    )

    over_under_line, over_under_line_source = resolve_over_under_line(
        current_odds=current_odds,
        analysis_context=analysis_context,
        to_float=to_float,
    )
    context = {
        'euro_odds': euro_odds,
        'asian_line': parse_handicap_value(asian_raw),
        'asian_line_raw': asian_raw,
        'over_under_line': over_under_line,
        'over_under_line_source': over_under_line_source,
    }
    analysis_context['market_context'] = context
    return context


def build_odds_runtime_options(driver: str = 'local-chrome', headed: bool = False, no_refresh_odds: bool = False) -> Dict[str, Any]:
    return {
        'okooo_driver': driver,
        'okooo_headed': bool(headed),
        'force_refresh_odds': not bool(no_refresh_odds),
    }


def external_snapshot_root(base_dir: str) -> str:
    return os.path.join(base_dir, '.okooo-scraper', 'snapshots')


def external_snapshot_dirs(base_dir: str, league_code: str) -> List[str]:
    aliases = EXTERNAL_SNAPSHOT_DIR_ALIASES.get(league_code, [league_code] if league_code else [''])
    dirs = [os.path.join(external_snapshot_root(base_dir), alias) for alias in aliases if alias]
    dirs.append(os.path.join(base_dir, 'okooo_snapshots'))
    dirs.extend(os.path.join(base_dir, 'okooo_snapshots', alias) for alias in aliases if alias)
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
) -> Tuple[Optional[float], str]:
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

    return None, 'missing_real_line'


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
        if src in ('snapshot_final', 'snapshot_initial'):
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

    @staticmethod
    def _pick_market_value(block: Any, phase: str, key: str) -> Optional[float]:
        if not isinstance(block, dict):
            return None
        phase_block = block.get(phase)
        if not isinstance(phase_block, dict):
            return None
        return HistoricalOddsReference._safe_float(phase_block.get(key))

    @staticmethod
    def _movement_ratio(initial: Optional[float], final: Optional[float]) -> float:
        if initial is None or final is None or initial <= 0:
            return 0.0
        return max(-0.18, min(0.18, (initial - final) / initial))

    @staticmethod
    def _direction_from_scores(score_map: Dict[str, float], *, min_top: float = 0.015, min_gap: float = 0.012) -> Tuple[str, float]:
        ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) < 2:
            return "balanced", 0.0
        top_name, top_value = ranked[0]
        second_value = ranked[1][1]
        if top_value > min_top and (top_value - second_value) > min_gap:
            return top_name, round(max(0.0, top_value - second_value), 4)
        return "balanced", 0.0

    @classmethod
    def _extract_market_movement_profile(cls, odds_snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(odds_snapshot, dict):
            return {
                'available': False,
                'side_direction': 'balanced',
                'side_strength': 0.0,
                'bookmaker_psychology': {'label': 'unknown', 'strength': 0.0},
                'capital_flow': {'direction': 'balanced', 'strength': 0.0},
                'totals_direction': 'balanced',
                'totals_strength': 0.0,
            }

        euro = odds_snapshot.get('欧赔')
        if not isinstance(euro, dict):
            euro = odds_snapshot.get('胜平负赔率') if isinstance(odds_snapshot.get('胜平负赔率'), dict) else {}
        asian = odds_snapshot.get('亚值') if isinstance(odds_snapshot.get('亚值'), dict) else {}
        kelly = odds_snapshot.get('凯利') if isinstance(odds_snapshot.get('凯利'), dict) else {}
        totals = odds_snapshot.get('大小球') if isinstance(odds_snapshot.get('大小球'), dict) else {}

        home_score = cls._movement_ratio(
            cls._pick_market_value(euro, 'initial', 'home'),
            cls._pick_market_value(euro, 'final', 'home'),
        )
        draw_score = cls._movement_ratio(
            cls._pick_market_value(euro, 'initial', 'draw'),
            cls._pick_market_value(euro, 'final', 'draw'),
        )
        away_score = cls._movement_ratio(
            cls._pick_market_value(euro, 'initial', 'away'),
            cls._pick_market_value(euro, 'final', 'away'),
        )

        h_water_i = cls._pick_market_value(asian, 'initial', 'home_water')
        h_water_f = cls._pick_market_value(asian, 'final', 'home_water')
        a_water_i = cls._pick_market_value(asian, 'initial', 'away_water')
        a_water_f = cls._pick_market_value(asian, 'final', 'away_water')
        if h_water_i is not None and h_water_f is not None:
            home_score += max(-0.08, min(0.08, (h_water_i - h_water_f) * 0.25))
        if a_water_i is not None and a_water_f is not None:
            away_score += max(-0.08, min(0.08, (a_water_i - a_water_f) * 0.25))

        k_h = cls._pick_market_value(kelly, 'final', 'home')
        k_d = cls._pick_market_value(kelly, 'final', 'draw')
        k_a = cls._pick_market_value(kelly, 'final', 'away')
        kelly_vals = [item for item in (k_h, k_d, k_a) if isinstance(item, float)]
        if len(kelly_vals) == 3:
            avg_k = sum(kelly_vals) / 3.0
            home_score += max(-0.05, min(0.05, (avg_k - k_h) * 0.35))
            draw_score += max(-0.05, min(0.05, (avg_k - k_d) * 0.35))
            away_score += max(-0.05, min(0.05, (avg_k - k_a) * 0.35))

        side_scores = {
            'home': round(home_score, 4),
            'draw': round(draw_score, 4),
            'away': round(away_score, 4),
        }
        side_direction, side_strength = cls._direction_from_scores(side_scores)

        hcp_i = None
        hcp_f = None
        if isinstance(asian.get('initial'), dict):
            ini = asian.get('initial') or {}
            hcp_i = parse_handicap_value(
                ini.get('handicap')
                if 'handicap' in ini
                else ini.get('handicap_value')
                if 'handicap_value' in ini
                else ini.get('盘口值')
                if '盘口值' in ini
                else ini.get('handicap_text')
            )
        if isinstance(asian.get('final'), dict):
            fin = asian.get('final') or {}
            hcp_f = parse_handicap_value(
                fin.get('handicap')
                if 'handicap' in fin
                else fin.get('handicap_value')
                if 'handicap_value' in fin
                else fin.get('盘口值')
                if '盘口值' in fin
                else fin.get('handicap_text')
            )
        giver = 'none'
        if isinstance(hcp_f, float):
            if hcp_f < -0.06:
                giver = 'home'
            elif hcp_f > 0.06:
                giver = 'away'
        retreat = isinstance(hcp_i, float) and isinstance(hcp_f, float) and abs(hcp_f) + 0.12 < abs(hcp_i)

        psychology_label = 'balanced'
        psychology_strength = 0.0
        if draw_score > max(home_score, away_score) and draw_score >= 0.04:
            psychology_label = 'guard_draw'
            psychology_strength = round(draw_score, 4)
        elif giver == 'home' and home_score > 0.03 and retreat and h_water_i is not None and h_water_f is not None and (h_water_f - h_water_i) >= 0.04:
            psychology_label = 'tempt_home'
            psychology_strength = round(min(0.18, 0.05 + max(0.0, h_water_f - h_water_i) * 0.2), 4)
        elif giver == 'away' and away_score > 0.03 and retreat and a_water_i is not None and a_water_f is not None and (a_water_f - a_water_i) >= 0.04:
            psychology_label = 'tempt_away'
            psychology_strength = round(min(0.18, 0.05 + max(0.0, a_water_f - a_water_i) * 0.2), 4)
        elif side_direction == 'home':
            psychology_label = 'support_home'
            psychology_strength = round(side_strength, 4)
        elif side_direction == 'away':
            psychology_label = 'support_away'
            psychology_strength = round(side_strength, 4)

        over_i = cls._pick_market_value(totals, 'initial', 'over')
        over_f = cls._pick_market_value(totals, 'final', 'over')
        under_i = cls._pick_market_value(totals, 'initial', 'under')
        under_f = cls._pick_market_value(totals, 'final', 'under')
        ou_i = cls._pick_market_value(totals, 'initial', 'line')
        ou_f = cls._pick_market_value(totals, 'final', 'line')
        totals_scores = {'over': 0.0, 'under': 0.0}
        if ou_i is not None and ou_f is not None:
            if ou_f - ou_i >= 0.24:
                totals_scores['over'] += 0.14
            elif ou_i - ou_f >= 0.24:
                totals_scores['under'] += 0.14
        if over_i is not None and over_f is not None:
            totals_scores['over'] += max(-0.08, min(0.08, (over_i - over_f) * 0.30))
        if under_i is not None and under_f is not None:
            totals_scores['under'] += max(-0.08, min(0.08, (under_i - under_f) * 0.30))
        totals_direction, totals_strength = cls._direction_from_scores(totals_scores, min_top=0.02, min_gap=0.015)

        capital_direction = side_direction
        capital_strength = side_strength
        return {
            'available': True,
            'side_direction': side_direction,
            'side_strength': round(side_strength, 4),
            'side_scores': side_scores,
            'bookmaker_psychology': {
                'label': psychology_label,
                'strength': round(psychology_strength, 4),
                'giver': giver,
                'retreat': retreat,
            },
            'capital_flow': {
                'direction': capital_direction,
                'strength': round(capital_strength, 4),
            },
            'totals_direction': totals_direction,
            'totals_strength': round(totals_strength, 4),
        }

    @classmethod
    def _compare_market_movement_profiles(cls, current_profile: Dict[str, Any], historical_profile: Dict[str, Any]) -> Dict[str, Any]:
        current_direction = str(current_profile.get('side_direction') or 'balanced')
        historical_direction = str(historical_profile.get('side_direction') or 'balanced')
        current_psychology = (current_profile.get('bookmaker_psychology') or {}) if isinstance(current_profile.get('bookmaker_psychology'), dict) else {}
        historical_psychology = (historical_profile.get('bookmaker_psychology') or {}) if isinstance(historical_profile.get('bookmaker_psychology'), dict) else {}
        current_flow = (current_profile.get('capital_flow') or {}) if isinstance(current_profile.get('capital_flow'), dict) else {}
        historical_flow = (historical_profile.get('capital_flow') or {}) if isinstance(historical_profile.get('capital_flow'), dict) else {}

        same_direction = current_direction not in ('', 'balanced') and current_direction == historical_direction
        current_psychology_label = str(current_psychology.get('label') or 'unknown')
        historical_psychology_label = str(historical_psychology.get('label') or 'unknown')
        same_psychology = current_psychology_label not in ('', 'unknown', 'balanced') and current_psychology_label == historical_psychology_label
        current_flow_direction = str(current_flow.get('direction') or 'balanced')
        historical_flow_direction = str(historical_flow.get('direction') or 'balanced')
        same_capital_flow = current_flow_direction not in ('', 'balanced') and current_flow_direction == historical_flow_direction
        same_totals_direction = (
            str(current_profile.get('totals_direction') or 'balanced') not in ('', 'balanced')
            and str(current_profile.get('totals_direction') or 'balanced') == str(historical_profile.get('totals_direction') or 'balanced')
        )

        score = 0.0
        if same_direction:
            score += 0.45
        if same_psychology:
            score += 0.2
        if same_capital_flow:
            score += 0.2
        if same_totals_direction:
            score += 0.1
        current_strength = cls._safe_float((current_flow or {}).get('strength')) or 0.0
        historical_strength = cls._safe_float((historical_flow or {}).get('strength')) or 0.0
        strength_gap = abs(current_strength - historical_strength)
        if same_direction:
            score += max(0.0, 0.05 - min(0.05, strength_gap * 0.6))
        score = round(min(1.0, score), 4)
        return {
            'same_direction': same_direction,
            'same_psychology': same_psychology,
            'same_capital_flow': same_capital_flow,
            'same_totals_direction': same_totals_direction,
            'score': score,
            'matched_direction': current_direction if same_direction else 'balanced',
            'current_psychology': current_psychology_label,
            'historical_psychology': historical_psychology_label,
            'current_capital_flow_direction': current_flow_direction,
            'historical_capital_flow_direction': historical_flow_direction,
        }

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

    @classmethod
    def _build_market_alignment_summary(cls, matches: List[Dict[str, Any]], current_profile: Dict[str, Any]) -> Dict[str, Any]:
        aligned_matches = []
        direction_counts = {'home': 0, 'draw': 0, 'away': 0}
        same_psychology_count = 0
        same_capital_flow_count = 0
        same_totals_direction_count = 0
        total_score = 0.0
        for item in matches:
            alignment = item.get('market_movement_alignment') if isinstance(item.get('market_movement_alignment'), dict) else {}
            if not alignment:
                continue
            score = float(alignment.get('score') or 0.0)
            if score < 0.45:
                continue
            aligned_matches.append(item)
            total_score += score
            matched_direction = str(alignment.get('matched_direction') or 'balanced')
            if matched_direction in direction_counts:
                direction_counts[matched_direction] += 1
            if alignment.get('same_psychology'):
                same_psychology_count += 1
            if alignment.get('same_capital_flow'):
                same_capital_flow_count += 1
            if alignment.get('same_totals_direction'):
                same_totals_direction_count += 1
        dominant_direction = 'balanced'
        if any(direction_counts.values()):
            dominant_direction = max(direction_counts.items(), key=lambda item: item[1])[0]
            if direction_counts.get(dominant_direction, 0) <= 0:
                dominant_direction = 'balanced'
        aligned_count = len(aligned_matches)
        return {
            'current_market_profile': current_profile,
            'aligned_count': aligned_count,
            'avg_alignment_score': round(total_score / aligned_count, 4) if aligned_count else 0.0,
            'same_psychology_count': same_psychology_count,
            'same_capital_flow_count': same_capital_flow_count,
            'same_totals_direction_count': same_totals_direction_count,
            'dominant_direction': dominant_direction,
            'direction_counts': direction_counts,
            'aligned_match_ids': [str(item.get('match_id') or '') for item in aligned_matches if str(item.get('match_id') or '').strip()],
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
        current_profile = self._extract_market_movement_profile(current_odds)
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
                'market_alignment': self._build_market_alignment_summary([], current_profile),
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
            historical_profile = self._extract_market_movement_profile(match)
            market_alignment = self._compare_market_movement_profiles(current_profile, historical_profile)
            similarity = min(1.0, similarity + float(market_alignment.get('score') or 0.0) * 0.06)
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
                '大小球': match.get('大小球', {}),
                '凯利': match.get('凯利', {}),
                '离散率': match.get('离散率', {}),
                'market_movement_profile': historical_profile,
                'market_movement_alignment': market_alignment,
            })
        scored_matches.sort(key=lambda item: (-item['similarity'], item['distance']))
        top_matches = scored_matches[:top_k]
        summary = self._build_result_summary(top_matches)
        market_alignment = self._build_market_alignment_summary(top_matches, current_profile)
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
        if market_alignment.get('aligned_count', 0) >= 2:
            dominant_direction = market_alignment.get('dominant_direction')
            avg_alignment_score = float(market_alignment.get('avg_alignment_score') or 0.0)
            if dominant_direction in ('home', 'draw', 'away'):
                mapped = {'home': '主胜', 'draw': '平局', 'away': '客胜'}[dominant_direction]
                insights.append(f'相似赔率轨迹与当前盘口同向，历史走势更支持{mapped}(一致性{avg_alignment_score:.0%})')
            if int(market_alignment.get('same_psychology_count') or 0) >= 2:
                insights.append('历史样本与当前盘口操盘手法相近')
            if int(market_alignment.get('same_capital_flow_count') or 0) >= 2:
                insights.append('历史样本与当前资金流向代理一致')
        result = {
            'available': bool(top_matches),
            'league_history_count': len(history),
            'memory_history_count': memory_count,
            'matched_feature_count': max((item['matched_feature_count'] for item in top_matches), default=0),
            'similar_matches': top_matches,
            'summary': summary,
            'market_alignment': market_alignment,
            'insights': insights,
        }
        self.cache.set('find_similar_odds_matches', cache_params, result)
        return result
