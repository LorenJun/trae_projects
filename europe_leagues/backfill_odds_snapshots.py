#!/usr/bin/env python3
"""
回填未来赛程的“即时赔率快照”，用于生成带历史相似盘路的预测报告。

输出位置:
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.json
- <league>/analysis/odds_snapshots/<start>_<end>_odds_snapshot.csv

说明:
- 对未开赛场次，500 页面仍提供“初始/即时”两行数据；本脚本将“即时”写入 final 字段。
- actual_score / actual_result 对未完赛为空字符串。
"""

import argparse
import csv
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from lxml import html


BASE_DIR = Path(__file__).resolve().parent

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


class OddsSnapshotBackfill:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._ouzhi_page_cache: Dict[str, str] = {}
        self._yazhi_page_cache: Dict[str, str] = {}
        self._page_id_cache: Dict[Tuple[str, str], str] = {}
        self._stage_id_cache: Dict[str, int] = {}
        self._round_match_cache: Dict[Tuple[str, int], List[Dict]] = {}

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
        normalized = normalized.replace("FC", "").replace(" ", "")
        return normalized
    
    def _parse_round_number(self, round_name: str) -> Optional[int]:
        # 例: "第34轮"
        m = re.search(r"第\s*(\d+)\s*轮", round_name or "")
        if m:
            return int(m.group(1))
        # 兜底：纯数字
        if round_name and round_name.isdigit():
            return int(round_name)
        return None

    def _infer_stage_id_from_existing_pages(self, league_code: str) -> Optional[int]:
        """尽量从已存在的 odds/odds_snapshots 文件中取一个 page_id，然后反查 500 的 stageId。"""
        candidates = []
        # 优先使用历史落盘 odds（更可信），快照 odds_snapshots 可能会因搜索误命中到旧赛季 fid
        for rel in [
            Path(league_code) / "analysis" / "odds",
            Path(league_code) / "analysis" / "odds_snapshots",
        ]:
            d = BASE_DIR / rel
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for m in payload.get("matches", []):
                    pid = m.get("page_id") or m.get("pageId") or m.get("fid")
                    if pid:
                        # 粗过滤：500 旧赛季 fid 往往偏小（例如 109xxxx），避免误命中导致推导 stageId 偏到旧赛季
                        try:
                            if int(pid) < 1150000:
                                continue
                        except Exception:
                            pass
                        candidates.append(str(pid))
                        break
                if candidates:
                    break
            if candidates:
                break

        if not candidates:
            return None

        page_id = candidates[0]
        try:
            page = self.get_ouzhi_page(page_id)
        except Exception:
            return None
        # 先从赔率页提取联赛 sid，再到联赛首页提取 stageId
        m = re.search(r"liansai\.500\.com/zuqiu-(\d+)/", page)
        if not m:
            return None
        sid = m.group(1)
        try:
            league_home = self.fetch_text(f"https://liansai.500.com/zuqiu-{sid}/", encoding="gb18030", retries=4, timeout=40)
        except Exception:
            return None
        # 优先匹配导航栏“赛程积分榜”对应的 stageId（避免页面其它历史赛季链接干扰）
        m2 = re.search(rf"/zuqiu-{sid}/jifen-(\d+)/\"[^>]*>\\s*赛程积分榜\\s*<", league_home)
        if m2:
            return int(m2.group(1))
        # 兜底：取该联赛首页中最大的 jifen id（通常是最新赛季）
        all_ids = [int(x) for x in re.findall(r"jifen-(\d+)/", league_home)]
        if all_ids:
            return max(all_ids)
        return None

    def get_stage_id(self, league_code: str) -> Optional[int]:
        """获取 500 联赛赛季 stageId（用于 liansai API 取赛程 fid）。"""
        if league_code in self._stage_id_cache:
            return self._stage_id_cache[league_code]
        stid = self._infer_stage_id_from_existing_pages(league_code)
        if not stid:
            stid = self._infer_stage_id_from_liansai_index(LEAGUE_CONFIG[league_code]["name"])
        if stid:
            self._stage_id_cache[league_code] = stid
            return stid
        return None

    def _infer_stage_id_from_liansai_index(self, league_cn_name: str) -> Optional[int]:
        """从 liansai.500.com 首页（热门赛事）推导 sid，再取导航里的 stageId。"""
        try:
            index_html = self.fetch_text("https://liansai.500.com/", encoding="gb18030", retries=4, timeout=60)
        except Exception:
            return None

        # 优先：匹配 alt="2025-2026 西甲" 这类标识，获取 sid
        alt_pat = re.compile(rf'alt=\"\\d{{4}}-\\d{{4}}\\s+{re.escape(league_cn_name)}\"', re.I)
        sid = None
        for m in re.finditer(r'href=\"https://liansai\.500\.com/zuqiu-(\d+)/\"[^>]*>[\s\S]*?<img[^>]+alt=\"([^\"]+)\"', index_html, re.I):
            cand_sid = m.group(1)
            alt_text = m.group(2)
            if alt_pat.search(f'alt=\"{alt_text}\"'):
                sid = cand_sid
                break

        # 兜底：直接找“<h2>西甲</h2>”附近的 /zuqiu-xxxx/
        if not sid:
            m2 = re.search(rf'href=\"https://liansai\.500\.com/zuqiu-(\d+)/\"[\s\S]{{0,400}}?<h2[^>]*>{re.escape(league_cn_name)}<', index_html)
            if m2:
                sid = m2.group(1)

        if not sid:
            return None

        try:
            league_home = self.fetch_text(f"https://liansai.500.com/zuqiu-{sid}/", encoding="gb18030", retries=4, timeout=60)
        except Exception:
            return None

        # 从导航栏提取当前赛季 stageId
        m3 = re.search(rf"/zuqiu-{sid}/jifen-(\d+)/\"[^>]*>\s*赛程积分榜\s*<", league_home)
        if m3:
            return int(m3.group(1))
        all_ids = [int(x) for x in re.findall(r"jifen-(\d+)/", league_home)]
        return max(all_ids) if all_ids else None

    def _fetch_round_matches_from_500(self, league_code: str, round_no: int) -> List[Dict]:
        """通过 liansai.500.com API 拉取指定轮次的对阵列表（包含 fid）。"""
        cache_key = (league_code, round_no)
        if cache_key in self._round_match_cache:
            return self._round_match_cache[cache_key]

        stid = self.get_stage_id(league_code)
        if not stid:
            self._round_match_cache[cache_key] = []
            return []

        url = "https://liansai.500.com/index.php"
        params = {"c": "score", "a": "getmatch", "stid": stid, "round": round_no}
        try:
            resp = self.session.get(url, params=params, timeout=40)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                data = []
        except Exception:
            data = []

        self._round_match_cache[cache_key] = data
        return data

    def _find_page_id_via_500_round_api(self, fixture: MatchFixture) -> Optional[str]:
        round_no = self._parse_round_number(fixture.round_name)
        if not round_no:
            return None
        matches = self._fetch_round_matches_from_500(fixture.league_code, round_no)
        if not matches:
            return None

        target_home = self.normalize_team_name(fixture.home_team)
        target_away = self.normalize_team_name(fixture.away_team)
        for m in matches:
            h = self.normalize_team_name(m.get("hname") or m.get("hsxname") or "")
            a = self.normalize_team_name(m.get("gname") or m.get("gsxname") or "")
            if not h or not a:
                continue
            if h == target_home and a == target_away:
                fid = m.get("fid")
                if fid:
                    return str(fid)
        return None

    def get_ouzhi_page(self, page_id: str) -> str:
        if page_id not in self._ouzhi_page_cache:
            url = f"https://odds.500star.com/fenxi/ouzhi-{page_id}.shtml"
            self._ouzhi_page_cache[page_id] = self.fetch_text(url, encoding="gb18030")
        return self._ouzhi_page_cache[page_id]

    def get_yazhi_page(self, page_id: str) -> str:
        if page_id not in self._yazhi_page_cache:
            url = f"https://odds.500star.com/fenxi/yazhi-{page_id}.shtml"
            self._yazhi_page_cache[page_id] = self.fetch_text(url, encoding="gb18030")
        return self._yazhi_page_cache[page_id]

    def page_matches_teams(self, page_id: str, home_team: str, away_team: str) -> bool:
        try:
            page = self.get_ouzhi_page(page_id)
            title_match = re.search(r"<title>(.*?)VS(.*?)\(", page, re.S)
            if not title_match:
                return False
            page_home = self.normalize_team_name(title_match.group(1).strip())
            page_away = self.normalize_team_name(title_match.group(2).strip())
            expected_home = self.normalize_team_name(home_team)
            expected_away = self.normalize_team_name(away_team)
            return page_home == expected_home and page_away == expected_away
        except Exception:
            return False

    def _extract_candidate_page_ids(self, search_page: str) -> List[str]:
        """从搜索结果中尽可能提取 500 page_id（支持欧赔/亚盘两类入口、以及 uddg 解码）。"""
        ids = set()

        patterns = [
            r"(?:ouzhi|yazhi)(?:%2D|-)(\d+)\.shtml",          # urlencoded or hyphen
            r"(?:ouzhi|yazhi)-(\d+)\.shtml",                 # direct
            r"fenxi/(?:ouzhi|yazhi)-(\d+)\.shtml",           # path form
        ]
        for pat in patterns:
            for pid in re.findall(pat, search_page):
                ids.add(pid)

        # DuckDuckGo html 结果常把真实链接编码在 uddg= 参数里
        for enc in re.findall(r"uddg=([^&]+)", search_page):
            try:
                decoded = urllib.parse.unquote(enc)
            except Exception:
                decoded = enc
            for pat in patterns:
                for pid in re.findall(pat, decoded):
                    ids.add(pid)

        return list(ids)

    def search_page_id(self, home_team: str, away_team: str) -> str:
        cache_key = (home_team, away_team)
        if cache_key in self._page_id_cache:
            return self._page_id_cache[cache_key]

        homes = [home_team, TEAM_ALIASES.get(home_team, home_team)]
        aways = [away_team, TEAM_ALIASES.get(away_team, away_team)]
        tried = set()

        for home in homes:
            for away in aways:
                # 备用检索关键词：覆盖“欧赔/亚盘/指数/分析/500star/500.com”等场景
                query_templates = [
                    "{h} {a} 500 亚盘",
                    "{h} {a} 500 欧赔",
                    "{h} {a} 500 指数",
                    "{h} {a} 500 分析",
                    "{h}vs{a} 500 亚盘",
                    "{h}vs{a} 500 欧赔",
                    "{h} 对 {a} 500 欧赔",
                    "{h} {a} odds.500star.com fenxi",
                    "{h} {a} odds.500.com fenxi",
                ]

                for query in (tpl.format(h=home, a=away) for tpl in query_templates):
                    if query in tried:
                        continue
                    tried.add(query)
                    search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
                    try:
                        search_page = self.fetch_text(search_url, retries=3, timeout=40)
                    except Exception:
                        continue
                    candidates = self._extract_candidate_page_ids(search_page)
                    # 候选较多时，先走一遍对阵校验挑出正确的
                    for pid in candidates:
                        if self.page_matches_teams(pid, home_team, away_team):
                            self._page_id_cache[cache_key] = pid
                            return pid
                    time.sleep(0.2)

        # 最后兜底：尝试交换主客（有些站点会用相反的顺序展示）
        swap_key = (away_team, home_team)
        if swap_key in self._page_id_cache:
            return self._page_id_cache[swap_key]
        raise RuntimeError(f"未找到 500 页 ID: {home_team} vs {away_team}")

    def _float_or_none(self, value: str) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    def parse_ouzhi(self, page_id: str) -> Dict:
        url = f"https://odds.500star.com/fenxi/ouzhi-{page_id}.shtml"
        page = self.get_ouzhi_page(page_id)
        root = html.fromstring(page)

        # 取“竞彩官方”行（若没有则取第一行）来作为胜平负赔率
        bookmaker_rows = root.xpath("//tr[@xls='row']")
        official_row = None
        for tr in bookmaker_rows:
            company = "".join(tr.xpath(".//td[contains(@class,'tb_plgs')]/@title")).strip()
            if "竞" in company:
                official_row = tr
                break
        if official_row is None and bookmaker_rows:
            official_row = bookmaker_rows[0]

        def parse_triplet(tr_node, row_idx: int) -> Dict[str, Optional[float]]:
            values = [
                self._float_or_none(v.strip())
                for v in tr_node.xpath(f"./td[3]//tr[{row_idx}]/td/text()")
                if v.strip()
            ]
            while len(values) < 3:
                values.append(None)
            return {"home": values[0], "draw": values[1], "away": values[2]}

        def id_triplet(ids: List[str]) -> Dict[str, Optional[float]]:
            vals = [self._float_or_none(root.xpath(f"string(//td[@id='{i}'])").strip()) for i in ids]
            return {"home": vals[0], "draw": vals[1], "away": vals[2]}

        official_final = parse_triplet(official_row, 1) if official_row is not None else {"home": None, "draw": None, "away": None}
        official_initial = parse_triplet(official_row, 2) if official_row is not None else {"home": None, "draw": None, "away": None}

        return {
            "source_url": url,
            "page_id": page_id,
            "胜平负赔率": {"initial": official_initial, "final": official_final},
            "欧赔": {
                "initial": id_triplet(["avwinj2", "avdrawj2", "avlostj2"]),
                "final": id_triplet(["avwinc2", "avdrawc2", "avlostc2"]),
            },
            "凯利": {
                "initial": id_triplet(["avklwj2", "avkldj2", "avkllj2"]),
                "final": id_triplet(["avklwc2", "avkldc2", "avkllc2"]),
            },
            "离散率": {
                "initial": id_triplet(["lswj2", "lsdj2", "lslj2"]),
                "final": id_triplet(["lswc2", "lsdc2", "lslc2"]),
            },
        }

    def parse_yazhi(self, page_id: str) -> Dict:
        url = f"https://odds.500star.com/fenxi/yazhi-{page_id}.shtml"
        try:
            page = self.get_yazhi_page(page_id)
        except Exception:
            return {"source_url": url, "page_id": page_id, "亚值": {"initial": {}, "final": {}}}
        root = html.fromstring(page)
        footer_rows = root.xpath("//tr[@xls='footer']")
        if not footer_rows:
            return {"source_url": url, "page_id": page_id, "亚值": {"initial": {}, "final": {}}}

        avg_row = footer_rows[0]

        def parse_asian_table(td_index: int) -> Dict:
            values = [v.strip() for v in avg_row.xpath(f"./td[{td_index}]//text()") if v.strip()]
            while len(values) < 3:
                values.append("")
            # 500 的 footer 平均值里盘口直接给数值（如 -0.235）
            handicap_value = self._float_or_none(values[1])
            return {
                "home_water": self._float_or_none(values[0]),
                "handicap_text": values[1],
                "handicap_value": handicap_value,
                "away_water": self._float_or_none(values[2]),
            }

        return {
            "source_url": url,
            "page_id": page_id,
            "亚值": {
                "final": parse_asian_table(3),
                "initial": parse_asian_table(5),
            },
        }

    def build_match_record(self, fixture: MatchFixture, captured_at: str) -> Dict:
        # 优先走 500 联赛轮次 API（比搜索引擎稳定，且能覆盖未来赛程）
        pid = self._find_page_id_via_500_round_api(fixture)
        if not pid:
            pid = self.search_page_id(fixture.home_team, fixture.away_team)
        ouzhi = self.parse_ouzhi(pid)
        yazhi = self.parse_yazhi(pid)

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
            "page_id": pid,
            "captured_at": captured_at,
            "sources": {
                "fixture": f"https://tzuqiu.cc/competitions/{fixture.competition_id}/fixture.do",
                "europe_odds": ouzhi["source_url"],
                "asian_odds": yazhi["source_url"],
            },
            "胜平负赔率": ouzhi["胜平负赔率"],
            "欧赔": ouzhi["欧赔"],
            "亚值": yazhi["亚值"],
            "凯利": ouzhi["凯利"],
            "离散率": ouzhi["离散率"],
        }

    def flatten_record(self, record: Dict) -> Dict:
        def pick(group: str, phase: str, key: str):
            return record.get(group, {}).get(phase, {}).get(key)

        def pick_asian(phase: str, key: str):
            return record.get("亚值", {}).get(phase, {}).get(key)

        return {
            "match_id": record["match_id"],
            "match_date": record["match_date"],
            "match_time": record.get("match_time", ""),
            "home_team": record["home_team"],
            "away_team": record["away_team"],
            "page_id": record.get("page_id", ""),
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
            fixtures = self.fetch_league_matches(league_code, start, end)
            records = []
            for fixture in fixtures:
                try:
                    records.append(self.build_match_record(fixture, captured_at))
                except Exception as e:
                    print(f"  [WARN] skip {fixture.match_date} {fixture.home_team} vs {fixture.away_team}: {e}")
                time.sleep(0.3)
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
