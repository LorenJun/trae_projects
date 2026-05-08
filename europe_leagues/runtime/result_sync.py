"""模块说明：负责预测后赛果自动同步的登记、到期检查与后台轮询执行。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from runtime.paths import get_default_paths

logger = logging.getLogger(__name__)

MATCH_DURATION_HOURS = 2
RESULT_SYNC_DELAY_HOURS = 2
DEFAULT_FALLBACK_KICKOFF = "23:59"
FALLBACK_MATCH_ID_PREFIXES = ("premier_league_", "la_liga_", "serie_a_", "bundesliga_", "ligue_1_")

LEAGUE_NAME_MAP = {
    "premier_league": "英超",
    "la_liga": "西甲",
    "serie_a": "意甲",
    "bundesliga": "德甲",
    "ligue_1": "法甲",
    "europa_league": "欧联",
    "champions_league": "欧冠",
    "conference_league": "欧协联",
}

LEAGUE_SOT_CODES = ("premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1")


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


def _get_prediction_field(payload: Dict[str, Any], *field_names: str) -> Any:
    for field_name in field_names:
        value = payload.get(field_name)
        if value not in (None, ""):
            return value
    full_prediction = payload.get("full_prediction")
    if isinstance(full_prediction, dict):
        for field_name in field_names:
            value = full_prediction.get(field_name)
            if value not in (None, ""):
                return value
    return ""


def _build_match_id(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("match_id") or "").strip()
    if explicit and not explicit.startswith(FALLBACK_MATCH_ID_PREFIXES):
        return explicit
    external_match_id = _resolve_external_match_id(payload)
    if external_match_id:
        return external_match_id
    teams_match_id = _build_teams_match_id(payload)
    if teams_match_id:
        return teams_match_id
    if explicit:
        return explicit
    return ""


def _build_teams_match_id(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("teams_match_id") or payload.get("internal_match_id") or "").strip()
    if explicit:
        return explicit
    league_code = str(_get_prediction_field(payload, "league_code", "league") or "").strip()
    if league_code not in LEAGUE_SOT_CODES:
        return ""
    match_date = str(_get_prediction_field(payload, "match_date") or "").strip().replace("-", "")
    home_team = str(_get_prediction_field(payload, "home_team") or "").strip()
    away_team = str(_get_prediction_field(payload, "away_team") or "").strip()
    return f"{league_code}_{match_date}_{home_team}_{away_team}".strip("_")


def _resolve_external_match_id(payload: Dict[str, Any]) -> str:
    explicit = str(payload.get("match_id") or "").strip()
    if explicit and not explicit.startswith(FALLBACK_MATCH_ID_PREFIXES):
        return explicit
    candidate = str(payload.get("external_match_id") or "").strip()
    if candidate and not candidate.startswith(FALLBACK_MATCH_ID_PREFIXES):
        return candidate
    realtime = payload.get("realtime")
    if isinstance(realtime, dict):
        okooo = realtime.get("okooo")
        if isinstance(okooo, dict):
            candidate = str(okooo.get("match_id") or "").strip()
            if candidate and not candidate.startswith(FALLBACK_MATCH_ID_PREFIXES):
                return candidate
    full_prediction = payload.get("full_prediction")
    if isinstance(full_prediction, dict):
        candidate = str(full_prediction.get("match_id") or "").strip()
        if candidate and not candidate.startswith(FALLBACK_MATCH_ID_PREFIXES):
            return candidate
    return ""


def _merge_registry_entry(primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
    def _iso_dt(value: Any) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except Exception:
            return None

    merged = dict(secondary)
    merged.update(primary)
    merged["check_count"] = max(int(primary.get("check_count") or 0), int(secondary.get("check_count") or 0))
    if not merged.get("last_checked_at"):
        merged["last_checked_at"] = str(secondary.get("last_checked_at") or "")
    if not merged.get("result_synced_at"):
        merged["result_synced_at"] = str(secondary.get("result_synced_at") or "")
    if not merged.get("last_error"):
        merged["last_error"] = str(secondary.get("last_error") or "")
    if secondary.get("status") == "completed" and secondary.get("result_synced_at"):
        merged["status"] = "completed"
    primary_due = _iso_dt(primary.get("due_at"))
    secondary_due = _iso_dt(secondary.get("due_at"))
    schedule_source = primary
    if secondary_due and (not primary_due or secondary_due < primary_due):
        schedule_source = secondary
    for field in (
        "league_code",
        "league_name",
        "match_date",
        "match_time",
        "home_team",
        "away_team",
        "kickoff_source",
        "kickoff_at",
        "due_at",
        "teams_match_id",
    ):
        if schedule_source.get(field):
            merged[field] = schedule_source.get(field)
    return merged


def register_prediction_result_sync(base_dir: Optional[str], prediction: Dict[str, Any]) -> Dict[str, Any]:
    match_id = _build_match_id(prediction)
    if not match_id:
        return {}

    league_code = str(_get_prediction_field(prediction, "league_code", "league") or "").strip()
    match_date = str(_get_prediction_field(prediction, "match_date") or "").strip()
    match_time = str(_get_prediction_field(prediction, "match_time") or "").strip()
    home_team = str(_get_prediction_field(prediction, "home_team") or "").strip()
    away_team = str(_get_prediction_field(prediction, "away_team") or "").strip()
    external_match_id = _resolve_external_match_id(prediction)
    teams_match_id = _build_teams_match_id(prediction)
    kickoff_dt, kickoff_source = _parse_kickoff(match_date, match_time)
    due_at = None
    if kickoff_dt:
        due_at = kickoff_dt + timedelta(hours=MATCH_DURATION_HOURS + RESULT_SYNC_DELAY_HOURS)

    registry = _load_registry(base_dir)
    prior = registry.get(match_id, {}) if isinstance(registry.get(match_id), dict) else {}
    if teams_match_id and teams_match_id != match_id and isinstance(registry.get(teams_match_id), dict):
        prior = _merge_registry_entry(prior, registry[teams_match_id])
    entry = {
        "match_id": match_id,
        "external_match_id": external_match_id,
        "teams_match_id": teams_match_id,
        "league_code": league_code,
        "league_name": str(_get_prediction_field(prediction, "league_name") or LEAGUE_NAME_MAP.get(league_code) or league_code),
        "match_date": match_date,
        "match_time": match_time,
        "home_team": home_team,
        "away_team": away_team,
        "prediction": _get_prediction_field(prediction, "prediction"),
        "confidence": _get_prediction_field(prediction, "confidence"),
        "registered_at": datetime.now().isoformat(),
        "kickoff_source": kickoff_source,
        "kickoff_at": kickoff_dt.isoformat() if kickoff_dt else "",
        "due_at": due_at.isoformat() if due_at else "",
        "status": "pending",
        "last_prediction_timestamp": str(_get_prediction_field(prediction, "timestamp") or ""),
        "check_count": int(prior.get("check_count") or 0),
        "last_checked_at": str(prior.get("last_checked_at") or ""),
        "result_synced_at": str(prior.get("result_synced_at") or ""),
        "last_error": str(prior.get("last_error") or ""),
    }
    if prior.get("status") == "completed" and prior.get("result_synced_at"):
        entry["status"] = "completed"
    registry[match_id] = entry
    if teams_match_id and teams_match_id != match_id:
        registry.pop(teams_match_id, None)
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
    touched = False
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        candidates = {
            str(key).strip(),
            str(entry.get("match_id") or "").strip(),
            str(entry.get("external_match_id") or "").strip(),
            str(entry.get("teams_match_id") or "").strip(),
        }
        if str(match_id).strip() not in candidates:
            continue
        entry["status"] = "completed"
        entry["result_synced_at"] = datetime.now().isoformat()
        entry["actual_score"] = actual_score
        entry["actual_winner"] = actual_winner
        entry["last_error"] = ""
        registry[key] = entry
        touched = True
    if not touched:
        return
    _save_registry(base_dir, registry)


def migrate_result_sync_registry_match_ids(base_dir: Optional[str] = None) -> Dict[str, Any]:
    from storage import PredictionArchiveStore

    registry = _load_registry(base_dir)
    archive = PredictionArchiveStore(base_dir).load()
    migrated = 0
    merged = 0
    normalized: Dict[str, Dict[str, Any]] = {}
    for key, entry in list(registry.items()):
        if not isinstance(entry, dict):
            continue
        archived = archive.get(key) if key in archive else None
        external_match_id = str(entry.get("external_match_id") or "").strip()
        if not external_match_id and isinstance(archived, dict):
            external_match_id = _resolve_external_match_id(archived)
        teams_match_id = str(entry.get("teams_match_id") or "").strip() or _build_teams_match_id(entry)
        target_key = external_match_id or str(entry.get("match_id") or key).strip() or key
        updated_entry = dict(entry)
        updated_entry["match_id"] = target_key
        updated_entry["external_match_id"] = external_match_id
        updated_entry["teams_match_id"] = teams_match_id
        existing = normalized.get(target_key)
        if existing:
            normalized[target_key] = _merge_registry_entry(updated_entry, existing)
            merged += 1
        else:
            normalized[target_key] = updated_entry
        if target_key != key or external_match_id or teams_match_id:
            migrated += 1
    if migrated or merged:
        _save_registry(base_dir, normalized)
    return {
        "updated_at": datetime.now().isoformat(),
        "total_entries": len(normalized) if (migrated or merged) else len(registry),
        "migrated_entries": migrated,
        "merged_entries": merged,
    }


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


def _normalize_direct_result_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    normalized = re.sub(
        r"\s+(?:盈亏|亚指|欧指|分析|预测|AI|积分|阵容)(?:\s+.*)?$",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    return normalized


def _parse_direct_result_header(text: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_direct_result_text(text)
    if not normalized:
        return None
    match = re.search(
        r"^(?:\[[^\]]+\])?\s*(?P<home>.+?)\s+"
        r"(?P<home_score>\d{1,2})\s*[-:]\s*(?P<away_score>\d{1,2})"
        r"(?:\s*\(半\s*(?P<half_home>\d{1,2})\s*[-:]\s*(?P<half_away>\d{1,2})\))?"
        r"\s+(?P<away>.+?)\s*(?:\[[^\]]+\])?$",
        normalized,
    )
    if not match:
        return None
    home_team = re.sub(r"^\[[^\]]+\]\s*", "", match.group("home")).strip()
    away_team = re.sub(r"\s*\[[^\]]+\]$", "", match.group("away")).strip()
    if not home_team or not away_team:
        return None
    result = {
        "status": "已结束",
        "home_team": home_team,
        "away_team": away_team,
        "home_score": int(match.group("home_score")),
        "away_score": int(match.group("away_score")),
        "score": f"{int(match.group('home_score'))}-{int(match.group('away_score'))}",
        "header_text": normalized,
    }
    if match.group("half_home") is not None and match.group("half_away") is not None:
        result["half_time_score"] = f"{int(match.group('half_home'))}-{int(match.group('half_away'))}"
    return result


def _parse_direct_result_title(title: str) -> Dict[str, str]:
    normalized = re.sub(r"\s+", " ", str(title or "")).strip()
    match = re.search(r"战绩走势-(?P<home>.+?)vs(?P<away>.+?)(?:【|-\s*澳客|$)", normalized)
    if not match:
        return {}
    return {
        "home_team": match.group("home").strip(),
        "away_team": match.group("away").strip(),
    }


def _extract_match_result_direct_payload(client: Any, match_id: str) -> Dict[str, Any]:
    history_url = f"https://m.okooo.com/match/history.php?MatchID={match_id}"
    client.open(history_url)
    time.sleep(2.5)
    state_text = ""
    try:
        state_text = client.state()
    except Exception:
        state_text = ""
    blocked = any(
        marker in (state_text or "")
        for marker in ("访问被阻断", "安全威胁", "您的访问被阻断", "Sorry, your request has been blocked", "<title>405</title>")
    )
    payload = client.eval_json(
        r"""
