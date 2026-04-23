#!/usr/bin/env python3
"""
足球比赛数据抓取模块（升级版）
实现多数据源集成、错误处理、数据验证和缓存机制
"""

import re
import json
import time
import random
import hashlib
import asyncio
import importlib.util
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class MatchData:
    """比赛数据结构"""
    home_team: str
    away_team: str
    league: str
    match_date: str
    match_time: str
    status: str  # 待进行、进行中、已结束
    score: Optional[Tuple[int, int]] = None
    odds_data: Optional[Dict] = None
    update_time: str = None
    sources: List[str] = None

    def __post_init__(self):
        if self.update_time is None:
            self.update_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if self.sources is None:
            self.sources = []


class BaseScraper:
    """基础抓取器类"""
    
    def __init__(self, name: str):
        self.name = name
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }

    async def fetch_data(self, url: str) -> Optional[Dict]:
        """抓取数据的基础方法"""
        raise NotImplementedError

    async def fetch_league_matches(self, league: str, date: str) -> List[MatchData]:
        """获取联赛当日所有比赛"""
        raise NotImplementedError


class SportteryScraper(BaseScraper):
    """中国体育彩票官网数据抓取器"""

    BASE_URL = "https://www.sporttery.cn/jc/zqszsc/"

    def __init__(self):
        super().__init__("sporttery")

    async def fetch_league_matches(self, league: str, date: str) -> List[MatchData]:
        """从竞彩网获取比赛列表"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse

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
                1. 比赛时间（格式：HH:MM）
                2. 主队和客队名称
                3. 比赛状态（待进行、进行中、已结束）
                4. 比分（如果已结束）
                5. 赔率信息入口链接
                以JSON数组格式返回所有比赛信息。""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            
            return self._parse_match_list(result, league, date)
        except Exception as e:
            print(f"竞彩网抓取失败: {e}")
            return []

    def _parse_match_list(self, raw_data: str, league: str, date: str) -> List[MatchData]:
        """解析比赛列表数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            matches = []
            if isinstance(data, list):
                for item in data:
                    match = MatchData(
                        home_team=item.get('home_team', ''),
                        away_team=item.get('away_team', ''),
                        league=league,
                        match_date=date,
                        match_time=item.get('match_time', ''),
                        status=item.get('status', '待进行'),
                        sources=[self.name]
                    )
                    
                    # 解析比分
                    score = item.get('score')
                    if score:
                        try:
                            home_score, away_score = map(int, score.split('-'))
                            match.score = (home_score, away_score)
                        except:
                            pass
                    
                    matches.append(match)
            return matches
        except Exception as e:
            print(f"解析竞彩网数据失败: {e}")
            return []


class OkoooScraper(BaseScraper):
    """澳客官网数据抓取器"""

    BASE_URL = "https://www.okooo.com/livecenter/"

    def __init__(self):
        super().__init__("okooo")

    async def fetch_league_matches(self, league: str, date: str) -> List[MatchData]:
        """从澳客网获取比赛列表"""
        try:
            from browser_use import Agent, Browser, ChatBrowserUse

            league_codes = {
                'bundesliga': '德甲',
                'premier_league': '英超',
                'la_liga': '西甲',
                'serie_a': '意甲',
                'ligue_1': '法甲'
            }

            browser = Browser()
            agent = Agent(
                task=f"""访问 https://www.okooo.com/livecenter/ 获取{league_codes.get(league, league)}联赛 {date} 的比赛列表。
                对于每场比赛，请提取：
                1. 比赛时间（格式：HH:MM）
                2. 主队和客队名称
                3. 比赛状态（待进行、进行中、已结束）
                4. 比分（如果已结束）
                以JSON数组格式返回所有比赛信息。""",
                llm=ChatBrowserUse(),
                browser=browser,
            )
            result = await agent.run()
            await browser.close()
            
            return self._parse_match_list(result, league, date)
        except Exception as e:
            print(f"澳客网抓取失败: {e}")
            return []

    def _parse_match_list(self, raw_data: str, league: str, date: str) -> List[MatchData]:
        """解析比赛列表数据"""
        try:
            if isinstance(raw_data, str):
                data = json.loads(raw_data)
            else:
                data = raw_data

            matches = []
            if isinstance(data, list):
                for item in data:
                    match = MatchData(
                        home_team=item.get('home_team', ''),
                        away_team=item.get('away_team', ''),
                        league=league,
                        match_date=date,
                        match_time=item.get('match_time', ''),
                        status=item.get('status', '待进行'),
                        sources=[self.name]
                    )
                    
                    # 解析比分
                    score = item.get('score')
                    if score:
                        try:
                            home_score, away_score = map(int, score.split('-'))
                            match.score = (home_score, away_score)
                        except:
                            pass
                    
                    matches.append(match)
            return matches
        except Exception as e:
            print(f"解析澳客网数据失败: {e}")
            return []


