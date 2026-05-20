#!/usr/bin/env python3
"""Refresh five-league schedule dates/times in teams_2025-26.md from 7m fixture data."""

from __future__ import annotations

import argparse
import ast
import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent

LEAGUE_CONFIG = {
    "premier_league": {
        "label": "英超",
        "source_url": "https://data.7m.com.cn/matches_data/92/gb/index.shtml",
        "file_path": BASE_DIR / "premier_league" / "teams_2025-26.md",
        "season_start_year": 2025,
    },
    "la_liga": {
        "label": "西甲",
        "source_url": "https://data.7m.com.cn/matches_data/85/gb/index.shtml",
        "file_path": BASE_DIR / "la_liga" / "teams_2025-26.md",
        "season_start_year": 2025,
        "fallback_url": "https://m.qiumiwu.com/game/xijia",
    },
    "serie_a": {
        "label": "意甲",
        "source_url": "https://data.7m.com.cn/matches_data/34/gb/index.shtml",
        "file_path": BASE_DIR / "serie_a" / "teams_2025-26.md",
        "season_start_year": 2025,
        "fallback_url": "https://m.qiumiwu.com/game/yijia",
    },
    "bundesliga": {
        "label": "德甲",
        "source_url": "https://data.7m.com.cn/matches_data/39/gb/index.shtml",
        "file_path": BASE_DIR / "bundesliga" / "teams_2025-26.md",
        "season_start_year": 2025,
    },
    "ligue_1": {
        "label": "法甲",
        "source_url": "https://data.7m.com.cn/matches_data/93/gb/index.shtml",
        "file_path": BASE_DIR / "ligue_1" / "teams_2025-26.md",
        "season_start_year": 2025,
    },
}


TEAM_ALIASES = {
    "曼彻斯特联": "曼联",
    "曼彻斯特城": "曼城",
    "托特纳姆热刺": "热刺",
    "托特纳姆": "热刺",
    "瓦伦西亚": "瓦伦西亚",
    "巴伦西亚": "瓦伦西亚",
    "马洛卡": "马略卡",
    "皇家奥维多": "皇家奥维耶多",
    "奥维耶多": "皇家奥维耶多",
    "赫塔菲": "赫塔费",
    "马竞": "马德里竞技",
    "国际米兰": "国际米兰",
    "莱比锡RB": "莱比锡红牛",
    "RB莱比锡": "莱比锡红牛",
    "门兴": "门兴格拉德巴赫",
    "圣日门": "圣日耳曼",
    "巴黎圣日门": "巴黎圣日耳曼",
    "勒哈费尔": "勒阿弗尔",
    "勒哈費爾": "勒阿弗尔",
    "马竞": "马德里竞技",
    "巴萨": "巴塞罗那",
    "皇马": "皇家马德里",
    "贝蒂斯": "皇家贝蒂斯",
    "比利亚雷": "比利亚雷亚尔",
    "毕尔巴鄂": "毕尔巴鄂竞技",
    "奥维耶多": "皇家奥维耶多",
    "斯特拉斯堡": "斯特拉斯堡",
    "尼斯": "尼斯",
    "欧塞尔": "欧塞尔",
}


ROUND_HEADER_RE = re.compile(r"^### 第(\d+)轮(?:（收官轮）)?\s*$")
TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$")
HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
HTML_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})$")
SCORE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
ARRAY_RE_TEMPLATE = r"var\s+{name}\s*=\s*\[(.*?)\];"
QIUMIUWU_DAY_RE = re.compile(
    r'<div class="fixture__details__header"><span>(?:[^0-9<]*\s+)?(\d{2})-(\d{2})[^<]*</span>',
    re.S,
)
QIUMIUWU_MATCH_RE = re.compile(
    r'<div[^>]*class="fixture__list"[^>]*>.*?'
    r'<div class="fixture__list__header"><span>(\d{2}:\d{2})</span>.*?<span>第(\d+)轮 联赛</span></div>.*?'
    r'<a class="fixture__list__info"[^>]*>.*?<div class="fixture__list__team"><span>([^<]+)</span>\s*</div>.*?'
    r'<div class="fixture__list__score">(.*?)</div>.*?'
    r'<div class="fixture__list__team"><span>([^<]+)</span>\s*</div>.*?</a>.*?</div>',
    re.S,
)


