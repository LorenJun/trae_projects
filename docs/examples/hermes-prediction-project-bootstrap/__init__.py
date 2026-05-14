"""Hermes plugin template for the europe_leagues prediction project.

Copy this directory to:
  ~/.hermes/plugins/prediction-project-bootstrap/

Then restart Hermes or start a new session.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

PLUGIN_NAME = "prediction-project-bootstrap"
HERMES_HOME = Path.home() / ".hermes"
LOG_PATH = HERMES_HOME / "logs" / f"{PLUGIN_NAME}.log"
LOCK_PATH = HERMES_HOME / f".{PLUGIN_NAME}.lock"

# Adjust these paths if your project lives somewhere else.
PROJECT_ROOTS = (
    "/Users/bytedance/trae_projects",
    "/Users/bytedance/trae_projects/europe_leagues",
)
PREDICTION_PROJECT_DIR = "/Users/bytedance/trae_projects/europe_leagues"

# Keep hooks lightweight and read-only. Do not add business write commands here.
ENABLE_SKILLS_UPDATE = True
ENABLE_PROJECT_HEALTH_CHECK = True
ENABLE_CLI_SMOKE_CHECK = False

SKILLS_UPDATE_TIMEOUT_SEC = 180
HEALTH_CHECK_TIMEOUT_SEC = 90
CLI_SMOKE_CHECK_TIMEOUT_SEC = 30


def _append_log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def _acquire_lock() -> bool:
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(datetime.now().timestamp()))
        return True
    except Exception:
        return False


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return None


def _collect_candidate_paths(kwargs: dict[str, Any]) -> list[str]:
    candidates = []
    for key in ("cwd", "workspace", "project_path", "repo_root", "workdir"):
        normalized = _normalize_path(str(kwargs.get(key))) if kwargs.get(key) else None
        if normalized:
            candidates.append(normalized)

    for env_name in ("PWD", "HERMES_WORKSPACE", "HERMES_PROJECT_PATH"):
        normalized = _normalize_path(os.environ.get(env_name))
        if normalized:
            candidates.append(normalized)

    try:
        candidates.append(str(Path.cwd().resolve()))
    except Exception:
        pass

    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _is_prediction_project_session(kwargs: dict[str, Any]) -> bool:
    normalized_roots = [_normalize_path(path) for path in PROJECT_ROOTS]
    normalized_roots = [path for path in normalized_roots if path]
    for candidate in _collect_candidate_paths(kwargs):
        if any(candidate == root or candidate.startswith(f"{root}/") for root in normalized_roots):
            return True
    return False


def _run_command(
    command: list[str],
    *,
    cwd: str,
    timeout_sec: int,
    label: str,
) -> tuple[int | None, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired:
        _append_log(f"error: {label} timed out after {timeout_sec}s")
        return None, ""
    except Exception as exc:
        _append_log(f"error: {label} failed: {exc}")
        return None, ""


def _single_line(text: str, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _summarize_skills_update(output: str) -> str:
    if not output.strip():
        return "no_output"
    if "No skills tracked in lock file." in output:
        return "no_tracked_skills"
    if "Checking for skill updates" in output and "updated" not in output.lower():
        return "checked"
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return _single_line(stripped)
    return "no_output"


def _summarize_health_check(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return f"unparsed_output={_single_line(output)}"

    success = payload.get("success")
    data = payload.get("data", {})
    dependency_report = data.get("openclaw_dependency_report", {})
    recommended_action = data.get("recommended_action", {})
    parts = [
        f"success={success}",
        f"preferred_driver={data.get('preferred_okooo_driver', 'unknown')}",
        f"local_chrome={data.get('local_chrome_available', 'unknown')}",
        f"browser_use_cli={data.get('browser_use_cli_available', 'unknown')}",
        f"browser_use_module={data.get('browser_use_python_module_available', 'unknown')}",
        f"openclaw_full_ready={dependency_report.get('openclaw_full_ready', 'unknown')}",
    ]
    if recommended_action:
        owner = recommended_action.get("owner")
        reason = recommended_action.get("reason")
        if owner:
            parts.append(f"recommended_owner={owner}")
        if reason:
            parts.append(f"reason={_single_line(str(reason), limit=120)}")
    return " | ".join(str(part) for part in parts)


def _run_skills_update() -> None:
    if not ENABLE_SKILLS_UPDATE:
        _append_log("skip: skills update disabled")
        return

    npx = shutil.which("npx")
    if not npx:
        _append_log("skip: npx not found")
        return

    exit_code, output = _run_command(
        [npx, "skills", "update", "-g", "-y"],
        cwd=str(Path.home()),
        timeout_sec=SKILLS_UPDATE_TIMEOUT_SEC,
        label="skills-update",
    )
    _append_log(
        f"skills-update result: exit_code={exit_code} summary={_summarize_skills_update(output)}"
    )


def _run_project_health_check() -> None:
    if not ENABLE_PROJECT_HEALTH_CHECK:
        _append_log("skip: project health-check disabled")
        return

    python3 = shutil.which("python3")
    if not python3:
        _append_log("skip: python3 not found")
        return

    exit_code, output = _run_command(
        [python3, "prediction_system.py", "health-check", "--json"],
        cwd=PREDICTION_PROJECT_DIR,
        timeout_sec=HEALTH_CHECK_TIMEOUT_SEC,
        label="prediction-health-check",
    )
    _append_log(
        "prediction-health-check summary: "
        f"exit_code={exit_code} | {_summarize_health_check(output)}"
    )


def _run_cli_smoke_check() -> None:
    if not ENABLE_CLI_SMOKE_CHECK:
        return

    python3 = shutil.which("python3")
    if not python3:
        _append_log("skip: python3 not found for cli smoke check")
        return

    _run_command(
        [python3, "prediction_system.py", "--help"],
        cwd=PREDICTION_PROJECT_DIR,
        timeout_sec=CLI_SMOKE_CHECK_TIMEOUT_SEC,
        label="prediction-cli-smoke-check",
    )


def on_session_start(session_id: str, model: str, platform: str, **kwargs: Any) -> None:
    _append_log(
        f"hook: session_start session_id={session_id} model={model} platform={platform}"
    )

    if not _acquire_lock():
        _append_log("skip: another bootstrap session is already running")
        return

    try:
        _run_skills_update()
        if _is_prediction_project_session(kwargs):
            _append_log("info: prediction project session detected")
            _run_project_health_check()
            _run_cli_smoke_check()
        else:
            _append_log("skip: not in prediction project scope")
    finally:
        _release_lock()


def register(ctx: Any) -> None:
    ctx.register_hook("on_session_start", on_session_start)