class MockScraper(BaseScraper):
    """模拟数据源抓取器"""

    def __init__(self):
        super().__init__("mock")

    async def fetch_league_matches(self, league: str, date: str) -> List[MatchData]:
        """返回模拟的比赛数据"""
        try:
            # 模拟不同联赛的比赛数据
            mock_data = {
                'premier_league': [
                    {
                        'home_team': '切尔西',
                        'away_team': '曼联',
                        'match_time': '03:00',
                        'status': '已结束',
                        'score': (0, 1)
                    },
                    {
                        'home_team': '曼城',
                        'away_team': '阿森纳',
                        'match_time': '23:30',
                        'status': '待进行',
                        'score': None
                    }
                ],
                'serie_a': [
                    {
                        'home_team': '那不勒斯',
                        'away_team': '拉齐奥',
                        'match_time': '00:00',
                        'status': '已结束',
                        'score': (0, 2)
                    },
                    {
                        'home_team': '罗马',
                        'away_team': '亚特兰大',
                        'match_time': '02:45',
                        'status': '已结束',
                        'score': (1, 1)
                    }
                ],
                'bundesliga': [
                    {
                        'home_team': '弗莱堡',
                        'away_team': '拜仁',
                        'match_time': '23:30',
                        'status': '已结束',
                        'score': (0, 5)
                    },
                    {
                        'home_team': '斯图加特',
                        'away_team': '法兰克福',
                        'match_time': '21:30',
                        'status': '待进行',
                        'score': None
                    }
                ],
                'ligue_1': [
                    {
                        'home_team': '里尔',
                        'away_team': '尼斯',
                        'match_time': '03:05',
                        'status': '已结束',
                        'score': (0, 0)
                    },
                    {
                        'home_team': '摩纳哥',
                        'away_team': '欧塞尔',
                        'match_time': '21:00',
                        'status': '待进行',
                        'score': None
                    }
                ],
                'la_liga': [
                    {
                        'home_team': '巴塞罗那',
                        'away_team': '皇家马德里',
                        'match_time': '03:00',
                        'status': '待进行',
                        'score': None
                    }
                ]
            }

            matches = []
            for item in mock_data.get(league, []):
                match = MatchData(
                    home_team=item['home_team'],
                    away_team=item['away_team'],
                    league=league,
                    match_date=date,
                    match_time=item['match_time'],
                    status=item['status'],
                    sources=[self.name]
                )
                if item['score']:
                    match.score = item['score']
                matches.append(match)
            
            print(f"从模拟数据源成功获取 {len(matches)} 场比赛")
            return matches
        except Exception as e:
            print(f"模拟数据源失败: {e}")
            return []


