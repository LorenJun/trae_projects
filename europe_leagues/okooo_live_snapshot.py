"""模块说明：封装单场澳客实时快照刷新流程，供预测主链按需调用。"""

import json
import os
import re
import subprocess
from glob import glob
from typing import Any, Dict, Optional, Tuple


LEAGUE_CODE_TO_CN = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "serie_a": "意甲",
    "bundesliga": "德甲",
    "ligue_1": "法甲",
    "europa_league": "欧联",
    "champions_league": "欧冠",
    "conference_league": "欧协联",
}

LEAGUE_SNAPSHOT_DIR_ALIASES = {
    "europa_league": ["europa_league", "欧联", "欧罗巴"],
    "champions_league": ["champions_league", "欧冠"],
    "conference_league": ["conference_league", "欧协联"],
}


def snapshots_root(base_dir: str) -> str:
    # Project-relative runtime dir (gitignored)
    return os.path.join(base_dir, ".okooo-scraper", "snapshots")


def list_snapshot_dirs(base_dir: str, league_code: str) -> list[str]:
    aliases = LEAGUE_SNAPSHOT_DIR_ALIASES.get(league_code, [league_code] if league_code else [""])
    dirs = [os.path.join(snapshots_root(base_dir), alias) for alias in aliases if alias]
    # legacy compat
    dirs.append(os.path.join(base_dir, "okooo_snapshots"))
    dirs.extend(os.path.join(base_dir, "okooo_snapshots", alias) for alias in aliases if alias)
    # de-dup
    out = []
    seen = set()
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_filename(value: str) -> str:
    text = _normalize_text(value).replace(" ", "")
    text = re.sub(r'[<>:"/\\\\|?*]+', "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "snapshot"


def _canonical_snapshot_path(file_path: str, home_team: str, away_team: str) -> str:
    directory = os.path.dirname(file_path)
    filename = f"{_safe_filename(f'{home_team}vs{away_team}')}.json"
    return os.path.join(directory, filename)


def _normalize_snapshot_file(
    file_path: str,
    *,
    home_team: str,
    away_team: str,
) -> str:
    canonical_path = _canonical_snapshot_path(file_path, home_team, away_team)
    if not canonical_path or os.path.normpath(canonical_path) == os.path.normpath(file_path):
        return file_path
    if os.path.exists(canonical_path):
        return canonical_path
    try:
        os.replace(file_path, canonical_path)
        return canonical_path
    except Exception:
        return file_path


def snapshot_matches_request(
    snapshot: Optional[Dict[str, Any]],
    *,
    home_team: str = "",
    away_team: str = "",
    match_date: str = "",
) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if home_team and _normalize_text(snapshot.get("home_team") or snapshot.get("team1")) != _normalize_text(home_team):
        return False
    if away_team and _normalize_text(snapshot.get("away_team") or snapshot.get("team2")) != _normalize_text(away_team):
        return False
    if match_date and _normalize_text(snapshot.get("match_date")) != _normalize_text(match_date):
        return False
    return True


def extract_current_odds(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    europe = snapshot.get("欧赔", {}) or {}
    asian = snapshot.get("亚值", {}) or {}
    kelly = snapshot.get("凯利", {}) or {}
    totals = snapshot.get("大小球", {}) or {}
    # okooo_save_snapshot.py stores totals as:
    #   {"found": true/false, "initial": {...}, "final": {...}, ...}
    # We normalize it to the nested schema used by prediction workflow.
    totals_initial = totals.get("initial", {}) if isinstance(totals, dict) else {}
    totals_final = totals.get("final", {}) if isinstance(totals, dict) else {}
    europe_out = {"initial": europe.get("initial", {}), "final": europe.get("final", {})}
    if isinstance(europe, dict):
        consensus = europe.get("consensus")
        companies = europe.get("companies")
        if isinstance(consensus, dict):
            europe_out["consensus"] = consensus
        if isinstance(companies, list):
            europe_out["companies"] = companies
        company_mode = europe.get("company_mode")
        if company_mode:
            europe_out["company_mode"] = company_mode
    asian_out = {"initial": asian.get("initial", {}), "final": asian.get("final", {})}
    if isinstance(asian, dict):
        consensus = asian.get("consensus")
        companies = asian.get("companies")
        if isinstance(consensus, dict):
            asian_out["consensus"] = consensus
        if isinstance(companies, list):
            asian_out["companies"] = companies
        company_mode = asian.get("company_mode")
        if company_mode:
            asian_out["company_mode"] = company_mode
    totals_out = {"initial": totals_initial, "final": totals_final}
    if isinstance(totals, dict):
        consensus = totals.get("consensus")
        companies = totals.get("companies")
        if isinstance(consensus, dict):
            totals_out["consensus"] = consensus
        if isinstance(companies, list):
            totals_out["companies"] = companies
        company_mode = totals.get("company_mode")
        if company_mode:
            totals_out["company_mode"] = company_mode
    return {
        "match_id": snapshot.get("match_id"),
        "胜平负赔率": dict(europe_out),
        "欧赔": europe_out,
        "亚值": asian_out,
        "大小球": totals_out,
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


def find_snapshot_by_teams(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    match_date: str = "",
) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not home_team or not away_team:
        return None
    for d in list_snapshot_dirs(base_dir, league_code):
        if not os.path.isdir(d):
            continue
        for fp in sorted(glob(os.path.join(d, "*.json"))):
            payload = _read_json(fp)
            if not snapshot_matches_request(
                payload,
                home_team=home_team,
                away_team=away_team,
                match_date=match_date,
            ):
                continue
            return _normalize_snapshot_file(fp, home_team=home_team, away_team=away_team), payload
    return None


def find_snapshot_for_match(
    base_dir: str,
    league_code: str,
    *,
    match_id: str = "",
    home_team: str = "",
    away_team: str = "",
    match_date: str = "",
) -> Optional[Tuple[str, Dict[str, Any]]]:
    wanted_id = _normalize_text(match_id)
    if wanted_id:
        found = find_snapshot_by_match_id(base_dir, league_code, wanted_id)
        if found:
            path, payload = found
            if snapshot_matches_request(
                payload,
                home_team=home_team,
                away_team=away_team,
                match_date=match_date,
            ):
                return _normalize_snapshot_file(path, home_team=home_team, away_team=away_team), payload
    return find_snapshot_by_teams(
        base_dir,
        league_code,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date,
    )


def refresh_snapshot(
    base_dir: str,
    league_code: str,
    home_team: str,
    away_team: str,
    match_date: str,
    driver: str = "local-chrome",
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
        if match_id and not snapshot_matches_request(
            payload,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
        ):
            retry_cmd = cmd[:]
            if "--match-id" in retry_cmd:
                idx = retry_cmd.index("--match-id")
                del retry_cmd[idx:idx + 2]
            cp = subprocess.run(retry_cmd, capture_output=True, text=True, timeout=600)
            if cp.returncode == 0:
                retry_out = (cp.stdout or "").strip().splitlines()
                retry_path = retry_out[-1].strip() if retry_out else ""
                retry_payload = _read_json(retry_path) if retry_path else None
                if isinstance(retry_payload, dict) and snapshot_matches_request(
                    retry_payload,
                    home_team=home_team,
                    away_team=away_team,
                    match_date=match_date,
                ):
                    normalized_retry_path = _normalize_snapshot_file(
                        retry_path,
                        home_team=home_team,
                        away_team=away_team,
                    )
                    return normalized_retry_path, retry_payload
        normalized_path = _normalize_snapshot_file(
            out_path,
            home_team=home_team,
            away_team=away_team,
        ) if snapshot_matches_request(
            payload,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
        ) else out_path
        return normalized_path, payload
    return None
