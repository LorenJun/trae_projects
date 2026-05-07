"""模块说明：集中读取历史赔率快照，并转换为预测主链可用的 current_odds。"""

from __future__ import annotations

import csv
import json
import logging
import os
from glob import glob
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OddsSnapshotRepository:
    def __init__(self, base_dir: str, odds_reference: Any = None):
        self.base_dir = base_dir
        self.odds_reference = odds_reference

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
    def extract_current_odds_snapshot(match_record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'match_id': match_record.get('match_id'),
            '胜平负赔率': match_record.get('胜平负赔率', {}),
            '欧赔': match_record.get('欧赔', {}),
            '亚值': match_record.get('亚值', {}),
            '大小球': match_record.get('大小球', {}) or {},
            '凯利': match_record.get('凯利', {}),
            '离散率': match_record.get('离散率', {}),
        }

    @staticmethod
    def extract_current_odds_live_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        europe = snapshot.get('欧赔', {}) or {}
        asian = snapshot.get('亚值', {}) or {}
        kelly = snapshot.get('凯利', {}) or {}
        totals = snapshot.get('大小球', {}) or {}
        euro_block = {
            'initial': europe.get('initial', {}),
            'final': europe.get('final', {}),
        }
        if isinstance(europe, dict):
            if isinstance(europe.get('consensus'), dict):
                euro_block['consensus'] = europe.get('consensus') or {}
            if isinstance(europe.get('companies'), list):
                euro_block['companies'] = europe.get('companies') or []
            if europe.get('company_mode'):
                euro_block['company_mode'] = europe.get('company_mode')
        asian_block = {
            'initial': asian.get('initial', {}),
            'final': asian.get('final', {}),
        }
        if isinstance(asian, dict):
            if isinstance(asian.get('consensus'), dict):
                asian_block['consensus'] = asian.get('consensus') or {}
            if isinstance(asian.get('companies'), list):
                asian_block['companies'] = asian.get('companies') or []
            if asian.get('company_mode'):
                asian_block['company_mode'] = asian.get('company_mode')
        ou_block = {
            'initial': totals.get('initial', {}) if isinstance(totals, dict) else {},
            'final': totals.get('final', {}) if isinstance(totals, dict) else {},
        }
        if isinstance(totals, dict):
            if isinstance(totals.get('consensus'), dict):
                ou_block['consensus'] = totals.get('consensus') or {}
            if isinstance(totals.get('companies'), list):
                ou_block['companies'] = totals.get('companies') or []
            if totals.get('company_mode'):
                ou_block['company_mode'] = totals.get('company_mode')
        return {
            'match_id': snapshot.get('match_id'),
            '胜平负赔率': dict(euro_block),
            '欧赔': euro_block,
            '亚值': asian_block,
            '大小球': ou_block,
            '凯利': {
                'initial': kelly.get('initial', {}),
                'final': kelly.get('final', {}),
            },
            '离散率': snapshot.get('离散率', {}) or {},
        }

    def extract_current_odds_from_csv_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        f = self._to_float
        europe_initial = {
            'home': f(row.get('欧赔_初始_主')),
            'draw': f(row.get('欧赔_初始_平')),
            'away': f(row.get('欧赔_初始_客')),
        }
        europe_final = {
            'home': f(row.get('欧赔_即时_主')),
            'draw': f(row.get('欧赔_即时_平')),
            'away': f(row.get('欧赔_即时_客')),
        }
        spf_initial = {
            'home': f(row.get('胜平负_初始_主')),
            'draw': f(row.get('胜平负_初始_平')),
            'away': f(row.get('胜平负_初始_客')),
        }
        spf_final = {
            'home': f(row.get('胜平负_即时_主')),
            'draw': f(row.get('胜平负_即时_平')),
            'away': f(row.get('胜平负_即时_客')),
        }
        asian_initial = {
            'home_water': f(row.get('亚值_初始_主水')),
            'handicap': f(row.get('亚值_初始_盘口值')),
            'away_water': f(row.get('亚值_初始_客水')),
        }
        asian_final = {
            'home_water': f(row.get('亚值_即时_主水')),
            'handicap': f(row.get('亚值_即时_盘口值')),
            'away_water': f(row.get('亚值_即时_客水')),
        }
        kelly_initial = {
            'home': f(row.get('凯利_初始_主')),
            'draw': f(row.get('凯利_初始_平')),
            'away': f(row.get('凯利_初始_客')),
        }
        kelly_final = {
            'home': f(row.get('凯利_即时_主')),
            'draw': f(row.get('凯利_即时_平')),
            'away': f(row.get('凯利_即时_客')),
        }
        disc_initial = {
            'home': f(row.get('离散率_初始_主')),
            'draw': f(row.get('离散率_初始_平')),
            'away': f(row.get('离散率_初始_客')),
        }
        disc_final = {
            'home': f(row.get('离散率_即时_主')),
            'draw': f(row.get('离散率_即时_平')),
            'away': f(row.get('离散率_即时_客')),
        }
        totals_initial = {
            'line': f(row.get('大小球_初始_盘口')) if row.get('大小球_初始_盘口') is not None else f(row.get('大小球_初始_盘口值')),
            'over': f(row.get('大小球_初始_大')) if row.get('大小球_初始_大') is not None else f(row.get('大小球_初始_大球水')),
            'under': f(row.get('大小球_初始_小')) if row.get('大小球_初始_小') is not None else f(row.get('大小球_初始_小球水')),
        }
        totals_final = {
            'line': f(row.get('大小球_即时_盘口')) if row.get('大小球_即时_盘口') is not None else f(row.get('大小球_即时_盘口值')),
            'over': f(row.get('大小球_即时_大')) if row.get('大小球_即时_大') is not None else f(row.get('大小球_即时_大球水')),
            'under': f(row.get('大小球_即时_小')) if row.get('大小球_即时_小') is not None else f(row.get('大小球_即时_小球水')),
        }
        if all(value is None for value in totals_initial.values()):
            totals_initial = {}
        if all(value is None for value in totals_final.values()):
            totals_final = {}
        totals_block = {'initial': totals_initial, 'final': totals_final} if (totals_initial or totals_final) else {}
        return {
            'match_id': row.get('match_id') or row.get('page_id') or '',
            '胜平负赔率': {'initial': spf_initial, 'final': spf_final},
            '欧赔': {'initial': europe_initial, 'final': europe_final},
            '亚值': {'initial': asian_initial, 'final': asian_final},
            '大小球': totals_block,
            '凯利': {'initial': kelly_initial, 'final': kelly_final},
            '离散率': {'initial': disc_initial, 'final': disc_final},
            '_source': 'odds_snapshot.csv',
        }

    def _external_snapshot_dirs(self, league_code: str) -> List[str]:
        dirs = [
            os.path.join(self.base_dir, '.okooo-scraper', 'snapshots', league_code),
            os.path.join(self.base_dir, 'okooo_snapshots'),
            os.path.join(self.base_dir, 'okooo_snapshots', league_code),
        ]
        seen = set()
        out: List[str] = []
        for item in dirs:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def get_matches_from_odds_history(self, league_code: str, match_date: str) -> List[Dict[str, Any]]:
        matches = []
        records_by_league = getattr(self.odds_reference, 'records_by_league', {}) or {}
        for record in records_by_league.get(league_code, []):
            if record.get('match_date') != match_date:
                continue
            matches.append({
                'home_team': record.get('home_team'),
                'away_team': record.get('away_team'),
                'current_odds': self.extract_current_odds_snapshot(record),
            })
        return [m for m in matches if m.get('home_team') and m.get('away_team')]

    def get_matches_from_odds_snapshots(self, league_code: str, match_date: str) -> List[Dict[str, Any]]:
        snapshot_dir = os.path.join(self.base_dir, league_code, 'analysis', 'odds_snapshots')
        if not os.path.isdir(snapshot_dir):
            return []

        matches: List[Dict[str, Any]] = []
        for file_path in sorted(glob(os.path.join(snapshot_dir, '*_odds_snapshot.json'))):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
            except Exception:
                continue
            for record in payload.get('matches', []):
                if record.get('match_date') != match_date:
                    continue
                matches.append({
                    'home_team': record.get('home_team'),
                    'away_team': record.get('away_team'),
                    'current_odds': self.extract_current_odds_snapshot(record),
                })
        matches = [m for m in matches if m.get('home_team') and m.get('away_team')]
        if matches:
            return matches

        csv_matches: List[Dict[str, Any]] = []
        for file_path in sorted(glob(os.path.join(snapshot_dir, '*_odds_snapshot.csv'))):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('match_date') != match_date:
                            continue
                        home = (row.get('home_team') or '').strip()
                        away = (row.get('away_team') or '').strip()
                        if not home or not away:
                            continue
                        csv_matches.append({
                            'home_team': home,
                            'away_team': away,
                            'current_odds': self.extract_current_odds_from_csv_row(row),
                        })
            except Exception:
                continue
        csv_matches = [m for m in csv_matches if m.get('home_team') and m.get('away_team')]
        if csv_matches:
            logger.info("使用 CSV 赔率快照进行预测: %s %s, matches=%s", league_code, match_date, len(csv_matches))
            return csv_matches

        live_matches: List[Dict[str, Any]] = []
        for external_dir in self._external_snapshot_dirs(league_code):
            if not os.path.isdir(external_dir):
                continue
            for file_path in sorted(glob(os.path.join(external_dir, '*.json'))):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                except Exception:
                    continue
                if payload.get('match_date') != match_date:
                    continue
                home = payload.get('home_team')
                away = payload.get('away_team')
                if not home or not away:
                    continue
                live_matches.append({
                    'home_team': home,
                    'away_team': away,
                    'current_odds': self.extract_current_odds_live_snapshot(payload),
                })
        if live_matches:
            logger.info("使用外部实时快照进行预测: %s %s, matches=%s", league_code, match_date, len(live_matches))
            return live_matches
        return []

    def fill_missing_current_odds(self, league_code: str, match_date: str, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not matches:
            return matches
        need_fill = any(not m.get('current_odds') for m in matches if isinstance(m, dict))
        if not need_fill:
            return matches
        snapshot_index = {}
        for match in self.get_matches_from_odds_snapshots(league_code, match_date):
            snapshot_index[(match.get('home_team'), match.get('away_team'))] = match.get('current_odds')
        for match in matches:
            if not isinstance(match, dict) or match.get('current_odds'):
                continue
            key = (match.get('home_team') or match.get('主队'), match.get('away_team') or match.get('客队'))
            if key in snapshot_index:
                match['current_odds'] = snapshot_index[key]
        return matches
