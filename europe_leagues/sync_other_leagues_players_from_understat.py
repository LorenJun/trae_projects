#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import formatdate
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

LEAGUES: Dict[str, Dict[str, Any]] = {
    "la_liga": {
        "understat": "La_Liga",
        "team_map": {
            "Alaves": "阿拉维斯",
            "Athletic Club": "毕尔巴鄂竞技",
            "Atletico Madrid": "马德里竞技",
            "Barcelona": "巴塞罗那",
            "Celta Vigo": "塞尔塔",
            "Elche": "埃尔切",
            "Espanyol": "西班牙人",
            "Getafe": "赫塔菲",
            "Girona": "赫罗纳",
            "Levante": "莱万特",
            "Mallorca": "马略卡",
            "Osasuna": "奥萨苏纳",
            "Rayo Vallecano": "巴列卡诺",
            "Real Betis": "皇家贝蒂斯",
            "Real Madrid": "皇家马德里",
            "Real Oviedo": "皇家奥维耶多",
            "Real Sociedad": "皇家社会",
            "Sevilla": "塞维利亚",
            "Valencia": "巴伦西亚",
            "Villarreal": "比利亚雷亚尔",
        },
    },
    "bundesliga": {
        "understat": "Bundesliga",
        "team_map": {
            "Augsburg": "奥格斯堡",
            "Bayer Leverkusen": "勒沃库森",
            "Bayern Munich": "拜仁慕尼黑",
            "Borussia Dortmund": "多特蒙德",
            "Borussia M.Gladbach": "门兴格拉德巴赫",
            "Eintracht Frankfurt": "法兰克福",
            "FC Cologne": "科隆",
            "FC Heidenheim": "海登海姆",
            "Freiburg": "弗赖堡",
            "Hamburger SV": "汉堡",
            "Hoffenheim": "霍芬海姆",
            "Mainz 05": "美因茨",
            "RasenBallsport Leipzig": "莱比锡红牛",
            "St. Pauli": "圣保利",
            "Union Berlin": "柏林联合",
            "VfB Stuttgart": "斯图加特",
            "Werder Bremen": "云达不莱梅",
            "Wolfsburg": "沃尔夫斯堡",
        },
    },
    "serie_a": {
        "understat": "Serie_A",
        "team_map": {
            "AC Milan": "AC米兰",
            "Atalanta": "亚特兰大",
            "Bologna": "博洛尼亚",
            "Cagliari": "卡利亚里",
            "Como": "科莫",
            "Cremonese": "克雷莫纳",
            "Fiorentina": "佛罗伦萨",
            "Genoa": "热那亚",
            "Inter": "国际米兰",
            "Juventus": "尤文图斯",
            "Lazio": "拉齐奥",
            "Lecce": "莱切",
            "Napoli": "那不勒斯",
            "Parma Calcio 1913": "帕尔马",
            "Pisa": "比萨",
            "Roma": "罗马",
            "Sassuolo": "萨索洛",
            "Torino": "都灵",
            "Udinese": "乌迪内斯",
            "Verona": "维罗纳",
        },
    },
    "ligue_1": {
        "understat": "Ligue_1",
        "team_map": {
            "Angers": "昂热",
            "Auxerre": "欧塞尔",
            "Brest": "布雷斯特",
            "Le Havre": "勒阿弗尔",
            "Lens": "朗斯",
            "Lille": "里尔",
            "Lorient": "洛里昂",
            "Lyon": "里昂",
            "Marseille": "马赛",
            "Metz": "梅斯",
            "Monaco": "摩纳哥",
            "Nantes": "南特",
            "Nice": "尼斯",
            "Paris FC": "巴黎FC",
            "Paris Saint Germain": "巴黎圣日耳曼",
            "Rennes": "雷恩",
            "Strasbourg": "斯特拉斯堡",
            "Toulouse": "图卢兹",
        },
    },
}


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value


def _normalize_name(value: str) -> str:
    value = _normalize_text(value).lower()
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


def _position_code_to_cn(position_code: str) -> str:
    code = (position_code or "").strip().upper()
    if code.startswith("GK"):
        return "门将"
    if code.startswith("D"):
        return "后卫"
    if code.startswith("M"):
        return "中场"
    if code.startswith("F"):
        return "前锋"
    return ""


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


def fetch_understat_league(understat_code: str) -> Dict[str, Any]:
    url = f"https://understat.com/getLeagueData/{understat_code}/2025"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_understat_player(player_id: str) -> Dict[str, Any]:
    url = f"https://understat.com/getPlayerData/{player_id}"
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _current_team_title(raw_team_title: str) -> str:
    # Some Understat rows are "FormerClub,CurrentClub".
    if "," in raw_team_title:
        return raw_team_title.split(",")[-1].strip()
    return raw_team_title.strip()


