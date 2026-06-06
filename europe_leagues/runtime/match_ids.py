from __future__ import annotations

from typing import Any


def normalize_external_match_id(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw.isdigit() else ""


def first_valid_external_match_id(*candidates: Any) -> str:
    for candidate in candidates:
        normalized = normalize_external_match_id(candidate)
        if normalized:
            return normalized
    return ""
