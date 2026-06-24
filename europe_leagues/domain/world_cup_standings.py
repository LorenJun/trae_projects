"""模块说明：从世界杯 SoT（teams_2026.md）已回填比分重算各组积分榜并写回「小组积分榜」章节。

供赛果回填 / 复盘总结闭环调用，保证积分榜随赛果自动刷新，无需手工维护。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

PathLike = Union[str, Path]

STANDINGS_HEADER = "## 小组积分榜（截至已完赛，北京时间）"
STANDINGS_NOTE = "> 规则：胜 3 分 / 平 1 分 / 负 0 分；排序按 积分 → 净胜球 → 进球数。仅统计已回填比分的小组赛，未开赛场次不计入。"
GROUP_INFO_HEADER = "## 小组信息"
SCHEDULE_HEADER = "## 赛程信息"
GROUP_STAGE_HEADER = "### 小组赛"


def _parse_groups(lines: List[str]) -> Tuple["Dict[str, List[str]]", Dict[str, str]]:
    groups: Dict[str, List[str]] = {}
    team_group: Dict[str, str] = {}
    cur_group = None
    in_section = False
    for raw in lines:
        s = raw.strip()
        if s.startswith(GROUP_INFO_HEADER):
            in_section = True
            continue
        if in_section and s.startswith("## ") and not s.startswith(GROUP_INFO_HEADER):
            break
        if not in_section:
            continue
        m = re.match(r"^###\s+([A-L])组", s)
        if m:
            cur_group = m.group(1)
            groups[cur_group] = []
            continue
        if cur_group and s.startswith("|"):
            cols = [c.strip() for c in s.strip("|").split("|")]
            if len(cols) == 2 and cols[0].isdigit():
                team = cols[1]
                groups[cur_group].append(team)
                team_group[team] = cur_group
    return groups, team_group


def _parse_completed_matches(lines: List[str], team_group: Dict[str, str]) -> List[Tuple[str, int, str, int]]:
    matches: List[Tuple[str, int, str, int]] = []
    in_stage = False
    for raw in lines:
        s = raw.strip()
        if s.startswith(GROUP_STAGE_HEADER):
            in_stage = True
            continue
        if in_stage and s.startswith("### ") and not s.startswith(GROUP_STAGE_HEADER):
            break
        if not in_stage or not s.startswith("|"):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 5:
            continue
        home, score, away = cols[2], cols[3], cols[4]
        sm = re.match(r"^(\d+)\s*-\s*(\d+)$", score)
        if not sm:
            continue
        if home not in team_group or away not in team_group:
            continue
        matches.append((home, int(sm.group(1)), away, int(sm.group(2))))
    return matches


def _compute_stats(
    groups: Dict[str, List[str]],
    team_group: Dict[str, str],
    matches: List[Tuple[str, int, str, int]],
) -> Dict[str, Dict[str, int]]:
    stats = {t: {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "Pts": 0} for t in team_group}
    for home, hg, away, ag in matches:
        for t in (home, away):
            stats[t]["P"] += 1
        stats[home]["GF"] += hg
        stats[home]["GA"] += ag
        stats[away]["GF"] += ag
        stats[away]["GA"] += hg
        if hg > ag:
            stats[home]["W"] += 1
            stats[home]["Pts"] += 3
            stats[away]["L"] += 1
        elif hg < ag:
            stats[away]["W"] += 1
            stats[away]["Pts"] += 3
            stats[home]["L"] += 1
        else:
            stats[home]["D"] += 1
            stats[away]["D"] += 1
            stats[home]["Pts"] += 1
            stats[away]["Pts"] += 1
    return stats


def _render_section(groups: Dict[str, List[str]], stats: Dict[str, Dict[str, int]]) -> str:
    out: List[str] = [STANDINGS_HEADER, "", STANDINGS_NOTE, ""]
    for g, teams in groups.items():
        rows = []
        for t in teams:
            st = stats[t]
            gd = st["GF"] - st["GA"]
            rows.append((t, st, gd))
        rows.sort(key=lambda r: (r[1]["Pts"], r[2], r[1]["GF"]), reverse=True)
        out.append(f"### {g}组")
        out.append("| 排名 | 球队 | 赛 | 胜 | 平 | 负 | 进 | 失 | 净 | 积分 |")
        out.append("|-----|------|----|----|----|----|----|----|----|------|")
        for i, (t, st, gd) in enumerate(rows, 1):
            gd_s = f"+{gd}" if gd > 0 else str(gd)
            out.append(
                f"| {i} | {t} | {st['P']} | {st['W']} | {st['D']} | {st['L']} | "
                f"{st['GF']} | {st['GA']} | {gd_s} | {st['Pts']} |"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _replace_section(text: str, section: str) -> str:
    start = text.find(STANDINGS_HEADER)
    if start != -1:
        rest = text.find("\n## ", start + len(STANDINGS_HEADER))
        if rest == -1:
            return text[:start].rstrip() + "\n\n" + section + "\n"
        return text[:start].rstrip() + "\n\n" + section + "\n" + text[rest + 1 :]

    anchor = text.find(SCHEDULE_HEADER)
    if anchor == -1:
        return text.rstrip() + "\n\n" + section + "\n"
    return text[:anchor].rstrip() + "\n\n" + section + "\n" + text[anchor:]


def update_world_cup_standings(teams_file: PathLike) -> Dict[str, Any]:
    """重算并写回世界杯小组积分榜章节，返回统计摘要。"""
    path = Path(teams_file)
    if not path.exists():
        return {"available": False, "reason": "teams_file_not_found", "teams_file": str(path)}

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    groups, team_group = _parse_groups(lines)
    if not groups:
        return {"available": False, "reason": "no_groups_parsed", "teams_file": str(path)}

    matches = _parse_completed_matches(lines, team_group)
    stats = _compute_stats(groups, team_group, matches)
    section = _render_section(groups, stats)
    updated = _replace_section(text, section)

    written = updated != text
    if written:
        path.write_text(updated, encoding="utf-8")

    return {
        "available": True,
        "written": written,
        "teams_file": str(path),
        "groups": len(groups),
        "teams": len(team_group),
        "completed_matches": len(matches),
    }
