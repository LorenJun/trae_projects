"""模块说明：负责预测后赛果自动同步的登记、到期检查与后台轮询执行。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from runtime.paths import get_default_paths

logger = logging.getLogger(__name__)

MATCH_DURATION_HOURS = 2
RESULT_SYNC_DELAY_HOURS = 2
DEFAULT_FALLBACK_KICKOFF = "23:59"

LEAGUE_NAME_MAP = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "serie_a": "意甲",
    "bundesliga": "德甲",
    "ligue_1": "法甲",
}


def result_sync_registry_path(base_dir: Optional[str] = None):
    return get_default_paths(base_dir).runtime_file("result_sync_registry.json")


def _load_registry(base_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    path = result_sync_registry_path(base_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_registry(base_dir: Optional[str], registry: Dict[str, Dict[str, Any]]) -> None:
    result_sync_registry_path(base_dir).write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_kickoff(match_date: str, match_time: str) -> Tuple[Optional[datetime], str]:
    normalized_date = str(match_date or "").strip()
    normalized_time = str(match_time or "").strip()
    if not normalized_date:
        return None, "missing_match_date"
    kickoff_time = normalized_time if normalized_time and ":" in normalized_time else DEFAULT_FALLBACK_KICKOFF
    source = "scheduled_time" if normalized_time and ":" in normalized_time else "fallback_end_of_day"
    try:
        kickoff = datetime.strptime(f"{normalized_date} {kickoff_time}", "%Y-%m-%d %H:%M")
        return kickoff, source
    except Exception:
        return None, "invalid_match_datetime"


def _build_match_id(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("match_id") or "").strip()
    if explicit:
        return explicit
    league_code = str(payload.get("league_code") or payload.get("league") or "").strip()
    match_date = str(payload.get("match_date") or "").strip().replace("-", "")
    home_team = str(payload.get("home_team") or "").strip()
    away_team = str(payload.get("away_team") or "").strip()
    return f"{league_code}_{match_date}_{home_team}_{away_team}".strip("_")


def register_prediction_result_sync(base_dir: Optional[str], prediction: Dict[str, Any]) -> Dict[str, Any]:
    match_id = _build_match_id(prediction)
    if not match_id:
        return {}

    league_code = str(prediction.get("league_code") or prediction.get("league") or "").strip()
    match_date = str(prediction.get("match_date") or "").strip()
    match_time = str(prediction.get("match_time") or "").strip()
    home_team = str(prediction.get("home_team") or "").strip()
    away_team = str(prediction.get("away_team") or "").strip()
    kickoff_dt, kickoff_source = _parse_kickoff(match_date, match_time)
    due_at = None
    if kickoff_dt:
        due_at = kickoff_dt + timedelta(hours=MATCH_DURATION_HOURS + RESULT_SYNC_DELAY_HOURS)

    registry = _load_registry(base_dir)
    prior = registry.get(match_id, {}) if isinstance(registry.get(match_id), dict) else {}
    entry = {
        "match_id": match_id,
        "league_code": league_code,
        "league_name": str(prediction.get("league_name") or LEAGUE_NAME_MAP.get(league_code) or league_code),
        "match_date": match_date,
        "match_time": match_time,
        "home_team": home_team,
        "away_team": away_team,
        "prediction": prediction.get("prediction"),
        "confidence": prediction.get("confidence"),
        "registered_at": datetime.now().isoformat(),
        "kickoff_source": kickoff_source,
        "kickoff_at": kickoff_dt.isoformat() if kickoff_dt else "",
        "due_at": due_at.isoformat() if due_at else "",
        "status": "pending",
        "last_prediction_timestamp": str(prediction.get("timestamp") or ""),
        "check_count": int(prior.get("check_count") or 0),
        "last_checked_at": str(prior.get("last_checked_at") or ""),
        "result_synced_at": str(prior.get("result_synced_at") or ""),
        "last_error": str(prior.get("last_error") or ""),
    }
    if prior.get("status") == "completed" and prior.get("result_synced_at"):
        entry["status"] = "completed"
    registry[match_id] = entry
    _save_registry(base_dir, registry)
    return entry


def mark_result_sync_completed(
    base_dir: Optional[str],
    match_id: str,
    actual_score: str,
    actual_winner: str,
) -> None:
    if not match_id:
        return
    registry = _load_registry(base_dir)
    entry = registry.get(match_id)
    if not isinstance(entry, dict):
        return
    entry["status"] = "completed"
    entry["result_synced_at"] = datetime.now().isoformat()
    entry["actual_score"] = actual_score
    entry["actual_winner"] = actual_winner
    entry["last_error"] = ""
    registry[match_id] = entry
    _save_registry(base_dir, registry)


def _fetch_finished_matches(league_code: str, match_date: str) -> List[Dict[str, Any]]:
    league_name = LEAGUE_NAME_MAP.get(league_code)
    if not league_name:
        return []
    try:
        from bulk_fetch_and_update import fetch_day

        payload = fetch_day(league_name, league_code, match_date)
    except Exception as exc:
        logger.warning("自动同步赛果抓取失败 %s %s: %s", league_code, match_date, exc)
        return []
    if not isinstance(payload, dict):
        return []
    matches = payload.get("matches", [])
    finished: List[Dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "已结束":
            continue
        if item.get("home_score") is None or item.get("away_score") is None:
            continue
        finished.append(item)
    return finished


def _match_finished_item(entry: Dict[str, Any], finished: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    wanted_id = str(entry.get("match_id") or "").strip()
    wanted_home = str(entry.get("home_team") or "").strip()
    wanted_away = str(entry.get("away_team") or "").strip()
    for item in finished:
        if wanted_id and str(item.get("match_id") or "").strip() == wanted_id:
            return item
    for item in finished:
        if str(item.get("home_team") or "").strip() == wanted_home and str(item.get("away_team") or "").strip() == wanted_away:
            return item
    return None


def sync_due_prediction_results(
    base_dir: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    registry = _load_registry(base_dir)
    current_time = now or datetime.now()
    due_entries: List[Tuple[str, Dict[str, Any]]] = []
    for match_id, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "completed":
            continue
        due_at = str(entry.get("due_at") or "").strip()
        if not due_at:
            continue
        try:
            if datetime.fromisoformat(due_at) <= current_time:
                due_entries.append((match_id, entry))
        except Exception:
            continue
    due_entries.sort(key=lambda item: str(item[1].get("due_at") or ""))
    due_entries = due_entries[: max(0, int(limit))]

    if not due_entries:
        return {
            "checked_at": current_time.isoformat(),
            "due_count": 0,
            "updated_count": 0,
            "pending_count": 0,
            "updates": [],
        }

    from result_manager import ResultManager

    manager = ResultManager(base_dir)
    existing_results = {item["match_id"] for item in manager.load_results() if isinstance(item, dict) and item.get("match_id")}
    fetch_cache: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    updates: List[Dict[str, Any]] = []

    for match_id, entry in due_entries:
        entry["check_count"] = int(entry.get("check_count") or 0) + 1
        entry["last_checked_at"] = current_time.isoformat()
        if match_id in existing_results:
            entry["status"] = "completed"
            entry["result_synced_at"] = current_time.isoformat()
            entry["last_error"] = ""
            updates.append({"match_id": match_id, "updated": False, "reason": "already_completed"})
            registry[match_id] = entry
            continue

        key = (str(entry.get("league_code") or ""), str(entry.get("match_date") or ""))
        if key not in fetch_cache:
            fetch_cache[key] = _fetch_finished_matches(*key)
        matched = _match_finished_item(entry, fetch_cache[key])
        if not matched:
            entry["status"] = "pending"
            entry["last_error"] = "result_not_available_yet"
            updates.append({"match_id": match_id, "updated": False, "reason": "not_finished_or_not_found"})
            registry[match_id] = entry
            continue

        home_score = int(matched["home_score"])
        away_score = int(matched["away_score"])
        try:
            result = manager.save_result(
                f"{entry.get('home_team')} vs {entry.get('away_team')}",
                home_score,
                away_score,
                league=entry.get("league_code"),
                date_override=entry.get("match_date"),
            )
            entry["status"] = "completed"
            entry["result_synced_at"] = current_time.isoformat()
            entry["actual_score"] = result.get("actual_score")
            entry["actual_winner"] = result.get("actual_winner")
            entry["last_error"] = ""
            updates.append(
                {
                    "match_id": match_id,
                    "updated": True,
                    "actual_score": result.get("actual_score"),
                    "home_team": entry.get("home_team"),
                    "away_team": entry.get("away_team"),
                }
            )
        except Exception as exc:
            entry["status"] = "pending"
            entry["last_error"] = str(exc)
            updates.append({"match_id": match_id, "updated": False, "reason": str(exc)})
        registry[match_id] = entry

    if any(item.get("updated") for item in updates):
        try:
            manager.update_accuracy_stats()
        except Exception as exc:
            logger.warning("自动同步赛果后更新准确率失败: %s", exc)

    _save_registry(base_dir, registry)
    updated_count = sum(1 for item in updates if item.get("updated"))
    return {
        "checked_at": current_time.isoformat(),
        "due_count": len(due_entries),
        "updated_count": updated_count,
        "pending_count": len(due_entries) - updated_count,
        "updates": updates,
    }


def run_result_sync_daemon(
    base_dir: Optional[str] = None,
    *,
    interval_minutes: int = 10,
    max_cycles: int = 0,
) -> Dict[str, Any]:
    cycles = 0
    last_report: Dict[str, Any] = {}
    while True:
        last_report = sync_due_prediction_results(base_dir, limit=50)
        cycles += 1
        if max_cycles and cycles >= max_cycles:
            break
        time.sleep(max(60, int(interval_minutes) * 60))
    last_report["cycles"] = cycles
    return last_report
