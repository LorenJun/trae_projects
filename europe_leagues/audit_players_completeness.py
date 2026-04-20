#!/usr/bin/env python3
"""
Audit completeness of players/*.json for the 4 leagues:
La Liga / Bundesliga / Serie A / Ligue 1.

Checks:
- team file exists & has players
- per-player has cn name (wikidata_id) and shirt number (shirt_number or shirt_numbers)
- quick detection of "likely incomplete roster" (very low player count)

Outputs a concise report to stdout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple


BASE_DIR = Path(__file__).resolve().parent
LEAGUES = ("la_liga", "bundesliga", "serie_a", "ligue_1")


def audit_league(league: str) -> Dict[str, object]:
    league_dir = BASE_DIR / league / "players"
    files = sorted(league_dir.glob("*.json"))
    team_rows: List[Tuple[str, int, int, int]] = []
    empty_teams: List[str] = []

    totals = {
        "league": league,
        "team_files": len(files),
        "players": 0,
        "teams_empty": 0,
        "missing_cn": 0,
        "missing_number": 0,
        "likely_incomplete_roster": 0,  # player_count < 18 (heuristic)
    }

    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            empty_teams.append(fp.stem)
            totals["teams_empty"] += 1
            team_rows.append((fp.stem, 0, 0, 0))
            continue

        players = data.get("players", []) or []
        if not players:
            empty_teams.append(fp.stem)
            totals["teams_empty"] += 1

        missing_cn = sum(1 for p in players if not p.get("wikidata_id"))
        missing_num = sum(1 for p in players if not (p.get("shirt_number") or p.get("shirt_numbers")))
        totals["players"] += len(players)
        totals["missing_cn"] += missing_cn
        totals["missing_number"] += missing_num
        if len(players) and len(players) < 18:
            totals["likely_incomplete_roster"] += 1

        team_rows.append((fp.stem, len(players), missing_cn, missing_num))

    # top 10 teams by missing_cn then missing_num
    team_rows_sorted = sorted(team_rows, key=lambda x: (-x[2], -x[3], x[0]))
    return {
        "totals": totals,
        "empty_teams": empty_teams,
        "top_missing": team_rows_sorted[:10],
    }


def main() -> int:
    print("PLAYERS COMPLETENESS AUDIT (4 leagues)\n")
    for league in LEAGUES:
        r = audit_league(league)
        tot = r["totals"]
        print(f"[{league}] team_files={tot['team_files']} players={tot['players']} empty={tot['teams_empty']} "
              f"missing_cn={tot['missing_cn']} missing_number={tot['missing_number']} "
              f"likely_incomplete_roster={tot['likely_incomplete_roster']}")
        empty = r["empty_teams"]
        if empty:
            print("  empty teams:", ", ".join(empty[:20]) + (" ..." if len(empty) > 20 else ""))
        print("  top missing (team, players, missing_cn, missing_number):")
        for row in r["top_missing"]:
            print("   ", row)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