def build_base_team_json(league_code: str, team_cn: str) -> Dict[str, Any]:
    return {
        "name": team_cn,
        "league": league_code,
        "players": [],
        "last_updated": "",
        "season": "2026",
    }


def build_player_row(player: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": player.get("player_name", ""),
        "english_name": player.get("player_name", ""),
        "position": _position_code_to_cn(player.get("position", "")),
        "age": 0,
        "nationality": "",
        "transfer_status": "current",
        "join_date": "",
        "contract_until": "",
        "market_value": 0,
        "stats": {
            "appearances": _safe_int(player.get("games")),
            "minutes": _safe_int(player.get("time")),
            "goals": _safe_int(player.get("goals")),
            "assists": _safe_int(player.get("assists")),
            "yellow_cards": _safe_int(player.get("yellow_cards")),
            "red_cards": _safe_int(player.get("red_cards")),
        },
        "shooting_stats": {
            "source": "understat",
            "shots": _safe_int(player.get("shots")),
            "goals": _safe_int(player.get("goals")),
            "xg": _safe_float(player.get("xG")),
            "npxg": _safe_float(player.get("npxG")),
            "xg_chain": _safe_float(player.get("xGChain")),
            "xg_buildup": _safe_float(player.get("xGBuildup")),
        },
        "passing_stats": {
            "source": "understat",
            "key_passes": _safe_int(player.get("key_passes")),
            "xa": _safe_float(player.get("xA")),
        },
        "advanced_stats": {
            "source": "understat",
            "games": _safe_int(player.get("games")),
            "minutes": _safe_int(player.get("time")),
            "position_code": player.get("position", ""),
            "xg": _safe_float(player.get("xG")),
            "xa": _safe_float(player.get("xA")),
            "npxg": _safe_float(player.get("npxG")),
            "shots": _safe_int(player.get("shots")),
            "key_passes": _safe_int(player.get("key_passes")),
            "xg_chain": _safe_float(player.get("xGChain")),
            "xg_buildup": _safe_float(player.get("xGBuildup")),
        },
        "data_sources": ["understat.com"],
        "understat_player_id": str(player.get("id", "")),
    }


def enrich_heatmaps(players: List[Dict[str, Any]], max_workers: int = 8) -> None:
    targets = [p for p in players if _safe_int(p.get("shooting_stats", {}).get("shots")) > 0 and p.get("understat_player_id")]
    if not targets:
        return

    future_map = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for player in targets:
            future = executor.submit(fetch_understat_player, str(player["understat_player_id"]))
            future_map[future] = player

        for future in as_completed(future_map):
            player = future_map[future]
            try:
                detail = future.result()
                shots = detail.get("shots", [])
                player["heatmap"] = _build_heatmap_grid(shots)
                player["shot_map_available"] = bool(shots)
            except Exception:
                player["shot_map_available"] = False


def sync_league(league_code: str) -> Tuple[int, int]:
    cfg = LEAGUES[league_code]
    league_dir = BASE_DIR / league_code / "players"
    league_dir.mkdir(parents=True, exist_ok=True)

    data = fetch_understat_league(cfg["understat"])
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for raw_player in data["players"]:
        team_en = _current_team_title(raw_player.get("team_title", ""))
        team_cn = cfg["team_map"].get(team_en)
        if not team_cn:
            continue
        grouped.setdefault(team_cn, []).append(raw_player)

    files_written = 0
    players_written = 0

    for team_cn, raw_players in sorted(grouped.items()):
        team_file = league_dir / f"{team_cn}.json"
        if team_file.exists():
            try:
                existing = json.loads(team_file.read_text(encoding="utf-8"))
            except Exception:
                existing = build_base_team_json(league_code, team_cn)
        else:
            existing = build_base_team_json(league_code, team_cn)

        players = [build_player_row(player) for player in raw_players]
        # Deduplicate by english_name after transfer rows normalization.
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for player in players:
            key = _normalize_name(player.get("english_name", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(player)

        enrich_heatmaps(deduped)

        existing["name"] = team_cn
        existing["league"] = league_code
        existing["season"] = "2026"
        existing["players"] = deduped
        existing["last_updated"] = formatdate(usegmt=True)
        team_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        files_written += 1
        players_written += len(deduped)
        print(f"{league_code}/{team_cn}: {len(deduped)} players")

    return files_written, players_written


def main() -> int:
    total_files = 0
    total_players = 0
    for league_code in ("la_liga", "bundesliga", "serie_a", "ligue_1"):
        files_written, players_written = sync_league(league_code)
        total_files += files_written
        total_players += players_written
    print(f"TOTAL files={total_files} players={total_players}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

