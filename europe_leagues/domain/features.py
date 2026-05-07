"""模块说明：负责特征补强、EWMA 近况学习与 analysis_context 预处理。"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from collectors.aliasing import load_team_alias_map
from collectors.sofascore import build_match_team_context
from runtime.cache import PredictionCache
from storage.teams_md import TeamsMarkdownStore


def ensure_analysis_context(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def load_analysis_context_file(file_path: str) -> Dict[str, Any]:
    if not file_path:
        return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    return ensure_analysis_context(payload)


def aliases_for_team(alias_map: Dict[str, Any], league_code: str, team_name: str) -> List[str]:
    league = alias_map.get(league_code) if isinstance(alias_map, dict) else None
    if not isinstance(league, dict):
        return []
    aliases = league.get(team_name)
    return [str(x) for x in aliases] if isinstance(aliases, list) else []


def derive_form_from_recent(points: Any, matches: Any) -> int:
    try:
        pts = int(points)
        m = max(1, int(matches))
    except Exception:
        return 3
    ppg = pts / m
    if ppg >= 2.2:
        return 5
    if ppg >= 1.6:
        return 4
    if ppg >= 1.0:
        return 3
    if ppg >= 0.5:
        return 2
    return 1


def auto_enrich_team_context_if_enabled(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    analysis_context: Dict[str, Any],
    realtime_context_applied: Dict[str, Any],
) -> None:
    enabled = os.environ.get('ENABLE_TEAM_CONTEXT', '1').strip() in ('1', 'true', 'True')
    if not enabled or not isinstance(analysis_context, dict) or 'team_context' in analysis_context:
        return

    diag: Dict[str, Any] = {'attempted': True, 'ok': False, 'provider': 'sofascore'}
    try:
        alias_map = load_team_alias_map(base_dir)
        home_aliases = aliases_for_team(alias_map, league_code, home_team)
        away_aliases = aliases_for_team(alias_map, league_code, away_team)
        last_n = int(os.environ.get('TEAM_CONTEXT_LAST_N', '5') or '5')
        team_context = build_match_team_context(
            base_dir=base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            home_aliases=home_aliases,
            away_aliases=away_aliases,
            last_n=last_n,
        )
        analysis_context['team_context'] = team_context
        diag['ok'] = bool(team_context.get('ok'))
        if 'home_form' not in analysis_context and isinstance(team_context.get('home'), dict):
            recent = team_context['home'].get('recent') if isinstance(team_context['home'].get('recent'), dict) else {}
            analysis_context['home_form'] = derive_form_from_recent(recent.get('points', 0), recent.get('matches', 5))
        if 'away_form' not in analysis_context and isinstance(team_context.get('away'), dict):
            recent = team_context['away'].get('recent') if isinstance(team_context['away'].get('recent'), dict) else {}
            analysis_context['away_form'] = derive_form_from_recent(recent.get('points', 0), recent.get('matches', 5))
        diag['home_form'] = analysis_context.get('home_form')
        diag['away_form'] = analysis_context.get('away_form')
    except Exception as exc:
        diag['error'] = str(exc)

    realtime_context_applied['team_context'] = diag


class LeagueOverUnderLearning:
    MIN_SAMPLE_SIZE = 6
    DEFAULT_LOOKBACK_DAYS = 14

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache = PredictionCache()
        self.teams_store = TeamsMarkdownStore(self.base_dir)

    @staticmethod
    def _pct(value: int, total: int) -> float:
        return float(value) / float(total) if total else 0.0

    @staticmethod
    def _safe_date(date_str: str) -> Optional[datetime]:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            return None

    def _teams_md_path(self, league_code: str) -> str:
        return self.teams_store.path_for_league(league_code)

    def _parse_finished_matches(
        self,
        league_code: str,
        end_date: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> List[Dict[str, Any]]:
        cache_params = {
            'league_code': league_code,
            'end_date': end_date,
            'lookback_days': lookback_days,
        }
        cached = self.cache.get('league_ou_finished_matches', cache_params, ttl_hours=12)
        if cached:
            return cached

        path = self._teams_md_path(league_code)
        if not os.path.exists(path):
            return []

        end_dt = self._safe_date(end_date)
        if not end_dt:
            return []
        start_dt = end_dt - timedelta(days=lookback_days)

        matches: List[Dict[str, Any]] = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception:
            return []

        for line in lines:
            if not line.startswith('| 20'):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) < 6:
                continue

            date_str, _time, home_team, score, away_team, remark = cells[:6]
            match_dt = self._safe_date(date_str)
            if not match_dt or not (start_dt <= match_dt < end_dt):
                continue
            if score == '-' or '进行中' in remark:
                continue

            match = re.match(r'^(\d+)-(\d+)$', score)
            if not match:
                continue

            home_goals = int(match.group(1))
            away_goals = int(match.group(2))
            total_goals = home_goals + away_goals
            matches.append({
                'date': date_str,
                'home_team': home_team,
                'away_team': away_team,
                'home_goals': home_goals,
                'away_goals': away_goals,
                'total_goals': total_goals,
                'over15': total_goals >= 2,
                'over25': total_goals >= 3,
                'over35': total_goals >= 4,
                'btts': home_goals > 0 and away_goals > 0,
                'clean_sheet': home_goals == 0 or away_goals == 0,
            })

        self.cache.set('league_ou_finished_matches', cache_params, matches)
        return matches

    def get_recent_learning(
        self,
        league_code: str,
        match_date: str,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> Dict[str, Any]:
        cache_params = {
            'league_code': league_code,
            'match_date': match_date,
            'lookback_days': lookback_days,
        }
        cached = self.cache.get('league_ou_learning', cache_params, ttl_hours=12)
        if cached:
            return cached

        matches = self._parse_finished_matches(league_code, match_date, lookback_days=lookback_days)
        sample_size = len(matches)
        if sample_size == 0:
            return {
                'available': False,
                'league_code': league_code,
                'sample_size': 0,
                'lookback_days': lookback_days,
                'reason': 'no_finished_matches',
            }

        avg_goals = sum(m['total_goals'] for m in matches) / sample_size
        over15_count = sum(1 for m in matches if m['over15'])
        over25_count = sum(1 for m in matches if m['over25'])
        over35_count = sum(1 for m in matches if m['over35'])
        btts_count = sum(1 for m in matches if m['btts'])
        clean_sheet_count = sum(1 for m in matches if m['clean_sheet'])
        total_dist = Counter(m['total_goals'] for m in matches)
        match_dt = self._safe_date(match_date)

        result = {
            'available': True,
            'league_code': league_code,
            'sample_size': sample_size,
            'lookback_days': lookback_days,
            'match_window': {
                'start': (match_dt - timedelta(days=lookback_days)).strftime('%Y-%m-%d'),
                'end': (match_dt - timedelta(days=1)).strftime('%Y-%m-%d'),
            },
            'avg_goals': round(avg_goals, 3),
            'over15_rate': round(self._pct(over15_count, sample_size), 4),
            'over25_rate': round(self._pct(over25_count, sample_size), 4),
            'over35_rate': round(self._pct(over35_count, sample_size), 4),
            'btts_rate': round(self._pct(btts_count, sample_size), 4),
            'clean_sheet_rate': round(self._pct(clean_sheet_count, sample_size), 4),
            'goal_bins': {
                '0': total_dist.get(0, 0),
                '1': total_dist.get(1, 0),
                '2': total_dist.get(2, 0),
                '3': total_dist.get(3, 0),
                '4_plus': sum(v for k, v in total_dist.items() if k >= 4),
            },
            'high_samples': [
                {
                    'date': m['date'],
                    'home_team': m['home_team'],
                    'away_team': m['away_team'],
                    'score': f"{m['home_goals']}-{m['away_goals']}",
                    'total_goals': m['total_goals'],
                }
                for m in sorted(matches, key=lambda item: (item['total_goals'], item['date']), reverse=True)[:3]
            ],
            'low_samples': [
                {
                    'date': m['date'],
                    'home_team': m['home_team'],
                    'away_team': m['away_team'],
                    'score': f"{m['home_goals']}-{m['away_goals']}",
                    'total_goals': m['total_goals'],
                }
                for m in sorted(matches, key=lambda item: (item['total_goals'], item['date']))[:3]
            ],
        }
        self.cache.set('league_ou_learning', cache_params, result)
        return result


class TeamEWMALearning:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache = PredictionCache()
        self.teams_store = TeamsMarkdownStore(self.base_dir)

    @staticmethod
    def _safe_date(date_str: str) -> Optional[datetime]:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except Exception:
            return None

    @staticmethod
    def _ewma(values: List[float], alpha: float) -> Optional[float]:
        if not values:
            return None
        current = float(values[0])
        alpha = max(0.01, min(0.99, float(alpha)))
        for value in values[1:]:
            current = alpha * float(value) + (1.0 - alpha) * current
        return current

    @staticmethod
    def _points_for_result(team_goals: int, opp_goals: int) -> int:
        if team_goals > opp_goals:
            return 3
        if team_goals < opp_goals:
            return 0
        return 1

    def _teams_md_path(self, league_code: str) -> str:
        return self.teams_store.path_for_league(league_code)

    def _iter_finished_team_matches(
        self,
        league_code: str,
        team_name: str,
        before_date: str,
        max_matches: int = 12,
    ) -> List[Dict[str, Any]]:
        cache_params = {
            'league': league_code,
            'team': team_name,
            'before_date': before_date,
            'max_matches': max_matches,
        }
        cached = self.cache.get('team_finished_matches', cache_params, ttl_hours=12)
        if cached:
            return cached

        path = self._teams_md_path(league_code)
        if not os.path.exists(path):
            return []
        cutoff = self._safe_date(before_date)
        if not cutoff:
            return []

        rows: List[Dict[str, Any]] = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        except Exception:
            return []

        for line in lines:
            if not line.startswith('| 20'):
                continue
            cols = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cols) < 6:
                continue
            date_str, _time, home, score, away, remark = cols[:6]
            dt = self._safe_date(date_str)
            if not dt or dt >= cutoff or score == '-' or '进行中' in remark:
                continue
            match = re.match(r'^(\d+)-(\d+)$', score)
            if not match:
                continue
            home_goals, away_goals = int(match.group(1)), int(match.group(2))
            if home != team_name and away != team_name:
                continue
            is_home = home == team_name
            team_goals = home_goals if is_home else away_goals
            opp_goals = away_goals if is_home else home_goals
            rows.append({
                'date': date_str,
                'is_home': is_home,
                'opponent': away if is_home else home,
                'team_goals': team_goals,
                'opp_goals': opp_goals,
                'points': self._points_for_result(team_goals, opp_goals),
                'score': score,
            })

        rows.sort(key=lambda row: row['date'], reverse=True)
        rows = rows[:max_matches]
        self.cache.set('team_finished_matches', cache_params, rows)
        return rows

    def get_team_ewma_features(
        self,
        league_code: str,
        team_name: str,
        match_date: str,
        n5: int = 5,
        n10: int = 10,
    ) -> Dict[str, Any]:
        cache_params = {
            'league': league_code,
            'team': team_name,
            'match_date': match_date,
            'n5': n5,
            'n10': n10,
        }
        cached = self.cache.get('team_ewma_features', cache_params, ttl_hours=12)
        if cached:
            return cached

        rows = self._iter_finished_team_matches(league_code, team_name, before_date=match_date, max_matches=max(n10, n5))
        if not rows:
            return {
                'available': False,
                'team': team_name,
                'league': league_code,
                'reason': 'no_history',
            }

        points = [float(row['points']) for row in rows]
        goals_for = [float(row['team_goals']) for row in rows]
        goals_against = [float(row['opp_goals']) for row in rows]
        alpha_5 = 2.0 / (min(n5, len(rows)) + 1.0)
        alpha_10 = 2.0 / (min(n10, len(rows)) + 1.0)

        def win_rate(last_n: int) -> float:
            recent = rows[:min(last_n, len(rows))]
            if not recent:
                return 0.0
            wins = sum(1 for row in recent if row['points'] == 3)
            return wins / len(recent)

        feature = {
            'available': True,
            'team': team_name,
            'league': league_code,
            'sample_size': len(rows),
            'windows': {
                'n5': min(n5, len(rows)),
                'n10': min(n10, len(rows)),
            },
            'ewma': {
                'points_5': round(float(self._ewma(points[:min(n5, len(points))], alpha_5)), 3) if points else None,
                'points_10': round(float(self._ewma(points[:min(n10, len(points))], alpha_10)), 3) if points else None,
                'gf_5': round(float(self._ewma(goals_for[:min(n5, len(goals_for))], alpha_5)), 3) if goals_for else None,
                'ga_5': round(float(self._ewma(goals_against[:min(n5, len(goals_against))], alpha_5)), 3) if goals_against else None,
            },
            'rates': {
                'win_rate_5': round(win_rate(5), 3),
                'win_rate_10': round(win_rate(10), 3),
            },
            'form_scale': derive_form_from_recent(points[0] if points else 0, 1) if False else derive_form_from_recent(
                self._ewma(points[:min(n5, len(points))], alpha_5), 1
            ),
            'recent_matches': [
                {
                    'date': row['date'],
                    'opponent': row['opponent'],
                    'is_home': row['is_home'],
                    'score': row['score'],
                    'team_goals': row['team_goals'],
                    'opp_goals': row['opp_goals'],
                    'points': row['points'],
                }
                for row in rows[:5]
            ],
        }
        ewma_pts_5 = feature['ewma']['points_5']
        feature['form_scale'] = derive_form_from_recent(ewma_pts_5 if ewma_pts_5 is not None else 1, 1)
        self.cache.set('team_ewma_features', cache_params, feature)
        return feature
