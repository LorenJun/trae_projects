#!/usr/bin/env python3
"""
Import player roster + season stats + injury status into <league>/players/*.json from a CSV.

Why CSV:
- In this environment, many public sites (Transfermarkt/FBref/etc.) trigger bot checks.
- User provides a CSV/JSON export; we convert it into the project's players schema.

CSV columns (header required):
- league_code: premier_league / la_liga / bundesliga / serie_a / ligue_1  (optional if --league is given)
- team: team name in Chinese, must match existing `<league>/players/<team>.json` filename
- player_name: required
- player_name_cn: optional, preferred display name in Chinese; if present,
  JSON `name` uses this field and original English name is stored in `english_name`
- position: 门将/后卫/中场/前锋 (or keep your own; system treats as string)
- age: integer (optional)
- nationality: string (optional)
- market_value: integer (optional, e.g. 50000000)
- transfer_status: current / injured / suspended (optional, default=current)
- injury_reason: string (optional)
- expected_return: YYYY-MM-DD (optional)
- appearances, goals, assists, yellow_cards, red_cards: integers (optional)

Extra columns are ignored.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent

LEAGUE_NAME_CN = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "bundesliga": "德甲",
    "serie_a": "意甲",
    "ligue_1": "法甲",
}


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if v in (None, "", "-", "NA", "N/A"):
            return default
        return int(float(str(v).strip()))
    except Exception:
        return default


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _norm_status(v: str) -> str:
    v = _to_str(v).lower()
    if v in ("injured", "injury", "hurt", "伤病"):
        return "injured"
    if v in ("suspended", "susp", "停赛"):
        return "suspended"
    if v in ("current", "available", "正常", "可用", ""):
        return "current"
    # keep unknown as-is but default to current
    return v or "current"


def _load_team_json(path: Path, league_code: str, team: str) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "name": team,
        "league": LEAGUE_NAME_CN.get(league_code, league_code),
        "players": [],
        "last_updated": "",
        "season": "2026",
    }


def import_csv(csv_path: Path, league_override: Optional[str]) -> Dict[str, int]:
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError("CSV 缺少表头(header)")
        for r in reader:
            rows.append({k: (v or "") for k, v in r.items()})

    grouped: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    for r in rows:
        league_code = league_override or _to_str(r.get("league_code"))
        if not league_code:
            raise RuntimeError("CSV 中缺少 league_code，或请使用 --league 指定")
        team = _to_str(r.get("team"))
        player_name = _to_str(r.get("player_name"))
        if not team or not player_name:
            continue
        grouped.setdefault(league_code, {}).setdefault(team, []).append(r)

    now = datetime.now().isoformat(timespec="seconds")
    written: Dict[str, int] = {}

    for league_code, teams in grouped.items():
        for team, team_rows in teams.items():
            team_file = BASE_DIR / league_code / "players" / f"{team}.json"
            team_json = _load_team_json(team_file, league_code, team)

            players: List[Dict[str, Any]] = []
            for r in team_rows:
                player_name = _to_str(r.get("player_name"))
                player_name_cn = _to_str(r.get("player_name_cn"))
                display_name = player_name_cn or player_name
                p = {
                    "name": display_name,
                    "position": _to_str(r.get("position")),
                    "age": _to_int(r.get("age"), 0),
                    "nationality": _to_str(r.get("nationality")),
                    "transfer_status": _norm_status(r.get("transfer_status", "")),
                    "join_date": _to_str(r.get("join_date")),
                    "contract_until": _to_str(r.get("contract_until")),
                    "market_value": _to_int(r.get("market_value"), 0),
                    "stats": {
                        "appearances": _to_int(r.get("appearances"), 0),
                        "goals": _to_int(r.get("goals"), 0),
                        "assists": _to_int(r.get("assists"), 0),
                        "yellow_cards": _to_int(r.get("yellow_cards"), 0),
                        "red_cards": _to_int(r.get("red_cards"), 0),
                    },
                    "last_updated": now,
                }
                if player_name_cn and player_name and player_name_cn != player_name:
                    p["english_name"] = player_name

                injury_reason = _to_str(r.get("injury_reason"))
                expected_return = _to_str(r.get("expected_return"))
                if injury_reason:
                    p["injury_reason"] = injury_reason
                if expected_return:
                    p["expected_return"] = expected_return

                # Keep optional extra fields if present in CSV
                for extra_key in ("number", "height_cm", "foot", "minutes", "starts"):
                    if extra_key in r and _to_str(r.get(extra_key)):
                        p[extra_key] = _to_str(r.get(extra_key))

                players.append(p)

            team_json["players"] = players
            team_json["last_updated"] = now
            team_json.setdefault("season", "2026")
            team_json.setdefault("name", team)
            team_json.setdefault("league", LEAGUE_NAME_CN.get(league_code, league_code))

            team_file.parent.mkdir(parents=True, exist_ok=True)
            team_file.write_text(json.dumps(team_json, ensure_ascii=False, indent=2), encoding="utf-8")
            written[f"{league_code}/{team}"] = len(players)

    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--league", default="", help="Optional league_code override")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    if not csv_path.exists():
        raise SystemExit(f"CSV 不存在: {csv_path}")

    league_override = args.league.strip() or None
    written = import_csv(csv_path, league_override)
    print(f"Imported teams: {len(written)}")
    for k in sorted(written.keys())[:20]:
        print(f"- {k}: {written[k]} players")
    if len(written) > 20:
        print("... (truncated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
