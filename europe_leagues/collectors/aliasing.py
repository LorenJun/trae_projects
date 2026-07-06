"""模块说明：维护球队名称别名映射与归一化能力。"""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from runtime.paths import get_default_paths

LEAGUE_ALIAS_KEYS = {
    'friendly': ('friendly', '友谊赛'),
    'europa_league': ('europa_league', '欧联', '欧罗巴'),
    'champions_league': ('champions_league', '欧冠'),
    'conference_league': ('conference_league', '欧协联'),
}


# 全角标点 → 半角，避免 "刚果（金）" 与 "刚果(金)" 因符号差异归不到同一 canonical
_PUNCT_FULL_TO_HALF = {
    '（': '(', '）': ')',
    '［': '[', '］': ']',
    '｛': '{', '｝': '}',
    '，': ',', '。': '.', '：': ':', '；': ';',
    '！': '!', '？': '?',
    '＆': '&', '／': '/', '＼': '\\',
    '－': '-', '＿': '_',
    '＇': "'", '＂': '"',
}
_PUNCT_TRANS = str.maketrans(_PUNCT_FULL_TO_HALF)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_symbols(name: str) -> str:
    """把全角标点/多余空白折叠成半角单空格版本，便于跨源比对。"""
    text = str(name or '').strip()
    if not text:
        return ''
    text = text.translate(_PUNCT_TRANS)
    text = _WHITESPACE_RE.sub('', text)
    return text


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
            canonical_norm = _normalize_symbols(canonical_name)
            if canonical_norm and canonical_norm not in league_map:
                league_map[canonical_norm] = canonical_name
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_name = str(alias or '').strip()
                    if not alias_name:
                        continue
                    league_map[alias_name] = canonical_name
                    alias_norm = _normalize_symbols(alias_name)
                    if alias_norm and alias_norm not in league_map:
                        league_map[alias_norm] = canonical_name
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
        if raw_name in league_map:
            return league_map.get(raw_name, raw_name)
        raw_norm = _normalize_symbols(raw_name)
        if raw_norm and raw_norm in league_map:
            return league_map[raw_norm]

    global_matches = set()
    raw_norm = _normalize_symbols(raw_name)
    for candidate_map in mapping.values():
        if not isinstance(candidate_map, dict):
            continue
        canonical_name = candidate_map.get(raw_name)
        if not canonical_name and raw_norm:
            canonical_name = candidate_map.get(raw_norm)
        if canonical_name:
            global_matches.add(str(canonical_name).strip())
    if len(global_matches) == 1:
        return next(iter(global_matches))
    return raw_name
