#!/usr/bin/env python3
"""
Update league schedules in teams_2025-26.md using liansai.500.com round API.

User intent:
- Data source: 500 (stable round-based fixtures)
- Scope: update from "current rounds already listed in file" through season end
- Only modify the schedule section in teams_2025-26.md (keep other sections intact)

This script:
- Detects the schedule section (between "## 赛程信息" and next "## ")
- Keeps any preface inside schedule section before the first "### 第N轮" heading (e.g. cups, rescheduled notes)
- Rebuilds round blocks from the first round found through the league's final round
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backfill_odds_snapshots import OddsSnapshotBackfill, TEAM_ALIASES


BASE_DIR = Path(__file__).resolve().parent

LEAGUES: Dict[str, Dict[str, str]] = {
    "premier_league": {"name": "英超"},
    "la_liga": {"name": "西甲"},
    "bundesliga": {"name": "德甲"},
    "serie_a": {"name": "意甲"},
    "ligue_1": {"name": "法甲"},
}

FINAL_ROUND: Dict[str, int] = {
    "premier_league": 38,
    "la_liga": 38,
    "serie_a": 38,
    "bundesliga": 34,
    "ligue_1": 34,
}


def _norm_team(name: str) -> str:
    name = (name or "").strip()
    name = TEAM_ALIASES.get(name, name)
    return name.replace("FC", "").replace(" ", "")


@dataclass
class RoundMatch:
    date: str
    time: str
    home: str
    away: str
    score: str
    status_text: str


def _parse_stime(stime: str) -> Tuple[str, str]:
    # stime: "YYYY-MM-DD HH:MM"
    stime = (stime or "").strip()
    if not stime:
        return "", ""
    parts = stime.split()
    if len(parts) == 2:
        return parts[0], parts[1]
    # best effort
    if len(stime) >= 16 and stime[4] == "-" and stime[7] == "-" and stime[10] == " ":
        return stime[:10], stime[11:16]
    return stime[:10], stime[10:].strip()


def _status_text(status: Optional[int], hscore: Optional[int], gscore: Optional[int]) -> Tuple[str, str]:
    """
    Returns (score_str, remark_status_text).
    Observed from 500:
    - status=5: finished (scores present)
    - status=0: not started
    Other statuses may exist (in-play).
    """
    try:
        s = int(status) if status is not None else None
    except Exception:
        s = None
    # Rule to avoid misleading "0-0" for not-finished matches:
    # Only write an actual score for finished matches (status=5). Otherwise always use "-".
    finished = (s == 5)
    if finished and hscore is not None and gscore is not None:
        return f"{hscore}-{gscore}", "已结束"
    if s in (1, 2, 3, 4):
        return "-", "进行中"
    return "-", "待验证"


def fetch_round(backfill: OddsSnapshotBackfill, league_code: str, round_no: int) -> List[RoundMatch]:
    raw = backfill._fetch_round_matches_from_500(league_code, round_no)  # noqa: SLF001
    matches: List[RoundMatch] = []
    for m in raw:
        stime = m.get("stime") or ""
        d, t = _parse_stime(stime)
        home = m.get("hname") or m.get("hsxname") or ""
        away = m.get("gname") or m.get("gsxname") or ""
        hscore = m.get("hscore")
        gscore = m.get("gscore")
        score, status_txt = _status_text(m.get("status"), hscore, gscore)
        matches.append(
            RoundMatch(
                date=d,
                time=t,
                home=home,
                away=away,
                score=score,
                status_text=status_txt,
            )
        )
    # sort by datetime
    def _key(x: RoundMatch) -> str:
        return f"{x.date} {x.time}".strip()

    matches.sort(key=_key)
    return matches


# Accept headings like:
# - "### 第33轮"
# - "### 第38轮（收官轮）"
# - "### 西甲第33轮"
# - "### 第26轮补赛"
ROUND_HEADER_RE = re.compile(r"^###\s*(?:\S+)?第\s*(\d+)\s*轮")


def find_schedule_section(lines: List[str]) -> Optional[Tuple[int, int]]:
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## 赛程信息"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") and not lines[j].startswith("## 赛程信息"):
            end = j
            break
    return start, end


def find_first_round_in_section(lines: List[str], start: int, end: int) -> Optional[int]:
    for i in range(start, end):
        m = ROUND_HEADER_RE.match(lines[i])
        if m:
            return int(m.group(1))
    return None


def collect_existing_notes(lines: List[str], start: int, end: int) -> Dict[Tuple[str, str, str], str]:
    """
    Preserve user notes in the "备注" column when rewriting tables.
    Keyed by (date, normalized_home, normalized_away).
    """
    notes: Dict[Tuple[str, str, str], str] = {}
    row_re = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([0-9:]{4,5}|-)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$")
    for i in range(start, end):
        line = lines[i].rstrip("\n")
        m = row_re.match(line)
        if not m:
            continue
        d = m.group(1).strip()
        home = m.group(3).strip()
        away = m.group(5).strip()
        remark = m.group(6).strip()
        if not remark:
            continue
        # only keep non-trivial notes beyond default status labels
        # allow things like "已结束/争冠焦点战"
        notes[(d, _norm_team(home), _norm_team(away))] = remark
    return notes


def merge_remark(status_txt: str, existing: Optional[str]) -> str:
    if not existing:
        return status_txt
    existing = existing.strip()
    # If existing is just a default status label, prefer the fresh status from 500.
    if existing in ("已结束", "待验证", "进行中"):
        return status_txt

    # If existing is "已结束/xxx" style, keep the note part but refresh the status prefix.
    for prefix in ("已结束", "待验证", "进行中"):
        if existing.startswith(prefix + "/"):
            return status_txt + existing[len(prefix) :]

    # Otherwise, combine status + note.
    return f"{status_txt}/{existing}"


def build_round_block(round_no: int, matches: List[RoundMatch], final_round: int, notes: Dict[Tuple[str, str, str], str]) -> List[str]:
    header = f"### 第{round_no}轮"
    if round_no == final_round:
        header = f"### 第{round_no}轮（收官轮）"
    out = [header + "\n"]
    out.append("| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |\n")
    out.append("|-----|------|-----|------|-----|------|\n")
    for m in matches:
        key = (m.date, _norm_team(m.home), _norm_team(m.away))
        remark = merge_remark(m.status_text, notes.get(key))
        out.append(f"| {m.date} | {m.time or '-'} | {m.home} | {m.score} | {m.away} | {remark} |\n")
    out.append("\n")
    return out


def update_file(league_code: str, dry_run: bool) -> bool:
    teams_md = BASE_DIR / league_code / "teams_2025-26.md"
    if not teams_md.exists():
        return False

    lines = teams_md.read_text(encoding="utf-8").splitlines(keepends=True)
    sec = find_schedule_section(lines)
    if not sec:
        return False
    sec_start, sec_end = sec

    first_round = find_first_round_in_section(lines, sec_start, sec_end)
    if not first_round:
        return False

    final_round = FINAL_ROUND[league_code]
    # Keep schedule preface inside the schedule section before the first round header.
    first_round_idx = None
    for i in range(sec_start, sec_end):
        if ROUND_HEADER_RE.match(lines[i]):
            first_round_idx = i
            break
    assert first_round_idx is not None

    preface = lines[sec_start + 1 : first_round_idx]  # keep after "## 赛程信息..." header line
    notes = collect_existing_notes(lines, sec_start, sec_end)

    backfill = OddsSnapshotBackfill()
    # Warm up stageId once to fail fast.
    if not backfill.get_stage_id(league_code):
        raise RuntimeError(f"无法推导 {league_code} 的 stageId，无法通过 500 API 拉取赛程")

    new_section: List[str] = []
    # Keep the "## 赛程信息(...)" line itself.
    new_section.append(lines[sec_start])
    new_section.extend(preface)
    if preface and not preface[-1].endswith("\n"):
        new_section[-1] = new_section[-1] + "\n"
    if preface and preface[-1].strip():
        new_section.append("\n")

    for r in range(first_round, final_round + 1):
        matches = fetch_round(backfill, league_code, r)
        # If the API returns empty (rare), still create an empty table to keep structure stable.
        new_section.extend(build_round_block(r, matches, final_round, notes))

    new_lines = lines[:sec_start] + new_section + lines[sec_end:]

    new_text = "".join(new_lines)
    old_text = "".join(lines)
    changed = (new_text != old_text)
    if changed and not dry_run:
        teams_md.write_text(new_text, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="all", choices=["all"] + sorted(LEAGUES.keys()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = sorted(LEAGUES.keys()) if args.league == "all" else [args.league]
    changed_any = False
    for league_code in targets:
        changed = update_file(league_code, dry_run=args.dry_run)
        changed_any = changed_any or changed
        print(f"{league_code}: {'updated' if changed else 'no-change'}")

    if args.dry_run:
        return 0
    return 0 if changed_any else 0


if __name__ == "__main__":
    raise SystemExit(main())
