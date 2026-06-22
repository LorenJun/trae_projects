"""Europe-only gap filler: for matches missing 欧赔, land directly on odds.php via
the hub (the proven 欧指 -> 初赔 -> parse flow) and merge 欧赔 into the saved JSON.

Other markets (亚值/大小球/凯利) are already complete for these matches and are
never touched. Retries with device-pool rotation across attempts.
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
ODDS_DIR = BASE / "odds"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE_DIR = str(oss._default_data_root().parent)


def _found(v: Any) -> bool:
    return isinstance(v, dict) and bool(v.get("found"))


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


def _fetch_europe(mid: str, port: int) -> Dict[str, Any]:
    """Direct hub -> 欧指 -> 初赔 -> parse, matching the proven recon flow."""
    bu = oss.LocalChromeSession(port=port, session_name=f"wc22eu_{datetime.now().strftime('%H%M%S')}_{mid}")
    try:
        hub = f"https://m.okooo.com/match/history.php?MatchID={mid}"
        state = oss._open_ready(bu, hub, settle_seconds=3.0)
        if oss._is_blocked_text(state):
            return {"found": False, "_blocked_at": "hub"}
        oss._click_visible_text(bu, ["欧值", "欧指", "欧赔", "赔率"], settle_seconds=oss.HUB_NAV_SETTLE_SECONDS)
        time.sleep(2.0)
        url = oss._current_url(bu)
        europe = oss._parse_europe_on_current_page(bu)
        if not _found(europe) or oss._score_europe_payload(europe) < (3, 2, 2):
            oss._click_visible_text(bu, ["欧赔", "初赔", "欧值", "欧指"], settle_seconds=oss.CLICK_SETTLE_SECONDS)
            time.sleep(1.5)
            retry = oss._parse_europe_on_current_page(bu)
            europe = oss._pick_preferred_europe_result(europe, retry)
        europe["url"] = url or hub
        europe["_flow"] = "europe_only_direct"
        return europe
    except Exception as exc:
        return {"found": False, "_error": str(exc)[:200]}
    finally:
        bu.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=9.0)
    ap.add_argument("--chrome-port", type=int, default=9222)
    args = ap.parse_args()

    meta = oss._ensure_local_chrome(args.chrome_port, CHROME_PATH, str(oss._default_data_root() / "chrome_profile"))
    port = int(meta.get("port") or args.chrome_port)

    for attempt in range(1, args.attempts + 1):
        targets = []
        for p in sorted(ODDS_DIR.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            if not _found(d.get("欧赔")):
                targets.append((p.stem, p))
        if not targets:
            print(f"[attempt {attempt}] no 欧赔 gaps left")
            break
        _clear_breaker_for([mid for mid, _ in targets])
        print(f"[attempt {attempt}/{args.attempts}] europe gaps={[mid for mid,_ in targets]}", flush=True)
        for mid, p in targets:
            europe = _fetch_europe(mid, port)
            ok = _found(europe)
            if ok:
                payload = json.loads(p.read_text(encoding="utf-8"))
                payload["欧赔"] = europe
                payload["captured_at"] = datetime.now().isoformat(timespec="seconds")
                p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  {mid} europe -> {'OK score=%s' % str(oss._score_europe_payload(europe)) if ok else 'EMPTY'}", flush=True)
            time.sleep(max(0.0, args.sleep))

    remaining = [p.stem for p in sorted(ODDS_DIR.glob("*.json")) if not _found(json.loads(p.read_text(encoding="utf-8")).get("欧赔"))]
    print(f"DONE europe remaining_gaps={remaining}")


if __name__ == "__main__":
    main()