@dataclass
class SourceMatch:
    round_no: int
    date: str
    time: str
    home_team: str
    away_team: str
    score: str
    finished: bool


@dataclass
class MarkdownRow:
    date: str
    time: str
    home_team: str
    score: str
    away_team: str
    remark: str


def normalize_team_name(name: str) -> str:
    normalized = SPACE_RE.sub("", str(name or "").strip())
    return TEAM_ALIASES.get(normalized, normalized)


def fetch_text(url: str) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        },
        timeout=30,
    )
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


def clean_html_text(raw: str) -> str:
    text = html.unescape(TAG_RE.sub("", raw or ""))
    text = text.replace("\xa0", " ").replace("&#160;", " ")
    text = SPACE_RE.sub(" ", text).strip()
    return text


def parse_js_array(js_text: str, name: str) -> List:
    match = re.search(ARRAY_RE_TEMPLATE.format(name=re.escape(name)), js_text, re.S)
    if not match:
        raise ValueError(f"fixture.js 中缺少数组: {name}")
    body = "[" + match.group(1).strip() + "]"
    return ast.literal_eval(body)


def parse_time_value(raw_value: str) -> Tuple[str, str]:
    parts = [part.strip() for part in str(raw_value).split(",")]
    if len(parts) < 5:
        raise ValueError(f"无法解析开球时间: {raw_value}")
    year, month, day, hour, minute = [int(part) for part in parts[:5]]
    return f"{year:04d}-{month:02d}-{day:02d}", f"{hour:02d}:{minute:02d}"


def _parse_schedule_datetime(date_text: str, time_text: str) -> Tuple[Optional[datetime], bool]:
    normalized_date = str(date_text or "").strip()
    normalized_time = str(time_text or "").strip()
    if not normalized_date:
        return None, False
    if normalized_time:
        try:
            return datetime.strptime(f"{normalized_date} {normalized_time}", "%Y-%m-%d %H:%M"), True
        except Exception:
            pass
    try:
        return datetime.strptime(normalized_date, "%Y-%m-%d"), False
    except Exception:
        return None, False


def choose_later_schedule(
    existing_date: str,
    existing_time: str,
    fetched_date: str,
    fetched_time: str,
) -> Tuple[str, str]:
    existing_dt, existing_has_time = _parse_schedule_datetime(existing_date, existing_time)
    fetched_dt, fetched_has_time = _parse_schedule_datetime(fetched_date, fetched_time)
    if fetched_dt is None:
        return str(existing_date or "").strip(), str(existing_time or "").strip()
    if existing_dt is None:
        return str(fetched_date or "").strip(), str(fetched_time or "").strip()
    if fetched_dt > existing_dt:
        return str(fetched_date or "").strip(), str(fetched_time or "").strip()
    if fetched_dt < existing_dt:
        return str(existing_date or "").strip(), str(existing_time or "").strip()
    if fetched_has_time and not existing_has_time:
        return str(fetched_date or "").strip(), str(fetched_time or "").strip()
    return str(existing_date or "").strip(), str(existing_time or "").strip()


