"""模块说明：读写 elo_ratings.json —— ELO/Glicko 球队历史评分持久化。

结构：
{
  "<league_code>": {
    "<team>": {"elo": 1623.4, "glicko": 1598.1, "games": 12, "updated": "2026-06-09"}
  }
}
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from runtime.paths import get_default_paths
from ._jsonio import atomic_write_json, safe_read_json


class RatingStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)
        self.path = self.paths.runtime_file('elo_ratings.json')

    def load(self) -> Dict[str, Any]:
        payload = safe_read_json(self.path, {})
        return payload if isinstance(payload, dict) else {}

    def save(self, ratings: Dict[str, Any]) -> None:
        atomic_write_json(self.path, ratings)

    def mtime(self) -> float:
        """返回评分文件的最后修改时间；文件不存在时返回 0.0。"""
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return 0.0


# ELO 标准参数（与 ml_prediction_models.EloRatingSystem 一致）
_K_FACTOR = 32
_HOME_ADV = 100
# Glicko 用更小的 K，体现"更精准、更新更稳"
_GLICKO_K = 24


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


class RatingService:
    """ELO/Glicko 历史评分服务：预测时读取、赛果后更新并落盘。

    设计要点（保证 SoT 联赛零回归）：
    - 评分库为空时 get_ratings 返回 None，预测侧回退到 strength 派生（与历史行为一致）；
    - 只有真实赛果累积后，评分才逐步偏离纯实力派生值，体现历史信息。
    """

    def __init__(self, base_dir: Optional[str] = None):
        self.store = RatingStore(base_dir)
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_mtime: float = -1.0

    def _data(self) -> Dict[str, Any]:
        # 基于文件 mtime 失效缓存：多实例（预测侧/回填侧）共享同一文件时，
        # 任一实例落盘后其它实例下次读取自动重载，避免读到陈旧评分。
        current_mtime = self.store.mtime()
        if self._cache is None or current_mtime != self._cache_mtime:
            self._cache = self.store.load()
            self._cache_mtime = current_mtime
        return self._cache

    def get_ratings(self, league_code: str, team: str) -> Optional[Dict[str, float]]:
        """返回某队的历史 {elo, glicko, games}；无记录返回 None。"""
        entry = self._data().get(league_code, {}).get(team)
        if not isinstance(entry, dict):
            return None
        return {
            'elo': float(entry.get('elo', 1500.0)),
            'glicko': float(entry.get('glicko', 1500.0)),
            'games': int(entry.get('games', 0)),
        }

    def update_from_result(
        self,
        league_code: str,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        home_seed: float,
        away_seed: float,
        match_date: Optional[str] = None,
        match_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """用真实赛果更新主客队 ELO/Glicko，缺失记录时用 strength 派生值播种。

        幂等保证：同一 match_id 重复回填只生效一次（避免重试/重跑导致评分重复累加）。
        """
        data = self._data()
        league_map = data.setdefault(league_code, {})

        # match_id 去重：记录已处理赛事，重复则直接跳过（非幂等问题修复）
        mid = str(match_id) if match_id is not None else None
        if mid is not None:
            processed = data.setdefault('_processed_matches', {})
            if not isinstance(processed, dict):
                processed = {}
                data['_processed_matches'] = processed
            if mid in processed:
                return {
                    'league': league_code,
                    'skipped': True,
                    'reason': 'duplicate_match_id',
                    'match_id': mid,
                }

        def _seed(team: str, seed_rating: float) -> Dict[str, Any]:
            entry = league_map.get(team)
            if not isinstance(entry, dict):
                entry = {'elo': float(seed_rating), 'glicko': float(seed_rating), 'games': 0}
                league_map[team] = entry
            return entry

        home = _seed(home_team, home_seed)
        away = _seed(away_team, away_seed)

        # 实际得分（胜=1 平=0.5 负=0）
        if home_goals > away_goals:
            home_actual, away_actual = 1.0, 0.0
        elif home_goals < away_goals:
            home_actual, away_actual = 0.0, 1.0
        else:
            home_actual = away_actual = 0.5

        for key, k in (('elo', _K_FACTOR), ('glicko', _GLICKO_K)):
            hr, ar = float(home[key]), float(away[key])
            he = _expected(hr + _HOME_ADV, ar)
            ae = _expected(ar, hr + _HOME_ADV)
            home[key] = round(hr + k * (home_actual - he), 2)
            away[key] = round(ar + k * (away_actual - ae), 2)

        home['games'] = int(home.get('games', 0)) + 1
        away['games'] = int(away.get('games', 0)) + 1
        if match_date:
            home['updated'] = match_date
            away['updated'] = match_date

        if mid is not None:
            data['_processed_matches'][mid] = match_date or True

        self.store.save(data)
        self._cache = data
        self._cache_mtime = self.store.mtime()
        return {
            'league': league_code,
            'home': {'team': home_team, **{k: home[k] for k in ('elo', 'glicko', 'games')}},
            'away': {'team': away_team, **{k: away[k] for k in ('elo', 'glicko', 'games')}},
        }

