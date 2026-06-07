from __future__ import annotations

import json
import time
import re
from pathlib import Path
from typing import Any, Dict, Tuple


_TEXT_MARKERS = (
    "访问被阻断",
    "安全威胁",
    "您的访问被阻断",
    "sorry, your request has been blocked",
    "<title>405</title>",
    "请进行验证",
    "滑动到最右边",
    "拖动滑块",
    "请按住滑块",
    "验证码",
)

_HTML_MARKERS = (
    "captcha",
    "verify",
    "verification",
    "slider",
    "geetest",
    "aliyun",
    "nc_",
    "vcode",
)

_BREAKER_TTL_SECONDS = 600
_THROTTLE_MIN_INTERVAL_SECONDS = 1.0
_BREAKER_FILE_NAME = "okooo_verification_breaker.json"
_THROTTLE_FILE_NAME = "okooo_market_throttle.json"


def _runtime_dir(base_dir: str) -> Path:
    return Path(base_dir).resolve() / ".okooo-scraper" / "runtime"


def _runtime_file(base_dir: str, name: str) -> Path:
    path = _runtime_dir(base_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_runtime_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_runtime_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _breaker_key(match_id: str, market_family: str) -> str:
    return f"{str(match_id or '').strip()}::{str(market_family or '').strip()}"


def read_okooo_verification_breaker(base_dir: str, match_id: str, market_family: str) -> Dict[str, Any]:
    key = _breaker_key(match_id, market_family)
    payload = _load_runtime_json(_runtime_file(base_dir, _BREAKER_FILE_NAME))
    item = payload.get(key)
    if not isinstance(item, dict):
        return {"open": False}
    expires_at = float(item.get("expires_at") or 0.0)
    if expires_at <= time.time():
        payload.pop(key, None)
        _save_runtime_json(_runtime_file(base_dir, _BREAKER_FILE_NAME), payload)
        return {"open": False}
    return {"open": True, **item}


def open_okooo_verification_breaker(base_dir: str, match_id: str, market_family: str, *, ttl_seconds: int = _BREAKER_TTL_SECONDS, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    key = _breaker_key(match_id, market_family)
    path = _runtime_file(base_dir, _BREAKER_FILE_NAME)
    payload = _load_runtime_json(path)
    now = time.time()
    item = {
        "match_id": str(match_id or "").strip(),
        "market_family": str(market_family or "").strip(),
        "opened_at": now,
        "expires_at": now + max(1, int(ttl_seconds)),
    }
    if isinstance(details, dict):
        item.update(details)
    payload[key] = item
    _save_runtime_json(path, payload)
    return item


def wait_for_okooo_market_slot(base_dir: str, *, min_interval_seconds: float = _THROTTLE_MIN_INTERVAL_SECONDS) -> float:
    path = _runtime_file(base_dir, _THROTTLE_FILE_NAME)
    payload = _load_runtime_json(path)
    now = time.time()
    last_access_at = float(payload.get("last_access_at") or 0.0)
    wait_seconds = max(0.0, float(min_interval_seconds) - max(0.0, now - last_access_at))
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    payload["last_access_at"] = time.time()
    _save_runtime_json(path, payload)
    return wait_seconds


def is_okooo_blocked_text(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    compact = re.sub(r"\s+", "", lowered)
    if any(marker in raw for marker in _TEXT_MARKERS[:5]):
        return True
    if any(marker in compact for marker in ("请进行验证", "滑动到最右边", "拖动滑块", "请按住滑块", "验证码")):
        return True
    if any(marker in lowered for marker in _TEXT_MARKERS[5:]):
        return True
    if "<canvas" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if "<iframe" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if "<img" in lowered and any(marker in lowered for marker in _HTML_MARKERS):
        return True
    if re.search(r"<img[^>]+(?:width|height)\s*=\s*['\"]?(?:2\d\d|[3-9]\d\d)", lowered):
        if any(marker in lowered for marker in _HTML_MARKERS):
            return True
    if re.search(r"style\s*=\s*['\"][^'\"]*(?:width|height)\s*:\s*(?:2\d\d|[3-9]\d\d)px", lowered):
        if any(marker in lowered for marker in _HTML_MARKERS):
            return True
    return False
