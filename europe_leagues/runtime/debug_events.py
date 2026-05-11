"""Best-effort local debug event emitter used by ad-hoc instrumentation.

The prediction pipeline must never depend on a local debug server being alive.
This helper keeps the old debug payload shape but makes emission opt-in and
failure-tolerant.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib import request


DEBUG_EVENT_ENV = "ENABLE_LOCAL_DEBUG_EVENTS"
DEBUG_EVENT_URL_ENV = "TRAE_DEBUG_EVENT_URL"
DEFAULT_DEBUG_EVENT_URL = "http://127.0.0.1:7777/event"


def emit_local_debug_event(payload: Mapping[str, Any]) -> bool:
    """Send a local debug event when explicitly enabled.

    Returns ``True`` only when the event is sent successfully. Any formatting,
    network, or local server error is swallowed so prediction flows remain
    deterministic.
    """

    enabled = os.environ.get(DEBUG_EVENT_ENV, "0").strip() in ("1", "true", "True")
    if not enabled or not isinstance(payload, Mapping):
        return False

    url = os.environ.get(DEBUG_EVENT_URL_ENV, DEFAULT_DEBUG_EVENT_URL).strip()
    if not url:
        return False

    try:
        req = request.Request(
            url,
            data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=0.5) as response:
            response.read()
        return True
    except Exception:
        return False
