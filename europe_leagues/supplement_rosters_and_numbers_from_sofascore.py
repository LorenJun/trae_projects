#!/usr/bin/env python3
"""
Supplement missing roster members + jersey numbers using Sofascore API.

Why:
- Understat league data covers mainly players with recorded stats, not always full squad.
- Wikidata does not have jersey numbers for everyone.
- Sofascore provides team roster and jerseyNumber in a stable JSON endpoint.

Scope:
- la_liga / bundesliga / serie_a / ligue_1

Behavior:
- For each team file:
  - Find Sofascore team id via search/all?q=<team_en>
  - Fetch team players via /team/<id>/players
  - Merge:
    - If player exists (match by normalized english_name), fill missing `shirt_number`
      and attach `sofascore_player_id`.
    - If player missing, add a new minimal player entry (stats remain 0/unknown).
  - Adds `data_sources += sofascore.com`.

This does not overwrite existing `shirt_number_source` unless missing.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent
LEAGUES = ("la_liga", "bundesliga", "serie_a", "ligue_1")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; europe_leagues/1.0)"}

# Reuse the Understat English team names we already mapped.
from sync_other_leagues_players_from_understat import LEAGUES as UNDERSTAT_CFG  # noqa: E402


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    for old, new in {
        "&": " and ",
        "-": " ",
        "'": "",
        ".": " ",
        ",": " ",
        "/": " ",
    }.items():
        value = value.replace(old, new)
    return " ".join(value.split())


def _pos_to_cn(code: str) -> str:
    code = (code or "").upper().strip()
    if code.startswith("G"):
        return "门将"
    if code.startswith("D"):
        return "后卫"
    if code.startswith("M"):
        return "中场"
    if code.startswith("F"):
        return "前锋"
    return ""


def _get_team_id(team_en: str) -> Optional[int]:
    url = "https://api.sofascore.com/api/v1/search/all"
    r = requests.get(url, params={"q": team_en}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    results = r.json().get("results", [])
    for item in results:
        if item.get("type") != "team":
            continue
        ent = item.get("entity", {}) or {}
        sport = ent.get("sport", {}) or {}
        if sport.get("id") != 1:
            continue
        name = ent.get("name") or ""
        # Prefer exact match, otherwise accept first football team.
        if _norm(name) == _norm(team_en):
            return int(ent.get("id"))
    for item in results:
        if item.get("type") == "team":
            ent = item.get("entity", {}) or {}
            sport = ent.get("sport", {}) or {}
            if sport.get("id") == 1:
                return int(ent.get("id"))
    return None


def _get_roster(team_id: int) -> List[Dict[str, Any]]:
    url = f"https://api.sofascore.com/api/v1/team/{team_id}/players"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    out = r.json().get("players", [])
    players: List[Dict[str, Any]] = []
    for row in out:
        ent = row.get("player") or {}
        if not ent.get("name") or not ent.get("id"):
            continue
        players.append(ent)
    return players


def _cn_to_en_map(league_code: str) -> Dict[str, str]:
    # UNDERSTAT_CFG[league]['team_map'] is {english -> chinese}
    team_map = UNDERSTAT_CFG[league_code]["team_map"]
    return {cn: en for en, cn in team_map.items()}


def _ensure_sources(p: Dict[str, Any], source: str) -> None:
    sources = set(p.get("data_sources") or [])
    sources.add(source)
    p["data_sources"] = sorted(sources)


def merge_team_file(league_code: str, team_file: Path, team_en: str) -> Tuple[int, int]:
    data = json.loads(team_file.read_text(encoding="utf-8"))
    players = data.get("players") or []
    existing: Dict[str, Dict[str, Any]] = {}
    for p in players:
        key = _norm(p.get("english_name") or p.get("name") or "")
        if key:
            existing[key] = p

    team_id = _get_team_id(team_en)
    if not team_id:
        return 0, 0
    roster = _get_roster(team_id)

    added = 0
    filled_numbers = 0
    for ent in roster:
        en_name = ent.get("name") or ""
        key = _norm(en_name)
        if not key:
            continue
        jersey = ent.get("jerseyNumber") or ent.get("shirtNumber")
        try:
            jersey_int = int(jersey) if jersey is not None and str(jersey).strip() else None
        except Exception:
            jersey_int = None

        if key in existing:
            p = existing[key]
            p["sofascore_player_id"] = ent.get("id")
            if not (p.get("shirt_number") or p.get("shirt_numbers")) and jersey_int is not None:
                p["shirt_number"] = jersey_int
                p["shirt_number_source"] = "sofascore"
                filled_numbers += 1
            if not p.get("position"):
                p["position"] = _pos_to_cn(ent.get("position"))
            _ensure_sources(p, "sofascore.com")
        else:
            new_p = {
                "name": en_name,
                "english_name": en_name,
                "position": _pos_to_cn(ent.get("position")),
                "age": 0,
                "nationality": (ent.get("country") or {}).get("name") or "",
                "transfer_status": "current",
                "market_value": 0,
                "stats": {
                    "appearances": 0,
                    "minutes": 0,
                    "goals": 0,
                    "assists": 0,
                    "yellow_cards": 0,
                    "red_cards": 0,
                },
                "data_sources": ["sofascore.com"],
                "sofascore_player_id": ent.get("id"),
            }
            if jersey_int is not None:
                new_p["shirt_number"] = jersey_int
                new_p["shirt_number_source"] = "sofascore"
            players.append(new_p)
            existing[key] = new_p
            added += 1

    data["players"] = players
    team_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return added, filled_numbers


def main() -> int:
    total_added = 0
    total_filled = 0
    for league in LEAGUES:
        cn_to_en = _cn_to_en_map(league)
        league_dir = BASE_DIR / league / "players"
        for team_file in sorted(league_dir.glob("*.json")):
            team_cn = team_file.stem
            team_en = cn_to_en.get(team_cn)
            if not team_en:
                # likely an old team file; skip to avoid wrong league mapping
                continue
            added, filled = merge_team_file(league, team_file, team_en)
            if added or filled:
                print(f"{league}/{team_cn}: added={added} filled_numbers={filled}")
            total_added += added
            total_filled += filled
    print(f"TOTAL added={total_added} filled_numbers={total_filled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

