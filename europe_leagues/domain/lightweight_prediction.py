from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


COMMON_RUNTIME_LEAGUE_CODES = {
    "英冠": "championship",
    "葡超": "primeira_liga",
    "瑞超": "allsvenskan",
    "沙特联": "saudi_pro_league",
    "瑞典甲": "sweden_superettan",
}


def _normalize_team_text(name: str) -> str:
    text = str(name or "").strip().replace(" ", "")
    for suffix in ("足球俱乐部", "俱乐部", "足球", "队", "FC", "fc"):
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
    return text


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _normalize_triplet(home: float, draw: float, away: float) -> Dict[str, float]:
    total = max(home + draw + away, 1e-9)
    return {
        "home_win": home / total,
        "draw": draw / total,
        "away_win": away / total,
    }


def _normalized_implied_probs(home_odds: Any, draw_odds: Any, away_odds: Any) -> Dict[str, float]:
    home = _safe_float(home_odds)
    draw = _safe_float(draw_odds)
    away = _safe_float(away_odds)
    if not home or not draw or not away or min(home, draw, away) <= 1.0:
        return {"home_win": 0.3334, "draw": 0.3333, "away_win": 0.3333}
    return _normalize_triplet(1.0 / home, 1.0 / draw, 1.0 / away)


def _league_code_from_name(league_name: str, explicit_code: str = "") -> str:
    code = str(explicit_code or "").strip()
    if code:
        return code
    text = str(league_name or "").strip()
    return COMMON_RUNTIME_LEAGUE_CODES.get(text, "runtime_only")


def _label_probs(probabilities: Dict[str, float]) -> Dict[str, float]:
    return {
        "主胜": float(probabilities.get("home_win") or 0.0),
        "平局": float(probabilities.get("draw") or 0.0),
        "客胜": float(probabilities.get("away_win") or 0.0),
    }


def _apply_handicap_adjustment(
    probabilities: Dict[str, float],
    handicap_value: Optional[float],
    home_water: Optional[float],
    away_water: Optional[float],
) -> Dict[str, float]:
    home = float(probabilities.get("home_win") or 0.0)
    draw = float(probabilities.get("draw") or 0.0)
    away = float(probabilities.get("away_win") or 0.0)

    if handicap_value is None:
        return probabilities

    shift = 0.0
    if handicap_value <= -1.0:
        shift = 0.06
    elif handicap_value <= -0.75:
        shift = 0.045
    elif handicap_value <= -0.25:
        shift = 0.025
    elif handicap_value >= 1.0:
        shift = -0.06
    elif handicap_value >= 0.75:
        shift = -0.045
    elif handicap_value >= 0.25:
        shift = -0.025

    if shift > 0 and home_water is not None:
        if home_water <= 1.75:
            shift += 0.01
        elif home_water >= 2.05:
            shift -= 0.015
    elif shift < 0 and away_water is not None:
        if away_water <= 1.75:
            shift -= 0.01
        elif away_water >= 2.05:
            shift += 0.015

    if abs(shift) < 1e-9:
        return probabilities

    if shift > 0:
        home += shift
        draw = max(draw - shift * 0.45, 0.02)
        away = max(away - shift * 0.55, 0.02)
    else:
        away += abs(shift)
        draw = max(draw - abs(shift) * 0.45, 0.02)
        home = max(home - abs(shift) * 0.55, 0.02)
    return _normalize_triplet(home, draw, away)


