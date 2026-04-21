#!/usr/bin/env python3
"""
回填未来赛程的"即时赔率快照"，用于生成带历史相似盘路的预测报告。
数据源: 澳客网 (okooo.com) - 使用浏览器自动化

注意: 由于澳客网有严格的反爬虫机制，本脚本使用 browser-use 工具进行浏览器自动化。

输出位置:
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.json
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.csv

说明:
- 对未开赛场次，澳客网页面提供"初始/即时"两行数据；本脚本将"即时"写入 final 字段。
- actual_score / actual_result 对未完赛为空字符串。
"""

import argparse
import csv
import json
import re
import subprocess
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

    def _browser_use_open_and_state(self, url: str, timeout: int = 45) -> str:
        """Open a page via browser-use and return the textual state output.

        We keep this isolated so all odds pages share consistent error handling.
        """
        try:
            subprocess.run(
                ["browser-use", "open", url],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            result = subprocess.run(
                ["browser-use", "state"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception as exc:
            print(f"  [WARN] browser-use 调用失败: {exc}")
            return ""

        if result.returncode != 0:
            # Keep stderr small to avoid noisy logs; caller will treat as empty.
            err = (result.stderr or "").strip()
            if err:
                err = err.splitlines()[-1]
            print(f"  [WARN] browser-use state 失败: {err or 'unknown'}")
            return ""

        return result.stdout or ""

    def _parse_total_line(self, line_text: str) -> Optional[float]:
        """Parse O/U line text like '2.5', '2/2.5', '2.5/3' into a float value."""
        if not line_text:
            return None
        text = line_text.strip()
        # Normalize some common separators
        text = text.replace(" ", "")
        parts = text.split("/")
        try:
            if len(parts) == 1:
                return float(parts[0])
            nums = [float(p) for p in parts if p]
            if not nums:
                return None
            # Asian-style split line: use the mean as a numeric proxy.
            return sum(nums) / len(nums)
        except Exception:
            return None

    def _extract_kelly_from_text(self, page_content: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Extract kelly (home/draw/away) for (final, initial) from page text."""
        if not page_content:
            return (None, None, None, None, None, None)

        # Prefer matches near "凯利/Kelly" + "平均"
        patt = re.compile(r'(?:凯利|Kelly)[\s\S]{0,120}?平均\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', re.IGNORECASE)
        lines = patt.findall(page_content)
        if not lines:
            # Fallback: any "平均 x.xx x.xx x.xx" with plausible kelly ranges.
            cand = re.findall(r'平均\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', page_content)
            for trip in cand:
                h, d, a = (float(trip[0]), float(trip[1]), float(trip[2]))
                if all(0.3 <= v <= 2.5 for v in (h, d, a)):
                    lines.append((trip[0], trip[1], trip[2]))
            # keep at most 2 rows (final, initial)
            lines = lines[:2]

        if lines:
            fh, fd, fa = (float(lines[0][0]), float(lines[0][1]), float(lines[0][2]))
        else:
            fh = fd = fa = None
        if len(lines) >= 2:
            ih, id_, ia = (float(lines[1][0]), float(lines[1][1]), float(lines[1][2]))
        else:
            ih = id_ = ia = None

        return (ih, id_, ia, fh, fd, fa)

    def _extract_over_under_from_text(
        self, page_content: str
    ) -> Tuple[Optional[str], Optional[float], Optional[float], Optional[float], Optional[str], Optional[float], Optional[float], Optional[float]]:
        """Extract O/U (line + over/under water) for (final, initial) from page text.

        Returns:
            (initial_line_text, initial_line_value, initial_over_water, initial_under_water,
             final_line_text, final_line_value, final_over_water, final_under_water)
        """
        if not page_content:
            return (None, None, None, None, None, None, None, None)

        # Prefer "平均 <over_water> <line> <under_water>"
        patt = re.compile(r'平均\s+(\d+\.\d{2})\s+(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)\s+(\d+\.\d{2})')
        rows = patt.findall(page_content)

        # Fallback: any triplet water/line/water with plausible ranges
        if not rows:
            generic = re.findall(r'(\d+\.\d{2})\s+(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)\s+(\d+\.\d{2})', page_content)
            for ow, line, uw in generic:
                try:
                    ow_f = float(ow)
                    uw_f = float(uw)
                    lv = self._parse_total_line(line)
                except Exception:
                    continue
                if lv is None:
                    continue
                if 0.5 <= ow_f <= 2.5 and 0.5 <= uw_f <= 2.5 and 0.5 <= lv <= 10:
                    rows.append((ow, line, uw))
            rows = rows[:2]

        # Convention in this repo: first row is "即时/final", second is "初始/initial".
        final_line_text = final_line_value = final_over = final_under = None
        init_line_text = init_line_value = init_over = init_under = None
        if len(rows) >= 1:
            final_over, final_line_text, final_under = rows[0][0], rows[0][1], rows[0][2]
            final_line_value = self._parse_total_line(final_line_text)
            final_over = float(final_over)
            final_under = float(final_under)
        if len(rows) >= 2:
            init_over, init_line_text, init_under = rows[1][0], rows[1][1], rows[1][2]
            init_line_value = self._parse_total_line(init_line_text)
            init_over = float(init_over)
            init_under = float(init_under)

        return (init_line_text, init_line_value, init_over, init_under, final_line_text, final_line_value, final_over, final_under)

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

    def _date_in_range(self, md: str, start: str, end: str) -> bool:
        return start <= md <= end

    def fetch_league_matches(self, league_code: str, start: str, end: str) -> List[MatchFixture]:
        """从 tzuqiu.cc 获取赛程数据"""
        config = LEAGUE_CONFIG[league_code]
        url = f"https://tzuqiu.cc/competitions/{config['competition_id']}/fixture.do"
        page_text = self.fetch_text(url, retries=5, timeout=80)

        raw_matches = re.findall(
            r'(\{"matchDate":"\d{4}-\d{2}-\d{2}".*?"competitionTeamType":"club"\})',
            page_text,
        )

        fixtures: List[MatchFixture] = []
        for item in raw_matches:
            try:
                data = json.loads(item)
            except Exception:
                continue
            md = data.get("matchDate")
            if not md or not self._date_in_range(md, start, end):
                continue
            fixtures.append(
                MatchFixture(
                    league_code=league_code,
                    league_name=config["name"],
                    competition_id=config["competition_id"],
                    match_date=md,
                    match_time=data.get("startHMStr", ""),
                    round_name=data.get("stageName", ""),
                    home_team=data.get("homeTeamName", ""),
                    away_team=data.get("awayTeamName", ""),
                    source_match_id=int(data.get("id") or 0),
                )
            )
        # 同一天可能重复，按对阵去重
        seen = set()
        unique = []
        for f in fixtures:
            key = (f.match_date, f.home_team, f.away_team)
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        return unique

    def normalize_team_name(self, name: str) -> str:
        normalized = TEAM_ALIASES.get(name, name)
        normalized = normalized.replace("FC", "").replace(" ", "").replace("俱乐部", "")
        return normalized

    def _search_okooo_match(self, home_team: str, away_team: str) -> Optional[str]:
        """使用搜索引擎查找澳客网比赛页面"""
        cache_key = (home_team, away_team)
        if cache_key in self._match_id_cache:
            return self._match_id_cache[cache_key]

        # 构建搜索查询 - 使用中文球队名
        query = f"{home_team}vs{away_team} site:okooo.com/match"
        
        # 使用 DuckDuckGo 搜索
        search_url = f"https://html.duckduckgo.com/html/?q={query}"
        
        try:
            page = self.fetch_text(search_url, retries=2, timeout=30)
            
            # 从搜索结果中提取澳客网比赛ID
            # 澳客网移动端链接格式: m.okooo.com/match/odds.php?MatchID=XXXXX
            match_ids = re.findall(r'okooo\.com/match/[^?]*\?MatchID=(\d+)', page)
            if match_ids:
                # 去重并保持顺序
                seen = set()
                unique_ids = []
                for mid in match_ids:
                    if mid not in seen:
                        seen.add(mid)
                        unique_ids.append(mid)
                
                # 优先选择欧指页面的ID
                for mid in unique_ids:
                    if f"odds.php?MatchID={mid}" in page:
                        self._match_id_cache[cache_key] = mid
                        return mid
                
                # 如果没有欧指页面，返回第一个
                match_id = unique_ids[0]
                self._match_id_cache[cache_key] = match_id
                return match_id
            
            # 尝试交换主客
            query = f"{away_team}vs{home_team} site:okooo.com/match"
            search_url = f"https://html.duckduckgo.com/html/?q={query}"
            page = self.fetch_text(search_url, retries=2, timeout=30)
            match_ids = re.findall(r'okooo\.com/match/[^?]*\?MatchID=(\d+)', page)
            if match_ids:
                seen = set()
                unique_ids = []
                for mid in match_ids:
                    if mid not in seen:
                        seen.add(mid)
                        unique_ids.append(mid)
                
                for mid in unique_ids:
                    if f"odds.php?MatchID={mid}" in page:
                        self._match_id_cache[cache_key] = mid
                        return mid
                
                match_id = unique_ids[0]
                self._match_id_cache[cache_key] = match_id
                return match_id
                
        except Exception as e:
            print(f"  [WARN] 搜索澳客网比赛失败: {e}")
        
        return None

    def _fetch_okooo_odds_with_browser(self, match_id: str) -> Dict:
        """使用 browser-use 工具获取澳客网赔率数据"""
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
            "over_under_line_text_initial": None,
            "over_under_line_value_initial": None,
            "over_under_over_water_initial": None,
            "over_under_under_water_initial": None,
            "over_under_line_text_final": None,
            "over_under_line_value_final": None,
            "over_under_over_water_final": None,
            "over_under_under_water_final": None,
            "kelly_source_url": "",
            "over_under_source_url": "",
        }
        
        # 欧赔页面 - 使用移动端
        ouzhi_url = f"https://m.okooo.com/match/odds.php?MatchID={match_id}"
        
        try:
            page_content = self._browser_use_open_and_state(ouzhi_url, timeout=45)
            if page_content:
                
                # 检查是否被阻断
                if "访问被阻断" in page_content or "安全威胁" in page_content:
                    print("  [WARN] 澳客网访问被阻断")
                    return odds_data
                
                # 解析欧赔数据
                # 查找平均赔率行 - 通常格式为 "平均 2.15 3.20 3.40"
                avg_lines = re.findall(r'平均\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', page_content)
                if avg_lines:
                    # 第一行通常是即时赔率，第二行是初始赔率
                    if len(avg_lines) >= 1:
                        odds_data["home_win_final"] = float(avg_lines[0][0])
                        odds_data["draw_final"] = float(avg_lines[0][1])
                        odds_data["away_win_final"] = float(avg_lines[0][2])
                    if len(avg_lines) >= 2:
                        odds_data["home_win_initial"] = float(avg_lines[1][0])
                        odds_data["draw_initial"] = float(avg_lines[1][1])
                        odds_data["away_win_initial"] = float(avg_lines[1][2])
                
                # 如果没有找到"平均"行，尝试其他模式
                if odds_data["home_win_final"] is None:
                    # 查找任何三个连续的数字（赔率通常在1-10之间）
                    all_odds = re.findall(r'(\d+\.\d{2})\s+(\d+\.\d{2})\s+(\d+\.\d{2})', page_content)
                    for odd_triplet in all_odds:
                        h, d, a = float(odd_triplet[0]), float(odd_triplet[1]), float(odd_triplet[2])
                        # 验证是否是合理的赔率（1-20之间）
                        if 1.0 <= h <= 20.0 and 1.0 <= d <= 20.0 and 1.0 <= a <= 20.0:
                            if odds_data["home_win_final"] is None:
                                odds_data["home_win_final"] = h
                                odds_data["draw_final"] = d
                                odds_data["away_win_final"] = a
                            elif odds_data["home_win_initial"] is None:
                                odds_data["home_win_initial"] = h
                                odds_data["draw_initial"] = d
                                odds_data["away_win_initial"] = a
                                break
        except Exception as e:
            print(f"  [WARN] 获取欧赔失败: {e}")
        
        # 亚盘页面
        yazhi_url = f"https://m.okooo.com/match/handicap.php?MatchID={match_id}"
        
        try:
            page_content = self._browser_use_open_and_state(yazhi_url, timeout=45)
            if page_content:
                
                # 检查是否被阻断
                if "访问被阻断" in page_content or "安全威胁" in page_content:
                    return odds_data
                
                # 解析亚盘数据
                # 格式通常是: 主水 盘口 客水
                # 例如: 0.95 半球 0.95
                asian_pattern = r'(\d+\.\d{2})\s+([\w\-/]+)\s+(\d+\.\d{2})'
                matches = re.findall(asian_pattern, page_content)
                
                if matches:
                    # 第一行通常是即时赔率，第二行是初始赔率
                    if len(matches) >= 1:
                        odds_data["asian_home_water_final"] = float(matches[0][0])
                        odds_data["asian_handicap_final"] = self._parse_handicap(matches[0][1])
                        odds_data["asian_away_water_final"] = float(matches[0][2])
                    if len(matches) >= 2:
                        odds_data["asian_home_water_initial"] = float(matches[1][0])
                        odds_data["asian_handicap_initial"] = self._parse_handicap(matches[1][1])
                        odds_data["asian_away_water_initial"] = float(matches[1][2])
        except Exception as e:
            print(f"  [WARN] 获取亚盘失败: {e}")

        # 凯利指数页面（优先抓取平均凯利：主/平/客）
        kelly_urls = [
            f"https://m.okooo.com/match/kelly.php?MatchID={match_id}",
            f"https://m.okooo.com/match/kellyindex.php?MatchID={match_id}",
            f"https://m.okooo.com/match/kelly_index.php?MatchID={match_id}",
        ]
        for kelly_url in kelly_urls:
            try:
                page_content = self._browser_use_open_and_state(kelly_url, timeout=45)
                if not page_content:
                    continue
                if "访问被阻断" in page_content or "安全威胁" in page_content:
                    continue
                ih, id_, ia, fh, fd, fa = self._extract_kelly_from_text(page_content)
                if any(v is not None for v in (ih, id_, ia, fh, fd, fa)):
                    odds_data["kelly_home_initial"] = ih
                    odds_data["kelly_draw_initial"] = id_
                    odds_data["kelly_away_initial"] = ia
                    odds_data["kelly_home_final"] = fh
                    odds_data["kelly_draw_final"] = fd
                    odds_data["kelly_away_final"] = fa
                    odds_data["kelly_source_url"] = kelly_url
                    break
            except Exception as e:
                print(f"  [WARN] 获取凯利失败: {e}")

        # 大小球页面（总进球 O/U）
        ou_urls = [
            f"https://m.okooo.com/match/goal.php?MatchID={match_id}",
            f"https://m.okooo.com/match/goals.php?MatchID={match_id}",
            f"https://m.okooo.com/match/total.php?MatchID={match_id}",
            f"https://m.okooo.com/match/daxiaoqiu.php?MatchID={match_id}",
            f"https://m.okooo.com/match/ou.php?MatchID={match_id}",
        ]
        for ou_url in ou_urls:
            try:
                page_content = self._browser_use_open_and_state(ou_url, timeout=45)
                if not page_content:
                    continue
                if "访问被阻断" in page_content or "安全威胁" in page_content:
                    continue
                (
                    init_line_text,
                    init_line_value,
                    init_over,
                    init_under,
                    final_line_text,
                    final_line_value,
                    final_over,
                    final_under,
                ) = self._extract_over_under_from_text(page_content)
                if any(v is not None for v in (init_line_value, final_line_value, init_over, final_over, init_under, final_under)):
                    odds_data["over_under_line_text_initial"] = init_line_text
                    odds_data["over_under_line_value_initial"] = init_line_value
                    odds_data["over_under_over_water_initial"] = init_over
                    odds_data["over_under_under_water_initial"] = init_under
                    odds_data["over_under_line_text_final"] = final_line_text
                    odds_data["over_under_line_value_final"] = final_line_value
                    odds_data["over_under_over_water_final"] = final_over
                    odds_data["over_under_under_water_final"] = final_under
                    odds_data["over_under_source_url"] = ou_url
                    break
            except Exception as e:
                print(f"  [WARN] 获取大小球失败: {e}")
        
        return odds_data

    def _parse_handicap(self, handicap_text: str) -> Optional[float]:
        """解析盘口文本为数值"""
        if not handicap_text:
            return None
        
        try:
            text = handicap_text.strip()
            
            # 数字直接解析
            if re.match(r'^-?\d+\.?\d*$', text):
                return float(text)
            
            # 中文盘口映射
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
            
            # 尝试提取数字
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
            # 使用澳客网数据源
            fixture.okooo_match_id = okooo_match_id
            odds = self._fetch_okooo_odds_with_browser(okooo_match_id)
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
            "actual_score": "",
            "actual_result": "",
            "okooo_match_id": fixture.okooo_match_id,
            "captured_at": captured_at,
            "sources": {
                "fixture": f"https://tzuqiu.cc/competitions/{fixture.competition_id}/fixture.do",
                "europe_odds": f"https://m.okooo.com/match/odds.php?MatchID={fixture.okooo_match_id}" if fixture.okooo_match_id else "",
                "asian_odds": f"https://m.okooo.com/match/handicap.php?MatchID={fixture.okooo_match_id}" if fixture.okooo_match_id else "",
                "kelly": odds.get("kelly_source_url") or (f"https://m.okooo.com/match/kelly.php?MatchID={fixture.okooo_match_id}" if fixture.okooo_match_id else ""),
                "over_under": odds.get("over_under_source_url") or "",
            },
            "胜平负赔率": {
                "initial": {"home": odds["home_win_initial"], "draw": odds["draw_initial"], "away": odds["away_win_initial"]},
                "final": {"home": odds["home_win_final"], "draw": odds["draw_final"], "away": odds["away_win_final"]},
            },
            "欧赔": {
                "initial": {"home": odds["home_win_initial"], "draw": odds["draw_initial"], "away": odds["away_win_initial"]},
                "final": {"home": odds["home_win_final"], "draw": odds["draw_final"], "away": odds["away_win_final"]},
            },
            "亚值": {
                "initial": {
                    "home_water": odds["asian_home_water_initial"],
                    "handicap_text": str(odds["asian_handicap_initial"]) if odds["asian_handicap_initial"] else "",
                    "handicap_value": odds["asian_handicap_initial"],
                    "away_water": odds["asian_away_water_initial"],
                },
                "final": {
                    "home_water": odds["asian_home_water_final"],
                    "handicap_text": str(odds["asian_handicap_final"]) if odds["asian_handicap_final"] else "",
                    "handicap_value": odds["asian_handicap_final"],
                    "away_water": odds["asian_away_water_final"],
                },
            },
            "凯利": {
                "initial": {"home": odds["kelly_home_initial"], "draw": odds["kelly_draw_initial"], "away": odds["kelly_away_initial"]},
                "final": {"home": odds["kelly_home_final"], "draw": odds["kelly_draw_final"], "away": odds["kelly_away_final"]},
            },
            "大小球": {
                "initial": {
                    "line_text": odds.get("over_under_line_text_initial") or "",
                    "line_value": odds.get("over_under_line_value_initial"),
                    "over_water": odds.get("over_under_over_water_initial"),
                    "under_water": odds.get("over_under_under_water_initial"),
                },
                "final": {
                    "line_text": odds.get("over_under_line_text_final") or "",
                    "line_value": odds.get("over_under_line_value_final"),
                    "over_water": odds.get("over_under_over_water_final"),
                    "under_water": odds.get("over_under_under_water_final"),
                },
            },
            "离散率": {
                "initial": {"home": None, "draw": None, "away": None},
                "final": {"home": None, "draw": None, "away": None},
            },
        }

    def flatten_record(self, record: Dict) -> Dict:
        def pick(group: str, phase: str, key: str):
            return record.get(group, {}).get(phase, {}).get(key)

        def pick_asian(phase: str, key: str):
            return record.get("亚值", {}).get(phase, {}).get(key)

        def pick_ou(phase: str, key: str):
            return record.get("大小球", {}).get(phase, {}).get(key)

        return {
            "match_id": record["match_id"],
            "match_date": record["match_date"],
            "match_time": record.get("match_time", ""),
            "home_team": record["home_team"],
            "away_team": record["away_team"],
            "okooo_match_id": record.get("okooo_match_id", ""),
            "captured_at": record.get("captured_at", ""),
            "胜平负_初始_主": pick("胜平负赔率", "initial", "home"),
            "胜平负_初始_平": pick("胜平负赔率", "initial", "draw"),
            "胜平负_初始_客": pick("胜平负赔率", "initial", "away"),
            "胜平负_即时_主": pick("胜平负赔率", "final", "home"),
            "胜平负_即时_平": pick("胜平负赔率", "final", "draw"),
            "胜平负_即时_客": pick("胜平负赔率", "final", "away"),
            "欧赔_初始_主": pick("欧赔", "initial", "home"),
            "欧赔_初始_平": pick("欧赔", "initial", "draw"),
            "欧赔_初始_客": pick("欧赔", "initial", "away"),
            "欧赔_即时_主": pick("欧赔", "final", "home"),
            "欧赔_即时_平": pick("欧赔", "final", "draw"),
            "欧赔_即时_客": pick("欧赔", "final", "away"),
            "亚值_初始_主水": pick_asian("initial", "home_water"),
            "亚值_初始_盘口值": pick_asian("initial", "handicap_value"),
            "亚值_初始_客水": pick_asian("initial", "away_water"),
            "亚值_即时_主水": pick_asian("final", "home_water"),
            "亚值_即时_盘口值": pick_asian("final", "handicap_value"),
            "亚值_即时_客水": pick_asian("final", "away_water"),
            "凯利_初始_主": pick("凯利", "initial", "home"),
            "凯利_初始_平": pick("凯利", "initial", "draw"),
            "凯利_初始_客": pick("凯利", "initial", "away"),
            "凯利_即时_主": pick("凯利", "final", "home"),
            "凯利_即时_平": pick("凯利", "final", "draw"),
            "凯利_即时_客": pick("凯利", "final", "away"),
            "大小球_初始_盘口值": pick_ou("initial", "line_value"),
            "大小球_初始_大水": pick_ou("initial", "over_water"),
            "大小球_初始_小水": pick_ou("initial", "under_water"),
            "大小球_即时_盘口值": pick_ou("final", "line_value"),
            "大小球_即时_大水": pick_ou("final", "over_water"),
            "大小球_即时_小水": pick_ou("final", "under_water"),
            "离散率_初始_主": pick("离散率", "initial", "home"),
            "离散率_初始_平": pick("离散率", "initial", "draw"),
            "离散率_初始_客": pick("离散率", "initial", "away"),
            "离散率_即时_主": pick("离散率", "final", "home"),
            "离散率_即时_平": pick("离散率", "final", "draw"),
            "离散率_即时_客": pick("离散率", "final", "away"),
        }

    def write_outputs(self, league_code: str, start: str, end: str, records: List[Dict]) -> Tuple[str, str]:
        output_dir = BASE_DIR / league_code / "analysis" / "odds_snapshots"
        output_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{start}_{end}"

        payload = {
            "league_code": league_code,
            "league_name": LEAGUE_CONFIG[league_code]["name"],
            "date_range": {"start": start, "end": end},
            "match_count": len(records),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data_source": "okooo.com (澳客网)",
            "matches": records,
        }

        json_path = output_dir / f"{tag}_odds_snapshot.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        csv_path = output_dir / f"{tag}_odds_snapshot.csv"
        rows = [self.flatten_record(r) for r in records]
        if rows:
            fieldnames = list(rows[0].keys())
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            csv_path.write_text("", encoding="utf-8")

        return str(json_path), str(csv_path)

    def run(self, start: str, end: str, leagues: Optional[List[str]] = None) -> List[str]:
        captured_at = time.strftime("%Y-%m-%d %H:%M:%S")
        targets = leagues or list(LEAGUE_CONFIG.keys())
        outputs = []
        for league_code in targets:
            print(f"[INFO] snapshot {league_code} {start}~{end}")
            print(f"  首选数据源: okooo.com (澳客网)")
            print(f"  数据源: okooo.com (澳客网)")
            fixtures = self.fetch_league_matches(league_code, start, end)
            print(f"  Found {len(fixtures)} fixtures")
            records = []
            for fixture in fixtures:
                try:
                    records.append(self.build_match_record(fixture, captured_at))
                    print(f"  [OK] {fixture.match_date} {fixture.home_team} vs {fixture.away_team}")
                except Exception as e:
                    print(f"  [WARN] skip {fixture.match_date} {fixture.home_team} vs {fixture.away_team}: {e}")
                time.sleep(2)  # 增加延迟，避免请求过快
            j, c = self.write_outputs(league_code, start, end, records)
            outputs.extend([j, c])
        return outputs


def _default_range(days: int = 7) -> Tuple[str, str]:
    start = date.today()
    end = start + timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--end", type=str, default=None)
    parser.add_argument("--leagues", type=str, default=None, help="comma separated league codes")

    args = parser.parse_args()

    start, end = args.start, args.end
    if not start or not end:
        start, end = _default_range(7)
    leagues = args.leagues.split(",") if args.leagues else None

    outputs = OddsSnapshotBackfill().run(start, end, leagues=leagues)
    print("OUTPUTS")
    for p in outputs:
        print(p)


if __name__ == "__main__":
    main()
