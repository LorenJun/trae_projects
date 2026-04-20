#!/usr/bin/env python3
"""
回填五大联赛赔率数据 - 使用 okooo.com (澳客网) 数据源

注意: 此脚本已完全迁移至使用 okooo.com 数据源

输出位置:
- <league>/analysis/odds/<date_range>_odds.json
- <league>/analysis/odds/<date_range>_odds.csv
"""

import csv
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DATE_START = "2026-04-18"
DATE_END = "2026-04-20"
DATE_TAG = f"{DATE_START}_{DATE_END}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

LEAGUE_CONFIG = {
    "premier_league": {"name": "英超", "competition_id": 1},
    "la_liga": {"name": "西甲", "competition_id": 2},
    "bundesliga": {"name": "德甲", "competition_id": 4},
    "serie_a": {"name": "意甲", "competition_id": 5},
    "ligue_1": {"name": "法甲", "competition_id": 6},
}

# okooo.com 比赛ID映射 (需要手动维护或使用搜索功能)
# 格式: (联赛, 主队, 客队, 日期): match_id
KNOWN_OKOOO_MATCH_IDS = {
    # 英超
    ("premier_league", "布伦特福德", "富勒姆", "2026-04-18"): "1296057",
    ("premier_league", "纽卡斯尔联", "伯恩茅斯", "2026-04-18"): "1296058",
    # 添加更多已知的比赛ID...
}

