#!/usr/bin/env python3
"""
回填未来赛程的"即时赔率快照"，使用浏览器自动化访问澳客网。
数据源: 澳客网 (okooo.com) - 使用 Playwright 浏览器自动化

输出位置:
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.json
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.csv

说明:
- 对未开赛场次，澳客网页面提供"初始/即时"两行数据
- actual_score / actual_result 对未完赛为空字符串
"""

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

LEAGUE_CONFIG = {
    "premier_league": {"name": "英超", "competition_id": 1, "okooo_id": 92},
    "la_liga": {"name": "西甲", "competition_id": 2, "okooo_id": 85},
    "bundesliga": {"name": "德甲", "competition_id": 4, "okooo_id": 39},
    "serie_a": {"name": "意甲", "competition_id": 5, "okooo_id": 34},
    "ligue_1": {"name": "法甲", "competition_id": 6, "okooo_id": 93},
}

TEAM_ALIASES = {
    "纽卡斯尔": "纽卡斯尔联",
    "热刺": "托特纳姆热刺",
    "布莱顿": "布赖顿",
    "曼联": "曼彻斯特联",
    "曼城": "曼彻斯特城",
    "拜仁": "拜仁慕尼黑",
    "不莱梅": "云达不莱梅",
    "柏林联盟": "柏林联合",
    "门兴": "门兴格拉德巴赫",
    "弗赖堡": "弗莱堡",
    "巴黎圣曼": "巴黎圣日耳曼",
    "斯特拉斯": "斯特拉斯堡",
    "萨索罗": "萨索洛",
    "水晶宫": "水晶宫",
    "西汉姆": "西汉姆联",
    "西汉姆联队": "西汉姆联",
    "诺丁汉": "诺丁汉森林",
    "森林": "诺丁汉森林",
    "伊普斯": "伊普斯维奇",
    "布伦特": "布伦特福德",
    "富勒姆": "富勒姆",
}


@dataclass
class MatchFixture:
    league_code: str
    league_name: str
    competition_id: int
    match_date: str
    match_time: str
    round_name: str
    home_team: str
    away_team: str
    source_match_id: int
    okooo_match_id: Optional[str] = None


