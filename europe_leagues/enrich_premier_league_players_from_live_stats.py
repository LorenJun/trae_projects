#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict
from email.utils import formatdate
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent
PLAYERS_DIR = BASE_DIR / "premier_league" / "players"

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
UNDERSTAT_LEAGUE_URL = "https://understat.com/getLeagueData/EPL/2025"
UNDERSTAT_PLAYER_URL = "https://understat.com/getPlayerData/{player_id}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

TEAM_EN_TO_CN = {
    "Arsenal": "阿森纳",
    "Aston Villa": "阿斯顿维拉",
    "Bournemouth": "伯恩茅斯",
    "Brentford": "布伦特福德",
    "Brighton": "布莱顿",
    "Brighton & Hove Albion": "布莱顿",
    "Burnley": "伯恩利",
    "Chelsea": "切尔西",
    "Crystal Palace": "水晶宫",
    "Everton": "埃弗顿",
    "Fulham": "富勒姆",
    "Ipswich": "伊普斯维奇",
    "Leeds": "利兹联",
    "Leeds United": "利兹联",
    "Leicester": "莱斯特城",
    "Liverpool": "利物浦",
    "Man City": "曼城",
    "Manchester City": "曼城",
    "Man Utd": "曼联",
    "Manchester United": "曼联",
    "Newcastle": "纽卡斯尔联",
    "Newcastle United": "纽卡斯尔联",
    "Nott'm Forest": "诺丁汉森林",
    "Nottingham Forest": "诺丁汉森林",
    "Southampton": "南安普顿",
    "Sunderland": "桑德兰",
    "Spurs": "热刺",
    "Tottenham": "热刺",
    "Tottenham Hotspur": "热刺",
    "West Ham": "西汉姆联",
    "West Ham United": "西汉姆联",
    "Wolves": "狼队",
    "Wolverhampton Wanderers": "狼队",
}

POSITION_MAP = {
    1: "门将",
    2: "后卫",
    3: "中场",
    4: "前锋",
}


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value


def _normalize_name(value: str) -> str:
    value = _normalize_text(value or "").lower()
    for old, new in {
        "&": " and ",
        "-": " ",
        "'": "",
        ".": " ",
        ",": " ",
        "/": " ",
    }.items():
        value = value.replace(old, new)
    value = " ".join(value.split())
    return value


def _name_variants(value: str) -> List[str]:
    value = value or ""
    variants = set()
    normalized = _normalize_name(value)
    if normalized:
        variants.add(normalized)

    if "," in value:
        left, right = [x.strip() for x in value.split(",", 1)]
        left_words = left.split()
        right_words = right.split()
        if right_words:
            variants.add(_normalize_name(" ".join(right_words + left_words)))
        if left_words and right_words:
            variants.add(_normalize_name(f"{right_words[0]} {left_words[-1]}"))
    else:
        parts = value.split()
        if len(parts) >= 2:
            variants.add(_normalize_name(f"{parts[0]} {parts[-1]}"))

    return [v for v in variants if v]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "None"):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(str(value))
    except Exception:
        return default


def _status_to_transfer_status(status: str, news: str) -> str:
    status = (status or "").lower()
    news_l = (news or "").lower()
    if status in {"i", "u"} or "injur" in news_l:
        return "injured"
    if status == "s" or "suspend" in news_l:
        return "suspended"
    if status == "d":
        return "doubtful"
    return "current"


def _build_heatmap_grid(shots: Iterable[Dict[str, Any]], cols: int = 6, rows: int = 5) -> Dict[str, Any]:
    grid = [[0 for _ in range(cols)] for _ in range(rows)]
    shot_count = 0
    for shot in shots:
        x = _safe_float(shot.get("X"), -1)
        y = _safe_float(shot.get("Y"), -1)
        if x < 0 or y < 0:
            continue
        shot_count += 1
        c = min(cols - 1, max(0, int(math.floor(x * cols))))
        r = min(rows - 1, max(0, int(math.floor(y * rows))))
        grid[r][c] += 1
    return {
        "source": "understat_shots",
        "grid_rows": rows,
        "grid_cols": cols,
        "shot_count": shot_count,
        "shot_density": grid,
    }


