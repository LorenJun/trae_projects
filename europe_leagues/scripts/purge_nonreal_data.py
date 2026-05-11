#!/usr/bin/env python3
"""Scan, purge, and rebuild runtime data that does not match five-league SoT."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple


FIVE_LEAGUES = (
    "premier_league",
    "la_liga",
    "serie_a",
    "bundesliga",
    "ligue_1",
)

UI_GARBAGE_TOKENS = {"盈亏", "亚指", "欧指", "分析", "预测", "AI", "积分", "阵容"}

SCHEDULE_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]*)\|\s*([^|]+?)\s*\|\s*([^|]*)\|\s*([^|]+?)\s*\|",
    re.M,
)
TEAM_TABLE_RE = re.compile(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|", re.M)
MEMORY_KEY_RE = re.compile(r"^- \[([^\]]+)\]")


def _load_alias_map(base_dir: Path) -> Dict[str, Dict[str, str]]:
    alias_path = base_dir / "okooo_team_aliases.json"
    if not alias_path.exists():
        return {}
    raw = json.loads(alias_path.read_text(encoding="utf-8"))
    result: Dict[str, Dict[str, str]] = {}
    for league, mapping in raw.items():
        league_map: Dict[str, str] = {}
        if not isinstance(mapping, dict):
            result[str(league)] = league_map
            continue
        for canonical, aliases in mapping.items():
            canonical_name = str(canonical or "").strip()
            if not canonical_name:
                continue
            league_map[canonical_name] = canonical_name
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_name = str(alias or "").strip()
                    if alias_name:
                        league_map[alias_name] = canonical_name
        result[str(league)] = league_map
    return result


def _norm_name(alias_map: Dict[str, Dict[str, str]], league: str, name: Any) -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    return alias_map.get(league, {}).get(value, value)


def _build_truth_index(base_dir: Path) -> Tuple[Dict[str, Set[Tuple[str, str, str]]], Dict[str, Set[str]]]:
    alias_map = _load_alias_map(base_dir)
    schedule_index: Dict[str, Set[Tuple[str, str, str]]] = defaultdict(set)
    team_sets: Dict[str, Set[str]] = defaultdict(set)
    for league in FIVE_LEAGUES:
        text = (base_dir / league / "teams_2025-26.md").read_text(encoding="utf-8")
        for match in TEAM_TABLE_RE.finditer(text):
            team_sets[league].add(_norm_name(alias_map, league, match.group(1)))
        for match in SCHEDULE_ROW_RE.finditer(text):
            match_date = match.group(1).strip()
            home_team = _norm_name(alias_map, league, match.group(3))
            away_team = _norm_name(alias_map, league, match.group(5))
            if match_date and home_team and away_team:
                schedule_index[league].add((match_date, home_team, away_team))
    return schedule_index, team_sets


def _scan_memory(memory_path: Path, schedule_index: Dict[str, Set[Tuple[str, str, str]]]) -> Dict[str, Any]:
    lines = memory_path.read_text(encoding="utf-8").splitlines(True)
    entries_to_remove: List[str] = []
    blocks_to_remove = 0
    i = 0
    while i < len(lines):
        match = MEMORY_KEY_RE.match(lines[i])
        if not match:
            i += 1
            continue
        key = match.group(1)
        parts = key.split("|")
        if len(parts) >= 4:
            league, match_date, home_team, away_team = parts[:4]
            if league in FIVE_LEAGUES and (match_date, home_team, away_team) not in schedule_index[league]:
                entries_to_remove.append(key)
                blocks_to_remove += 1
        i += 1
    return {
        "entries_to_remove": entries_to_remove,
        "blocks_to_remove": blocks_to_remove,
    }


def _rewrite_memory(memory_path: Path, entries_to_remove: Set[str]) -> int:
    lines = memory_path.read_text(encoding="utf-8").splitlines(True)
    output: List[str] = []
    removed = 0
    i = 0
    while i < len(lines):
        match = MEMORY_KEY_RE.match(lines[i])
        if match and match.group(1) in entries_to_remove:
            removed += 1
            i += 1
            while i < len(lines):
                next_match = MEMORY_KEY_RE.match(lines[i])
                if next_match or lines[i].startswith("#### ") or "<!-- prediction-memory:end -->" in lines[i]:
                    break
                i += 1
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        output.append(lines[i])
        i += 1
    memory_path.write_text("".join(output), encoding="utf-8")
    return removed


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_nonreal_record(
    *,
    league: str,
    match_date: str,
    home_team: str,
    away_team: str,
    schedule_index: Dict[str, Set[Tuple[str, str, str]]],
    team_sets: Dict[str, Set[str]],
) -> bool:
    if league not in FIVE_LEAGUES:
        return False
    if not match_date or not home_team or not away_team:
        return True
    if home_team not in team_sets[league] or away_team not in team_sets[league]:
        return True
    return (match_date, home_team, away_team) not in schedule_index[league]


def _scan_archive(
    runtime_dir: Path,
    schedule_index: Dict[str, Set[Tuple[str, str, str]]],
    team_sets: Dict[str, Set[str]],
    alias_map: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    archive_path = runtime_dir / "prediction_archive.json"
    archive = _load_json(archive_path, {})
    bad_ids: List[str] = []
    details: List[Dict[str, str]] = []
    if not isinstance(archive, dict):
        return {"path": str(archive_path), "bad_ids": bad_ids, "details": details}
    for key, value in archive.items():
        if not isinstance(value, dict):
            continue
        league = str(value.get("league") or "").strip()
        if league not in FIVE_LEAGUES:
            continue
        match_date = str(value.get("match_date") or "").strip()
        home_team = _norm_name(alias_map, league, value.get("home_team"))
        away_team = _norm_name(alias_map, league, value.get("away_team"))
        if _is_nonreal_record(
            league=league,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            schedule_index=schedule_index,
            team_sets=team_sets,
        ):
            bad_ids.append(key)
            details.append(
                {
                    "match_id": key,
                    "league": league,
                    "match_date": match_date,
                    "home_team": home_team,
                    "away_team": away_team,
                }
            )
    return {"path": str(archive_path), "bad_ids": bad_ids, "details": details}


def _purge_archive(archive_path: Path, bad_ids: Set[str]) -> int:
    archive = _load_json(archive_path, {})
    if not isinstance(archive, dict):
        return 0
    removed = 0
    for bad_id in list(bad_ids):
        if bad_id in archive:
            del archive[bad_id]
            removed += 1
    _write_json(archive_path, archive)
    return removed


def _scan_registry(
    runtime_dir: Path,
    schedule_index: Dict[str, Set[Tuple[str, str, str]]],
    team_sets: Dict[str, Set[str]],
    alias_map: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    registry_path = runtime_dir / "result_sync_registry.json"
    registry = _load_json(registry_path, {})
    bad_ids: List[str] = []
    details: List[Dict[str, str]] = []
    if not isinstance(registry, dict):
        return {"path": str(registry_path), "bad_ids": bad_ids, "details": details}
    for key, value in registry.items():
        if not isinstance(value, dict):
            continue
        league = str(value.get("league_code") or "").strip()
        if league not in FIVE_LEAGUES:
            continue
        match_date = str(value.get("match_date") or "").strip()
        home_team = _norm_name(alias_map, league, value.get("home_team"))
        away_team = _norm_name(alias_map, league, value.get("away_team"))
        if _is_nonreal_record(
            league=league,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            schedule_index=schedule_index,
            team_sets=team_sets,
        ):
            bad_ids.append(key)
            details.append(
                {
                    "match_id": key,
                    "league": league,
                    "match_date": match_date,
                    "home_team": home_team,
                    "away_team": away_team,
                }
            )
    return {"path": str(registry_path), "bad_ids": bad_ids, "details": details}


def _purge_registry(registry_path: Path, bad_ids: Set[str]) -> int:
    registry = _load_json(registry_path, {})
    if not isinstance(registry, dict):
        return 0
    removed = 0
    for bad_id in list(bad_ids):
        if bad_id in registry:
            del registry[bad_id]
            removed += 1
    _write_json(registry_path, registry)
    return removed


def _scan_snapshot_files(
    base_dir: Path,
    schedule_index: Dict[str, Set[Tuple[str, str, str]]],
    team_sets: Dict[str, Set[str]],
    alias_map: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    snapshots_dir = base_dir / ".okooo-scraper" / "snapshots"
    bad_files: List[str] = []
    details: List[Dict[str, str]] = []
    for path in sorted(snapshots_dir.glob("*/*.json")):
        league = path.parent.name
        if league not in FIVE_LEAGUES:
            continue
        try:
            payload = _load_json(path, {})
        except Exception:
            bad_files.append(str(path))
            details.append({"path": str(path), "reason": "invalid_json"})
            continue
        if not isinstance(payload, dict):
            bad_files.append(str(path))
            details.append({"path": str(path), "reason": "invalid_payload"})
            continue
        match_date = str(payload.get("match_date") or "").strip()
        home_team = _norm_name(alias_map, league, payload.get("home_team"))
        away_team = _norm_name(alias_map, league, payload.get("away_team"))
        if _is_nonreal_record(
            league=league,
            match_date=match_date,
            home_team=home_team,
            away_team=away_team,
            schedule_index=schedule_index,
            team_sets=team_sets,
        ):
            bad_files.append(str(path))
            details.append(
                {
                    "path": str(path),
                    "league": league,
                    "match_date": match_date,
                    "home_team": home_team,
                    "away_team": away_team,
                }
            )
    return {"bad_files": bad_files, "details": details}


def _scan_schedule_cache_files(
    base_dir: Path,
    schedule_index: Dict[str, Set[Tuple[str, str, str]]],
    team_sets: Dict[str, Set[str]],
    alias_map: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    schedules_dir = base_dir / ".okooo-scraper" / "schedules"
    bad_files: List[str] = []
    details: List[Dict[str, str]] = []
    for path in sorted(schedules_dir.glob("*/*.json")):
        league = path.parent.name
        if league not in FIVE_LEAGUES:
            continue
        try:
            payload = _load_json(path, {})
        except Exception:
            bad_files.append(str(path))
            details.append({"path": str(path), "reason": "invalid_json"})
            continue
        if not isinstance(payload, dict):
            bad_files.append(str(path))
            details.append({"path": str(path), "reason": "invalid_payload"})
            continue
        match_date = str(payload.get("date") or "").strip()
        rows = payload.get("matches") or []
        file_bad_reason = ""
        for row in rows:
            if not isinstance(row, dict):
                file_bad_reason = "nondict_row"
                break
            raw_text = str(row.get("raw_text") or "")
            home_team = _norm_name(alias_map, league, row.get("home_team"))
            away_team = _norm_name(alias_map, league, row.get("away_team"))
            score_text = str(row.get("score") or "").strip()
            if home_team in UI_GARBAGE_TOKENS or away_team in UI_GARBAGE_TOKENS:
                file_bad_reason = "ui_garbage"
                break
            if score_text and "完" not in raw_text:
                file_bad_reason = "time_as_score"
                break
            if not home_team or not away_team:
                file_bad_reason = "missing_teams"
                break
            if home_team not in team_sets[league] or away_team not in team_sets[league]:
                file_bad_reason = "cross_league_team"
                break
            if match_date and (match_date, home_team, away_team) not in schedule_index[league]:
                file_bad_reason = "not_in_sot"
                break
        if file_bad_reason:
            bad_files.append(str(path))
            details.append({"path": str(path), "reason": file_bad_reason})
    return {"bad_files": bad_files, "details": details}


def _delete_files(paths: Iterable[str]) -> int:
    removed = 0
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def _rebuild_runtime(base_dir: Path, sample_limit: int, rag_limit: int, refresh_accuracy: bool) -> Dict[str, Any]:
    from domain.rag import HybridRAGService
    from result_manager import ResultManager
    from runtime.memory_samples import sync_prediction_memory_samples

    samples = sync_prediction_memory_samples(str(base_dir), limit=sample_limit)
    rag_service = HybridRAGService(str(base_dir))
    rag_result = rag_service.refresh(limit=rag_limit)
    accuracy_result = None
    if refresh_accuracy:
        manager = ResultManager(base_dir=str(base_dir))
        accuracy_result = manager.update_accuracy_stats()
    return {
        "memory_samples": {
            "total_candidates": samples.get("total_candidates", 0),
            "completed_samples": samples.get("completed_samples", 0),
            "output_file": samples.get("output_file"),
        },
        "rag_rebuild": {
            "rag_mode": rag_result.get("rag_mode"),
            "case_count": rag_result.get("case_count"),
        },
        "accuracy_refresh": {
            "overall": (accuracy_result or {}).get("overall", {}),
        } if accuracy_result else None,
    }


def scan_nonreal_data(base_dir: Path, memory_path: Path) -> Dict[str, Any]:
    alias_map = _load_alias_map(base_dir)
    schedule_index, team_sets = _build_truth_index(base_dir)
    runtime_dir = base_dir / ".okooo-scraper" / "runtime"

    memory_report = _scan_memory(memory_path, schedule_index)
    archive_report = _scan_archive(runtime_dir, schedule_index, team_sets, alias_map)
    registry_report = _scan_registry(runtime_dir, schedule_index, team_sets, alias_map)
    snapshot_report = _scan_snapshot_files(base_dir, schedule_index, team_sets, alias_map)
    schedule_report = _scan_schedule_cache_files(base_dir, schedule_index, team_sets, alias_map)

    return {
        "memory": memory_report,
        "archive": archive_report,
        "registry": registry_report,
        "snapshots": snapshot_report,
        "schedule_cache": schedule_report,
        "summary": {
            "memory_blocks": memory_report["blocks_to_remove"],
            "archive_records": len(archive_report["bad_ids"]),
            "registry_records": len(registry_report["bad_ids"]),
            "snapshot_files": len(snapshot_report["bad_files"]),
            "schedule_files": len(schedule_report["bad_files"]),
        },
    }


def purge_nonreal_data(
    *,
    base_dir: Path,
    memory_path: Path,
    confirm: bool,
    sample_limit: int = 100,
    rag_limit: int = 200,
    refresh_accuracy: bool = True,
) -> Dict[str, Any]:
    scan_report = scan_nonreal_data(base_dir, memory_path)
    result = {
        "confirmed": bool(confirm),
        "base_dir": str(base_dir),
        "memory_path": str(memory_path),
        "scan": scan_report,
        "removed": {
            "memory_blocks": 0,
            "archive_records": 0,
            "registry_records": 0,
            "snapshot_files": 0,
            "schedule_files": 0,
        },
        "rebuild": None,
    }
    if not confirm:
        result["message"] = "当前为预览模式；追加 --yes 后才会执行删除与重建。"
        return result

    memory_entries = set(scan_report["memory"]["entries_to_remove"])
    archive_ids = set(scan_report["archive"]["bad_ids"])
    registry_ids = set(scan_report["registry"]["bad_ids"])

    result["removed"]["memory_blocks"] = _rewrite_memory(memory_path, memory_entries) if memory_entries else 0
    result["removed"]["archive_records"] = _purge_archive(
        base_dir / ".okooo-scraper" / "runtime" / "prediction_archive.json",
        archive_ids,
    ) if archive_ids else 0
    result["removed"]["registry_records"] = _purge_registry(
        base_dir / ".okooo-scraper" / "runtime" / "result_sync_registry.json",
        registry_ids,
    ) if registry_ids else 0
    result["removed"]["snapshot_files"] = _delete_files(scan_report["snapshots"]["bad_files"])
    result["removed"]["schedule_files"] = _delete_files(scan_report["schedule_cache"]["bad_files"])
    result["rebuild"] = _rebuild_runtime(
        base_dir=base_dir,
        sample_limit=sample_limit,
        rag_limit=rag_limit,
        refresh_accuracy=refresh_accuracy,
    )
    result["rescan"] = scan_nonreal_data(base_dir, memory_path)
    return result
