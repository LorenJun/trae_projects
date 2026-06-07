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
    "friendly": "友谊赛",
    "world_cup": "世界杯",
    "europa_league": "欧联",
    "champions_league": "欧冠",
    "conference_league": "欧协联",
}

LEAGUE_SNAPSHOT_DIR_ALIASES = {
    "friendly": ["friendly", "world_cup", "友谊赛"],
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


def _parse_compact_triplet(text: Any) -> Optional[Dict[str, float]]:
    raw = _normalize_text(text)
    if not raw:
        return None
    nums = [float(item) for item in re.findall(r"\d{1,2}\.\d{2}", raw)]
    if len(nums) < 3:
        return None
    return {"home": nums[0], "draw": nums[1], "away": nums[2]}


def _parse_handicap_text(text: Any) -> Optional[float]:
    raw = _normalize_text(text).replace(" ", "")
    if not raw:
        return None
    try:
        if "/" in raw and not any(ch in raw for ch in "球平受让"):
            parts = [float(item) for item in raw.split("/") if item]
            if parts:
                return sum(parts) / len(parts)
        return float(raw)
    except Exception:
        pass
    mapping = {
        "平手": 0.0,
        "平手/半球": -0.25,
        "平/半": -0.25,
        "半球": -0.5,
        "半球/一球": -0.75,
        "半/一": -0.75,
        "一球": -1.0,
        "一球/球半": -1.25,
        "一/球半": -1.25,
        "球半": -1.5,
        "球半/两球": -1.75,
        "两球": -2.0,
        "两球/两球半": -2.25,
        "两球半": -2.5,
        "受让平手": 0.0,
        "受让平手/半球": 0.25,
        "受让平/半": 0.25,
        "受让半球": 0.5,
        "受让半球/一球": 0.75,
        "受让半/一": 0.75,
        "受让一球": 1.0,
        "受让一球/球半": 1.25,
        "受让一/球半": 1.25,
        "受让球半": 1.5,
        "受让球半/两球": 1.75,
        "受让两球": 2.0,
        "受让两球/两球半": 2.25,
        "受让两球半": 2.5,
    }
    return mapping.get(raw)


def _recover_europe_from_state_excerpt(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    excerpt = _normalize_text(block.get("_state_excerpt"))
    if not excerpt:
        return None
    row_match = re.search(r"99家平均\s*((?:\d{1,2}\.\d{2}){3})\s*((?:\d{1,2}\.\d{2}){3})", excerpt)
    if not row_match:
        return None
    initial = _parse_compact_triplet(row_match.group(1))
    final = _parse_compact_triplet(row_match.group(2))
    if not initial or not final:
        return None
    return {
        "initial": initial,
        "final": final,
        "company_mode": "average_row_fallback",
        "consensus": {
            "mode": "average_row_fallback",
            "company_count": 0,
            "filtered_company_count": 0,
            "companies": [],
            "all_companies": [],
        },
        "companies": [],
    }


def _recover_asian_from_state_excerpt(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    excerpt = _normalize_text(block.get("_state_excerpt"))
    if not excerpt:
        return None
    row_match = re.search(
        r"平均指数\s*(\d+\.\d+)\s*([^\d\s]+)\s*(\d+\.\d+)\s*(\d+\.\d+)\s*([^\d\s]+)\s*(\d+\.\d+)",
        excerpt,
    )
    if not row_match:
        return None
    initial_text = row_match.group(2).strip()
    final_text = row_match.group(5).strip()
    initial_value = _parse_handicap_text(initial_text)
    final_value = _parse_handicap_text(final_text)
    initial = {
        "home_water": float(row_match.group(1)),
        "handicap_text": initial_text,
        "handicap_value": initial_value,
        "handicap": initial_value,
        "away_water": float(row_match.group(3)),
    }
    final = {
        "home_water": float(row_match.group(4)),
        "handicap_text": final_text,
        "handicap_value": final_value,
        "handicap": final_value,
        "away_water": float(row_match.group(6)),
    }
    return {
        "initial": initial,
        "final": final,
        "company_mode": "average_row_fallback",
        "consensus": {
            "mode": "average_row_fallback",
            "company_count": 0,
            "filtered_company_count": 0,
            "companies": [],
            "all_companies": [],
            "final_handicap": final_value,
            "final_handicap_text": final_text,
            "initial_handicap": initial_value,
            "initial_handicap_text": initial_text,
        },
        "companies": [],
    }


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


def _market_fetch_status(block: Any, *, market_key: str, normalized_block: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = block if isinstance(block, dict) else {}
    normalized = normalized_block if isinstance(normalized_block, dict) else {}
    if not payload and not normalized:
        return {"market": market_key, "status": "missing", "source": "missing_block"}
    if normalized.get("initial") or normalized.get("final"):
        if payload.get("_state_excerpt") or payload.get("blocked") or payload.get("verification_required"):
            return {
                "market": market_key,
                "status": "available",
                "source": "state_excerpt_recovery",
            }
        return {
            "market": market_key,
            "status": "available",
            "source": normalized.get("company_mode") or normalized.get("_source") or "snapshot_reuse",
        }
    if payload.get("breaker_open"):
        return {
            "market": market_key,
            "status": "verification_required",
            "source": "ttl_circuit_open",
            "market_family": payload.get("market_family"),
            "breaker_expires_at": payload.get("breaker_expires_at"),
        }
    if payload.get("verification_required") or payload.get("blocked"):
        return {
            "market": market_key,
            "status": "verification_required",
            "source": "live_blocked",
            "market_family": payload.get("market_family"),
        }
    if payload.get("found"):
        return {
            "market": market_key,
            "status": "available",
            "source": payload.get("_flow") or payload.get("_source") or "live_fetch",
        }
    if payload.get("_state_excerpt"):
        return {
            "market": market_key,
            "status": "degraded",
            "source": "state_excerpt_only",
        }
    return {
        "market": market_key,
        "status": "unavailable",
        "source": payload.get("_flow") or payload.get("_source") or "live_fetch_not_found",
    }


def _build_market_fetch_status(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    payload = snapshot if isinstance(snapshot, dict) else {}
    statuses = {
        "欧赔": _market_fetch_status(payload.get("欧赔"), market_key="欧赔"),
        "亚值": _market_fetch_status(payload.get("亚值"), market_key="亚值"),
        "大小球": _market_fetch_status(payload.get("大小球"), market_key="大小球"),
        "凯利": _market_fetch_status(payload.get("凯利"), market_key="凯利"),
    }
    available = [item for item in statuses.values() if item.get("status") == "available"]
    degraded = [item for item in statuses.values() if item.get("status") == "degraded"]
    verification = [item for item in statuses.values() if item.get("status") == "verification_required"]
    unavailable = [item for item in statuses.values() if item.get("status") == "unavailable"]
    return {
        "by_market": statuses,
        "market_coverage": {
            "available_count": len(available),
            "degraded_count": len(degraded),
            "verification_required_count": len(verification),
            "unavailable_count": len(unavailable),
            "coverage_label": "full" if len(available) == 4 else "partial" if available else "none",
        },
    }


def extract_current_odds(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    europe = snapshot.get("欧赔", {}) or {}
    asian = snapshot.get("亚值", {}) or {}
    kelly = snapshot.get("凯利", {}) or {}
    totals = snapshot.get("大小球", {}) or {}
    if isinstance(europe, dict) and not any(isinstance(europe.get(key), dict) and europe.get(key) for key in ("initial", "final")):
        recovered = _recover_europe_from_state_excerpt(europe)
        if recovered:
            europe = {**europe, **recovered}
    if isinstance(asian, dict) and not any(isinstance(asian.get(key), dict) and asian.get(key) for key in ("initial", "final")):
        recovered = _recover_asian_from_state_excerpt(asian)
        if recovered:
            asian = {**asian, **recovered}
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
    market_status = {
        "by_market": {
            "欧赔": _market_fetch_status(snapshot.get("欧赔"), market_key="欧赔", normalized_block=europe_out),
            "亚值": _market_fetch_status(snapshot.get("亚值"), market_key="亚值", normalized_block=asian_out),
            "大小球": _market_fetch_status(snapshot.get("大小球"), market_key="大小球", normalized_block=totals_out),
            "凯利": _market_fetch_status(snapshot.get("凯利"), market_key="凯利", normalized_block={"initial": kelly.get("initial", {}), "final": kelly.get("final", {})}),
        }
    }
    available = [item for item in market_status["by_market"].values() if item.get("status") == "available"]
    degraded = [item for item in market_status["by_market"].values() if item.get("status") == "degraded"]
    verification = [item for item in market_status["by_market"].values() if item.get("status") == "verification_required"]
    unavailable = [item for item in market_status["by_market"].values() if item.get("status") == "unavailable"]
    market_status["market_coverage"] = {
        "available_count": len(available),
        "degraded_count": len(degraded),
        "verification_required_count": len(verification),
        "unavailable_count": len(unavailable),
        "coverage_label": "full" if len(available) == 4 else "partial" if available else "none",
    }
    return {
        "match_id": snapshot.get("match_id"),
        "胜平负赔率": dict(europe_out),
        "欧赔": europe_out,
        "亚值": asian_out,
        "大小球": totals_out,
        "凯利": {"initial": kelly.get("initial", {}), "final": kelly.get("final", {})},
        "离散率": snapshot.get("离散率", {}) or {},
        "market_fetch_status": market_status["by_market"],
        "market_coverage": market_status["market_coverage"],
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
    strict_identity: bool = False,
    league_name_override: str = "",
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Run okooo_save_snapshot.py to refresh odds and return (path, payload)."""
    league_cn = str(league_name_override or "").strip() or LEAGUE_CODE_TO_CN.get(league_code, league_code)
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
    if strict_identity:
        cmd.append("--strict-identity")
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
            if strict_identity:
                return out_path, payload
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
