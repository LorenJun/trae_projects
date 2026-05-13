#!/usr/bin/env python3
"""模块说明：负责赛果写回、预测归档、准确率统计、爆冷案例同步与结果管理。

比赛结果管理和准确率更新系统"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent_runtime_registry import get_runtime_profile
from collectors.aliasing import load_team_alias_map
from runtime.memory_samples import sync_prediction_memory_samples
from runtime.paths import get_default_paths
from runtime.rag_store import sync_rag_index
from storage import AccuracyStatsStore, PredictionArchiveStore, TeamsMarkdownStore

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
    'la_liga': '西甲联赛',
    'europa_league': '欧联',
    'champions_league': '欧冠',
    'conference_league': '欧协联',
}

LEAGUE_SHORT_NAMES = {
    'premier_league': '英超',
    'serie_a': '意甲',
    'bundesliga': '德甲',
    'ligue_1': '法甲',
    'la_liga': '西甲',
    'europa_league': '欧联',
    'champions_league': '欧冠',
    'conference_league': '欧协联',
}

COMPETITION_ALIASES = {
    'europa_league': ('europa_league', '欧联', '欧罗巴'),
    'champions_league': ('champions_league', '欧冠'),
    'conference_league': ('conference_league', '欧协联'),
}

SNAPSHOT_DIR_ALIASES = {
    'europa_league': ('europa_league', '欧联', '欧罗巴'),
    'champions_league': ('champions_league', '欧冠'),
    'conference_league': ('conference_league', '欧协联'),
}

PREDICTED_WINNER_TEXT = {
    'home': '主胜',
    'draw': '平局',
    'away': '客胜',
}


class ResultManager:
    """结果管理器，维护联赛 SoT、runtime-only 归档与统一准确率统计。"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.paths = get_default_paths(self.base_dir)
        self.accuracy_store = AccuracyStatsStore(self.base_dir)
        self.prediction_archive_store = PredictionArchiveStore(self.base_dir)
        self.teams_store = TeamsMarkdownStore(self.base_dir)
        self.accuracy_file = str(self.accuracy_store.path)
        self.prediction_archive_file = str(self.prediction_archive_store.path)
        self.upset_library_file = os.path.join(self.base_dir, '爆冷案例库.json')
        self.upset_export_file = os.path.join(self.base_dir, 'upset_cases.json')
        self.team_alias_map = self._load_team_alias_map()
        self.prediction_runtime_profile = get_runtime_profile(
            ["data_collector", "match_analyzer", "odds_analyzer"]
        )

    def _load_team_alias_map(self) -> Dict[str, Dict[str, str]]:
        """Load team alias map as {league: {alias_or_name: canonical_name}}."""
        return load_team_alias_map(self.base_dir)

    def _normalize_team_name(self, league_code: str, name: str) -> str:
        raw_name = (name or '').strip()
        if not raw_name:
            return ''
        league_map = self.team_alias_map.get(league_code, {})
        return league_map.get(raw_name, raw_name)

    def _teams_md_path(self, league_code: str) -> str:
        return self.teams_store.path_for_league(league_code)

    def _match_id_for_teams_row(self, league_code: str, match_date: str, home_team: str, away_team: str) -> str:
        return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

    def _find_existing_teams_match_id(self, league_code: str, match_date: str, home_team: str, away_team: str) -> str:
        if league_code not in LEAGUE_NAMES:
            return ''
        path = self._teams_md_path(league_code)
        if not os.path.exists(path):
            return ''
        normalized_date = str(match_date or '').strip()
        normalized_home = self._normalize_team_name(league_code, home_team)
        normalized_away = self._normalize_team_name(league_code, away_team)
        if not (normalized_date and normalized_home and normalized_away):
            return ''
        try:
            lines = self.teams_store.read_lines(league_code)
        except Exception:
            return ''
        for line in lines:
            if not line.strip().startswith('|'):
                continue
            cols = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cols) != 6:
                continue
            row_date, _row_time, row_home, _score_text, row_away, _note = cols
            if row_date != normalized_date:
                continue
            if self._normalize_team_name(league_code, row_home) != normalized_home:
                continue
            if self._normalize_team_name(league_code, row_away) != normalized_away:
                continue
            return self._match_id_for_teams_row(league_code, row_date, row_home, row_away)
        return ''

    def _runtime_only_match_id(self, external_match_id: str, league_code: str, match_date: str, home_team: str, away_team: str) -> str:
        explicit = str(external_match_id or '').strip()
        if explicit:
            return explicit
        return self._match_id_for_teams_row(league_code, match_date, home_team, away_team)

    @staticmethod
    def _canonical_competition_code(*values: Any) -> str:
        for value in values:
            raw = str(value or '').strip()
            if not raw:
                continue
            for code, aliases in COMPETITION_ALIASES.items():
                if raw == code or raw in aliases:
                    return code
        return ''

    @staticmethod
    def _competition_display_name(league_code: str) -> str:
        normalized = str(league_code or '').strip()
        canonical = ResultManager._canonical_competition_code(normalized) or normalized
        return LEAGUE_SHORT_NAMES.get(canonical, canonical)

    def _snapshot_dirs_for_competition(self, league_code: str) -> List[str]:
        canonical = self._canonical_competition_code(league_code) or str(league_code or '').strip()
        aliases = SNAPSHOT_DIR_ALIASES.get(canonical, (canonical,) if canonical else tuple())
        dirs: List[str] = []
        for alias in aliases:
            candidate = os.path.join(self.base_dir, '.okooo-scraper', 'snapshots', alias)
            if candidate not in dirs:
                dirs.append(candidate)
        return dirs

    @staticmethod
    def _snapshot_context_from_path(snapshot_path: str) -> tuple[str, str]:
        normalized = os.path.normpath(str(snapshot_path or '').strip())
        if not normalized:
            return '', ''
        parts = normalized.split(os.sep)
        if 'snapshots' not in parts:
            return '', ''
        index = parts.index('snapshots')
        dir_name = parts[index + 1] if index + 1 < len(parts) else ''
        return dir_name, ResultManager._canonical_competition_code(dir_name)

    def _find_snapshot_file_by_match_id(self, league_code: str, match_id: str) -> tuple[str, Optional[Dict[str, Any]]]:
        wanted_id = str(match_id or '').strip()
        if not wanted_id:
            return '', None
        for snap_dir in self._snapshot_dirs_for_competition(league_code):
            if not os.path.isdir(snap_dir):
                continue
            for name in sorted(os.listdir(snap_dir)):
                if not name.endswith('.json'):
                    continue
                file_path = os.path.join(snap_dir, name)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                except Exception:
                    continue
                if str(payload.get('match_id') or '').strip() == wanted_id:
                    return file_path, payload
        return '', None

    @staticmethod
    def _snapshot_totals_line_source(snapshot: Optional[Dict[str, Any]]) -> str:
        if not isinstance(snapshot, dict):
            return ''
        totals = snapshot.get('大小球') if isinstance(snapshot.get('大小球'), dict) else {}
        for source_name, source_key in (('snapshot_final', 'final'), ('snapshot_initial', 'initial')):
            block = totals.get(source_key) if isinstance(totals, dict) else None
            if not isinstance(block, dict):
                continue
            value = block.get('line')
            if value in (None, ''):
                value = block.get('盘口')
            try:
                parsed = float(value)
            except Exception:
                continue
            if 0.5 <= parsed <= 6.5:
                return source_name
        return ''

    @staticmethod
    def _result_text(actual_winner: Optional[str], actual_score: str) -> str:
        winner_text = PREDICTED_WINNER_TEXT.get(str(actual_winner or '').strip(), '')
        score_text = str(actual_score or '').strip()
        if winner_text and score_text:
            return f'{winner_text} {score_text}'
        return score_text or winner_text

    def _memory_entry_prefixes_for_prediction(self, prediction_data: Dict[str, Any], archive: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
        from domain.persistence import PredictionPersistenceService

        prefixes: List[str] = []
        entry_keys, _ = PredictionPersistenceService._memory_identity_aliases(prediction_data)
        for entry_key in sorted(entry_keys):
            prefix = f"- [{entry_key}]"
            if prefix not in prefixes:
                prefixes.append(prefix)

        archive_payload = archive if isinstance(archive, dict) else self._load_prediction_archive()
        external_match_id = str(prediction_data.get('external_match_id') or prediction_data.get('match_id') or '').strip()
        if not external_match_id:
            return prefixes

        for archived in archive_payload.values():
            if not isinstance(archived, dict):
                continue
            candidates = {
                str(archived.get('match_id') or '').strip(),
                str(archived.get('external_match_id') or '').strip(),
            }
            full_prediction = archived.get('full_prediction')
            if isinstance(full_prediction, dict):
                candidates.add(str(full_prediction.get('match_id') or '').strip())
            if external_match_id not in candidates:
                continue
            archived_entry_keys, _ = PredictionPersistenceService._memory_identity_aliases(archived)
            for entry_key in sorted(archived_entry_keys):
                prefix = f"- [{entry_key}]"
                if prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    def _update_memory_result_entry(self, prediction_data: Dict[str, Any]) -> None:
        memory_path = self.paths.memory_file
        if not memory_path.exists():
            return
        actual_score = str(prediction_data.get('actual_score') or '').strip()
        actual_winner = str(prediction_data.get('actual_winner') or '').strip()
        result_text = self._result_text(actual_winner, actual_score)
        if not result_text:
            return
        archive = self._load_prediction_archive()
        prefixes = self._memory_entry_prefixes_for_prediction(prediction_data, archive=archive)
        try:
            content = memory_path.read_text(encoding='utf-8')
        except Exception:
            return

        start_marker = '<!-- prediction-memory:start -->'
        end_marker = '<!-- prediction-memory:end -->'
        marker = re.search(
            rf'{re.escape(start_marker)}\n(?P<body>.*?){re.escape(end_marker)}',
            content,
            re.DOTALL,
        )
        if not marker:
            return

        updated = False

        from domain.persistence import PredictionPersistenceService

        entry_blocks = PredictionPersistenceService._extract_memory_entry_lines(marker.group('body'))
        new_lines = []
        for entry in entry_blocks:
            normalized = PredictionPersistenceService._unescape_memory_entry_text(entry)
            first_line = normalized.splitlines()[0].strip() if normalized.splitlines() else normalized.strip()
            if not any(first_line.startswith(prefix) for prefix in prefixes):
                new_lines.append(normalized)
                continue

            entry_lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
            cleaned_lines = [
                line
                for line in entry_lines
                if not (
                    line.strip().startswith('赛果:')
                    or line.strip().startswith('■ 赛果:')
                )
            ]
            meta_index = next((idx for idx, line in enumerate(cleaned_lines) if '记忆ID:' in line), -1)
            meta_line = cleaned_lines[meta_index] if meta_index >= 0 else ''
            meta_line = re.sub(r'^\s*赛果:\s*[^|]+\s*\|\s*', '', meta_line.strip())
            meta_line = re.sub(r'\s*\|\s*更新时间:\s*[^|]+', '', meta_line)
            meta_prefix = f'赛果: {result_text}'
            meta_body = f'{meta_prefix} | {meta_line}' if meta_line else meta_prefix
            updated_line = f'  {meta_body} | 更新时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
            if meta_index >= 0:
                cleaned_lines[meta_index] = updated_line
            else:
                cleaned_lines.append(updated_line)
            new_lines.append('\n'.join(cleaned_lines))
            updated = True

        if not updated:
            return

        entry_lines = PredictionPersistenceService._extract_memory_entry_lines('\n'.join(new_lines))
        replacement = PredictionPersistenceService.render_prediction_memory_block(
            entry_lines,
            start_marker,
            end_marker,
        )
        content = re.sub(
            rf'{re.escape(start_marker)}\n.*?{re.escape(end_marker)}',
            replacement,
            content,
            count=1,
            flags=re.DOTALL,
        )
        memory_path.write_text(content, encoding='utf-8')

    def save_runtime_only_result(self, prediction_ref: Dict[str, Any], home_score: int, away_score: int) -> Dict[str, Any]:
        external_match_id = str(
            prediction_ref.get('external_match_id')
            or prediction_ref.get('match_id')
            or ''
        ).strip()
        archive = self._load_prediction_archive()
        matched_key = ''
        matched_entry: Dict[str, Any] = {}
        for archive_key, archived in archive.items():
            if not isinstance(archived, dict):
                continue
            candidates = {
                str(archive_key).strip(),
                str(archived.get('match_id') or '').strip(),
                str(archived.get('external_match_id') or '').strip(),
            }
            full_prediction = archived.get('full_prediction')
            if isinstance(full_prediction, dict):
                candidates.add(str(full_prediction.get('match_id') or '').strip())
            if external_match_id and external_match_id in candidates:
                matched_key = str(archive_key).strip()
                matched_entry = dict(archived)
                break
        if not matched_entry:
            raise ValueError(f'找不到 runtime-only 预测归档: {external_match_id or prediction_ref.get("home_team")}')

        actual_score = f'{int(home_score)}-{int(away_score)}'
        actual_winner = self._parse_score_to_winner(actual_score)
        matched_entry['actual_score'] = actual_score
        matched_entry['actual_winner'] = actual_winner
        matched_entry['actual_result'] = self._result_text(actual_winner, actual_score).split(' ', 1)[0]
        matched_entry['saved_at'] = datetime.now().isoformat()
        if isinstance(matched_entry.get('full_prediction'), dict):
            matched_entry['full_prediction']['actual_score'] = actual_score
            matched_entry['full_prediction']['actual_winner'] = actual_winner
        archive[matched_key] = matched_entry
        self._save_prediction_archive(archive)
        self._update_memory_result_entry(matched_entry)
        sync_prediction_memory_samples(self.base_dir, limit=100)
        sync_rag_index(self.base_dir, limit=200)
        return {
            'match_id': matched_key,
            'league': matched_entry.get('league'),
            'league_name': matched_entry.get('league_name'),
            'match_date': matched_entry.get('match_date'),
            'match_time': matched_entry.get('match_time'),
            'home_team': matched_entry.get('home_team'),
            'away_team': matched_entry.get('away_team'),
            'actual_winner': actual_winner,
            'actual_score': actual_score,
            'saved_at': matched_entry.get('saved_at'),
            'storage_mode': 'runtime_only',
        }

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

    @staticmethod
    def _winner_key_from_text(value: str) -> Optional[str]:
        mapping = {
            '主胜': 'home',
            '平局': 'draw',
            '客胜': 'away',
            'home': 'home',
            'draw': 'draw',
            'away': 'away',
        }
        return mapping.get(str(value or '').strip())

    @staticmethod
    def _normalize_predicted_ou_value(predicted_ou: Any) -> Optional[Dict[str, object]]:
        if not isinstance(predicted_ou, dict):
            return None
        raw_side = str(predicted_ou.get('side') or '').strip()
        if raw_side in ('大球', '大', 'over', 'OVER'):
            side = '大'
        elif raw_side in ('小球', '小', 'under', 'UNDER'):
            side = '小'
        else:
            return None
        try:
            line = float(predicted_ou.get('line'))
        except Exception:
            return None
        return {'side': side, 'line': line}

    @staticmethod
    def _parse_completed_score(score_text: str) -> Optional[tuple[int, int]]:
        if not re.match(r'^\d+\s*-\s*\d+$', str(score_text or '').strip()):
            return None
        try:
            home_score, away_score = [int(x.strip()) for x in str(score_text).split('-')]
        except Exception:
            return None
        return home_score, away_score

    @staticmethod
    def _line_bucket(line: float) -> str:
        if abs(line - 2.5) < 1e-9:
            return '2.5'
        if abs(line - 2.75) < 1e-9:
            return '2.75'
        if abs(line - 3.0) < 1e-9:
            return '3.0'
        if line >= 3.25:
            return '3.25+'
        return f'{line:g}'

    @staticmethod
    def _within_days(match_date: str, days: int) -> bool:
        if days <= 0:
            return True
        try:
            match_ts = datetime.fromisoformat(str(match_date or '')).timestamp()
        except Exception:
            return True
        cutoff = datetime.now().timestamp() - (days * 86400)
        return match_ts >= cutoff

    @staticmethod
    def _normalize_accuracy_line_source(*candidates: Any) -> str:
        saw_unknown = False
        for candidate in candidates:
            if isinstance(candidate, dict):
                line_source = str(candidate.get('line_source') or '').strip()
                reason = str(candidate.get('reason') or '').strip()
                if line_source and line_source != 'unknown':
                    return line_source
                if reason == 'missing_real_line':
                    return 'missing_real_line'
                if line_source == 'unknown':
                    saw_unknown = True
                continue
            value = str(candidate or '').strip()
            if value and value != 'unknown':
                return value
            if value == 'unknown':
                saw_unknown = True
        return 'unknown' if saw_unknown else ''

    def _merge_accuracy_sample(self, sample: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        for key in (
            'match_id',
            'league',
            'league_name',
            'match_date',
            'match_time',
            'home_team',
            'away_team',
            'actual_score',
            'actual_winner',
            'predicted_winner',
            'storage_mode',
        ):
            if incoming.get(key) and not sample.get(key):
                sample[key] = incoming[key]

        if incoming.get('predicted_scores'):
            existing_scores = list(sample.get('predicted_scores') or [])
            for score in incoming.get('predicted_scores') or []:
                if score and score not in existing_scores:
                    existing_scores.append(score)
            sample['predicted_scores'] = existing_scores

        incoming_ou = self._normalize_predicted_ou_value(incoming.get('predicted_ou'))
        current_ou = self._normalize_predicted_ou_value(sample.get('predicted_ou'))
        if incoming_ou and (not current_ou or str(sample.get('line_source') or 'unknown') == 'unknown'):
            sample['predicted_ou'] = incoming_ou

        incoming_line_source = self._normalize_accuracy_line_source(incoming.get('line_source'))
        if incoming_line_source and incoming_line_source != 'unknown':
            sample['line_source'] = incoming_line_source
        elif not sample.get('line_source'):
            sample['line_source'] = 'unknown'

        sources = set(sample.get('source_presence') or [])
        sources.update(incoming.get('source_presence') or [])
        sample['source_presence'] = sorted(source for source in sources if source)

    def _archive_lookup(self, archive: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        lookup: Dict[str, Dict[str, Any]] = {}
        for archive_key, entry in archive.items():
            if not isinstance(entry, dict):
                continue
            candidates = {
                str(archive_key or '').strip(),
                str(entry.get('match_id') or '').strip(),
                str(entry.get('external_match_id') or '').strip(),
                str(entry.get('internal_match_id') or '').strip(),
                str(entry.get('teams_match_id') or '').strip(),
            }
            full_prediction = entry.get('full_prediction')
            if isinstance(full_prediction, dict):
                candidates.add(str(full_prediction.get('match_id') or '').strip())
                candidates.add(str(full_prediction.get('internal_match_id') or '').strip())
                candidates.add(str(full_prediction.get('teams_match_id') or '').strip())
            for candidate in candidates:
                if candidate:
                    lookup[candidate] = entry
        return lookup

    def _sample_from_archive_entry(self, archive_key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
        over_under = entry.get('over_under') if isinstance(entry.get('over_under'), dict) else {}
        fp_over_under = full_prediction.get('over_under') if isinstance(full_prediction.get('over_under'), dict) else {}
        predicted_ou = self._normalize_predicted_ou_value(entry.get('predicted_ou'))
        if not predicted_ou:
            derived_line = over_under.get('line')
            if derived_line in (None, ''):
                derived_line = fp_over_under.get('line')
            try:
                if derived_line is not None and derived_line != '':
                    predicted_ou = {
                        'side': '大' if float(over_under.get('over', fp_over_under.get('over', 0)) or 0) > float(over_under.get('under', fp_over_under.get('under', 0)) or 0) else '小',
                        'line': float(derived_line),
                    }
            except Exception:
                predicted_ou = None

        predicted_winner = (
            self._winner_key_from_text(str(entry.get('predicted_winner') or ''))
            or self._winner_key_from_text(str(full_prediction.get('predicted_winner') or ''))
            or self._winner_key_from_text(str(entry.get('prediction') or full_prediction.get('prediction') or ''))
        )

        return {
            'match_id': str(
                entry.get('match_id')
                or entry.get('external_match_id')
                or entry.get('teams_match_id')
                or entry.get('internal_match_id')
                or archive_key
            ).strip(),
            'league': str(entry.get('league') or full_prediction.get('league_code') or '').strip(),
            'league_name': str(entry.get('league_name') or full_prediction.get('league_name') or '').strip(),
            'match_date': str(entry.get('match_date') or full_prediction.get('match_date') or '').strip(),
            'match_time': str(entry.get('match_time') or full_prediction.get('match_time') or '').strip(),
            'home_team': str(entry.get('home_team') or full_prediction.get('home_team') or '').strip(),
            'away_team': str(entry.get('away_team') or full_prediction.get('away_team') or '').strip(),
            'actual_score': str(entry.get('actual_score') or full_prediction.get('actual_score') or '').strip(),
            'actual_winner': str(entry.get('actual_winner') or full_prediction.get('actual_winner') or '').strip(),
            'predicted_winner': predicted_winner,
            'predicted_scores': list(entry.get('predicted_scores') or []),
            'predicted_ou': predicted_ou,
            'storage_mode': str(
                entry.get('storage_mode')
                or full_prediction.get('storage_mode')
                or ('league_sot' if entry.get('teams_match_id') else 'runtime_only')
            ).strip(),
            'line_source': self._normalize_accuracy_line_source(
                entry.get('line_source'),
                full_prediction.get('line_source'),
                over_under,
                fp_over_under,
                over_under.get('line_source'),
                fp_over_under.get('line_source'),
            ) or 'unknown',
            'source_presence': ['archive'],
        }

    def _iter_memory_prediction_entries(self):
        memory_path = self.paths.memory_file
        if not memory_path.exists():
            return
        try:
            text = memory_path.read_text(encoding='utf-8')
        except Exception:
            return
        block_match = re.search(
            r'<!-- prediction-memory:start -->([\s\S]*?)<!-- prediction-memory:end -->',
            text,
        )
        if not block_match:
            return
        for raw_line in block_match.group(1).splitlines():
            line = raw_line.strip()
            if not line.startswith('- '):
                continue
            header_match = re.match(r'- \[([^\]]+)\]\s+(\d{4}-\d{2}-\d{2})', line)
            if not header_match:
                continue
            raw_identity = str(header_match.group(1) or '').strip()
            parts = [part.strip() for part in raw_identity.split('|') if part.strip()]
            if len(parts) < 3:
                continue
            league_code = parts[0]
            if len(parts) >= 4:
                home_team = parts[-2]
                away_team = parts[-1]
            else:
                home_team = parts[1]
                away_team = parts[2]
            predicted_match = re.search(r'->\s*(主胜|平局|客胜)', line)
            actual_match = re.search(r'\|\s*赛果:\s*(主胜|平局|客胜)\s+(\d+\s*-\s*\d+)', line)
            if not predicted_match or not actual_match:
                continue
            score_match = re.search(r'\|\s*比分:\s*([^|]+?)\s*\|', line)
            ou_match = re.search(r'\|\s*大小球:\s*(大球|小球)\s+([0-9]+(?:\.[0-9]+)?)', line)
            memory_id_match = re.search(r'\|\s*记忆ID:\s*([^|]+?)\s*(?=\||$)', line)

            predicted_scores: List[str] = []
            if score_match:
                predicted_scores = [
                    re.sub(r'\s+', '', part.strip())
                    for part in str(score_match.group(1) or '').split('>')
                    if re.match(r'^\d+\s*-\s*\d+$', part.strip())
                ]

            predicted_ou = None
            if ou_match:
                try:
                    predicted_ou = {
                        'side': '大' if str(ou_match.group(1) or '').strip() == '大球' else '小',
                        'line': float(ou_match.group(2)),
                    }
                except Exception:
                    predicted_ou = None

            memory_id = str(memory_id_match.group(1) or '').strip() if memory_id_match else ''
            yield {
                'match_id': memory_id or raw_identity,
                'league': league_code,
                'league_name': LEAGUE_SHORT_NAMES.get(league_code, league_code),
                'match_date': str(header_match.group(2) or '').strip(),
                'home_team': home_team,
                'away_team': away_team,
                'actual_score': re.sub(r'\s+', '', str(actual_match.group(2) or '').strip()),
                'actual_winner': self._winner_key_from_text(str(actual_match.group(1) or '').strip()),
                'predicted_winner': self._winner_key_from_text(str(predicted_match.group(1) or '').strip()),
                'predicted_scores': predicted_scores,
                'predicted_ou': predicted_ou,
                'storage_mode': 'runtime_only',
                'line_source': 'unknown',
                'source_presence': ['memory'],
            }

    def _build_unified_prediction_samples(self, days: int = 30) -> Dict[str, Dict[str, Any]]:
        samples: Dict[str, Dict[str, Any]] = {}
        archive = self._load_prediction_archive()
        archive_lookup = self._archive_lookup(archive)

        for archive_key, entry in archive.items():
            if not isinstance(entry, dict):
                continue
            sample = self._sample_from_archive_entry(archive_key, entry)
            if not sample.get('match_date') or not self._within_days(str(sample.get('match_date') or ''), days):
                continue
            match_id = str(sample.get('match_id') or '').strip()
            if not match_id:
                continue
            current = samples.setdefault(match_id, {'source_presence': []})
            self._merge_accuracy_sample(current, sample)

        for row in self._iter_teams_rows():
            if not self._within_days(str(row.get('match_date') or ''), days):
                continue
            row_sample = {
                'match_id': str(row.get('match_id') or '').strip(),
                'league': row.get('league'),
                'league_name': row.get('league_name'),
                'match_date': row.get('match_date'),
                'match_time': row.get('match_time'),
                'home_team': row.get('home_team'),
                'away_team': row.get('away_team'),
                'actual_score': re.sub(r'\s+', '', str(row.get('score_text') or '').strip()),
                'actual_winner': self._parse_score_to_winner(str(row.get('score_text') or '')),
                'predicted_winner': self._parse_predicted_winner(str(row.get('note') or '')),
                'predicted_scores': self._parse_predicted_scores(str(row.get('note') or '')),
                'predicted_ou': self._parse_predicted_ou(str(row.get('note') or '')),
                'storage_mode': 'league_sot',
                'line_source': 'unknown',
                'source_presence': ['teams_sot'],
            }
            archived = archive_lookup.get(str(row.get('match_id') or '').strip())
            if archived:
                self._merge_accuracy_sample(
                    row_sample,
                    self._sample_from_archive_entry(str(row.get('match_id') or '').strip(), archived),
                )
            match_id = str(row_sample.get('match_id') or '').strip()
            if not match_id:
                continue
            current = samples.setdefault(match_id, {'source_presence': []})
            self._merge_accuracy_sample(current, row_sample)

        for memory_sample in self._iter_memory_prediction_entries() or []:
            if not self._within_days(str(memory_sample.get('match_date') or ''), days):
                continue
            match_id = str(memory_sample.get('match_id') or '').strip()
            if not match_id:
                continue
            current = samples.setdefault(match_id, {'source_presence': []})
            self._merge_accuracy_sample(current, memory_sample)

        return samples

    @staticmethod
    def _summarize_ou_records(records: List[Dict[str, Any]], push_count: int = 0) -> Dict[str, Any]:
        hit_count = sum(1 for record in records if record.get('hit'))
        sample_count = len(records)
        return {
            'sample_count': sample_count,
            'hit_count': hit_count,
            'hit_rate': round(hit_count / sample_count * 100, 2) if sample_count > 0 else 0.0,
            'push_count': push_count,
        }

    def calculate_over_under_report(self, days: int = 30, league: Optional[str] = None) -> Dict[str, Any]:
        samples = self._build_unified_prediction_samples(days=days)
        records: List[Dict[str, Any]] = []
        push_count = 0

        for sample in samples.values():
            if league and str(sample.get('league') or '') != league:
                continue
            predicted_ou = self._normalize_predicted_ou_value(sample.get('predicted_ou'))
            score_pair = self._parse_completed_score(str(sample.get('actual_score') or ''))
            if not predicted_ou or not score_pair:
                continue
            home_score, away_score = score_pair
            total_goals = home_score + away_score
            line = float(predicted_ou['line'])
            if abs(total_goals - line) < 1e-9:
                push_count += 1
                continue
            actual_side = '大' if total_goals > line else '小'
            source_presence = '+'.join(sorted(sample.get('source_presence') or [])) or 'unknown'
            records.append({
                'match_id': str(sample.get('match_id') or '').strip(),
                'league': str(sample.get('league') or '').strip(),
                'home_team': str(sample.get('home_team') or '').strip(),
                'away_team': str(sample.get('away_team') or '').strip(),
                'actual_score': str(sample.get('actual_score') or '').strip(),
                'predicted_side': str(predicted_ou['side']),
                'actual_side': actual_side,
                'line': line,
                'line_bucket': self._line_bucket(line),
                'line_source': str(sample.get('line_source') or 'unknown').strip() or 'unknown',
                'storage_mode': str(sample.get('storage_mode') or 'unknown').strip() or 'unknown',
                'source_presence': source_presence,
                'hit': actual_side == str(predicted_ou['side']),
            })

        def group_by(field: str) -> Dict[str, Dict[str, Any]]:
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for record in records:
                key = str(record.get(field) or 'unknown')
                grouped.setdefault(key, []).append(record)
            return {
                key: {
                    **self._summarize_ou_records(items),
                    'matches': [
                        {
                            'match_id': item['match_id'],
                            'teams': f"{item['home_team']} vs {item['away_team']}",
                            'score': item['actual_score'],
                            'predicted_side': item['predicted_side'],
                            'actual_side': item['actual_side'],
                            'line': item['line'],
                        }
                        for item in items[:10]
                    ],
                }
                for key, items in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            }

        return {
            'scope': 'unified_prediction_sources',
            'league': league,
            'days': days,
            'overall': self._summarize_ou_records(records, push_count=push_count),
            'by_storage_mode': group_by('storage_mode'),
            'by_line_source': group_by('line_source'),
            'by_line_bucket': group_by('line_bucket'),
            'by_league': group_by('league'),
            'by_source_presence': group_by('source_presence'),
            'samples_preview': records[:20],
        }

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
        _path, payload = self._find_snapshot_file_by_match_id(league_code, match_id)
        return payload

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
        return self.prediction_archive_store.load()

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
        competition_backfilled = 0
        snapshot_backfilled = 0
        line_source_backfilled = 0

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
                migrated_count += 1

            full_prediction = entry.get('full_prediction') if isinstance(entry.get('full_prediction'), dict) else {}
            canonical_code = self._canonical_competition_code(
                entry.get('league'),
                entry.get('league_code'),
                entry.get('league_name'),
                full_prediction.get('league_code'),
                full_prediction.get('league_name'),
            )
            entry_changed = False
            if canonical_code:
                display_name = self._competition_display_name(canonical_code)
                if entry.get('league') != canonical_code:
                    entry['league'] = canonical_code
                    entry_changed = True
                if entry.get('league_code') != canonical_code:
                    entry['league_code'] = canonical_code
                    entry_changed = True
                if entry.get('league_name') != display_name:
                    entry['league_name'] = display_name
                    entry_changed = True

                if full_prediction:
                    if full_prediction.get('league_code') != canonical_code:
                        full_prediction['league_code'] = canonical_code
                        entry_changed = True
                    if full_prediction.get('league_name') != display_name:
                        full_prediction['league_name'] = display_name
                        entry_changed = True

                over_under = entry.get('over_under') if isinstance(entry.get('over_under'), dict) else {}
                fp_over_under = full_prediction.get('over_under') if isinstance(full_prediction.get('over_under'), dict) else {}
                realtime = full_prediction.get('realtime') if isinstance(full_prediction.get('realtime'), dict) else {}
                okooo = realtime.get('okooo') if isinstance(realtime.get('okooo'), dict) else {}
                external_match_id = str(
                    entry.get('external_match_id')
                    or entry.get('match_id')
                    or full_prediction.get('match_id')
                    or ''
                ).strip()
                snapshot_path = str(
                    entry.get('snapshot_path')
                    or entry.get('source_snapshot')
                    or full_prediction.get('snapshot_path')
                    or realtime.get('source_snapshot')
                    or okooo.get('source_snapshot')
                    or okooo.get('snapshot_path')
                    or ''
                ).strip()
                snapshot_dir_alias, snapshot_code_from_path = self._snapshot_context_from_path(snapshot_path)
                snapshot_payload: Optional[Dict[str, Any]] = None
                if snapshot_path and os.path.exists(snapshot_path):
                    try:
                        with open(snapshot_path, 'r', encoding='utf-8') as f:
                            snapshot_payload = json.load(f)
                    except Exception:
                        snapshot_payload = None
                if not snapshot_path and external_match_id:
                    snapshot_path, snapshot_payload = self._find_snapshot_file_by_match_id(canonical_code, external_match_id)
                    if snapshot_path:
                        snapshot_backfilled += 1
                        entry_changed = True
                        snapshot_dir_alias, snapshot_code_from_path = self._snapshot_context_from_path(snapshot_path)

                snapshot_dir = snapshot_code_from_path or canonical_code
                alias_list = list(SNAPSHOT_DIR_ALIASES.get(canonical_code, (canonical_code,)))
                if entry.get('snapshot_dir') != snapshot_dir:
                    entry['snapshot_dir'] = snapshot_dir
                    entry_changed = True
                if entry.get('snapshot_dir_aliases') != alias_list:
                    entry['snapshot_dir_aliases'] = alias_list
                    entry_changed = True
                if snapshot_dir_alias and entry.get('snapshot_dir_alias') != snapshot_dir_alias:
                    entry['snapshot_dir_alias'] = snapshot_dir_alias
                    entry_changed = True
                if full_prediction and full_prediction.get('snapshot_dir') != snapshot_dir:
                    full_prediction['snapshot_dir'] = snapshot_dir
                    entry_changed = True
                if okooo.get('snapshot_dir') != snapshot_dir:
                    okooo['snapshot_dir'] = snapshot_dir
                    entry_changed = True
                if okooo.get('snapshot_dir_aliases') != alias_list:
                    okooo['snapshot_dir_aliases'] = alias_list
                    entry_changed = True
                if snapshot_dir_alias and okooo.get('snapshot_dir_alias') != snapshot_dir_alias:
                    okooo['snapshot_dir_alias'] = snapshot_dir_alias
                    entry_changed = True
                if snapshot_path:
                    if entry.get('snapshot_path') != snapshot_path:
                        entry['snapshot_path'] = snapshot_path
                        entry_changed = True
                    if entry.get('source_snapshot') != snapshot_path:
                        entry['source_snapshot'] = snapshot_path
                        entry_changed = True
                    if full_prediction:
                        if full_prediction.get('snapshot_path') != snapshot_path:
                            full_prediction['snapshot_path'] = snapshot_path
                            entry_changed = True
                    if realtime.get('source_snapshot') != snapshot_path:
                        realtime['source_snapshot'] = snapshot_path
                        entry_changed = True
                    if okooo.get('source_snapshot') != snapshot_path:
                        okooo['source_snapshot'] = snapshot_path
                        entry_changed = True
                    if okooo.get('snapshot_path') != snapshot_path:
                        okooo['snapshot_path'] = snapshot_path
                        entry_changed = True

                line_source = str(
                    entry.get('line_source')
                    or over_under.get('line_source')
                    or fp_over_under.get('line_source')
                    or ''
                ).strip()
                if not line_source:
                    line_source = self._snapshot_totals_line_source(snapshot_payload)
                if line_source:
                    if entry.get('line_source') != line_source:
                        entry['line_source'] = line_source
                        line_source_backfilled += 1
                        entry_changed = True
                    if over_under and over_under.get('line_source') != line_source:
                        over_under['line_source'] = line_source
                        entry_changed = True
                    if fp_over_under and fp_over_under.get('line_source') != line_source:
                        fp_over_under['line_source'] = line_source
                        entry_changed = True

                if okooo:
                    realtime['okooo'] = okooo
                if realtime:
                    full_prediction['realtime'] = realtime
                if full_prediction:
                    entry['full_prediction'] = full_prediction
                if entry_changed:
                    competition_backfilled += 1

            archive[match_id] = entry

        if migrated_count or competition_backfilled:
            self._save_prediction_archive(archive)

        return {
            "archive_file": self.prediction_archive_file,
            "total_records": total_records,
            "migrated_records": migrated_count,
            "competition_backfilled_records": competition_backfilled,
            "snapshot_backfilled_records": snapshot_backfilled,
            "line_source_backfilled_records": line_source_backfilled,
            "dimension_schema_version": self.prediction_runtime_profile.get("dimension_schema_version"),
            "agent_roles": self.prediction_runtime_profile.get("agent_roles", []),
            "updated_at": datetime.now().isoformat(),
        }

    def _save_prediction_archive(self, archive: Dict[str, Dict[str, Any]]) -> None:
        self.prediction_archive_store.save(archive)

    def _archive_prediction(self, prediction_data: Dict[str, Any]) -> None:
        archive_key = str(
            prediction_data.get('teams_match_id')
            or prediction_data.get('external_match_id')
            or prediction_data.get('internal_match_id')
            or prediction_data.get('match_id')
            or ''
        ).strip()
        if not archive_key:
            return
        archive = self._load_prediction_archive()
        external_match_id = str(prediction_data.get('external_match_id') or '').strip()
        if external_match_id and not prediction_data.get('teams_match_id'):
            for key, archived in list(archive.items()):
                if not isinstance(archived, dict):
                    continue
                candidates = {
                    str(key).strip(),
                    str(archived.get('match_id') or '').strip(),
                    str(archived.get('external_match_id') or '').strip(),
                }
                full_prediction = archived.get('full_prediction')
                if isinstance(full_prediction, dict):
                    candidates.add(str(full_prediction.get('match_id') or '').strip())
                if external_match_id in candidates and key != archive_key:
                    archive.pop(key, None)
        archive[archive_key] = prediction_data
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
            from runtime.result_sync import mark_result_sync_completed
            mark_result_sync_completed(self.base_dir, result_data.get("match_id", ""), result_data.get("actual_score", ""), result_data.get("actual_winner", ""))
            self._update_memory_result_entry(result_data)
            sync_prediction_memory_samples(self.base_dir, limit=100)
            sync_rag_index(self.base_dir, limit=200)
            logger.info("保存比赛结果: %s vs %s -> %s", home_candidate, away_candidate, result_data['actual_score'])
            return result_data
        else:
            result_data = self._update_teams_row_score(identifier, home_score, away_score)
            result_data['upset_sync'] = self._auto_sync_upset_case(result_data)
            from runtime.result_sync import mark_result_sync_completed
            mark_result_sync_completed(self.base_dir, result_data.get("match_id", ""), result_data.get("actual_score", ""), result_data.get("actual_winner", ""))
            self._update_memory_result_entry(result_data)
            sync_prediction_memory_samples(self.base_dir, limit=100)
            sync_rag_index(self.base_dir, limit=200)
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

    def calculate_accuracy(self, league: Optional[str] = None, days: int = 30, ou_report: Optional[Dict[str, Any]] = None) -> Dict:
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
        ou_stats = ou_report if isinstance(ou_report, dict) else self.calculate_over_under_report(days=days, league=league)
        ou_overall = ou_stats.get('overall') if isinstance(ou_stats.get('overall'), dict) else {}
        total_ou = int(ou_overall.get('sample_count', total_ou) or 0)
        correct_ou = int(ou_overall.get('hit_count', correct_ou) or 0)
        ou_acc = float(ou_overall.get('hit_rate', ou_acc) or 0.0)
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
            'ou_push_excluded': int(ou_overall.get('push_count', 0) or 0),
            'ou_scope': 'unified_prediction_sources',
            'model_accuracy': {},
            'calculated_at': datetime.now().isoformat(),
            'days': days
        }

    def update_accuracy_stats(self):
        """更新准确率统计。"""
        overall_ou_report = self.calculate_over_under_report(days=30)
        stats = {
            'overall': self.calculate_accuracy(ou_report=overall_ou_report),
            'by_league': {},
            'over_under_report': overall_ou_report,
            'last_updated': datetime.now().isoformat()
        }

        for league_code in LEAGUE_NAMES.keys():
            league_ou_report = self.calculate_over_under_report(days=30, league=league_code)
            stats['by_league'][league_code] = self.calculate_accuracy(league=league_code, ou_report=league_ou_report)

        self.accuracy_store.save(stats)

        logger.info("准确率统计已更新")
        return stats

    def save_prediction_from_enhanced(self, enhanced_pred: Dict, league_code: str):
        """兼容旧调用方，返回基于 teams 文件可推导的预测信息。"""
        match_date = enhanced_pred.get('match_date', datetime.now().strftime('%Y-%m-%d'))
        home_team = enhanced_pred.get('home_team', '')
        away_team = enhanced_pred.get('away_team', '')
        teams_match_id = self._find_existing_teams_match_id(league_code, match_date, home_team, away_team)
        external_match_id = str(enhanced_pred.get('match_id') or '').strip()
        realtime = enhanced_pred.get('realtime')
        if not external_match_id and isinstance(realtime, dict):
            okooo = realtime.get('okooo')
            if isinstance(okooo, dict):
                external_match_id = str(okooo.get('match_id') or '').strip()
        match_id = teams_match_id or self._runtime_only_match_id(external_match_id, league_code, match_date, home_team, away_team)
        storage_mode = 'league_sot' if teams_match_id else 'runtime_only'
        enhanced_pred['teams_match_id'] = teams_match_id
        enhanced_pred['internal_match_id'] = match_id
        enhanced_pred['storage_mode'] = storage_mode

        prediction_result = enhanced_pred.get('prediction', '')
        predicted_winner = {'主胜': 'home', '客胜': 'away', '平局': 'draw'}.get(prediction_result)
        top_scores = enhanced_pred.get('top_scores', [])
        predicted_scores = [score for score, _prob in top_scores[:2]] if top_scores else []
        predicted_score = '/'.join(predicted_scores) if predicted_scores else ''
        predicted_ou = None
        over_under = enhanced_pred.get('over_under', {})
        over_under_available = (
            isinstance(over_under, dict)
            and bool(over_under.get('available', True))
            and isinstance(over_under.get('line'), (int, float))
        )
        if over_under_available:
            predicted_ou = {
                'side': '大' if over_under.get('over', 0) > over_under.get('under', 0) else '小',
                'line': float(over_under.get('line')),
            }

        prediction_data = {
            'match_id': match_id,
            'external_match_id': external_match_id,
            'teams_match_id': teams_match_id,
            'league': league_code,
            'league_name': LEAGUE_NAMES.get(league_code, league_code),
            'home_team': home_team,
            'away_team': away_team,
            'match_date': match_date,
            'match_time': str(enhanced_pred.get('match_time') or ''),
            'storage_mode': storage_mode,
            'predicted_winner': predicted_winner,
            'predicted_score': predicted_score,
            'predicted_scores': predicted_scores,
            'predicted_probability': str(enhanced_pred.get('confidence', '')),
            'over_under': (
                '大球'
                if over_under_available and enhanced_pred.get('over_under', {}).get('over', 0) > enhanced_pred.get('over_under', {}).get('under', 0)
                else '小球'
                if over_under_available
                else '待补真实盘口'
            ),
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
                'external_match_id': external_match_id,
                'internal_match_id': match_id,
                'teams_match_id': teams_match_id,
                'league': league_code,
                'league_name': LEAGUE_NAMES.get(league_code, league_code),
                'match_date': match_date,
                'home_team': home_team,
                'away_team': away_team,
                'prediction': prediction_result,
                'predicted_winner': predicted_winner,
                'predicted_scores': predicted_scores,
                'top_scores': enhanced_pred.get('top_scores', []),
                'over_under': enhanced_pred.get('over_under', {}),
                'market_snapshot': enhanced_pred.get('market_snapshot', {}),
                'predicted_ou': predicted_ou,
                'confidence': enhanced_pred.get('confidence'),
                'upset_potential': enhanced_pred.get('upset_potential', {}),
                'match_intelligence': enhanced_pred.get('match_intelligence', {}),
                'realtime': enhanced_pred.get('realtime', {}),
                'storage_mode': storage_mode,
                'runtime_profile': enhanced_pred.get('runtime_profile', {}),
                'note': f"预测:{prediction_result} 信心:{float(enhanced_pred.get('confidence') or 0):.2f}".strip(),
                'full_prediction': enhanced_pred,
                'archived_at': datetime.now().isoformat(),
            }
        )
        from runtime.result_sync import register_prediction_result_sync
        register_prediction_result_sync(self.base_dir, prediction_data)
        if teams_match_id:
            logger.info("预测已关联联赛 SoT 行: %s", teams_match_id)
        else:
            logger.info("预测按 runtime-only 归档，不写入联赛 SoT: %s", match_id)
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
