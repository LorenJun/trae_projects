"""模块说明：把澳客拉到的世界杯赛程覆盖式写回 `world_cup/teams_2026.md`。

只更新赛程行的「日期 / 时间 / 主队 / 客队 / MatchID」五段，比分列仅在
原值为空或 `-` 且澳客返回 finished 时覆盖；备注列按 `；` 切分，仅重写
「前缀非预测段」中的 `MatchID:` 段，保留所有预测尾段。

匹配键：备注中的 `MatchID:####`（首选）→ 其次 `(date, home_canonical,
away_canonical)`。找不到匹配则落 warning，不新增行。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from collectors.aliasing import load_team_alias_map, normalize_team_name


_MATCHID_RE = re.compile(r"MatchID\s*[:：]\s*(\d+)", re.IGNORECASE)
_SCORE_RE = re.compile(r"^\s*\d+\s*-\s*\d+\s*$")

# 预测尾段关键词：段内出现任一即视为预测尾段，整段原样保留。
_PREDICTION_KEYWORDS = (
    "预测:", "信心:", "比分:", "大小:", "进球数:", "爆冷:", "仓位:",
    "案例:", "情景:", "动态调权:", "解读:", "复盘:",
)


def _split_row(line: str) -> Optional[List[str]]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) < 6:
        return None
    return cells


def _join_row(cells: List[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _extract_matchid(note: str) -> str:
    if not note:
        return ""
    m = _MATCHID_RE.search(note)
    return m.group(1) if m else ""


def _segment_is_prediction(seg: str) -> bool:
    return any(kw in seg for kw in _PREDICTION_KEYWORDS)


def _rewrite_note(original_note: str, new_match_id: str) -> str:
    """按 `；` 切段，只重写前缀段里的 MatchID; 预测尾段保留原样。

    - 段内包含预测关键词（`预测:` / `信心:` / …）→ 原样保留。
    - 段内包含 `MatchID:` 且**不**是预测段 → 用 new_match_id 覆盖数字部分。
    - 其余非预测前缀段 → 原样保留。
    - 若原备注中前缀段都没有 `MatchID:` 段但需要写入，则追加一段。
    """
    text = str(original_note or "").strip()
    if not text:
        return f"MatchID:{new_match_id}" if new_match_id else ""

    segments = [seg.strip() for seg in text.split("；")]
    matchid_written = False
    for i, seg in enumerate(segments):
        if not seg:
            continue
        if _segment_is_prediction(seg):
            continue
        if _MATCHID_RE.search(seg) and new_match_id:
            segments[i] = _MATCHID_RE.sub(f"MatchID:{new_match_id}", seg, count=1)
            matchid_written = True
    if new_match_id and not matchid_written:
        # 找最后一个非预测前缀段插入位置
        insert_at = 0
        for i, seg in enumerate(segments):
            if seg and not _segment_is_prediction(seg):
                insert_at = i + 1
        segments.insert(insert_at, f"MatchID:{new_match_id}")
    return "；".join(seg for seg in segments if seg)


def _find_schedule_range(lines: List[str]) -> Tuple[int, int]:
    """返回赛程信息区块的 [start, end)。找不到则返回 (0, len(lines))。"""
    start = -1
    end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if start < 0 and s.startswith("## 赛程信息"):
            start = i + 1
            continue
        if start >= 0 and s.startswith("## ") and not s.startswith("## 赛程信息"):
            end = i
            break
    if start < 0:
        return 0, len(lines)
    return start, end


def _index_rows(
    lines: List[str], start: int, end: int, alias_map: Dict[str, Dict[str, str]]
) -> Tuple[Dict[str, int], Dict[Tuple[str, str, str], int]]:
    """构建 md 赛程行的索引：MatchID → line_no ; (date, home_canon, away_canon) → line_no。"""
    by_match_id: Dict[str, int] = {}
    by_triplet: Dict[Tuple[str, str, str], int] = {}
    for idx in range(start, end):
        cells = _split_row(lines[idx])
        if not cells:
            continue
        date, _tm, home, _score, away, note = cells[0], cells[1], cells[2], cells[3], cells[4], cells[5]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue
        mid = _extract_matchid(note)
        if mid and mid not in by_match_id:
            by_match_id[mid] = idx
        home_c = normalize_team_name("world_cup", home, alias_map) or home
        away_c = normalize_team_name("world_cup", away, alias_map) or away
        by_triplet.setdefault((date, home_c, away_c), idx)
    return by_match_id, by_triplet


def apply_schedule_updates(
    teams_file: Path | str,
    records: Iterable[Any],
) -> Dict[str, Any]:
    """把 records（list[MatchRecord] 或等价 dict/obj）覆盖式写回 teams_2026.md。

    返回：{"updated_lines": int, "added_lines": int, "warnings": list[str]}。
    实现只更新既有行；`added_lines` 恒为 0（保留字段以便日后扩展）。
    """
    path = Path(teams_file)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=False)
    start, end = _find_schedule_range(lines)
    alias_map = load_team_alias_map()
    by_match_id, by_triplet = _index_rows(lines, start, end, alias_map)

    updated = 0
    warnings: List[str] = []

    def _rec_field(rec: Any, name: str, default: Any = None) -> Any:
        if isinstance(rec, dict):
            return rec.get(name, default)
        return getattr(rec, name, default)

    for rec in records or []:
        mid = str(_rec_field(rec, "match_id") or "").strip()
        date = str(_rec_field(rec, "date") or "").strip()[:10]
        home_raw = str(_rec_field(rec, "home") or "").strip()
        away_raw = str(_rec_field(rec, "away") or "").strip()
        if not mid or not date or not home_raw or not away_raw:
            warnings.append(f"skip record with missing fields: mid={mid} date={date} home={home_raw} away={away_raw}")
            continue
        tm = str(_rec_field(rec, "time") or "").strip()
        home = normalize_team_name("world_cup", home_raw, alias_map) or home_raw
        away = normalize_team_name("world_cup", away_raw, alias_map) or away_raw
        finished = bool(_rec_field(rec, "finished") or False)
        hs = _rec_field(rec, "home_score")
        as_ = _rec_field(rec, "away_score")

        target_idx = by_match_id.get(mid)
        if target_idx is None:
            target_idx = by_triplet.get((date, home, away))
        if target_idx is None:
            warnings.append(
                f"no matching schedule row for MatchID={mid} date={date} {home} vs {away}"
            )
            continue

        cells = _split_row(lines[target_idx])
        if not cells:
            warnings.append(f"row at line {target_idx + 1} is not parseable; skipped")
            continue

        old_date, old_time, old_home, old_score, old_away, old_note = cells[:6]
        # 比分：仅在原值空/`-` 且 record.finished 时覆盖
        new_score = old_score
        old_score_stripped = (old_score or "").strip()
        if _SCORE_RE.match(old_score_stripped or ""):
            new_score = old_score  # 已有真实比分，绝不动
        elif finished and hs is not None and as_ is not None:
            new_score = f"{int(hs)}-{int(as_)}"
        elif not old_score_stripped or old_score_stripped == "-":
            new_score = old_score if old_score_stripped else "-"

        new_note = _rewrite_note(old_note, mid)
        new_cells = [
            date,
            tm or old_time,
            home,
            new_score,
            away,
            new_note,
        ]
        if cells[:6] == new_cells:
            continue
        # 保留任何超过 6 列的额外单元格（当前 md 都是 6 列，这里防御）。
        rebuilt = new_cells + cells[6:]
        lines[target_idx] = _join_row(rebuilt)
        updated += 1

    if updated:
        new_text = "\n".join(lines)
        if text.endswith("\n") and not new_text.endswith("\n"):
            new_text += "\n"
        path.write_text(new_text, encoding="utf-8")

    return {"updated_lines": updated, "added_lines": 0, "warnings": warnings}


__all__ = ["apply_schedule_updates"]