# 博彩公司ID映射
BOOKMAKER_MAP = {
    "24": "威廉希尔",
    "25": "立博", 
    "26": "Bet365",
    "27": "bwin",
    "28": "澳门彩票",
    "29": "竞彩官方",
    "30": "香港马会",
    "31": "必发",
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
    score: str
    is_finish: bool
    source_match_id: int


class OddsBackfill:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch_league_matches(self, league_code: str) -> List[MatchFixture]:
        """从 tzuqiu.cc 获取赛程数据"""
        config = LEAGUE_CONFIG[league_code]
        url = f"https://tzuqiu.cc/competitions/{config['competition_id']}/fixture.do"
        
        try:
            response = self.session.get(url, timeout=25)
            response.raise_for_status()
            page_text = response.text
        except Exception as e:
            print(f"[ERROR] 获取赛程失败: {url} - {e}")
            return []
        
        # 提取指定日期范围的比赛
        pattern = rf'(\{{"matchDate":"(?:{DATE_START}|{DATE_END})".*?"competitionTeamType":"club"\}})'
        raw_matches = re.findall(pattern, page_text)

        fixtures: List[MatchFixture] = []
        for item in raw_matches:
            try:
                data = json.loads(item)
                fixtures.append(
                    MatchFixture(
                        league_code=league_code,
                        league_name=config["name"],
                        competition_id=config["competition_id"],
                        match_date=data["matchDate"],
                        match_time=data.get("startHMStr", ""),
                        round_name=data.get("stageName", ""),
                        home_team=data["homeTeamName"],
                        away_team=data["awayTeamName"],
                        score=data.get("score", ""),
                        is_finish=bool(data.get("isFinish")),
                        source_match_id=int(data["id"]),
                    )
                )
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[WARN] 解析比赛数据失败: {e}")
                continue
        return fixtures

    def get_okooo_match_id(self, fixture: MatchFixture) -> Optional[str]:
        """获取 okooo.com 的比赛ID"""
        key = (fixture.league_code, fixture.home_team, fixture.away_team, fixture.match_date)
        match_id = KNOWN_OKOOO_MATCH_IDS.get(key)
        if match_id:
            return match_id
        
        # TODO: 实现搜索功能来查找比赛ID
        print(f"[WARN] 未找到比赛ID: {fixture.home_team} vs {fixture.away_team}")
        return None

    def fetch_okooo_odds(self, match_id: str) -> Optional[Dict]:
        """使用 Playwright 从 okooo.com 获取赔率数据"""
        url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=HEADERS["User-Agent"],
                )
                page = context.new_page()
                
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_selector("table#datatable1", timeout=10000)
                
                # 提取表格数据
                rows = page.query_selector_all("table#datatable1 tbody tr")
                
                bookmaker_odds = {}
                for row in rows:
                    try:
                        row_id = row.get_attribute("id")
                        if not row_id or not row_id.startswith("tr"):
                            continue
                        
                        bookmaker_id = row_id.replace("tr", "")
                        bookmaker_name = BOOKMAKER_MAP.get(bookmaker_id, f"未知_{bookmaker_id}")
                        
                        cells = row.query_selector_all("td")
                        if len(cells) < 5:
                            continue
                        
                        # 提取初赔和即时赔
                        odds_data = {
                            "bookmaker": bookmaker_name,
                            "initial": {},
                            "current": {},
                        }
                        
                        # 解析赔率单元格
                        for i, cell in enumerate(cells):
                            text = cell.inner_text().strip()
                            if i == 2:  # 初赔
                                values = text.split()
                                if len(values) >= 3:
                                    odds_data["initial"] = {
                                        "home": float(values[0]) if values[0].replace(".", "").isdigit() else None,
                                        "draw": float(values[1]) if values[1].replace(".", "").isdigit() else None,
                                        "away": float(values[2]) if values[2].replace(".", "").isdigit() else None,
                                    }
                            elif i == 3:  # 即时赔
                                values = text.split()
                                if len(values) >= 3:
                                    odds_data["current"] = {
                                        "home": float(values[0]) if values[0].replace(".", "").isdigit() else None,
                                        "draw": float(values[1]) if values[1].replace(".", "").isdigit() else None,
                                        "away": float(values[2]) if values[2].replace(".", "").isdigit() else None,
                                    }
                        
                        bookmaker_odds[bookmaker_name] = odds_data
                        
                    except Exception as e:
                        print(f"[WARN] 解析行数据失败: {e}")
                        continue
                
                browser.close()
                
                return {
                    "match_id": match_id,
                    "source_url": url,
                    "bookmaker_odds": bookmaker_odds,
                    "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                
        except Exception as e:
            print(f"[ERROR] 获取赔率失败: {url} - {e}")
            return None

    def build_match_record(self, fixture: MatchFixture) -> Optional[Dict]:
        """构建比赛记录"""
        match_id = self.get_okooo_match_id(fixture)
        if not match_id:
            return None
        
        odds_data = self.fetch_okooo_odds(match_id)
        if not odds_data:
            return None
        
        # 解析比分
        home_score = None
        away_score = None
        if fixture.score:
            match = re.match(r"(\d+)\s*[:：-]\s*(\d+)", fixture.score)
            if match:
                home_score = int(match.group(1))
                away_score = int(match.group(2))
        
        # 确定比赛结果
        if home_score is None or away_score is None:
            result_text = ""
        elif home_score > away_score:
            result_text = "主胜"
        elif home_score < away_score:
            result_text = "客胜"
        else:
            result_text = "平局"
        
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
            "actual_score": fixture.score,
            "actual_result": result_text,
            "home_score": home_score,
            "away_score": away_score,
            "okooo_match_id": match_id,
            "data_source": "okooo.com (澳客网)",
            "sources": {
                "fixture": f"https://tzuqiu.cc/competitions/{fixture.competition_id}/fixture.do",
                "odds": odds_data["source_url"],
            },
            "odds": odds_data.get("bookmaker_odds", {}),
            "metadata": {
                "fetched_at": odds_data["fetched_at"],
                "source_match_id_tzuqiu": fixture.source_match_id,
            },
        }

    def write_outputs(self, league_code: str, records: List[Dict]) -> None:
        """写入输出文件"""
        output_dir = BASE_DIR / league_code / "analysis" / "odds"
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "league_code": league_code,
            "league_name": LEAGUE_CONFIG[league_code]["name"],
            "date_range": {"start": DATE_START, "end": DATE_END},
            "match_count": len(records),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_source": "okooo.com (澳客网)",
            "matches": records,
        }

        json_path = output_dir / f"{DATE_TAG}_odds.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[INFO] JSON 输出: {json_path}")

        # CSV 输出 (简化版)
        if records:
            csv_path = output_dir / f"{DATE_TAG}_odds.csv"
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "match_id", "league", "date", "home_team", "away_team",
                    "score", "result", "okooo_match_id"
                ])
                for r in records:
                    writer.writerow([
                        r["match_id"],
                        r["league_name"],
                        r["match_date"],
                        r["home_team"],
                        r["away_team"],
                        r["actual_score"],
                        r["actual_result"],
                        r["okooo_match_id"],
                    ])
            print(f"[INFO] CSV 输出: {csv_path}")

    def run(self) -> None:
        """运行回填流程"""
        print(f"[INFO] 开始回填 {DATE_START} ~ {DATE_END} 的赔率数据")
        print(f"[INFO] 数据源: okooo.com (澳客网)")
        
        for league_code in LEAGUE_CONFIG:
            print(f"\n[INFO] 处理联赛: {league_code}")
            fixtures = self.fetch_league_matches(league_code)
            print(f"  找到 {len(fixtures)} 场比赛")
            
            records = []
            for fixture in fixtures:
                print(f"  处理: {fixture.home_team} vs {fixture.away_team}")
                record = self.build_match_record(fixture)
                if record:
                    records.append(record)
                    print(f"    ✓ 成功获取赔率")
                else:
                    print(f"    ✗ 获取赔率失败")
                time.sleep(1)  # 避免请求过快
            
            self.write_outputs(league_code, records)
            print(f"  完成: {len(records)}/{len(fixtures)} 场比赛")


def main():
    backfill = OddsBackfill()
    backfill.run()


if __name__ == "__main__":
    main()
