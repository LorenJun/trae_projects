"""模块说明：从预测结果按「方向 + 大小球」重算泊松比分（单一数据源）。

网页卡片与 MEMORY / teams_2026.md 写回共用本模块，确保三处比分完全一致。
比分按「盘口λ + 方向 + 大小球」三重约束在候选集内归一化，展示条件概率。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Tuple

DRAW_HINT_THRESHOLD = 0.30  # 平局概率达此值视为「有平局数据」，允许纳入平局比分

# 亚值让球口诀 → 强侧视角的「比分方向意图」。
# 印证类(强侧真赢) → 'fav'：比分方向取让球方(强侧)获胜。
# 诱导类(强侧不赢) → 'fade'：比分方向放行 平局 + 弱侧获胜两侧（用户选择「平局+弱侧都放行」）。
_VERDICT_DIR_INTENT: Dict[str, str] = {
    'block_up_home_genuine': 'fav',   # 升盘配高水+欧赔主降·阻上(主真赢)
    'lure_dog_no_point': 'fav',       # 降盘配下盘低水·诱客(客无分,主稳)
    'block_down_dog_hard': 'fav',     # 降盘配下盘高水·阻下(客难打出,主稳)
    'lure_up_home_fade': 'fade',      # 升盘配高水+欧赔主升·诱上(主难赢)
    'lure_up_hot_death': 'fade',      # 升盘配低水·诱上(大热必死)
    'protect_dog_genuine': 'fade',    # 降盘配下盘低水·防客(客拿分)
}


def _poisson_pmf(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _direction_handicap(d: Dict[str, Any]) -> Dict[str, Any]:
    """取出亚值让球口诀矩阵：优先 tri_axis_consistency.direction_handicap，
    回退 realtime.context_applied.market_operation_pattern.direction_handicap_matrix。
    """
    tri = d.get("tri_axis_consistency")
    if isinstance(tri, dict):
        dh = tri.get("direction_handicap")
        if isinstance(dh, dict) and dh.get("verdict_dir"):
            return dh
    realtime = d.get("realtime")
    if isinstance(realtime, dict):
        ctx = realtime.get("context_applied")
        if isinstance(ctx, dict):
            mop = ctx.get("market_operation_pattern")
            if isinstance(mop, dict):
                dh = mop.get("direction_handicap_matrix")
                if isinstance(dh, dict) and dh.get("verdict_dir"):
                    return dh
    return {}


def _model_direction(d: Dict[str, Any]) -> str:
    """模型 1X2 方向：主胜/客胜/平局；缺失时按概率推断。"""
    pred = (d.get("prediction") or d.get("predicted_winner") or "").strip()
    if pred in ("主胜", "客胜", "平局"):
        return pred
    probs = d.get("all_probabilities") or {}
    if probs:
        return max(("主胜", "平局", "客胜"), key=lambda k: probs.get(k) or 0.0)
    return ""


def direction_of(d: Dict[str, Any]) -> str:
    """比分方向：优先用亚值让球口诀(verdict_dir)判定，未触发时回退模型 1X2。

    - 印证类口诀(强侧真赢) → 让球方(fav_side)获胜方向；
    - 诱导类口诀(强侧不赢) → 取 平局 / 弱侧 中模型概率更高者为主方向（弱侧集合见 allowed_outcomes）。
    """
    dh = _direction_handicap(d)
    verdict_dir = dh.get("verdict_dir")
    intent = _VERDICT_DIR_INTENT.get(verdict_dir)
    fav_side = dh.get("fav_side")
    if intent and fav_side in ("home", "away"):
        if intent == "fav":
            return "主胜" if fav_side == "home" else "客胜"
        # fade：强侧不赢，主方向在 平局 / 弱侧 中取模型概率更高者
        dog = "客胜" if fav_side == "home" else "主胜"
        probs = d.get("all_probabilities") or {}
        return "平局" if (probs.get("平局") or 0.0) >= (probs.get(dog) or 0.0) else dog
    return _model_direction(d)


def allowed_outcomes(d: Dict[str, Any]) -> set[str]:
    """允许展示的胜负结果集合。

    优先按亚值口诀(verdict_dir)圈定方向：
    - 印证类 → 仅强侧获胜（爆冷达标时再放行反向+平局）；
    - 诱导类 → 平局 + 弱侧获胜两侧均放行。
    无口诀时回退模型 1X2 方向，并保留原有「平局概率达标并入平局 / 平局方向并入第二高方向 / 爆冷放行反向」逻辑。
    """
    probs = d.get("all_probabilities") or {}
    dh = _direction_handicap(d)
    intent = _VERDICT_DIR_INTENT.get(dh.get("verdict_dir"))
    fav_side = dh.get("fav_side")
    if intent and fav_side in ("home", "away"):
        fav_outcome = "主胜" if fav_side == "home" else "客胜"
        dog_outcome = "客胜" if fav_side == "home" else "主胜"
        if intent == "fav":
            allowed = {fav_outcome}
        else:  # fade：平局 + 弱侧都放行
            allowed = {"平局", dog_outcome}
        level = (d.get("upset_potential") or {}).get("level")
        if level in ("中", "高"):
            allowed.update({"主胜", "平局", "客胜"})
        return allowed

    direction = _model_direction(d)
    if direction not in ("主胜", "客胜", "平局"):
        return {"主胜", "平局", "客胜"}
    allowed = {direction}
    if (probs.get("平局") or 0.0) >= DRAW_HINT_THRESHOLD:
        allowed.add("平局")
    # 方向为平局时，平局比分（对角线）与大球同侧约束易冲突、塌缩到 2-2/3-3 等大比分；
    # 扩张到胜平负第二高方向的比分，给出更合理的候选。
    if direction == "平局":
        ranked = sorted(("主胜", "平局", "客胜"), key=lambda k: probs.get(k) or 0.0, reverse=True)
        for outcome in ranked:
            if outcome != "平局":
                allowed.add(outcome)
                break
    level = (d.get("upset_potential") or {}).get("level")
    if level in ("中", "高"):
        if direction == "主胜":
            allowed.update({"客胜", "平局"})
        elif direction == "客胜":
            allowed.update({"主胜", "平局"})
        else:
            allowed.update({"主胜", "客胜"})
    return allowed


def project_scores_for_side(d: Dict[str, Any]) -> List[Tuple[str, float]]:
    """按方向 + 大小球判定，从泊松比分网格中筛选比分。
    方向以预测胜负方为准：判主胜剔除客胜（反向爆冷）比分，判客胜剔除主胜比分，
    判平局只留平局比分；大小球同侧（判大球只出大球比分，反之）。
    例外：当模型提示爆冷（中/高）或平局概率达标时，按 allowed_outcomes 放行
    对应的平局 / 爆冷比分。无 λ 或无盘口线时回退到原始 top_scores（仍按允许集过滤）。
    """
    eg = d.get("expected_goals") or {}
    lam_h, lam_a = eg.get("home"), eg.get("away")
    ou = d.get("over_under") or {}
    line = ou.get("line")
    over_p, under_p = ou.get("over") or 0.0, ou.get("under") or 0.0
    side = "大" if over_p >= under_p else "小"
    allowed = allowed_outcomes(d)

    def _dir_ok(i: int, j: int) -> bool:
        outcome = "主胜" if i > j else ("客胜" if i < j else "平局")
        return outcome in allowed

    if lam_h is None or lam_a is None or line is None:
        fallback: List[Tuple[str, float]] = []
        for item in (d.get("top_scores") or [])[:6]:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                sc, p = str(item[0]), float(item[1])
            elif isinstance(item, dict):
                sc, p = str(item.get("score")), float(item.get("prob") or 0.0)
            else:
                continue
            m = re.match(r"^(\d+)-(\d+)$", sc)
            if m and not _dir_ok(int(m.group(1)), int(m.group(2))):
                continue
            fallback.append((sc, p))
        return fallback[:3]
    try:
        lf = float(line)
        lam_h, lam_a = float(lam_h), float(lam_a)
    except (TypeError, ValueError):
        return []

    def _build(apply_dir: bool, apply_ou: bool) -> List[Tuple[str, float]]:
        grid: List[Tuple[str, float]] = []
        for i in range(8):
            for j in range(8):
                total = i + j
                if apply_ou and side == "大" and total <= lf:
                    continue
                if apply_ou and side == "小" and total >= lf:
                    continue
                if apply_dir and not _dir_ok(i, j):
                    continue
                grid.append((f"{i}-{j}", _poisson_pmf(lam_h, i) * _poisson_pmf(lam_a, j)))
        if not grid:
            return []
        mass = sum(p for _, p in grid) or 1.0
        grid = [(sc, p / mass) for sc, p in grid]
        grid.sort(key=lambda x: x[1], reverse=True)
        return grid[:3]

    def _scores_of_outcome(outcome: str) -> List[Tuple[str, float]]:
        grid: List[Tuple[str, float]] = []
        for i in range(8):
            for j in range(8):
                oc = "主胜" if i > j else ("客胜" if i < j else "平局")
                if oc != outcome:
                    continue
                grid.append((f"{i}-{j}", _poisson_pmf(lam_h, i) * _poisson_pmf(lam_a, j)))
        if not grid:
            return []
        mass = sum(p for _, p in grid) or 1.0
        grid = [(sc, p / mass) for sc, p in grid]
        grid.sort(key=lambda x: x[1], reverse=True)
        return grid

    def _fallback_top_outcome() -> List[Tuple[str, float]]:
        probs = d.get("all_probabilities") or {}
        top = max(("主胜", "平局", "客胜"), key=lambda k: probs.get(k) or 0.0) if probs else "主胜"
        return _scores_of_outcome(top)

    primary = _build(True, True) or _build(True, False)
    if not primary:
        return _fallback_top_outcome()[:3]
    if len(primary) < 3:
        probs = d.get("all_probabilities") or {}
        allowed = allowed_outcomes(d)
        # 仅在方向允许集内补足，避免破坏亚值口诀圈定的比分方向。
        ranked = sorted(allowed, key=lambda k: probs.get(k) or 0.0, reverse=True)
        have = {sc for sc, _ in primary}
        for outcome in ranked:
            for sc, p in _scores_of_outcome(outcome):
                if sc not in have:
                    primary.append((sc, p))
                    have.add(sc)
                    break
            if len(primary) >= 3:
                break
    return primary[:3]
