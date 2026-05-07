#!/usr/bin/env python3
"""
比赛结果管理和准确率更新系统
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent_runtime_registry import get_runtime_profile

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 联赛名称映射
LEAGUE_NAMES = {
    'premier_league': '英超联赛',
    'serie_a': '意甲联赛',
    'bundesliga': '德甲联赛',
    'ligue_1': '法甲联赛',
    'la_liga': '西甲联赛'
}

LEAGUE_SHORT_NAMES = {
    'premier_league': '英超',
    'serie_a': '意甲',
    'bundesliga': '德甲',
    'ligue_1': '法甲',
    'la_liga': '西甲',
}

PREDICTED_WINNER_TEXT = {
    'home': '主胜',
    'draw': '平局',
    'away': '客胜',
}


class ResultManager:
    """结果管理器，直接以 teams_2025-26.md 为单一事实来源。"""

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        runtime_dir = os.path.join(self.base_dir, '.okooo-scraper', 'runtime')
        os.makedirs(runtime_dir, exist_ok=True)
        self.accuracy_file = os.path.join(runtime_dir, 'accuracy_stats.json')
        self.prediction_archive_file = os.path.join(runtime_dir, 'prediction_archive.json')
        self.upset_library_file = os.path.join(self.base_dir, '爆冷案例库.json')
        self.upset_export_file = os.path.join(self.base_dir, 'upset_cases.json')
        self.team_alias_map = self._load_team_alias_map()
        self.prediction_runtime_profile = get_runtime_profile(
            ["data_collector", "match_analyzer", "odds_analyzer"]
        )

    def _load_team_alias_map(self) -> Dict[str, Dict[str, str]]:
        """Load team alias map as {league: {alias_or_name: canonical_name}}."""
        path = Path(self.base_dir) / "okooo_team_aliases.json"
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
            league_map: Dict[str, str] = {}
            for canonical, aliases in mapping.items():
                if not canonical:
                    continue
                canonical_name = str(canonical).strip()
                league_map[canonical_name] = canonical_name
                if isinstance(aliases, list):
                    for alias in aliases:
                        alias_name = str(alias or '').strip()
                        if alias_name:
                            league_map[alias_name] = canonical_name
            out[str(league).strip()] = league_map
        return out

    def _normalize_team_name(self, league_code: str, name: str) -> str:
        raw_name = (name or '').strip()
        if not raw_name:
            return ''
        league_map = self.team_alias_map.get(league_code, {})
        return league_map.get(raw_name, raw_name)

    def _teams_md_path(self, league_code: str) -> str:
        return os.path.join(self.base_dir, league_code, 'teams_2025-26.md')

    def _match_id_for_teams_row(self, league_code: str, match_date: str, home_team: str, away_team: str) -> str:
        return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

    def _parse_score_to_winner(self, score_text: str) -> Optional[str]:
        if not isinstance(score_text, str) or '-' not in score_text:
            return None
        parts = score_text.split('-')
        if len(parts) != 2:
            return None
        try:
            hs = int(parts[0].strip())
            as_ = int(parts[1].strip())
        except Exception:
            return None
        if hs > as_:
            return 'home'
        if hs < as_:
            return 'away'
        return 'draw'

    def _parse_predicted_winner(self, note: str) -> Optional[str]:
        if not isinstance(note, str):
            return None
        # Support both:
        # - enhanced writeback: `预测:主胜 ...`
        # - legacy fragments: `预测主胜✅` / `已结束/预测平局✅`
        match = re.search(r'(?:预测\s*[:：]?\s*)(主胜|平局|客胜)', note)
        if not match:
            return None
        return {'主胜': 'home', '平局': 'draw', '客胜': 'away'}.get(match.group(1))

    def _parse_prediction_confidence(self, note: str) -> Optional[float]:
        if not isinstance(note, str):
            return None
        match = re.search(r'信心[:：]?\s*([0-9]+(?:\.[0-9]+)?)', note)
        if not match:
            return None
        try:
            value = float(match.group(1))
        except Exception:
            return None
        if value > 1:
            value = value / 100.0
        if 0 < value <= 1:
            return value
        return None

    def _parse_predicted_scores(self, note: str) -> List[str]:
        """Parse `比分:1-1/1-0` style fragments from the schedule note."""
        if not isinstance(note, str):
            return []
        m = re.search(r'比分[:\s]*([0-9\-\/]+)', note)
        if not m:
            return []
        raw = (m.group(1) or '').strip()
        scores = []
        for part in raw.split('/'):
            s = part.strip()
            if re.match(r'^\d+\s*-\s*\d+$', s):
                scores.append(re.sub(r'\s+', '', s))
        return scores

    def _parse_predicted_ou(self, note: str) -> Optional[Dict[str, object]]:
        """Parse `大小:小2.5(0.58)` fragments from the schedule note."""
        if not isinstance(note, str):
            return None
        m = re.search(r'大小[:\s]*([大小])\s*([0-9]+(?:\.[0-9]+)?)', note)
        if not m:
            return None
        side = m.group(1)
        try:
            line = float(m.group(2))
        except Exception:
            return None
        return {'side': side, 'line': line}

    def _iter_teams_rows(self):
        """Yield match rows derived from all league tables."""
        for league_code in LEAGUE_NAMES.keys():
            path = self._teams_md_path(league_code)
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.read().splitlines()
            except Exception:
                continue

            for line in lines:
                if not line.strip().startswith('|'):
                    continue
                cols = [c.strip() for c in line.strip().strip('|').split('|')]
                if len(cols) != 6:
                    continue
                match_date, match_time, home_team, score_text, away_team, note = cols
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', match_date):
                    continue
                yield {
                    'match_id': self._match_id_for_teams_row(league_code, match_date, home_team, away_team),
                    'league': league_code,
                    'league_name': LEAGUE_NAMES.get(league_code, league_code),
                    'match_date': match_date,
                    'match_time': match_time,
                    'home_team': home_team,
                    'score_text': score_text,
                    'away_team': away_team,
                    'note': note,
                }

    def _append_result_marker(self, note: str, predicted_winner: Optional[str], actual_winner: Optional[str]) -> str:
        if not note or predicted_winner not in PREDICTED_WINNER_TEXT or actual_winner not in PREDICTED_WINNER_TEXT:
            return note

        cleaned = re.sub(r'\s*[✅❌]\s*$', '', note).strip()
        marker = '✅' if predicted_winner == actual_winner else '❌'
        return f"{cleaned} {marker}".strip()

    def _load_league_standings(self, league_code: str) -> Dict[str, Dict[str, int]]:
        path = self._teams_md_path(league_code)
        table: Dict[str, Dict[str, int]] = {}
        if not os.path.exists(path):
            return table
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        except Exception:
            return table

        for line in lines:
            if not line.strip().startswith('|'):
                continue
            cols = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cols) != 10:
                continue
            if not cols[0].isdigit():
                continue
            if not cols[2].isdigit() or not cols[8].lstrip('+-').isdigit() or not cols[9].isdigit():
                continue
            team_name = cols[1]
            table[team_name] = {
                'rank': int(cols[0]),
                'played': int(cols[2]),
                'points': int(cols[9]),
            }
        return table

    def _load_snapshot_by_match_id(self, league_code: str, match_id: str) -> Optional[Dict[str, Any]]:
        if not league_code or not match_id:
            return None
        snap_dir = os.path.join(self.base_dir, '.okooo-scraper', 'snapshots', league_code)
        if not os.path.isdir(snap_dir):
            return None
        for name in os.listdir(snap_dir):
            if not name.endswith('.json'):
                continue
            file_path = os.path.join(snap_dir, name)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
                if str(payload.get('match_id') or '') == str(match_id):
                    return payload
            except Exception:
                continue
        return None

    def _get_snapshot_europe_odds(self, snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        if not isinstance(snapshot, dict):
            return None
        europe = snapshot.get('欧赔') or {}
        final = europe.get('final') or {}
        initial = europe.get('initial') or {}
        result: Dict[str, float] = {}
        for prefix, data in (('final_', final), ('initial_', initial)):
            if not isinstance(data, dict):
                continue
            for key in ('home', 'draw', 'away'):
                try:
                    result[f'{prefix}{key}'] = float(data.get(key))
                except Exception:
                    continue
        return result or None

    def _get_actual_outcome_odds(self, actual_winner: str, snapshot: Optional[Dict[str, Any]]) -> float:
        odds = self._get_snapshot_europe_odds(snapshot) or {}
        key = f"final_{actual_winner}"
        if key in odds:
            return float(odds[key])
        if actual_winner == 'draw':
            return 3.0
        return 4.0

    def _estimate_prediction_probability(
        self,
        note: str,
        predicted_winner: str,
        snapshot: Optional[Dict[str, Any]],
    ) -> float:
        conf = self._parse_prediction_confidence(note)
        if conf is not None:
            return round(conf * 100, 1)

        odds = self._get_snapshot_europe_odds(snapshot) or {}
        key = f"final_{predicted_winner}"
        odd = odds.get(key)
        if odd and odd > 1:
            return round(max(min(100.0 / odd, 95.0), 5.0), 1)
        return 50.0

    def _format_odds_change(self, snapshot: Optional[Dict[str, Any]]) -> str:
        odds = self._get_snapshot_europe_odds(snapshot) or {}
        has_initial = all(f'initial_{k}' in odds for k in ('home', 'draw', 'away'))
        has_final = all(f'final_{k}' in odds for k in ('home', 'draw', 'away'))
        if not (has_initial and has_final):
            return "缺欧赔快照"
        return (
            "欧赔初赔 "
            f"{odds['initial_home']:.2f}/{odds['initial_draw']:.2f}/{odds['initial_away']:.2f} -> "
            f"{odds['final_home']:.2f}/{odds['final_draw']:.2f}/{odds['final_away']:.2f}"
        )

    def _format_kelly_summary(self, snapshot: Optional[Dict[str, Any]]) -> str:
        if not isinstance(snapshot, dict):
            return "缺样本"
        kelly = snapshot.get('凯利') or {}
        final = kelly.get('final') or {}
        if not isinstance(final, dict):
            return "缺样本"
        values = []
        for key, label in (('home', '主胜'), ('draw', '平局'), ('away', '客胜')):
            try:
                values.append(f"{label}{float(final.get(key)):.2f}")
            except Exception:
                continue
        return ' / '.join(values) if values else "缺样本"

    def _format_handicap_anomaly(
        self,
        predicted_winner: str,
        standings_gap: int,
        snapshot: Optional[Dict[str, Any]],
    ) -> str:
        if not isinstance(snapshot, dict):
            return "缺盘口快照"
        asian = snapshot.get('亚值') or {}
        final = asian.get('final') or {}
        if not isinstance(final, dict):
            return "缺盘口快照"

        handicap = str(final.get('handicap_value') or '').strip()
        try:
            home_water = float(final.get('home_water'))
            away_water = float(final.get('away_water'))
        except Exception:
            home_water = None
            away_water = None

        if standings_gap >= 8 and predicted_winner in ('home', 'away'):
            if handicap in ('0', '0.0', '平手', ''):
                return "强侧排名优势明显但终盘仅平手，存在让步不足"
            if predicted_winner == 'home' and handicap in ('0.25', '平/半', '0/0.5'):
                return "主队优势明显但仅让平/半，存在让步不足"
            if predicted_winner == 'away' and handicap in ('-0.25', '受平/半', '-0/0.5'):
                return "客队优势明显但仅客让平/半，存在让步不足"
        if predicted_winner == 'draw':
            return "预测指向平局，重点关注强侧未兑现"
        if home_water and away_water and max(home_water, away_water) >= 1.0:
            return "终盘高水，机构对强侧赔付较为谨慎"
        return "无明显异常"

    def _build_psychology_summary(
        self,
        home_meta: Dict[str, int],
        away_meta: Dict[str, int],
        predicted_winner: str,
        actual_winner: str,
    ) -> str:
        gap = abs((home_meta or {}).get('points', 0) - (away_meta or {}).get('points', 0))
        if actual_winner == 'draw' and predicted_winner in ('home', 'away'):
            return "强侧未能兑现，比赛落入平局冷门"
        if gap >= 10:
            return "强弱分层明显，但弱侧抢分意图更强"
        return "临场执行与战意出现偏差"

    def _load_prediction_archive(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.prediction_archive_file):
            return {}
        try:
            with open(self.prediction_archive_file, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _normalized_prediction_runtime_profile(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self.prediction_runtime_profile, ensure_ascii=False))

    def _prediction_archive_entry_needs_migration(self, entry: Dict[str, Any]) -> bool:
        if not isinstance(entry, dict):
            return True
        runtime_profile = entry.get("runtime_profile")
        if not isinstance(runtime_profile, dict):
            return True
        return runtime_profile != self.prediction_runtime_profile

    def migrate_prediction_archive_runtime_profiles(self) -> Dict[str, Any]:
        archive = self._load_prediction_archive()
        total_records = len(archive)
        migrated_count = 0

        for match_id, entry in list(archive.items()):
            if not isinstance(entry, dict):
                archive[match_id] = {
                    "match_id": str(match_id),
                    "runtime_profile": self._normalized_prediction_runtime_profile(),
                }
                migrated_count += 1
                continue
            if self._prediction_archive_entry_needs_migration(entry):
                entry["runtime_profile"] = self._normalized_prediction_runtime_profile()
                archive[match_id] = entry
                migrated_count += 1

        if migrated_count:
            self._save_prediction_archive(archive)

        return {
            "archive_file": self.prediction_archive_file,
            "total_records": total_records,
            "migrated_records": migrated_count,
            "dimension_schema_version": self.prediction_runtime_profile.get("dimension_schema_version"),
            "agent_roles": self.prediction_runtime_profile.get("agent_roles", []),
            "updated_at": datetime.now().isoformat(),
        }

    def _save_prediction_archive(self, archive: Dict[str, Dict[str, Any]]) -> None:
        with open(self.prediction_archive_file, 'w', encoding='utf-8') as f:
            json.dump(archive, f, ensure_ascii=False, indent=2)

    def _archive_prediction(self, prediction_data: Dict[str, Any]) -> None:
        match_id = str(prediction_data.get('match_id') or '').strip()
        if not match_id:
            return
        archive = self._load_prediction_archive()
        archive[match_id] = prediction_data
        self._save_prediction_archive(archive)

    def _hydrate_prediction_fields(self, result_data: Dict[str, Any]) -> Dict[str, Any]:
        hydrated = dict(result_data)
        if hydrated.get('predicted_winner') in PREDICTED_WINNER_TEXT:
            return hydrated

        archive = self._load_prediction_archive()
        archived = archive.get(str(hydrated.get('match_id') or '')) or {}
        archived_predicted_winner = archived.get('predicted_winner')
        if archived_predicted_winner in PREDICTED_WINNER_TEXT:
            hydrated['predicted_winner'] = archived_predicted_winner
            hydrated['predicted_scores'] = archived.get('predicted_scores') or hydrated.get('predicted_scores') or []
            hydrated['predicted_ou'] = archived.get('predicted_ou') or hydrated.get('predicted_ou')
            if not hydrated.get('note'):
                hydrated['note'] = archived.get('note') or ''
            if archived.get('confidence') and '信心:' not in str(hydrated.get('note') or ''):
                hydrated['note'] = (
                    f"{hydrated.get('note', '').strip()} 信心:{float(archived['confidence']):.2f}"
                ).strip()
        return hydrated

    def _should_record_upset_case(
        self,
        predicted_winner: str,
        actual_winner: str,
        prediction_probability: float,
        home_meta: Dict[str, int],
        away_meta: Dict[str, int],
        snapshot: Optional[Dict[str, Any]],
    ) -> bool:
        if predicted_winner not in PREDICTED_WINNER_TEXT or actual_winner not in PREDICTED_WINNER_TEXT:
            return False
        if predicted_winner == actual_winner:
            return False

        points_gap = abs((home_meta or {}).get('points', 0) - (away_meta or {}).get('points', 0))
        rank_gap = abs((home_meta or {}).get('rank', 0) - (away_meta or {}).get('rank', 0))
        actual_odds = self._get_actual_outcome_odds(actual_winner, snapshot)

        if actual_winner == 'draw' and predicted_winner in ('home', 'away'):
            return True
        if prediction_probability >= 55.0:
            return True
        if rank_gap >= 5 or points_gap >= 10:
            return True
        if actual_odds >= 3.4:
            return True
        return False

    def _sync_upset_export_file(self, case_list: List[Dict[str, Any]]) -> None:
        payload = {
            'cases': case_list,
            'imported_at': datetime.now().isoformat(),
        }
        with open(self.upset_export_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _auto_sync_upset_case(self, result_data: Dict[str, Any]) -> Dict[str, Any]:
        result_data = self._hydrate_prediction_fields(result_data)
        predicted_winner = result_data.get('predicted_winner')
        actual_winner = result_data.get('actual_winner')
        note = result_data.get('note') or ''
        if predicted_winner not in PREDICTED_WINNER_TEXT or actual_winner not in PREDICTED_WINNER_TEXT:
            return {'status': 'skipped', 'reason': 'missing_prediction_or_result'}

        standings = self._load_league_standings(result_data['league'])
        home_meta = standings.get(result_data['home_team'], {})
        away_meta = standings.get(result_data['away_team'], {})
        snapshot = self._load_snapshot_by_match_id(result_data['league'], result_data['match_id'])
        prediction_probability = self._estimate_prediction_probability(note, predicted_winner, snapshot)

        if not self._should_record_upset_case(
            predicted_winner=predicted_winner,
            actual_winner=actual_winner,
            prediction_probability=prediction_probability,
            home_meta=home_meta,
            away_meta=away_meta,
            snapshot=snapshot,
        ):
            return {'status': 'skipped', 'reason': 'did_not_meet_upset_threshold'}

        from upset_case_library import 创建爆冷案例, 爆冷案例库

        case_library = 爆冷案例库(self.upset_library_file)
        case = 创建爆冷案例(
            比赛日期=result_data['match_date'],
            联赛=LEAGUE_SHORT_NAMES.get(result_data['league'], result_data['league_name']),
            轮次='自动同步',
            主队=result_data['home_team'],
            客队=result_data['away_team'],
            预测结果=PREDICTED_WINNER_TEXT[predicted_winner],
            实际结果=PREDICTED_WINNER_TEXT[actual_winner],
            预测比分='/'.join(result_data.get('predicted_scores') or []) or '-',
            实际比分=result_data['actual_score'],
            预测概率=prediction_probability,
            主队排名=int(home_meta.get('rank') or 0),
            客队排名=int(away_meta.get('rank') or 0),
            主队积分=int(home_meta.get('points') or 0),
            客队积分=int(away_meta.get('points') or 0),
            伤病影响='自动同步，待人工补充',
            战术变化='赛果回填后自动识别为预测偏离案例',
            心理因素=self._build_psychology_summary(home_meta, away_meta, predicted_winner, actual_winner),
            赔率变化=self._format_odds_change(snapshot),
            凯利指数=self._format_kelly_summary(snapshot),
            盘口异常=self._format_handicap_anomaly(
                predicted_winner,
                abs(int(home_meta.get('rank') or 0) - int(away_meta.get('rank') or 0)),
                snapshot,
            ),
            爆冷原因分析=(
                f"自动同步识别：赛前预测为{PREDICTED_WINNER_TEXT[predicted_winner]}，"
                f"实际结果为{PREDICTED_WINNER_TEXT[actual_winner]}，"
                f"比分 {result_data['actual_score']}。"
            ),
            改进建议='复盘该场赛前伤停、盘口与临场变盘，必要时补充更细的人工标签。',
        )
        if actual_winner == 'draw' and predicted_winner in ('home', 'away'):
            case.爆冷类型 = '平局大师'

        added = case_library.添加案例(case)
        case_dict = {
            '案例ID': case.案例ID,
            '比赛日期': case.比赛日期,
            '联赛': case.联赛,
            '轮次': case.轮次,
            '主队': case.主队,
            '客队': case.客队,
            '预测结果': case.预测结果,
            '实际结果': case.实际结果,
            '预测比分': case.预测比分,
            '实际比分': case.实际比分,
            '预测概率': case.预测概率,
            '实际爆冷赔率': case.实际爆冷赔率,
            '爆冷等级': case.爆冷等级,
            '爆冷类型': case.爆冷类型,
            '主队排名': case.主队排名,
            '客队排名': case.客队排名,
            '排名差': case.排名差,
            '主队积分': case.主队积分,
            '客队积分': case.客队积分,
            '积分差': case.积分差,
            '伤病影响': case.伤病影响,
            '战术变化': case.战术变化,
            '心理因素': case.心理因素,
            '赔率变化': case.赔率变化,
            '凯利指数': case.凯利指数,
            '盘口异常': case.盘口异常,
            '爆冷原因分析': case.爆冷原因分析,
            '改进建议': case.改进建议,
            '记录时间': case.记录时间,
        }
        self._sync_upset_export_file([vars(item) for item in case_library.案例列表])

        status = 'added' if added else 'duplicate'
        return {
            'status': status,
            'case_id': case.案例ID,
            'upset_level': case.爆冷等级,
            'upset_type': case.爆冷类型,
        }

    def _update_teams_row_score(self, match_id: str, home_score: int, away_score: int) -> Dict:
        for league_code in LEAGUE_NAMES.keys():
            path = self._teams_md_path(league_code)
            if not os.path.exists(path):
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.read().splitlines(True)
            except Exception:
                continue

            changed = False
            updated_row = None
            out_lines = []

            for line in lines:
                if not line.strip().startswith('|'):
                    out_lines.append(line)
                    continue

                cols = [c.strip() for c in line.strip().strip('|').split('|')]
                if len(cols) != 6:
                    out_lines.append(line)
                    continue

                match_date, match_time, home_team, _score_text, away_team, note = cols
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', match_date):
                    out_lines.append(line)
                    continue

                current_match_id = self._match_id_for_teams_row(league_code, match_date, home_team, away_team)
                if current_match_id != match_id:
                    out_lines.append(line)
                    continue

                actual_score = f"{home_score}-{away_score}"
                actual_winner = self._parse_score_to_winner(actual_score)
                predicted_winner = self._parse_predicted_winner(note)
                cols[3] = actual_score
                cols[5] = self._append_result_marker(note, predicted_winner, actual_winner)
                out_lines.append("| " + " | ".join(cols) + " |\n")
                changed = True
                updated_row = {
                    'match_id': current_match_id,
                    'league': league_code,
                    'league_name': LEAGUE_NAMES.get(league_code, league_code),
                    'match_date': match_date,
                    'match_time': match_time,
                    'home_team': home_team,
                    'away_team': away_team,
                    'actual_winner': actual_winner,
                    'actual_score': actual_score,
                    'home_score': home_score,
                    'away_score': away_score,
                    'note': cols[5],
                    'predicted_winner': predicted_winner,
                    'predicted_scores': self._parse_predicted_scores(cols[5]),
                    'predicted_ou': self._parse_predicted_ou(cols[5]),
                    'saved_at': datetime.now().isoformat(),
                }

            if changed:
                with open(path, 'w', encoding='utf-8') as f:
                    f.writelines(out_lines)
                return updated_row

        raise ValueError(f"找不到比赛: {match_id}")

    def load_predictions(self) -> List[Dict]:
        """从赛程表备注列提取预测信息。"""
        predictions: List[Dict] = []
        for row in self._iter_teams_rows():
            predicted_winner = self._parse_predicted_winner(row['note'])
            if predicted_winner not in PREDICTED_WINNER_TEXT:
                continue
            predictions.append({
                'match_id': row['match_id'],
                'league': row['league'],
                'league_name': row['league_name'],
                'home_team': row['home_team'],
                'away_team': row['away_team'],
                'match_date': row['match_date'],
                'match_time': row['match_time'],
                'predicted_winner': predicted_winner,
                'saved_at': row['match_date'],
            })
        return predictions

    def load_results(self) -> List[Dict]:
        """从赛程表比分列提取完赛结果。"""
        results: List[Dict] = []
        for row in self._iter_teams_rows():
            if not re.match(r'^\d+\s*-\s*\d+$', row['score_text'] or ''):
                continue
            actual_winner = self._parse_score_to_winner(row['score_text'])
            if actual_winner not in PREDICTED_WINNER_TEXT:
                continue
            home_score, away_score = [int(x.strip()) for x in row['score_text'].split('-')]
            results.append({
                'match_id': row['match_id'],
                'league': row['league'],
                'league_name': row['league_name'],
                'home_team': row['home_team'],
                'away_team': row['away_team'],
                'match_date': row['match_date'],
                'match_time': row['match_time'],
                'actual_winner': actual_winner,
                'actual_score': row['score_text'],
                'home_score': home_score,
                'away_score': away_score,
                'result_status': 'completed',
                'saved_at': row['match_date'],
            })
        return results

    def save_result(self, identifier: str, home_score: int, away_score: int, league: Optional[str] = None, date_override: Optional[str] = None):
        """保存比赛结果到 teams_2025-26.md，支持 match_id 或者球队名模糊匹配。"""
        if " vs " in identifier or "VS" in identifier:
            parts = re.split(r'\s+vs\s+', identifier, flags=re.IGNORECASE)
            home_candidate = parts[0].strip()
            away_candidate = parts[1].strip()
            result_data = self._update_teams_row_score_by_team_names(home_candidate, away_candidate, home_score, away_score, league=league, date_override=date_override)
            result_data['upset_sync'] = self._auto_sync_upset_case(result_data)
            logger.info("保存比赛结果: %s vs %s -> %s", home_candidate, away_candidate, result_data['actual_score'])
            return result_data
        else:
            result_data = self._update_teams_row_score(identifier, home_score, away_score)
            result_data['upset_sync'] = self._auto_sync_upset_case(result_data)
            logger.info("保存比赛结果: %s -> %s", identifier, result_data['actual_score'])
            return result_data

    def _update_teams_row_score_by_team_names(self, home_candidate: str, away_candidate: str, home_score: int, away_score: int, league: Optional[str] = None, date_override: Optional[str] = None) -> Dict:
        """通过球队名模糊匹配来更新比赛结果。"""
        leagues_to_check = [league] if league else list(LEAGUE_NAMES.keys())

        def fuzzy_match_team(team_candidate, actual_team, league_code):
            if not team_candidate or not actual_team:
                return False
            normalized_candidate = self._normalize_team_name(league_code, team_candidate)
            normalized_actual = self._normalize_team_name(league_code, actual_team)
            t1 = normalized_candidate.replace(" ", "").lower()
            t2 = normalized_actual.replace(" ", "").lower()
            if t1 == t2:
                return True
            if t1 in t2 or t2 in t1:
                return True
            # Common suffix/prefix normalization
            for suffix in ["足球俱乐部", "俱乐部", "足球", "队", "FC", "fc", "竞技", "竞技队"]:
                t1_clean = t1.replace(suffix.lower(), "")
                t2_clean = t2.replace(suffix.lower(), "")
                if t1_clean == t2_clean:
                    return True
            return False

        def exact_match_score(team_candidate, actual_team, league_code):
            normalized_candidate = self._normalize_team_name(league_code, team_candidate).replace(" ", "").lower()
            normalized_actual = self._normalize_team_name(league_code, actual_team).replace(" ", "").lower()
            return 2 if normalized_candidate == normalized_actual else 1

        def date_distance(match_date: str, target_date: Optional[str]) -> int:
            if not target_date:
                return 0
            try:
                dt1 = datetime.strptime(match_date, "%Y-%m-%d")
                dt2 = datetime.strptime(target_date, "%Y-%m-%d")
                return abs((dt1 - dt2).days)
            except Exception:
                return 999

        # Daily league pages may surface the same round up to a few days ahead of kickoff.
        # Allow a slightly wider window so batch backfill can still land on the scheduled row.
        date_window = 4 if date_override else None

        for league_code in leagues_to_check:
            path = self._teams_md_path(league_code)
            if not os.path.exists(path):
                continue

            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.read().splitlines(True)
            except Exception:
                continue

            candidates = []
            for idx, line in enumerate(lines):
                if not line.strip().startswith('|'):
                    continue

                cols = [c.strip() for c in line.strip().strip('|').split('|')]
                if len(cols) != 6:
                    continue

                match_date, match_time, home_team, score_text, away_team, note = cols
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', match_date):
                    continue

                dist = date_distance(match_date, date_override)
                if date_window is not None and dist > date_window:
                    continue

                direct_home = fuzzy_match_team(home_candidate, home_team, league_code)
                reverse_home = fuzzy_match_team(home_candidate, away_team, league_code)
                direct_away = fuzzy_match_team(away_candidate, away_team, league_code)
                reverse_away = fuzzy_match_team(away_candidate, home_team, league_code)

                orientation = None
                match_quality = -1
                if direct_home and direct_away:
                    orientation = 'direct'
                    match_quality = exact_match_score(home_candidate, home_team, league_code) + exact_match_score(away_candidate, away_team, league_code)
                elif reverse_home and reverse_away:
                    orientation = 'reverse'
                    match_quality = exact_match_score(home_candidate, away_team, league_code) + exact_match_score(away_candidate, home_team, league_code)

                if not orientation:
                    continue

                has_score = bool(re.match(r'^\d+\s*-\s*\d+$', score_text))
                candidates.append({
                    'index': idx,
                    'line': line,
                    'cols': cols,
                    'match_date': match_date,
                    'match_time': match_time,
                    'home_team': home_team,
                    'away_team': away_team,
                    'note': note,
                    'score_text': score_text,
                    'date_distance': dist,
                    'orientation': orientation,
                    'match_quality': match_quality,
                    'has_score': has_score,
                })

            if not candidates:
                continue

            candidates.sort(
                key=lambda item: (
                    item['date_distance'],
                    0 if item['orientation'] == 'direct' else 1,
                    0 if item['has_score'] else 1,
                    -item['match_quality'],
                    item['match_date'],
                    item['index'],
                )
            )
            chosen = candidates[0]

            cols = list(chosen['cols'])
            match_date = chosen['match_date']
            match_time = chosen['match_time']
            home_team = chosen['home_team']
            away_team = chosen['away_team']
            note = chosen['note']
            score_text = chosen['score_text']

            if chosen['has_score']:
                print(f"⚠️  比赛已存在比分，跳过: {home_team} vs {away_team} 当前比分={score_text}")
                actual_winner = self._parse_score_to_winner(score_text)
                return {
                    'match_id': self._match_id_for_teams_row(league_code, match_date, home_team, away_team),
                    'league': league_code,
                    'league_name': LEAGUE_NAMES.get(league_code, league_code),
                    'match_date': match_date,
                    'match_time': match_time,
                    'home_team': home_team,
                    'away_team': away_team,
                    'actual_winner': actual_winner,
                    'actual_score': score_text,
                    'home_score': int(score_text.split('-')[0].strip()),
                    'away_score': int(score_text.split('-')[1].strip()),
                    'note': note,
                    'predicted_winner': self._parse_predicted_winner(note),
                    'predicted_scores': self._parse_predicted_scores(note),
                    'predicted_ou': self._parse_predicted_ou(note),
                    'saved_at': datetime.now().isoformat(),
                    'already_exists': True,
                    'matched_date_offset': chosen['date_distance'],
                }

            actual_score = f"{home_score}-{away_score}"
            actual_winner = self._parse_score_to_winner(actual_score)
            predicted_winner = self._parse_predicted_winner(note)
            cols[3] = actual_score
            cols[5] = self._append_result_marker(note, predicted_winner, actual_winner)
            lines[chosen['index']] = "| " + " | ".join(cols) + " |\n"
            with open(path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return {
                'match_id': self._match_id_for_teams_row(league_code, match_date, home_team, away_team),
                'league': league_code,
                'league_name': LEAGUE_NAMES.get(league_code, league_code),
                'match_date': match_date,
                'match_time': match_time,
                'home_team': home_team,
                'away_team': away_team,
                'actual_winner': actual_winner,
                'actual_score': actual_score,
                'home_score': home_score,
                'away_score': away_score,
                'note': cols[5],
                'predicted_winner': predicted_winner,
                'predicted_scores': self._parse_predicted_scores(cols[5]),
                'predicted_ou': self._parse_predicted_ou(cols[5]),
                'saved_at': datetime.now().isoformat(),
                'matched_date_offset': chosen['date_distance'],
            }

        raise ValueError(f"找不到比赛: {home_candidate} vs {away_candidate} (league={league}, date={date_override})")

    def get_pending_matches(self, days_back: int = 7) -> List[Dict]:
        """获取近期已预测但未写入比分的比赛。"""
        result_ids = {r['match_id'] for r in self.load_results()}
        cutoff = datetime.now().timestamp() - (days_back * 86400)
        pending = []

        for pred in self.load_predictions():
            if pred['match_id'] in result_ids:
                continue
            try:
                saved_time = datetime.fromisoformat(pred['saved_at']).timestamp()
                if saved_time < cutoff:
                    continue
            except Exception:
                pass
            pending.append(pred)

        return pending

    def calculate_accuracy(self, league: Optional[str] = None, days: int = 30) -> Dict:
        """计算预测胜负准确率。"""
        total = 0
        correct = 0
        total_score = 0
        correct_score = 0
        total_ou = 0
        correct_ou = 0
        cutoff = datetime.now().timestamp() - (days * 86400)

        for row in self._iter_teams_rows():
            if league and row['league'] != league:
                continue
            try:
                match_ts = datetime.fromisoformat(row['match_date']).timestamp()
                if match_ts < cutoff:
                    continue
            except Exception:
                pass

            predicted_winner = self._parse_predicted_winner(row['note'])
            actual_winner = self._parse_score_to_winner(row['score_text']) if re.match(r'^\d+\s*-\s*\d+$', row['score_text'] or '') else None
            if predicted_winner not in PREDICTED_WINNER_TEXT or actual_winner not in PREDICTED_WINNER_TEXT:
                continue

            total += 1
            if predicted_winner == actual_winner:
                correct += 1

            # Score accuracy: hit if actual score is within top-2 predicted scores.
            predicted_scores = self._parse_predicted_scores(row['note'])
            if predicted_scores:
                total_score += 1
                actual_score = re.sub(r'\s+', '', (row['score_text'] or '').strip())
                if actual_score in predicted_scores:
                    correct_score += 1

            # Over/Under accuracy: based on note line (normally 2.5 to avoid push).
            ou = self._parse_predicted_ou(row['note'])
            if ou and isinstance(ou.get('line'), (int, float)) and ou.get('side') in ('大', '小'):
                try:
                    hs, as_ = [int(x.strip()) for x in (row['score_text'] or '').split('-')]
                    total_goals = hs + as_
                except Exception:
                    total_goals = None
                if isinstance(total_goals, int):
                    line = float(ou['line'])
                    side = ou['side']
                    # Push handling: if total_goals equals the line exactly, do not count.
                    if abs(total_goals - line) < 1e-9:
                        continue
                    total_ou += 1
                    actual_side = '大' if total_goals > line else '小'
                    if actual_side == side:
                        correct_ou += 1

        accuracy = (correct / total * 100) if total > 0 else 0
        score_acc = (correct_score / total_score * 100) if total_score > 0 else 0
        ou_acc = (correct_ou / total_ou * 100) if total_ou > 0 else 0
        return {
            'league': league,
            'total_predictions': total,
            'correct_predictions': correct,
            'win_accuracy': round(accuracy, 2),
            'total_score_predictions': total_score,
            'correct_score_predictions': correct_score,
            'score_accuracy': round(score_acc, 2),
            'total_ou_predictions': total_ou,
            'correct_ou_predictions': correct_ou,
            'ou_accuracy': round(ou_acc, 2),
            'model_accuracy': {},
            'calculated_at': datetime.now().isoformat(),
            'days': days
        }

    def update_accuracy_stats(self):
        """更新准确率统计。"""
        stats = {
            'overall': self.calculate_accuracy(),
            'by_league': {},
            'last_updated': datetime.now().isoformat()
        }

        for league_code in LEAGUE_NAMES.keys():
            stats['by_league'][league_code] = self.calculate_accuracy(league=league_code)

        with open(self.accuracy_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        logger.info("准确率统计已更新")
        return stats

    def save_prediction_from_enhanced(self, enhanced_pred: Dict, league_code: str):
        """兼容旧调用方，返回基于 teams 文件可推导的预测信息。"""
        match_date = enhanced_pred.get('match_date', datetime.now().strftime('%Y-%m-%d'))
        home_team = enhanced_pred.get('home_team', '')
        away_team = enhanced_pred.get('away_team', '')
        match_id = self._match_id_for_teams_row(league_code, match_date, home_team, away_team)

        prediction_result = enhanced_pred.get('prediction', '')
        predicted_winner = {'主胜': 'home', '客胜': 'away', '平局': 'draw'}.get(prediction_result)
        top_scores = enhanced_pred.get('top_scores', [])
        predicted_scores = [score for score, _prob in top_scores[:2]] if top_scores else []
        predicted_score = '/'.join(predicted_scores) if predicted_scores else ''
        predicted_ou = None
        over_under = enhanced_pred.get('over_under', {})
        if isinstance(over_under, dict) and isinstance(over_under.get('line'), (int, float)):
            predicted_ou = {
                'side': '大' if over_under.get('over', 0) > over_under.get('under', 0) else '小',
                'line': float(over_under.get('line')),
            }

        prediction_data = {
            'match_id': match_id,
            'league': league_code,
            'league_name': LEAGUE_NAMES.get(league_code, league_code),
            'home_team': home_team,
            'away_team': away_team,
            'match_date': match_date,
            'match_time': '',
            'predicted_winner': predicted_winner,
            'predicted_score': predicted_score,
            'predicted_scores': predicted_scores,
            'predicted_probability': str(enhanced_pred.get('confidence', '')),
            'over_under': '大球' if enhanced_pred.get('over_under', {}).get('over', 0) > 0.5 else '小球',
            'predicted_ou': predicted_ou,
            'correct': False,
            'model_predictions': enhanced_pred.get('model_predictions', {}),
            'runtime_profile': enhanced_pred.get('runtime_profile', {}),
            'full_prediction': enhanced_pred,
            'saved_at': datetime.now().isoformat()
        }
        self._archive_prediction(
            {
                'match_id': match_id,
                'league': league_code,
                'match_date': match_date,
                'home_team': home_team,
                'away_team': away_team,
                'predicted_winner': predicted_winner,
                'predicted_scores': predicted_scores,
                'predicted_ou': predicted_ou,
                'confidence': enhanced_pred.get('confidence'),
                'runtime_profile': enhanced_pred.get('runtime_profile', {}),
                'note': f"预测:{prediction_result} 信心:{float(enhanced_pred.get('confidence') or 0):.2f}".strip(),
                'archived_at': datetime.now().isoformat(),
            }
        )
        logger.info("预测已写入 teams 文件，兼容返回预测对象: %s", match_id)
        return prediction_data


def print_accuracy_report(stats: Dict):
    """打印准确率报告"""
    print("\n" + "=" * 80)
    print("📊 预测准确率统计报告")
    print("=" * 80)
    
    overall = stats['overall']
    print(f"\n【总体统计】")
    print(f"  总预测数: {overall['total_predictions']}")
    print(f"  正确预测: {overall['correct_predictions']}")
    print(f"  胜负准确率: {overall['win_accuracy']}%")
    if overall.get('total_score_predictions', 0) > 0:
        print(f"  比分准确率: {overall['score_accuracy']}% ({overall['correct_score_predictions']}/{overall['total_score_predictions']})")
    if overall.get('total_ou_predictions', 0) > 0:
        print(f"  大小球准确率: {overall['ou_accuracy']}% ({overall['correct_ou_predictions']}/{overall['total_ou_predictions']})")
    
    print(f"\n【各联赛统计】")
    for league_code, league_stats in stats['by_league'].items():
        league_name = LEAGUE_NAMES.get(league_code, league_code)
        if league_stats['total_predictions'] > 0:
            print(f"  {league_name}: {league_stats['win_accuracy']}% ({league_stats['correct_predictions']}/{league_stats['total_predictions']})")
        else:
            print(f"  {league_name}: 暂无数据")
    
    print(f"\n【最后更新】: {stats.get('last_updated', '未知')}")
    print("=" * 80 + "\n")


def interactive_update():
    """交互式更新比赛结果"""
    manager = ResultManager()
    
    print("=" * 80)
    print("🏆 比赛结果更新系统")
    print("=" * 80)
    
    while True:
        print("\n请选择操作:")
        print("1. 查看待更新的比赛")
        print("2. 输入比赛结果")
        print("3. 查看准确率统计")
        print("4. 重新计算准确率")
        print("0. 退出")
        
        choice = input("\n请输入选项: ").strip()
        
        if choice == '0':
            print("👋 再见!")
            break
        
        elif choice == '1':
            pending = manager.get_pending_matches()
            print(f"\n📋 待更新结果的比赛 (共{len(pending)}场):")
            if not pending:
                print("  暂无待更新的比赛")
            else:
                for idx, pred in enumerate(pending[:20], 1):
                    print(f"\n  {idx}. {pred['league_name']} - {pred['match_date']}")
                    print(f"     {pred['home_team']} vs {pred['away_team']}")
                    pred_result = pred.get('predicted_winner', '')
                    result_text = {'home': '主胜', 'away': '客胜', 'draw': '平局'}.get(pred_result, pred_result)
                    print(f"     预测: {result_text} (ID: {pred['match_id']})")
                if len(pending) > 20:
                    print(f"\n  ... 还有 {len(pending) - 20} 场")
        
        elif choice == '2':
            pending = manager.get_pending_matches()
            
            if not pending:
                print("⚠️  暂无待更新的比赛")
                continue
            
            print("\n📋 请选择要更新的比赛:")
            for idx, pred in enumerate(pending[:20], 1):
                print(f"  {idx}. {pred['home_team']} vs {pred['away_team']} ({pred['league_name']})")
            
            match_idx = input("\n请输入序号 (或直接输入比赛ID): ").strip()
            
            match_id = None
            if match_idx.isdigit():
                idx = int(match_idx) - 1
                if 0 <= idx < len(pending):
                    match_id = pending[idx]['match_id']
            else:
                match_id = match_idx
            
            if not match_id:
                print("❌ 无效输入")
                continue
            
            # 查找比赛信息
            pred_info = None
            for pred in pending:
                if pred['match_id'] == match_id:
                    pred_info = pred
                    break
            
            if not pred_info:
                # 从所有预测中查找
                all_preds = manager.load_predictions()
                for pred in all_preds:
                    if pred['match_id'] == match_id:
                        pred_info = pred
                        break
            
            if pred_info:
                print(f"\n更新比赛: {pred_info['home_team']} vs {pred_info['away_team']}")
                
                home_score = input("主队进球: ").strip()
                away_score = input("客队进球: ").strip()
                
                if home_score.isdigit() and away_score.isdigit():
                    manager.save_result(match_id, int(home_score), int(away_score))
                    manager.update_accuracy_stats()
                    print("✅ 结果已保存!")
                else:
                    print("❌ 无效的比分输入")
            else:
                print(f"❌ 找不到比赛: {match_id}")
        
        elif choice == '3':
            try:
                with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                print_accuracy_report(stats)
            except Exception as e:
                print(f"❌ 读取统计失败: {e}")
        
        elif choice == '4':
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
        
        else:
            print("❌ 无效选项")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='比赛结果管理和准确率更新')
    parser.add_argument('--interactive', '-i', action='store_true', help='进入交互模式')
    parser.add_argument('--update-accuracy', '-u', action='store_true', help='更新准确率统计')
    parser.add_argument('--show-accuracy', '-s', action='store_true', help='显示准确率统计')
    
    args = parser.parse_args()
    
    manager = ResultManager()
    
    if args.interactive:
        interactive_update()
    elif args.update_accuracy:
        stats = manager.update_accuracy_stats()
        print_accuracy_report(stats)
    elif args.show_accuracy:
        try:
            with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            print_accuracy_report(stats)
        except Exception as e:
            print(f"❌ 读取统计失败: {e}")
    else:
        interactive_update()


if __name__ == '__main__':
    main()
