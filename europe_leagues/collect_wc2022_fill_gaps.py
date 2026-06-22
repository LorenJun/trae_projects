"""Fill per-market gaps for the 2022 World Cup odds snapshots.

Some matches are "usable" (>=1 market parsed) but still miss an individual
market (欧赔/亚值/大小球/凯利) due to a transient tab-nav failure. This script
re-fetches ONLY matches that have a missing market, then MERGES per-market:
keep any existing found=true market, and only overwrite a market when the new
fetch parsed it (found=true). This never drops already-good data.

Device-pool rotation + breaker clearing are reused so the re-fetch is not
short-circuited by the verification breaker.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import okooo_save_snapshot as oss
from runtime.okooo_access import _BREAKER_FILE_NAME, _runtime_file

BASE = oss._default_data_root() / "out_of_sample" / "world_cup_2022"
MANIFEST = BASE / "manifest.json"
ODDS_DIR = BASE / "odds"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE_DIR = str(oss._default_data_root().parent)

MARKET_KEYS = {"欧赔": "europe", "亚值": "asian", "大小球": "totals", "凯利": "kelly"}


def _found(v: Any) -> bool:
    return isinstance(v, dict) and bool(v.get("found"))


def _missing_markets(payload: Dict[str, Any]) -> List[str]:
    return [k for k in MARKET_KEYS if not _found(payload.get(k))]


def _clear_breaker_for(match_ids: List[str]) -> int:
    path = _runtime_file(BASE_DIR, _BREAKER_FILE_NAME)
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    wanted = set(str(m) for m in match_ids)
    removed = 0
    for key in list(data.keys()):
        if str(key).split("::", 1)[0] in wanted:
            data.pop(key, None)
            removed += 1
    if removed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def _fetch_markets(mid: str, history_url: str, port: int) -> Dict[str, Any]:
    def client_factory(session_name: str) -> Any:
        return oss.LocalChromeSession(port=port, session_name=session_name)

    token = datetime.now().strftime("%H%M%S")
    session_prefix = f"wc22fill_{token}_{mid}"
    try:
        return oss._run_with_verification_reentry(
            lambda cf, sp: oss._extract_all_markets_with_fallback(
                mid, history_url, cf, sp, base_dir=BASE_DIR, odds_fresh_session_recovery=True
            ),
            client_factory,
            session_prefix,
        ) or {}
    except Exception as exc:
        return {"_error": str(exc)[:300]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=10.0)
    ap.add_argument("--round-cooldown", type=float, default=150.0)
    ap.add_argument("--chrome-port", type=int, default=9222)
    args = ap.parse_args()

    matches = {str(m["match_id"]): m for m in json.loads(MANIFEST.read_text(encoding="utf-8"))["matches"]}
    meta = oss._ensure_local_chrome(args.chrome_port, CHROME_PATH, str(oss._default_data_root() / "chrome_profile"))
    port = int(meta.get("port") or args.chrome_port)

    for rnd in range(1, args.rounds + 1):
        # Recompute which files still have a missing market.
        targets: List[str] = []
        for mid in matches:
            p = ODDS_DIR / f"{mid}.json"
            if not p.exists():
                targets.append(mid)
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                targets.append(mid)
                continue
            if _missing_markets(payload):
                targets.append(mid)
        if not targets:
            print(f"[round {rnd}] no gaps — all markets present")
            break
        cleared = _clear_breaker_for(targets)
        print(f"[round {rnd}/{args.rounds}] gap_targets={len(targets)} breaker_cleared={cleared} ids={targets}", flush=True)

        for i, mid in enumerate(targets, start=1):
            p = ODDS_DIR / f"{mid}.json"
            payload = json.loads(p.read_text(encoding="utf-8"))
            before = _missing_markets(payload)
            m = matches[mid]
            history_url = payload.get("history_url") or m.get("history_url") or f"https://m.okooo.com/match/history.php?MatchID={mid}"
            fetched = _fetch_markets(mid, history_url, port)
            filled = []
            for cn, en in MARKET_KEYS.items():
                if not _found(payload.get(cn)) and _found(fetched.get(en)):
                    payload[cn] = fetched.get(en)
                    filled.append(cn)
            if filled:
                payload["captured_at"] = datetime.now().isoformat(timespec="seconds")
                payload.pop("_note_no_usable_odds", None)
                p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            after = _missing_markets(payload)
            print(f"  [r{rnd} {i}/{len(targets)}] {mid} before_missing={before} filled={filled} after_missing={after}", flush=True)
            if i < len(targets):
                time.sleep(max(0.0, args.sleep))

        # If anything still missing, cool down and rotate again.
        still = [mid for mid in matches if (ODDS_DIR / f"{mid}.json").exists() and _missing_markets(json.loads((ODDS_DIR / f"{mid}.json").read_text(encoding="utf-8")))]
        print(f"[round {rnd}] still_missing={len(still)} ids={still}", flush=True)
        if not still:
            break
        if rnd < args.rounds:
            print(f"[round {rnd}] cooldown {args.round_cooldown}s...", flush=True)
            time.sleep(max(0.0, args.round_cooldown))

    # Final per-market report
    cnt = {k: 0 for k in MARKET_KEYS}
    gaps = []
    for mid in matches:
        payload = json.loads((ODDS_DIR / f"{mid}.json").read_text(encoding="utf-8"))
        miss = _missing_markets(payload)
        for k in MARKET_KEYS:
            if _found(payload.get(k)):
                cnt[k] += 1
        if miss:
            gaps.append((mid, miss))
    print(f"DONE per-market found/64: {cnt}")
    if gaps:
        print("REMAINING_GAPS=" + json.dumps(gaps, ensure_ascii=False))


if __name__ == "__main__":
    main()
