#!/usr/bin/env python3
"""
Update standings tables in five leagues teams_2025-26.md by computing from finished match results
already present in the schedule tables.

Why:
  - External sources may be blocked/unreliable.
  - Our project already updates schedules/results; standings should be consistent with them.

What it does:
  - Parses all markdown table rows like:
      | YYYY-MM-DD | HH:MM | Home | x-y | Away | note |
    where score is digits-digits.
  - Computes: played, won, draw, lost, goals for/against, goal diff, points.
  - Updates only the "## 积分榜（截至第X轮）" header and the table under it.

Limitations:
  - If teams_2025-26.md only contains a partial season schedule/results, the standings reflect that subset.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple


LEAGUES = ["premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1"]


@dataclass
class TeamStat:
    team: str
    played: int = 0
    won: int = 0
    draw: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def pts(self) -> int:
        return self.won * 3 + self.draw


def _fmt_gd(gd: int) -> str:
    if gd == 0:
        return "0"
    return f"{gd:+d}"


def parse_results_from_teams_md(path: str) -> Tuple[Dict[str, TeamStat], int]:
    stats: Dict[str, TeamStat] = {}

    def get(team: str) -> TeamStat:
        if team not in stats:
            stats[team] = TeamStat(team=team)
        return stats[team]

    score_re = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")

    lines = open(path, "r", encoding="utf-8").read().splitlines()
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 6:
            continue
        _date, _time, home, score, away, _note = cols
        m = score_re.match(score or "")
        if not m:
            continue
        hs = int(m.group(1))
        as_ = int(m.group(2))

        h = get(home)
        a = get(away)
        h.played += 1
        a.played += 1
        h.gf += hs
        h.ga += as_
        a.gf += as_
        a.ga += hs

        if hs > as_:
            h.won += 1
            a.lost += 1
        elif hs < as_:
            a.won += 1
            h.lost += 1
        else:
            h.draw += 1
            a.draw += 1

    max_round = max((s.played for s in stats.values()), default=0)
    return stats, max_round


def update_standings_section(path: str, stats: Dict[str, TeamStat], max_round: int) -> None:
    lines = open(path, "r", encoding="utf-8").read().splitlines(True)

    # Build sorted standings list
    rows = list(stats.values())
    rows.sort(key=lambda s: (-s.pts, -s.gd, -s.gf, s.team))

    # Find standings header
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("## 积分榜"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError(f"No standings header found in {path}")

    # Update header round number if present
    m = re.match(r"^(## 积分榜（截至第)(\d+)(轮）)\s*$", lines[header_idx].strip())
    if m and max_round > 0:
        lines[header_idx] = f"{m.group(1)}{max_round}{m.group(3)}\n"

    # Find table header line after standings header
    table_header_idx = None
    for i in range(header_idx + 1, len(lines)):
        if lines[i].lstrip().startswith("| 排名 |"):
            table_header_idx = i
            break
        # stop if next section begins
        if lines[i].startswith("## ") and i > header_idx + 1:
            break
    if table_header_idx is None:
        raise RuntimeError(f"No standings table header found in {path}")

    sep_idx = table_header_idx + 1
    if sep_idx >= len(lines) or not lines[sep_idx].lstrip().startswith("|"):
        raise RuntimeError(f"No standings table separator found in {path}")

    end_idx = sep_idx + 1
    while end_idx < len(lines) and lines[end_idx].lstrip().startswith("|"):
        end_idx += 1

    # Rebuild table rows (all teams present in computed stats)
    new_table_rows: List[str] = []
    for pos, s in enumerate(rows, start=1):
        new_table_rows.append(
            "| "
            + " | ".join(
                [
                    str(pos),
                    s.team,
                    str(s.played),
                    str(s.won),
                    str(s.draw),
                    str(s.lost),
                    str(s.gf),
                    str(s.ga),
                    _fmt_gd(s.gd),
                    str(s.pts),
                ]
            )
            + " |\n"
        )

    updated = lines[: sep_idx + 1] + new_table_rows + lines[end_idx:]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(updated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="all", choices=["all"] + LEAGUES)
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    targets = LEAGUES if args.league == "all" else [args.league]
    updated_files = []

    for league in targets:
        path = os.path.join(base_dir, league, "teams_2025-26.md")
        if not os.path.exists(path):
            continue
        stats, max_round = parse_results_from_teams_md(path)
        if not stats:
            # No finished matches in file; skip.
            continue
        update_standings_section(path, stats, max_round)
        updated_files.append(path)

    print("\n".join(updated_files))


if __name__ == "__main__":
    main()

