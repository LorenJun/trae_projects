from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any, Dict, List


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

STANDARD_DIMENSIONS = [
    "足球业务分析",
    "赔率与盘口交易分析",
    "概率建模",
    "统计验证与模型评估",
    "风险控制与资金管理",
    "数据工程、流程自动化与策略迭代",
]

PERSONA_DOC = "agents/football_actuary_persona.md"

AGENT_DOCS = {
    "data_collector": "agents/data_collector_agent.md",
    "match_analyzer": "agents/match_analyzer_agent.md",
    "odds_analyzer": "agents/odds_analyzer_agent.md",
    "result_tracker": "agents/result_tracker_agent.md",
}


def _read_markdown(relative_path: str) -> str:
    file_path = os.path.join(PROJECT_ROOT, relative_path)
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter(markdown: str) -> Dict[str, str]:
    lines = markdown.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}

    metadata: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = _strip_quotes(value)
    return metadata


def _extract_bullets_after_anchor(markdown: str, anchor: str, limit: int = 6) -> List[str]:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if anchor not in line:
            continue
        bullets: List[str] = []
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped:
                if bullets:
                    break
                continue
            if stripped.startswith("- "):
                bullets.append(stripped[2:].strip())
                if len(bullets) >= limit:
                    break
                continue
            if bullets:
                break
        return bullets
    return []


def _extract_primary_responsibility(markdown: str) -> str:
    match = re.search(r'主要承担“([^”]+)”职责', markdown)
    return match.group(1).strip() if match else ""


@lru_cache(maxsize=1)
def load_persona_definition() -> Dict[str, Any]:
    markdown = _read_markdown(PERSONA_DOC)
    metadata = _parse_frontmatter(markdown)
    dimensions = _extract_bullets_after_anchor(
        markdown,
        "其本质是把以下几类能力整合成统一分析身份：",
        limit=6,
    )
    if not dimensions:
        dimensions = list(STANDARD_DIMENSIONS)

    return {
        "persona_key": "football_actuary",
        "persona_name": metadata.get("persona_name", "Professional Football Betting Data Actuary"),
        "version": metadata.get("version", "unknown"),
        "purpose": metadata.get("purpose", ""),
        "source_file": PERSONA_DOC,
        "dimension_schema_version": "standard-six-v1",
        "dimensions": dimensions,
    }


@lru_cache(maxsize=None)
def load_agent_definition(agent_key: str) -> Dict[str, Any]:
    if agent_key not in AGENT_DOCS:
        raise KeyError(f"未知 agent_key: {agent_key}")

    relative_path = AGENT_DOCS[agent_key]
    markdown = _read_markdown(relative_path)
    metadata = _parse_frontmatter(markdown)

    return {
        "agent_key": agent_key,
        "agent_name": metadata.get("agent_name", agent_key),
        "version": metadata.get("version", "unknown"),
        "purpose": metadata.get("purpose", ""),
        "source_file": relative_path,
        "primary_responsibility": _extract_primary_responsibility(markdown),
    }


def get_runtime_profile(agent_keys: List[str] | None = None) -> Dict[str, Any]:
    persona = load_persona_definition()
    ordered_keys: List[str] = []
    for key in agent_keys or []:
        if key not in AGENT_DOCS:
            continue
        if key not in ordered_keys:
            ordered_keys.append(key)

    agents = [load_agent_definition(key) for key in ordered_keys]
    return {
        "persona": persona,
        "agent_roles": ordered_keys,
        "agents": agents,
        "dimensions": list(persona["dimensions"]),
        "dimension_schema_version": persona["dimension_schema_version"],
    }