def fetch_fpl() -> Dict[str, Any]:
    resp = requests.get(FPL_BOOTSTRAP_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_understat_league() -> Dict[str, Any]:
    resp = requests.get(UNDERSTAT_LEAGUE_URL, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_understat_player(player_id: str) -> Dict[str, Any]:
    resp = requests.get(UNDERSTAT_PLAYER_URL.format(player_id=player_id), headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def build_fpl_index(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    team_id_to_name = {t["id"]: TEAM_EN_TO_CN.get(t["name"], t["name"]) for t in data["teams"]}
    index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for player in data["elements"]:
        team_cn = team_id_to_name.get(player["team"])
        if not team_cn:
            continue
        player["_team_cn"] = team_cn
        player["_variant_keys"] = {
            _normalize_name(player.get("web_name", "")),
            _normalize_name(f"{player.get('first_name', '')} {player.get('second_name', '')}"),
            _normalize_name(f"{player.get('second_name', '')} {player.get('first_name', '')}"),
        }
        index[team_cn].append(player)
    return index


def build_understat_index(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for player in data["players"]:
        team_cn = TEAM_EN_TO_CN.get(player.get("team_title", ""), player.get("team_title", ""))
        if not team_cn:
            continue
        player["_team_cn"] = team_cn
        player["_variant_keys"] = set(_name_variants(player.get("player_name", "")))
        index[team_cn].append(player)
    return index


def match_player(candidates: List[Dict[str, Any]], english_name: str) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    targets = set(_name_variants(english_name))
    if not targets:
        targets = {_normalize_name(english_name)}

    best: Optional[Tuple[int, Dict[str, Any]]] = None
    for candidate in candidates:
        score = 0
        variant_keys = candidate.get("_variant_keys", set())
        for t in targets:
            if not t:
                continue
            if t in variant_keys:
                score = max(score, 100)
            else:
                for v in variant_keys:
                    if t and v and (t in v or v in t):
                        score = max(score, 70)
        if best is None or score > best[0]:
            best = (score, candidate)
    if best and best[0] >= 70:
        return best[1]
    return None


def enrich_team_file(
    file_path: Path,
    fpl_index: Dict[str, List[Dict[str, Any]]],
    understat_index: Dict[str, List[Dict[str, Any]]],
    understat_cache: Dict[str, Dict[str, Any]],
) -> Tuple[int, int]:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    team_cn = data.get("name") or file_path.stem
    players = data.get("players", [])
    fpl_hits = 0
    understat_hits = 0

    for player in players:
        english_name = player.get("english_name") or player.get("name") or ""
        fpl = match_player(fpl_index.get(team_cn, []), english_name)
        ustat = match_player(understat_index.get(team_cn, []), english_name)

        if fpl:
            fpl_hits += 1
            player["position"] = POSITION_MAP.get(fpl.get("element_type"), player.get("position", ""))
            player["transfer_status"] = _status_to_transfer_status(fpl.get("status", ""), fpl.get("news", ""))
            if fpl.get("news"):
                player["injury_reason"] = fpl.get("news")
            player["chance_of_playing_this_round"] = fpl.get("chance_of_playing_this_round")
            player["chance_of_playing_next_round"] = fpl.get("chance_of_playing_next_round")
            player["squad_number"] = fpl.get("squad_number")
            player["photo"] = fpl.get("photo")
            player["fpl_price_m"] = _safe_float(fpl.get("now_cost")) / 10.0
            player["stats"] = {
                "appearances": _safe_int(fpl.get("starts")),
                "minutes": _safe_int(fpl.get("minutes")),
                "goals": _safe_int(fpl.get("goals_scored")),
                "assists": _safe_int(fpl.get("assists")),
                "yellow_cards": _safe_int(fpl.get("yellow_cards")),
                "red_cards": _safe_int(fpl.get("red_cards")),
                "clean_sheets": _safe_int(fpl.get("clean_sheets")),
                "saves": _safe_int(fpl.get("saves")),
                "bonus": _safe_int(fpl.get("bonus")),
            }
            player["rating_metrics"] = {
                "source": "fpl",
                "form": _safe_float(fpl.get("form")),
                "points_per_game": _safe_float(fpl.get("points_per_game")),
                "ict_index": _safe_float(fpl.get("ict_index")),
                "influence": _safe_float(fpl.get("influence")),
                "creativity": _safe_float(fpl.get("creativity")),
                "threat": _safe_float(fpl.get("threat")),
                "selected_by_percent": _safe_float(fpl.get("selected_by_percent")),
            }
            player["technical_stats"] = {
                "expected_goals": _safe_float(fpl.get("expected_goals")),
                "expected_assists": _safe_float(fpl.get("expected_assists")),
                "expected_goal_involvements": _safe_float(fpl.get("expected_goal_involvements")),
                "expected_goals_conceded": _safe_float(fpl.get("expected_goals_conceded")),
                "influence": _safe_float(fpl.get("influence")),
                "creativity": _safe_float(fpl.get("creativity")),
                "threat": _safe_float(fpl.get("threat")),
                "ict_index": _safe_float(fpl.get("ict_index")),
            }

        if ustat:
            understat_hits += 1
            player_id = str(ustat.get("id"))
            player["understat_player_id"] = player_id
            player["shooting_stats"] = {
                "source": "understat",
                "shots": _safe_int(ustat.get("shots")),
                "goals": _safe_int(ustat.get("goals")),
                "xg": _safe_float(ustat.get("xG")),
                "npxg": _safe_float(ustat.get("npxG")),
                "xg_chain": _safe_float(ustat.get("xGChain")),
                "xg_buildup": _safe_float(ustat.get("xGBuildup")),
            }
            player["passing_stats"] = {
                "source": "understat+fpl",
                "key_passes": _safe_int(ustat.get("key_passes")),
                "xa": _safe_float(ustat.get("xA")),
                "creativity_index": _safe_float(player.get("technical_stats", {}).get("creativity", 0)),
            }
            player["defensive_stats"] = {
                "source": "fpl",
                "clean_sheets": _safe_int(player.get("stats", {}).get("clean_sheets")),
                "saves": _safe_int(player.get("stats", {}).get("saves")),
                "yellow_cards": _safe_int(player.get("stats", {}).get("yellow_cards")),
                "red_cards": _safe_int(player.get("stats", {}).get("red_cards")),
                "expected_goals_conceded": _safe_float(player.get("technical_stats", {}).get("expected_goals_conceded", 0)),
            }
            player["advanced_stats"] = {
                "source": "understat",
                "games": _safe_int(ustat.get("games")),
                "minutes": _safe_int(ustat.get("time")),
                "position_code": ustat.get("position", ""),
                "xg": _safe_float(ustat.get("xG")),
                "xa": _safe_float(ustat.get("xA")),
                "npxg": _safe_float(ustat.get("npxG")),
                "shots": _safe_int(ustat.get("shots")),
                "key_passes": _safe_int(ustat.get("key_passes")),
                "xg_chain": _safe_float(ustat.get("xGChain")),
                "xg_buildup": _safe_float(ustat.get("xGBuildup")),
            }
            if player_id not in understat_cache:
                understat_cache[player_id] = fetch_understat_player(player_id)
            player_detail = understat_cache[player_id]
            player["heatmap"] = _build_heatmap_grid(player_detail.get("shots", []))
            player["shot_map_available"] = bool(player_detail.get("shots"))

        player.setdefault("data_sources", [])
        sources = set(player["data_sources"])
        if fpl:
            sources.add("fantasy.premierleague.com")
        if ustat:
            sources.add("understat.com")
        player["data_sources"] = sorted(sources)

    data["last_updated"] = formatdate(usegmt=True)
    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return fpl_hits, understat_hits


def main() -> int:
    fpl = fetch_fpl()
    understat = fetch_understat_league()
    fpl_index = build_fpl_index(fpl)
    understat_index = build_understat_index(understat)
    understat_cache: Dict[str, Dict[str, Any]] = {}

    total_fpl_hits = 0
    total_understat_hits = 0
    for file_path in sorted(PLAYERS_DIR.glob("*.json")):
        fpl_hits, understat_hits = enrich_team_file(file_path, fpl_index, understat_index, understat_cache)
        total_fpl_hits += fpl_hits
        total_understat_hits += understat_hits
        print(f"{file_path.stem}: fpl={fpl_hits}, understat={understat_hits}")

    print(f"TOTAL: fpl={total_fpl_hits}, understat={total_understat_hits}, understat_cached={len(understat_cache)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
