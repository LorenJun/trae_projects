"""Collect per-match odds for the 2022 World Cup (out-of-sample).

For each MatchID in manifest.json, reuse the project's production market
extractor (_extract_all_markets_with_fallback) to pull 欧赔/亚值/大小球/凯利
from okooo's history.php hub. Results are written one JSON per match under
out_of_sample/world_cup_2022/odds/ and progress is resumable.

Anti-verification strategy (device-pool rotation + breaker cooldown):
  okooo guards a fixed IP with a slider wall scored on behaviour + burst rate.
  The module already rotates a 500-profile / 5-device-pool fingerprint on every
  request and does one cross-pool re-entry. The remaining stall is the 600s
  per-match verification breaker: once a wall trips, further attempts short
  circuit as ttl_circuit_open. So we work in ROUNDS — each round only retries
  matches that still lack usable odds, first CLEARING their breaker so a fresh
  device-pool profile gets a clean attempt, with a cooldown between rounds to
  let the IP behaviour score relax.
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


def _has_usable(payload: Dict[str, Any]) -> bool:
    return oss._snapshot_has_usable_odds(payload)


def _clear_breaker_for(match_ids: List[str]) -> int:
    """Remove any open verification-breaker entries for the given match ids so a
    fresh device-pool retry is not short-circuited by ttl_circuit_open."""
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
        mid = str(key).split("::", 1)[0]
        if mid in wanted:
            data.pop(key, None)
            removed += 1
    if removed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def _pending(matches: List[Dict[str, Any]], force: bool) -> List[Dict[str, Any]]:
    out = []
    for m in matches:
        mid = str(m.get("match_id"))
        p = ODDS_DIR / f"{mid}.json"
        if p.exists() and not force:
            try:
                if _has_usable(json.loads(p.read_text(encoding="utf-8"))):
                    continue
            except Exception:
                pass
        out.append(m)
    return out


def _fetch_one(m: Dict[str, Any], port: int) -> bool:
    mid = str(m.get("match_id"))
    home = m.get("home_team") or ""
    away = m.get("away_team") or ""
    history_url = m.get("history_url") or f"https://m.okooo.com/match/history.php?MatchID={mid}"

    def client_factory(session_name: str) -> Any:
        return oss.LocalChromeSession(port=port, session_name=session_name)

    token = datetime.now().strftime("%H%M%S")
    session_prefix = f"wc22_{token}_{mid}"
    try:
        all_markets = oss._run_with_verification_reentry(
            lambda cf, sp: oss._extract_all_markets_with_fallback(
                mid, history_url, cf, sp, base_dir=BASE_DIR, odds_fresh_session_recovery=True
            ),
            client_factory,
            session_prefix,
        )
    except Exception as exc:
        all_markets = {"_error": str(exc)[:300]}

    payload: Dict[str, Any] = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "driver": "local-chrome",
        "league": "世界杯",
        "season": "2022",
        "purpose": "out_of_sample",
        "match_id": mid,
        "match_date": m.get("date") or "",
        "round": m.get("round") or m.get("stage") or "",
        "home_team": home,
        "away_team": away,
        "score": m.get("score") or "",
        "history_url": history_url,
        "欧赔": (all_markets or {}).get("europe"),
        "亚值": (all_markets or {}).get("asian"),
        "大小球": (all_markets or {}).get("totals"),
        "凯利": (all_markets or {}).get("kelly"),
    }
    usable = _has_usable(payload)
    if not usable:
        payload["_note_no_usable_odds"] = "blocked/empty markets this run"
    out_path = ODDS_DIR / f"{mid}.json"
    # Never overwrite a previously-usable snapshot with an empty/blocked one.
    if not usable and out_path.exists():
        try:
            if _has_usable(json.loads(out_path.read_text(encoding="utf-8"))):
                return True
        except Exception:
            pass
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return usable


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5, help="设备池轮换重试轮数")
    ap.add_argument("--sleep", type=float, default=8.0, help="每场之间的基础间隔秒数")
    ap.add_argument("--round-cooldown", type=float, default=150.0, help="每轮之间的冷却秒数")
    ap.add_argument("--force", action="store_true", help="即使已有可用快照也重抓")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 场待办（0=全部）")
    ap.add_argument("--chrome-port", type=int, default=9222)
    args = ap.parse_args()

    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    matches = manifest.get("matches") or []

    meta = oss._ensure_local_chrome(args.chrome_port, CHROME_PATH, str(oss._default_data_root() / "chrome_profile"))
    port = int(meta.get("port") or args.chrome_port)

    for rnd in range(1, args.rounds + 1):
        pending = _pending(matches, force=args.force and rnd == 1)
        if args.limit:
            pending = pending[: args.limit]
        if not pending:
            print(f"[round {rnd}] nothing pending — all usable")
            break
        cleared = _clear_breaker_for([str(m.get("match_id")) for m in pending])
        print(f"[round {rnd}/{args.rounds}] pending={len(pending)} breaker_cleared={cleared}")
        got = 0
        for i, m in enumerate(pending, start=1):
            mid = str(m.get("match_id"))
            ok = _fetch_one(m, port)
            got += int(ok)
            print(f"  [r{rnd} {i}/{len(pending)}] {mid} {m.get('home_team')}vs{m.get('away_team')} -> {'OK' if ok else 'EMPTY/BLOCKED'}", flush=True)
            if i < len(pending):
                time.sleep(max(0.0, args.sleep))
        remaining = len(_pending(matches, force=False))
        print(f"[round {rnd}] got={got} remaining_empty={remaining}", flush=True)
        if remaining == 0:
            break
        if rnd < args.rounds:
            print(f"[round {rnd}] cooldown {args.round_cooldown}s before next rotation round...", flush=True)
            time.sleep(max(0.0, args.round_cooldown))

    # Final summary
    usable = 0
    empty: List[str] = []
    for m in matches:
        p = ODDS_DIR / f"{m['match_id']}.json"
        if p.exists() and _has_usable(json.loads(p.read_text(encoding="utf-8"))):
            usable += 1
        else:
            empty.append(str(m["match_id"]))
    print(f"DONE total={len(matches)} usable={usable} empty={len(empty)}")
    if empty:
        print("EMPTY_IDS=" + ",".join(empty))


if __name__ == "__main__":
    main()
