#!/usr/bin/env python3
"""
Update five-league standings (积分榜) in teams_2025-26.md by scraping gongfutiyu.com.

We intentionally avoid bs4; standings are available in Next.js __NEXT_DATA__ JSON.

Output:
  - Updates only the "## 积分榜（截至第X轮）" header line and the markdown table under it.
  - Does NOT modify other sections.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Dict, List, Tuple

import requests


LEAGUE_URLS = {
    "premier_league": "https://www.gongfutiyu.com/data/yingchao/",
    "la_liga": "https://www.gongfutiyu.com/data/xijia/",
    "serie_a": "https://www.gongfutiyu.com/data/yijia/",
    "bundesliga": "https://www.gongfutiyu.com/data/dejia/",
    "ligue_1": "https://www.gongfutiyu.com/data/fajia/",
}


def _extract_next_data(html: str) -> Dict:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Missing __NEXT_DATA__")
    return json.loads(m.group(1))


def fetch_standings(league_code: str) -> Tuple[int, List[Dict]]:
    """Return (max_round, rows). rows are normalized dicts."""
    url = LEAGUE_URLS[league_code]
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    next_data = _extract_next_data(resp.text)
    page_props = next_data.get("props", {}).get("pageProps", {})
    jf = page_props.get("dataJifenList", {}) or {}
    data = jf.get("data", {}) or {}
    tables = (data.get("data", {}) or {}).get("tables") or []
    if not tables:
        # Some pages place the payload directly under dataJifenList['data']['tables']
        tables = (data.get("tables") or [])
    if not tables:
        raise RuntimeError(f"No standings tables found for {league_code}")

    table0 = tables[0]
    raw_rows = table0.get("rows") or []
    if not raw_rows:
        raise RuntimeError(f"No standings rows found for {league_code}")

    team_info = jf.get("team_info", {}) or {}

    def team_name(team_id: int) -> str:
        info = team_info.get(str(team_id)) or team_info.get(team_id) or {}
        if isinstance(info, dict):
            return info.get("name_cn") or info.get("name") or info.get("short_name") or str(team_id)
        return str(team_id)

    rows = []
    max_total = 0
    for r in raw_rows:
        if not isinstance(r, dict):
            continue
        total = int(r.get("total") or 0)
        max_total = max(max_total, total)
        rows.append(
            {
                "position": int(r.get("position") or 0),
                "team": team_name(int(r.get("team_id") or 0)),
                "total": total,
                "won": int(r.get("won") or 0),
                "draw": int(r.get("draw") or 0),
                "loss": int(r.get("loss") or 0),
                "goals": int(r.get("goals") or 0),
                "goals_against": int(r.get("goals_against") or 0),
                "goal_diff": int(r.get("goal_diff") or 0),
                "points": int(r.get("points") or 0),
            }
        )

    rows.sort(key=lambda x: x["position"] or 999)
    return max_total, rows


def _fmt_goal_diff(gd: int) -> str:
    if gd == 0:
        return "0"
    return f"{gd:+d}"


def update_teams_md(league_code: str, max_round: int, rows: List[Dict]) -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, league_code, "teams_2025-26.md")
    if not os.path.exists(path):
        raise RuntimeError(f"teams file not found: {path}")

    lines = open(path, "r", encoding="utf-8").read().splitlines(True)

    # Update header line: "## 积分榜（截至第X轮）"
    out = []
    i = 0
    standings_header_idx = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("## 积分榜"):
            standings_header_idx = len(out)
            # Replace only if pattern matches
            m = re.match(r"^(## 积分榜（截至第)(\d+)(轮）)\s*$", line.strip())
            if m:
                line = f"{m.group(1)}{max_round}{m.group(3)}\n"
        out.append(line)
        i += 1

    if standings_header_idx is None:
        raise RuntimeError(f"No standings header found in {path}")

    # Find the standings table after the header
    header_pos = None
    for idx in range(standings_header_idx, len(out)):
        if out[idx].lstrip().startswith("| 排名 |"):
            header_pos = idx
            break
    if header_pos is None:
        raise RuntimeError(f"No standings table header found in {path}")

    # Keep the header and separator line; replace subsequent team rows
    sep_pos = header_pos + 1 if header_pos + 1 < len(out) else None
    if sep_pos is None or not out[sep_pos].lstrip().startswith("|"):
        raise RuntimeError(f"No standings table separator found in {path}")

    end_pos = sep_pos + 1
    while end_pos < len(out) and out[end_pos].lstrip().startswith("|"):
        end_pos += 1

    new_rows = []
    for r in rows:
        new_rows.append(
            "| "
            + " | ".join(
                [
                    str(r["position"]),
                    str(r["team"]),
                    str(r["total"]),
                    str(r["won"]),
                    str(r["draw"]),
                    str(r["loss"]),
                    str(r["goals"]),
                    str(r["goals_against"]),
                    _fmt_goal_diff(int(r["goal_diff"])),
                    str(r["points"]),
                ]
            )
            + " |\n"
        )

    updated = out[: sep_pos + 1] + new_rows + out[end_pos:]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(updated)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="all", choices=["all"] + sorted(LEAGUE_URLS.keys()))
    args = parser.parse_args()

    targets = list(LEAGUE_URLS.keys()) if args.league == "all" else [args.league]
    updated = []
    for lc in targets:
        max_round, rows = fetch_standings(lc)
        updated.append(update_teams_md(lc, max_round, rows))
    print("\n".join(updated))


if __name__ == "__main__":
    main()

