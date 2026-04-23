import json
import os
import subprocess
from glob import glob
from typing import Any, Dict, Optional, Tuple


LEAGUE_CODE_TO_CN = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "serie_a": "意甲",
    "bundesliga": "德甲",
    "ligue_1": "法甲",
}


def snapshots_root(base_dir: str) -> str:
    # Project-relative runtime dir (gitignored)
    return os.path.join(base_dir, ".okooo-scraper", "snapshots")


def list_snapshot_dirs(base_dir: str, league_code: str) -> list[str]:
    # new layout
    dirs = [os.path.join(snapshots_root(base_dir), league_code)]
    # legacy compat
    dirs.append(os.path.join(base_dir, "okooo_snapshots"))
    dirs.append(os.path.join(base_dir, "okooo_snapshots", league_code))
    # de-dup
    out = []
    seen = set()
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def extract_current_odds(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    europe = snapshot.get("欧赔", {}) or {}
    asian = snapshot.get("亚值", {}) or {}
    kelly = snapshot.get("凯利", {}) or {}
    return {
        "match_id": snapshot.get("match_id"),
        "胜平负赔率": {"initial": europe.get("initial", {}), "final": europe.get("final", {})},
        "欧赔": {"initial": europe.get("initial", {}), "final": europe.get("final", {})},
        "亚值": {"initial": asian.get("initial", {}), "final": asian.get("final", {})},
        "凯利": {"initial": kelly.get("initial", {}), "final": kelly.get("final", {})},
        "离散率": snapshot.get("离散率", {}) or {},
    }


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def find_snapshot_by_match_id(base_dir: str, league_code: str, match_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not match_id:
        return None
    for d in list_snapshot_dirs(base_dir, league_code):
        if not os.path.isdir(d):
            continue
        for fp in sorted(glob(os.path.join(d, "*.json"))):
            payload = _read_json(fp)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("match_id") or "") == str(match_id):
                return fp, payload
    return None


def refresh_snapshot(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    match_date: str,
    driver: str = "browser-use",
    match_id: str = "",
    headed: bool = False,
    match_time: str = "",
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Run okooo_save_snapshot.py to refresh odds and return (path, payload)."""
    league_cn = LEAGUE_CODE_TO_CN.get(league_code, league_code)
    script_path = os.path.join(base_dir, "okooo_save_snapshot.py")
    cmd = [
        "python3",
        script_path,
        "--driver",
        driver,
        "--league",
        league_cn,
        "--team1",
        home_team,
        "--team2",
        away_team,
        "--date",
        match_date,
        "--overwrite",
    ]
    if match_time:
        cmd.extend(["--time", match_time])
    if headed and driver == "browser-use":
        cmd.append("--headed")
    if match_id:
        cmd.extend(["--match-id", str(match_id)])
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception as e:
        raise RuntimeError(f"refresh_snapshot failed to run: {e}")
    if cp.returncode != 0:
        tail = "\n".join([x for x in (cp.stdout or "").splitlines()[-10:] if x] + [x for x in (cp.stderr or "").splitlines()[-10:] if x])
        raise RuntimeError(f"refresh_snapshot failed: rc={cp.returncode}\n{tail}".strip())
    out = (cp.stdout or "").strip().splitlines()
    out_path = out[-1].strip() if out else ""
    payload = _read_json(out_path) if out_path else None
    if isinstance(payload, dict):
        return out_path, payload
    return None
