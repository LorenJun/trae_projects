#!/usr/bin/env python3
"""
Fetch recent team state (tactics/formation, possession, last lineup, player form)
using the public Sofascore JSON endpoints.

Design goals:
- Best-effort: never block prediction if fetch fails.
- Cache team id resolution to avoid repeated search calls.
- Return a compact, structured payload that can be injected into analysis_context.

Note:
- Starting lineup for *upcoming* matches is generally not available. We use the
  most recent finished match lineup as "latest XI" and compute recent player ratings.
"""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; europe_leagues/1.0)"}
BASE = "https://api.sofascore.com/api/v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip()
    for old, new in {"&": " and ", "-": " ", "'": "", ".": " ", ",": " ", "/": " "}.items():
        s = s.replace(old, new)
    return " ".join(s.split())


def _runtime_dir(base_dir: str) -> Path:
    d = Path(base_dir) / ".okooo-scraper" / "runtime"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _team_id_cache_path(base_dir: str) -> Path:
    return _runtime_dir(base_dir) / "sofascore_team_ids.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_team_id_cache(base_dir: str) -> Dict[str, Any]:
    path = _team_id_cache_path(base_dir)
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _save_team_id_cache(base_dir: str, cache: Dict[str, Any]) -> None:
    _write_json(_team_id_cache_path(base_dir), cache)


def _search_team_id(query: str, timeout_s: int = 20) -> Optional[Tuple[int, str]]:
    """Return (team_id, display_name) for best-effort search."""
    q = (query or "").strip()
    if not q:
        return None
    url = f"{BASE}/search/all"
    r = requests.get(url, params={"q": q}, headers=HEADERS, timeout=timeout_s)
    r.raise_for_status()
    results = r.json().get("results", [])
    # Prefer football teams.
    best: Optional[Tuple[int, str]] = None
    for item in results:
        if item.get("type") != "team":
            continue
        ent = item.get("entity", {}) or {}
        sport = ent.get("sport", {}) or {}
        if sport.get("id") != 1:
            continue
        tid = ent.get("id")
        name = ent.get("name") or ""
        if tid is None:
            continue
        # Exact match first.
        if _norm(name) == _norm(q):
            return int(tid), name
        if best is None:
            best = (int(tid), name)
    return best


def resolve_team_id(base_dir: str, league_code: str, team_name: str, aliases: Optional[List[str]] = None) -> Dict[str, Any]:
    """Resolve team id by (cached) mapping or search endpoint.

    Returns:
      {"ok": bool, "team_id": int|None, "name": str, "query": str, "source": "...", "error": str|None}
    """
    league_code = (league_code or "").strip()
    team_name = (team_name or "").strip()
    queries: List[str] = []
    if team_name:
        queries.append(team_name)
    if aliases:
        for a in aliases:
            a = (a or "").strip()
            if a and a not in queries:
                queries.append(a)

    cache = _load_team_id_cache(base_dir)
    league_cache = cache.get(league_code, {}) if isinstance(cache.get(league_code), dict) else {}

    # Cache hit by normalized key.
    key = _norm(team_name)
    if key and isinstance(league_cache.get(key), dict):
        row = league_cache[key]
        tid = row.get("team_id")
        name = row.get("name") or team_name
        if isinstance(tid, int):
            return {"ok": True, "team_id": tid, "name": name, "query": team_name, "source": "cache", "error": None}

    last_err = None
    for q in queries:
        try:
            found = _search_team_id(q)
            if not found:
                continue
            tid, name = found
            league_cache[key or _norm(q)] = {"team_id": int(tid), "name": name, "query": q, "saved_at": _utc_now_iso()}
            cache[league_code] = league_cache
            _save_team_id_cache(base_dir, cache)
            return {"ok": True, "team_id": int(tid), "name": name, "query": q, "source": "search", "error": None}
        except Exception as e:
            last_err = str(e)
            continue

    return {"ok": False, "team_id": None, "name": team_name, "query": team_name, "source": "search", "error": last_err or "not found"}


def _get_team_events(team_id: int, kind: str = "last", page: int = 0, timeout_s: int = 20) -> List[Dict[str, Any]]:
    """kind: last|next. Returns raw events."""
    url = f"{BASE}/team/{team_id}/events/{kind}/{page}"
    r = requests.get(url, headers=HEADERS, timeout=timeout_s)
    r.raise_for_status()
    data = r.json()
    events = data.get("events")
    return events if isinstance(events, list) else []


