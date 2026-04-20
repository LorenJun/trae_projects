#!/usr/bin/env python3
"""
Generate `premier_league_players_2026.csv` from the official Premier League
2025/26 squad-lists article.

Data fidelity:
- Source: official squad list article (first-hand roster names)
- Includes: first-team squad players + registered U21 players
- Does NOT invent unavailable fields; unknown columns stay empty
"""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Dict, List

import requests


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = BASE_DIR / "premier_league_players_2026.csv"
ARTICLE_URL = "https://www.premierleague.com/en/news/4580687/202526-premier-league-squad-lists"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

TEAM_MAP = {
    "AFC Bournemouth": "伯恩茅斯",
    "Arsenal": "阿森纳",
    "Aston Villa": "阿斯顿维拉",
    "Brentford": "布伦特福德",
    "Brighton & Hove Albion": "布莱顿",
    "Burnley": "伯恩利",
    "Chelsea": "切尔西",
    "Crystal Palace": "水晶宫",
    "Everton": "埃弗顿",
    "Fulham": "富勒姆",
    "Leeds United": "利兹联",
    "Liverpool": "利物浦",
    "Manchester City": "曼城",
    "Manchester United": "曼联",
    "Newcastle United": "纽卡斯尔联",
    "Nottingham Forest": "诺丁汉森林",
    "Sunderland": "桑德兰",
    "Tottenham Hotspur": "热刺",
    "West Ham United": "西汉姆联",
    "Wolverhampton Wanderers": "狼队",
}

CSV_COLUMNS = [
    "league_code",
    "team",
    "player_name",
    "player_name_cn",
    "position",
    "age",
    "nationality",
    "market_value",
    "transfer_status",
    "injury_reason",
    "expected_return",
    "appearances",
    "goals",
    "assists",
    "yellow_cards",
    "red_cards",
]


def fetch_html() -> str:
    resp = requests.get(ARTICLE_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.text


def clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("\xa0", " ")
    return value.strip()


def split_players(block_html: str) -> List[str]:
    # Normalize BR tags then split.
    block_html = re.sub(r"<br\s*/?>", "\n", block_html, flags=re.I)
    text = clean_text(block_html)
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    players: List[str] = []
    for line in lines:
        # Drop section labels.
        if line.startswith("25 Squad players") or line.startswith("U21 players"):
            continue
        # Strip numeric prefix and trailing home-grown marker.
        line = re.sub(r"^\d+\s+", "", line)
        line = re.sub(r"\*$", "", line).strip()
        if line:
            players.append(line)
    return players


def extract_team_sections(page_html: str) -> Dict[str, Dict[str, List[str]]]:
    """
    Returns:
      {
        'Arsenal': {'squad': [...], 'u21': [...]},
        ...
      }
    """
    results: Dict[str, Dict[str, List[str]]] = {}

    # Use a two-step parse instead of one giant regex so it tolerates minor
    # markup differences such as `<br />` placement inside `<strong>`.
    heading_pattern = re.compile(r"<h5>\s*<a[^>]*>([^<]+)</a>\s*</h5>", re.I)
    headings = list(heading_pattern.finditer(page_html))
    for i, m in enumerate(headings):
        club = clean_text(m.group(1))
        if club not in TEAM_MAP:
            continue
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(page_html)
        section_html = page_html[start:end]
        p_blocks = re.findall(r"<p>(.*?)</p>", section_html, flags=re.S | re.I)
        if len(p_blocks) < 2 and p_blocks and "U21 players" in p_blocks[0]:
            first = p_blocks[0]
            split_match = re.search(r"(<strong>U21 players.*)", first, flags=re.S | re.I)
            if split_match:
                idx = split_match.start(1)
                p_blocks = [first[:idx], first[idx:]]
        if len(p_blocks) < 2:
            continue
        squad = split_players(p_blocks[0])
        u21 = split_players(p_blocks[1])
        results[club] = {"squad": squad, "u21": u21}
    return results


def write_csv(team_sections: Dict[str, Dict[str, List[str]]]) -> int:
    row_count = 0
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for club_en, team_cn in TEAM_MAP.items():
            sections = team_sections.get(club_en, {"squad": [], "u21": []})
            for player_name in sections["squad"] + sections["u21"]:
                writer.writerow(
                    {
                        "league_code": "premier_league",
                        "team": team_cn,
                        "player_name": player_name,
                        "player_name_cn": "",
                        "position": "",
                        "age": "",
                        "nationality": "",
                        "market_value": "",
                        "transfer_status": "current",
                        "injury_reason": "",
                        "expected_return": "",
                        "appearances": "",
                        "goals": "",
                        "assists": "",
                        "yellow_cards": "",
                        "red_cards": "",
                    }
                )
                row_count += 1
    return row_count


def main() -> int:
    page_html = fetch_html()
    team_sections = extract_team_sections(page_html)
    if len(team_sections) != 20:
        print(f"warning: parsed teams={len(team_sections)}")
    row_count = write_csv(team_sections)
    print(f"generated: {OUTPUT_CSV}")
    print(f"teams: {len(team_sections)}")
    print(f"rows: {row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