(() => {
  const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const blockedText = norm(document.body?.innerText || '');
  const selectors = [
    '.page-fixed-top .page-nav',
    '.page-nav',
    '.content.match-nav-content',
    '.match-nav-content',
    '.date',
    'header',
    'h1',
    'h2'
  ];
  const candidates = [];
  const seen = new Set();
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      const text = norm(el.innerText || '');
      if (!text || seen.has(text)) continue;
      seen.add(text);
      candidates.push({selector, text: text.slice(0, 400)});
    }
  }
  return JSON.stringify({
    title: document.title || '',
    blocked: blockedText.includes('访问被阻断') || blockedText.includes('安全威胁') || (document.title || '').includes('405'),
    body_head: blockedText.slice(0, 2000),
    candidates,
  });
})()
"""
    )
    if not isinstance(payload, dict):
        payload = {}
    payload["url"] = history_url
    payload["blocked"] = bool(payload.get("blocked")) or blocked
    return payload


def _fetch_match_result_direct_by_match_id(base_dir: Optional[str], external_match_id: str) -> Optional[Dict[str, Any]]:
    wanted_id = str(external_match_id or "").strip()
    if not wanted_id:
        return None
    try:
        from collectors.okooo import DEFAULT_CHROME_PATH, build_okooo_driver_chain
        from okooo_save_snapshot import BrowserUse, LocalChromeSession, _ensure_local_chrome
    except Exception as exc:
        logger.warning("加载单场 MatchID 赛果直连抓取依赖失败 %s: %s", wanted_id, exc)
        return None

    paths = get_default_paths(base_dir)
    driver_chain = build_okooo_driver_chain("local-chrome", chrome_path=DEFAULT_CHROME_PATH)
    if not driver_chain:
        logger.warning("单场 MatchID 赛果直连抓取不可用 %s: 无可用 driver", wanted_id)
        return None

    last_error = ""
    for driver in driver_chain:
        client = None
        try:
            if driver == "local-chrome":
                chrome_meta = _ensure_local_chrome(
                    9222,
                    DEFAULT_CHROME_PATH,
                    str(paths.runtime_file("chrome_profile")),
                )
                port = int(chrome_meta.get("port") or 9222)
                client = LocalChromeSession(port=port, session_name=f"result_sync_{wanted_id}")
            else:
                client = BrowserUse(session=f"result_sync_{wanted_id}_{int(time.time())}")
            payload = _extract_match_result_direct_payload(client, wanted_id)
            if payload.get("blocked"):
                last_error = "blocked"
                continue
            title_teams = _parse_direct_result_title(str(payload.get("title") or ""))
            candidates = payload.get("candidates", [])
            parsed: Optional[Dict[str, Any]] = None
            for item in candidates if isinstance(candidates, list) else []:
                if not isinstance(item, dict):
                    continue
                parsed = _parse_direct_result_header(str(item.get("text") or ""))
                if parsed:
                    parsed["matched_selector"] = str(item.get("selector") or "")
                    break
            if not parsed:
                parsed = _parse_direct_result_header(str(payload.get("body_head") or ""))
            if parsed:
                parsed.update({k: v for k, v in title_teams.items() if v and not parsed.get(k)})
                parsed["match_id"] = wanted_id
                parsed["source"] = "okooo_history_match_direct"
                parsed["history_url"] = str(payload.get("url") or "")
                return parsed
            last_error = "not_finished_or_unparseable"
        except Exception as exc:
            last_error = str(exc)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    if last_error:
        logger.warning("按 MatchID 直连单场赛果抓取失败 %s: %s", wanted_id, last_error)
    return None


def _find_match_in_payload_by_match_id(payload: Dict[str, Any], wanted_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    matches = payload.get("matches", [])
    for item in matches if isinstance(matches, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("match_id") or "").strip() != wanted_id:
            continue
        if item.get("status") != "已结束":
            return None
        if item.get("home_score") is None or item.get("away_score") is None:
            return None
        return item
    return None


def _fetch_match_by_match_id(
    base_dir: Optional[str],
    league_code: str,
    match_date: str,
    external_match_id: str,
) -> Optional[Dict[str, Any]]:
    wanted_id = str(external_match_id or "").strip()
    if not wanted_id:
        return None
    try:
        schedule_root = get_default_paths(base_dir).schedules_dir
        for file_path in schedule_root.rglob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            matched = _find_match_in_payload_by_match_id(payload, wanted_id)
            if matched:
                return matched
    except Exception as exc:
        logger.warning("按 MatchID 读取已抓取赛程失败 %s: %s", wanted_id, exc)
    direct_matched = _fetch_match_result_direct_by_match_id(base_dir, wanted_id)
    if direct_matched:
        direct_matched = dict(direct_matched)
        direct_matched.setdefault("date", match_date)
        direct_matched.setdefault("league_code", league_code)
        return direct_matched
    league_name = LEAGUE_NAME_MAP.get(league_code)
    if not league_name or not match_date:
        return None
    try:
        from bulk_fetch_and_update import fetch_day

        base_day = datetime.strptime(match_date, "%Y-%m-%d")
        for delta in (0, -1, 1, -2, 2):
            query_date = (base_day + timedelta(days=delta)).strftime("%Y-%m-%d")
            payload = fetch_day(league_name, league_code, query_date)
            matched = _find_match_in_payload_by_match_id(payload or {}, wanted_id)
            if matched:
                matched = dict(matched)
                matched.setdefault("date", query_date)
                matched.setdefault("league_code", league_code)
                return matched
    except Exception as exc:
        logger.warning("按 MatchID 远程补抓赛果失败 %s: %s", wanted_id, exc)
    return None


def _match_finished_item(entry: Dict[str, Any], finished: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    wanted_id = str(entry.get("external_match_id") or entry.get("match_id") or "").strip()
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
        teams_match_id = str(entry.get("teams_match_id") or "").strip()
        entry["check_count"] = int(entry.get("check_count") or 0) + 1
        entry["last_checked_at"] = current_time.isoformat()
        if match_id in existing_results or (teams_match_id and teams_match_id in existing_results):
            entry["status"] = "completed"
            entry["result_synced_at"] = current_time.isoformat()
            entry["last_error"] = ""
            updates.append({"match_id": match_id, "updated": False, "reason": "already_completed"})
            registry[match_id] = entry
            continue

        matched = _fetch_match_by_match_id(
            base_dir,
            str(entry.get("league_code") or ""),
            str(entry.get("match_date") or ""),
            str(entry.get("external_match_id") or ""),
        )
        if not matched:
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
            target_league = str(matched.get("league_code") or entry.get("league_code") or "")
            if str(entry.get("teams_match_id") or "").strip():
                result = manager.save_result(
                    f"{entry.get('home_team')} vs {entry.get('away_team')}",
                    home_score,
                    away_score,
                    league=target_league,
                    date_override=str(matched.get("date") or entry.get("match_date") or ""),
                )
            else:
                result = manager.save_runtime_only_result(
                    entry,
                    home_score,
                    away_score,
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
