"""模块说明：世界杯小组赛"出线情景 / 动机扭曲"识别层。

目的：
  - 世界杯小组赛末轮常出现"已出线躺平、控分避强敌、双方平局即晋级（默契球）"等
    动机扭曲——常规盘口/模型在这种场次会系统性失真。
  - 本模块只做两件事：(1) 从 teams_2026.md 已写回的小组赛赛果聚合出小组积分与出线形势；
    (2) 对某场具体比赛判定它是否处在"动机扭曲窗口"，并给出对冲建议（降置信/降仓/大小球转中性）。

关键纪律：
  - 只识别"情景"，绝不预测剧本往哪演（不输出方向）。
  - 只在末轮（双方均已踢满 2 场、即将踢第 3 场）或小组已完赛时才可能判定动机扭曲；
    揭幕轮/次轮一律视为常规（normal），不降级。

赛制（FIFA World Cup 2026）：
  - 48 队 12 组每组 4 队；每组前 2 名 + 8 个成绩最好的第三名晋级 32 强淘汰赛。

已知局限：
  - 出线判定使用积分 + 净胜球 + 进球的常规排序，不建模 head-to-head / 公平竞赛分 / 抽签等
    极端 tiebreaker；"最佳第三名"在小组未完赛时用保守的可达性估计。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# 队名归一：处理小组信息表与赛程表可能出现的写法差异。
_TEAM_ALIASES = {
    '刚果(金)': '刚果民主共和国',
    '刚果金': '刚果民主共和国',
    '刚果（金）': '刚果民主共和国',
}


def _normalize_team(name: str) -> str:
    name = (name or '').strip()
    return _TEAM_ALIASES.get(name, name)


@dataclass
class TeamStat:
    team: str
    group: str = ''
    played: int = 0
    won: int = 0
    draw: int = 0
    lost: int = 0
    gf: int = 0
    ga: int = 0
    rank: int = 0  # 组内名次（1-4），完赛或可计算时填充

    @property
    def gd(self) -> int:
        return self.gf - self.ga

    @property
    def pts(self) -> int:
        return self.won * 3 + self.draw

    @property
    def max_possible_pts(self) -> int:
        return self.pts + 3 * max(0, 3 - self.played)


@dataclass
class StakesScenario:
    distortion: bool = False
    type: str = 'normal'
    summary: str = ''
    confidence_penalty: float = 0.0  # 加到 confidence 上的负值
    stake_scale: float = 1.0         # 乘到凯利仓位上的系数 (0~1)
    ou_neutral: bool = False         # 是否把大小球单边降级为"中性/不建议"
    matchday: int = 0
    home_status: str = ''
    away_status: str = ''
    diagnostics: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'distortion': self.distortion,
            'type': self.type,
            'summary': self.summary,
            'confidence_penalty': round(float(self.confidence_penalty), 4),
            'stake_scale': round(float(self.stake_scale), 4),
            'ou_neutral': self.ou_neutral,
            'matchday': self.matchday,
            'home_status': self.home_status,
            'away_status': self.away_status,
            'diagnostics': self.diagnostics,
        }


# ---------------------------------------------------------------------------
# 解析层：球队→小组映射 + 小组赛赛果聚合
# ---------------------------------------------------------------------------

def parse_group_map(md_text: str) -> Dict[str, str]:
    """从 `## 小组信息` 段解析 球队 -> 组字母(A~L)。"""
    group_map: Dict[str, str] = {}
    current_group: Optional[str] = None
    in_section = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('## '):
            in_section = stripped.startswith('## 小组信息')
            current_group = None
            continue
        if not in_section:
            continue
        m = re.match(r'^###\s*([A-L])组', stripped)
        if m:
            current_group = m.group(1)
            continue
        if current_group and stripped.startswith('|'):
            cols = [c.strip() for c in stripped.strip('|').split('|')]
            if len(cols) == 2 and cols[0] not in ('序号', '') and not set(cols[0]) <= {'-'}:
                team = _normalize_team(cols[1])
                if team and team != '球队':
                    group_map[team] = current_group
    return group_map


def _iter_group_stage_result_rows(md_text: str):
    """只遍历 `### 小组赛` 段内的赛程行，产出 (home, away, hs, as_)。"""
    score_re = re.compile(r'^\s*(\d+)\s*-\s*(\d+)\s*$')
    in_group_stage = False
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith('### '):
            in_group_stage = stripped.startswith('### 小组赛')
            continue
        if not in_group_stage or not stripped.startswith('|'):
            continue
        cols = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cols) != 6:
            continue
        _date, _time, home, score, away, _note = cols
        m = score_re.match(score or '')
        if not m:
            continue
        yield _normalize_team(home), _normalize_team(away), int(m.group(1)), int(m.group(2))


def compute_group_standings(
    md_text: str,
    exclude_pair: Optional[Tuple[str, str]] = None,
) -> Dict[str, List[TeamStat]]:
    """聚合每组积分并排序。返回 {组字母: [TeamStat 按名次排序]}。

    exclude_pair: 若给定 (队甲, 队乙)，则不把这两队之间的那场结果计入积分。
      用于"对某场比赛做情景判定"时排除该场自身（无论它在 md 里是否已回填比分），
      以保证 played 计数反映的是该场赛前状态。
    """
    group_map = parse_group_map(md_text)
    stats: Dict[str, TeamStat] = {
        team: TeamStat(team=team, group=grp) for team, grp in group_map.items()
    }
    excluded = None
    if exclude_pair:
        excluded = frozenset(_normalize_team(t) for t in exclude_pair)

    def get(team: str) -> Optional[TeamStat]:
        return stats.get(team)

    for home, away, hs, as_ in _iter_group_stage_result_rows(md_text):
        h, a = get(home), get(away)
        if h is None or a is None or h.group != a.group:
            continue  # 跳过未知队名或非同组（淘汰赛/数据异常）
        if excluded is not None and {home, away} == set(excluded):
            continue  # 排除待判定的本场比赛
        h.played += 1
        a.played += 1
        h.gf += hs
        h.ga += as_
        a.gf += as_
        a.ga += hs
        if hs > as_:
            h.won += 1
            a.lost += 1
        elif hs < as_:
            a.won += 1
            h.lost += 1
        else:
            h.draw += 1
            a.draw += 1

    groups: Dict[str, List[TeamStat]] = {}
    for st in stats.values():
        groups.setdefault(st.group, []).append(st)
    for grp, rows in groups.items():
        rows.sort(key=lambda s: (-s.pts, -s.gd, -s.gf, s.team))
        for pos, s in enumerate(rows, start=1):
            s.rank = pos
    return groups


# ---------------------------------------------------------------------------
# 出线判定（含 8 个最佳第三名）
# ---------------------------------------------------------------------------

def _best_third_qualifiers(groups: Dict[str, List[TeamStat]]) -> Tuple[List[str], bool]:
    """仅当全部 12 组都已完赛时，精确取 8 个最佳第三名。

    返回 (出线的第三名球队列表, all_complete)。未完赛时 all_complete=False。
    """
    thirds: List[TeamStat] = []
    all_complete = len(groups) == 12 and all(
        len(rows) == 4 and all(s.played == 3 for s in rows) for rows in groups.values()
    )
    for rows in groups.values():
        if len(rows) >= 3:
            thirds.append(rows[2])
    thirds.sort(key=lambda s: (-s.pts, -s.gd, -s.gf, s.team))
    qualified = [s.team for s in thirds[:8]]
    return qualified, all_complete


def compute_qualification(groups: Dict[str, List[TeamStat]]) -> Dict[str, str]:
    """每队出线状态：'已出线' / '已出局' / '争夺中'。

    - 小组全部完赛：前2 + 8最佳第三名=已出线，其余=已出局。
    - 未完赛：用可达性保守判定（数学上锁定才标 已出线/已出局，否则 争夺中）。
    """
    status: Dict[str, str] = {}
    best_thirds, all_complete = _best_third_qualifiers(groups)

    for rows in groups.values():
        for s in rows:
            if all_complete:
                if s.rank <= 2 or s.team in best_thirds:
                    status[s.team] = '已出线'
                else:
                    status[s.team] = '已出局'
                continue
            status[s.team] = _provisional_status(s, rows)
    return status


def _provisional_status(team: TeamStat, group_rows: List[TeamStat]) -> str:
    """小组未完赛时的保守出线判定（只标数学锁定的，否则争夺中）。

    2026 世界杯是 12 个小组前二 + 8 个成绩最好的第三名晋级 32 强。
    因此在全部小组第三名排序尚未尘埃落定前，不能因为某队已经无缘小组前二，
    就把它标成「已出局」；只要它仍可能拿到组内第三，就仍保留「争夺中」。
    """
    others = [s for s in group_rows if s.team != team.team]

    # 本组已踢完但其他组未完赛：前二已锁定出线，第四已确定出局，第三仍需等待
    # 12 个小组第三名横向比较，不能提前判死。
    if team.played >= 3:
        if team.rank <= 2:
            return '已出线'
        if team.rank >= 4:
            return '已出局'
        return '争夺中'

    # 保证前二：组内至多 1 队的"理论最高分"能超过本队当前分。
    can_surpass = sum(1 for o in others if o.max_possible_pts > team.pts)
    if can_surpass <= 1:
        return '已出线'

    # 保守出局：本队理论最高分仍低于组内已有 3 队的当前分，连组内第三都拿不到。
    # 若只是无缘前二但仍可拿第三，按 2026 赛制仍有争夺最佳第三名的机会，不能标出局。
    ahead_fixed = sum(1 for o in others if o.pts > team.max_possible_pts)
    if ahead_fixed >= 3:
        return '已出局'

    return '争夺中'


# ---------------------------------------------------------------------------
# 情景分类（主入口）
# ---------------------------------------------------------------------------

_SCENARIO_PROFILE = {
    # type: (confidence_penalty, stake_scale, ou_neutral, summary)
    'dead_rubber_both': (-0.08, 0.0, True, '双方均已出线/出局·荣誉战，常规盘口失真'),
    'mutual_draw_advances': (-0.08, 0.0, True, '双方平局即晋级·默契球风险，方向不可信'),
    'one_side_secured': (-0.05, 0.4, True, '一方已锁定出线·可能轮换保留，盘口失真'),
    'qualified_vs_eliminated': (-0.06, 0.25, True, '一方已出线一方已出局·双方均无强动机'),
    'one_eliminated': (-0.04, 0.5, False, '一方已出局·该方战意不确定'),
}


def classify_stakes_scenario(
    home: str,
    away: str,
    groups: Dict[str, List[TeamStat]],
    status: Dict[str, str],
) -> StakesScenario:
    """判定一场小组赛是否处于动机扭曲窗口。

    仅在末轮（双方均已踢 2 场、即将踢第 3 场）触发动机扭曲；其余一律 normal。
    """
    home = _normalize_team(home)
    away = _normalize_team(away)
    h_stat = _find_stat(home, groups)
    a_stat = _find_stat(away, groups)

    scenario = StakesScenario()
    if h_stat is None or a_stat is None or h_stat.group != a_stat.group:
        scenario.summary = '非同组或缺小组数据，按常规处理'
        return scenario

    matchday = max(h_stat.played, a_stat.played) + 1
    scenario.matchday = matchday
    scenario.home_status = status.get(home, '争夺中')
    scenario.away_status = status.get(away, '争夺中')

    # 只在末轮（即将踢第 3 场）才识别动机扭曲。
    if not (h_stat.played == 2 and a_stat.played == 2):
        scenario.summary = f'第{matchday}轮·非末轮，按常规处理'
        return scenario

    h_done = scenario.home_status in ('已出线', '已出局')
    a_done = scenario.away_status in ('已出线', '已出局')

    scenario_type: Optional[str] = None
    if scenario.home_status == '已出线' and scenario.away_status == '已出线':
        scenario_type = 'dead_rubber_both'
    elif scenario.home_status == '已出局' and scenario.away_status == '已出局':
        scenario_type = 'dead_rubber_both'
    elif {scenario.home_status, scenario.away_status} == {'已出线', '已出局'}:
        scenario_type = 'qualified_vs_eliminated'
    elif h_done or a_done:
        # 一方已出线/出局，另一方仍在争夺。
        if '已出线' in (scenario.home_status, scenario.away_status):
            scenario_type = 'one_side_secured'
        else:
            scenario_type = 'one_eliminated'
    elif _mutual_draw_advances(h_stat, a_stat, groups):
        scenario_type = 'mutual_draw_advances'

    if scenario_type is None:
        scenario.summary = '末轮生死战·双方均有出线动机，正常信模型'
        return scenario

    penalty, stake_scale, ou_neutral, summary = _SCENARIO_PROFILE[scenario_type]
    scenario.distortion = True
    scenario.type = scenario_type
    scenario.confidence_penalty = penalty
    scenario.stake_scale = stake_scale
    scenario.ou_neutral = ou_neutral
    scenario.summary = summary
    return scenario


def _find_stat(team: str, groups: Dict[str, List[TeamStat]]) -> Optional[TeamStat]:
    for rows in groups.values():
        for s in rows:
            if s.team == team:
                return s
    return None


def _mutual_draw_advances(h: TeamStat, a: TeamStat, groups: Dict[str, List[TeamStat]]) -> bool:
    """末轮两队相互对阵时，"打平则双方都能晋级（前二）"的默契球风险粗判。

    保守：若两队当前都已 >=4 分（打平后各 5 分），且组内另两队最高也追不上其中较低者，
    则认为打平大概率双双晋级。
    """
    group_rows = groups.get(h.group, [])
    others = [s for s in group_rows if s.team not in (h.team, a.team)]
    if len(others) != 2:
        return False
    draw_pts_h = h.pts + 1
    draw_pts_a = a.pts + 1
    lower_draw = min(draw_pts_h, draw_pts_a)
    # 另两队的理论最高分都 < 平局后较低者 → 打平铁定双双前二。
    return all(o.max_possible_pts < lower_draw for o in others)


def assess_stakes_scenario(
    home: str,
    away: str,
    teams_md_path: Optional[str] = None,
    md_text: Optional[str] = None,
) -> Dict:
    """对外主入口：给定主客队，返回情景判定 dict。

    md_text 优先；否则从 teams_md_path 读取。任一缺失/异常都安全返回 normal。
    """
    try:
        if md_text is None:
            if not teams_md_path or not Path(teams_md_path).exists():
                return StakesScenario(summary='无 teams md，按常规处理').to_dict()
            md_text = Path(teams_md_path).read_text(encoding='utf-8')
        groups = compute_group_standings(md_text, exclude_pair=(home, away))
        status = compute_qualification(groups)
        return classify_stakes_scenario(home, away, groups, status).to_dict()
    except Exception as exc:  # 任何解析异常都不能影响主预测
        return StakesScenario(summary=f'情景判定异常，按常规处理: {exc}').to_dict()
