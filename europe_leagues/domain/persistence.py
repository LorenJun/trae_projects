"""模块说明：负责预测结果的缓存、归档、MEMORY 写回与自动赛果同步登记。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from runtime.memory_dedupe import (
    are_entries_duplicate,
    clean_memory_duplicates,
    normalize_memory_entry_key,
    normalize_team_name,
    validate_memory_entry,
)
from runtime.memory_samples import sync_prediction_memory_samples
from runtime.paths import get_default_paths
from runtime.rag_store import sync_rag_index
from runtime.result_sync import register_prediction_result_sync

logger = logging.getLogger(__name__)

LEAGUE_DISPLAY_NAMES = {
    'premier_league': '英超',
    'la_liga': '西甲',
    'serie_a': '意甲',
    'bundesliga': '德甲',
    'ligue_1': '法甲',
    'europa_league': '欧联',
    'champions_league': '欧冠',
    'conference_league': '欧协联',
    '欧联': '欧联',
    '欧冠': '欧冠',
    '欧协联': '欧协联',
}

NON_SOT_COMPETITION_KEYWORDS = ('杯', '欧冠', '欧联', '欧协联', '亚冠', '淘汰赛', '半决赛', '决赛')
MEMORY_ACCURACY_PREFIX = '> 滚动预测准确率：'


@dataclass(frozen=True)
class PredictionPersistencePayload:
    match_id: str
    external_match_id: str
    internal_match_id: str
    teams_match_id: str
    storage_mode: str
    league_code: str
    league_name: str
    match_date: str
    match_time: str
    home_team: str
    away_team: str
    prediction: str
    predicted_winner: str
    confidence: float
    top_scores: list[Any]
    predicted_scores: list[str]
    over_under: Dict[str, Any]
    predicted_ou: Optional[Dict[str, Any]]
    runtime_profile: Any
    full_prediction: Dict[str, Any]


class PredictionPersistenceService:
    def __init__(self, base_dir: str, cache: Any, result_manager: Any):
        self.base_dir = base_dir
        self.paths = get_default_paths(base_dir)
        self.cache = cache
        self.result_manager = result_manager

    def memory_file_path(self) -> str:
        return str(self.paths.memory_file)

    @staticmethod
    def _unescape_memory_entry_text(text: str) -> str:
        return str(text or '').replace('\\[', '[').replace('\\]', ']').replace('\\_', '_')

    @classmethod
    def _extract_memory_entry_lines(cls, block_body: str) -> list[str]:
        entries: list[str] = []
        current: list[str] = []
        for raw_line in str(block_body or '').splitlines():
            line = raw_line.rstrip()
            normalized = cls._unescape_memory_entry_text(line).strip()
            if normalized.startswith('#### '):
                if current:
                    entries.append('\n'.join(current).strip())
                    current = []
                continue
            if normalized.startswith('- ['):
                if current:
                    entries.append('\n'.join(current).strip())
                current = [normalized]
                continue
            if current:
                if not normalized:
                    continue
                current.append(normalized)
        if current:
            entries.append('\n'.join(current).strip())
        return entries

    @classmethod
    def _normalize_memory_entry_layout(cls, entry: str) -> str:
        normalized = cls._unescape_memory_entry_text(entry).strip()
        if not normalized:
            return ''

        def market_lines_from_text(text: str) -> list[str]:
            body = str(text or '').strip()
            if not body:
                return []
            chunks = [chunk.strip() for chunk in body.split(' | ') if chunk.strip()]
            out: list[str] = []
            for chunk in chunks:
                if chunk.startswith('欧值 '):
                    out.append(f'  ◦ 欧赔: {chunk.replace("欧值 ", "", 1)}')
                elif chunk.startswith('亚盘 '):
                    out.append(f'  ◦ 亚盘: {chunk.replace("亚盘 ", "", 1)}')
                elif chunk.startswith('大小 '):
                    out.append(f'  ◦ 大小: {chunk.replace("大小 ", "", 1)}')
                elif chunk.startswith('凯利 '):
                    out.append(f'  ◦ 凯利: {chunk.replace("凯利 ", "", 1)}')
                else:
                    out.append(f'  ◦ 盘口: {chunk}')
            return out or ['  ◦ 盘口: 盘口变化待补齐']

        lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
        if len(lines) > 1:
            rebuilt = [lines[0].strip()]
            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith('盘口:'):
                    rebuilt.extend(market_lines_from_text(stripped.replace('盘口:', '', 1).strip()))
                elif stripped.startswith('◦ '):
                    rebuilt.append(stripped if stripped.startswith('  ') else f'  {stripped}')
                elif stripped.startswith('风险:') or stripped.startswith('▲ 风险:'):
                    risk_text = stripped.replace('▲ 风险:', '', 1).replace('风险:', '', 1).strip()
                    rebuilt.append(f'  ▲ 风险: {risk_text}')
                elif stripped.startswith('RAG记忆:') or stripped.startswith('◆ RAG记忆:'):
                    rag_text = stripped.replace('◆ RAG记忆:', '', 1).replace('RAG记忆:', '', 1).strip()
                    rebuilt.append(f'  ◆ RAG记忆: {rag_text}')
                elif stripped.startswith('赛果:') or stripped.startswith('■ 赛果:'):
                    result_text = stripped.replace('■ 赛果:', '', 1).replace('赛果:', '', 1).strip()
                    result_body, _, remainder = result_text.partition('|')
                    result_line = f'  ■ 赛果: {result_body.strip()}'
                    if result_line not in rebuilt:
                        rebuilt.append(result_line)
                    remainder = remainder.strip()
                    if remainder:
                        meta_line = f'  · {remainder.lstrip("· ").strip()}'
                        if meta_line not in rebuilt:
                            rebuilt.append(meta_line)
                elif '记忆ID:' in stripped or '更新时间:' in stripped:
                    rebuilt.append(f'  · {stripped.lstrip("· ").strip()}')
                else:
                    rebuilt.append(stripped if stripped.startswith('  ') else f'  {stripped}')
            return '\n'.join(rebuilt)

        parts = [part.strip() for part in normalized.split(' | ') if part.strip()]
        if not parts:
            return normalized

        head = parts[0]
        match = re.match(r'^(?P<header>- \[[^\]]+\]\s+\d{4}-\d{2}-\d{2}\s+.+? vs .+?)\s*->\s*(?P<prediction>.+)$', head)
        if not match:
            return normalized

        header = match.group('header').strip()
        prediction = match.group('prediction').strip()
        score = ''
        over_under = ''
        market_parts: list[str] = []
        risk = ''
        rag = ''
        result = ''
        memory_id = ''
        updated_at = ''
        collecting_market = False

        for part in parts[1:]:
            if part.startswith('比分:'):
                collecting_market = False
                score = part
            elif part.startswith('大小球:'):
                collecting_market = False
                over_under = part
            elif part.startswith('盘口:'):
                collecting_market = True
                market_parts = [part.replace('盘口:', '', 1).strip()]
            elif collecting_market and not any(
                part.startswith(prefix) for prefix in ('风险:', 'RAG记忆:', '赛果:', '记忆ID:', '更新时间:')
            ):
                market_parts.append(part)
            elif part.startswith('风险:'):
                collecting_market = False
                risk = part
            elif part.startswith('RAG记忆:'):
                collecting_market = False
                rag = part
            elif part.startswith('赛果:'):
                collecting_market = False
                result = part
            elif part.startswith('记忆ID:'):
                collecting_market = False
                memory_id = part
            elif part.startswith('更新时间:'):
                collecting_market = False
                updated_at = part
            else:
                collecting_market = False

        body_line_parts = [f'预测: {prediction}']
        if score:
            body_line_parts.append(score)
        if over_under:
            body_line_parts.append(over_under)

        rebuilt_lines = [header, f"  {' | '.join(body_line_parts)}"]
        if market_parts:
            rebuilt_lines.extend(market_lines_from_text(' | '.join(market_parts)))
        if risk:
            rebuilt_lines.append(f"  ▲ {risk}")
        if rag:
            rebuilt_lines.append(f"  ◆ {rag}")
        if result:
            rebuilt_lines.append(f"  ■ {result}")

        meta_parts = []
        if memory_id:
            meta_parts.append(memory_id)
        if updated_at:
            meta_parts.append(updated_at)
        if meta_parts:
            rebuilt_lines.append(f"  · {' | '.join(meta_parts)}")
        return '\n'.join(rebuilt_lines)

    @staticmethod
    def _build_memory_accuracy_summary(entry_lines: list[str]) -> str:
        completed = 0
        correct_win = 0
        total_score = 0
        correct_score = 0
        total_ou = 0
        correct_ou = 0

        for line in entry_lines:
            predicted_match = re.search(r'(?:->\s*|预测:\s*)(主胜|平局|客胜)', line)
            actual_match = re.search(r'赛果:\s*(主胜|平局|客胜)\s+(\d+\s*-\s*\d+)', line)
            if not predicted_match or not actual_match:
                continue

            completed += 1
            predicted_winner = str(predicted_match.group(1) or '').strip()
            actual_winner = str(actual_match.group(1) or '').strip()
            actual_score = re.sub(r'\s+', '', str(actual_match.group(2) or '').strip())
            if predicted_winner == actual_winner:
                correct_win += 1

            score_match = re.search(r'比分:\s*([^\n|]+(?:\s*>\s*[^\n|]+)*)', line)
            if score_match:
                predicted_scores = [
                    re.sub(r'\s+', '', part.strip())
                    for part in str(score_match.group(1) or '').split('>')
                    if part.strip()
                ]
                if predicted_scores:
                    total_score += 1
                    if actual_score in predicted_scores:
                        correct_score += 1

            ou_match = re.search(r'大小球:\s*(大球|小球)\s+([0-9]+(?:\.[0-9]+)?)', line)
            if ou_match:
                try:
                    line_value = float(ou_match.group(2))
                    home_goals, away_goals = [int(x.strip()) for x in actual_score.split('-')]
                    total_goals = home_goals + away_goals
                except Exception:
                    line_value = None
                    total_goals = None
                if isinstance(line_value, float) and isinstance(total_goals, int):
                    if abs(total_goals - line_value) >= 1e-9:
                        total_ou += 1
                        actual_side = '大球' if total_goals > line_value else '小球'
                        if actual_side == str(ou_match.group(1) or '').strip():
                            correct_ou += 1

        if completed <= 0:
            return f'{MEMORY_ACCURACY_PREFIX} 暂无已完赛样本'

        win_acc = correct_win / completed * 100
        score_acc = correct_score / total_score * 100 if total_score > 0 else 0.0
        ou_acc = correct_ou / total_ou * 100 if total_ou > 0 else 0.0
        return (
            f'{MEMORY_ACCURACY_PREFIX} 已完赛 {completed} 场 | '
            f'胜平负 {win_acc:.1f}% ({correct_win}/{completed}) | '
            f'比分 {score_acc:.1f}% ({correct_score}/{total_score}) | '
            f'大小球 {ou_acc:.1f}% ({correct_ou}/{total_ou})'
        )

    @staticmethod
    def _memory_entry_is_completed(entry: str) -> bool:
        text = PredictionPersistenceService._unescape_memory_entry_text(entry)
        return '赛果:' in text or '■ 赛果:' in text

    @classmethod
    def render_prediction_memory_block(cls, entry_lines: list[str], start_marker: str, end_marker: str) -> str:
        normalized_entries = [cls._normalize_memory_entry_layout(entry) for entry in entry_lines if str(entry or '').strip()]
        summary_line = cls._build_memory_accuracy_summary(normalized_entries)
        body_lines = [summary_line, '']
        if normalized_entries:
            pending_entries = [entry for entry in normalized_entries if not cls._memory_entry_is_completed(entry)]
            completed_entries = [entry for entry in normalized_entries if cls._memory_entry_is_completed(entry)]

            def append_group(title: str, entries: list[str]) -> None:
                if not entries:
                    return
                body_lines.append(f'#### {title}')
                body_lines.append('')
                for idx, entry in enumerate(entries):
                    body_lines.extend(entry.splitlines())
                    if idx != len(entries) - 1:
                        body_lines.append('')

            append_group('未完赛', pending_entries)
            if pending_entries and completed_entries:
                body_lines.append('')
            append_group('已完赛', completed_entries)
        else:
            body_lines = [summary_line]
        return start_marker + '\n' + '\n'.join(body_lines) + '\n' + end_marker

    @staticmethod
    def _canonical_memory_identity(result: Dict[str, Any]) -> tuple[str, str]:
        league_code = str(result.get('league_code') or result.get('league') or '-')
        match_date = str(result.get('match_date') or '-')
        home_team = str(result.get('home_team') or '-')
        away_team = str(result.get('away_team') or '-')
        external_match_id = str(result.get('match_id') or result.get('external_match_id') or '').strip()
        league_name = str(result.get('league_name') or LEAGUE_DISPLAY_NAMES.get(league_code) or league_code or '-')
        competition_stage_name = str(
            result.get('competition_stage_name')
            or result.get('competition_stage')
            or ''
        ).strip()
        is_non_sot = (
            str(result.get('storage_mode') or '').strip() == 'runtime_only'
            or bool(competition_stage_name)
            or any(keyword in league_name for keyword in NON_SOT_COMPETITION_KEYWORDS)
        )
        if is_non_sot and external_match_id:
            return external_match_id, f'{league_code}|{home_team}|{away_team}'
        return f'{league_code}|{match_date}|{home_team}|{away_team}', f'{league_code}|{match_date}|{home_team}|{away_team}'

    @classmethod
    def _memory_identity_aliases(cls, result: Dict[str, Any]) -> tuple[set[str], set[str]]:
        league_code = str(result.get('league_code') or result.get('league') or '-').strip()
        match_date = str(result.get('match_date') or '-').strip()
        home_team = str(result.get('home_team') or '-').strip()
        away_team = str(result.get('away_team') or '-').strip()
        external_match_id = str(result.get('match_id') or result.get('external_match_id') or '').strip()
        dedupe_id, display_identity = cls._canonical_memory_identity(result)

        entry_keys: set[str] = set()
        memory_ids: set[str] = set()

        if display_identity:
            entry_keys.add(display_identity)
        if dedupe_id:
            memory_ids.add(dedupe_id)

        legacy_date_identity = ''
        if league_code and match_date and home_team and away_team:
            legacy_date_identity = f'{league_code}|{match_date}|{home_team}|{away_team}'
            entry_keys.add(legacy_date_identity)
            memory_ids.add(legacy_date_identity)

        legacy_runtime_identity = ''
        if league_code and home_team and away_team:
            legacy_runtime_identity = f'{league_code}|{home_team}|{away_team}'
            entry_keys.add(legacy_runtime_identity)

        if external_match_id:
            memory_ids.add(external_match_id)

        return entry_keys, memory_ids

    @staticmethod
    def _memory_entry_updated_at(line: str) -> datetime:
        normalized = PredictionPersistenceService._unescape_memory_entry_text(line)
        timestamp_match = re.search(r'更新时间:\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})', normalized)
        if timestamp_match:
            try:
                return datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
            except Exception:
                pass

        date_match = re.match(r'- \[[^\]]+\]\s+([0-9]{4}-[0-9]{2}-[0-9]{2})\s+', normalized)
        if date_match:
            try:
                return datetime.strptime(date_match.group(1), '%Y-%m-%d')
            except Exception:
                pass

        return datetime.min

    @classmethod
    def _memory_entry_sort_key(cls, line: str) -> tuple[datetime, str]:
        return cls._memory_entry_updated_at(line), line

    @staticmethod
    def _memory_entry_matches_aliases(line: str, entry_prefixes: set[str], memory_id_markers: set[str]) -> bool:
        normalized = PredictionPersistenceService._unescape_memory_entry_text(line)
        first_line = normalized.splitlines()[0].strip() if normalized.splitlines() else ''
        return any(first_line.startswith(prefix) for prefix in entry_prefixes) or any(marker in normalized for marker in memory_id_markers)

    @staticmethod
    def _memory_risk_points(result: Dict[str, Any]) -> list[str]:
        points: list[str] = []

        upset = result.get('upset_potential')
        if isinstance(upset, dict):
            motivation_risk = upset.get('motivation_risk')
            if isinstance(motivation_risk, dict):
                for item in motivation_risk.get('factors') or []:
                    text = str(item or '').strip()
                    if text and text not in points:
                        points.append(text)
            for item in upset.get('factors') or []:
                text = str(item or '').strip()
                if text and text not in points:
                    points.append(text)

        match_intelligence = result.get('match_intelligence')
        if isinstance(match_intelligence, dict):
            for item in match_intelligence.get('signals') or []:
                text = str(item or '').strip()
                if text and text not in points:
                    points.append(text)

        realtime = result.get('realtime')
        if isinstance(realtime, dict):
            context_applied = realtime.get('context_applied')
            if isinstance(context_applied, dict):
                match_intel = context_applied.get('match_intelligence')
                if isinstance(match_intel, dict):
                    for item in match_intel.get('signals') or []:
                        text = str(item or '').strip()
                        if text and text not in points:
                            points.append(text)
                live_adj = context_applied.get('live_outcome_adjustment')
                if isinstance(live_adj, dict):
                    for item in live_adj.get('signals') or []:
                        text = str(item or '').strip()
                        if text and text not in points:
                            points.append(text)

        prioritized = []
        keywords = ('战意', '抢分', '保级', '争冠', '风险', '平局', '伤病', '诱盘', '反打', '退盘', '凯利')
        for item in points:
            if any(keyword in item for keyword in keywords) and item not in prioritized:
                prioritized.append(item)
        for item in points:
            if item not in prioritized:
                prioritized.append(item)
        return prioritized[:3]

    def format_memory_risk_summary(self, result: Dict[str, Any]) -> str:
        upset = result.get('upset_potential') if isinstance(result.get('upset_potential'), dict) else {}
        level = str(upset.get('level') or '').strip()
        index = upset.get('index')
        header = ''
        if level:
            header = level
            if isinstance(index, (int, float)):
                header = f'{header}({float(index):.0f})'

        points = self._memory_risk_points(result)
        if header and points:
            return f'{header} {"; ".join(points)}'
        if header:
            return header
        if points:
            return '; '.join(points)
        return '未提取到显著风险点'

    @staticmethod
    def _fmt_num(value: Any, digits: int = 2) -> str:
        try:
            return f'{float(value):.{digits}f}'
        except Exception:
            return '?'

    def format_memory_market_changes(self, result: Dict[str, Any]) -> str:
        source = result.get('market_snapshot')
        if not isinstance(source, dict):
            source = {}
        if not source and isinstance(result.get('full_prediction'), dict):
            source = result['full_prediction'].get('market_snapshot') or {}

        parts: list[str] = []

        euro = source.get('欧赔') if isinstance(source.get('欧赔'), dict) else {}
        euro_initial = euro.get('initial') if isinstance(euro.get('initial'), dict) else {}
        euro_final = euro.get('final') if isinstance(euro.get('final'), dict) else {}
        if euro_initial or euro_final:
            parts.append(
                '欧值 '
                f"{self._fmt_num(euro_initial.get('home'))}/{self._fmt_num(euro_initial.get('draw'))}/{self._fmt_num(euro_initial.get('away'))}"
                '->'
                f"{self._fmt_num(euro_final.get('home'))}/{self._fmt_num(euro_final.get('draw'))}/{self._fmt_num(euro_final.get('away'))}"
            )

        asian = source.get('亚值') if isinstance(source.get('亚值'), dict) else {}
        asian_initial = asian.get('initial') if isinstance(asian.get('initial'), dict) else {}
        asian_final = asian.get('final') if isinstance(asian.get('final'), dict) else {}
        if asian_initial or asian_final:
            parts.append(
                '亚盘 '
                f"{asian_initial.get('handicap_text') or '?'} {self._fmt_num(asian_initial.get('home_water'))}/{self._fmt_num(asian_initial.get('away_water'))}"
                '->'
                f"{asian_final.get('handicap_text') or '?'} {self._fmt_num(asian_final.get('home_water'))}/{self._fmt_num(asian_final.get('away_water'))}"
            )

        totals = source.get('大小球') if isinstance(source.get('大小球'), dict) else {}
        totals_initial = totals.get('initial') if isinstance(totals.get('initial'), dict) else {}
        totals_final = totals.get('final') if isinstance(totals.get('final'), dict) else {}
        if totals_initial or totals_final:
            parts.append(
                '大小 '
                f"{self._fmt_num(totals_initial.get('line'))} {self._fmt_num(totals_initial.get('over'))}/{self._fmt_num(totals_initial.get('under'))}"
                '->'
                f"{self._fmt_num(totals_final.get('line'))} {self._fmt_num(totals_final.get('over'))}/{self._fmt_num(totals_final.get('under'))}"
            )

        kelly = source.get('凯利') if isinstance(source.get('凯利'), dict) else {}
        kelly_initial = kelly.get('initial') if isinstance(kelly.get('initial'), dict) else {}
        kelly_final = kelly.get('final') if isinstance(kelly.get('final'), dict) else {}
        if kelly_initial or kelly_final:
            parts.append(
                '凯利 '
                f"{self._fmt_num(kelly_initial.get('home'))}/{self._fmt_num(kelly_initial.get('draw'))}/{self._fmt_num(kelly_initial.get('away'))}"
                '->'
                f"{self._fmt_num(kelly_final.get('home'))}/{self._fmt_num(kelly_final.get('draw'))}/{self._fmt_num(kelly_final.get('away'))}"
            )

        return ' | '.join(parts) if parts else '盘口变化待补齐'

    def format_memory_market_lines(self, result: Dict[str, Any]) -> list[str]:
        market_changes = self.format_memory_market_changes(result)
        normalized = self._normalize_memory_entry_layout(
            f"- [tmp] 2000-01-01 占位 占位 vs 占位\n  预测: 占位 | 比分: - | 大小球: -\n  盘口: {market_changes}"
        )
        return [
            line
            for line in normalized.splitlines()
            if line.strip().startswith('◦ ')
        ]

    def format_memory_prediction_entry(self, result: Dict[str, Any]) -> str:
        league_code = str(result.get('league_code') or result.get('league') or '-')
        league_name = str(result.get('league_name') or LEAGUE_DISPLAY_NAMES.get(league_code) or league_code or '-')
        match_date = str(result.get('match_date') or '-')
        home_team = str(result.get('home_team') or '-')
        away_team = str(result.get('away_team') or '-')
        competition_stage_name = str(
            result.get('competition_stage_name')
            or result.get('competition_stage')
            or ''
        ).strip()
        predicted_winner = str(result.get('predicted_winner') or '').strip()
        prediction = str(result.get('prediction') or {'home': '主胜', 'away': '客胜', 'draw': '平局'}.get(predicted_winner, '-'))
        confidence = float(result.get('confidence') or 0.0)
        dedupe_id, display_identity = self._canonical_memory_identity(result)
        key = display_identity

        top_scores = []
        for item in result.get('top_scores', [])[:3]:
            if isinstance(item, (list, tuple)) and item:
                top_scores.append(str(item[0]))
        if not top_scores:
            for item in result.get('predicted_scores', [])[:3]:
                text = str(item or '').strip()
                if text:
                    top_scores.append(text)
        score_summary = ' > '.join(top_scores) if top_scores else '-'

        over_under = result.get('over_under') if isinstance(result.get('over_under'), dict) else {}
        predicted_ou = result.get('predicted_ou') if isinstance(result.get('predicted_ou'), dict) else {}
        ou_available = bool(over_under) and bool(over_under.get('available', True))
        if ou_available:
            over_prob = float(over_under.get('over') or 0.0)
            under_prob = float(over_under.get('under') or 0.0)
            ou_line = over_under.get('line')
            line_label = f'{ou_line:g}' if isinstance(ou_line, (int, float)) else '?'
            ou_direction = '小球' if under_prob >= over_prob else '大球'
            ou_confidence = max(over_prob, under_prob)
            ou_summary = f'{ou_direction} {line_label} ({ou_confidence:.1%})'
        elif over_under:
            reason = str(over_under.get('reason') or 'missing_real_market_line').strip()
            reason_label = '待补真实盘口' if reason == 'missing_real_market_line' else f'不可用({reason})'
            ou_summary = reason_label
        else:
            ou_line = predicted_ou.get('line')
            line_label = f'{ou_line:g}' if isinstance(ou_line, (int, float)) else '?'
            side = str(predicted_ou.get('side') or '').strip()
            ou_direction = '大球' if side == '大' else '小球' if side == '小' else str(result.get('over_under') or '-')
            ou_confidence = confidence if ou_direction in ('大球', '小球') else 0.0
            ou_summary = f'{ou_direction} {line_label} ({ou_confidence:.1%})' if ou_direction in ('大球', '小球') else '待补真实盘口'
        risk_summary = self.format_memory_risk_summary(result)
        market_changes = self.format_memory_market_changes(result)
        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        title = f'{league_name}{competition_stage_name}' if competition_stage_name and competition_stage_name not in league_name else league_name
        actual_winner = str(result.get('actual_winner') or '').strip()
        actual_score = str(result.get('actual_score') or '').strip()
        actual_result = {'home': '主胜', 'away': '客胜', 'draw': '平局'}.get(actual_winner, '')
        rag_explanation = str(result.get('retrieved_memory_explanation') or '').replace('|', '/').strip()
        external_match_id = str(
            result.get('match_id')
            or result.get('external_match_id')
            or result.get('internal_match_id')
            or ''
        ).strip()

        entry_lines = [
            f'- [{key}] {match_date} {title} {home_team} vs {away_team}{(f" | MatchID: {external_match_id}") if external_match_id else ""}',
            f'  预测: {prediction} ({confidence:.1%}) | 比分: {score_summary} | 大小球: {ou_summary}',
        ]
        entry_lines.extend(self.format_memory_market_lines(result))
        entry_lines.append(f'  ▲ 风险: {risk_summary}')
        if rag_explanation:
            entry_lines.append(f'  ◆ RAG记忆: {rag_explanation}')

        meta_parts = []
        if actual_result and actual_score:
            entry_lines.append(f'  ■ 赛果: {actual_result} {actual_score}')
        if external_match_id:
            meta_parts.append(f'MatchID: {external_match_id}')
        meta_parts.append(f'记忆ID: {dedupe_id}')
        meta_parts.append(f'更新时间: {updated_at}')
        entry_lines.append(f"  · {' | '.join(meta_parts)}")
        return '\n'.join(entry_lines)

    def update_prediction_memory(self, result: Dict[str, Any]) -> None:
        memory_path = self.memory_file_path()
        if not os.path.exists(memory_path):
            logger.warning('未找到 MEMORY.md，跳过预测结果记忆更新')
            return

        start_marker = '<!-- prediction-memory:start -->'
        end_marker = '<!-- prediction-memory:end -->'
        section_title = '### 预测结果滚动记忆'
        section_note = '> 自动记录最近 100 场预测结果；同一场比赛重复预测时，覆盖旧记录并刷新到顶部。'
        max_entries = 100

        try:
            with open(memory_path, 'r', encoding='utf-8') as f:
                content = f.read()

            entry = self.format_memory_prediction_entry(result)
            entry_key_bodies, memory_ids = self._memory_identity_aliases(result)
            entry_prefixes = {f'- [{body}]' for body in entry_key_bodies if body}
            memory_id_markers = {f'记忆ID: {memory_id}' for memory_id in memory_ids if memory_id}
            
            league_code = str(result.get('league_code') or result.get('league') or '')
            match_date = str(result.get('match_date') or '')
            home_team = str(result.get('home_team') or '')
            away_team = str(result.get('away_team') or '')
            normalized_home = normalize_team_name(league_code, home_team)
            normalized_away = normalize_team_name(league_code, away_team)
            
            marker_block = re.compile(rf'{re.escape(start_marker)}\n(?P<body>.*?){re.escape(end_marker)}', re.DOTALL)
            match = marker_block.search(content)

            if match:
                existing_entries = self._extract_memory_entry_lines(match.group('body'))
                
                alias_entries = []
                retained_entries = []
                
                for existing_entry in existing_entries:
                    if self._memory_entry_matches_aliases(existing_entry, entry_prefixes, memory_id_markers):
                        alias_entries.append(existing_entry)
                        continue
                    
                    first_line = existing_entry.split('\n')[0] if existing_entry else ''
                    entry_match = re.match(r'- \[([^\]]+)\]', first_line)
                    if entry_match:
                        existing_key = entry_match.group(1)
                        existing_norm = normalize_memory_entry_key(existing_key)
                        # 仅当同联赛、同日期且标准化后的主客队一致时，才视为同一场比赛。
                        # 避免把不同轮次但主客相同的比赛误合并。
                        if (
                            existing_norm[0] == league_code
                            and existing_norm[1] == match_date
                            and existing_norm[2] == normalized_home
                            and existing_norm[3] == normalized_away
                        ):
                            alias_entries.append(existing_entry)
                            continue
                    
                    retained_entries.append(existing_entry)
                
                latest_entry = max(alias_entries + [entry], key=self._memory_entry_sort_key)
                new_entries = sorted(
                    [latest_entry] + retained_entries,
                    key=self._memory_entry_sort_key,
                    reverse=True,
                )[:max_entries]
                replacement = self.render_prediction_memory_block(new_entries, start_marker, end_marker)
                content = marker_block.sub(replacement, content, count=1)
            else:
                rendered_block = self.render_prediction_memory_block([entry], start_marker, end_marker)
                section = (
                    f'{section_title}\n\n'
                    f'{section_note}\n\n'
                    f'{rendered_block}\n\n'
                )
                insert_anchor = '### 技术栈总结'
                if insert_anchor in content:
                    content = content.replace(insert_anchor, section + insert_anchor, 1)
                else:
                    content = content.rstrip() + '\n\n---\n\n' + section

            footer_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            footer_matches = list(re.finditer(r'\*预测记录更新时间: .*?\*', content))
            if footer_matches:
                last_match = footer_matches[-1]
                content = content[: last_match.start()] + f'*预测记录更新时间: {footer_ts}*' + content[last_match.end():]

            with open(memory_path, 'w', encoding='utf-8') as f:
                f.write(content)
            sync_prediction_memory_samples(self.base_dir, limit=max_entries)
            sync_rag_index(self.base_dir, limit=max_entries * 2)
        except Exception as exc:
            logger.warning('更新 MEMORY.md 失败: %s', exc)

    def prepare_cached_prediction(self, cached: Dict[str, Any], runtime_profile: Dict[str, Any]) -> Dict[str, Any]:
        if 'runtime_profile' not in cached:
            cached['runtime_profile'] = runtime_profile
        self.update_prediction_memory(cached)
        register_prediction_result_sync(self.base_dir, cached)
        return cached

    def persist_prediction(self, cache_name: str, cache_params: Dict[str, Any], result: Dict[str, Any], league_code: str) -> Dict[str, Any]:
        self.cache.set(cache_name, cache_params, result)
        try:
            self.result_manager.save_prediction_from_enhanced(result, league_code)
        except Exception as exc:
            logger.warning('归档单场预测失败: %s', exc)
        self.update_prediction_memory(result)
        return result

    def persist_prediction_batch(self, predictions: list[Dict[str, Any]], league_code: str) -> None:
        for prediction in predictions:
            self.result_manager.save_prediction_from_enhanced(prediction, league_code)
        self.result_manager.update_accuracy_stats()
