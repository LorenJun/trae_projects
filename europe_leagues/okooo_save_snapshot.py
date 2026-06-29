#!/usr/bin/env python3
"""模块说明：抓取澳客单场赔率页面并落盘欧赔、亚盘、大小球与凯利快照。

Fetch an okooo match snapshot via the current formal mobile access strategy and save JSON to disk.

Output naming rule:
  赛事名称_时间.json
Example:
  巴黎圣曼vs南特_2026-04-21_23-10-05.json

Notes:
- The formal path defaults to `local-chrome`.
- The access strategy uses `iPhone Safari` mobile profiles plus `Referer: https://m.okooo.com/`.
- `browser-use` is kept only as an explicit debug driver, not the default formal path."""

from __future__ import annotations

import atexit
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from websocket import create_connection

from okooo_mobile_access import (
    OkoooMobileProfile,
    available_mobile_profiles,
    cache_busted_okooo_url,
    fresh_mobile_profile,
    is_okooo_mobile_url,
    mobile_headers,
    random_mobile_profile,
)
from runtime.okooo_access import (
    is_okooo_blocked_text,
    open_okooo_verification_breaker,
    read_okooo_verification_breaker,
    wait_for_okooo_market_slot,
)

REMEN_URL = "https://m.okooo.com/saishi/remen/"
# Retry faster by default. For transient rendering issues we still retry, but
# we avoid long fixed backoffs when the failure is clearly non-recoverable.
RETRY_DELAYS = [0.6, 1.4, 3.0]
CLICK_SETTLE_SECONDS = 2.5
# Extra dwell on the handicap page after parsing 亚盘 and before clicking the
# inner 大小球 tab, so the asian numbers settle and the tab switch is less likely
# to look like rapid automated clicking.
ASIAN_TO_TOTALS_DWELL_SECONDS = 2.0
# Before a freshly-opened (cookie-less) session deep-links into a match odds page
# (handicap.php / odds.php), first land on a shallow okooo page so cookies and a
# natural referer are established. Cold browsers that request a deep odds page as
# their very first navigation are the most obvious bot signal and get blocked.
WARM_LANDING_URL = "https://m.okooo.com/"
WARM_UP_DWELL_SECONDS = 2.0
MARKET_FAMILY_ODDS = "odds_family"
MARKET_FAMILY_ASIAN = "asian_family"
MARKET_FAMILY_TOTALS = "totals_family"
# Single shared breaker family for the all-markets hub session: when one warm
# session pulls 欧赔/凯利/亚值/大小球 off the match hub page, a verification wall
# blocks all of them at once, so they share one breaker key.
MARKET_FAMILY_HUB = "hub_family"
# Dwell after switching to each tab on the hub page before parsing, so the tab
# content renders and the click cadence stays human-like.
HUB_TAB_DWELL_SECONDS = 2.0
# The hub (history.php) exposes 亚指/欧指 as real navigation links that load the
# handicap.php / odds.php pages. Clicking them triggers a full page load, so we
# wait longer than an in-page tab switch before parsing the destination.
HUB_NAV_SETTLE_SECONDS = 3.5
# Extra dwell applied right before parsing each market (亚值/大小球/欧赔/凯利) so
# the live odds table is fully rendered before we read it — this is on top of the
# click/navigation settle above. Freely configurable via the OKOOO_MARKET_DWELL
# env var or the --market-dwell CLI flag (which overrides this module global).
MARKET_PARSE_DWELL_SECONDS = float(os.environ.get("OKOOO_MARKET_DWELL", "5.0"))


def _parse_wait_ladder(raw: str, fallback: List[float]) -> List[float]:
    out: List[float] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            secs = float(part)
        except ValueError:
            continue
        if secs > 0:
            out.append(secs)
    return out or list(fallback)


# When odds.php lands on a verification wall, the wall is often transient. Before
# giving up on 欧赔/凯利 we re-enter the odds page with escalating waits between
# attempts (3s → 5s → 10s by default). Configurable via OKOOO_OUZHI_RETRY_WAITS
# (comma-separated seconds) or the --ouzhi-retry-waits CLI flag.
OUZHI_RETRY_WAITS = _parse_wait_ladder(
    os.environ.get("OKOOO_OUZHI_RETRY_WAITS", "3,5,10"), [3.0, 5.0, 10.0]
)

# Single-machine, fixed-IP抗封策略：所有深链之间用一道进程级最小间隔闸门约束节奏，
# 无论上游是哪个模型/agent、并发多猛，下游访问频率恒定温柔。固定 IP 下「节奏」是
# 被风控识别的首要信号，比指纹更关键。可用 OKOOO_MIN_REQUEST_INTERVAL 配置秒数。
OKOOO_MIN_REQUEST_INTERVAL = float(
    os.environ.get("OKOOO_MIN_REQUEST_INTERVAL", "2.5")
)


def _jittered(seconds: float, *, ratio: float = 0.35) -> float:
    """Return seconds perturbed by ±ratio random jitter (never below 0).

    Fixed wait cadences are themselves a machine fingerprint. Adding bounded
    randomness to every backoff/dwell makes the retry rhythm unpredictable and
    more human-like — important on a fixed IP where behaviour, not the device
    fingerprint, is what the anti-bot wall scores.
    """
    base = max(0.0, float(seconds))
    if base <= 0:
        return 0.0
    delta = base * max(0.0, ratio)
    return max(0.0, base + random.uniform(-delta, delta))


_LAST_DEEP_NAV_AT = 0.0


def _global_pace_gate() -> float:
    """Process-level minimum interval between deep navigations.

    On a single machine with a fixed IP the dominant block trigger is request
    *cadence*: some callers fire deep links back-to-back and push the IP past
    okooo's frequency threshold. This gate forces a jittered minimum gap before
    every deep navigation regardless of which model/agent drives the run, so the
    downstream access rhythm stays uniformly gentle no matter how bursty the
    upstream is. Returns the seconds actually slept.
    """
    global _LAST_DEEP_NAV_AT
    interval = _jittered(OKOOO_MIN_REQUEST_INTERVAL)
    now = time.time()
    wait = max(0.0, interval - max(0.0, now - _LAST_DEEP_NAV_AT))
    if wait > 0:
        time.sleep(wait)
    _LAST_DEEP_NAV_AT = time.time()
    return wait


def _default_data_root() -> Path:
    """Return a project-relative data directory for scraper artifacts.

    This avoids hardcoding user home paths and keeps paths consistent with
    prediction workflows. The directory is expected to be gitignored.
    """
    return Path(__file__).resolve().parent / ".okooo-scraper"


def _league_slug(league: str) -> str:
    """Normalize league name to a directory-friendly slug.

    For five major leagues we use stable english slugs for easier management.
    """
    name = (league or "").strip()
    mapping = {
        "英超": "premier_league",
        "意甲": "serie_a",
        "西甲": "la_liga",
        "德甲": "bundesliga",
        "法甲": "ligue_1",
        "世界杯": "world_cup",
        "友谊赛": "friendly",
        "friendly": "friendly",
        "world_cup": "world_cup",
        "欧联": "europa_league",
        "欧罗巴": "europa_league",
        "欧冠": "champions_league",
        "欧协联": "conference_league",
        "瑞超": "allsvenskan",
        "瑞典超": "allsvenskan",
        "挪超": "eliteserien",
        "芬超": "veikkausliiga",
    }
    if name in mapping:
        return mapping[name]
    # Fallback for other leagues: keep it readable but safe for filesystem
    return _safe_filename(name).lower() or "other"


def _normalize_okooo_league_name(league: str) -> str:
    """Map project league labels/codes to the labels shown on okooo pages."""
    text = (league or "").strip()
    mapping = {
        "world_cup": "世界杯",
        "friendly": "友谊赛",
        "friendlies": "友谊赛",
        "international_friendly": "友谊赛",
        "友谊赛": "友谊赛",
        "allsvenskan": "瑞典超",
        "eliteserien": "挪超",
        "veikkausliiga": "芬超",
        "瑞超": "瑞典超",
    }
    return mapping.get(text, text)


SCHEDULE_PAGE_LEAGUE_SLUGS = {
    "premier_league",
    "la_liga",
    "serie_a",
    "bundesliga",
    "ligue_1",
}


def _prefers_online_team_search(league: str) -> bool:
    return _mobile_league_url(league) is None


def _now_stamp() -> str:
    # Avoid ":" for cross-platform filenames.
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _safe_filename(s: str) -> str:
    s = (s or "").strip()
    s = s.replace(" ", "")
    # Windows/macOS reserved characters
    s = re.sub(r'[<>:"/\\\\|?*]+', "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "snapshot"

def _norm_team_tokens(name: str) -> list[str]:
    """Generate fuzzy-match tokens for a team name as shown on okooo schedule pages."""
    n = (name or "").strip().replace(" ", "")
    if not n:
        return []
    tokens = {n}
    # Common suffixes/words that may be omitted on schedule pages.
    for suf in ["足球俱乐部", "俱乐部", "足球", "队", "FC", "fc", "竞技", "竞技队"]:
        if n.endswith(suf) and len(n) > len(suf):
            tokens.add(n[: -len(suf)])
    # Some teams are commonly abbreviated by dropping the last 1-2 chars.
    if len(n) >= 4:
        tokens.add(n[:-1])
    if len(n) >= 5:
        tokens.add(n[:-2])
    # Remove very short tokens to reduce false positives.
    return sorted([t for t in tokens if len(t) >= 2], key=len, reverse=True)


def _date_tokens(date_yyyy_mm_dd: str) -> list[str]:
    """Convert YYYY-MM-DD to likely fragments shown in schedule rows, e.g. '04-22'."""
    if not date_yyyy_mm_dd:
        return []
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_yyyy_mm_dd.strip())
    if not m:
        return []
    _y, mm, dd = m.group(1), m.group(2), m.group(3)
    return [f"{mm}-{dd}", f"{int(mm)}-{int(dd)}"]


def _time_tokens(hh_mm: str) -> list[str]:
    """Convert HH:MM to likely fragments shown in schedule rows, e.g. '03:00' / '3:00'."""
    if not hh_mm:
        return []
    m = re.match(r"^(\d{1,2}):(\d{2})$", hh_mm.strip())
    if not m:
        return []
    hh = int(m.group(1))
    mm = m.group(2)
    tokens = [f"{hh:02d}:{mm}", f"{hh}:{mm}"]
    # Some schedule pages render midnight kickoffs as 24:00 instead of 00:00.
    if hh == 0 and mm == "00":
        tokens.extend(["24:00", "24"])
    seen = set()
    uniq = []
    for item in tokens:
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _candidate_date_hints(date_yyyy_mm_dd: str, hh_mm: str = "", strict_identity: bool = False) -> list[str]:
    """Return candidate dates for schedule matching.

    Some overnight matches are stored in local league files as the next calendar
    day (e.g. `2026-05-04 02:45`) while the user may ask with the competition
    day (`2026-05-03`). To avoid missing such rows, keep the original date
    first, then add adjacent-day fallbacks for early-morning kickoffs.
    """
    raw = (date_yyyy_mm_dd or "").strip()
    if not raw:
        return []
    out = [raw]
    if strict_identity:
        return out
    try:
        base = datetime.strptime(raw, "%Y-%m-%d")
    except Exception:
        return out

    hour = None
    if hh_mm:
        m = re.match(r"^(\d{1,2}):(\d{2})$", hh_mm.strip())
        if m:
            hour = int(m.group(1))

    # Overnight games are commonly described by the previous "match day" or by
    # the actual after-midnight calendar day across different data sources.
    if hour is not None and hour <= 5:
        out.append((base + timedelta(days=1)).strftime("%Y-%m-%d"))
        out.append((base - timedelta(days=1)).strftime("%Y-%m-%d"))
    else:
        out.append((base - timedelta(days=1)).strftime("%Y-%m-%d"))
        out.append((base + timedelta(days=1)).strftime("%Y-%m-%d"))

    seen = set()
    uniq = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def _target_year_month(date_yyyy_mm_dd: str) -> Optional[tuple[int, int]]:
    if not date_yyyy_mm_dd:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_yyyy_mm_dd.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _read_schedule_month_state(bu: Any) -> Dict[str, Any]:
    js = r"""
(() => {
  const nodes = Array.from(document.querySelectorAll('a,div,span,button,li,p,em,strong,h1,h2,h3'));
  let best = null;
  for (const el of nodes) {
    const text = String(el?.innerText || '').replace(/\s+/g, '').trim();
    if (!text || text.length > 24) continue;
    const m = text.match(/(\d{4})年(\d{1,2})月/);
    if (!m) continue;
    const candidate = {
      text,
      year: Number(m[1]),
      month: Number(m[2]),
      tag: el?.tagName || '',
    };
    if (!best || candidate.text.length < best.text.length) {
      best = candidate;
    }
  }
  if (!best) return JSON.stringify({found:false});
  return JSON.stringify({
    found: true,
    text: best.text,
    year: best.year,
    month: best.month,
    tag: best.tag,
  });
})()
"""
    result = bu.eval_json(js)
    if isinstance(result, dict):
        return result
    return {"found": False}


def _click_schedule_month_nav(bu: Any, direction: str) -> Dict[str, Any]:
    label = "下月" if direction == "next" else "上月"
    js = r"""
(() => {
  const label = %s;
  const nodes = Array.from(document.querySelectorAll('a,div,span,button,li,p,em,strong'));
  const candidates = nodes
    .map(el => ({el, text: String(el?.innerText || '').replace(/\s+/g, '').trim()}))
    .filter(x => x.text && x.text.includes(label))
    .filter(x => x.text.length <= 12)
    .sort((a, b) => a.text.length - b.text.length);
  if (!candidates.length) {
    return JSON.stringify({clicked:false, direction:label, reason:'nav_not_found'});
  }
  const target = candidates[0];
  target.el.click();
  return JSON.stringify({
    clicked: true,
    direction: label,
    text: target.text,
    tag: target.el?.tagName || '',
  });
})()
""" % json.dumps(label, ensure_ascii=False)
    result = bu.eval_json(js)
    if isinstance(result, dict):
        return result
    return {"clicked": False, "direction": label, "reason": "invalid_click_result"}


def _navigate_schedule_to_month(bu: Any, date_yyyy_mm_dd: str, max_steps: int = 18) -> Dict[str, Any]:
    target = _target_year_month(date_yyyy_mm_dd)
    if not target:
        return {
            "attempted": False,
            "matched": False,
            "target_date": date_yyyy_mm_dd,
            "reason": "invalid_target_date",
        }

    target_year, target_month = target
    state = _read_schedule_month_state(bu)
    history: list[Dict[str, Any]] = []

    for step in range(max_steps + 1):
        if isinstance(state, dict) and state.get("found"):
            current_year = int(state.get("year") or 0)
            current_month = int(state.get("month") or 0)
            if current_year == target_year and current_month == target_month:
                return {
                    "attempted": True,
                    "matched": True,
                    "target_date": date_yyyy_mm_dd,
                    "target_year": target_year,
                    "target_month": target_month,
                    "steps": step,
                    "final_state": state,
                    "history": history,
                }
            delta_months = (target_year - current_year) * 12 + (target_month - current_month)
            direction = "next" if delta_months > 0 else "prev"
        else:
            return {
                "attempted": True,
                "matched": False,
                "target_date": date_yyyy_mm_dd,
                "target_year": target_year,
                "target_month": target_month,
                "steps": step,
                "final_state": state if isinstance(state, dict) else {"found": False},
                "history": history,
                "reason": "month_label_not_found",
            }

        if step >= max_steps:
            break

        click_result = _click_schedule_month_nav(bu, direction)
        history.append(
            {
                "step": step + 1,
                "direction": direction,
                "before": state,
                "click": click_result,
            }
        )
        if not isinstance(click_result, dict) or click_result.get("clicked") is not True:
            return {
                "attempted": True,
                "matched": False,
                "target_date": date_yyyy_mm_dd,
                "target_year": target_year,
                "target_month": target_month,
                "steps": step,
                "final_state": state,
                "history": history,
                "reason": f"{direction}_nav_not_found",
            }
        time.sleep(1.4)
        state = _read_schedule_month_state(bu)

    return {
        "attempted": True,
        "matched": False,
        "target_date": date_yyyy_mm_dd,
        "target_year": target_year,
        "target_month": target_month,
        "steps": max_steps,
        "final_state": state if isinstance(state, dict) else {"found": False},
        "history": history,
        "reason": "max_steps_exceeded",
    }


def _click_schedule_date(bu: Any, date_yyyy_mm_dd: str) -> Dict[str, Any]:
    tokens = _date_tokens(date_yyyy_mm_dd)
    if not tokens:
        return {"clicked": False, "reason": "invalid_target_date", "target_date": date_yyyy_mm_dd}
    js = r"""
(() => {
  const tokens = %s;
  const nodes = Array.from(document.querySelectorAll('a,div,span,button,li,p,em,strong,td,th,h1,h2,h3'));
  const norm = (t) => String(t || '').replace(/\s+/g, '').trim();
  const candidates = [];
  for (const el of nodes) {
    const text = norm(el?.innerText);
    if (!text || text.length > 12) continue;
    if (!tokens.includes(text)) continue;
    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    const top = rect ? rect.top : 99999;
    const left = rect ? rect.left : 99999;
    const width = rect ? rect.width : 0;
    const height = rect ? rect.height : 0;
    const visible = rect ? (width > 0 && height > 0) : true;
    if (!visible) continue;
    candidates.push({
      el,
      text,
      top,
      left,
      width,
      height,
      tag: el?.tagName || '',
      nearTop: top >= -5 && top <= 260,
    });
  }
  candidates.sort((a, b) => {
    if (Number(b.nearTop) !== Number(a.nearTop)) return Number(b.nearTop) - Number(a.nearTop);
    if (a.top !== b.top) return a.top - b.top;
    if (a.left !== b.left) return a.left - b.left;
    return a.text.length - b.text.length;
  });
  if (!candidates.length) {
    return JSON.stringify({clicked:false, tokens, reason:'date_tab_not_found'});
  }
  const target = candidates[0];
  target.el.click();
  return JSON.stringify({
    clicked: true,
    token: target.text,
    text: target.text,
    top: target.top,
    left: target.left,
    tag: target.tag,
    near_top: target.nearTop,
  });
})()
""" % json.dumps(tokens, ensure_ascii=False)
    result = bu.eval_json(js)
    if isinstance(result, dict):
        return result
    return {"clicked": False, "reason": "invalid_click_result", "target_date": date_yyyy_mm_dd}


def _alias_table_path() -> str:
    return str(Path(__file__).resolve().parent / "okooo_team_aliases.json")


def _load_alias_table() -> Dict[str, Any]:
    """Load team alias table for fuzzy matching schedule rows (best-effort)."""
    path = _alias_table_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _schedule_cache_path(league: str, match_date: str) -> Path:
    return _default_data_root() / "schedules" / _league_slug(league) / f"{match_date}.json"