def _get_event_lineups(event_id: int, timeout_s: int = 20) -> Optional[Dict[str, Any]]:
    url = f"{BASE}/event/{event_id}/lineups"
    r = requests.get(url, headers=HEADERS, timeout=timeout_s)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _get_event_statistics(event_id: int, timeout_s: int = 20) -> Optional[Dict[str, Any]]:
    url = f"{BASE}/event/{event_id}/statistics"
    r = requests.get(url, headers=HEADERS, timeout=timeout_s)
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


def _extract_possession_percent(stats_payload: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Return (home_possession, away_possession) if present."""
    if not isinstance(stats_payload, dict):
        return None
    # Sofascore usually returns {"statistics":[{"period":"ALL","groups":[...]}]}
    blocks = stats_payload.get("statistics")
    if not isinstance(blocks, list):
        return None
    for blk in blocks:
        groups = blk.get("groups")
        if not isinstance(groups, list):
            continue
        for g in groups:
            items = g.get("statisticsItems")
            if not isinstance(items, list):
                continue
            for it in items:
                name = str(it.get("name") or "")
                if "possession" not in name.lower():
                    continue
                # values can be '55%' or 55
                hv = it.get("home")
                av = it.get("away")
                try:
                    hs = float(str(hv).replace("%", "").strip())
                    as_ = float(str(av).replace("%", "").strip())
                    if 0 <= hs <= 100 and 0 <= as_ <= 100:
                        return hs, as_
                except Exception:
                    continue
    return None


def _extract_lineup_for_team(lineups_payload: Dict[str, Any], team_id: int) -> Optional[Dict[str, Any]]:
    """Return lineup summary for the given team in an event."""
    if not isinstance(lineups_payload, dict):
        return None
    home = lineups_payload.get("home") if isinstance(lineups_payload.get("home"), dict) else None
    away = lineups_payload.get("away") if isinstance(lineups_payload.get("away"), dict) else None
    home_tid = ((lineups_payload.get("homeTeam") or {}) if isinstance(lineups_payload.get("homeTeam"), dict) else {}).get("id")
    away_tid = ((lineups_payload.get("awayTeam") or {}) if isinstance(lineups_payload.get("awayTeam"), dict) else {}).get("id")
    side = None
    block = None
    if isinstance(home_tid, int) and home_tid == team_id:
        side = "home"
        block = home
    elif isinstance(away_tid, int) and away_tid == team_id:
        side = "away"
        block = away
    else:
        # fallback: infer by comparing nested ids
        if isinstance(home, dict) and ((home.get("team") or {}).get("id") == team_id):
            side = "home"
            block = home
        if isinstance(away, dict) and ((away.get("team") or {}).get("id") == team_id):
            side = "away"
            block = away
    if not (side and isinstance(block, dict)):
        return None

    formation = block.get("formation") or ""
    players = block.get("players") if isinstance(block.get("players"), list) else []
    starters: List[Dict[str, Any]] = []
    for p in players:
        if not isinstance(p, dict):
            continue
        if p.get("substitute") is True:
            continue
        ent = p.get("player") if isinstance(p.get("player"), dict) else {}
        pid = ent.get("id")
        name = ent.get("name") or ent.get("shortName") or ""
        rating = None
        # rating might sit under statistics.rating
        stats = p.get("statistics") if isinstance(p.get("statistics"), dict) else {}
        if "rating" in stats:
            try:
                rating = float(stats.get("rating"))
            except Exception:
                rating = None
        starters.append({"player_id": pid, "name": name, "rating": rating})

    return {"side": side, "formation": str(formation or ""), "starters": starters}


def build_team_state(base_dir: str, league_code: str, team_name: str, aliases: Optional[List[str]] = None, last_n: int = 5) -> Dict[str, Any]:
    """Build recent-state summary for a team.

    Returns a dict with keys:
      ok, team_id, display_name, form, avg_possession, formations, last_lineup, key_players
    """
    diag: Dict[str, Any] = {"attempted": True, "ok": False, "team_name": team_name, "errors": []}
    resolved = resolve_team_id(base_dir, league_code, team_name, aliases=aliases)
    diag["resolve"] = resolved
    if not resolved.get("ok") or not isinstance(resolved.get("team_id"), int):
        diag["errors"].append({"step": "resolve_team_id", "error": resolved.get("error")})
        return {"ok": False, "diag": diag}

    team_id = int(resolved["team_id"])
    display_name = resolved.get("name") or team_name

    try:
        events = _get_team_events(team_id, kind="last", page=0)
    except Exception as e:
        diag["errors"].append({"step": "get_team_events", "error": str(e)})
        return {"ok": False, "team_id": team_id, "name": display_name, "diag": diag}

    # Keep finished events only.
    finished: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        st = (ev.get("status") or {}) if isinstance(ev.get("status"), dict) else {}
        if st.get("type") == "finished":
            finished.append(ev)
    finished = finished[: max(1, int(last_n))]

    points = 0
    gf = 0
    ga = 0
    possessions: List[float] = []
    formation_counts: Dict[str, int] = {}
    player_ratings: Dict[str, List[float]] = {}
    last_lineup: Optional[Dict[str, Any]] = None

    for ev in finished:
        event_id = ev.get("id")
        home_team = (ev.get("homeTeam") or {}) if isinstance(ev.get("homeTeam"), dict) else {}
        is_home = home_team.get("id") == team_id
        # scores
        hs = ((ev.get("homeScore") or {}) if isinstance(ev.get("homeScore"), dict) else {}).get("current")
        as_ = ((ev.get("awayScore") or {}) if isinstance(ev.get("awayScore"), dict) else {}).get("current")
        try:
            hs_i = int(hs)
            as_i = int(as_)
        except Exception:
            hs_i = None
            as_i = None
        if isinstance(hs_i, int) and isinstance(as_i, int):
            if is_home:
                gf += hs_i
                ga += as_i
            else:
                gf += as_i
                ga += hs_i

        # winnerCode: 1 home, 2 away, 3 draw
        wc = ev.get("winnerCode")
        if wc == 3:
            points += 1
        elif (wc == 1 and is_home) or (wc == 2 and not is_home):
            points += 3

        if isinstance(event_id, int):
            # possession
            try:
                sp = _get_event_statistics(event_id)
                poss = _extract_possession_percent(sp) if isinstance(sp, dict) else None
                if poss:
                    home_poss, away_poss = poss
                    possessions.append(float(home_poss if is_home else away_poss))
            except Exception:
                pass

            # lineup
            try:
                lp = _get_event_lineups(event_id)
                lineup = _extract_lineup_for_team(lp, team_id) if isinstance(lp, dict) else None
                if lineup:
                    formation = (lineup.get("formation") or "").strip()
                    if formation:
                        formation_counts[formation] = formation_counts.get(formation, 0) + 1
                    if last_lineup is None:
                        last_lineup = lineup
                    for s in lineup.get("starters") or []:
                        if not isinstance(s, dict):
                            continue
                        name = str(s.get("name") or "").strip()
                        rating = s.get("rating")
                        if name and isinstance(rating, (int, float)):
                            player_ratings.setdefault(name, []).append(float(rating))
            except Exception:
                pass

        # be nice to the upstream API
        time.sleep(0.1)

    avg_poss = round(sum(possessions) / len(possessions), 2) if possessions else None
    formations_sorted = sorted(formation_counts.items(), key=lambda x: (-x[1], x[0]))[:3]
    formations = [{"formation": k, "count": v} for k, v in formations_sorted]

    key_players = []
    for name, rs in player_ratings.items():
        if not rs:
            continue
        key_players.append({"name": name, "avg_rating": round(sum(rs) / len(rs), 2), "matches": len(rs)})
    key_players.sort(key=lambda x: (-x["avg_rating"], -x["matches"], x["name"]))
    key_players = key_players[:8]

    out = {
        "ok": True,
        "team_id": team_id,
        "name": display_name,
        "recent": {
            "matches": len(finished),
            "points": points,
            "gf": gf,
            "ga": ga,
        },
        "avg_possession": avg_poss,
        "formations": formations,
        "last_lineup": last_lineup,
        "key_players": key_players,
        "source": "sofascore",
        "generated_at": _utc_now_iso(),
        "diag": diag,
    }
    out["diag"]["ok"] = True
    out["diag"]["team_id"] = team_id
    return out


def build_match_team_context(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    home_aliases: Optional[List[str]] = None,
    away_aliases: Optional[List[str]] = None,
    last_n: int = 5,
) -> Dict[str, Any]:
    home = build_team_state(base_dir, league_code, home_team, aliases=home_aliases, last_n=last_n)
    away = build_team_state(base_dir, league_code, away_team, aliases=away_aliases, last_n=last_n)
    return {
        "ok": bool(home.get("ok") and away.get("ok")),
        "source": "sofascore",
        "league": league_code,
        "home": home,
        "away": away,
        "generated_at": _utc_now_iso(),
    }
