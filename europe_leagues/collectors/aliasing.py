"""模块说明：维护球队名称别名映射与归一化能力。"""

from __future__ import annotations

import json
from typing import Dict, Optional

from runtime.paths import get_default_paths

LEAGUE_ALIAS_KEYS = {
    'europa_league': ('europa_league', '欧联', '欧罗巴'),
    'champions_league': ('champions_league', '欧冠'),
    'conference_league': ('conference_league', '欧协联'),
}


def load_team_alias_map(base_dir: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    path = get_default_paths(base_dir).alias_map_path
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for league, mapping in raw.items():
        if not isinstance(mapping, dict):
            continue
        league_map: Dict[str, str] = {}
        for canonical, aliases in mapping.items():
            canonical_name = str(canonical or '').strip()
            if not canonical_name:
                continue
            league_map[canonical_name] = canonical_name
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_name = str(alias or '').strip()
                    if alias_name:
                        league_map[alias_name] = canonical_name
        league_key = str(league).strip()
        if not league_key:
            continue
        out[league_key] = league_map
        for canonical_key, aliases in LEAGUE_ALIAS_KEYS.items():
            if league_key == canonical_key or league_key in aliases:
                out.setdefault(canonical_key, dict(league_map))
                for alias in aliases:
                    out.setdefault(alias, dict(league_map))
    return out


def normalize_team_name(league_code: str, name: str, alias_map: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    raw_name = str(name or '').strip()
    if not raw_name:
        return ''
    mapping = alias_map if alias_map is not None else load_team_alias_map()
    league_map = mapping.get(league_code, {})
    if isinstance(league_map, dict):
        return league_map.get(raw_name, raw_name)
    return raw_name