class OddsSnapshotBackfill:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._match_id_cache: Dict[Tuple[str, str, str], str] = {}
        self._odds_cache: Dict[str, Dict] = {}

    def fetch_text(self, url: str, encoding: Optional[str] = None, retries: int = 4, timeout: int = 60) -> str:
        last_error = None
        for attempt in range(retries):
            try:
                response = self.session.get(url, timeout=timeout)
                response.raise_for_status()
                if encoding:
                    return response.content.decode(encoding, "ignore")
                return response.text
            except Exception as exc:
                last_error = exc
                time.sleep(1 + attempt * 2)
        raise RuntimeError(f"请求失败: {url} ({last_error})")

    def normalize_team_name(self, name: str) -> str:
        """标准化球队名称用于比较"""
        name = name.strip()
        # 移除常见后缀
        for suffix in ["队", "足球俱乐部", "FC"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        # 应用别名映射
        if name in TEAM_ALIASES:
            name = TEAM_ALIASES[name]
        return name

    def _search_okooo_match(self, home_team: str, away_team: str) -> Optional[str]:
        """从澳客网搜索比赛ID"""
        cache_key = (home_team, away_team)
        if cache_key in self._match_id_cache:
            return self._match_id_cache[cache_key]
        
        # 这里可以实现搜索逻辑
        # 暂时返回None，实际使用时可以通过澳客网的搜索功能获取
        return None

    def _fetch_okooo_odds_with_playwright(self, match_id: str) -> Dict:
        """使用 Playwright 获取澳客网赔率数据"""
        odds_data = {
            "home_win_initial": None,
            "draw_initial": None,
            "away_win_initial": None,
            "home_win_final": None,
            "draw_final": None,
            "away_win_final": None,
            "asian_handicap_initial": None,
            "asian_home_water_initial": None,
            "asian_away_water_initial": None,
            "asian_handicap_final": None,
            "asian_home_water_final": None,
            "asian_away_water_final": None,
            "kelly_home_initial": None,
            "kelly_draw_initial": None,
            "kelly_away_initial": None,
            "kelly_home_final": None,
            "kelly_draw_final": None,
            "kelly_away_final": None,
        }
        
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # 欧赔页面
                ouzhi_url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
                
                try:
                    page.goto(ouzhi_url, wait_until='networkidle', timeout=30000)
                    page.wait_for_timeout(3000)
                    
                    # 获取页面内容
                    page_content = page.content()
                    
                    # 检查是否被阻断
                    if "访问被阻断" in page_content or "安全威胁" in page_content:
                        print("  [WARN] 澳客网访问被阻断")
                        browser.close()
                        return odds_data
                    
                    # 解析欧赔数据
                    tree = page.query_selector_all('table tr')
                    for row in tree:
                        cells = row.query_selector_all('td')
                        if len(cells) >= 6:
                            cell_texts = [c.inner_text().strip() for c in cells]
                            if any('平均' in text for text in cell_texts):
                                numbers = []
                                for text in cell_texts:
                                    matches = re.findall(r'\d+\.\d+', text)
                                    numbers.extend(matches)
                                
                                if len(numbers) >= 6:
                                    odds_data["home_win_initial"] = float(numbers[0])
                                    odds_data["draw_initial"] = float(numbers[1])
                                    odds_data["away_win_initial"] = float(numbers[2])
                                    odds_data["home_win_final"] = float(numbers[3])
                                    odds_data["draw_final"] = float(numbers[4])
                                    odds_data["away_win_final"] = float(numbers[5])
                                break
                    
                    browser.close()
                    
                except Exception as e:
                    print(f"  [WARN] Playwright 访问页面失败: {e}")
                    browser.close()
                    
        except ImportError:
            print("  [WARN] Playwright 未安装")
        except Exception as e:
            print(f"  [WARN] Playwright 初始化失败: {e}")
        
        return odds_data

    def _parse_handicap(self, handicap_text: str) -> Optional[float]:
        """解析盘口文本为数值"""
        if not handicap_text:
            return None
        
        try:
            text = handicap_text.strip()
            
            if re.match(r'^-?\d+\.?\d*$', text):
                return float(text)
            
            handicap_map = {
                "平手": 0,
                "平手/半球": 0.25,
                "半球": 0.5,
                "半球/一球": 0.75,
                "一球": 1,
                "一球/球半": 1.25,
                "球半": 1.5,
                "球半/两球": 1.75,
                "两球": 2,
                "两球/两球半": 2.25,
                "两球半": 2.5,
                "受让平手": 0,
                "受让平手/半球": -0.25,
                "受让半球": -0.5,
                "受让半球/一球": -0.75,
                "受让一球": -1,
                "受让一球/球半": -1.25,
                "受让球半": -1.5,
            }
            
            if text in handicap_map:
                return handicap_map[text]
            
            numbers = re.findall(r'-?\d+\.?\d*', text)
            if numbers:
                return float(numbers[0])
                
        except Exception:
            pass
        
        return None

    def build_match_record(self, fixture: MatchFixture, captured_at: str) -> Dict:
        # 尝试获取澳客网比赛ID
        okooo_match_id = self._search_okooo_match(fixture.home_team, fixture.away_team)
        
        if okooo_match_id:
            fixture.okooo_match_id = okooo_match_id
            odds = self._fetch_okooo_odds_with_playwright(okooo_match_id)
        else:
            raise RuntimeError(f"未找到澳客网比赛ID: {fixture.home_team} vs {fixture.away_team}")

        return {
            "match_id": f"{fixture.match_date}_{fixture.home_team}_{fixture.away_team}",
            "league_code": fixture.league_code,
            "league_name": fixture.league_name,
            "competition_id": fixture.competition_id,
            "round_name": fixture.round_name,
            "match_date": fixture.match_date,
            "match_time": fixture.match_time,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "source_match_id": fixture.source_match_id,
            "okooo_match_id": fixture.okooo_match_id,
            "captured_at": captured_at,
            "eu_initial_home": odds["home_win_initial"],
            "eu_initial_draw": odds["draw_initial"],
            "eu_initial_away": odds["away_win_initial"],
            "eu_final_home": odds["home_win_final"],
            "eu_final_draw": odds["draw_final"],
            "eu_final_away": odds["away_win_final"],
            "ah_initial_handicap": odds["asian_handicap_initial"],
            "ah_initial_home": odds["asian_home_water_initial"],
            "ah_initial_away": odds["asian_away_water_initial"],
            "ah_final_handicap": odds["asian_handicap_final"],
            "ah_final_home": odds["asian_home_water_final"],
            "ah_final_away": odds["asian_away_water_final"],
            "kelly_initial_home": odds["kelly_home_initial"],
            "kelly_initial_draw": odds["kelly_draw_initial"],
            "kelly_initial_away": odds["kelly_away_initial"],
            "kelly_final_home": odds["kelly_home_final"],
            "kelly_final_draw": odds["kelly_draw_final"],
            "kelly_final_away": odds["kelly_away_final"],
            "actual_score": "",
            "actual_result": "",
        }

    def run(self, league_code: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """运行回填任务"""
        print(f"开始处理联赛: {league_code}")
        # 这里需要实现从赛程文件读取比赛列表的逻辑
        pass


def main():
    parser = argparse.ArgumentParser(description="回填赔率快照数据（澳客网数据源）")
    parser.add_argument("--league", default="premier_league", help="联赛代码")
    parser.add_argument("--start-date", help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="结束日期 (YYYY-MM-DD)")
    args = parser.parse_args()
    
    backfill = OddsSnapshotBackfill()
    backfill.run(args.league, args.start_date, args.end_date)


if __name__ == "__main__":
    main()
