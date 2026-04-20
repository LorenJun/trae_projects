#!/usr/bin/env python3
"""
Fill player Chinese names for La Liga / Bundesliga / Serie A / Ligue 1 using Wikidata.

Rationale:
- Understat provides strong stats but no Chinese names or jersey numbers.
- User selected Wikidata as the authoritative source for Chinese labels.

Behavior:
- For each player entry, uses `english_name` to query Wikidata search API with `language=zh`.
- If top result looks like a footballer, writes:
  - `name`: Chinese label
  - `english_name`: unchanged
  - `wikidata_id`: Qxxx
  - `name_cn_source`: "wikidata"
- Conservative: if not confidently a footballer, leaves as-is.

Cache:
- Writes a local cache to avoid re-querying.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = BASE_DIR / ".cache_wikidata_player_zh.json"

TARGET_LEAGUES = ("la_liga", "bundesliga", "serie_a", "ligue_1")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; europe_leagues/1.0)"}


def _looks_like_footballer(desc: str) -> bool:
    desc_l = (desc or "").lower()
    # English + Chinese variants
    keywords = [
        "footballer",
        "soccer player",
        "association football",
        "football player",
        "足球",
        "足球运动员",
        "足球運動員",
    ]
    return any(k in desc_l for k in keywords)


def load_cache() -> Dict[str, Dict[str, Any]]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, Dict[str, Any]]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def query_wikidata_zh_label(english_name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    resp = requests.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "format": "json",
            "language": "zh",
            "uselang": "zh",
            "search": english_name,
            "limit": 5,
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("search", [])
    if not results:
        return None, None, None

    # Prefer exact-ish label match, but keep it conservative.
    best = None
    for r in results:
        label = r.get("label") or ""
        desc = r.get("description") or ""
        # Must look like a footballer.
        # Must look like a footballer.
        if not _looks_like_footballer(desc):
            continue
        best = r
        # If Wikidata returns an English label (fallback), keep searching.
        if any("\u4e00" <= ch <= "\u9fff" for ch in label):
            break

    if not best:
        return None, None, None

    return best.get("label"), best.get("id"), best.get("description")


def query_wikidata_numbers(qids: List[str]) -> Dict[str, List[str]]:
    """
    Returns { qid: ['9','10', ...] } using P1618 (sport number / jersey number).
    """
    if not qids:
        return {}

    resp = requests.get(
        WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(qids),
            "props": "claims",
        },
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json().get("entities", {})
    out: Dict[str, List[str]] = {}
    for qid, entity in data.items():
        claims = entity.get("claims", {}) if isinstance(entity, dict) else {}
        vals: List[str] = []
        for c in claims.get("P1618", []):
            dv = c.get("mainsnak", {}).get("datavalue", {})
            if dv.get("type") == "string" and dv.get("value"):
                vals.append(str(dv.get("value")))
        # keep unique order
        seen = set()
        uniq = []
        for v in vals:
            if v in seen:
                continue
            seen.add(v)
            uniq.append(v)
        out[qid] = uniq
    return out


def process_file(path: Path, cache: Dict[str, Dict[str, Any]], sleep_s: float) -> Tuple[int, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    players = data.get("players", [])
    updated = 0
    looked_up = 0

    for p in players:
        en = (p.get("english_name") or "").strip()
        if not en:
            continue

        # Skip if already has a cn name source set.
        if p.get("name_cn_source") == "wikidata" and p.get("wikidata_id"):
            continue

        key = en
        cached = cache.get(key)
        if cached is None:
            label_zh, qid, desc = query_wikidata_zh_label(en)
            cache[key] = {"label_zh": label_zh or "", "qid": qid or "", "desc": desc or ""}
            looked_up += 1
            if sleep_s:
                time.sleep(sleep_s)
            cached = cache[key]

        label_zh = (cached.get("label_zh") or "").strip()
        qid = (cached.get("qid") or "").strip()
        desc = (cached.get("desc") or "").strip()
        if not label_zh or not qid:
            continue

        # Apply only if it seems like a footballer (double check cached desc).
        if desc and not _looks_like_footballer(desc):
            continue

        # Write CN name while preserving english_name.
        p["name"] = label_zh
        p["wikidata_id"] = qid
        p["name_cn_source"] = "wikidata"
        updated += 1

    data["players"] = players
    data["last_updated"] = data.get("last_updated") or ""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return updated, looked_up


def main() -> int:
    cache = load_cache()
    total_updated = 0
    total_looked_up = 0

    # Be polite to Wikidata. Adjust if needed.
    sleep_s = 0.2

    # Bootstrap cache from already-enriched JSON files so we can also fetch jersey
    # numbers for those QIDs without re-searching.
    for league in TARGET_LEAGUES:
        league_dir = BASE_DIR / league / "players"
        if not league_dir.exists():
            continue
        for path in league_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for p in data.get("players", []):
                en = (p.get("english_name") or "").strip()
                qid = (p.get("wikidata_id") or "").strip()
                if not en or not qid:
                    continue
                if en not in cache:
                    cache[en] = {"label_zh": (p.get("name") or "").strip(), "qid": qid, "desc": ""}

    for league in TARGET_LEAGUES:
        league_dir = BASE_DIR / league / "players"
        if not league_dir.exists():
            continue
        for path in sorted(league_dir.glob("*.json")):
            updated, looked_up = process_file(path, cache, sleep_s)
            total_updated += updated
            total_looked_up += looked_up
            if updated:
                print(f"{league}/{path.stem}: cn_updated={updated}")

            if total_looked_up and total_looked_up % 200 == 0:
                save_cache(cache)

    # Second pass: fill jersey number candidates from Wikidata (P1618).
    # Note: this is best-effort; many players have multiple numbers across teams.
    qids = sorted({(v.get("qid") or "").strip() for v in cache.values() if (v.get("qid") or "").strip()})
    batch_size = 50
    numbers_by_qid: Dict[str, List[str]] = {}
    for i in range(0, len(qids), batch_size):
        batch = qids[i : i + batch_size]
        numbers_by_qid.update(query_wikidata_numbers(batch))
        time.sleep(0.2)

    for league in TARGET_LEAGUES:
        league_dir = BASE_DIR / league / "players"
        if not league_dir.exists():
            continue
        for path in sorted(league_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            changed = False
            for p in data.get("players", []):
                qid = (p.get("wikidata_id") or "").strip()
                if not qid:
                    continue
                if p.get("shirt_number_source") == "wikidata":
                    continue
                nums = numbers_by_qid.get(qid, [])
                if not nums:
                    continue
                p["shirt_numbers"] = nums
                if len(nums) == 1:
                    try:
                        p["shirt_number"] = int(nums[0])
                    except Exception:
                        p["shirt_number"] = nums[0]
                p["shirt_number_source"] = "wikidata"
                changed = True
            if changed:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    save_cache(cache)
    print(f"TOTAL cn_updated={total_updated}, looked_up={total_looked_up}, cache={len(cache)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