def parse_source_schedule(url: str) -> Dict[int, List[SourceMatch]]:
    fixture_url = url.replace("index.shtml", "fixture.js")
    fixture_text = fetch_text(fixture_url)
    run_arr = parse_js_array(fixture_text, "Run_Arr")
    time_arr = parse_js_array(fixture_text, "Time_Arr")
    score_arr = parse_js_array(fixture_text, "Scores_Arr")
    home_arr = parse_js_array(fixture_text, "TeamA_Arr")
    away_arr = parse_js_array(fixture_text, "TeamB_Arr")
    stat_arr = parse_js_array(fixture_text, "Stat_Arr")

    total = len(run_arr)
    if not all(len(arr) == total for arr in (time_arr, score_arr, home_arr, away_arr, stat_arr)):
        raise ValueError(f"{fixture_url} 数组长度不一致")

    rounds: Dict[int, List[SourceMatch]] = {}
    seen_keys = set()

    for round_value, time_value, score_value, home_value, away_value, stat_value in zip(
        run_arr, time_arr, score_arr, home_arr, away_arr, stat_arr
    ):
        round_no = int(round_value)
        date_value, time_text = parse_time_value(time_value)
        score_text = str(score_value).strip()
        score_match = SCORE_RE.search(score_text)
        finished = bool(score_match)
        score = f"{score_match.group(1)}-{score_match.group(2)}" if score_match else "-"
        home_team = normalize_team_name(home_value)
        away_team = normalize_team_name(away_value)
        unique_key = (round_no, home_team, away_team)
        if unique_key in seen_keys:
            continue
        seen_keys.add(unique_key)

        match = SourceMatch(
            round_no=round_no,
            date=date_value,
            time=time_text,
            home_team=home_team,
            away_team=away_team,
            score=score,
            finished=finished and int(stat_value) not in {1, 2, 3, 17},
        )
        rounds.setdefault(round_no, []).append(match)

    for round_matches in rounds.values():
        round_matches.sort(key=lambda item: (item.date, item.time, item.home_team, item.away_team))
    return rounds


def infer_season_year(month: int, season_start_year: int) -> int:
    return season_start_year if month >= 7 else season_start_year + 1


def parse_qiumiwu_recent_schedule(url: str, season_start_year: int) -> Dict[Tuple[int, str, str], Tuple[str, str]]:
    html_text = fetch_text(url)
    fallback_rows: Dict[Tuple[int, str, str], Tuple[str, str]] = {}

    day_matches = list(QIUMIUWU_DAY_RE.finditer(html_text))
    for index, day_match in enumerate(day_matches):
        month_text, day_text = day_match.groups()
        section_start = day_match.end()
        section_end = day_matches[index + 1].start() if index + 1 < len(day_matches) else len(html_text)
        section_html = html_text[section_start:section_end]
        month = int(month_text)
        day = int(day_text)
        year = infer_season_year(month, season_start_year)
        date_value = f"{year:04d}-{month:02d}-{day:02d}"

        for time_text, round_text, home_text, _score_html, away_text in QIUMIUWU_MATCH_RE.findall(section_html):
            home_team = normalize_team_name(home_text)
            away_team = normalize_team_name(away_text)
            round_no = int(round_text)
            key = (round_no, home_team, away_team)
            fallback_rows[key] = (date_value, time_text)

    return fallback_rows


def merge_fallback_schedule(
    rounds: Dict[int, List[SourceMatch]],
    fallback_rows: Dict[Tuple[int, str, str], Tuple[str, str]],
) -> Dict[int, List[SourceMatch]]:
    if not fallback_rows:
        return rounds

    merged: Dict[int, List[SourceMatch]] = {}
    for round_no, matches in rounds.items():
        merged_matches: List[SourceMatch] = []
        for match in matches:
            fallback = fallback_rows.get((round_no, match.home_team, match.away_team))
            if fallback:
                chosen_date, chosen_time = choose_later_schedule(
                    match.date,
                    match.time,
                    fallback[0],
                    fallback[1],
                )
                match = SourceMatch(
                    round_no=match.round_no,
                    date=chosen_date,
                    time=chosen_time,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    score=match.score,
                    finished=match.finished,
                )
            merged_matches.append(match)
        merged_matches.sort(key=lambda item: (item.date, item.time, item.home_team, item.away_team))
        merged[round_no] = merged_matches
    return merged


