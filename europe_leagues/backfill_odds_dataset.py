#!/usr/bin/env python3
"""
回填 2026-04-18 至 2026-04-20 五大联赛赔率数据。

输出位置:
- <league>/analysis/odds/2026-04-18_2026-04-20_odds.json
- <league>/analysis/odds/2026-04-18_2026-04-20_odds.csv
"""

import csv
import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests
from lxml import html


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

TEAM_ALIASES = {
    "纽卡斯尔": "纽卡斯尔联",
    "热刺": "托特纳姆热刺",
    "布莱顿": "布赖顿",
    "曼联": "曼彻斯特联",
    "拜仁": "拜仁慕尼黑",
    "不莱梅": "云达不莱梅",
    "柏林联盟": "柏林联合",
    "门兴": "门兴格拉德巴赫",
    "弗赖堡": "弗莱堡",
    "巴黎圣曼": "巴黎圣日耳曼",
    "斯特拉斯": "斯特拉斯堡",
    "萨索罗": "萨索洛",
}

KNOWN_PAGE_IDS = {
    ("布伦特福德", "富勒姆"): "1202678",
    ("纽卡斯尔", "伯恩茅斯"): "1202621",
    ("利兹联", "狼队"): "1202514",
    ("热刺", "布莱顿"): "1202687",
    ("切尔西", "曼联"): "1147688",
    ("埃弗顿", "利物浦"): "1202505",
    ("阿斯顿维拉", "桑德兰"): "1202691",
    ("诺丁汉森林", "伯恩利"): "1202526",
    ("曼城", "阿森纳"): "1202625",
    ("圣保利", "科隆"): "1206143",
    ("霍芬海姆", "多特蒙德"): "1206150",
    ("勒沃库森", "奥格斯堡"): "1206138",
    ("柏林联盟", "沃尔夫斯堡"): "1205923",
    ("不莱梅", "汉堡"): "1205944",
    ("法兰克福", "莱比锡红牛"): "1206147",
    ("弗赖堡", "海登海姆"): "1206171",
    ("拜仁", "斯图加特"): "1206163",
    ("门兴", "美因茨"): "1206172",
    ("萨索罗", "科莫"): "1199675",
    ("国际米兰", "卡利亚里"): "1199670",
    ("乌迪内斯", "帕尔马"): "1199676",
    ("那不勒斯", "拉齐奥"): "1199673",
    ("罗马", "亚特兰大"): "1199668",
    ("克雷莫内塞", "都灵"): "1199669",
    ("维罗纳", "AC米兰"): "1199677",
    ("比萨", "热那亚"): "1199674",
    ("尤文图斯", "博洛尼亚"): "1199671",
    ("朗斯", "图卢兹"): "1205878",
    ("洛里昂", "马赛"): "1205791",
    ("昂热", "勒阿弗尔"): "1205761",
    ("里尔", "尼斯"): "1205799",
    ("摩纳哥", "欧塞尔"): "1205800",
    ("斯特拉斯堡", "雷恩"): "1205832",
    ("梅斯", "巴黎FC"): "1205847",
    ("南特", "布雷斯特"): "1205840",
    ("巴黎圣日耳曼", "里昂"): "1205635",
}

