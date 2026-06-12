"""模块说明：负责球队强度、球员状态与伤停可用性的分析。"""

from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any, Dict, Optional

from runtime.cache import PredictionCache

logger = logging.getLogger(__name__)


class TeamStrengthService:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache = PredictionCache()
        self._national_strength: Optional[Dict[str, Any]] = None

    def _load_national_strength(self) -> Dict[str, Any]:
        """加载国家队强度兜底表（FIFA 分档近似），只读一次并缓存。"""
        if self._national_strength is not None:
            return self._national_strength
        table: Dict[str, Any] = {}
        path = os.path.join(self.base_dir, 'data', 'national_team_strength.json')
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as handle:
                    raw = json.load(handle)
                table = {k: v for k, v in raw.items() if not k.startswith('_')}
        except Exception as exc:
            logger.warning('加载国家队强度兜底表失败: %s', exc)
        self._national_strength = table
        return table

    def _national_fallback_strength(self, team_name: str) -> Optional[Dict[str, Any]]:
        """无球员数据时，尝试用国家队强度兜底表给出差异化强度。"""
        entry = self._load_national_strength().get(team_name)
        if not isinstance(entry, dict):
            return None
        return {
            'team': team_name,
            'strength': float(entry.get('strength', 50.0)),
            'attack': float(entry.get('attack', 1.0)),
            'defense': float(entry.get('defense', 1.0)),
            'injured_count': 0,
            'suspended_count': 0,
            'key_players_available': True,
            'available_value': 0,
            'total_value': 0,
            'strength_source': 'national_fallback',
            'strength_tier': entry.get('tier'),
        }

    def get_player_data_path(self, league_code: str, team_name: str) -> str:
        return os.path.join(self.base_dir, league_code, 'players', f'{team_name}.json')

    def load_player_data(self, league_code: str, team_name: str) -> Optional[Dict[str, Any]]:
        cache_params = {'league': league_code, 'team': team_name}
        cached = self.cache.get('load_player_data', cache_params)
        if cached:
            return cached
        file_path = self.get_player_data_path(league_code, team_name)
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
                self.cache.set('load_player_data', cache_params, data)
                return data
            except Exception as exc:
                logger.warning('加载球员数据失败 %s: %s', team_name, exc)
        return None

    @staticmethod
    def _team_signal_score(team_data: Dict[str, Any]) -> Optional[float]:
        # 当 market_value 缺失时，从已采集的真实信号派生一个队级实力代理分。
        # 英超：FPL 价格 + xG；其余四大联赛：understat 队级 xG/xA + 进球助攻。
        # 任一信号缺失则跳过该项，全部为 0 返回 None（交给上层兜底）。
        players = team_data.get('players', []) if isinstance(team_data, dict) else []
        if not players:
            return None
        fpl_price = 0.0
        xg = 0.0
        xa = 0.0
        goals = 0.0
        assists = 0.0
        for player in players:
            if not isinstance(player, dict):
                continue
            fpl_price += float(player.get('fpl_price_m') or 0.0)
            shooting = player.get('shooting_stats') if isinstance(player.get('shooting_stats'), dict) else {}
            passing = player.get('passing_stats') if isinstance(player.get('passing_stats'), dict) else {}
            technical = player.get('technical_stats') if isinstance(player.get('technical_stats'), dict) else {}
            stats = player.get('stats') if isinstance(player.get('stats'), dict) else {}
            xg += float(shooting.get('xg') or technical.get('expected_goals') or 0.0)
            xa += float(passing.get('xa') or technical.get('expected_assists') or 0.0)
            goals += float(stats.get('goals') or 0.0)
            assists += float(stats.get('assists') or 0.0)
        # 优先用 understat/技术统计的攻击产出（xG 为主），它比 FPL 全队价格之和
        # 更能反映真实强弱（价格之和受阵容人数/人气干扰）；都缺失时退到 FPL 价格。
        output = xg + 0.5 * xa + 0.5 * goals + 0.25 * assists
        if output > 0:
            return output
        return fpl_price if fpl_price > 0 else None

    def _league_signal_ranks(self, league_code: str) -> Dict[str, float]:
        # 对一个联赛内所有有信号的队做分位归一（0~1），用于把真实信号映射成强度。
        cache_params = {'league': league_code}
        cached = self.cache.get('league_signal_ranks', cache_params)
        if cached is not None:
            return cached
        scores: Dict[str, float] = {}
        pattern = os.path.join(self.base_dir, league_code, 'players', '*.json')
        for file_path in glob.glob(pattern):
            team_name = os.path.splitext(os.path.basename(file_path))[0]
            try:
                with open(file_path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except Exception:
                continue
            score = self._team_signal_score(data)
            if score is not None:
                scores[team_name] = score
        ranks: Dict[str, float] = {}
        if len(scores) >= 2:
            ordered = sorted(scores.items(), key=lambda kv: kv[1])
            denom = len(ordered) - 1
            for idx, (team_name, _) in enumerate(ordered):
                ranks[team_name] = idx / denom
        self.cache.set('league_signal_ranks', cache_params, ranks)
        return ranks

    def _strength_from_signal(self, league_code: str, team_name: str, team_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 用联赛内分位把真实信号映射成 strength/attack/defense。
        ranks = self._league_signal_ranks(league_code)
        pct = ranks.get(team_name)
        if pct is None:
            return None
        strength = 30.0 + pct * 60.0
        attack = 0.6 + pct * 0.8
        defense = 0.6 + pct * 0.8
        return {
            'team': team_name,
            'strength': round(strength, 2),
            'attack': round(attack, 4),
            'defense': round(defense, 4),
            'injured_count': len([p for p in team_data.get('players', []) if p.get('transfer_status') == 'injured']),
            'suspended_count': len([p for p in team_data.get('players', []) if p.get('transfer_status') == 'suspended']),
            'key_players_available': True,
            'available_value': 0,
            'total_value': 0,
            'strength_source': 'rating_signal',
            'strength_percentile': round(pct, 4),
        }

    def analyze_team_strength(self, league_code: str, team_name: str) -> Dict[str, Any]:
        cache_params = {'league': league_code, 'team': team_name}
        cached = self.cache.get('analyze_team_strength', cache_params)
        if cached:
            return cached
        team_data = self.load_player_data(league_code, team_name)
        if not team_data:
            result = self._national_fallback_strength(team_name)
            if result is None:
                result = {
                    'team': team_name,
                    'strength': 50.0,
                    'attack': 1.0,
                    'defense': 1.0,
                    'injured_count': 0,
                    'suspended_count': 0,
                    'key_players_available': True,
                    'available_value': 0,
                    'total_value': 0,
                    'strength_source': 'flat_default',
                }
        else:
            players = team_data.get('players', [])
            injured_players = [player for player in players if player.get('transfer_status') == 'injured']
            suspended_players = [player for player in players if player.get('transfer_status') == 'suspended']
            available_players = [player for player in players if player.get('transfer_status') == 'current']
            total_value = sum(player.get('market_value', 0) for player in players)
            available_value = sum(player.get('market_value', 0) for player in available_players)
            attack_players = [player for player in available_players if player.get('position') in ['前锋', '中场']]
            defense_players = [player for player in available_players if player.get('position') in ['后卫', '门将']]
            attack = 1.0
            if attack_players:
                attack = sum(player.get('market_value', 0) for player in attack_players) / len(attack_players) / 50 + 0.5
                attack = max(0.5, min(1.5, attack))
            defense = 1.0
            if defense_players:
                defense = sum(player.get('market_value', 0) for player in defense_players) / len(defense_players) / 50 + 0.5
                defense = max(0.5, min(1.5, defense))
            base_strength = 50.0
            if total_value > 0:
                value_ratio = available_value / total_value
                base_strength += (value_ratio - 0.5) * 50
            avg_value = total_value / len(players) if players else 0
            value_strength = min(50, avg_value / 2)
            base_strength += value_strength
            strength = max(10, min(95, base_strength))
            key_positions = ['前锋', '中场', '后卫', '门将']
            key_players_available = True
            for position in key_positions:
                position_players = [player for player in available_players if player.get('position') == position]
                if not position_players:
                    key_players_available = False
                    break
            result = {
                'team': team_name,
                'strength': strength,
                'attack': attack,
                'defense': defense,
                'injured_count': len(injured_players),
                'suspended_count': len(suspended_players),
                'key_players_available': key_players_available,
                'available_value': available_value,
                'total_value': total_value,
            }
            # 球员档案存在但无任何市值数据（全项目当前 market_value 恒为 0）。
            # 优先用已采集的真实信号（英超 FPL 价格+xG；其余四大联赛 understat xG/xA+进球助攻）
            # 按联赛内分位派生强度；信号也缺失时退到国家队兜底表，再退到 flat_default
            # （由下游 λ 校准用市场盘口锚定，避免坍缩成 0-0）。
            if total_value <= 0:
                signal = self._strength_from_signal(league_code, team_name, team_data)
                if signal is not None:
                    result = signal
                else:
                    national = self._national_fallback_strength(team_name)
                    if national is not None:
                        result = national
                    else:
                        result['strength_source'] = 'flat_default'
        self.cache.set('analyze_team_strength', cache_params, result)
        return result
