#!/usr/bin/env python3
"""
懂球帝数据抓取脚本 - 增强版
用于从懂球帝网站获取2026赛季球队阵容数据
"""

import json
import re
import urllib.request
import urllib.error
from typing import Dict, List, Optional
import html
import time

class DongqiudiScraper:
    """懂球帝数据抓取器"""

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }

    def fetch_team_page(self, team_id: int) -> Optional[str]:
        """获取球队页面HTML"""
        url = f"https://m.dongqiudi.com/team/{team_id}.html"
        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode('utf-8')
        except urllib.error.HTTPError as e:
            print(f"HTTP错误: {e.code} - {url}")
        except Exception as e:
            print(f"请求错误: {e}")
        return None

    def extract_player_info(self, html_content: str) -> List[Dict]:
        """从HTML中提取球员信息"""
        players = []

        # 尝试从HTML中提取JSON数据
        # 查找包含球员信息的JSON结构
        json_patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\.playerData\s*=\s*({.*?});',
            r'window\.teamData\s*=\s*({.*?});',
            r'var\s+playerList\s*=\s*({.*?});',
            r'var\s+teamInfo\s*=\s*({.*?});'
        ]

        for pattern in json_patterns:
            match = re.search(pattern, html_content, re.DOTALL)
            if match:
                try:
                    json_data = match.group(1)
                    # 修复可能的JSON格式问题
                    json_data = json_data.replace('\'', '"')
                    json_data = re.sub(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', json_data)
                    data = json.loads(json_data)
                    players = self.parse_json_data(data)
                    if players:
                        return players
                except Exception as e:
                    print(f"JSON解析错误: {e}")
                    continue

        # 如果没有找到JSON数据，尝试直接从HTML中提取
        if not players:
            players = self.extract_from_html(html_content)

        return players

    def parse_json_data(self, data: dict) -> List[Dict]:
        """解析JSON数据提取球员信息"""
        players = []

        def recursive_search(obj, depth=0):
            if depth > 30:  # 防止无限递归
                return

            if isinstance(obj, dict):
                # 检查是否是球员对象
                if self.is_player_object(obj):
                    player = self.extract_player(obj)
                    if player and player not in players:
                        players.append(player)

                # 递归搜索所有值
                for key, value in obj.items():
                    if isinstance(value, (dict, list)):
                        recursive_search(value, depth + 1)

            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        recursive_search(item, depth + 1)

        recursive_search(data)
        return players

    def is_player_object(self, obj: dict) -> bool:
        """判断是否是一个球员对象"""
        # 球员对象通常包含这些字段的组合
        player_indicators = ['name', 'player_name', 'cn_name', 'position', 'age', 'nationality', 'number', 'jersey_number']
        present_indicators = sum(1 for indicator in player_indicators if indicator in obj)

        # 同时包含多个指标字段
        return present_indicators >= 3

    def extract_player(self, obj: dict) -> Optional[Dict]:
        """从对象中提取球员信息"""
        try:
            player = {
                'name': obj.get('name') or obj.get('player_name') or obj.get('cn_name') or '',
                'position': obj.get('position', ''),
                'age': obj.get('age', 0),
                'nationality': obj.get('nationality', ''),
                'number': obj.get('number', 0) or obj.get('jersey_number', 0),
            }

            if player['name'] and len(player['name']) > 1:
                return player
        except Exception:
            pass
        return None

    def extract_from_html(self, html_content: str) -> List[Dict]:
        """从HTML中直接提取球员信息"""
        players = []

        # 查找球员列表部分
        player_section_pattern = r'<div[^>]*class[^>]*="player-list|player_list"[^>]*>(.*?)</div>'
        match = re.search(player_section_pattern, html_content, re.DOTALL)

        if match:
            player_section = match.group(1)
            # 提取每个球员项
            player_items = re.findall(r'<div[^>]*class[^>]*="player-item|player_item"[^>]*>(.*?)</div>', player_section, re.DOTALL)

            for item in player_items:
                player = self.parse_player_item(item)
                if player and player not in players:
                    players.append(player)

        # 如果没有找到球员列表，尝试其他模式
        if not players:
            # 提取所有可能的球员名字和位置
            name_position_pattern = r'([\u4e00-\u9fa5]{2,4})[\s:：]+(门将|后卫|中场|前锋)'
            matches = re.findall(name_position_pattern, html_content)
            for name, position in matches:
                player = {
                    'name': name,
                    'position': position,
                    'age': 0,
                    'nationality': '',
                }
                if player not in players:
                    players.append(player)

        return players

    def parse_player_item(self, item_html: str) -> Optional[Dict]:
        """解析单个球员项"""
        try:
            # 提取名字
            name_pattern = r'([\u4e00-\u9fa5]{2,4})'
            name_match = re.search(name_pattern, item_html)
            if not name_match:
                return None
            name = name_match.group(1)

            # 提取位置
            position = ''
            position_pattern = r'(门将|后卫|中场|前锋)'
            position_match = re.search(position_pattern, item_html)
            if position_match:
                position = position_match.group(1)

            # 提取年龄
            age = 0
            age_pattern = r'(\d+)岁'
            age_match = re.search(age_pattern, item_html)
            if age_match:
                age = int(age_match.group(1))

            # 提取国籍
            nationality = ''
            # 这里可能需要更复杂的解析，暂时留空

            player = {
                'name': name,
                'position': position,
                'age': age,
                'nationality': nationality,
            }

            return player
        except Exception:
            return None

    def get_team_players(self, team_id: int) -> List[Dict]:
        """获取球队球员列表"""
        html_content = self.fetch_team_page(team_id)
        if not html_content:
            print(f"无法获取球队 {team_id} 的页面")
            return []

        players = self.extract_player_info(html_content)
        print(f"从懂球帝获取到 {len(players)} 名球员")

        for i, player in enumerate(players[:20], 1):
            print(f"{i}. {player.get('name', 'Unknown')} - {player.get('position', 'Unknown')} - {player.get('age', 0)}岁 - {player.get('nationality', 'Unknown')}")

        return players


def main():
    """测试懂球帝数据抓取"""
    scraper = DongqiudiScraper()

    # 阿森纳
    print("=" * 80)
    print("抓取阿森纳(1)球员数据...")
    players = scraper.get_team_players(1)
    print(f"\n总计: {len(players)} 名球员")
    print("=" * 80)


if __name__ == "__main__":
    main()