def _build_over_under(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    market = snapshot.get("大小球") if isinstance(snapshot.get("大小球"), dict) else {}
    final_bucket = market.get("final") if isinstance(market.get("final"), dict) else {}
    over = _safe_float(final_bucket.get("over"))
    under = _safe_float(final_bucket.get("under"))
    line = _safe_float(final_bucket.get("line"))
    if not over or not under or line is None or over <= 1.0 or under <= 1.0:
        return {"available": False, "reason": "missing_real_market_line"}
    normalized = _normalize_triplet(1.0 / over, 0.0, 1.0 / under)
    return {
        "available": True,
        "line": line,
        "over": normalized["home_win"],
        "under": normalized["away_win"],
        "line_source": "snapshot_final",
        "market": {"final": {"over": over, "under": under, "line": line}},
    }


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _parse_score(score: str) -> tuple[int, int]:
    home, away = score.split("-", 1)
    return int(home), int(away)


def _build_score_context(
    *,
    probabilities: Dict[str, float],
    prediction: str,
    handicap_value: Optional[float],
    home_water: Optional[float],
    away_water: Optional[float],
    ou_line: Optional[float],
    over_prob: Optional[float],
    under_prob: Optional[float],
) -> Dict[str, float]:
    line = float(ou_line) if isinstance(ou_line, (int, float)) else 2.5
    home_prob = float(probabilities.get("home_win") or 0.0)
    draw_prob = float(probabilities.get("draw") or 0.0)
    away_prob = float(probabilities.get("away_win") or 0.0)
    over = float(over_prob if isinstance(over_prob, (int, float)) else 0.5)
    under = float(under_prob if isinstance(under_prob, (int, float)) else 0.5)
    handicap = float(handicap_value) if isinstance(handicap_value, (int, float)) else 0.0
    favorite_prob = home_prob if prediction == "主胜" else away_prob if prediction == "客胜" else draw_prob
    underdog_prob = away_prob if prediction == "主胜" else home_prob if prediction == "客胜" else max(home_prob, away_prob)
    probability_gap = favorite_prob - underdog_prob

    margin_target = 0.0
    if prediction == "主胜":
        margin_target = 0.95 + max(0.0, abs(min(handicap, 0.0))) * 0.85 + probability_gap * 2.0
        if home_water is not None:
            if home_water <= 1.8:
                margin_target += 0.18
            elif home_water >= 2.02:
                margin_target -= 0.16
    elif prediction == "客胜":
        margin_target = 0.95 + max(0.0, max(handicap, 0.0)) * 0.85 + probability_gap * 2.0
        if away_water is not None:
            if away_water <= 1.8:
                margin_target += 0.18
            elif away_water >= 2.02:
                margin_target -= 0.16
    else:
        margin_target = 0.0

    total_target = line
    total_target += (over - under) * 0.7
    if prediction == "平局":
        total_target -= min(0.45, draw_prob * 0.4)
    elif abs(probability_gap) >= 0.16:
        total_target += 0.18

    clean_sheet_bias = 0.0
    if prediction == "主胜":
        clean_sheet_bias = (under - over) * 0.85 + max(0.0, home_prob - away_prob) * 0.4
    elif prediction == "客胜":
        clean_sheet_bias = (under - over) * 0.85 + max(0.0, away_prob - home_prob) * 0.4
    else:
        clean_sheet_bias = (under - over) * 0.5

    return {
        "line": line,
        "home_prob": home_prob,
        "draw_prob": draw_prob,
        "away_prob": away_prob,
        "over_prob": over,
        "under_prob": under,
        "margin_target": margin_target,
        "total_target": total_target,
        "clean_sheet_bias": clean_sheet_bias,
        "probability_gap": probability_gap,
    }


def _candidate_scores_for_prediction(prediction: str, line: float) -> List[str]:
    if prediction == "主胜":
        base = ["1-0", "2-0", "2-1", "3-0", "3-1", "1-1", "0-0", "1-2"]
        if line >= 2.75:
            base = ["2-1", "2-0", "3-1", "1-0", "3-0", "1-1", "2-2", "1-2"]
        elif line <= 2.25:
            base = ["1-0", "2-0", "1-1", "2-1", "0-0", "3-0", "0-1", "1-2"]
        return base
    if prediction == "客胜":
        base = ["0-1", "0-2", "1-2", "0-3", "1-3", "1-1", "0-0", "2-1"]
        if line >= 2.75:
            base = ["1-2", "0-2", "1-3", "0-1", "0-3", "1-1", "2-2", "2-1"]
        elif line <= 2.25:
            base = ["0-1", "0-2", "1-1", "1-2", "0-0", "0-3", "1-0", "2-1"]
        return base
    if line >= 2.75:
        return ["1-1", "2-2", "1-2", "2-1", "0-0", "0-1", "1-0"]
    if line <= 2.25:
        return ["0-0", "1-1", "1-0", "0-1", "2-0", "0-2", "2-2"]
    return ["1-1", "0-0", "1-0", "0-1", "2-1", "1-2", "2-2"]


def _score_candidate(score: str, prediction: str, context: Dict[str, float]) -> float:
    home_goals, away_goals = _parse_score(score)
    total_goals = home_goals + away_goals
    margin = home_goals - away_goals
    line = context["line"]
    total_target = context["total_target"]
    margin_target = context["margin_target"]
    clean_sheet_bias = context["clean_sheet_bias"]
    draw_prob = context["draw_prob"]
    over_prob = context["over_prob"]
    under_prob = context["under_prob"]

    if prediction == "主胜" and margin <= 0:
        return -999.0
    if prediction == "客胜" and margin >= 0:
        return -999.0
    if prediction == "平局" and margin != 0:
        return -999.0

    score_value = 1.0
    score_value -= abs(total_goals - total_target) * 0.85

    if prediction == "平局":
        draw_total_target = 1.1 + _clamp(line - 2.25, -0.4, 0.7) + (over_prob - under_prob) * 0.45
        score_value -= abs(total_goals - draw_total_target) * 0.65
        if score == "1-1":
            score_value += 0.25 + draw_prob * 0.15
        if score == "0-0":
            score_value += max(0.0, under_prob - 0.5) * 0.45
        if score == "2-2":
            score_value += max(0.0, over_prob - 0.5) * 0.4
        return score_value

    directional_margin = margin if prediction == "主胜" else -margin
    score_value -= abs(directional_margin - margin_target) * 0.75

    clean_sheet = (home_goals == 0 or away_goals == 0)
    if clean_sheet:
        score_value += clean_sheet_bias * 0.45
    else:
        score_value -= clean_sheet_bias * 0.2

    if prediction == "主胜":
        if score in ("1-0", "2-0") and under_prob >= over_prob:
            score_value += 0.14
        if score in ("2-1", "3-1") and over_prob > under_prob:
            score_value += 0.16
        if margin >= 2 and margin_target < 1.35:
            score_value -= 0.15
        if total_goals >= 4 and line <= 2.5:
            score_value -= 0.16
    else:
        if score in ("0-1", "0-2") and under_prob >= over_prob:
            score_value += 0.14
        if score in ("1-2", "1-3") and over_prob > under_prob:
            score_value += 0.16
        if directional_margin >= 2 and margin_target < 1.35:
            score_value -= 0.15
        if total_goals >= 4 and line <= 2.5:
            score_value -= 0.16

    return score_value


def _apply_under_three_score_penalty(
    ranked_scores: List[tuple[str, float]],
    line: float,
    over_prob: Optional[float],
    under_prob: Optional[float],
) -> tuple[List[tuple[str, float]], Dict[str, Any]]:
    diag: Dict[str, Any] = {
        "applied": False,
        "reason": "guard_not_triggered",
        "line": round(float(line), 4),
        "over": _safe_float(over_prob),
        "under": _safe_float(under_prob),
        "penalties": {},
    }
    over = _safe_float(over_prob)
    under = _safe_float(under_prob)
    if float(line) > 3.0 or over is None or under is None or under <= over:
        diag["reason"] = "not_under_three"
        return ranked_scores, diag

    if float(line) <= 2.5:
        factors = {"3-1": 0.64, "2-2": 0.74}
        diag["line_bucket"] = "<=2.5"
    elif float(line) <= 2.75:
        factors = {"3-1": 0.72, "2-2": 0.8}
        diag["line_bucket"] = "<=2.75"
    else:
        factors = {"3-1": 0.72, "2-2": 0.86}
        diag["line_bucket"] = "<=3.0"
    adjusted = dict(ranked_scores)
    penalties: Dict[str, float] = {}
    diag["factors"] = factors
    for score, factor in factors.items():
        if score not in adjusted:
            continue
        original = float(adjusted[score])
        if original <= 0:
            continue
        adjusted[score] = original * factor
        penalties[score] = round(original - adjusted[score], 6)
    reranked = sorted(adjusted.items(), key=lambda item: item[1], reverse=True)
    if penalties:
        diag["applied"] = True
        diag["reason"] = "under_three_score_penalty"
        diag["signals"] = ["under3-score-consistency-guard"]
        diag["penalties"] = penalties
    else:
        diag["reason"] = "target_scores_missing"
    return reranked, diag


def _pick_top_scores(
    prediction: str,
    *,
    probabilities: Dict[str, float],
    handicap_value: Optional[float],
    home_water: Optional[float],
    away_water: Optional[float],
    ou_line: Optional[float],
    over_prob: Optional[float],
    under_prob: Optional[float],
    include_diag: bool = False,
) -> Any:
    line = float(ou_line) if isinstance(ou_line, (int, float)) else 2.5
    context = _build_score_context(
        probabilities=probabilities,
        prediction=prediction,
        handicap_value=handicap_value,
        home_water=home_water,
        away_water=away_water,
        ou_line=line,
        over_prob=over_prob,
        under_prob=under_prob,
    )
    ranked_scores = sorted(
        (
            (score, _score_candidate(score, prediction, context))
            for score in _candidate_scores_for_prediction(prediction, line)
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    ranked_scores, score_guard_diag = _apply_under_three_score_penalty(
        ranked_scores,
        line,
        over_prob,
        under_prob,
    )
    ranked_scores = ranked_scores[:3]
    top_score = ranked_scores[0][1] if ranked_scores else 1.0
    results: List[List[Any]] = []
    for index, (score, value) in enumerate(ranked_scores):
        confidence = 0.2 - index * 0.025 - max(0.0, top_score - value) * 0.03
        results.append([score, round(_clamp(confidence, 0.08, 0.24), 4)])
    if include_diag:
        return results, score_guard_diag
    return results


def _favorite_from_probs(probabilities: Dict[str, float]) -> str:
    ranked = sorted(_label_probs(probabilities).items(), key=lambda item: item[1], reverse=True)
    return ranked[0][0] if ranked else "平局"


def _build_risk_summary(
    snapshot: Dict[str, Any],
    probabilities: Dict[str, float],
) -> Dict[str, Any]:
    signals: List[str] = []
    euro = snapshot.get("欧赔") if isinstance(snapshot.get("欧赔"), dict) else {}
    asian = snapshot.get("亚值") if isinstance(snapshot.get("亚值"), dict) else {}
    totals = snapshot.get("大小球") if isinstance(snapshot.get("大小球"), dict) else {}

    euro_initial = euro.get("initial") if isinstance(euro.get("initial"), dict) else {}
    euro_final = euro.get("final") if isinstance(euro.get("final"), dict) else {}
    asian_initial = asian.get("initial") if isinstance(asian.get("initial"), dict) else {}
    asian_final = asian.get("final") if isinstance(asian.get("final"), dict) else {}
    totals_initial = totals.get("initial") if isinstance(totals.get("initial"), dict) else {}
    totals_final = totals.get("final") if isinstance(totals.get("final"), dict) else {}

    home_initial = _safe_float(euro_initial.get("home"))
    away_initial = _safe_float(euro_initial.get("away"))
    home_final = _safe_float(euro_final.get("home"))
    away_final = _safe_float(euro_final.get("away"))
    draw_final = _safe_float(euro_final.get("draw"))
    asian_init_line = _safe_float(asian_initial.get("handicap_value"))
    asian_final_line = _safe_float(asian_final.get("handicap_value"))
    home_water = _safe_float(asian_final.get("home_water"))
    away_water = _safe_float(asian_final.get("away_water"))
    totals_init_line = _safe_float(totals_initial.get("line"))
    totals_final_line = _safe_float(totals_final.get("line"))

    predicted = _favorite_from_probs(probabilities)
    asian_favorite = "平局"
    if asian_final_line is not None:
        if asian_final_line < 0:
            asian_favorite = "主胜"
        elif asian_final_line > 0:
            asian_favorite = "客胜"

    if predicted != asian_favorite and asian_favorite != "平局":
        signals.append("三盘口背离，提升平局与冷门容错")

    if asian_init_line is not None and asian_final_line is not None and abs(asian_final_line) < abs(asian_init_line):
        signals.append("盘口退盘，热门方向稳定性下降")

    if asian_init_line is not None and asian_final_line is not None and abs(asian_final_line) > abs(asian_init_line):
        if asian_final_line < 0 and home_water is not None and home_water >= 2.0:
            signals.append("升盘配高水，主队穿盘阻力偏大")
        elif asian_final_line > 0 and away_water is not None and away_water >= 2.0:
            signals.append("升盘配高水，客队穿盘阻力偏大")

    if home_initial and away_initial and home_final and away_final:
        if home_final > home_initial and away_final < away_initial:
            signals.append("欧赔走弱主队，防平或客队不败")
        elif home_final < home_initial and away_final > away_initial:
            signals.append("欧赔强化主队，但需防热度集中")

    if draw_final is not None and draw_final <= 3.15:
        signals.append("平赔偏低，需保留平局容错")

    if totals_init_line is not None and totals_final_line is not None and totals_final_line < totals_init_line:
        signals.append("进球线下修，比赛更偏低比分")

    ranked = sorted(_label_probs(probabilities).values(), reverse=True)
    top = ranked[0] if ranked else 0.34
    second = ranked[1] if len(ranked) > 1 else 0.33
    gap = top - second
    index = min(85, int(round(max(8.0, len(signals) * 14 + max(0.0, 0.12 - gap) * 220))))
    level = "低"
    if index >= 55:
        level = "高"
    elif index >= 28:
        level = "中"
    return {"level": level, "index": index, "factors": signals[:3]}


def build_lightweight_prediction_result(
    *,
    snapshot: Dict[str, Any],
    league_name: str,
    league_code: str = "",
    home_team: str,
    away_team: str,
    match_date: str,
    match_time: str = "",
    match_id: str = "",
) -> Dict[str, Any]:
    euro = snapshot.get("欧赔") if isinstance(snapshot.get("欧赔"), dict) else {}
    asian = snapshot.get("亚值") if isinstance(snapshot.get("亚值"), dict) else {}
    euro_final = euro.get("final") if isinstance(euro.get("final"), dict) else {}
    asian_final = asian.get("final") if isinstance(asian.get("final"), dict) else {}

    probabilities = _normalized_implied_probs(
        euro_final.get("home"),
        euro_final.get("draw"),
        euro_final.get("away"),
    )
    probabilities = _apply_handicap_adjustment(
        probabilities,
        _safe_float(asian_final.get("handicap_value")),
        _safe_float(asian_final.get("home_water")),
        _safe_float(asian_final.get("away_water")),
    )
    label_probs = _label_probs(probabilities)
    prediction = max(label_probs.items(), key=lambda item: item[1])[0]
    confidence = float(label_probs.get(prediction) or 0.0)

    over_under = _build_over_under(snapshot)
    ou_line = over_under.get("line") if over_under.get("available") else None
    over_prob = over_under.get("over") if over_under.get("available") else None
    under_prob = over_under.get("under") if over_under.get("available") else None
    handicap_value = _safe_float(asian_final.get("handicap_value"))
    home_water = _safe_float(asian_final.get("home_water"))
    away_water = _safe_float(asian_final.get("away_water"))
    top_scores, score_rerank_guard = _pick_top_scores(
        prediction,
        probabilities=probabilities,
        handicap_value=handicap_value,
        home_water=home_water,
        away_water=away_water,
        ou_line=_safe_float(ou_line),
        over_prob=_safe_float(over_prob),
        under_prob=_safe_float(under_prob),
        include_diag=True,
    )
    risk = _build_risk_summary(snapshot, probabilities)
    league_code = _league_code_from_name(league_name, league_code)

    return {
        "league_code": league_code,
        "league_name": league_name,
        "home_team": home_team,
        "away_team": away_team,
        "match_date": match_date,
        "match_time": match_time,
        "match_id": str(match_id or snapshot.get("match_id") or "").strip(),
        "external_match_id": str(match_id or snapshot.get("match_id") or "").strip(),
        "storage_mode": "runtime_only",
        "prediction": prediction,
        "predicted_winner": {"主胜": "home", "平局": "draw", "客胜": "away"}.get(prediction, "draw"),
        "confidence": confidence,
        "final_probabilities": probabilities,
        "all_probabilities": label_probs,
        "top_scores": top_scores,
        "predicted_scores": [item[0] for item in top_scores],
        "over_under": over_under,
        "score_rerank_guard": score_rerank_guard,
        "market_snapshot": {
            "欧赔": snapshot.get("欧赔") or {},
            "亚值": snapshot.get("亚值") or {},
            "大小球": snapshot.get("大小球") or {},
            "凯利": snapshot.get("凯利") or {},
        },
        "upset_potential": risk,
        "retrieved_memory_explanation": "轻量模式：基于澳客欧赔、亚值与大小球快照生成单场预测记录，仅写入滚动记忆，不进入正式联赛主归档。",
        "runtime_profile": {"mode": "lightweight_market_snapshot"},
    }


def capture_okooo_snapshot(
    *,
    base_dir: str,
    league_name: str,
    home_team: str,
    away_team: str,
    match_date: str,
    match_time: str = "",
    match_id: str = "",
    okooo_driver: str = "local-chrome",
    okooo_headed: bool = False,
) -> Dict[str, Any]:
    resolved_match_id = str(match_id or "").strip()
    if not resolved_match_id:
        from okooo_match_finder import OkoooMatchFinder

        resolved_match_id = str(OkoooMatchFinder().find_match_id(home_team, away_team, league_name) or "").strip()
        if not resolved_match_id:
            raise RuntimeError(f"未能定位 MatchID: {league_name} {home_team} vs {away_team}")

    script_path = Path(base_dir) / "okooo_save_snapshot.py"
    out_dir = Path(base_dir) / ".okooo-scraper" / "snapshots" / "runtime_only"
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(script_path),
        "--driver",
        okooo_driver,
        "--league",
        league_name,
        "--team1",
        home_team,
        "--team2",
        away_team,
        "--date",
        match_date,
        "--out-dir",
        str(out_dir),
        "--overwrite",
    ]
    if okooo_headed:
        command.append("--headed")
    if match_time:
        command.extend(["--time", match_time])
    command.extend(["--match-id", resolved_match_id])

    completed = subprocess.run(command, capture_output=True, text=True, check=True, cwd=base_dir)
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    snapshot_path = None
    for line in reversed(output_lines):
        candidate = Path(line)
        if candidate.exists():
            snapshot_path = candidate
            break
    if snapshot_path is None:
        raise RuntimeError(f"未找到快照落盘路径: {completed.stdout.strip() or completed.stderr.strip()}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    expected_home = _normalize_team_text(home_team)
    expected_away = _normalize_team_text(away_team)
    actual_home = _normalize_team_text(snapshot.get("home_team"))
    actual_away = _normalize_team_text(snapshot.get("away_team"))
    if expected_home and expected_away and (
        (expected_home not in actual_home and actual_home not in expected_home)
        or (expected_away not in actual_away and actual_away not in expected_away)
    ):
        raise RuntimeError(
            "快照主客队校验失败: "
            f"expect={home_team} vs {away_team}, actual={snapshot.get('home_team')} vs {snapshot.get('away_team')}"
        )
    return snapshot


def predict_lightweight_match(
    *,
    base_dir: str,
    league_name: str,
    league_code: str,
    home_team: str,
    away_team: str,
    match_date: str,
    match_time: str = "",
    match_id: str = "",
    okooo_driver: str = "local-chrome",
    okooo_headed: bool = False,
) -> Dict[str, Any]:
    snapshot = capture_okooo_snapshot(
        base_dir=base_dir,
        league_name=league_name,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date,
        match_time=match_time,
        match_id=match_id,
        okooo_driver=okooo_driver,
        okooo_headed=okooo_headed,
    )
    return build_lightweight_prediction_result(
        snapshot=snapshot,
        league_name=league_name,
        league_code=league_code,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date,
        match_time=match_time,
        match_id=str(match_id or snapshot.get("match_id") or "").strip(),
    )