def parse_round_rows(lines: List[str], start_index: int, end_index: int) -> Dict[Tuple[str, str], MarkdownRow]:
    rows: Dict[Tuple[str, str], MarkdownRow] = {}
    for line in lines[start_index:end_index]:
        match = TABLE_ROW_RE.match(line.rstrip("\n"))
        if not match:
            continue
        date, time, home, score, away, remark = [item.strip() for item in match.groups()]
        if date == "日期" or set(date) == {"-"}:
            continue
        row = MarkdownRow(
            date=date,
            time=time,
            home_team=normalize_team_name(home),
            score=score,
            away_team=normalize_team_name(away),
            remark=remark,
        )
        rows[(row.home_team, row.away_team)] = row
    return rows


def build_round_table(source_rows: List[SourceMatch], existing_rows: Dict[Tuple[str, str], MarkdownRow]) -> List[str]:
    output = [
        "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |\n",
        "|-----|------|-----|------|-----|------|\n",
    ]
    for match in source_rows:
        existing = existing_rows.get((match.home_team, match.away_team))
        if match.finished:
            score = match.score
        else:
            score = "-"

        final_date, final_time = match.date, match.time
        if existing:
            final_date, final_time = choose_later_schedule(
                existing.date,
                existing.time,
                match.date,
                match.time,
            )

        remark = existing.remark if existing else ("已结束" if match.finished else "进行中")
        if match.finished and remark.startswith("进行中"):
            remark = remark.replace("进行中", "已结束", 1)
        if not match.finished and not remark:
            remark = "进行中"
        output.append(
            f"| {final_date} | {final_time} | {match.home_team} | {score} | {match.away_team} | {remark} |\n"
        )
    return output


def update_markdown_file(file_path: Path, source_rounds: Dict[int, List[SourceMatch]]) -> Dict[str, int]:
    original_lines = file_path.read_text(encoding="utf-8").splitlines(True)
    new_lines: List[str] = []
    updated_rounds = 0
    row_updates = 0
    i = 0

    while i < len(original_lines):
        line = original_lines[i]
        header_match = ROUND_HEADER_RE.match(line.strip())
        if not header_match:
            new_lines.append(line)
            i += 1
            continue

        round_no = int(header_match.group(1))
        start_of_block = i
        i += 1
        block_start = i
        while i < len(original_lines):
            stripped = original_lines[i].strip()
            if stripped.startswith("### ") or stripped.startswith("## "):
                break
            i += 1
        block_end = i

        if round_no not in source_rounds:
            new_lines.extend(original_lines[start_of_block:block_end])
            continue

        existing_rows = parse_round_rows(original_lines, block_start, block_end)
        new_lines.append(original_lines[start_of_block])
        new_lines.extend(build_round_table(source_rounds[round_no], existing_rows))
        if block_end < len(original_lines) and original_lines[block_end - 1].strip():
            new_lines.append("\n")
        updated_rounds += 1
        row_updates += len(source_rounds[round_no])

    file_path.write_text("".join(new_lines), encoding="utf-8")
    return {"updated_rounds": updated_rounds, "updated_rows": row_updates}


def refresh_league(league_code: str) -> Dict[str, int]:
    config = LEAGUE_CONFIG[league_code]
    source_rounds = parse_source_schedule(config["source_url"])
    fallback_url = config.get("fallback_url")
    if fallback_url:
        fallback_rows = parse_qiumiwu_recent_schedule(fallback_url, int(config["season_start_year"]))
        source_rounds = merge_fallback_schedule(source_rounds, fallback_rows)
    return update_markdown_file(config["file_path"], source_rounds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update five leagues schedule dates/times from 7m.")
    parser.add_argument(
        "--league",
        choices=sorted(LEAGUE_CONFIG.keys()),
        help="Only update one league.",
    )
    args = parser.parse_args()

    league_codes = [args.league] if args.league else list(LEAGUE_CONFIG.keys())
    for league_code in league_codes:
        result = refresh_league(league_code)
        label = LEAGUE_CONFIG[league_code]["label"]
        print(
            f"{league_code} ({label}): 更新 {result['updated_rounds']} 个轮次，"
            f"{result['updated_rows']} 场比赛"
        )


if __name__ == "__main__":
    main()