def _ensure_daily_schedule_cache(league: str, match_date: str) -> Optional[Path]:
    path = _schedule_cache_path(league, match_date)
    if path.exists():
        return path
    script_path = Path(__file__).resolve().parent / "okooo_fetch_daily_schedule.py"
    if not script_path.exists():
        return None
    try:
        cp = subprocess.run(
            ["python3", str(script_path), "--league", _normalize_okooo_league_name(league), "--date", match_date],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception:
        return None
    if cp.returncode == 0 and path.exists():
        return path
    return None


def _find_match_id_from_schedule_cache(
    league: str,
    team1: str,
    team2: str,
    candidate_dates: list[str],
    alias_table: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    from enhanced_prediction_workflow import validate_schedule_cache_payload

    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    best: Dict[str, Any] = {}
    best_score = -1.0
    for current_date in candidate_dates or []:
        path = _ensure_daily_schedule_cache(league, current_date)
        if not path or not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        validation = validate_schedule_cache_payload(
            payload,
            league_code=_league_slug(league),
            match_date=current_date,
        )
        if validation.get("status") != "valid":
            continue
        for row in payload.get("matches") or []:
            if not isinstance(row, dict):
                continue
            home_compact = re.sub(r"\s+", "", str(row.get("home_team") or ""))
            away_compact = re.sub(r"\s+", "", str(row.get("away_team") or ""))
            raw_compact = re.sub(r"\s+", "", str(row.get("raw_text") or row.get("text") or ""))
            if not (home_compact or raw_compact) or not (away_compact or raw_compact):
                continue
            # Some cup schedule rows expose the stage (e.g. "决赛") as home_team,
            # while the actual home team only appears in raw_text.
            home_match = any(tok and (home_compact.find(tok) >= 0 or raw_compact.find(tok) >= 0) for tok in t1_tokens)
            away_match = any(tok and (away_compact.find(tok) >= 0 or raw_compact.find(tok) >= 0) for tok in t2_tokens)
            if not home_match or not away_match:
                continue
            score = 20.0 + (0.5 if current_date else 0.0)
            if score <= best_score:
                continue
            best_score = score
            normalized_row = dict(row)
            if not normalized_row.get("href") and normalized_row.get("history_url"):
                normalized_row["href"] = normalized_row.get("history_url")
            best = {
                "match_id": str(row.get("match_id") or ""),
                "schedule_row": normalized_row,
                "_matched_date_hint": current_date,
                "_source": "daily_schedule_cache",
            }
    return best if best.get("match_id") else {}


def _find_match_id_via_online_search(
    league: str,
    team1: str,
    team2: str,
    alias_table: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Fallback to direct online lookup when schedule cache is unavailable."""
    try:
        from okooo_match_finder import OkoooMatchFinder
    except Exception:
        return {}

    alias_table = alias_table or {}
    finder = OkoooMatchFinder()
    league_hint = _normalize_okooo_league_name(league)
    team1_candidates = _team_aliases(alias_table, league, team1) or [team1]
    team2_candidates = _team_aliases(alias_table, league, team2) or [team2]

    seen_pairs: set[tuple[str, str]] = set()
    for home_name in team1_candidates:
        for away_name in team2_candidates:
            pair = (str(home_name).strip(), str(away_name).strip())
            if not pair[0] or not pair[1] or pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            try:
                match_id = finder.find_match_id(pair[0], pair[1], league_hint=league_hint)
            except Exception:
                continue
            if not match_id:
                continue
            return {
                "match_id": str(match_id),
                "schedule_row": {
                    "mid": str(match_id),
                    "href": f"https://m.okooo.com/match/history.php?MatchID={match_id}",
                    "text": f"{pair[0]} vs {pair[1]}",
                },
                "_source": "online_match_finder",
                "_matched_alias_pair": {"home_team": pair[0], "away_team": pair[1]},
            }
    return {}


def _team_aliases(alias_table: Dict[str, Any], league: str, team_name: str) -> list[str]:
    if not team_name:
        return []
    league_slug = _league_slug(league)
    league_keys = [
        str(league or "").strip(),
        league_slug,
    ]
    if league_slug == "europa_league":
        league_keys.extend(["欧联", "欧罗巴"])
    elif league_slug == "champions_league":
        league_keys.extend(["欧冠"])
    elif league_slug == "conference_league":
        league_keys.extend(["欧协联"])
    elif league_slug == "world_cup":
        league_keys.extend(["世界杯"])
    elif league_slug == "friendly":
        league_keys.extend(["友谊赛"])

    out = [team_name]
    if isinstance(alias_table, dict):
        for league_key in league_keys:
            league_map = alias_table.get(league_key, {})
            aliases = league_map.get(team_name, []) if isinstance(league_map, dict) else []
            for a in aliases or []:
                if a and isinstance(a, str):
                    out.append(a)
        if league_slug in ("world_cup", "friendly"):
            for league_key, league_map in alias_table.items():
                if not isinstance(league_map, dict):
                    continue
                for canonical_name, aliases in league_map.items():
                    alias_values = aliases if isinstance(aliases, list) else []
                    names = [str(canonical_name or "").strip()] + [str(item or "").strip() for item in alias_values]
                    if str(team_name).strip() not in names:
                        continue
                    for item in names:
                        if item:
                            out.append(item)
    seen = set()
    uniq = []
    for x in out:
        x = x.strip()
        if not x or x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def _norm_team_tokens_multi(names: list[str]) -> list[str]:
    """Generate fuzzy-match tokens for multiple aliases."""
    tokens: set[str] = set()
    for name in names or []:
        for tok in _norm_team_tokens(name):
            tokens.add(tok)
    # Prefer longer tokens first to reduce false positives.
    return sorted([t for t in tokens if len(t) >= 2], key=len, reverse=True)



def _pick_best_token_hit(compact_text: str, tokens: list[str]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for token in tokens or []:
        position = compact_text.find(token)
        if position < 0:
            continue
        candidate = {"token": token, "position": position, "length": len(token)}
        if best is None:
            best = candidate
            continue
        if candidate["length"] > best["length"]:
            best = candidate
            continue
        if candidate["length"] == best["length"] and candidate["position"] < best["position"]:
            best = candidate
    return best



def _select_best_schedule_row(
    rows: list[Dict[str, Any]],
    *,
    team1: str,
    team2: str,
    league: str = "",
    alias_table: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    alias_table = alias_table or {}
    team1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    team2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    best_row: Optional[Dict[str, Any]] = None
    best_score: Optional[tuple] = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        compact_text = re.sub(r"\s+", "", str(row.get("text") or "")).strip()
        if not compact_text:
            continue
        team1_hit = _pick_best_token_hit(compact_text, team1_tokens)
        team2_hit = _pick_best_token_hit(compact_text, team2_tokens)
        if not team1_hit or not team2_hit:
            continue
        ordered = int(team1_hit["position"] <= team2_hit["position"])
        if ordered:
            gap = max(0, team2_hit["position"] - (team1_hit["position"] + team1_hit["length"]))
        else:
            gap = 9999
        broad_round_penalty = compact_text.count("第")
        score = (
            ordered,
            team1_hit["length"] + team2_hit["length"],
            -gap,
            -max(0, len(compact_text) - 48),
            -broad_round_penalty,
        )
        if best_score is None or score > best_score:
            best_row = row
            best_score = score
    return best_row



def _is_broad_round_summary_row(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False
    compact_text = re.sub(r"\s+", "", str(row.get("text") or "")).strip()
    if not compact_text:
        return False
    return compact_text.count("第") >= 3 and len(compact_text) >= 60



def _eval_scroll_to_bottom(bu: Any) -> None:
    bu.eval_json("(() => { window.scrollTo(0, document.body.scrollHeight); return JSON.stringify({ok:true}); })()")
    time.sleep(1.2)


def _eval_scroll_to_top(bu: Any) -> None:
    bu.eval_json("(() => { window.scrollTo(0, 0); return JSON.stringify({ok:true}); })()")
    time.sleep(0.8)


def _extract_schedule_rows_for_date_on_current_page(
    bu: Any,
    date_hint: str = "",
    limit: int = 100,
) -> Dict[str, Any]:
    d_tokens = _date_tokens(date_hint)
    js = r"""
(() => {
  const dateFull = %s;
  const tokens = %s;
  const limit = %d;
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const compact = (s) => String(s || '').replace(/\s+/g, '').trim();
  const hasDateToken = (text) => {
    const c = compact(text);
    if (dateFull && c.includes(dateFull)) return true;
    return (tokens || []).some(tok => tok && c.includes(tok));
  };

  const blocks = Array.from(document.querySelectorAll('div,section,li,tr,tbody'))
    .map(el => {
      const text = norm(el.innerText);
      const itemCount = el.querySelectorAll('.item').length;
      const matchAnchorCount = el.querySelectorAll("a[href*='MatchID='],a[href*='matchid='],[onclick*='MatchID='],[onclick*='matchid=']").length;
      return {el, text, itemCount, matchAnchorCount};
    })
    .filter(x => x.text && x.text.length < 1500)
    .filter(x => hasDateToken(x.text));

  blocks.sort((a, b) => {
    if (b.itemCount !== a.itemCount) return b.itemCount - a.itemCount;
    if (b.matchAnchorCount !== a.matchAnchorCount) return b.matchAnchorCount - a.matchAnchorCount;
    return a.text.length - b.text.length;
  });
  let root = null;
  for (const block of blocks) {
    if (block.itemCount >= 2 || block.matchAnchorCount >= 2) {
      root = block.el;
      break;
    }
  }
  if (!root && blocks.length) root = blocks[0].el;
  if (!root) {
    return JSON.stringify({count: 0, rows: [], mode: 'date_section_missing'});
  }

  const dateTitleNode = Array.from(root.children || [])
    .find(el => hasDateToken(el?.innerText || ''));
  const sectionDateText = norm(dateTitleNode?.innerText || dateFull || '');

  const out = [];
  const seen = new Set();
  const itemNodes = Array.from(root.querySelectorAll('.item'));
  for (const item of itemNodes) {
    const text = norm(item.innerText);
    if (!text) continue;
    const anchor =
      item.querySelector("a.middle[href*='MatchID='], a.middle[href*='matchid=']") ||
      item.querySelector("a.arraw[href*='MatchID='], a.arraw[href*='matchid=']") ||
      item.querySelector("a[href*='MatchID='], a[href*='matchid=']");
    if (!anchor) continue;
    let href = anchor.getAttribute('href') || '';
    const midMatch = href.match(/MatchID=(\d+)/i) || href.match(/matchid=(\d+)/i);
    const mid = midMatch ? midMatch[1] : '';
    if (!mid || seen.has(mid)) continue;
    if (href.startsWith('/')) href = location.origin + href;
    out.push({
      mid,
      href,
      text: `${sectionDateText} ${text}`.trim(),
    });
    seen.add(mid);
    if (out.length >= limit) break;
  }

  if (!out.length) {
    const anchors = Array.from(root.querySelectorAll("a[href*='MatchID='],a[href*='matchid='],[onclick*='MatchID='],[onclick*='matchid=']")).slice(0, limit * 4);
    for (const el of anchors) {
      const attrs = [el.getAttribute?.('href') || '', el.getAttribute?.('onclick') || '', el.getAttribute?.('data-href') || ''].join(' ');
      const midMatch = attrs.match(/MatchID=(\d+)/i) || attrs.match(/matchid=(\d+)/i);
      const mid = midMatch ? midMatch[1] : '';
      if (!mid || seen.has(mid)) continue;
      const row = el.closest('.item') || el.closest('li') || el.closest('tr') || el.parentElement;
      const text = norm(row?.innerText || el.innerText || '');
      if (!text) continue;
      let href = el.getAttribute?.('href') || '';
      if (!href || !/MatchID=/i.test(href)) {
        href = `https://m.okooo.com/match/history.php?MatchID=${mid}`;
      } else if (href.startsWith('/')) {
        href = location.origin + href;
      }
      out.push({
        mid,
        href,
        text: `${sectionDateText} ${text}`.trim(),
      });
      seen.add(mid);
      if (out.length >= limit) break;
    }
  }

  return JSON.stringify({count: out.length, rows: out.slice(0, limit), mode: 'date_section'});
})()
""" % (
        json.dumps(date_hint, ensure_ascii=False),
        json.dumps(d_tokens, ensure_ascii=False),
        limit,
    )
    return bu.eval_json(js)


def _find_rows_in_date_section(
    bu: Any,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    league: str = "",
    alias_table: Dict[str, Any] | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    tm_tokens = _time_tokens(time_hint)
    raw = _extract_schedule_rows_for_date_on_current_page(bu, date_hint=date_hint, limit=max(limit * 6, 30))
    rows = []
    for row in (raw or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        compact = re.sub(r"\s+", "", text).strip()
        if not compact:
            continue
        has_t1 = any(tok and tok in compact for tok in t1_tokens)
        has_t2 = any(tok and tok in compact for tok in t2_tokens)
        if not has_t1 or not has_t2:
            continue
        if tm_tokens and not any(tok and tok in compact for tok in tm_tokens):
            continue
        score = 20.0 + (8.0 if tm_tokens else 0.0) + min(len(compact), 80) / 100.0
        new_row = dict(row)
        new_row["score"] = score
        rows.append(new_row)
    rows.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return {"count": len(rows), "rows": rows[:limit], "mode": (raw or {}).get("mode", "date_section")}


def _find_rows_fuzzy(
    bu: Any,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    league: str = "",
    alias_table: Dict[str, Any] | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    d_tokens = _date_tokens(date_hint)
    tm_tokens = _time_tokens(time_hint)
    js = r"""
(() => {
  const t1 = %s;
  const t2 = %s;
  const ds = %s;
  const ts = %s;
  const requireDate = %s;
  const requireTime = %s;
  const rows = Array.from(document.querySelectorAll("a[href*='history.php?MatchID=']"))
    .map(a => {
      const m = a.href.match(/MatchID=(\d+)/);
      const mid = m ? m[1] : null;
      const row = a.closest('li') || a.closest('tr') || a.closest('div');
      const text = row ? (row.innerText || '').replace(/\s+/g,' ').trim() : '';
      if (!mid) return null;
      const compact = text.replace(/\s+/g,'');
      const hasAny = (tokens) => tokens.some(tok => tok && compact.includes(tok));
      const hasDate = (ds.length ? hasAny(ds) : true);
      const hasTime = (ts.length ? hasAny(ts) : true);
      if (requireDate && !hasDate) return null;
      if (requireTime && !hasTime) return null;
      const score =
        (hasAny(t1) ? 10 : 0) +
        (hasAny(t2) ? 10 : 0) +
        (ds.length ? (hasDate ? 6 : 0) : 0) +
        (ts.length ? (hasTime ? 8 : 0) : 0) +
        Math.min(compact.length, 100) / 100.0;
      return { mid, href: a.href, text, score };
    })
    .filter(x => x && x.score >= 20)  // require both teams present; date/time handled above if required
    .sort((a,b) => b.score - a.score);
  return JSON.stringify({count: rows.length, rows: rows.slice(0, %d)});
})()
""" % (
        json.dumps(t1_tokens, ensure_ascii=False),
        json.dumps(t2_tokens, ensure_ascii=False),
        json.dumps(d_tokens, ensure_ascii=False),
        json.dumps(tm_tokens, ensure_ascii=False),
        "true" if bool(d_tokens) else "false",
        "true" if bool(tm_tokens) else "false",
        limit,
    )
    return bu.eval_json(js)


def _find_rows_anywhere_on_current_page(
    bu: Any,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    league: str = "",
    alias_table: Dict[str, Any] | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Search the current page for candidate matches and MatchID.

    Unlike `_find_rows_fuzzy`, this scans:
    - anchors with `history.php?MatchID=...`
    - any element containing `MatchID=...` in href/onclick/data-href
    - nearby container text

    This is more robust on cup/continental competition pages where the mobile
    schedule markup differs from domestic league pages.
    """
    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    d_tokens = _date_tokens(date_hint)
    tm_tokens = _time_tokens(time_hint)
    js = r"""
(() => {
  const t1 = %s;
  const t2 = %s;
  const ds = %s;
  const ts = %s;
  const requireDate = %s;
  const requireTime = %s;

  const norm = (s) => String(s || '').replace(/\s+/g, '').trim();
  const hasAny = (compact, tokens) => (tokens || []).some(tok => tok && compact.includes(tok));

  const nodes = Array.from(document.querySelectorAll('a,div,span,button,li,tr,td'));
  const seen = new Set();
  const rows = [];

  const extractMid = (el) => {
    const attrs = [];
    const collectAttrs = (node) => {
      if (!node || !node.getAttribute) return;
      attrs.push(node.getAttribute('href') || '');
      attrs.push(node.getAttribute('onclick') || '');
      attrs.push(node.getAttribute('data-href') || '');
      attrs.push(node.getAttribute('data-url') || '');
    };
    collectAttrs(el);
    if (el && el.querySelectorAll) {
      const inner = Array.from(el.querySelectorAll('a,[onclick],[data-href],[data-url]')).slice(0, 8);
      for (const node of inner) collectAttrs(node);
    }
    const combined = attrs.join(' ');
    const m = combined.match(/MatchID=(\d+)/i) || combined.match(/matchid=(\d+)/i);
    return m ? m[1] : null;
  };

  for (const el of nodes) {
    const mid = extractMid(el);
    if (!mid || seen.has(mid)) continue;
    const row = el.closest('li') || el.closest('tr') || el.closest('div') || el.parentElement;
    const text = (row?.innerText || el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const compact = norm(text);
    const hasT1 = hasAny(compact, t1);
    const hasT2 = hasAny(compact, t2);
    const hasDate = ds.length ? hasAny(compact, ds) : true;
    const hasTime = ts.length ? hasAny(compact, ts) : true;
    if (!hasT1 || !hasT2) continue;
    if (requireDate && !hasDate) continue;
    if (requireTime && !hasTime) continue;

    let href = '';
    if (el && el.getAttribute) href = el.getAttribute('href') || '';
    if ((!href || !/MatchID=/i.test(href)) && row && row.querySelector) {
      const a = row.querySelector("a[href*='MatchID='], a[href*='matchid=']");
      if (a && a.getAttribute) href = a.getAttribute('href') || href;
    }
    if (!href || !/MatchID=/i.test(href)) {
      href = `https://m.okooo.com/match/history.php?MatchID=${mid}`;
    } else if (href.startsWith('/')) {
      href = location.origin + href;
    }

    const score =
      10 +
      10 +
      (ds.length ? (hasDate ? 6 : 0) : 0) +
      (ts.length ? (hasTime ? 8 : 0) : 0) +
      Math.min(compact.length, 100) / 100.0;
    rows.push({ mid, href, text, score });
    seen.add(mid);
  }

  rows.sort((a, b) => b.score - a.score);
  return JSON.stringify({count: rows.length, rows: rows.slice(0, %d)});
})()
""" % (
        json.dumps(t1_tokens, ensure_ascii=False),
        json.dumps(t2_tokens, ensure_ascii=False),
        json.dumps(d_tokens, ensure_ascii=False),
        json.dumps(tm_tokens, ensure_ascii=False),
        "true" if bool(d_tokens) else "false",
        "true" if bool(tm_tokens) else "false",
        limit,
    )
    return bu.eval_json(js)


def _snapshot_identity_matches_request(
    payload: Dict[str, Any],
    *,
    home_team: str = "",
    away_team: str = "",
    match_date: str = "",
) -> bool:
    if not isinstance(payload, dict):
        return False
    if home_team and str(payload.get("home_team") or payload.get("team1") or "").strip() != str(home_team).strip():
        return False
    if away_team and str(payload.get("away_team") or payload.get("team2") or "").strip() != str(away_team).strip():
        return False
    if match_date and str(payload.get("match_date") or "").strip() != str(match_date).strip():
        return False
    return True



def _find_existing_snapshot_by_match_id(
    out_dir: Path,
    match_id: str,
    *,
    home_team: str = "",
    away_team: str = "",
    match_date: str = "",
) -> Optional[Path]:
    """Return an existing snapshot path only when both match_id and identity match."""
    if not match_id:
        return None
    try:
        candidates = sorted(out_dir.glob("*.json"))
    except Exception:
        return None

    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or str(data.get("match_id") or "") != str(match_id):
            continue
        if not _snapshot_identity_matches_request(
            data,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
        ):
            continue
        return p
    return None


@dataclass
class BrowserUse:
    session: str
    headed: bool = False
    mobile_profile: OkoooMobileProfile | None = None

    def _run_once(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _close_session_best_effort(self) -> None:
        try:
            cp = self._run_once(["browser-use", "--session", self.session, "close"], timeout=30)
            _ = cp.returncode
        except Exception:
            pass

    def run(self, *args: str, timeout: int = 60, use_headed: bool = False) -> str:
        cmd = ["browser-use", "--session", self.session]
        if use_headed:
            cmd.append("--headed")
        cmd.extend(args)
        cp = self._run_once(cmd, timeout=timeout)
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        if cp.returncode == 0:
            return out

        combined = (out + "\n" + err).strip()
        if "already running with different config" in combined:
            self._close_session_best_effort()
            cp2 = self._run_once(cmd, timeout=timeout)
            out2 = (cp2.stdout or "").strip()
            err2 = (cp2.stderr or "").strip()
            if cp2.returncode == 0:
                return out2
            combined = (out2 + "\n" + err2).strip()

        tail = "\n".join([x for x in combined.splitlines() if x][-8:])
        raise RuntimeError(f"browser-use failed: {' '.join(cmd)}\n{tail}")

    def open(self, url: str) -> None:
        profile = fresh_mobile_profile(self.mobile_profile) if is_okooo_mobile_url(url) else None
        self.mobile_profile = profile
        self.run("open", cache_busted_okooo_url(url, profile=profile), timeout=90, use_headed=self.headed)

    def state(self) -> str:
        return self.run("state", timeout=60)

    def eval_json(self, js_expr: str) -> Dict[str, Any]:
        out = self.run("eval", js_expr, timeout=90)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        payload = lines[-1] if lines else ""
        payload = payload.removeprefix("result:").strip()
        try:
            return json.loads(payload)
        except Exception:
            return {"_raw": out}

    def close(self) -> None:
        try:
            self.run("close", timeout=30)
        except Exception:
            pass


@dataclass(frozen=True)
class VerificationRecoveryContext:
    mobile_profile: OkoooMobileProfile | None = None
    mobile_profile_meta: Dict[str, Any] | None = None


class LocalChromeSession:
    def __init__(self, port: int, session_name: str = "") -> None:
        self.port = port
        self.session_name = session_name
        self.target_id: str | None = None
        self.ws = None
        self._msg_id = 0
        self.mobile_profile: OkoooMobileProfile | None = None

    def _browser_version(self) -> Dict[str, Any]:
        r = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=10)
        r.raise_for_status()
        return r.json()

    def _new_target(self, url: str) -> Dict[str, Any]:
        endpoint = f"http://127.0.0.1:{self.port}/json/new?{url}"
        r = requests.put(endpoint, timeout=15)
        if r.status_code >= 400:
            r = requests.get(endpoint, timeout=15)
        r.raise_for_status()
        return r.json()

    def _connect_if_needed(self, url: str = "about:blank") -> None:
        if self.ws:
            return
        profile = fresh_mobile_profile(self.mobile_profile) if is_okooo_mobile_url(url) else None
        self.mobile_profile = profile
        # Create a blank target first, then navigate with explicit mobile headers
        # and referrer. Opening the okooo mobile URL as the initial target can be
        # treated as a cold direct hit and is much more likely to be blocked.
        target = self._new_target("about:blank")
        self.target_id = target.get("id")
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("CDP page target missing webSocketDebuggerUrl")
        self.ws = create_connection(ws_url, timeout=20, suppress_origin=True)
        self._cdp("Page.enable")
        self._cdp("Runtime.enable")
        self._cdp("Network.enable")
        self._install_stealth_script()
        self._apply_okooo_mobile_profile(profile)

    def _install_stealth_script(self) -> None:
        # On a fixed IP the wall scores behaviour + automation fingerprints far
        # more than the device profile. A CDP-driven page leaks the most obvious
        # tell — navigator.webdriver === true, plus a missing window.chrome and
        # empty plugin/language shapes. Inject a document-start script that masks
        # these so the page reads like a normal mobile Safari session. Best effort:
        # any failure must not abort the scrape.
        stealth_js = r"""
(() => {
  try { Object.defineProperty(navigator, 'webdriver', {get: () => undefined}); } catch (e) {}
  try {
    if (!window.chrome) { window.chrome = { runtime: {} }; }
  } catch (e) {}
  try {
    if (!navigator.languages || !navigator.languages.length) {
      Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
    }
  } catch (e) {}
  try {
    const origQuery = navigator.permissions && navigator.permissions.query;
    if (origQuery) {
      navigator.permissions.query = (params) => (
        params && params.name === 'notifications'
          ? Promise.resolve({state: Notification.permission})
          : origQuery(params)
      );
    }
  } catch (e) {}
})();
"""
        try:
            self._cdp(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": stealth_js},
            )
        except Exception:
            pass

    def _apply_okooo_mobile_profile(self, profile: OkoooMobileProfile | None = None) -> None:
        # Mobile emulation + no-cache headers reduce the chance of desktop or stale
        # variants being served for m.okooo.com pages.
        resolved = profile or self.mobile_profile or random_mobile_profile()
        self.mobile_profile = resolved
        headers = mobile_headers(profile=resolved)
        self._cdp(
            "Network.setUserAgentOverride",
            {
                "userAgent": resolved.user_agent,
                "acceptLanguage": resolved.accept_language,
                "platform": "iOS",
            },
        )
        self._cdp("Network.setCacheDisabled", {"cacheDisabled": True})
        self._cdp("Network.setExtraHTTPHeaders", {"headers": headers})
        self._cdp(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": resolved.viewport["width"],
                "height": resolved.viewport["height"],
                "deviceScaleFactor": resolved.device_scale_factor,
                "mobile": True,
            },
        )
        self._cdp("Emulation.setTouchEmulationEnabled", {"enabled": True})

    def _navigate(self, url: str) -> None:
        resolved_url = cache_busted_okooo_url(url, profile=self.mobile_profile)
        params: Dict[str, Any] = {"url": resolved_url}
        if is_okooo_mobile_url(url):
            params["referrer"] = "https://m.okooo.com/"
        self._cdp("Page.navigate", params)

    def _cdp(self, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.ws:
            self._connect_if_needed()
        self._msg_id += 1
        payload = {"id": self._msg_id, "method": method, "params": params or {}}
        assert self.ws is not None
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == self._msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result", {})

    def open(self, url: str) -> None:
        if not self.ws:
            self._connect_if_needed(url)
        if is_okooo_mobile_url(url):
            self.mobile_profile = fresh_mobile_profile(self.mobile_profile)
            self._apply_okooo_mobile_profile(self.mobile_profile)
        self._navigate(url)
        time.sleep(3.5)

    def state(self) -> str:
        result = self._cdp(
            "Runtime.evaluate",
            {
                "expression": """(() => {
                  const body = document.body ? document.body.innerText : '';
                  return `viewport: ${window.innerWidth}x${window.innerHeight}\\npage: ${window.innerWidth}x${window.innerHeight}\\nscroll: (${window.scrollX}, ${window.scrollY})\\n${body}`;
                })()""",
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return result.get("result", {}).get("value", "") or ""

    def eval_json(self, js_expr: str) -> Dict[str, Any]:
        result = self._cdp(
            "Runtime.evaluate",
            {"expression": js_expr, "returnByValue": True, "awaitPromise": True},
        )
        value = result.get("result", {}).get("value")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"_raw": value}
        if isinstance(value, dict):
            return value
        return {"_raw": value}

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        finally:
            self.ws = None
        if self.target_id:
            try:
                requests.get(f"http://127.0.0.1:{self.port}/json/close/{self.target_id}", timeout=5)
            except Exception:
                pass
            self.target_id = None


def _wait_for_chrome_debug_port(port: int, timeout_seconds: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _ensure_local_chrome(port: int, chrome_path: str, user_data_dir: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        if r.ok:
            return {"port": port, "started_by_script": False}
    except Exception:
        pass

    # If the desired port is unavailable/flaky, try a few adjacent ports.
    port_candidates = [port] + [p for p in range(port + 1, port + 6)]
    last_err: str | None = None
    for cand in port_candidates:
        profile_dir = (Path(user_data_dir).resolve() / f"port_{cand}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={cand}",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=AutomationControlled",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if _wait_for_chrome_debug_port(cand, timeout_seconds=20.0):
            def _cleanup() -> None:
                try:
                    proc.terminate()
                except Exception:
                    pass

            atexit.register(_cleanup)
            return {"port": cand, "started_by_script": True, "profile_dir": str(profile_dir)}
        # failed; kill and try next port
        try:
            proc.terminate()
        except Exception:
            pass
        last_err = f"port {cand} did not respond"

    raise RuntimeError(f"本地 Chrome 远程调试端口启动失败（尝试 {port_candidates}）: {last_err or ''}".strip())


def _is_blocked_text(text: str) -> bool:
    return is_okooo_blocked_text(text)


def _page_blocked_now(bu: BrowserUse) -> bool:
    """Strong in-page verification-wall check for use mid-flow.

    The per-market 亚盘/大小球/凯利 parsers only detect the literal "访问被阻断"
    text and a "405" title, so a slider/captcha/geetest wall that appears after
    an in-page navigation would be misread as "no data". This mirrors the rich
    detection used by _open_ready (wall text + slider/captcha keywords + verify
    iframe/canvas/large image) so a mid-flow wall is caught and propagated.
    """
    try:
        result = bu.eval_json(
            r"""
(() => {
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const bodyText = norm(document.body?.innerText || '');
  const html = String(document.documentElement?.outerHTML || '');
  const title = String(document.title || '');
  const hasVerifyIframe = Array.from(document.querySelectorAll('iframe')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''}`.toLowerCase();
    return /(verify|captcha|geetest|aliyun|nc_|vcode)/.test(attrs);
  });
  const hasVerifyImage = Array.from(document.querySelectorAll('img')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''} ${el.alt || ''}`.toLowerCase();
    const width = Number(el.naturalWidth || el.width || 0);
    const height = Number(el.naturalHeight || el.height || 0);
    return /(verify|captcha|geetest|aliyun|slider|nc_)/.test(attrs) && Math.max(width, height) >= 200;
  });
  // Escape valve: a real slider/captcha wall never renders a full odds table.
  // If the page is dense with x.xx odds numbers, it is the genuine odds page —
  // never treat it as a wall (this is what kept 欧赔/凯利 from being parsed).
  const oddsHits = (bodyText.match(/\d{1,2}\.\d{2}/g) || []).length;
  if (oddsHits >= 6) return JSON.stringify({blocked: false});
  const blocked = [
    '访问被阻断',
    '安全威胁',
    '您的访问被阻断',
    '请进行验证',
    '滑动到最右边',
    '拖动滑块',
    '请按住滑块',
    '验证码'
  ].some((marker) => bodyText.includes(marker) || title.includes(marker))
    || title.includes('405')
    || hasVerifyIframe
    || hasVerifyImage;
  return JSON.stringify({blocked});
})()
"""
        )
    except Exception:
        return False
    return bool(isinstance(result, dict) and result.get("blocked"))


def _mobile_profile_meta(profile: Any) -> Dict[str, Any]:
    if not isinstance(profile, OkoooMobileProfile):
        return {}
    return {
        "profile_id": profile.profile_id,
        "device_pool_id": profile.device_pool_id,
        "device_name": profile.device_name,
        "user_agent": profile.user_agent,
    }


def _mobile_profile_from_meta(meta: Dict[str, Any]) -> OkoooMobileProfile | None:
    if not isinstance(meta, dict):
        return None
    wanted_profile_id = str(meta.get("profile_id") or "").strip()
    wanted_pool_id = str(meta.get("device_pool_id") or "").strip()
    for profile in available_mobile_profiles():
        if wanted_profile_id and profile.profile_id == wanted_profile_id:
            return profile
    for profile in available_mobile_profiles():
        if wanted_pool_id and profile.device_pool_id == wanted_pool_id:
            return profile
    return None


def _build_verification_recovery_context(data: Dict[str, Any]) -> VerificationRecoveryContext:
    meta = data.get("mobile_profile") if isinstance(data, dict) else None
    profile = _mobile_profile_from_meta(meta if isinstance(meta, dict) else {})
    return VerificationRecoveryContext(
        mobile_profile=profile,
        mobile_profile_meta=dict(meta) if isinstance(meta, dict) else None,
    )


def _mark_verification_required(data: Dict[str, Any], client: Any = None) -> Dict[str, Any]:
    out = dict(data) if isinstance(data, dict) else {"error": str(data)}
    out["blocked"] = True
    out["verification_required"] = True
    out["status"] = "verification_required"
    out["error"] = "verification_required"
    profile_meta = _mobile_profile_meta(getattr(client, "mobile_profile", None))
    if profile_meta:
        out["mobile_profile"] = profile_meta
    out.setdefault("retry_strategy", "stop_after_verification")
    return out


def _is_verification_required_payload(data: Dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return False
    return bool(data.get("verification_required")) or str(data.get("status") or "").strip() == "verification_required"


def _is_success_payload(data: Dict[str, Any]) -> bool:
    if data.get("blocked") or _is_verification_required_payload(data):
        return False
    if data.get("found") is False:
        return False
    if data.get("parsed") is False:
        return False
    return True


def _market_is_usable(market: Any) -> bool:
    """一个盘口（欧赔/亚值/大小球）是否拿到了真实可用数据。

    熔断/风控时盘口会被标记为 blocked/verification_required（如 ttl_circuit_open），
    或整段为空；这两种都不算可用，不能覆盖已有的好快照。
    """
    if not isinstance(market, dict):
        return False
    if market.get("blocked") or _is_verification_required_payload(market):
        return False
    final = market.get("final")
    if isinstance(final, dict) and (final.get("blocked") or _is_verification_required_payload(final)):
        return False
    # final 至少要有一项真实报价字段才算可用。
    return bool(final) and any(
        v not in (None, "", {}, [])
        for k, v in final.items()
        if k not in {"blocked", "verification_required", "status", "error"}
    )


def _snapshot_has_usable_odds(payload: Dict[str, Any]) -> bool:
    """快照里三大盘口（欧赔/亚值/大小球）只要有任意一项可用即视为有效抓取。"""
    if not isinstance(payload, dict):
        return False
    return any(_market_is_usable(payload.get(k)) for k in ("欧赔", "亚值", "大小球"))


def _classify_retry_error_message(msg: str) -> str:
    """Classify retry errors so we can fail fast on clearly non-recoverable cases."""
    s = (msg or "").lower()
    if not s:
        return "unknown"
    if "failed to establish a new connection" in s or "connection refused" in s:
        return "cdp_connection_refused"
    if "port" in s and "did not respond" in s:
        return "chrome_port_unavailable"
    if "本地 chrome 远程调试端口启动失败" in msg:
        return "chrome_start_failed"
    if "no such file or directory" in s and "google chrome" in s:
        return "chrome_binary_missing"
    if "command not found" in s or "browser-use" in s and "not found" in s:
        return "browser_use_missing"
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if "blocked" in s or "访问被阻断" in msg or "405" in s:
        return "blocked"
    return "other"


def _should_stop_retrying_from_payload(data: Dict[str, Any]) -> tuple[bool, str]:
    """Return (stop_now, reason) for a non-success payload."""
    if not isinstance(data, dict):
        return False, ""
    if data.get("blocked") or _is_verification_required_payload(data):
        # Repeating the exact same extractor/session is usually wasteful; move to fallback path.
        return True, "verification_required"
    err = data.get("error")
    if isinstance(err, str):
        kind = _classify_retry_error_message(err)
        if kind in {
            "cdp_connection_refused",
            "chrome_port_unavailable",
            "chrome_start_failed",
            "chrome_binary_missing",
            "browser_use_missing",
        }:
            return True, kind
    return False, ""


def _annotate_attempts(data: Dict[str, Any], attempts: list[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(data)
    out["_attempts"] = attempts
    return out


def _score_europe_payload(data: Dict[str, Any]) -> tuple[int, int, int]:
    if not _is_success_payload(data):
        return (0, 0, 0)
    if not isinstance(data, dict):
        return (0, 0, 0)
    consensus = data.get("consensus") if isinstance(data.get("consensus"), dict) else {}
    companies = data.get("companies") if isinstance(data.get("companies"), list) else []
    mode = data.get("company_mode") or consensus.get("mode")
    mode_score = {
        "multi_company_consensus": 3,
        "single_company": 2,
        "average_row_fallback": 1,
    }.get(str(mode), 0)
    filtered_count = int(consensus.get("filtered_company_count") or 0)
    company_count = int(consensus.get("company_count") or 0)
    return (mode_score, max(filtered_count, len(companies)), company_count)


def _pick_preferred_europe_result(*candidates: Dict[str, Any]) -> Dict[str, Any]:
    best: Dict[str, Any] | None = None
    best_score = (-1, -1, -1)
    for candidate in candidates:
        score = _score_europe_payload(candidate)
        if score > best_score:
            best = candidate
            best_score = score
    return best or {}


def _warm_up_session(bu: BrowserUse) -> None:
    """Land on a shallow okooo page once per session before deep-linking.

    A freshly created browser has no cookies; navigating straight to a deep
    odds page (handicap.php / odds.php) as the very first request is the most
    obvious automation signal and reliably trips okooo's verification wall.
    Opening the mobile home page first lets cookies and a natural navigation
    history settle, mimicking a real user who browses in before viewing odds.
    Guarded by a per-session flag so repeated _open_ready calls within the same
    session (e.g. tab switches) do not re-warm.
    """
    if getattr(bu, "_okooo_warmed", False):
        return
    try:
        bu.open(WARM_LANDING_URL)
        time.sleep(WARM_UP_DWELL_SECONDS)
    except Exception:
        pass
    try:
        setattr(bu, "_okooo_warmed", True)
    except Exception:
        pass


def _open_ready(bu: BrowserUse, url: str, settle_seconds: float = 2.5) -> str:
    _warm_up_session(bu)
    _global_pace_gate()
    bu.open(url)
    time.sleep(settle_seconds)
    try:
        state_text = bu.state()
    except Exception:
        state_text = ""
    try:
        verification = bu.eval_json(
            r"""
(() => {
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const bodyText = norm(document.body?.innerText || '');
  const html = String(document.documentElement?.outerHTML || '');
  const title = String(document.title || '');
  const hasVerifyIframe = Array.from(document.querySelectorAll('iframe')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''}`.toLowerCase();
    return /(verify|captcha|geetest|aliyun|nc_|vcode)/.test(attrs);
  });
  const hasVerifyImage = Array.from(document.querySelectorAll('img')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''} ${el.alt || ''}`.toLowerCase();
    const width = Number(el.naturalWidth || el.width || 0);
    const height = Number(el.naturalHeight || el.height || 0);
    return /(verify|captcha|geetest|aliyun|slider|nc_)/.test(attrs) && Math.max(width, height) >= 200;
  });
  const blocked = [
    '访问被阻断',
    '安全威胁',
    '您的访问被阻断',
    '请进行验证',
    '滑动到最右边',
    '拖动滑块',
    '请按住滑块',
    '验证码'
  ].some((marker) => bodyText.includes(marker) || title.includes(marker))
    || title.includes('405')
    || (html.includes('<canvas') && /(verify|captcha|geetest|aliyun|nc_|slider)/i.test(html))
    || hasVerifyIframe
    || hasVerifyImage;
  return JSON.stringify({
    blocked,
    bodyText: bodyText.slice(0, 500),
    title: title.slice(0, 200),
  });
})()
"""
        )
    except Exception:
        verification = {}
    if isinstance(verification, dict) and verification.get("blocked"):
        body_text = str(verification.get("bodyText") or "").strip()
        title = str(verification.get("title") or "").strip()
        state_text = "\n".join(part for part in (state_text, title, body_text, "请进行验证") if part)
    return state_text


def _mobile_league_url(league: str) -> str | None:
    league = _normalize_okooo_league_name(league)
    mapping = {
        "英超": "https://m.okooo.com/saishi/17/",
        "意甲": "https://m.okooo.com/saishi/23/",
        "西甲": "https://m.okooo.com/saishi/8/",
        "德甲": "https://m.okooo.com/saishi/35/",
        "法甲": "https://m.okooo.com/saishi/34/",
        "世界杯": "https://m.okooo.com/saishi/16/",
        "友谊赛": "https://m.okooo.com/saishi/851/",
        "欧冠": "https://m.okooo.com/saishi/7/",
        "欧联": "https://m.okooo.com/saishi/679/",
        "欧罗巴": "https://m.okooo.com/saishi/679/",
        "中超": "https://m.okooo.com/saishi/649/",
        "英冠": "https://m.okooo.com/saishi/133/",
        "瑞典超": "https://m.okooo.com/saishi/40/",
        "挪超": "https://m.okooo.com/saishi/20/",
        "芬超": "https://m.okooo.com/saishi/41/",
    }
    return mapping.get(league)


def _click_visible_text(bu: BrowserUse, labels: list[str], settle_seconds: float = CLICK_SETTLE_SECONDS) -> Dict[str, Any]:
    js = r"""
(() => {
  const labels = %s;
  const els = [...document.querySelectorAll('a,div,span,button,li')];
  for (const label of labels) {
    const el = els.find(e => ((e.innerText || '').replace(/\s+/g,'').trim() === label));
    if (el) {
      el.click();
      return JSON.stringify({clicked:true, label, tag:el.tagName});
    }
  }
  return JSON.stringify({clicked:false, labels});
})()
""" % json.dumps(labels, ensure_ascii=False)
    result = bu.eval_json(js)
    time.sleep(settle_seconds)
    return result


def _parse_europe_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    js = "(() => {\n" + _DEOBFUSCATE_HELPER_JS + r"""
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const bodyText = norm(visibleInnerText(document.body));
  const html = String(document.documentElement?.outerHTML || '');
  const title = String(document.title || '');
  const hasVerifyIframe = Array.from(document.querySelectorAll('iframe')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''}`.toLowerCase();
    return /(verify|captcha|geetest|aliyun|nc_|vcode)/.test(attrs);
  });
  const hasVerifyImage = Array.from(document.querySelectorAll('img')).some((el) => {
    const attrs = `${el.id || ''} ${el.className || ''} ${el.src || ''} ${el.alt || ''}`.toLowerCase();
    const width = Number(el.naturalWidth || el.width || 0);
    const height = Number(el.naturalHeight || el.height || 0);
    return /(verify|captcha|geetest|aliyun|slider|nc_)/.test(attrs) && Math.max(width, height) >= 200;
  });
  const blocked = [
    '访问被阻断',
    '安全威胁',
    '您的访问被阻断',
    '请进行验证',
    '滑动到最右边',
    '拖动滑块',
    '请按住滑块',
    '验证码'
  ].some((marker) => bodyText.includes(marker) || title.includes(marker))
    || title.includes('405')
    || (html.includes('<canvas') && /(verify|captcha|geetest|aliyun|nc_|slider)/i.test(html))
    || hasVerifyIframe
    || hasVerifyImage;
  // Tighten the verification verdict: a real odds.php view is dense with x.xx
  // odds numbers. Right after navigating from the hub the 欧赔 table can flash a
  // transient view that trips a verification keyword without the page actually
  // being walled. Only treat it as blocked when the verification signal fires
  // AND the page lacks a meaningful amount of odds data.
  const oddsHits = (bodyText.match(/\d{1,2}\.\d{2}/g) || []).length;
  if (blocked && oddsHits < 6) return JSON.stringify({blocked:true});
  const body = visibleInnerText(document.body);
  const lines = body.split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const compact = body.replace(/\s+/g, ' ').trim();
  const compactOddsRe = /\d{1,2}\.\d{2}/g;

  const aliasPairs = [
    ['Bet365', 'Bet365'],
    ['bet365', 'Bet365'],
    ['皇冠', '皇冠'],
    ['Pinnacle', 'Pinnacle'],
    ['平博', '平博'],
    ['SBOBET', 'SBOBET'],
    ['SBO', 'SBOBET'],
    ['12BET', '12BET'],
    ['易胜博', '易胜博'],
    ['威廉.希尔', '威廉希尔'],
    ['威廉希尔', '威廉希尔'],
    ['立博', '立博'],
    ['Bwin', 'Bwin'],
    ['Interwetten', 'Interwetten'],
    ['伟德', '伟德'],
    ['韦德', '伟德'],
    ['香港马会', '香港马会'],
    ['澳门彩票', '澳门彩票'],
    ['澳门', '澳门彩票'],
    ['明陞', '明陞'],
    ['利记', '利记']
  ];
  const companyAliases = aliasPairs.map(x => x[0]);
  const priority = {
    'Bet365': 9,
    '皇冠': 8,
    'Pinnacle': 8,
    '澳门彩票': 7,
    '易胜博': 6,
    '威廉希尔': 6,
    '立博': 5,
    'Interwetten': 4,
    'Bwin': 4,
    '平博': 4,
    'SBOBET': 4,
    '12BET': 4,
    '伟德': 3,
    '香港马会': 3,
    '明陞': 3,
    '利记': 3,
  };
  const round4 = (v) => +Number(v).toFixed(4);
  const sanitizeText = (text) => (text || '').replace(/[!#＊*·•|｜]/g, '').replace(/\s+/g, ' ').trim();
  const normalizeCompany = (name) => {
    const row = aliasPairs.find(x => x[0] === name);
    return row ? row[1] : name;
  };
  const median = (arr) => {
    const vals = (arr || []).filter(v => Number.isFinite(v)).slice().sort((a, b) => a - b);
    if (!vals.length) return null;
    const mid = Math.floor(vals.length / 2);
    return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
  };
  const weightedAvg = (items, getter) => {
    let sum = 0;
    let wsum = 0;
    for (const item of items || []) {
      const val = getter(item);
      const w = item._priority || 1;
      if (!Number.isFinite(val) || !Number.isFinite(w) || w <= 0) continue;
      sum += val * w;
      wsum += w;
    }
    return wsum > 0 ? sum / wsum : null;
  };
  const isNear = (v, med, pct) => {
    if (!Number.isFinite(v) || !Number.isFinite(med) || med <= 0) return false;
    return Math.abs(v - med) / med <= pct;
  };

  const parseLine = (line, companyAlias, sourceTag) => {
    const cleanLine = sanitizeText(line);
    const cleanAlias = sanitizeText(companyAlias);
    const idx = cleanLine.toLowerCase().indexOf(cleanAlias.toLowerCase());
    if (idx < 0) return null;
    const company = normalizeCompany(companyAlias);
    const tail = cleanLine.slice(idx + cleanAlias.length).trim();
    const nums = (tail.match(compactOddsRe) || []).map(x => parseFloat(x));
    if (nums.length < 6) return null;
    const initial = { home: nums[0], draw: nums[1], away: nums[2] };
    const final = { home: nums[3], draw: nums[4], away: nums[5] };
    const vals = [initial.home, initial.draw, initial.away, final.home, final.draw, final.away];
    if (vals.some(v => !Number.isFinite(v) || v <= 1.01 || v >= 80)) return null;
    return {
      company,
      initial,
      final,
      delta: {
        home: round4(final.home - initial.home),
        draw: round4(final.draw - initial.draw),
        away: round4(final.away - initial.away),
      },
      _source: sourceTag,
      _matched_line: cleanLine,
      _priority: priority[company] || 1,
    };
  };

  const dedup = new Map();
  const pushCandidate = (item) => {
    if (!item) return;
    const key = [
      item.company,
      round4(item.initial.home),
      round4(item.initial.draw),
      round4(item.initial.away),
      round4(item.final.home),
      round4(item.final.draw),
      round4(item.final.away)
    ].join('|');
    if (!dedup.has(key)) dedup.set(key, item);
  };

  const tableRows = [...document.querySelectorAll('tr')].map(tr => visibleInnerText(tr).replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const company of companyAliases) {
    for (const line of tableRows) pushCandidate(parseLine(line, company, 'table_row'));
  }
  for (const company of companyAliases) {
    for (const line of lines) pushCandidate(parseLine(line, company, 'body_text_line'));
  }
  for (const company of companyAliases) {
    const idx = compact.indexOf(company);
    if (idx < 0) continue;
    const windowText = compact.slice(Math.max(0, idx - 8), idx + 160);
    pushCandidate(parseLine(windowText, company, 'body_text_window'));
  }

  const companies = [...dedup.values()];
  if (companies.length) {
    const medHome = median(companies.map(x => x.final.home));
    const medDraw = median(companies.map(x => x.final.draw));
    const medAway = median(companies.map(x => x.final.away));
    let filtered = companies.filter(x =>
      isNear(x.final.home, medHome, 0.22) &&
      isNear(x.final.draw, medDraw, 0.28) &&
      isNear(x.final.away, medAway, 0.28)
    );
    if (!filtered.length) filtered = companies;

    const initial = {
      home: round4(weightedAvg(filtered, x => x.initial.home)),
      draw: round4(weightedAvg(filtered, x => x.initial.draw)),
      away: round4(weightedAvg(filtered, x => x.initial.away)),
    };
    const final = {
      home: round4(weightedAvg(filtered, x => x.final.home)),
      draw: round4(weightedAvg(filtered, x => x.final.draw)),
      away: round4(weightedAvg(filtered, x => x.final.away)),
    };
    const allCompanies = companies
      .slice()
      .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
      .map(x => ({
        company: x.company,
        initial: x.initial,
        final: x.final,
        delta: x.delta,
        _source: x._source,
        _matched_line: x._matched_line,
      }));
    const filteredNames = filtered
      .slice()
      .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
      .map(x => x.company);

    return JSON.stringify({
      found:true,
      parsed:true,
      company: filteredNames[0] || allCompanies[0]?.company || '',
      company_mode: filtered.length > 1 ? 'multi_company_consensus' : 'single_company',
      initial,
      final,
      delta:{
        home: round4(final.home - initial.home),
        draw: round4(final.draw - initial.draw),
        away: round4(final.away - initial.away),
      },
      consensus:{
        mode: filtered.length > 1 ? 'multi_company_consensus' : 'single_company',
        company_count: allCompanies.length,
        filtered_company_count: filtered.length,
        companies: filteredNames,
        all_companies: allCompanies.map(x => x.company),
        final_median: {home: round4(medHome), draw: round4(medDraw), away: round4(medAway)},
      },
      companies: allCompanies,
      _source:'multi_company_consensus',
      _matched_line: filtered[0]?._matched_line || ''
    });
  }

  const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('99家平均'));
  if(!row) return JSON.stringify({found:false});
  const num=(sel)=>{const el=row.querySelector(sel); if(!el) return null; const v=parseFloat((el.textContent||'').trim()); return Number.isFinite(v)?v:null;};
  const initial={home:num('span[type=sheng]'),draw:num('span[type=ping]'),away:num('span[type=fu]')};
  const final={home:num('span[type=xinsheng]'),draw:num('span[type=xinping]'),away:num('span[type=xinfu]')};
  const delta=(a,b)=> (a==null||b==null) ? null : +(a-b).toFixed(4);
  return JSON.stringify({
    found:true,
    parsed:true,
    company:'99家平均',
    company_mode:'average_row_fallback',
    initial,
    final,
    delta:{home:delta(final.home,initial.home), draw:delta(final.draw,initial.draw), away:delta(final.away,initial.away)},
    consensus:{mode:'average_row_fallback', company_count:0, filtered_company_count:0, companies:[], all_companies:[]},
    companies:[]
  });
})()
"""
    return bu.eval_json(js)


# m.okooo obfuscates Chinese company names by inserting zero-font-size <span>
# nodes carrying junk glyphs (e.g. 澳<span style="font-size:0">!</span>门彩票,
# b<span style="font-size:0">!</span>et365). Stripping those nodes before
# sampling innerText restores the canonical company names so alias matching
# can see Bet365/皇冠/澳门彩票 instead of bogus b!et365/澳!门#彩!票.
# Confirmed on handicap.php (亚盘); pre-emptively wired into 欧赔/大小球/凯利
# parsers so a future obfuscation rollout on those pages doesn't silently
# collapse multi_company_consensus back to single_company.
_DEOBFUSCATE_HELPER_JS = r"""
const visibleInnerText = (el) => {
  if (!el) return '';
  const clone = el.cloneNode(true);
  const all = clone.querySelectorAll ? clone.querySelectorAll('*') : [];
  for (const node of all) {
    const style = (node.getAttribute && node.getAttribute('style')) || '';
    if (/font-size\s*:\s*0(?:px|pt|em|%)?/i.test(style) || /display\s*:\s*none/i.test(style) || /visibility\s*:\s*hidden/i.test(style)) {
      node.parentNode && node.parentNode.removeChild(node);
    }
  }
  return (clone.innerText || clone.textContent || '');
};
"""


ASIAN_EXPAND_LABEL_GROUPS: list[list[str]] = [
    ["全部公司", "全部博彩公司", "所有公司", "更多公司", "更多博彩公司", "查看更多公司", "查看全部公司"],
    ["机构指数", "公司指数", "公司列表", "更多机构", "全部机构"],
    ["切换公司", "切换机构", "展开", "查看更多"],
]


def _asian_page_signal(bu: BrowserUse) -> Dict[str, Any]:
    """Probe handicap.php for how many companies the page is currently exposing.

    Returns ``{tr_count, company_hit_count, aliases}`` so the caller can tell
    whether an expansion click actually unfolded more company rows."""
    js = r"""
(() => {
  const aliases = ['Bet365','bet365','皇冠','Pinnacle','澳门彩票','澳门','易胜博',
    '威廉希尔','威廉.希尔','立博','Bwin','Interwetten','12BET','SBOBET','SBO',
    '伟德','韦德','香港马会','明陞','利记','平博'];
  const body = (document.body?.innerText || '').replace(/\s+/g,' ').trim();
  const trCount = document.querySelectorAll('tr').length;
  const seen = new Set();
  for (const a of aliases) { if (body.includes(a)) seen.add(a.toLowerCase()); }
  return JSON.stringify({
    tr_count: trCount,
    company_hit_count: seen.size,
    aliases: [...seen],
  });
})()
"""
    try:
        return bu.eval_json(js)
    except Exception:
        return {}


def _expand_asian_company_view(bu: BrowserUse, dwell: float = 2.0) -> Dict[str, Any]:
    """Best-effort: click any "全部公司 / 更多公司 / 机构指数" control on m.okooo
    handicap.php so the table renders more than the default single company row.

    Strategy:
    - Snapshot current `(tr_count, company_hit_count)` as the baseline.
    - For each label group, try `_click_visible_text` with exact-match labels.
      After each successful click, settle and re-probe; accept the click if the
      signal strictly improved.
    - Also try a click against any anchor/button whose textContent matches the
      Chinese label loosely (substring), since some pages wrap the trigger in
      `<a><span>` or `<button>` tags whose innerText has surrounding glyphs.
    - Always returns a diagnostics dict; never raises.
    """
    before = _asian_page_signal(bu)
    attempts: list[Dict[str, Any]] = []
    best_signal = before

    def _record(label: str, click_result: Dict[str, Any], after: Dict[str, Any]) -> None:
        attempts.append({
            "label": label,
            "click": click_result,
            "after_tr": after.get("tr_count"),
            "after_companies": after.get("company_hit_count"),
            "after_aliases": after.get("aliases"),
        })

    def _signal_improved(prev: Dict[str, Any], curr: Dict[str, Any]) -> bool:
        prev_hits = int(prev.get("company_hit_count") or 0)
        curr_hits = int(curr.get("company_hit_count") or 0)
        if curr_hits > prev_hits:
            return True
        prev_rows = int(prev.get("tr_count") or 0)
        curr_rows = int(curr.get("tr_count") or 0)
        return curr_rows >= prev_rows + 5 and curr_hits >= prev_hits

    for group in ASIAN_EXPAND_LABEL_GROUPS:
        # The existing _click_visible_text matches exact text (after whitespace
        # collapse); good enough for explicit buttons.
        click_result = _click_visible_text(bu, group, settle_seconds=dwell)
        if not click_result.get("clicked"):
            continue
        time.sleep(_jittered(dwell))
        after = _asian_page_signal(bu)
        _record(click_result.get("label") or "", click_result, after)
        if _signal_improved(best_signal, after):
            best_signal = after
            # One successful expansion usually reveals every company; no need
            # to keep poking and risk collapsing the panel.
            break

    # If nothing exact-matched, try a loose-substring sweep over inline controls.
    if int(best_signal.get("company_hit_count") or 0) <= 1:
        loose_js = r"""
(() => {
  const want = %s;
  const flat = want.flat();
  const els = [...document.querySelectorAll('a,button,div,span,li,td,th')];
  const fired = [];
  for (const el of els) {
    const t = ((el.innerText || el.textContent || '').replace(/\s+/g,'').trim());
    if (!t) continue;
    if (flat.some(w => t.includes(w))) {
      try { el.click(); fired.push({text: t.slice(0,40), tag: el.tagName}); } catch (e) {}
      if (fired.length >= 4) break;
    }
  }
  return JSON.stringify({fired});
})()
""" % json.dumps(ASIAN_EXPAND_LABEL_GROUPS, ensure_ascii=False)
        try:
            loose_result = bu.eval_json(loose_js)
        except Exception:
            loose_result = {}
        if isinstance(loose_result, dict) and loose_result.get("fired"):
            time.sleep(_jittered(dwell))
            after = _asian_page_signal(bu)
            _record("__loose_sweep__", loose_result, after)
            if _signal_improved(best_signal, after):
                best_signal = after

    # Collect what *looks* like an expansion trigger on the page so we can iterate
    # on the label list next round. This is cheap (<1ms) and capped.
    candidates_js = r"""
(() => {
  const pats = /(公司|机构|更多|全部|展开|切换)/;
  const out = [];
  const els = [...document.querySelectorAll('a,button,div,span,li,td,th')];
  for (const el of els) {
    const t = ((el.innerText || el.textContent || '').replace(/\s+/g,' ').trim());
    if (!t || t.length > 30) continue;
    if (!pats.test(t)) continue;
    out.push({
      tag: el.tagName,
      text: t,
      cls: (el.className || '').toString().slice(0, 120),
      id: (el.id || '').toString().slice(0, 60),
    });
    if (out.length >= 30) break;
  }
  return JSON.stringify({candidates: out});
})()
"""
    try:
        cand = bu.eval_json(candidates_js)
    except Exception:
        cand = {}
    candidates = cand.get("candidates") if isinstance(cand, dict) else []

    return {
        "before": before,
        "after": best_signal,
        "expanded": bool(int(best_signal.get("company_hit_count") or 0) > int(before.get("company_hit_count") or 0)),
        "attempts": attempts,
        "candidates": candidates,
    }


def _parse_asian_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    js = "(() => {\n" + _DEOBFUSCATE_HELPER_JS + r"""
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});
  // Build the deobfuscated views once: body / lines / compact / table rows.
  const body = visibleInnerText(document.body);
  const lines = body.split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const compact = body.replace(/\s+/g, ' ').trim();

  const aliasPairs = [
    ['澳门彩票', '澳门彩票'],
    ['澳门', '澳门彩票'],
    ['Bet365', 'Bet365'],
    ['bet365', 'Bet365'],
    ['皇冠', '皇冠'],
    ['Pinnacle', 'Pinnacle'],
    ['平博', '平博'],
    ['SBOBET', 'SBOBET'],
    ['SBO', 'SBOBET'],
    ['12BET', '12BET'],
    ['易胜博', '易胜博'],
    ['威廉.希尔', '威廉希尔'],
    ['威廉希尔', '威廉希尔'],
    ['立博', '立博'],
    ['Bwin', 'Bwin'],
    ['Interwetten', 'Interwetten'],
    ['伟德', '伟德'],
    ['韦德', '伟德'],
    ['香港马会', '香港马会'],
    ['明陞', '明陞'],
    ['利记', '利记']
  ];
  const companyAliases = aliasPairs.map(x => x[0]);
  const priority = {
    'Bet365': 9,
    '皇冠': 8,
    'Pinnacle': 8,
    '澳门彩票': 7,
    '易胜博': 6,
    '威廉希尔': 6,
    '立博': 5,
    'Bwin': 4,
    'Interwetten': 4,
    '平博': 4,
    'SBOBET': 4,
    '12BET': 4,
    '伟德': 3,
    '香港马会': 3,
    '明陞': 3,
    '利记': 3,
  };
  const round4 = (v) => +Number(v).toFixed(4);
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  const normalizeCompany = (name) => {
    const row = aliasPairs.find(x => x[0] === name);
    return row ? row[1] : name;
  };
  const normalizeLineValue = (v) => Math.round(Number(v) * 4) / 4;
  const lineKey = (v) => normalizeLineValue(v).toFixed(2);

  const handicapMap = {
    '平手': 0.0,
    '平手/半球': -0.25,
    '平/半': -0.25,
    '半球': -0.5,
    '半球/一球': -0.75,
    '半/一': -0.75,
    '一球': -1.0,
    '一球/球半': -1.25,
    '一/球半': -1.25,
    '球半': -1.5,
    '球半/两球': -1.75,
    '两球': -2.0,
    '两球/两球半': -2.25,
    '两球半': -2.5,
    '受让平手': 0.0,
    '受让平手/半球': 0.25,
    '受让平/半': 0.25,
    '受让半球': 0.5,
    '受让半球/一球': 0.75,
    '受让半/一': 0.75,
    '受让一球': 1.0,
    '受让一球/球半': 1.25,
    '受让一/球半': 1.25,
    '受让球半': 1.5,
    '受让球半/两球': 1.75,
    '受让两球': 2.0,
    '受让两球/两球半': 2.25,
    '受让两球半': 2.5,
  };
  const parseHandicap = (text) => {
    const s = String(text || '').replace(/\s+/g, '');
    if (!s) return null;
    if (Object.prototype.hasOwnProperty.call(handicapMap, s)) return handicapMap[s];
    if (/^-?\d+(?:\.\d+)?$/.test(s)) return parseFloat(s);
    return null;
  };

  const normalizeText = (raw) => String(raw || '')
    .replace(/\u00A0/g, ' ')
    .replace(/[／\uFF0F]/g, '/')
    .replace(/\s+/g, ' ')
    .trim();

  const sortedHandicapTokens = Object.keys(handicapMap).sort((a, b) => b.length - a.length);

  const buildFromSixSegments = (homeInitWater, initialText, awayInitWater, homeFinalWater, finalText, awayFinalWater) => {
    const initialValue = parseHandicap(initialText);
    const finalValue = parseHandicap(finalText);
    if (initialValue == null || finalValue == null) return null;
    const initial = {
      home_water: homeInitWater,
      handicap_text: initialText,
      handicap_value: initialValue,
      handicap: initialValue,
      away_water: awayInitWater,
    };
    const final = {
      home_water: homeFinalWater,
      handicap_text: finalText,
      handicap_value: finalValue,
      handicap: finalValue,
      away_water: awayFinalWater,
    };
    if (![initial.home_water, initial.away_water, final.home_water, final.away_water].every(v => Number.isFinite(v))) return null;
    return {
      initial,
      final,
      delta: {
        home_water: round4(final.home_water - initial.home_water),
        handicap_value: round4(final.handicap_value - initial.handicap_value),
        away_water: round4(final.away_water - initial.away_water),
      },
    };
  };

  const tokenizeAsian = (tail) => {
    const tokens = [];
    let cursor = 0;
    while (cursor < tail.length) {
      const slice = tail.slice(cursor);
      const numMatch = slice.match(/^\s*(\d+(?:\.\d+)?)/);
      if (numMatch) {
        tokens.push({ type: 'num', val: parseFloat(numMatch[1]), raw: numMatch[1] });
        cursor += numMatch[0].length;
        continue;
      }
      let matched = false;
      for (const token of sortedHandicapTokens) {
        if (slice.startsWith(token)) {
          tokens.push({ type: 'hcap', val: token });
          cursor += token.length;
          matched = true;
          break;
        }
      }
      if (matched) continue;
      cursor += 1;
    }
    return tokens;
  };

  const findSegmentByTokens = (tail) => {
    const tokens = tokenizeAsian(tail);
    for (let i = 0; i + 5 < tokens.length; i += 1) {
      const window = tokens.slice(i, i + 6);
      if (window[0].type === 'num' && window[1].type === 'hcap'
        && window[2].type === 'num' && window[3].type === 'num'
        && window[4].type === 'hcap' && window[5].type === 'num') {
        return buildFromSixSegments(
          window[0].val,
          window[1].val,
          window[2].val,
          window[3].val,
          window[4].val,
          window[5].val,
        );
      }
    }
    return null;
  };

  const parseLine = (line, companyAlias, sourceTag) => {
    const normalized = normalizeText(line);
    const idx = normalized.indexOf(companyAlias);
    if (idx < 0) return null;
    const company = normalizeCompany(companyAlias);
    const tail = normalized.slice(idx + companyAlias.length).trim();
    const strictRe = /(\d+(?:\.\d+)?)\s*([\u4e00-\u9fff\/半球两一平受让]+)\s*(\d+(?:\.\d+)?)\s*(\d+(?:\.\d+)?)\s*([\u4e00-\u9fff\/半球两一平受让]+)\s*(\d+(?:\.\d+)?)/;
    const m = tail.match(strictRe);
    let segment = null;
    if (m) {
      segment = buildFromSixSegments(
        parseFloat(m[1]), m[2].trim(), parseFloat(m[3]),
        parseFloat(m[4]), m[5].trim(), parseFloat(m[6]),
      );
    }
    if (!segment) segment = findSegmentByTokens(tail);
    if (!segment) return null;
    return {
      company,
      initial: segment.initial,
      final: segment.final,
      delta: segment.delta,
      _source: sourceTag,
      _matched_line: normalized,
      _priority: priority[company] || 1,
    };
  };

  const parseRowCells = (tr, companyAlias) => {
    const cellTexts = [...tr.querySelectorAll('td,th')].map(td => normalizeText(visibleInnerText(td)));
    if (!cellTexts.length) return null;
    const joinedText = cellTexts.join(' ');
    if (joinedText.indexOf(companyAlias) < 0) return null;
    const segment = findSegmentByTokens(joinedText.slice(joinedText.indexOf(companyAlias) + companyAlias.length));
    if (!segment) return null;
    return {
      company: normalizeCompany(companyAlias),
      initial: segment.initial,
      final: segment.final,
      delta: segment.delta,
      _source: 'table_cells',
      _matched_line: joinedText,
      _priority: priority[normalizeCompany(companyAlias)] || 1,
    };
  };

  const dedup = new Map();
  const pushCandidate = (item) => {
    if (!item) return;
    const key = [
      item.company,
      lineKey(item.initial.handicap_value),
      lineKey(item.final.handicap_value),
      round4(item.initial.home_water),
      round4(item.initial.away_water),
      round4(item.final.home_water),
      round4(item.final.away_water),
    ].join('|');
    if (!dedup.has(key)) dedup.set(key, item);
  };

  const tableRows = [...document.querySelectorAll('tr')].map(tr => visibleInnerText(tr).replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const company of companyAliases) {
    for (const line of tableRows) pushCandidate(parseLine(line, company, 'table_row'));
  }
  for (const tr of document.querySelectorAll('tr')) {
    for (const company of companyAliases) {
      pushCandidate(parseRowCells(tr, company));
    }
  }
  for (const company of companyAliases) {
    for (const line of lines) pushCandidate(parseLine(line, company, 'body_text_line'));
  }
  for (const company of companyAliases) {
    const idx = compact.indexOf(company);
    if (idx < 0) continue;
    const windowText = compact.slice(Math.max(0, idx - 8), idx + 140);
    pushCandidate(parseLine(windowText, company, 'body_text_window'));
  }

  const companies = [...dedup.values()];
  if (companies.length) {
    const finalGroups = {};
    for (const item of companies) {
      const key = lineKey(item.final.handicap_value);
      if (!finalGroups[key]) finalGroups[key] = {items: [], count: 0, weight: 0};
      finalGroups[key].items.push(item);
      finalGroups[key].count += 1;
      finalGroups[key].weight += item._priority || 1;
    }
    const orderedFinalGroups = Object.entries(finalGroups).sort((a, b) => {
      if (b[1].count !== a[1].count) return b[1].count - a[1].count;
      if (b[1].weight !== a[1].weight) return b[1].weight - a[1].weight;
      return parseFloat(a[0]) - parseFloat(b[0]);
    });
    const finalKey = orderedFinalGroups[0][0];
    const finalBucket = orderedFinalGroups[0][1];

    const initialGroups = {};
    for (const item of finalBucket.items) {
      const key = lineKey(item.initial.handicap_value);
      if (!initialGroups[key]) initialGroups[key] = {items: [], count: 0, weight: 0};
      initialGroups[key].items.push(item);
      initialGroups[key].count += 1;
      initialGroups[key].weight += item._priority || 1;
    }
    const orderedInitialGroups = Object.entries(initialGroups).sort((a, b) => {
      if (b[1].count !== a[1].count) return b[1].count - a[1].count;
      if (b[1].weight !== a[1].weight) return b[1].weight - a[1].weight;
      return parseFloat(a[0]) - parseFloat(b[0]);
    });
    const initialKey = orderedInitialGroups[0][0];
    const initialBucket = orderedInitialGroups[0][1];

    const finalLineCompanies = finalBucket.items;
    const initialLineCompanies = initialBucket.items;
    const finalLabel = finalLineCompanies[0]?.final?.handicap_text || '';
    const initialLabel = initialLineCompanies[0]?.initial?.handicap_text || '';
    const initial = {
      home_water: round4(avg(initialLineCompanies.map(x => Number(x.initial.home_water)))),
      handicap_text: initialLabel,
      handicap_value: parseFloat(initialKey),
      handicap: parseFloat(initialKey),
      away_water: round4(avg(initialLineCompanies.map(x => Number(x.initial.away_water)))),
    };
    const final = {
      home_water: round4(avg(finalLineCompanies.map(x => Number(x.final.home_water)))),
      handicap_text: finalLabel,
      handicap_value: parseFloat(finalKey),
      handicap: parseFloat(finalKey),
      away_water: round4(avg(finalLineCompanies.map(x => Number(x.final.away_water)))),
    };
    const allCompanies = companies
      .slice()
      .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
      .map(x => ({
        company: x.company,
        initial: x.initial,
        final: x.final,
        delta: x.delta,
        _source: x._source,
        _matched_line: x._matched_line,
      }));
    const consensusCompanies = finalLineCompanies
      .slice()
      .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
      .map(x => x.company);
    return JSON.stringify({
      found:true,
      parsed:true,
      company: consensusCompanies[0] || allCompanies[0]?.company || '',
      company_mode: finalLineCompanies.length > 1 ? 'multi_company_consensus' : 'single_company',
      initial,
      final,
      delta:{
        home_water: round4(final.home_water - initial.home_water),
        handicap_value: round4(final.handicap_value - initial.handicap_value),
        away_water: round4(final.away_water - initial.away_water),
      },
      consensus:{
        mode: finalLineCompanies.length > 1 ? 'multi_company_consensus' : 'single_company',
        final_handicap: final.handicap_value,
        final_handicap_text: final.handicap_text,
        initial_handicap: initial.handicap_value,
        initial_handicap_text: initial.handicap_text,
        final_line_count: finalLineCompanies.length,
        initial_line_count: initialLineCompanies.length,
        company_count: allCompanies.length,
        companies: consensusCompanies,
        all_companies: allCompanies.map(x => x.company),
      },
      companies: allCompanies,
      _source:'multi_company_consensus',
      _matched_line: finalLineCompanies[0]?._matched_line || ''
    });
  }

  const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('平均指数'));
  if(!row) return JSON.stringify({found:false});
  const s=(row.innerText||'').replace(/\s+/g,' ').trim();
  const m=s.match(/平均指数\s*(\d+\.\d+)\s*([\u4e00-\u9fff/]+)\s*(\d+\.\d+)\s*(\d+\.\d+)\s*([\u4e00-\u9fff/]+)\s*(\d+\.\d+)/);
  if(!m) return JSON.stringify({found:true, parsed:false, text:s});
  const initialText = m[2].trim();
  const finalText = m[5].trim();
  const initialValue = parseHandicap(initialText);
  const finalValue = parseHandicap(finalText);
  const initial={home_water:parseFloat(m[1]), handicap_text:initialText, handicap_value:initialValue, handicap:initialValue, away_water:parseFloat(m[3])};
  const final={home_water:parseFloat(m[4]), handicap_text:finalText, handicap_value:finalValue, handicap:finalValue, away_water:parseFloat(m[6])};
  const delta=(a,b)=> (a==null||b==null) ? null : +(a-b).toFixed(4);
  return JSON.stringify({
    found:true,
    parsed:true,
    company:'平均指数',
    company_mode:'average_row_fallback',
    initial,
    final,
    delta:{home_water:delta(final.home_water, initial.home_water), handicap_value:delta(final.handicap_value, initial.handicap_value), away_water:delta(final.away_water, initial.away_water)},
    consensus:{mode:'average_row_fallback', company_count:0, filtered_company_count:0, companies:[], all_companies:[], final_handicap: final.handicap_value, final_handicap_text: final.handicap_text, initial_handicap: initial.handicap_value, initial_handicap_text: initial.handicap_text},
    companies:[]
  });
})()
"""
    return bu.eval_json(js)


def _parse_totals_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    """Parse Over/Under (大小球) lines on current page.

    New behavior:
    - Parse as many company rows as possible from tables and body text.
    - Build a market consensus line from the most common final line.
    - Keep the company-level details for traceability.
    """
    js = "(() => {\n" + _DEOBFUSCATE_HELPER_JS + r"""
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const body = visibleInnerText(document.body);
  const lines = body.split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const compact = body.replace(/\s+/g, ' ').trim();

  const aliasPairs = [
    ['澳门彩票', '澳门彩票'],
    ['澳门', '澳门彩票'],
    ['Bet365', 'Bet365'],
    ['bet365', 'Bet365'],
    ['皇冠', '皇冠'],
    ['Pinnacle', 'Pinnacle'],
    ['平博', '平博'],
    ['SBOBET', 'SBOBET'],
    ['SBO', 'SBOBET'],
    ['12BET', '12BET'],
    ['易胜博', '易胜博'],
    ['威廉.希尔', '威廉希尔'],
    ['威廉希尔', '威廉希尔'],
    ['立博', '立博'],
    ['Bwin', 'Bwin'],
    ['Interwetten', 'Interwetten'],
    ['伟德', '伟德'],
    ['韦德', '伟德'],
    ['香港马会', '香港马会'],
    ['明陞', '明陞'],
    ['利记', '利记']
  ];
  const companyAliases = aliasPairs.map(x => x[0]);
  const priority = {
    'Bet365': 9,
    '皇冠': 8,
    'Pinnacle': 8,
    '澳门彩票': 7,
    '易胜博': 6,
    '威廉希尔': 6,
    '立博': 5,
    'Bwin': 4,
    'Interwetten': 4,
    '平博': 4,
    'SBOBET': 4,
    '12BET': 4,
    '伟德': 3,
    '香港马会': 3,
    '明陞': 3,
    '利记': 3,
  };
  const numRe = /\d+(?:\.\d+)?/g;

  const normalizeLineValue = (v) => Math.round(Number(v) * 4) / 4;
  const lineKey = (v) => normalizeLineValue(v).toFixed(2);
  const round4 = (v) => +Number(v).toFixed(4);
  const avg = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  const normalizeCompany = (name) => {
    const row = aliasPairs.find(x => x[0] === name);
    return row ? row[1] : name;
  };

  const parseLine = (line, companyAlias, sourceTag) => {
    const idx = line.indexOf(companyAlias);
    if (idx < 0) return null;
    const company = normalizeCompany(companyAlias);
    const tail = line.slice(idx + companyAlias.length).trim();
    const nums = (tail.match(numRe) || []).map(x => parseFloat(x));
    if (nums.length < 6) return null;
    return {
      company,
      initial: { over: nums[0], line: nums[1], under: nums[2] },
      final: { over: nums[3], line: nums[4], under: nums[5] },
      delta: {
        over: round4(nums[3] - nums[0]),
        line: round4(nums[4] - nums[1]),
        under: round4(nums[5] - nums[2]),
      },
      _source: sourceTag,
      _matched_line: line,
      _priority: priority[company] || 1,
    };
  };

  const dedup = new Map();
  const pushCandidate = (item) => {
    if (!item) return;
    const key = [
      item.company,
      lineKey(item.initial.line),
      lineKey(item.final.line),
      round4(item.initial.over),
      round4(item.initial.under),
      round4(item.final.over),
      round4(item.final.under)
    ].join('|');
    if (!dedup.has(key)) dedup.set(key, item);
  };

  const tableRows = [...document.querySelectorAll('tr')].map(tr => visibleInnerText(tr).replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const company of companyAliases) {
    for (const line of tableRows) {
      pushCandidate(parseLine(line, company, 'table_row'));
    }
  }
  for (const company of companyAliases) {
    for (const line of lines) {
      pushCandidate(parseLine(line, company, 'body_text_line'));
    }
  }
  for (const company of companyAliases) {
    const idx = compact.indexOf(company);
    if (idx < 0) continue;
    const windowText = compact.slice(Math.max(0, idx - 8), idx + 140);
    pushCandidate(parseLine(windowText, company, 'body_text_window'));
  }

  const companies = [...dedup.values()];
  if (!companies.length) {
    const avgRow = [...document.querySelectorAll('tr')].find(tr => {
      const txt = (tr.innerText || '').replace(/\s+/g, ' ').trim();
      return txt.includes('平均指数') || txt.includes('澳门');
    });
    if (!avgRow) return JSON.stringify({found:false, _source:'multi_company_fallback'});
    const tds = [...avgRow.querySelectorAll('td')].map(td => (td.innerText||'').replace(/\s+/g,' ').trim());
    const nums = [];
    for (const td of tds) {
      const m = td.match(/\d+(?:\.\d+)?/g) || [];
      for (const x of m) nums.push(parseFloat(x));
    }
    if (nums.length < 6) {
      return JSON.stringify({found:true, parsed:false, text:(avgRow.innerText||'').replace(/\s+/g,' ').trim(), tds});
    }
    const initial = { over: nums[0], line: nums[1], under: nums[2] };
    const final = { over: nums[3], line: nums[4], under: nums[5] };
    return JSON.stringify({
      found:true,
      parsed:true,
      company:'平均指数',
      initial,
      final,
      delta:{
        over: round4(final.over - initial.over),
        line: round4(final.line - initial.line),
        under: round4(final.under - initial.under),
      },
      consensus:{
        mode:'average_row_fallback',
        company_count:0,
        final_line: final.line,
        initial_line: initial.line,
        companies:[]
      },
      companies:[]
    });
  }

  const finalGroups = {};
  for (const item of companies) {
    const key = lineKey(item.final.line);
    if (!finalGroups[key]) finalGroups[key] = {items: [], count: 0, weight: 0};
    finalGroups[key].items.push(item);
    finalGroups[key].count += 1;
    finalGroups[key].weight += item._priority || 1;
  }
  const orderedFinalGroups = Object.entries(finalGroups).sort((a, b) => {
    if (b[1].count !== a[1].count) return b[1].count - a[1].count;
    if (b[1].weight !== a[1].weight) return b[1].weight - a[1].weight;
    return parseFloat(b[0]) - parseFloat(a[0]);
  });
  const finalKey = orderedFinalGroups[0][0];
  const finalBucket = orderedFinalGroups[0][1];

  const initialGroups = {};
  for (const item of finalBucket.items) {
    const key = lineKey(item.initial.line);
    if (!initialGroups[key]) initialGroups[key] = {items: [], count: 0, weight: 0};
    initialGroups[key].items.push(item);
    initialGroups[key].count += 1;
    initialGroups[key].weight += item._priority || 1;
  }
  const orderedInitialGroups = Object.entries(initialGroups).sort((a, b) => {
    if (b[1].count !== a[1].count) return b[1].count - a[1].count;
    if (b[1].weight !== a[1].weight) return b[1].weight - a[1].weight;
    return parseFloat(b[0]) - parseFloat(a[0]);
  });
  const initialKey = orderedInitialGroups[0][0];
  const initialBucket = orderedInitialGroups[0][1];

  const finalLineCompanies = finalBucket.items;
  const consensusInitialItems = initialBucket.items;
  const initial = {
    over: round4(avg(consensusInitialItems.map(x => Number(x.initial.over)))),
    line: parseFloat(initialKey),
    under: round4(avg(consensusInitialItems.map(x => Number(x.initial.under)))),
  };
  const final = {
    over: round4(avg(finalLineCompanies.map(x => Number(x.final.over)))),
    line: parseFloat(finalKey),
    under: round4(avg(finalLineCompanies.map(x => Number(x.final.under)))),
  };
  const allCompanies = companies
    .slice()
    .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
    .map(x => ({
      company: x.company,
      initial: x.initial,
      final: x.final,
      delta: x.delta,
      _source: x._source,
      _matched_line: x._matched_line,
    }));

  const consensusCompanies = finalLineCompanies
    .slice()
    .sort((a, b) => (b._priority || 1) - (a._priority || 1) || a.company.localeCompare(b.company))
    .map(x => x.company);

  return JSON.stringify({
    found:true,
    parsed:true,
    company: consensusCompanies[0] || allCompanies[0]?.company || '',
    company_mode: finalLineCompanies.length > 1 ? 'multi_company_consensus' : 'single_company',
    initial,
    final,
    delta:{
      over: round4(final.over - initial.over),
      line: round4(final.line - initial.line),
      under: round4(final.under - initial.under),
    },
    consensus:{
      mode: finalLineCompanies.length > 1 ? 'multi_company_consensus' : 'single_company',
      final_line: final.line,
      initial_line: initial.line,
      final_line_count: finalLineCompanies.length,
      initial_line_count: consensusInitialItems.length,
      company_count: allCompanies.length,
      companies: consensusCompanies,
      all_companies: allCompanies.map(x => x.company),
    },
    companies: allCompanies,
    _source:'multi_company_consensus',
    _matched_line: finalLineCompanies[0]?._matched_line || ''
  });
})()
"""
    return bu.eval_json(js)


def _parse_kelly_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    # The 凯利 tab renders one row per bookmaker, structurally identical to the
    # 欧赔 table: each company row carries [初始凯利 主/平/客][最新凯利 主/平/客]
    # and a trailing 返还率. We harvest those per-company rows and build a
    # multi-company consensus, mirroring _parse_europe_on_current_page, instead of
    # reading a single 99家平均 aggregate cell (which only exposes the payout rate
    # and collapsed all three outcomes onto the same number).
    js = "(() => {\n" + _DEOBFUSCATE_HELPER_JS + r"""
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const aliasPairs = [
    ['Bet365', 'Bet365'], ['bet365', 'Bet365'],
    ['皇冠', '皇冠'], ['Pinnacle', 'Pinnacle'], ['平博', '平博'],
    ['SBOBET', 'SBOBET'], ['SBO', 'SBOBET'], ['12BET', '12BET'],
    ['易胜博', '易胜博'], ['威廉.希尔', '威廉希尔'], ['威廉希尔', '威廉希尔'],
    ['立博', '立博'], ['Bwin', 'Bwin'], ['Interwetten', 'Interwetten'],
    ['伟德', '伟德'], ['韦德', '伟德'], ['香港马会', '香港马会'],
    ['澳门彩票', '澳门彩票'], ['澳门', '澳门彩票'], ['明陞', '明陞'], ['利记', '利记']
  ];
  const priority = {
    'Bet365':9,'皇冠':8,'Pinnacle':8,'澳门彩票':7,'易胜博':6,'威廉希尔':6,
    '立博':5,'Interwetten':4,'Bwin':4,'平博':4,'SBOBET':4,'12BET':4,
    '伟德':3,'香港马会':3,'明陞':3,'利记':3
  };
  const round4 = (v) => +Number(v).toFixed(4);
  const normalizeCompany = (name) => { const r = aliasPairs.find(x => x[0] === name); return r ? r[1] : name; };
  const sanitizeText = (t) => (t || '').replace(/[!#＊*·•|｜]/g, '').replace(/\s+/g, ' ').trim();
  const kellyRe = /\d+\.\d{2}/g;
  const okKelly = (v) => Number.isFinite(v) && v >= 0.3 && v <= 2.5;
  const median = (arr) => {
    const vals = (arr || []).filter(v => Number.isFinite(v)).slice().sort((a, b) => a - b);
    if (!vals.length) return null;
    const mid = Math.floor(vals.length / 2);
    return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
  };
  const isNear = (v, med, pct) => Number.isFinite(v) && Number.isFinite(med) && med > 0 && Math.abs(v - med) / med <= pct;
  const weightedAvg = (items, getter) => {
    let sum = 0, wsum = 0;
    for (const it of items || []) {
      const val = getter(it); const w = it._priority || 1;
      if (!Number.isFinite(val) || w <= 0) continue;
      sum += val * w; wsum += w;
    }
    return wsum > 0 ? sum / wsum : null;
  };

  const parseLine = (line, alias) => {
    const cleanLine = sanitizeText(line);
    const cleanAlias = sanitizeText(alias);
    const idx = cleanLine.toLowerCase().indexOf(cleanAlias.toLowerCase());
    if (idx < 0) return null;
    const company = normalizeCompany(alias);
    const tail = cleanLine.slice(idx + cleanAlias.length).trim();
    const nums = (tail.match(kellyRe) || []).map(parseFloat);
    if (nums.length < 6) return null;
    const initial = { home: nums[0], draw: nums[1], away: nums[2] };
    const final = { home: nums[3], draw: nums[4], away: nums[5] };
    const vals = [initial.home, initial.draw, initial.away, final.home, final.draw, final.away];
    if (!vals.every(okKelly)) return null;
    // The 7th number, when present, is the 返还率 (~0.8..1.2).
    const payout = (nums[6] !== undefined && nums[6] >= 0.8 && nums[6] <= 1.2) ? nums[6] : null;
    return {
      company, initial, final,
      delta: { home: round4(final.home - initial.home), draw: round4(final.draw - initial.draw), away: round4(final.away - initial.away) },
      payout_rate: payout,
      _priority: priority[company] || 1,
      _matched_line: cleanLine,
    };
  };

  const dedup = new Map();
  const push = (item) => {
    if (!item) return;
    const key = [item.company, round4(item.initial.home), round4(item.initial.draw), round4(item.initial.away), round4(item.final.home), round4(item.final.draw), round4(item.final.away)].join('|');
    if (!dedup.has(key)) dedup.set(key, item);
  };

  const aliases = aliasPairs.map(x => x[0]);
  const tableRows = [...document.querySelectorAll('tr')].map(tr => visibleInnerText(tr).replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const a of aliases) for (const line of tableRows) push(parseLine(line, a));
  const bodyLines = visibleInnerText(document.body).split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const a of aliases) for (const line of bodyLines) push(parseLine(line, a));

  const companies = [...dedup.values()];
  if (!companies.length) return JSON.stringify({found:false});

  const medHome = median(companies.map(x => x.final.home));
  const medDraw = median(companies.map(x => x.final.draw));
  const medAway = median(companies.map(x => x.final.away));
  let filtered = companies.filter(x => isNear(x.final.home, medHome, 0.28) && isNear(x.final.draw, medDraw, 0.30) && isNear(x.final.away, medAway, 0.30));
  if (!filtered.length) filtered = companies;

  const initial = {
    home: round4(weightedAvg(filtered, x => x.initial.home)),
    draw: round4(weightedAvg(filtered, x => x.initial.draw)),
    away: round4(weightedAvg(filtered, x => x.initial.away)),
  };
  const final = {
    home: round4(weightedAvg(filtered, x => x.final.home)),
    draw: round4(weightedAvg(filtered, x => x.final.draw)),
    away: round4(weightedAvg(filtered, x => x.final.away)),
  };
  const payoutVals = companies.map(x => x.payout_rate).filter(v => Number.isFinite(v));
  const filteredNames = filtered.slice().sort((a, b) => (b._priority||1)-(a._priority||1) || a.company.localeCompare(b.company)).map(x => x.company);
  const allCompanies = companies.slice().sort((a, b) => (b._priority||1)-(a._priority||1) || a.company.localeCompare(b.company))
    .map(x => ({company:x.company, initial:x.initial, final:x.final, delta:x.delta, payout_rate:x.payout_rate}));

  return JSON.stringify({
    found:true,
    company: filteredNames[0] || allCompanies[0]?.company || '',
    company_mode: filtered.length > 1 ? 'multi_company_consensus' : 'single_company',
    initial, final,
    delta:{ home: round4(final.home - initial.home), draw: round4(final.draw - initial.draw), away: round4(final.away - initial.away) },
    payout_rate: payoutVals.length ? round4(median(payoutVals)) : null,
    consensus:{
      mode: filtered.length > 1 ? 'multi_company_consensus' : 'single_company',
      company_count: allCompanies.length,
      filtered_company_count: filtered.length,
      companies: filteredNames,
      all_companies: allCompanies.map(x => x.company),
      final_median: {home: round4(medHome), draw: round4(medDraw), away: round4(medAway)},
    },
    companies: allCompanies,
    _source:'multi_company_consensus',
  });
})()
"""
    return bu.eval_json(js)


def _parse_kelly_anywhere_on_page(bu: BrowserUse) -> Dict[str, Any]:
    """Fallback: scan any table/text row that exposes a full 主/平/客 kelly triplet.

    Used when the per-company consensus parser finds no recognizable bookmaker
    rows. We accept any row carrying at least six in-range (0.3..2.5) x.xx values
    — first three are 初始凯利 主/平/客, next three 最新凯利 主/平/客, an optional
    seventh is the 返还率. A 99家平均 aggregate row is preferred when present.
    """
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const toNums = (s) => (String(s||'').match(/\d+\.\d{2}/g)||[]).map(parseFloat);
  const okKelly = (v) => Number.isFinite(v) && v >= 0.3 && v <= 2.5;
  const round4 = (v) => +Number(v).toFixed(4);

  const buildFromNums = (nums, source) => {
    if (nums.length < 6) return null;
    const init = nums.slice(0, 3);
    const fin = nums.slice(3, 6);
    if (!init.every(okKelly) || !fin.every(okKelly)) return null;
    const initial = {home:init[0], draw:init[1], away:init[2]};
    const final = {home:fin[0], draw:fin[1], away:fin[2]};
    const payout = (nums[6] !== undefined && nums[6] >= 0.8 && nums[6] <= 1.2) ? nums[6] : null;
    return JSON.stringify({
      found:true, initial, final,
      delta:{home:round4(final.home-initial.home), draw:round4(final.draw-initial.draw), away:round4(final.away-initial.away)},
      payout_rate:payout, _source:source
    });
  };

  const allRows = [...document.querySelectorAll('tr')];
  const aggRows = allRows.filter(tr => (tr.innerText||'').includes('99家平均') || (tr.innerText||'').includes('平均'));
  // Try aggregate rows first (most stable), then any other row.
  for (const row of [...aggRows, ...allRows]) {
    const tds = [...row.querySelectorAll('td')].map(td => (td.innerText||'').trim());
    // Dedicated kelly table: tds[1]=初始(3) tds[2]=最新(3) tds[3]=返还率.
    if (tds.length >= 3) {
      const init = toNums(tds[1]);
      const fin = toNums(tds[2]);
      if (init.length >= 3 && fin.length >= 3 && init.slice(0,3).every(okKelly) && fin.slice(0,3).every(okKelly)) {
        const out = buildFromNums([...init.slice(0,3), ...fin.slice(0,3), ...toNums(tds[3]||'')], 'row_tds');
        if (out) return out;
      }
    }
    const out = buildFromNums(toNums(row.innerText), 'row_innerText');
    if (out) return out;
  }

  // Last resort: scan body text lines.
  const lines = (document.body?.innerText || '').split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  for (const line of lines) {
    const out = buildFromNums(toNums(line), 'body_line');
    if (out) return out;
  }

  return JSON.stringify({found:false});
})()
"""
    return bu.eval_json(js)


def _find_match_id(
    bu: Any,
    league: str,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    alias_table: Dict[str, Any] | None = None,
    strict_identity: bool = False,
) -> Dict[str, Any]:
    online_search_only = _prefers_online_team_search(league)
    if online_search_only:
        online = _find_match_id_via_online_search(
            league=league,
            team1=team1,
            team2=team2,
            alias_table=alias_table or {},
        )
        if isinstance(online, dict) and online.get("match_id"):
            return online

    def find_rows_in_section(date_hint_current: str, time_hint_current: str) -> Dict[str, Any]:
        return _find_rows_in_date_section(
            bu,
            team1,
            team2,
            date_hint=date_hint_current,
            time_hint=time_hint_current,
            league=league,
            alias_table=alias_table or {},
            limit=5,
        )

    def find_rows(date_hint_current: str, time_hint_current: str) -> Dict[str, Any]:
        return _find_rows_fuzzy(
            bu,
            team1,
            team2,
            date_hint=date_hint_current,
            time_hint=time_hint_current,
            league=league,
            alias_table=alias_table or {},
            limit=5,
        )

    def find_rows_anywhere(date_hint_current: str, time_hint_current: str) -> Dict[str, Any]:
        return _find_rows_anywhere_on_current_page(
            bu,
            team1,
            team2,
            date_hint=date_hint_current,
            time_hint=time_hint_current,
            league=league,
            alias_table=alias_table or {},
            limit=5,
        )

    candidate_dates = _candidate_date_hints(date_hint, time_hint, strict_identity=strict_identity)
    if not candidate_dates:
        candidate_dates = [date_hint]

    def search_with_candidates(relax_time: bool = False) -> Dict[str, Any]:
        effective_time_hint = "" if relax_time else time_hint
        for current_date in candidate_dates:
            found_local = find_rows_in_section(current_date, effective_time_hint)
            if isinstance(found_local, dict) and found_local.get("rows"):
                best_local = _select_best_schedule_row(
                    found_local.get("rows") or [],
                    team1=team1,
                    team2=team2,
                    league=league,
                    alias_table=alias_table or {},
                )
                if isinstance(best_local, dict) and not _is_broad_round_summary_row(best_local):
                    found_local["_best_row"] = best_local
                    if current_date and current_date != date_hint:
                        found_local["_matched_date_hint"] = current_date
                    if relax_time and time_hint:
                        found_local["_matched_time_hint"] = effective_time_hint
                    return found_local
            found_local = find_rows(current_date, effective_time_hint)
            if isinstance(found_local, dict) and found_local.get("rows"):
                best_local = _select_best_schedule_row(
                    found_local.get("rows") or [],
                    team1=team1,
                    team2=team2,
                    league=league,
                    alias_table=alias_table or {},
                )
                if isinstance(best_local, dict) and not _is_broad_round_summary_row(best_local):
                    found_local["_best_row"] = best_local
                    if current_date and current_date != date_hint:
                        found_local["_matched_date_hint"] = current_date
                    if relax_time and time_hint:
                        found_local["_matched_time_hint"] = effective_time_hint
                    return found_local
            # Relaxed midnight fallback should avoid broad page-wide scans; otherwise
            # large container text can incorrectly match unrelated rows and collapse
            # multiple matches onto the same MatchID.
            if relax_time or strict_identity:
                continue
            found_local = find_rows_anywhere(current_date, effective_time_hint)
            if isinstance(found_local, dict) and found_local.get("rows"):
                best_local = _select_best_schedule_row(
                    found_local.get("rows") or [],
                    team1=team1,
                    team2=team2,
                    league=league,
                    alias_table=alias_table or {},
                )
                if isinstance(best_local, dict) and not _is_broad_round_summary_row(best_local):
                    found_local["_best_row"] = best_local
                    if current_date and current_date != date_hint:
                        found_local["_matched_date_hint"] = current_date
                    if relax_time and time_hint:
                        found_local["_matched_time_hint"] = effective_time_hint
                    return found_local
        return {}

    display_league = _normalize_okooo_league_name(league)
    bu.open(REMEN_URL)

    # Click the league entry by visible text.
    click_league = r"""
(() => {
  const name = %s;
  const els = Array.from(document.querySelectorAll('a,div,span,button'));
  const el = els.find(e => ((e.innerText || '').trim() === name));
  if (!el) return JSON.stringify({clicked:false, reason:'league not found'});
  el.click();
  return JSON.stringify({clicked:true, tag:el.tagName});
})()
""" % json.dumps(display_league, ensure_ascii=False)
    bu.eval_json(click_league)
    time.sleep(2.0)

    if date_hint:
        _navigate_schedule_to_month(bu, date_hint, max_steps=18)

    found = search_with_candidates()
    if not isinstance(found, dict) or not found.get("rows"):
        league_url = _mobile_league_url(league)
        if league_url:
            bu.open(league_url)
            time.sleep(2.0)
            if date_hint:
                _navigate_schedule_to_month(bu, date_hint, max_steps=18)
            found = search_with_candidates()

    # If still not found, try scrolling to load more schedule blocks.
    if not isinstance(found, dict) or not found.get("rows"):
        for _ in range(10):
            _eval_scroll_to_bottom(bu)
            found = search_with_candidates()
            if isinstance(found, dict) and found.get("rows"):
                break
        # One more try from top (some pages lazy-load above fold).
        if not isinstance(found, dict) or not found.get("rows"):
            _eval_scroll_to_top(bu)
            found = search_with_candidates()
    if (not isinstance(found, dict) or not found.get("rows")) and time_hint and not strict_identity:
        found = search_with_candidates(relax_time=True)
    if not isinstance(found, dict) or not found.get("rows"):
        if not strict_identity or online_search_only:
            cached = _find_match_id_from_schedule_cache(
                league=league,
                team1=team1,
                team2=team2,
                candidate_dates=candidate_dates,
                alias_table=alias_table or {},
            )
            if isinstance(cached, dict) and cached.get("match_id"):
                return cached
            online = _find_match_id_via_online_search(
                league=league,
                team1=team1,
                team2=team2,
                alias_table=alias_table or {},
            )
            if isinstance(online, dict) and online.get("match_id"):
                return online
    if not isinstance(found, dict) or not found.get("rows"):
        if online_search_only:
            raise RuntimeError(f"未通过球队搜索或赛程缓存找到 {team1} 和 {team2} 的比赛ID")
        raise RuntimeError(f"未在联赛赛程中找到包含 {team1} 和 {team2} 的比赛行(可尝试补充别名/时间)")

    best = found.get("_best_row") if isinstance(found.get("_best_row"), dict) else _select_best_schedule_row(
        found.get("rows") or [],
        team1=team1,
        team2=team2,
        league=league,
        alias_table=alias_table or {},
    )
    if not isinstance(best, dict):
        best = found["rows"][0]
    return {"match_id": best["mid"], "schedule_row": best}


def _current_url(bu: BrowserUse) -> str:
    try:
        return str(bu.eval_json("(() => JSON.stringify({u: location.href}))()").get("u") or "")
    except Exception:
        return ""


def _parse_lineup_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    """Parse okooo form.php (阵容) page: squad/starting-XI value + injuries + XI.

    The page is a mirrored two-column layout that linearises to
    ``[home] [label] [away]`` per row. We extract the aggregate signals that
    actually drive λ (total squad value, starting-XI value, injury count) plus a
    best-effort starting-XI player list. Values are normalised to 万 (10k EUR).
    """
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});
  const body = document.body?.innerText || '';
  const compact = body.replace(/\s+/g, ' ').trim();
  if (!/阵容|首发/.test(compact)) return JSON.stringify({found:false});

  const toWan = (s) => {
    if (!s) return null;
    const m = String(s).match(/([\d.]+)\s*(亿|万)?/);
    if (!m) return null;
    let v = parseFloat(m[1]);
    if (m[2] === '亿') v *= 10000;
    return +v.toFixed(2);
  };
  const parseInjury = (s) => {
    s = String(s || '');
    if (/无缺阵|暂无/.test(s)) return {count: 0, value_wan: 0};
    const cm = s.match(/(\d+)\s*人/);
    const vm = s.match(/([\d.]+\s*[亿万])/);
    return {count: cm ? parseInt(cm[1], 10) : 0, value_wan: vm ? toWan(vm[1]) : 0};
  };

  const result = {found: false};

  // 阵容概览: 总身价 (home left / away right)
  let m = compact.match(/阵容概览\s*([\d.]+\s*[亿万])\s*总身价[€\s]*([\d.]+\s*[亿万])/);
  if (m) { result.home_squad_value_wan = toWan(m[1]); result.away_squad_value_wan = toWan(m[2]); }

  // 伤停身价 row: home injury token (left) / away injury token (right)
  m = compact.match(/总身价[€\s]*[\d.]+\s*[亿万]\s*(无缺阵|[\d.]+\s*[亿万]?\s*\d+\s*人)\s*伤停身价[€\s]*(无缺阵|([\d.]+\s*[亿万])?\s*\d+\s*人)/);
  if (m) {
    const h = parseInjury(m[1]); const a = parseInjury(m[2]);
    result.home_injury_count = h.count; result.home_injury_value_wan = h.value_wan;
    result.away_injury_count = a.count; result.away_injury_value_wan = a.value_wan;
  }

  // 首发 row in 阵容实力对比: home starting value / away starting value
  m = compact.match(/([\d.]+\s*[亿万])\s*\d+cm\s*\/\s*\d+岁\s*首发\s*\d+cm\s*\/\s*\d+岁\s*([\d.]+\s*[亿万])/);
  if (m) { result.home_starting_value_wan = toWan(m[1]); result.away_starting_value_wan = toWan(m[2]); }

  // Best-effort starting XI player lists from 首发阵容对比 .. 预计伤停 section
  let seg = compact;
  const segStart = compact.indexOf('首发阵容对比');
  const segEnd = compact.indexOf('预计伤停');
  if (segStart >= 0) seg = compact.slice(segStart, segEnd > segStart ? segEnd : undefined);
  const homePlayers = [];
  const awayPlayers = [];
  let pm;
  const nameChars = '[\\u4e00-\\u9fffA-Za-zÀ-ÖØ-öø-ÿ·•・.．’\'-]{2,}';
  const homeRe = new RegExp('(\\d{1,2})\\s+(' + nameChars + ')\\s+(门将|后卫|中场|前锋)\\s+€\\s*([\\d.]+\\s*[亿万])', 'g');
  while ((pm = homeRe.exec(seg)) && homePlayers.length < 14) {
    homePlayers.push({number: parseInt(pm[1],10), name: pm[2], position: pm[3], value_wan: toWan(pm[4])});
  }
  const awayRe = new RegExp('(\\d{1,2})\\s+(门将|后卫|中场|前锋)\\s+(' + nameChars + ')\\s+€\\s*([\\d.]+\\s*[亿万])', 'g');
  while ((pm = awayRe.exec(seg)) && awayPlayers.length < 14) {
    awayPlayers.push({number: parseInt(pm[1],10), position: pm[2], name: pm[3], value_wan: toWan(pm[4])});
  }
  if (homePlayers.length) result.home_starting_xi = homePlayers;
  if (awayPlayers.length) result.away_starting_xi = awayPlayers;

  // success: prefer core λ value signal, but do not discard a fully parsed lineup tab
  // when the aggregate value row is absent/lazy. 阵容 tab 本身应给出 11 人名单。
  result.found = (result.home_starting_value_wan != null && result.away_starting_value_wan != null)
    || (homePlayers.length >= 11 && awayPlayers.length >= 11);
  return JSON.stringify(result);
})()
"""
    return bu.eval_json(js)


def _pull_lineup_leg(bu: BrowserUse, history_url: str, dwell: float) -> Dict[str, Any]:
    """From the hub, click 阵容 -> form.php, scroll to render lazy content, parse.

    form.php lazy-loads the starting-XI block on scroll, so we step-scroll to the
    bottom before parsing. Returns the lineup payload (or blocked/found:false).
    """
    _open_ready(bu, history_url, settle_seconds=2.0)
    lineup_click = _click_visible_text(bu, ["阵容", "首发阵容", "首发"], settle_seconds=HUB_NAV_SETTLE_SECONDS)
    lineup_url = _current_url(bu)
    if _page_blocked_now(bu):
        return {"blocked": True, "url": lineup_url or history_url, "_blocked_at": "hub_nav_zhenrong", "_flow": "hub_nav_zhenrong", "_click": lineup_click}
    # form.php lazy-loads on scroll; step to the bottom to render the full XI.
    for _ in range(8):
        try:
            bu.eval_json("(() => { window.scrollBy(0, document.body.scrollHeight); return '{}'; })()")
        except Exception:
            break
        time.sleep(1.0)
    try:
        bu.eval_json("(() => { window.scrollTo(0, 0); return '{}'; })()")
    except Exception:
        pass
    time.sleep(dwell)
    lineup = _parse_lineup_on_current_page(bu)
    lineup["url"] = _current_url(bu) or lineup_url or history_url
    lineup["_flow"] = "hub_nav_zhenrong"
    lineup["_click"] = lineup_click
    return lineup


def _pull_odds_leg(bu: BrowserUse, history_url: str, dwell: float) -> Dict[str, Any]:
    """From the hub, click 欧值 -> odds.php and parse 欧赔 + the inner 凯利 tab.

    Returns a dict with keys ``europe`` and ``kelly``. On a verification wall (after
    the OUZHI_RETRY_WAITS ladder is exhausted) both come back blocked; this never
    touches the already-captured 亚值/大小球.
    """
    _open_ready(bu, history_url, settle_seconds=2.0)
    europe_click = _click_visible_text(bu, ["欧值", "欧指", "欧赔", "赔率"], settle_seconds=HUB_NAV_SETTLE_SECONDS)
    europe_url = _current_url(bu)

    # The odds.php landing page often throws a transient verification wall. Rather
    # than giving up immediately, re-enter the odds page with escalating waits
    # (OUZHI_RETRY_WAITS, default 3s → 5s → 10s): wait, re-navigate from the hub,
    # and re-check. Only after the ladder is exhausted do we mark 欧赔/凯利 blocked.
    ouzhi_attempts = 0
    if _page_blocked_now(bu):
        for wait_secs in OUZHI_RETRY_WAITS:
            ouzhi_attempts += 1
            time.sleep(_jittered(wait_secs))
            _open_ready(bu, history_url, settle_seconds=2.0)
            europe_click = _click_visible_text(bu, ["欧值", "欧指", "欧赔", "赔率"], settle_seconds=HUB_NAV_SETTLE_SECONDS)
            europe_url = _current_url(bu)
            if not _page_blocked_now(bu):
                break

    if _page_blocked_now(bu):
        europe = {"blocked": True, "url": europe_url or history_url, "_blocked_at": "hub_nav_ouzhi", "_flow": "hub_nav_ouzhi", "_click": europe_click, "_ouzhi_retries": ouzhi_attempts}
        kelly = {"blocked": True, "url": europe_url or history_url, "_blocked_at": "hub_nav_ouzhi", "_flow": "hub_nav_ouzhi_inner", "_ouzhi_retries": ouzhi_attempts}
        return {"europe": europe, "kelly": kelly}

    # 欧赔 sometimes renders a transient/empty view right after navigation, which
    # the parser's blocked-keyword heuristics can misread as a verification wall.
    # Explicitly click the 欧赔/初赔 main tab to force the odds table to render,
    # then re-parse after a short settle and keep the better-scored result.
    time.sleep(dwell)
    europe = _parse_europe_on_current_page(bu)
    if not _is_success_payload(europe) or _score_europe_payload(europe) < (3, 2, 2):
        europe_tab_click = _click_visible_text(bu, ["欧赔", "初赔", "欧值", "欧指"], settle_seconds=CLICK_SETTLE_SECONDS)
        time.sleep(HUB_TAB_DWELL_SECONDS)
        europe_retry = _parse_europe_on_current_page(bu)
        europe = _pick_preferred_europe_result(europe, europe_retry)
        europe["_tab_click"] = europe_tab_click
    europe["url"] = europe_url or history_url
    europe["_flow"] = "hub_nav_ouzhi"
    europe["_click"] = europe_click
    if ouzhi_attempts:
        europe["_ouzhi_retries"] = ouzhi_attempts

    kelly_click = _click_visible_text(bu, ["凯利", "凯利指数"], settle_seconds=CLICK_SETTLE_SECONDS)
    time.sleep(dwell)
    kelly = _parse_kelly_on_current_page(bu)
    if isinstance(kelly, dict) and kelly.get("found") is False:
        kelly = _parse_kelly_anywhere_on_page(bu)
    kelly["url"] = _current_url(bu) or europe_url or history_url
    kelly["_flow"] = "hub_nav_ouzhi_inner"
    kelly["_click"] = kelly_click
    return {"europe": europe, "kelly": kelly}


def _extract_odds_only_from_hub(bu: BrowserUse, history_url: str, market_dwell_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Fresh-session odds-only pass: warm, land on the hub, pull 欧赔 + 凯利.

    Used to recover 欧赔/凯利 on a brand-new device fingerprint when the main hub
    run salvaged 亚值/大小球 but odds.php was walled. Returns a bundle whose
    ``found`` reflects only the odds markets; on a wall it returns a top-level
    blocked payload so _run_with_retries opens the breaker / reentry can apply.
    """
    dwell = MARKET_PARSE_DWELL_SECONDS if market_dwell_seconds is None else float(market_dwell_seconds)
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}

    legs = _pull_odds_leg(bu, history_url, dwell)
    europe = legs.get("europe") or {}
    kelly = legs.get("kelly") or {}
    blocked_markets = [
        name for name, payload in (("europe", europe), ("kelly", kelly))
        if isinstance(payload, dict) and payload.get("blocked")
    ]
    if blocked_markets and not (_is_success_payload(europe) or _is_success_payload(kelly)):
        return {
            "blocked": True,
            "url": history_url,
            "_blocked_at": ",".join(blocked_markets),
            "europe": europe,
            "kelly": kelly,
        }
    bundle = {
        "found": _is_success_payload(europe) or _is_success_payload(kelly),
        "url": history_url,
        "europe": europe,
        "kelly": kelly,
    }
    if blocked_markets:
        bundle["_partial_blocked_at"] = ",".join(blocked_markets)
    return bundle


def _extract_all_markets_from_hub(bu: BrowserUse, history_url: str, market_dwell_seconds: Optional[float] = None) -> Dict[str, Any]:
    """One warm session, real in-page navigation: pull all four markets.

    The per-match hub (history.php?MatchID=...) is a stats page, not an odds
    page: it exposes 亚指 and 欧指 as real navigation links. Clicking 亚指 loads
    handicap.php (亚盘 + an inner 大小球 tab); clicking 欧指 loads odds.php (欧赔
    + an inner 凯利 tab). Doing this inside one warm session mimics a real user
    landing on the match page and tabbing between markets, which keeps cookies
    and referer chains intact and avoids the cold deep-link that trips okooo's
    verification wall.

    `market_dwell_seconds` is an extra settle applied right before parsing each
    market (亚值/大小球/欧赔/凯利) so the live odds table is fully rendered; it
    defaults to MARKET_PARSE_DWELL_SECONDS and is freely configurable.

    Returns a bundle dict keyed by market; on a verification wall it returns a
    single blocked payload that the caller maps onto every market.
    """
    dwell = MARKET_PARSE_DWELL_SECONDS if market_dwell_seconds is None else float(market_dwell_seconds)
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}

    def _cur_url() -> str:
        try:
            return str(bu.eval_json("(() => JSON.stringify({u: location.href}))()").get("u") or "")
        except Exception:
            return ""

    # 1) 亚指 nav link -> handicap.php. Parse 亚盘, then the inner 大小球 tab.
    asian_click = _click_visible_text(bu, ["亚指", "亚盘", "亚值"], settle_seconds=HUB_NAV_SETTLE_SECONDS)
    asian_url = _cur_url()
    if _page_blocked_now(bu):
        return {"blocked": True, "url": asian_url or history_url, "_blocked_at": "hub_nav_yazhi"}
    time.sleep(dwell)
    # m.okooo handicap.php defaults to a single-company table (Pinnacle only) on
    # most match pages; this best-effort expander tries to surface the full
    # multi-company list before parsing. Diagnostics are kept so we can iterate
    # on label coverage when a page exposes a different trigger.
    asian_expand = _expand_asian_company_view(bu, dwell=dwell)
    asian = _parse_asian_on_current_page(bu)
    asian["url"] = asian_url or history_url
    asian["_flow"] = "hub_nav_yazhi"
    asian["_click"] = asian_click
    asian["_expand"] = asian_expand

    time.sleep(ASIAN_TO_TOTALS_DWELL_SECONDS)
    totals_click = _click_visible_text(bu, ["大小球", "大/小", "总进球"], settle_seconds=CLICK_SETTLE_SECONDS)
    time.sleep(dwell)
    totals = _parse_totals_on_current_page(bu)
    totals["url"] = _cur_url() or asian_url or history_url
    totals["_flow"] = "hub_nav_yazhi_inner"
    totals["_click"] = totals_click

    # 2) Back to hub, then pull the odds leg (欧值 -> odds.php: 欧赔 + 凯利). This
    # mirrors a real user tabbing to the odds page within the same warm session.
    odds_legs = _pull_odds_leg(bu, history_url, dwell)
    europe = odds_legs.get("europe") or {}
    kelly = odds_legs.get("kelly") or {}

    # 3) Lineup leg (阵容 -> form.php): best-effort starting-XI value + injuries.
    # Non-critical: a walled/empty lineup never escalates the bundle, since the
    # four odds markets are what gate success.
    try:
        lineup = _pull_lineup_leg(bu, history_url, dwell)
    except Exception as exc:
        lineup = {"found": False, "_error": str(exc)[:200], "_flow": "hub_nav_zhenrong"}

    asian_ok = _is_success_payload(asian)
    totals_ok = _is_success_payload(totals)
    blocked_markets = [
        name
        for name, payload in (("asian", asian), ("totals", totals), ("europe", europe), ("kelly", kelly))
        if isinstance(payload, dict) and payload.get("blocked")
    ]

    # Escalate to a top-level blocked payload (which the caller maps onto every
    # market and which opens the breaker + triggers verification-reentry) ONLY
    # when a wall was hit and nothing useful was salvaged. If the handicap.php leg
    # already returned real 亚值/大小球, preserve them and keep only the walled
    # 欧赔/凯利 blocked, so a single odds.php wall no longer throws away good data.
    if blocked_markets and not (asian_ok or totals_ok):
        return {
            "blocked": True,
            "url": history_url,
            "_blocked_at": ",".join(blocked_markets),
            "europe": europe,
            "kelly": kelly,
            "asian": asian,
            "totals": totals,
            "lineup": lineup,
        }

    bundle = {
        "found": (
            _is_success_payload(europe)
            or _is_success_payload(kelly)
            or asian_ok
            or totals_ok
        ),
        "url": history_url,
        "europe": europe,
        "kelly": kelly,
        "asian": asian,
        "totals": totals,
        "lineup": lineup,
    }
    if blocked_markets:
        bundle["_partial_blocked_at"] = ",".join(blocked_markets)
    return bundle


def _run_with_retries(
    _label: str,
    session_prefix: str,
    client_factory: Callable[[str], Any],
    extractor,
    *extractor_args: str,
    market_family: str = "",
    base_dir: str = "",
    breaker_match_id: str = "",
) -> Dict[str, Any]:
    attempts: list[Dict[str, Any]] = []
    last_data: Dict[str, Any] = {"found": False}
    max_attempts = len(RETRY_DELAYS)
    match_id_hint = str(breaker_match_id or (extractor_args[0] if extractor_args else "") or "").strip()

    if base_dir and market_family and match_id_hint:
        breaker = read_okooo_verification_breaker(base_dir, match_id_hint, market_family)
        if breaker.get("open"):
            return {
                "blocked": True,
                "verification_required": True,
                "status": "verification_required",
                "error": "ttl_circuit_open",
                "retry_strategy": "ttl_circuit_open",
                "breaker_open": True,
                "market_family": market_family,
                "breaker_expires_at": breaker.get("expires_at"),
                "match_id": match_id_hint,
                "_attempts": [],
            }

    # Always retry with fresh sessions. Headed mode is preferred because it is
    # empirically less likely to be blocked on okooo mobile pages.
    for index, delay in enumerate(RETRY_DELAYS, start=1):
        session = f"{session_prefix}_{datetime.now().strftime('%H%M%S')}_{index}"
        if base_dir and market_family:
            wait_for_okooo_market_slot(
                base_dir,
                min_interval_seconds=_jittered(OKOOO_MIN_REQUEST_INTERVAL),
            )
        bu = client_factory(session)
        stop_now = False
        stop_reason = ""
        try:
            data = extractor(bu, *extractor_args)
            if isinstance(data, dict) and data.get("blocked"):
                data = _mark_verification_required(data, bu)
                if base_dir and market_family and match_id_hint:
                    open_okooo_verification_breaker(
                        base_dir,
                        match_id_hint,
                        market_family,
                        details={"mobile_profile": data.get("mobile_profile")},
                    )
            last_data = data
            success = _is_success_payload(data)
            stop_now, stop_reason = _should_stop_retrying_from_payload(data)
            attempts.append(
                {
                    "attempt": index,
                    "client": bu.__class__.__name__,
                    "success": success,
                    "blocked": bool(isinstance(data, dict) and data.get("blocked")),
                    "verification_required": bool(isinstance(data, dict) and _is_verification_required_payload(data)),
                    "found": None if not isinstance(data, dict) else data.get("found"),
                    "parsed": None if not isinstance(data, dict) else data.get("parsed"),
                    "stop_retry": stop_now,
                    "stop_reason": stop_reason or None,
                }
            )
            if success:
                return _annotate_attempts(data, attempts)
        except Exception as exc:
            last_data = {"error": str(exc)}
            kind = _classify_retry_error_message(str(exc))
            stop_now = kind in {
                "blocked",
                "cdp_connection_refused",
                "chrome_port_unavailable",
                "chrome_start_failed",
                "chrome_binary_missing",
                "browser_use_missing",
            }
            if kind == "blocked":
                last_data = _mark_verification_required(last_data, bu)
                if base_dir and market_family and match_id_hint:
                    open_okooo_verification_breaker(
                        base_dir,
                        match_id_hint,
                        market_family,
                        details={"mobile_profile": last_data.get("mobile_profile")},
                    )
            attempts.append(
                {
                    "attempt": index,
                    "client": bu.__class__.__name__,
                    "success": False,
                    "error": str(exc),
                    "error_kind": kind,
                    "verification_required": kind == "blocked",
                    "stop_retry": stop_now,
                    "stop_reason": "verification_required" if kind == "blocked" else (kind if stop_now else None),
                }
            )
        finally:
            bu.close()

        if stop_now:
            break
        if index < max_attempts:
            time.sleep(delay)

    return _annotate_attempts(last_data, attempts)


def _run_with_verification_reentry(
    runner: Callable[[Callable[[str], Any], str], Dict[str, Any]],
    client_factory: Callable[[str], Any],
    session_prefix: str,
) -> Dict[str, Any]:
    first = runner(client_factory, session_prefix)
    if not _is_verification_required_payload(first):
        return first

    recovery = _build_verification_recovery_context(first)
    if recovery.mobile_profile is None:
        return first

    def recovered_client_factory(session_name: str) -> Any:
        client = client_factory(session_name)
        try:
            client.mobile_profile = recovery.mobile_profile
        except Exception:
            pass
        return client

    second = runner(recovered_client_factory, f"{session_prefix}_freshpool")
    if isinstance(second, dict):
        second["verification_reentry_count"] = 1
        second["reentry_from_mobile_profile"] = recovery.mobile_profile_meta or _mobile_profile_meta(recovery.mobile_profile)
        existing_reentry_profile = second.get("reentry_mobile_profile") if isinstance(second.get("reentry_mobile_profile"), dict) else None
        second["reentry_mobile_profile"] = existing_reentry_profile or _mobile_profile_meta(getattr(second, "mobile_profile", None))
        if not _is_verification_required_payload(second):
            second["reentered_after_verification"] = True
    return second


def _extract_all_markets_with_fallback(match_id: str, history_url: str, client_factory: Callable[[str], Any], session_prefix: str, *, base_dir: str = "", odds_fresh_session_recovery: bool = True) -> Dict[str, Any]:
    """One warm hub session, real in-page navigation, all four markets.

    The only supported flow: warm a session, land on the per-match hub
    (history.php), click 亚指/欧指 to navigate to the real odds pages, and tab
    between 大小球/凯利 in-page. There is no deep-link fallback — any market the
    hub cannot parse is returned as-is (blocked/found:false). On a hub
    verification wall every market inherits the blocked payload so the
    breaker/reentry machinery still applies.

    okooo guards odds.php (欧赔/凯利) more aggressively than handicap.php
    (亚值/大小球). When the main hub run salvaged 亚值/大小球 but odds.php was
    walled, and ``odds_fresh_session_recovery`` is set, we run a second
    odds-only pass on a brand-new device fingerprint (a fresh client_factory
    session mints a new mobile profile) and merge any recovered 欧赔/凯利 back in
    without touching the already-captured 亚值/大小球.
    """
    hub = _run_with_retries(
        "all_markets_hub",
        f"{session_prefix}_hub",
        client_factory,
        _extract_all_markets_from_hub,
        history_url,
        market_family=MARKET_FAMILY_HUB,
        base_dir=base_dir,
        breaker_match_id=match_id,
    )
    if _is_verification_required_payload(hub):
        return {"europe": hub, "kelly": hub, "asian": hub, "totals": hub, "lineup": None}

    europe = dict(hub.get("europe") or {}) if isinstance(hub, dict) else {}
    kelly = dict(hub.get("kelly") or {}) if isinstance(hub, dict) else {}
    asian = dict(hub.get("asian") or {}) if isinstance(hub, dict) else {}
    totals = dict(hub.get("totals") or {}) if isinstance(hub, dict) else {}
    lineup = hub.get("lineup") if isinstance(hub, dict) else None

    odds_walled = (europe.get("blocked") or kelly.get("blocked")) and not (
        _is_success_payload(europe) or _is_success_payload(kelly)
    )
    salvaged_handicap = _is_success_payload(asian) or _is_success_payload(totals)
    if odds_fresh_session_recovery and odds_walled and salvaged_handicap:
        recovered = _run_with_verification_reentry(
            lambda cf, sp: _run_with_retries(
                "odds_only_hub",
                f"{sp}_oddsonly",
                cf,
                _extract_odds_only_from_hub,
                history_url,
                market_family=MARKET_FAMILY_ODDS,
                base_dir=base_dir,
                breaker_match_id=match_id,
            ),
            client_factory,
            f"{session_prefix}_oddsfresh",
        )
        if isinstance(recovered, dict) and not _is_verification_required_payload(recovered):
            rec_europe = dict(recovered.get("europe") or {})
            rec_kelly = dict(recovered.get("kelly") or {})
            if _is_success_payload(rec_europe):
                rec_europe["_recovered_via"] = "odds_fresh_session"
                europe = rec_europe
            if _is_success_payload(rec_kelly):
                rec_kelly["_recovered_via"] = "odds_fresh_session"
                kelly = rec_kelly

    return {"europe": europe, "kelly": kelly, "asian": asian, "totals": totals, "lineup": lineup}


def main() -> None:
    global MARKET_PARSE_DWELL_SECONDS, OUZHI_RETRY_WAITS, OKOOO_MIN_REQUEST_INTERVAL
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="联赛名称（需与热门赛事页展示文本一致，如：法甲/英超/意甲...）")
    parser.add_argument("--team1", required=True, help="主队名称（用于赛程行匹配）")
    parser.add_argument("--team2", required=True, help="客队名称（用于赛程行匹配）")
    parser.add_argument("--match-id", default="", help="可选：直接指定 MatchID，跳过赛程匹配。")
    parser.add_argument(
        "--driver",
        choices=["browser-use", "local-chrome"],
        default="local-chrome",
        help="抓取驱动：默认 local-chrome；browser-use 仅用于显式调试",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_default_data_root() / "snapshots"),
        help="输出目录（默认写入用户目录下的 okooo-scraper/snapshots，避免污染仓库）。",
    )
    parser.add_argument(
        "--no-league-subdir",
        action="store_true",
        help="不按联赛分目录（默认会在 out-dir 下按联赛 slug 建子目录保存快照）。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="同一场比赛固定文件名并覆盖已有 JSON（默认开启时间戳命名）。",
    )
    parser.add_argument(
        "--no-matchid-dedupe",
        action="store_true",
        help="禁用按 match_id 自动覆盖（默认同 match_id 会覆盖已有 JSON）。",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="以有头浏览器运行 browser-use（仅显式指定 browser-use 时生效）",
    )
    parser.add_argument("--chrome-port", type=int, default=9222, help="local-chrome 模式使用的 CDP 端口")
    parser.add_argument(
        "--chrome-path",
        default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        help="Google Chrome 可执行文件路径",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default=str(_default_data_root() / "chrome_profile"),
        help="local-chrome 模式启动的独立用户目录（默认写入用户目录下，避免污染仓库）。",
    )
    parser.add_argument(
        "--date",
        default="",
        help="可选：比赛日期 YYYY-MM-DD，用于在赛程中更准确定位（例如 2026-04-22）。",
    )
    parser.add_argument(
        "--time",
        default="",
        help="可选：比赛时间 HH:MM，用于在赛程中更精准定位（例如 03:00）。",
    )
    parser.add_argument(
        "--strict-identity",
        action="store_true",
        help="严格按请求日期+主客队(+时间)定位比赛，禁用相邻日期与宽松页面兜底。",
    )
    parser.add_argument(
        "--market-dwell",
        type=float,
        default=None,
        help=f"每盘解析前的额外停留秒数（默认 {MARKET_PARSE_DWELL_SECONDS}，也可用 OKOOO_MARKET_DWELL 环境变量配置）。",
    )
    parser.add_argument(
        "--ouzhi-retry-waits",
        type=str,
        default=None,
        help="欧值(odds.php)撞验证墙后的阶梯重试等待秒数，逗号分隔（默认 3,5,10，也可用 OKOOO_OUZHI_RETRY_WAITS 环境变量配置）。",
    )
    parser.add_argument(
        "--min-request-interval",
        type=float,
        default=None,
        help=f"深链导航之间的进程级最小间隔秒数（带随机抖动，默认 {OKOOO_MIN_REQUEST_INTERVAL}，也可用 OKOOO_MIN_REQUEST_INTERVAL 环境变量配置）。固定 IP 单机抗封的核心闸门。",
    )
    parser.add_argument(
        "--no-odds-fresh-session",
        action="store_true",
        help="禁用欧赔/凯利在 odds.php 撞墙后的『换新设备指纹会话单独重抓』恢复（默认开启，且不影响已拿到的亚值/大小球）。",
    )
    parser.add_argument(
        "--odds-only",
        action="store_true",
        help="只抓欧赔/凯利：用一个独立冷会话直接走 hub→欧值→odds.php，完全不碰 handicap.php(亚值/大小球)，用于单独验证 odds.php 能否拿到。",
    )
    args = parser.parse_args()

    if args.market_dwell is not None:
        MARKET_PARSE_DWELL_SECONDS = float(args.market_dwell)
    if args.ouzhi_retry_waits is not None:
        OUZHI_RETRY_WAITS = _parse_wait_ladder(args.ouzhi_retry_waits, OUZHI_RETRY_WAITS)
    if args.min_request_interval is not None:
        OKOOO_MIN_REQUEST_INTERVAL = max(0.0, float(args.min_request_interval))

    event_name = f"{args.team1}vs{args.team2}"
    out_dir = Path(args.out_dir).resolve()
    if not args.no_league_subdir:
        out_dir = out_dir / _league_slug(args.league)
    out_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: some browser-use implementations may truncate/normalize session names.
    # Keep the random token at the beginning to reduce collision probability after truncation.
    token = uuid.uuid4().hex[:8]
    session_prefix = f"ok_{token}"

    chrome_meta: Dict[str, Any] = {}
    if args.driver == "local-chrome":
        chrome_meta = _ensure_local_chrome(args.chrome_port, args.chrome_path, args.chrome_user_data_dir)
        resolved_chrome_port = int(chrome_meta.get("port") or args.chrome_port)

        def client_factory(session_name: str) -> Any:
            return LocalChromeSession(port=resolved_chrome_port, session_name=session_name)

    else:

        def client_factory(session_name: str) -> Any:
            return BrowserUse(session=session_name, headed=args.headed)

    schedule_bu = None
    keep_schedule_session_alive = bool(
        args.driver == "local-chrome" and chrome_meta.get("started_by_script")
    )
    try:
        if args.match_id:
            match_id = str(args.match_id)
            found = {
                "match_id": match_id,
                "schedule_row": {
                    "mid": match_id,
                    "href": f"https://m.okooo.com/match/history.php?MatchID={match_id}",
                    "text": "",
                    "score": None,
                },
            }
        else:
            alias_table = _load_alias_table()
            schedule_bu = client_factory(f"{session_prefix}_sched")
            try:
                found = _find_match_id(
                    schedule_bu,
                    args.league,
                    args.team1,
                    args.team2,
                    date_hint=args.date,
                    time_hint=args.time,
                    alias_table=alias_table,
                    strict_identity=bool(args.strict_identity),
                )
            finally:
                if schedule_bu and not keep_schedule_session_alive:
                    schedule_bu.close()
                    schedule_bu = None
            match_id = found["match_id"]

        # Output path policy:
        # - If a snapshot with the same match_id already exists in out_dir, overwrite it.
        # - Else: if --overwrite is set, use a stable event filename.
        # - Else: create a timestamped filename.
        out_path: Path
        existing = None if args.no_matchid_dedupe else _find_existing_snapshot_by_match_id(
            out_dir,
            str(match_id),
            home_team=args.team1,
            away_team=args.team2,
            match_date=args.date,
        )
        if existing:
            out_path = existing
        else:
            if args.overwrite:
                filename = f"{_safe_filename(event_name)}.json"
            else:
                filename = f"{_safe_filename(event_name)}_{_now_stamp()}.json"
            out_path = out_dir / filename

        payload: Dict[str, Any] = {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "driver": args.driver,
            "chrome": chrome_meta if chrome_meta else None,
            "league": args.league,
            "event": event_name,
            "match_id": match_id,
            "match_date": args.date or "",
            "home_team": args.team1,
            "away_team": args.team2,
            "match_time": found["schedule_row"].get("text", ""),
            "schedule": found["schedule_row"],
            "欧赔": None,
            "亚值": None,
            "大小球": None,
            "凯利": None,
            "阵容": None,
        }
        # Single warm hub session: land on the home page, open the match hub
        # once, then flip 欧赔/凯利/亚值/大小球 tabs in-page. Per-market fallbacks
        # inside the wrapper handle anything the hub fails to parse.
        if args.odds_only:
            # Independent cold session that only touches odds.php (hub → 欧值 →
            # odds.php → 凯利 tab), never visiting handicap.php. Used to verify
            # whether 欧赔/凯利 can be captured on their own, isolated from the
            # handicap.php leg that may "warm" the IP first.
            all_markets = _run_with_verification_reentry(
                lambda cf, sp: _run_with_retries(
                    "odds_only_hub",
                    f"{sp}_oddsonly",
                    cf,
                    _extract_odds_only_from_hub,
                    found["schedule_row"]["href"],
                    market_family=MARKET_FAMILY_ODDS,
                    base_dir=str(_default_data_root().parent),
                    breaker_match_id=match_id,
                ),
                client_factory,
                f"{session_prefix}_oddsonly",
            )
        else:
            all_markets = _run_with_verification_reentry(
                lambda cf, sp: _extract_all_markets_with_fallback(match_id, found["schedule_row"]["href"], cf, sp, base_dir=str(_default_data_root().parent), odds_fresh_session_recovery=not args.no_odds_fresh_session),
                client_factory,
                f"{session_prefix}_all",
            )
        if isinstance(all_markets, dict):
            if args.odds_only and _is_verification_required_payload(all_markets):
                # Cold odds-only session was fully walled; record the blocked state
                # on 欧赔/凯利 rather than leaving them null.
                payload["欧赔"] = dict(all_markets)
                payload["凯利"] = dict(all_markets)
            else:
                payload["欧赔"] = all_markets.get("europe")
                payload["凯利"] = all_markets.get("kelly")
                if not args.odds_only:
                    payload["亚值"] = all_markets.get("asian")
                    payload["大小球"] = all_markets.get("totals")
                    payload["阵容"] = all_markets.get("lineup")

        if args.overwrite:
            payload["_note"] = "overwrite=true: same event writes to a stable filename"
        if existing:
            payload["_note_match_id_overwrite"] = f"match_id={match_id}: overwrote existing snapshot file"

        # 熔断/空盘口保护：本次抓取若三大盘口全部 blocked/空（如 ttl_circuit_open 风控熔断），
        # 不要用这份废快照覆盖已有的好快照，避免污染下游 predict 写回。
        if not _snapshot_has_usable_odds(payload):
            prior_ok = False
            if existing and existing.exists():
                try:
                    prior = json.loads(existing.read_text(encoding="utf-8"))
                    prior_ok = _snapshot_has_usable_odds(prior)
                except Exception:
                    prior_ok = False
            if prior_ok:
                print(
                    f"[skip] 本次抓取盘口为空/被熔断，保留已有有效快照不覆盖: {existing}",
                    file=sys.stderr,
                )
                print(str(existing))
                return
            # 没有可保留的旧快照时，仍写出占位快照，但显式标注无可用盘口。
            payload["_note_no_usable_odds"] = "scrape returned no usable odds (blocked/empty); not a valid market snapshot"

        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out_path))
    finally:
        if schedule_bu:
            schedule_bu.close()


if __name__ == "__main__":
    main()
