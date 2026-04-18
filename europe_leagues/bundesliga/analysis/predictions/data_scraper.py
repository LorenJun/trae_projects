#!/usr/bin/env python3
"""
足球比赛数据抓取模块
从中国体育彩票官网、澳客官网获取实时赔率数据
支持浏览器自动化抓取和API直接抓取两种方式
"""

import re
import json
import time
import random
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime


@dataclass
class OddsData:
    """赔率数据结构"""
    home_team: str
    away_team: str
    league: str
    match_date: str
    initial_odds: Dict[str, float]
    final_odds: Dict[str, float]
    kelly_odds: Dict[str, List[float]]
    asian_handicap: Optional[Dict] = None
    over_under: Optional[Dict] = None
    bookmakers: Optional[List[str]] = None
    update_time: Optional[str] = None


class SportteryScraper:
    """中国体育彩票官网数据抓取器"""

    BASE_URL = "https://www.sporttery.cn/jc/zqszsc/"

    def __init__(self):
        self.session = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

    async def fetch_match_odds(self, match_url: str) -> Optional[Dict]:
        """从竞彩网获取比赛赔率数据"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse
            import asyncio

            browser = Browser()
            agent = Agent(
                task=f"""访问 {match_url} 获取比赛赔率数据。
                请提取以下信息：
                1. 主队和客队名称
                2. 胜平负赔率（初始和最新）
                3. 让球赔率
                4. 大小球赔率
                5. 赔率变化时间线
                以JSON格式返回数据。""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            return self._parse_sporttery_data(result)
        except Exception as e:
            print(f"抓取竞彩网数据失败: {e}")
            return None

    def _parse_sporttery_data(self, raw_data: str) -> Dict:
        """解析竞彩网返回的数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            odds_info = {
                'home_team': data.get('home_team', ''),
                'away_team': data.get('away_team', ''),
                'initial_odds': {
                    'home': float(data.get('initial_home_odds', 0)),
                    'draw': float(data.get('initial_draw_odds', 0)),
                    'away': float(data.get('initial_away_odds', 0)),
                },
                'final_odds': {
                    'home': float(data.get('final_home_odds', 0)),
                    'draw': float(data.get('final_draw_odds', 0)),
                    'away': float(data.get('final_away_odds', 0)),
                },
                'handicap': data.get('handicap', {}),
                'over_under': data.get('over_under', {}),
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            return odds_info
        except Exception as e:
            print(f"解析数据失败: {e}")
            return {}

    async def fetch_league_matches(self, league: str, date: str) -> List[Dict]:
        """获取联赛当日所有比赛"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse
            import asyncio

            league_codes = {
                'bundesliga': '德甲',
                'premier_league': '英超',
                'la_liga': '西甲',
                'serie_a': '意甲',
                'ligue_1': '法甲'
            }

            browser = Browser()
            agent = Agent(
                task=f"""访问 https://www.sporttery.cn/jc/zqszsc/ 获取{league_codes.get(league, league)}联赛 {date} 的比赛列表。
                对于每场比赛，请提取：
                1. 比赛时间
                2. 主队和客队名称
                3. 比赛编号
                4. 赔率信息入口链接
                以JSON数组格式返回所有比赛信息。""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            return self._parse_match_list(result)
        except Exception as e:
            print(f"获取联赛比赛列表失败: {e}")
            return []

    def _parse_match_list(self, raw_data: str) -> List[Dict]:
        """解析比赛列表数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            print(f"解析比赛列表失败: {e}")
            return []


class OkoooScraper:
    """澳客官网数据抓取器"""

    BASE_URL = "https://www.okooo.com/livecenter/"

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

    async def fetch_kelly_odds(self, match_url: str) -> Optional[Dict[str, List[float]]]:
        """获取澳客凯利指数数据"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse
            import asyncio

            browser = Browser()
            agent = Agent(
                task=f"""访问 {match_url} 获取凯利指数数据。
                请提取多家博彩公司（至少5家：威廉希尔、立博、澳客、Bet365、Interwetten等）的凯利指数。
                返回格式：
                {{
                    "home_kelly": {{"威廉希尔": x.xx, "立博": x.xx, ...}},
                    "draw_kelly": {{"威廉希尔": x.xx, "立博": x.xx, ...}},
                    "away_kelly": {{"威廉希尔": x.xx, "立博": x.xx, ...}}
                }}""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            return self._parse_kelly_data(result)
        except Exception as e:
            print(f"抓取澳客凯利指数失败: {e}")
            return None

    def _parse_kelly_data(self, raw_data: str) -> Dict[str, List[float]]:
        """解析凯利指数数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            kelly_data = {
                'home': [],
                'draw': [],
                'away': []
            }

            if 'home_kelly' in data:
                kelly_data['home'] = list(data['home_kelly'].values())
            if 'draw_kelly' in data:
                kelly_data['draw'] = list(data['draw_kelly'].values())
            if 'away_kelly' in data:
                kelly_data['away'] = list(data['away_kelly'].values())

            return kelly_data
        except Exception as e:
            print(f"解析凯利指数失败: {e}")
            return {'home': [], 'draw': [], 'away': []}

    async def fetch_odds_comparison(self, match_url: str) -> Optional[Dict]:
        """获取多家公司赔率对比数据"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse
            import asyncio

            browser = Browser()
            agent = Agent(
                task=f"""访问 {match_url} 获取赔率对比数据。
                请提取至少10家博彩公司的胜平负赔率数据，包括：
                - 威廉希尔
                - 立博
                - 澳客
                - Bet365
                - Interwetten
                - 10BET
                - 188BET
                - 明陞
                - 12bet
                - 必发

                返回格式：
                {{
                    "companies": ["公司1", "公司2", ...],
                    "home_odds": [x.xx, x.xx, ...],
                    "draw_odds": [x.xx, x.xx, ...],
                    "away_odds": [x.xx, x.xx, ...]
                }}""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            return self._parse_odds_comparison(result)
        except Exception as e:
            print(f"抓取赔率对比数据失败: {e}")
            return None

    def _parse_odds_comparison(self, raw_data: str) -> Dict:
        """解析赔率对比数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            return {
                'companies': data.get('companies', []),
                'home_odds': [float(x) for x in data.get('home_odds', [])],
                'draw_odds': [float(x) for x in data.get('draw_odds', [])],
                'away_odds': [float(x) for x in data.get('away_odds', [])],
            }
        except Exception as e:
            print(f"解析赔率对比数据失败: {e}")
            return {}


class DataCollector:
    """综合数据收集器"""

    def __init__(self):
        self.sporttery = SportteryScraper()
        self.okooo = OkoooScraper()

    async def collect_match_data(self, match_url: str, league: str) -> Optional[OddsData]:
        """收集单场比赛的完整数据"""
        try:
            sporttery_data = await self.sporttery.fetch_match_odds(match_url)

            if not sporttery_data:
                print("未能获取竞彩网数据")
                return None

            kelly_data = await self.okooo.fetch_kelly_odds(match_url)

            odds_comparison = await self.okooo.fetch_odds_comparison(match_url)

            home_team = sporttery_data.get('home_team', '')
            away_team = sporttery_data.get('away_team', '')
            match_date = sporttery_data.get('match_date', datetime.now().strftime('%Y-%m-%d'))

            return OddsData(
                home_team=home_team,
                away_team=away_team,
                league=league,
                match_date=match_date,
                initial_odds=sporttery_data.get('initial_odds', {}),
                final_odds=sporttery_data.get('final_odds', {}),
                kelly_odds=kelly_data or {'home': [], 'draw': [], 'away': []},
                asian_handicap=sporttery_data.get('handicap'),
                over_under=sporttery_data.get('over_under'),
                bookmakers=odds_comparison.get('companies') if odds_comparison else None,
                update_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            )
        except Exception as e:
            print(f"收集比赛数据失败: {e}")
            return None

    async def collect_league_data(self, league: str, date: str) -> List[OddsData]:
        """收集联赛当日所有比赛数据"""
        matches = await self.sporttery.fetch_league_matches(league, date)
        results = []

        for match in matches:
            match_url = match.get('url')
            if match_url:
                data = await self.collect_match_data(match_url, league)
                if data:
                    results.append(data)
                time.sleep(random.uniform(1, 3))

        return results


class SimpleScraper:
    """简单的网页数据抓取器（无需AI Agent）"""

    @staticmethod
    async def fetch_with_browser(url: str, selectors: Dict[str, str]) -> Dict:
        """
        使用浏览器抓取网页数据

        Args:
            url: 目标URL
            selectors: CSS选择器字典，例如 {'home_odds': '.home-odds', 'draw_odds': '.draw-odds'}

        Returns:
            提取的数据字典
        """
        try:
            from browser_use import Agent, Browser, ChatBrowserUse
            import asyncio

            selector_str = json.dumps(selectors, ensure_ascii=False)

            browser = Browser()
            agent = Agent(
                task=f"""访问 {url} 并提取数据。
                使用以下CSS选择器提取数据：
                {selector_str}

                请提取对应的数据并以JSON格式返回。""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()

            if isinstance(result, str):
                return json.loads(result)
            return result
        except Exception as e:
            print(f"浏览器抓取失败: {e}")
            return {}

    @staticmethod
    def calculate_离散率(odds_list: List[float]) -> float:
        """计算离散率（标准差/平均值×100%）"""
        if len(odds_list) < 2:
            return 0.0
        import statistics
        mean_val = statistics.mean(odds_list)
        stdev_val = statistics.stdev(odds_list)
        return (stdev_val / mean_val) * 100 if mean_val != 0 else 0


async def main():
    """测试数据抓取功能"""
    print("=" * 60)
    print("足球比赛数据抓取工具测试")
    print("=" * 60)

    collector = DataCollector()

    print("\n使用方法：")
    print("1. collect_match_data: 收集单场比赛数据")
    print("2. collect_league_data: 收集联赛当日所有比赛")
    print("\n示例：")
    print("""
# 收集单场比赛数据
data = await collector.collect_match_data(
    match_url="https://www.sporttery.cn/jc/zqszsc/match_id",
    league="bundesliga"
)

if data:
    print(f"主队: {data.home_team}")
    print(f"客队: {data.away_team}")
    print(f"初始赔率: {data.initial_odds}")
    print(f"最新赔率: {data.final_odds}")
    print(f"凯利指数: {data.kelly_odds}")

# 收集联赛数据
matches = await collector.collect_league_data("bundesliga", "2026-04-18")
for match in matches:
    print(f"{match.home_team} vs {match.away_team}")
    """)
    print("\n" + "=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())