HANDICAP_MAP = {
    "平手": 0.0,
    "平/半": 0.25,
    "平手/半球": 0.25,
    "半球": 0.5,
    "半/一": 0.75,
    "半球/一球": 0.75,
    "一球": 1.0,
    "一/球半": 1.25,
    "一球/球半": 1.25,
    "球半": 1.5,
    "球半/两球": 1.75,
    "两球": 2.0,
    "两/两球半": 2.25,
    "两球/两球半": 2.25,
    "两球半": 2.5,
    "两球半/三球": 2.75,
    "三球": 3.0,
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
        self._ouzhi_page_cache: Dict[str, str] = {}
        self._yazhi_page_cache: Dict[str, str] = {}

    def fetch_text(self, url: str, encoding: Optional[str] = None, retries: int = 5) -> str:
        last_error = None
        for attempt in range(retries):
            try:
                response = self.session.get(url, timeout=25)
                response.raise_for_status()
                if encoding:
                    return response.content.decode(encoding, "ignore")
                return response.text
            except Exception as exc:
                last_error = exc
                time.sleep(1 + attempt)
        raise RuntimeError(f"请求失败: {url} ({last_error})")

    def fetch_league_matches(self, league_code: str) -> List[MatchFixture]:
        config = LEAGUE_CONFIG[league_code]
        url = f"https://tzuqiu.cc/competitions/{config['competition_id']}/fixture.do"
        page_text = self.fetch_text(url)
        raw_matches = re.findall(
            r'(\{"matchDate":"2026-04-(?:18|19|20)".*?"competitionTeamType":"club"\})',
            page_text,
        )

        fixtures: List[MatchFixture] = []
        for item in raw_matches:
            data = json.loads(item)
            fixtures.append(
                MatchFixture(
                    league_code=league_code,
                    league_name=config["name"],
                    competition_id=config["competition_id"],
                    match_date=data["matchDate"],
                    match_time=data["startHMStr"],
                    round_name=data["stageName"],
                    home_team=data["homeTeamName"],
                    away_team=data["awayTeamName"],
                    score=data.get("score", ""),
                    is_finish=bool(data.get("isFinish")),
                    source_match_id=int(data["id"]),
                )
            )
        return fixtures

    def search_page_id(self, home_team: str, away_team: str) -> str:
        known = KNOWN_PAGE_IDS.get((home_team, away_team))
        if known:
            return known

        homes = [home_team, TEAM_ALIASES.get(home_team, home_team)]
        aways = [away_team, TEAM_ALIASES.get(away_team, away_team)]
        tried = set()

        for home in homes:
            for away in aways:
                for query in (
                    f"{home} {away} 500 亚盘",
                    f"{home}vs{away} 500 亚盘",
                    f"{home} {away} 500 亚盘对比",
                    f"{home} {away} 亚盘对比 500彩票网",
                ):
                    if query in tried:
                        continue
                    tried.add(query)
                    search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
                    try:
                        search_page = self.fetch_text(search_url, retries=3)
                    except Exception:
                        continue
                    candidates = re.findall(
                        r"yazhi(?:%2D|-)(\d+)\.shtml",
                        search_page,
                    )
                    for page_id in candidates:
                        if self.page_matches_teams(page_id, home_team, away_team):
                            return page_id
                    time.sleep(0.2)
        raise RuntimeError(f"未找到 500 页 ID: {home_team} vs {away_team}")

    def normalize_team_name(self, name: str) -> str:
        normalized = TEAM_ALIASES.get(name, name)
        normalized = normalized.replace("FC", "").replace(" ", "")
        return normalized

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

    def parse_handicap_value(self, handicap_text: str) -> Optional[float]:
        text = handicap_text.replace("*", "").replace(" ", "")
        negative = text.startswith("受")
        if negative:
            text = text[1:]
        value = HANDICAP_MAP.get(text)
        if value is None:
            return None
        return -value if negative else value

    def _float_or_none(self, value: str) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    def parse_ouzhi(self, page_id: str) -> Dict:
        url = f"https://odds.500star.com/fenxi/ouzhi-{page_id}.shtml"
        page = self.get_ouzhi_page(page_id)
        root = html.fromstring(page)

        title = root.xpath("string(//title)").strip()
        title_match = re.search(r"^(.*?)VS(.*?)\(", title)
        round_match = re.search(r"\((.*?)\)", title)
        home_team_500 = title_match.group(1).strip() if title_match else ""
        away_team_500 = title_match.group(2).strip() if title_match else ""
        round_name_from_title = round_match.group(1).strip() if round_match else ""

        match_time = root.xpath("string(//p[@class='game_time'])").strip().replace("比赛时间", "")
        actual_score = root.xpath("string(//p[@class='odds_hd_bf']/strong)").strip()
        round_name = round_name_from_title or root.xpath("string(//a[contains(@class, 'hd_name')])").strip()

        bookmaker_rows = root.xpath("//tr[@xls='row']")
        bookmaker_count = len(bookmaker_rows)

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

        official_company = "".join(
            official_row.xpath(".//td[contains(@class,'tb_plgs')]/@title")
        ).strip()

        official_final = parse_triplet(official_row, 1)
        official_initial = parse_triplet(official_row, 2)

        def id_triplet(ids: List[str]) -> Dict[str, Optional[float]]:
            vals = [self._float_or_none(root.xpath(f"string(//td[@id='{i}'])").strip()) for i in ids]
            return {"home": vals[0], "draw": vals[1], "away": vals[2]}

        return {
            "source_url": url,
            "page_id": page_id,
            "match_time_500": match_time,
            "round_name_500": round_name,
            "home_team_500": home_team_500,
            "away_team_500": away_team_500,
            "actual_score_500": actual_score,
            "bookmaker_count": bookmaker_count,
            "official_company": official_company,
            "official_odds": {
                "initial": official_initial,
                "final": official_final,
            },
            "europe_odds_avg": {
                "initial": id_triplet(["avwinj2", "avdrawj2", "avlostj2"]),
                "final": id_triplet(["avwinc2", "avdrawc2", "avlostc2"]),
            },
            "kelly_avg": {
                "initial": id_triplet(["avklwj2", "avkldj2", "avkllj2"]),
                "final": id_triplet(["avklwc2", "avkldc2", "avkllc2"]),
            },
            "dispersion_avg": {
                "initial": id_triplet(["lswj2", "lsdj2", "lslj2"]),
                "final": id_triplet(["lswc2", "lsdc2", "lslc2"]),
            },
        }

    def parse_yazhi(self, page_id: str) -> Dict:
        url = f"https://odds.500star.com/fenxi/yazhi-{page_id}.shtml"
        page = self.get_yazhi_page(page_id)
        root = html.fromstring(page)

        footer_rows = root.xpath("//tr[@xls='footer']")
        if not footer_rows:
            raise RuntimeError(f"亚盘页缺少 footer: {url}")

        avg_row = footer_rows[0]

        def parse_asian_table(td_index: int) -> Dict:
            values = [v.strip() for v in avg_row.xpath(f"./td[{td_index}]//text()") if v.strip()]
            while len(values) < 3:
                values.append("")
            handicap_text = values[1]
            return {
                "home_water": self._float_or_none(values[0]),
                "handicap_text": handicap_text,
                "handicap_value": self._float_or_none(handicap_text),
                "away_water": self._float_or_none(values[2]),
            }

        bookmaker_count_match = re.search(r"共\s*(\d+)\s*家公司", page)

        return {
            "source_url": url,
            "page_id": page_id,
            "bookmaker_count": int(bookmaker_count_match.group(1)) if bookmaker_count_match else None,
            "asian_odds_avg": {
                "final": parse_asian_table(3),
                "initial": parse_asian_table(5),
            },
        }

    def build_match_record(self, fixture: MatchFixture) -> Dict:
        page_id = self.search_page_id(fixture.home_team, fixture.away_team)
        ouzhi = self.parse_ouzhi(page_id)
        yazhi = self.parse_yazhi(page_id)

        actual_score = ouzhi["actual_score_500"] or fixture.score
        home_score = None
        away_score = None
        match = re.match(r"(\d+)\s*[:：-]\s*(\d+)", actual_score)
        if match:
            home_score = int(match.group(1))
            away_score = int(match.group(2))

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
            "actual_score": actual_score,
            "actual_result": result_text,
            "home_score": home_score,
            "away_score": away_score,
            "page_id": page_id,
            "sources": {
                "fixture": f"https://tzuqiu.cc/competitions/{fixture.competition_id}/fixture.do",
                "europe_odds": ouzhi["source_url"],
                "asian_odds": yazhi["source_url"],
            },
            "胜平负赔率": ouzhi["official_odds"],
            "欧赔": ouzhi["europe_odds_avg"],
            "亚值": yazhi["asian_odds_avg"],
            "凯利": ouzhi["kelly_avg"],
            "离散率": ouzhi["dispersion_avg"],
            "metadata": {
                "official_company": ouzhi["official_company"],
                "bookmaker_count_europe": ouzhi["bookmaker_count"],
                "bookmaker_count_asian": yazhi["bookmaker_count"],
                "match_time_500": ouzhi["match_time_500"],
                "round_name_500": ouzhi["round_name_500"],
                "home_team_500": ouzhi["home_team_500"],
                "away_team_500": ouzhi["away_team_500"],
                "source_match_id_tzuqiu": fixture.source_match_id,
            },
        }

    def flatten_record(self, record: Dict) -> Dict:
        def pick(group: str, phase: str, key: str) -> Optional[float]:
            return record[group][phase].get(key)

        def pick_asian(phase: str, key: str):
            return record["亚值"][phase].get(key)

        return {
            "match_id": record["match_id"],
            "league_code": record["league_code"],
            "league_name": record["league_name"],
            "round_name": record["round_name"],
            "match_date": record["match_date"],
            "match_time": record["match_time"],
            "home_team": record["home_team"],
            "away_team": record["away_team"],
            "actual_score": record["actual_score"],
            "actual_result": record["actual_result"],
            "page_id": record["page_id"],
            "胜平负_初始_主": pick("胜平负赔率", "initial", "home"),
            "胜平负_初始_平": pick("胜平负赔率", "initial", "draw"),
            "胜平负_初始_客": pick("胜平负赔率", "initial", "away"),
            "胜平负_最终_主": pick("胜平负赔率", "final", "home"),
            "胜平负_最终_平": pick("胜平负赔率", "final", "draw"),
            "胜平负_最终_客": pick("胜平负赔率", "final", "away"),
            "欧赔_初始_主": pick("欧赔", "initial", "home"),
            "欧赔_初始_平": pick("欧赔", "initial", "draw"),
            "欧赔_初始_客": pick("欧赔", "initial", "away"),
            "欧赔_最终_主": pick("欧赔", "final", "home"),
            "欧赔_最终_平": pick("欧赔", "final", "draw"),
            "欧赔_最终_客": pick("欧赔", "final", "away"),
            "亚值_初始_主水": pick_asian("initial", "home_water"),
            "亚值_初始_盘口": pick_asian("initial", "handicap_text"),
            "亚值_初始_盘口值": pick_asian("initial", "handicap_value"),
            "亚值_初始_客水": pick_asian("initial", "away_water"),
            "亚值_最终_主水": pick_asian("final", "home_water"),
            "亚值_最终_盘口": pick_asian("final", "handicap_text"),
            "亚值_最终_盘口值": pick_asian("final", "handicap_value"),
            "亚值_最终_客水": pick_asian("final", "away_water"),
            "凯利_初始_主": pick("凯利", "initial", "home"),
            "凯利_初始_平": pick("凯利", "initial", "draw"),
            "凯利_初始_客": pick("凯利", "initial", "away"),
            "凯利_最终_主": pick("凯利", "final", "home"),
            "凯利_最终_平": pick("凯利", "final", "draw"),
            "凯利_最终_客": pick("凯利", "final", "away"),
            "离散率_初始_主": pick("离散率", "initial", "home"),
            "离散率_初始_平": pick("离散率", "initial", "draw"),
            "离散率_初始_客": pick("离散率", "initial", "away"),
            "离散率_最终_主": pick("离散率", "final", "home"),
            "离散率_最终_平": pick("离散率", "final", "draw"),
            "离散率_最终_客": pick("离散率", "final", "away"),
        }

    def write_outputs(self, league_code: str, records: List[Dict]) -> None:
        output_dir = BASE_DIR / league_code / "analysis" / "odds"
        output_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            "league_code": league_code,
            "league_name": LEAGUE_CONFIG[league_code]["name"],
            "date_range": {"start": DATE_START, "end": DATE_END},
            "match_count": len(records),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "matches": records,
        }

        json_path = output_dir / f"{DATE_TAG}_odds.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        csv_rows = [self.flatten_record(record) for record in records]
        csv_path = output_dir / f"{DATE_TAG}_odds.csv"
        fieldnames = list(csv_rows[0].keys()) if csv_rows else [
            "match_id",
            "league_code",
            "league_name",
            "round_name",
            "match_date",
            "match_time",
            "home_team",
            "away_team",
            "actual_score",
            "actual_result",
            "page_id",
        ]
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

    def run(self) -> None:
        for league_code in LEAGUE_CONFIG:
            print(f"[INFO] 处理 {league_code} ...")
            fixtures = self.fetch_league_matches(league_code)
            records = []
            for fixture in fixtures:
                print(
                    f"  - {fixture.match_date} {fixture.home_team} vs {fixture.away_team}",
                    flush=True,
                )
                try:
                    records.append(self.build_match_record(fixture))
                except Exception as exc:
                    print(f"    [WARN] 跳过: {exc}", flush=True)
                time.sleep(0.3)
            self.write_outputs(league_code, records)
            print(f"[INFO] {league_code} 完成，共 {len(records)} 场")


def main() -> None:
    OddsBackfill().run()


if __name__ == "__main__":
    main()
