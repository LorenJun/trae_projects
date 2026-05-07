"""模块说明：负责预测结果的缓存、归档、MEMORY 写回与自动赛果同步登记。"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict

from runtime.memory_samples import sync_prediction_memory_samples
from runtime.paths import get_default_paths
from runtime.result_sync import register_prediction_result_sync

logger = logging.getLogger(__name__)

LEAGUE_DISPLAY_NAMES = {
    'premier_league': '英超',
    'la_liga': '西甲',
    'serie_a': '意甲',
    'bundesliga': '德甲',
    'ligue_1': '法甲',
    'europa_league': '欧联',
    '欧联': '欧联',
}


class PredictionPersistenceService:
    def __init__(self, base_dir: str, cache: Any, result_manager: Any):
        self.base_dir = base_dir
        self.paths = get_default_paths(base_dir)
        self.cache = cache
        self.result_manager = result_manager

    def memory_file_path(self) -> str:
        return str(self.paths.memory_file)

    @staticmethod
    def _memory_risk_points(result: Dict[str, Any]) -> list[str]:
        points: list[str] = []

        upset = result.get('upset_potential')
        if isinstance(upset, dict):
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
        keywords = ('风险', '平局', '伤病', '诱盘', '反打', '退盘', '凯利')
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

    def format_memory_prediction_entry(self, result: Dict[str, Any]) -> str:
        league_code = str(result.get('league_code') or result.get('league') or '-')
        league_name = str(result.get('league_name') or LEAGUE_DISPLAY_NAMES.get(league_code) or league_code or '-')
        match_date = str(result.get('match_date') or '-')
        home_team = str(result.get('home_team') or '-')
        away_team = str(result.get('away_team') or '-')
        predicted_winner = str(result.get('predicted_winner') or '').strip()
        prediction = str(result.get('prediction') or {'home': '主胜', 'away': '客胜', 'draw': '平局'}.get(predicted_winner, '-'))
        confidence = float(result.get('confidence') or 0.0)
        key = f'{league_code}|{match_date}|{home_team}|{away_team}'

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
        if over_under:
            over_prob = float(over_under.get('over') or 0.0)
            under_prob = float(over_under.get('under') or 0.0)
            ou_line = over_under.get('line')
            line_label = f'{ou_line:g}' if isinstance(ou_line, (int, float)) else '?'
            ou_direction = '小球' if under_prob >= over_prob else '大球'
            ou_confidence = max(over_prob, under_prob)
        else:
            ou_line = predicted_ou.get('line')
            line_label = f'{ou_line:g}' if isinstance(ou_line, (int, float)) else '?'
            side = str(predicted_ou.get('side') or '').strip()
            ou_direction = '大球' if side == '大' else '小球' if side == '小' else str(result.get('over_under') or '-')
            ou_confidence = confidence if ou_direction in ('大球', '小球') else 0.0
        risk_summary = self.format_memory_risk_summary(result)
        market_changes = self.format_memory_market_changes(result)
        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        return (
            f'- [{key}] {match_date} {league_name} {home_team} vs {away_team} -> '
            f'{prediction} ({confidence:.1%}) | 比分: {score_summary} | '
            f'大小球: {ou_direction} {line_label} ({ou_confidence:.1%}) | '
            f'盘口: {market_changes} | '
            f'风险: {risk_summary} | '
            f'更新时间: {updated_at}'
        )

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
            entry_key = entry.split(']', 1)[0] + ']'
            marker_block = re.compile(rf'{re.escape(start_marker)}\n(?P<body>.*?){re.escape(end_marker)}', re.DOTALL)
            match = marker_block.search(content)

            if match:
                existing_entries = [
                    line.strip()
                    for line in match.group('body').splitlines()
                    if line.strip().startswith('- ')
                ]
                existing_entries = [line for line in existing_entries if not line.startswith(entry_key)]
                new_entries = [entry] + existing_entries[: max_entries - 1]
                replacement = start_marker + '\n' + '\n'.join(new_entries) + '\n' + end_marker
                content = marker_block.sub(replacement, content, count=1)
            else:
                section = (
                    f'{section_title}\n\n'
                    f'{section_note}\n\n'
                    f'{start_marker}\n'
                    f'{entry}\n'
                    f'{end_marker}\n\n'
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
        register_prediction_result_sync(self.base_dir, result)
        return result

    def persist_prediction_batch(self, predictions: list[Dict[str, Any]], league_code: str) -> None:
        for prediction in predictions:
            self.result_manager.save_prediction_from_enhanced(prediction, league_code)
            register_prediction_result_sync(self.base_dir, prediction)
        self.result_manager.update_accuracy_stats()
