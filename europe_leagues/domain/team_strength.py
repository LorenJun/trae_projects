"""模块说明：负责球队强度、球员状态与伤停可用性的分析。"""

from __future__ import annotations

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

    def analyze_team_strength(self, league_code: str, team_name: str) -> Dict[str, Any]:
        cache_params = {'league': league_code, 'team': team_name}
        cached = self.cache.get('analyze_team_strength', cache_params)
        if cached:
            return cached
        team_data = self.load_player_data(league_code, team_name)
        if not team_data:
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
        self.cache.set('analyze_team_strength', cache_params, result)
        return result