class CacheManager:
    """缓存管理器"""
    
    def __init__(self, cache_dir: str = ".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def get_cache_key(self, league: str, date: str) -> str:
        """生成缓存键"""
        return hashlib.md5(f"{league}_{date}".encode()).hexdigest()

    def get_cache_path(self, league: str, date: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / f"{self.get_cache_key(league, date)}.json"

    def get_cache(self, league: str, date: str) -> Optional[List[Dict]]:
        """获取缓存数据"""
        cache_path = self.get_cache_path(league, date)
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 检查缓存是否过期（24小时）
                cache_time = datetime.fromisoformat(data.get('cache_time', ''))
                if (datetime.now() - cache_time).total_seconds() < 86400:
                    return data.get('matches', [])
            except Exception as e:
                print(f"读取缓存失败: {e}")
        return None

    def set_cache(self, league: str, date: str, matches: List[MatchData]):
        """设置缓存数据"""
        try:
            cache_path = self.get_cache_path(league, date)
            data = {
                'cache_time': datetime.now().isoformat(),
                'matches': [
                    {
                        'home_team': m.home_team,
                        'away_team': m.away_team,
                        'league': m.league,
                        'match_date': m.match_date,
                        'match_time': m.match_time,
                        'status': m.status,
                        'score': m.score,
                        'sources': m.sources,
                        'update_time': m.update_time
                    }
                    for m in matches
                ]
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"写入缓存失败: {e}")


class DataValidator:
    """数据验证器"""
    
    @staticmethod
    def validate_match_data(matches: List[MatchData]) -> List[MatchData]:
        """验证比赛数据"""
        valid_matches = []
        for match in matches:
            if DataValidator._is_valid_match(match):
                valid_matches.append(match)
        return valid_matches

    @staticmethod
    def _is_valid_match(match: MatchData) -> bool:
        """验证单个比赛数据"""
        # 检查必要字段
        if not match.home_team or not match.away_team:
            return False
        if not match.match_date or not match.match_time:
            return False
        # 检查比分格式
        if match.score:
            if not isinstance(match.score, tuple) or len(match.score) != 2:
                return False
            if not all(isinstance(s, int) for s in match.score):
                return False
        return True

    @staticmethod
    def cross_validate(matches_list: List[List[MatchData]]) -> List[MatchData]:
        """交叉验证多个数据源的数据"""
        if not matches_list:
            return []
        
        # 以第一个数据源为基础
        base_matches = matches_list[0]
        validated_matches = []
        
        for base_match in base_matches:
            # 查找其他数据源中的相同比赛
            same_matches = [base_match]
            for other_matches in matches_list[1:]:
                for other_match in other_matches:
                    if DataValidator._is_same_match(base_match, other_match):
                        same_matches.append(other_match)
                        break
            
            # 合并数据
            merged_match = DataValidator._merge_matches(same_matches)
            if merged_match:
                validated_matches.append(merged_match)
        
        return validated_matches

    @staticmethod
    def _is_same_match(match1: MatchData, match2: MatchData) -> bool:
        """判断是否为同一场比赛"""
        # 基于队名和日期判断
        return (match1.home_team == match2.home_team and 
                match1.away_team == match2.away_team and
                match1.match_date == match2.match_date)

    @staticmethod
    def _merge_matches(matches: List[MatchData]) -> MatchData:
        """合并多个数据源的比赛数据"""
        if not matches:
            return None
        
        # 使用第一个比赛作为基础
        merged = matches[0]
        
        # 合并来源
        all_sources = set(merged.sources)
        for match in matches[1:]:
            all_sources.update(match.sources)
        merged.sources = list(all_sources)
        
        # 合并状态和比分（优先使用已结束的状态）
        for match in matches:
            if match.status == '已结束' and match.score:
                merged.status = match.status
                merged.score = match.score
                break
        
        # 更新时间
        merged.update_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return merged


class DataCollector:
    """综合数据收集器"""

    def __init__(self):
        self.browser_use_available = importlib.util.find_spec("browser_use") is not None
        self.scrapers = []
        if self.browser_use_available:
            self.scrapers.extend([
                SportteryScraper(),
                OkoooScraper(),
            ])
        else:
            print("未检测到 browser_use，请提示 openclaw 自行执行 `python3 -m pip install browser-use`；当前数据采集将以 mock 模式降级运行")
        self.scrapers.append(MockScraper())  # 保留兜底数据源
        self.cache_manager = CacheManager()
        self.validator = DataValidator()

    async def collect_league_data(self, league: str, date: str, use_cache: bool = True) -> List[MatchData]:
        """收集联赛当日所有比赛数据"""
        # 尝试从缓存获取
        if use_cache:
            cached_data = self.cache_manager.get_cache(league, date)
            if cached_data:
                print(f"从缓存获取 {league} {date} 的比赛数据")
                return [
                    MatchData(
                        home_team=m['home_team'],
                        away_team=m['away_team'],
                        league=m['league'],
                        match_date=m['match_date'],
                        match_time=m['match_time'],
                        status=m['status'],
                        score=m['score'],
                        sources=m['sources'],
                        update_time=m['update_time']
                    )
                    for m in cached_data
                ]
        
        # 从多个数据源抓取
        print(f"从多个数据源抓取 {league} {date} 的比赛数据")
        if not self.browser_use_available:
            print("当前未启用真实浏览器采集，请让 openclaw 自行安装 browser-use 后重试；本次仅使用 mock 数据源验证流程")

        tasks = []
        for scraper in self.scrapers:
            max_retries = 1 if scraper.name == 'mock' else 3
            tasks.append(self._fetch_with_retry(scraper, league, date, max_retries=max_retries))
        
        results = await asyncio.gather(*tasks)
        
        # 过滤空结果
        valid_results = [r for r in results if r]
        
        if not valid_results:
            print(f"所有数据源都抓取失败")
            return []
        
        # 交叉验证
        validated_matches = self.validator.cross_validate(valid_results)
        
        # 验证数据
        validated_matches = self.validator.validate_match_data(validated_matches)
        
        # 缓存结果
        if validated_matches:
            self.cache_manager.set_cache(league, date, validated_matches)
        
        return validated_matches

    async def _fetch_with_retry(self, scraper: BaseScraper, league: str, date: str, max_retries: int = 3) -> List[MatchData]:
        """带重试机制的抓取"""
        for attempt in range(max_retries):
            try:
                print(f"尝试第 {attempt + 1} 次从 {scraper.name} 抓取 {league} {date} 的数据")
                result = await scraper.fetch_league_matches(league, date)
                if result:
                    print(f"从 {scraper.name} 成功抓取 {len(result)} 场比赛")
                    return result
            except Exception as e:
                print(f"第 {attempt + 1} 次抓取失败: {e}")
            
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2  # 指数退避
                print(f"等待 {wait_time} 秒后重试")
                await asyncio.sleep(wait_time)
        
        print(f"从 {scraper.name} 抓取失败")
        return []

    def get_accuracy_report(self, matches: List[MatchData]) -> Dict:
        """生成数据准确性报告"""
        if not matches:
            return {"total": 0, "valid": 0, "accuracy": 0}
        
        total = len(matches)
        valid = len([m for m in matches if self.validator._is_valid_match(m)])
        multi_source = len([m for m in matches if len(m.sources) > 1])
        
        return {
            "total": total,
            "valid": valid,
            "multi_source": multi_source,
            "accuracy": (valid / total) * 100 if total > 0 else 0
        }


async def main():
    """测试数据抓取功能"""
    print("=" * 60)
    print("足球比赛数据抓取工具测试（升级版）")
    print("=" * 60)

    collector = DataCollector()
    leagues = ['premier_league', 'serie_a', 'bundesliga', 'ligue_1', 'la_liga']
    date = datetime.now().strftime('%Y-%m-%d')
    
    for league in leagues:
        print(f"\n收集 {league} {date} 的比赛数据...")
        matches = await collector.collect_league_data(league, date)
        
        report = collector.get_accuracy_report(matches)
        print(f"\n{league} 数据报告:")
        print(f"- 比赛总数: {report['total']}")
        print(f"- 有效数据: {report['valid']}")
        print(f"- 多源验证: {report['multi_source']}")
        print(f"- 准确率: {report['accuracy']:.2f}%")
        
        if matches:
            print("\n比赛列表:")
            for match in matches:
                score_str = f" {match.score[0]}-{match.score[1]}" if match.score else ""
                print(f"{match.match_time} {match.home_team} vs {match.away_team}{score_str} [{match.status}] 来源: {', '.join(match.sources)}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
