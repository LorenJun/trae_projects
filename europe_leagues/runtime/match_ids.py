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


def require_external_match_id(value: Any, *, field_name: str = "external_match_id") -> str:
    normalized = normalize_external_match_id(value)
    if not normalized:
        raise ValueError(f"{field_name} 必须是纯数字 external_match_id")
    return normalized


def build_okooo_match_url(page: str, external_match_id: Any) -> str:
    match_id = require_external_match_id(external_match_id)
    page_name = str(page or "").strip().strip("/")
    if not page_name:
        raise ValueError("page 不能为空")
    return f"https://m.okooo.com/match/{page_name}.php?MatchID={match_id}"
