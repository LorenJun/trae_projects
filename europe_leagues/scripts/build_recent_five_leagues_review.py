from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.rag_store import DOMESTIC_LEAGUE_CODES, LEAGUE_CN_NAMES, rag_cases_path

LEAGUE_ORDER = ["premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1"]
TOP_PROBLEM_GROUP_LABELS = {
    "coverage": "覆盖",
    "direction": "方向",
    "score": "比分",
    "market": "盘口",
}
CALIBRATION_RULE_LABELS = {
    "strong_home_shallow_line": "主让浅盘但实力差大",
    "away_shallow_market_doubt": "客让浅盘但市场疑虑高",
    "balanced_draw_guard": "均势盘平局漏防",
    "draw_market_balance": "平局方向市场均衡",
}
CALIBRATION_MISS_REASON_LABELS = {
    "missing_strength_diff": "缺实力差数据",
    "missing_asian_line": "缺亚盘数据",
    "missing_euro_odds": "缺欧赔数据",
    "prediction_draw_not_supported": "预测为平局暂不适用",
    "handicap_not_shallow_or_level": "盘口不属于浅盘或均势盘",
    "strength_not_large_enough": "实力差未达到强弱阈值",
    "strength_not_balanced_enough": "实力差未落入均势阈值",
    "euro_not_doubtful_enough": "欧赔未体现市场疑虑",
}


def _safe_json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_auto_remediation_advice(
    rule_rows: List[Dict[str, Any]],
    miss_rows: List[Dict[str, Any]],
    score_rows: Optional[List[Dict[str, Any]]] = None,
    ou_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    advice: List[str] = []
    miss_count_by_id = {str(item.get("reason_id") or ""): int(item.get("count") or 0) for item in miss_rows}
    rule_by_id = {str(item.get("rule_id") or ""): item for item in rule_rows}
    score_by_id = {str(item.get("rule_id") or ""): item for item in (score_rows or [])}
    ou_by_id = {str(item.get("rule_id") or ""): item for item in (ou_rows or [])}

    if miss_count_by_id.get("missing_strength_diff", 0) >= 1:
        advice.append("优先补齐 `prediction_archive.json` 中的 `strength_diff` 留存，否则强弱差分层无法稳定触发。")
    if miss_count_by_id.get("missing_asian_line", 0) >= 1:
        advice.append("优先补齐亚盘快照留存，确保 `asian_line` 能稳定进入复盘与分层校准。")
    if miss_count_by_id.get("missing_euro_odds", 0) >= 1:
        advice.append("优先补齐欧赔终盘留存，避免“市场疑虑高”类规则长期失明。")
    if miss_count_by_id.get("handicap_not_shallow_or_level", 0) >= 3:
        advice.append("当前大量样本不在浅盘/均势盘区间，规则覆盖面偏窄，可评估是否纳入 `半球至半一` 的次级分层。")
    if miss_count_by_id.get("prediction_draw_not_supported", 0) >= 1:
        advice.append("当前三层规则对“预测为平局”场景尚未建模，建议补一层平局主导的专用复盘规则。")
    if miss_count_by_id.get("euro_not_doubtful_enough", 0) >= 2:
        advice.append("客让浅盘样本里欧赔疑虑阈值偏严，可适度放宽 `draw_guarded/soft_support` 判定边界。")
    if miss_count_by_id.get("strength_not_balanced_enough", 0) >= 2:
        advice.append("均势盘平局漏防规则的实力差阈值覆盖偏窄，可评估把均势边界从 `<=8` 放宽到 `<=10~12`。")
    if miss_count_by_id.get("strength_not_large_enough", 0) >= 2:
        advice.append("强弱差触发阈值存在拦截，可评估把“实力差大”门槛从 `18` 下调到更平滑的分档触发。")

    strong_home_row = rule_by_id.get("strong_home_shallow_line")
    if isinstance(strong_home_row, dict) and str(strong_home_row.get("verdict") or "") in {"近期失效", "效果分化"}:
        advice.append("“主让浅盘但实力差大”近期效果不稳，建议降低主胜回收幅度并增加对欧赔支持强度的二次过滤。")
    away_doubt_row = rule_by_id.get("away_shallow_market_doubt")
    if isinstance(away_doubt_row, dict) and str(away_doubt_row.get("verdict") or "") in {"近期失效", "效果分化"}:
        advice.append("“客让浅盘但市场疑虑高”近期失真，建议强化平局分支，减少直接转向主胜的权重。")
    balanced_draw_row = rule_by_id.get("balanced_draw_guard")
    if isinstance(balanced_draw_row, dict) and str(balanced_draw_row.get("verdict") or "") in {"近期失效", "效果分化"}:
        advice.append("“均势盘平局漏防”近期未兑现，建议重新校准平局增益幅度，避免对非平局样本过度加平。")

    strong_home_score = score_by_id.get("strong_home_shallow_line")
    if isinstance(strong_home_score, dict) and int(strong_home_score.get("failed_count") or 0) >= max(1, int(strong_home_score.get("effective_count") or 0)):
        advice.append("“主让浅盘但实力差大”在比分层仍偏激进，建议继续压缩 `3-0/3-1` 一类大胜零封比分，抬高 `1-0/2-1/1-1`。")
    away_doubt_score = score_by_id.get("away_shallow_market_doubt")
    if isinstance(away_doubt_score, dict) and int(away_doubt_score.get("failed_count") or 0) >= max(1, int(away_doubt_score.get("effective_count") or 0)):
        advice.append("“客让浅盘但市场疑虑高”在比分层仍高估客队打穿，建议提高 `0-1/1-2/1-1`，压低客队两球以上净胜。")
    draw_guard_score = score_by_id.get("balanced_draw_guard") or score_by_id.get("draw_market_balance")
    if isinstance(draw_guard_score, dict) and int(draw_guard_score.get("failed_count") or 0) >= max(1, int(draw_guard_score.get("effective_count") or 0)):
        advice.append("均势/平局导向场景的比分三层效果偏弱，建议提高 `0-0/1-1/2-2` 权重，并下调单边比分模板。")

    strong_home_ou = ou_by_id.get("strong_home_shallow_line")
    if isinstance(strong_home_ou, dict) and int(strong_home_ou.get("failed_count") or 0) >= max(1, int(strong_home_ou.get("effective_count") or 0)):
        advice.append("“主让浅盘但实力差大”在大小球层仍偏热，建议进一步降低极端大球倾向，回收至 `2-3` 球主区间。")
    away_doubt_ou = ou_by_id.get("away_shallow_market_doubt")
    if isinstance(away_doubt_ou, dict) and int(away_doubt_ou.get("failed_count") or 0) >= max(1, int(away_doubt_ou.get("effective_count") or 0)):
        advice.append("“客让浅盘但市场疑虑高”在大小球层仍偏进取，建议压低客胜放大时的 `大球` 迁移幅度。")
    draw_guard_ou = ou_by_id.get("balanced_draw_guard") or ou_by_id.get("draw_market_balance")
    if isinstance(draw_guard_ou, dict) and int(draw_guard_ou.get("failed_count") or 0) >= max(1, int(draw_guard_ou.get("effective_count") or 0)):
        advice.append("均势/平局导向场景在大小球层未充分降温，建议提高 `小球` 与 `2.5 以下` 的防守权重。")

    for rule_id in ("strong_home_shallow_line", "away_shallow_market_doubt", "balanced_draw_guard", "draw_market_balance"):
        score_row = score_by_id.get(rule_id)
        ou_row = ou_by_id.get(rule_id)
        if not isinstance(score_row, dict) or not isinstance(ou_row, dict):
            continue
        score_effective = int(score_row.get("effective_count") or 0)
        score_failed = int(score_row.get("failed_count") or 0)
        ou_effective = int(ou_row.get("effective_count") or 0)
        ou_failed = int(ou_row.get("failed_count") or 0)
        if score_effective > score_failed and ou_failed > ou_effective:
            advice.append(f"“{CALIBRATION_RULE_LABELS.get(rule_id, rule_id)}”当前呈现“比分有效、大小球失效”，下一步应优先优化大小球三层迁移。")
            break
        if ou_effective > ou_failed and score_failed > score_effective:
            advice.append(f"“{CALIBRATION_RULE_LABELS.get(rule_id, rule_id)}”当前呈现“大小球有效、比分失效”，下一步应优先优化比分分布重排。")
            break

    deduped: List[str] = []
    for item in advice:
        if item not in deduped:
            deduped.append(item)
    return deduped[:5]


def _load_cases(base_dir: str) -> List[Dict[str, Any]]:
    path = Path(rag_cases_path(base_dir))
    if not path.exists():
        return []
    payload = _safe_json_load(path)
    if isinstance(payload, dict):
        cases = payload.get("cases")
        return [item for item in cases if isinstance(item, dict)] if isinstance(cases, list) else []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _resolve_as_of_date(as_of_date: Optional[object] = None) -> date:
    if isinstance(as_of_date, datetime):
        return as_of_date.date()
    if isinstance(as_of_date, date):
        return as_of_date
    if isinstance(as_of_date, str) and as_of_date.strip():
        text = as_of_date.strip()
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    return date.fromisoformat(text[:10])
                except ValueError:
                    return datetime.now().date()
    return datetime.now().date()


def _load_prediction_archive(base_dir: str) -> Dict[str, Any]:
    path = Path(base_dir) / ".okooo-scraper" / "runtime" / "prediction_archive.json"
    if not path.exists():
        return {}
    payload = _safe_json_load(path)
    return payload if isinstance(payload, dict) else {}


def _match_key(case: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(case.get("league_code") or "").strip(),
        _normalize_match_date(case.get("match_date")),
        str(case.get("home_team") or "").strip(),
        str(case.get("away_team") or "").strip(),
    )


def _normalize_score(score_text: Any) -> str:
    normalized = str(score_text or "").replace(":", "-").replace("：", "-").replace(" ", "").strip()
    return normalized


def _normalize_match_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10:
        prefix = text[:10]
        try:
            return date.fromisoformat(prefix).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text


def _match_lookup_keys(case: Dict[str, Any]) -> List[str]:
    match_id = str(case.get("match_id") or "").strip()
    league_code = str(case.get("league_code") or case.get("league") or "").strip()
    match_date = str(case.get("match_date") or "").strip()
    home_team = str(case.get("home_team") or "").strip()
    away_team = str(case.get("away_team") or "").strip()
    keys = []
    if match_id:
        keys.append(f"id:{match_id}")
    if league_code and match_date and home_team and away_team:
        keys.append(f"meta:{league_code}|{_normalize_match_date(match_date)}|{home_team}|{away_team}")
    return keys


def _build_archive_lookup(base_dir: str) -> Dict[str, Dict[str, Any]]:
    archive = _load_prediction_archive(base_dir)
    lookup: Dict[str, Dict[str, Any]] = {}
    for archive_key, entry in archive.items():
        if not isinstance(entry, dict):
            continue
        full_prediction = entry.get("full_prediction") if isinstance(entry.get("full_prediction"), dict) else {}
        sample = {
            "match_id": entry.get("match_id") or entry.get("external_match_id") or full_prediction.get("match_id") or archive_key,
            "league_code": entry.get("league") or full_prediction.get("league_code"),
            "match_date": entry.get("match_date") or full_prediction.get("match_date"),
            "home_team": entry.get("home_team") or full_prediction.get("home_team"),
            "away_team": entry.get("away_team") or full_prediction.get("away_team"),
        }
        payload = {
            "strength_diff": entry.get("strength_diff"),
        }
        for key in _match_lookup_keys(sample):
            lookup[key] = payload
    return lookup


def _winner_key_from_text(text: Any) -> str:
    mapping = {"主胜": "home", "平局": "draw", "客胜": "away", "home": "home", "draw": "draw", "away": "away"}
    return mapping.get(str(text or "").strip(), "")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _classify_handicap_depth_bucket(asian_line: Any) -> str:
    line = _safe_float(asian_line)
    if line is None:
        return "unknown"
    depth = abs(float(line))
    if depth < 0.125:
        return "level_ball"
    if depth <= 0.25:
        return "level_shallow"
    if depth <= 0.75:
        return "level_medium"
    if depth <= 1.25:
        return "level_deep"
    return "level_very_deep"


def _classify_strength_gap_bucket(strength_diff: Any) -> str:
    gap = abs(float(_safe_float(strength_diff) or 0.0))
    if gap <= 8:
        return "balanced"
    if gap <= 16:
        return "edge"
    if gap <= 24:
        return "clear"
    return "huge"


def _classify_euro_support_bucket(predicted_winner: str, euro_home: Any, euro_draw: Any, euro_away: Any) -> str:
    oh = _safe_float(euro_home)
    od = _safe_float(euro_draw)
    oa = _safe_float(euro_away)
    if not oh or not od or not oa or oh <= 1.01 or od <= 1.01 or oa <= 1.01:
        return "unknown"
    raw_home = 1.0 / oh
    raw_draw = 1.0 / od
    raw_away = 1.0 / oa
    total = raw_home + raw_draw + raw_away
    if total <= 0:
        return "unknown"
    ph = raw_home / total
    pd = raw_draw / total
    pa = raw_away / total

    if predicted_winner == "draw":
        if pd >= max(ph, pa) + 0.02:
            return "draw_supported"
        if pd >= max(ph, pa) - 0.01:
            return "draw_live"
        return "draw_soft"

    pred_prob = ph if predicted_winner == "home" else pa
    opp_prob = pa if predicted_winner == "home" else ph
    if opp_prob >= pred_prob + 0.03:
        return "market_opposes"
    if pd >= pred_prob - 0.01:
        return "draw_guarded"
    if pred_prob >= max(pd, opp_prob) + 0.08:
        return "strong_support"
    if pred_prob >= max(pd, opp_prob) + 0.03:
        return "support"
    return "soft_support"


def _match_archive_strength(case: Dict[str, Any], archive_lookup: Dict[str, Dict[str, Any]]) -> Optional[float]:
    for key in _match_lookup_keys(case):
        payload = archive_lookup.get(key)
        if isinstance(payload, dict):
            return _safe_float(payload.get("strength_diff"))
    return None


def _identify_calibration_rules(case: Dict[str, Any], strength_diff: Optional[float]) -> List[str]:
    predicted_winner = _winner_key_from_text(case.get("prediction"))
    handicap_depth_bucket = _classify_handicap_depth_bucket(case.get("asian_line"))
    euro_support_bucket = _classify_euro_support_bucket(
        predicted_winner,
        case.get("euro_home"),
        case.get("euro_draw"),
        case.get("euro_away"),
    )
    gap = float(strength_diff) if strength_diff is not None else 0.0
    rules: List[str] = []
    if (
        predicted_winner == "home"
        and strength_diff is not None
        and abs(float(_safe_float(case.get("asian_line")) or 0.0)) <= 0.5
        and gap >= 18
    ):
        rules.append("strong_home_shallow_line")
    if predicted_winner == "away" and handicap_depth_bucket in {"level_ball", "level_shallow"} and euro_support_bucket in {"draw_guarded", "market_opposes", "soft_support"}:
        rules.append("away_shallow_market_doubt")
    if (
        predicted_winner in {"home", "away"}
        and strength_diff is not None
        and handicap_depth_bucket in {"level_ball", "level_shallow"}
        and _classify_strength_gap_bucket(gap) == "balanced"
    ):
        rules.append("balanced_draw_guard")
    return rules


def _rule_effectiveness(rule_id: str, case: Dict[str, Any]) -> str:
    predicted = _winner_key_from_text(case.get("prediction"))
    actual = _winner_key_from_text(case.get("actual_result"))
    if rule_id == "strong_home_shallow_line":
        return "effective" if actual in {"draw", "away"} else "failed"
    if rule_id == "away_shallow_market_doubt":
        return "effective" if actual in {"draw", "home"} else "failed"
    if rule_id == "balanced_draw_guard":
        return "effective" if actual == "draw" else "failed"
    return "failed" if actual == predicted else "effective"


def _build_rule_example(case: Dict[str, Any]) -> str:
    return f"{case.get('home_team')} {_normalize_score(case.get('actual_score'))} {case.get('away_team')}"


def _analyze_calibration_miss_reasons(case: Dict[str, Any], strength_diff: Optional[float]) -> List[str]:
    predicted_winner = _winner_key_from_text(case.get("prediction"))
    if predicted_winner == "draw":
        return ["prediction_draw_not_supported"]

    reasons: List[str] = []
    asian_line = _safe_float(case.get("asian_line"))
    if asian_line is None:
        reasons.append("missing_asian_line")
    handicap_depth_bucket = _classify_handicap_depth_bucket(asian_line)
    if handicap_depth_bucket not in {"level_ball", "level_shallow"}:
        reasons.append("handicap_not_shallow_or_level")

    if strength_diff is None:
        reasons.append("missing_strength_diff")
    else:
        gap = float(strength_diff)
        if predicted_winner == "home" and gap < 18:
            reasons.append("strength_not_large_enough")
        if predicted_winner == "away" and gap > -18:
            reasons.append("strength_not_large_enough")
        if _classify_strength_gap_bucket(gap) != "balanced":
            reasons.append("strength_not_balanced_enough")

    euro_support_bucket = _classify_euro_support_bucket(
        predicted_winner,
        case.get("euro_home"),
        case.get("euro_draw"),
        case.get("euro_away"),
    )
    if euro_support_bucket == "unknown":
        reasons.append("missing_euro_odds")
    elif predicted_winner == "away" and euro_support_bucket not in {"draw_guarded", "market_opposes", "soft_support"}:
        reasons.append("euro_not_doubtful_enough")

    deduped: List[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)
    return deduped or ["handicap_not_shallow_or_level"]


def _summarize_calibration_rules(prediction_docs: List[Dict[str, Any]], archive_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    by_league: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    overall_rows: List[Dict[str, Any]] = []
    overall_miss_rows: List[Dict[str, Any]] = []
    by_league_miss: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for case in prediction_docs:
        strength_diff = _match_archive_strength(case, archive_lookup)
        rule_ids = _identify_calibration_rules(case, strength_diff)
        for rule_id in rule_ids:
            status = _rule_effectiveness(rule_id, case)
            row = {
                "rule_id": rule_id,
                "rule_label": CALIBRATION_RULE_LABELS.get(rule_id, rule_id),
                "status": status,
                "league_code": str(case.get("league_code") or "").strip(),
                "match_text": _build_rule_example(case),
                "match_date": str(case.get("match_date") or ""),
                "actual_result": str(case.get("actual_result") or ""),
                "prediction": str(case.get("prediction") or ""),
            }
            overall_rows.append(row)
            by_league[row["league_code"]].append(row)
        if not rule_ids:
            for reason_id in _analyze_calibration_miss_reasons(case, strength_diff):
                miss_row = {
                    "reason_id": reason_id,
                    "reason_label": CALIBRATION_MISS_REASON_LABELS.get(reason_id, reason_id),
                    "league_code": str(case.get("league_code") or "").strip(),
                    "match_text": _build_rule_example(case),
                }
                overall_miss_rows.append(miss_row)
                by_league_miss[miss_row["league_code"]].append(miss_row)

    def _finalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        summary_rows: List[Dict[str, Any]] = []
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["rule_id"]].append(row)
        for rule_id, items in grouped.items():
            hit_count = len(items)
            effective_examples = [item["match_text"] for item in items if item["status"] == "effective"][:3]
            failed_examples = [item["match_text"] for item in items if item["status"] == "failed"][:3]
            effective_count = sum(1 for item in items if item["status"] == "effective")
            failed_count = sum(1 for item in items if item["status"] == "failed")
            rate = round(effective_count / hit_count, 4) if hit_count else 0.0
            if hit_count <= 1:
                verdict = "样本待观察"
            elif rate >= 0.67:
                verdict = "近期有效"
            elif rate <= 0.33:
                verdict = "近期失效"
            else:
                verdict = "效果分化"
            summary_rows.append(
                {
                    "rule_id": rule_id,
                    "rule_label": CALIBRATION_RULE_LABELS.get(rule_id, rule_id),
                    "hit_count": hit_count,
                    "effective_count": effective_count,
                    "failed_count": failed_count,
                    "effective_rate": rate,
                    "verdict": verdict,
                    "effective_examples": effective_examples,
                    "failed_examples": failed_examples,
                }
            )
        summary_rows.sort(key=lambda item: (-item["hit_count"], item["rule_label"]))
        return summary_rows

    def _finalize_misses(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        summary_rows: List[Dict[str, Any]] = []
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row["reason_id"]].append(row)
        for reason_id, items in grouped.items():
            summary_rows.append(
                {
                    "reason_id": reason_id,
                    "reason_label": CALIBRATION_MISS_REASON_LABELS.get(reason_id, reason_id),
                    "count": len(items),
                    "examples": [item["match_text"] for item in items[:3]],
                }
            )
        summary_rows.sort(key=lambda item: (-item["count"], item["reason_label"]))
        return summary_rows

    return {
        "overall": _finalize(overall_rows),
        "by_league": {league_code: _finalize(rows) for league_code, rows in by_league.items()},
        "miss_reason_overall": _finalize_misses(overall_miss_rows),
        "miss_reason_by_league": {league_code: _finalize_misses(rows) for league_code, rows in by_league_miss.items()},
    }


def _identify_score_ou_three_layer_rules(case: Dict[str, Any], strength_diff: Optional[float]) -> List[str]:
    predicted_winner = _winner_key_from_text(case.get("prediction"))
    handicap_depth_bucket = _classify_handicap_depth_bucket(case.get("asian_line"))
    euro_support_bucket = _classify_euro_support_bucket(
        predicted_winner,
        case.get("euro_home"),
        case.get("euro_draw"),
        case.get("euro_away"),
    )
    rules: List[str] = []
    gap = float(strength_diff) if strength_diff is not None else 0.0
    if predicted_winner == "home" and strength_diff is not None and handicap_depth_bucket in {"level_ball", "level_shallow"} and gap >= 18:
        rules.append("strong_home_shallow_line")
    if predicted_winner == "away" and handicap_depth_bucket in {"level_ball", "level_shallow"} and euro_support_bucket in {"draw_guarded", "market_opposes", "soft_support"}:
        rules.append("away_shallow_market_doubt")
    if predicted_winner in {"home", "away"} and strength_diff is not None and handicap_depth_bucket in {"level_ball", "level_shallow"} and _classify_strength_gap_bucket(gap) == "balanced":
        rules.append("balanced_draw_guard")
    if predicted_winner == "draw" and (handicap_depth_bucket in {"level_ball", "level_shallow"} or euro_support_bucket in {"draw_supported", "draw_live"}):
        rules.append("draw_market_balance")
    return rules


def _score_rule_effectiveness(rule_id: str, case: Dict[str, Any]) -> Optional[str]:
    parsed = _parse_score_tuple(case.get("actual_score"))
    if not parsed:
        return None
    home_goals, away_goals = parsed
    margin = home_goals - away_goals
    if rule_id == "strong_home_shallow_line":
        if (margin == 1 and home_goals > away_goals) or (home_goals == away_goals and home_goals <= 1):
            return "effective"
        return "failed"
    if rule_id == "away_shallow_market_doubt":
        if margin == -1 or (home_goals == away_goals and away_goals <= 1):
            return "effective"
        return "failed"
    if rule_id in {"balanced_draw_guard", "draw_market_balance"}:
        return "effective" if home_goals == away_goals and _normalize_score(case.get("actual_score")) in {"0-0", "1-1", "2-2"} else "failed"
    return None


def _parse_score_tuple(score_text: Any) -> Optional[Tuple[int, int]]:
    normalized = _normalize_score(score_text)
    try:
        left, right = normalized.split("-")
        return int(left), int(right)
    except Exception:
        return None


def _ou_rule_effectiveness(rule_id: str, case: Dict[str, Any]) -> Optional[str]:
    parsed = _parse_score_tuple(case.get("actual_score"))
    line = _safe_float(case.get("ou_line"))
    if not parsed or line is None:
        return None
    total_goals = parsed[0] + parsed[1]
    if abs(total_goals - line) < 1e-9:
        return "effective"
    if rule_id in {"strong_home_shallow_line", "away_shallow_market_doubt", "balanced_draw_guard", "draw_market_balance"}:
        return "effective" if total_goals < line else "failed"
    return None


def _finalize_effectiveness_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary_rows: List[Dict[str, Any]] = []
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("rule_id") or "")].append(row)
    for rule_id, items in grouped.items():
        sample_count = len(items)
        effective_count = sum(1 for item in items if item.get("status") == "effective")
        failed_count = sum(1 for item in items if item.get("status") == "failed")
        rate = round(effective_count / sample_count, 4) if sample_count else 0.0
        if sample_count <= 1:
            verdict = "样本待观察"
        elif rate >= 0.67:
            verdict = "近期有效"
        elif rate <= 0.33:
            verdict = "近期失效"
        else:
            verdict = "效果分化"
        summary_rows.append(
            {
                "rule_id": rule_id,
                "rule_label": CALIBRATION_RULE_LABELS.get(rule_id, rule_id),
                "sample_count": sample_count,
                "effective_count": effective_count,
                "failed_count": failed_count,
                "effective_rate": rate,
                "verdict": verdict,
                "effective_examples": [item.get("match_text") for item in items if item.get("status") == "effective"][:3],
                "failed_examples": [item.get("match_text") for item in items if item.get("status") == "failed"][:3],
            }
        )
    summary_rows.sort(key=lambda item: (-int(item.get("sample_count") or 0), str(item.get("rule_label") or "")))
    return summary_rows


def _summarize_score_ou_three_layer_effects(prediction_docs: List[Dict[str, Any]], archive_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    score_rows: List[Dict[str, Any]] = []
    ou_rows: List[Dict[str, Any]] = []
    score_by_league: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    ou_by_league: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for case in prediction_docs:
        strength_diff = _match_archive_strength(case, archive_lookup)
        league_code = str(case.get("league_code") or "").strip()
        for rule_id in _identify_score_ou_three_layer_rules(case, strength_diff):
            score_status = _score_rule_effectiveness(rule_id, case)
            if score_status:
                row = {
                    "rule_id": rule_id,
                    "status": score_status,
                    "league_code": league_code,
                    "match_text": _build_rule_example(case),
                }
                score_rows.append(row)
                score_by_league[league_code].append(row)
            ou_status = _ou_rule_effectiveness(rule_id, case)
            if ou_status:
                row = {
                    "rule_id": rule_id,
                    "status": ou_status,
                    "league_code": league_code,
                    "match_text": _build_rule_example(case),
                }
                ou_rows.append(row)
                ou_by_league[league_code].append(row)
    return {
        "score_overall": _finalize_effectiveness_rows(score_rows),
        "score_by_league": {league_code: _finalize_effectiveness_rows(rows) for league_code, rows in score_by_league.items()},
        "ou_overall": _finalize_effectiveness_rows(ou_rows),
        "ou_by_league": {league_code: _finalize_effectiveness_rows(rows) for league_code, rows in ou_by_league.items()},
    }


def _compute_ou_hit(case: Dict[str, Any]) -> Optional[bool]:
    predicted_ou_direction = str(case.get("predicted_ou_direction") or "").strip()
    if not predicted_ou_direction:
        return None

    try:
        ou_line = float(case.get("ou_line")) if case.get("ou_line") not in (None, "") else None
    except Exception:
        ou_line = None
    if ou_line is None:
        return None

    actual_score = _normalize_score(case.get("actual_score"))
    try:
        home_score, away_score = [int(part.strip()) for part in actual_score.split("-")]
    except Exception:
        return None

    total_goals = home_score + away_score
    if abs(total_goals - ou_line) < 1e-9:
        return None
    actual_ou_direction = "大球" if total_goals > ou_line else "小球"
    return actual_ou_direction == predicted_ou_direction


def _build_match_snapshot(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "league_code": str(case.get("league_code") or "").strip(),
        "match_date": _normalize_match_date(case.get("match_date")),
        "home_team": str(case.get("home_team") or "").strip(),
        "away_team": str(case.get("away_team") or "").strip(),
        "actual_score": _normalize_score(case.get("actual_score")),
        "actual_result": str(case.get("actual_result") or "").strip(),
    }


def _format_match_summary(item: Dict[str, Any]) -> str:
    return f"{item.get('home_team')} {_normalize_score(item.get('actual_score')) or '-'} {item.get('away_team')}"


def _strip_league_prefix(league_code: str, text: str) -> str:
    league_name = LEAGUE_CN_NAMES.get(league_code, league_code)
    prefix = f"{league_name}-"
    return text[len(prefix) :] if text.startswith(prefix) else text


def _classify_top_problem_group(text: Any) -> Optional[str]:
    value = str(text or "").strip()
    if not value:
        return None
    if any(keyword in value for keyword in ["归档覆盖", "无预测", "覆盖不足"]):
        return "coverage"
    if any(keyword in value for keyword in ["亚盘", "欧赔", "盘口", "大小球", "进球弹性", "实力差", "浅盘", "均势盘", "暂不适用"]):
        return "market"
    if "比分" in value:
        return "score"
    if any(keyword in value for keyword in ["主胜", "平局", "客胜"]):
        return "direction"
    return None


def _dedupe_problem_items(items: List[str]) -> List[str]:
    deduped: List[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _build_top_problem_groups(
    *,
    case_tag_counts: Dict[str, int],
    unpredicted_count: int = 0,
    miss_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[str]] = {key: [] for key in TOP_PROBLEM_GROUP_LABELS}
    if unpredicted_count > 0:
        grouped["coverage"].append(f"预测归档覆盖不足 x{unpredicted_count}")

    sorted_case_tags = sorted(
        [(str(tag), int(count)) for tag, count in (case_tag_counts or {}).items()],
        key=lambda item: (-item[1], item[0]),
    )
    for tag, count in sorted_case_tags:
        group_key = _classify_top_problem_group(tag)
        if group_key and group_key != "coverage":
            grouped[group_key].append(f"{tag} x{count}")

    for row in miss_rows or []:
        reason_label = str(row.get("reason_label") or "").strip()
        group_key = _classify_top_problem_group(reason_label)
        if group_key == "market":
            grouped[group_key].append(f"{reason_label} x{int(row.get('count') or 0)}")

    fallback_text = {
        "coverage": "无明显缺口",
        "direction": "无显著偏差",
        "score": "无显著偏差",
        "market": "无显著风险",
    }
    rows: List[Dict[str, Any]] = []
    for group_key, group_label in TOP_PROBLEM_GROUP_LABELS.items():
        deduped = _dedupe_problem_items(grouped[group_key])
        rows.append(
            {
                "group_key": group_key,
                "group_label": group_label,
                "items": deduped[:2],
                "summary": " / ".join(deduped[:2]) if deduped else fallback_text[group_key],
            }
        )
    return rows


def _format_top_problem_groups(group_rows: List[Dict[str, Any]]) -> str:
    return "；".join(
        f"{item.get('group_label')}：{item.get('summary')}"
        for item in group_rows
        if str(item.get("group_label") or "").strip()
    )


def _build_global_top_problem_lines(summary: Dict[str, Any]) -> List[str]:
    grouped = summary.get("overall_top_problem_groups") or []
    return [f"{item.get('group_label')}：`{item.get('summary')}`。" for item in grouped[:4]]


def _build_league_conclusion(league_code: str, section: Dict[str, Any]) -> str:
    tags = [_strip_league_prefix(league_code, str(item)) for item in (section.get("league_tags") or []) if str(item).strip()]
    if tags:
        core = "；".join(tags[:2])
    else:
        core = "当前窗口样本有限，先以补齐覆盖和继续观察为主"

    suffixes: List[str] = []
    prediction_count = int(section.get("prediction_count") or 0)
    if prediction_count > 0 and int(section.get("score_hits") or 0) == 0:
        suffixes.append("比分层明显弱于方向层")
    ou_samples = int(section.get("ou_samples") or 0)
    if ou_samples > 0 and int(section.get("ou_hits") or 0) * 2 < ou_samples:
        suffixes.append("大小球层仍需继续修正")
    unpredicted_count = int(section.get("unpredicted_completed_count") or 0)
    if unpredicted_count > 0 and unpredicted_count >= max(2, prediction_count):
        suffixes.append(f"另有 {unpredicted_count} 场无预测样本")
    if suffixes:
        return f"{core}，{'，'.join(suffixes)}。"
    return f"{core}。"


def _build_league_representative_examples(section: Dict[str, Any]) -> str:
    mismatches = section.get("representative_mismatches") or []
    if mismatches:
        return " / ".join(_format_match_summary(item) for item in mismatches[:3])
    missing = section.get("unpredicted_completed_matches") or []
    if missing:
        return " / ".join(_format_match_summary(item) for item in missing[:3])
    return "无"


def _build_league_action_line(section: Dict[str, Any]) -> str:
    advice_lines = [str(item).strip() for item in (section.get("auto_remediation_advice") or []) if str(item).strip()]
    if advice_lines:
        return " / ".join(advice_lines[:2])
    if int(section.get("unpredicted_completed_count") or 0) > 0:
        return "优先补齐预测归档覆盖，再继续观察联赛级规则表现。"
    return "保持现有校准配置，继续累积样本后再判断是否需要新增分层。"


def summarize_recent_five_leagues_cases(
    base_dir: str,
    recent_days: int = 7,
    as_of_date: Optional[object] = None,
) -> Dict[str, Any]:
    cases = _load_cases(base_dir)
    archive_lookup = _build_archive_lookup(base_dir)
    window_end = _resolve_as_of_date(as_of_date)
    window_start = window_end - timedelta(days=recent_days)
    cutoff = window_start.isoformat()

    completed_match_keys = {
        _match_key(case)
        for case in cases
        if str(case.get("league_code") or "").strip() in DOMESTIC_LEAGUE_CODES
        and _normalize_match_date(case.get("match_date")) >= cutoff
        and bool(case.get("completed"))
    }

    prediction_docs: List[Dict[str, Any]] = []
    seen_prediction_matches = set()
    completed_match_snapshots: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for case in cases:
        league_code = str(case.get("league_code") or "").strip()
        if league_code not in DOMESTIC_LEAGUE_CODES:
            continue
        if _normalize_match_date(case.get("match_date")) < cutoff or not bool(case.get("completed")):
            continue
        match_key = _match_key(case)
        completed_match_snapshots.setdefault(match_key, _build_match_snapshot(case))

    for case in cases:
        if str(case.get("case_type") or "") != "prediction_case":
            continue
        league_code = str(case.get("league_code") or "").strip()
        if league_code not in DOMESTIC_LEAGUE_CODES:
            continue
        if _normalize_match_date(case.get("match_date")) < cutoff or not bool(case.get("completed")):
            continue
        if not str(case.get("prediction") or "").strip():
            continue
        match_key = _match_key(case)
        if match_key in seen_prediction_matches:
            continue
        seen_prediction_matches.add(match_key)
        prediction_docs.append(case)

    unpredicted_match_keys = sorted(completed_match_keys - seen_prediction_matches)
    unpredicted_matches = [
        completed_match_snapshots.get(match_key)
        for match_key in unpredicted_match_keys
        if completed_match_snapshots.get(match_key)
    ]
    unpredicted_matches.sort(
        key=lambda item: (
            str(item.get("match_date") or ""),
            str(item.get("league_code") or ""),
            str(item.get("home_team") or ""),
        )
    )

    win_hits = sum(1 for case in prediction_docs if str(case.get("prediction") or "") == str(case.get("actual_result") or ""))
    score_hits = sum(
        1
        for case in prediction_docs
        if _normalize_score(case.get("actual_score")) in [_normalize_score(item) for item in (case.get("predicted_scores") or [])]
    )
    ou_hits = 0
    ou_samples = 0
    for case in prediction_docs:
        ou_hit = _compute_ou_hit(case)
        if ou_hit is None:
            continue
        ou_samples += 1
        if ou_hit:
            ou_hits += 1

    by_league: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for case in prediction_docs:
        by_league[str(case.get("league_code") or "").strip()].append(case)

    mismatches = [case for case in prediction_docs if str(case.get("prediction") or "") != str(case.get("actual_result") or "")]
    mismatches.sort(key=lambda item: (str(item.get("match_date") or ""), str(item.get("league_code") or ""), str(item.get("home_team") or "")))
    calibration_rule_review = _summarize_calibration_rules(prediction_docs, archive_lookup)
    score_ou_three_layer_review = _summarize_score_ou_three_layer_effects(prediction_docs, archive_lookup)

    league_sections: Dict[str, Dict[str, Any]] = {}
    for league_code in LEAGUE_ORDER:
        league_cases = by_league.get(league_code, [])
        league_completed = {key for key in completed_match_keys if key[0] == league_code}
        league_unpredicted = [item for item in unpredicted_matches if str(item.get("league_code") or "") == league_code]
        league_tag_counter: Counter[str] = Counter()
        league_level_tags: List[str] = []
        league_win_hits = sum(1 for case in league_cases if str(case.get("prediction") or "") == str(case.get("actual_result") or ""))
        league_score_hits = sum(
            1
            for case in league_cases
            if _normalize_score(case.get("actual_score")) in [_normalize_score(item) for item in (case.get("predicted_scores") or [])]
        )
        league_ou_hits = 0
        league_ou_samples = 0
        league_mismatches = []

        for case in league_cases:
            league_tag_counter.update(case.get("review_tags") or [])
            if not league_level_tags:
                league_level_tags = list(case.get("league_review_tags") or [])
            ou_hit = _compute_ou_hit(case)
            if ou_hit is not None:
                league_ou_samples += 1
                if ou_hit:
                    league_ou_hits += 1
            if str(case.get("prediction") or "") != str(case.get("actual_result") or ""):
                league_mismatches.append(
                    {
                        "match_date": str(case.get("match_date") or ""),
                        "home_team": str(case.get("home_team") or ""),
                        "away_team": str(case.get("away_team") or ""),
                        "actual_score": _normalize_score(case.get("actual_score")),
                        "prediction": str(case.get("prediction") or ""),
                        "review_tags": list(case.get("review_tags") or []),
                    }
                )

        league_rule_review = calibration_rule_review["by_league"].get(league_code, [])
        league_rule_miss_reasons = calibration_rule_review["miss_reason_by_league"].get(league_code, [])
        league_sections[league_code] = {
            "completed_count": len(league_completed),
            "completed_dates": sorted({key[1] for key in league_completed if key[1]}),
            "prediction_count": len(league_cases),
            "unpredicted_completed_count": len(league_unpredicted),
            "unpredicted_completed_matches": league_unpredicted[:5],
            "win_hits": league_win_hits,
            "score_hits": league_score_hits,
            "ou_hits": league_ou_hits,
            "ou_samples": league_ou_samples,
            "league_tags": league_level_tags,
            "case_tags": dict(league_tag_counter),
            "top_case_tags": [f"{tag} x{count}" for tag, count in league_tag_counter.most_common(5)],
            "representative_mismatches": league_mismatches[:3],
            "calibration_rule_review": league_rule_review,
            "calibration_rule_miss_reasons": league_rule_miss_reasons,
            "score_three_layer_review": score_ou_three_layer_review["score_by_league"].get(league_code, []),
            "ou_three_layer_review": score_ou_three_layer_review["ou_by_league"].get(league_code, []),
            "auto_remediation_advice": _build_auto_remediation_advice(
                league_rule_review,
                league_rule_miss_reasons,
                score_ou_three_layer_review["score_by_league"].get(league_code, []),
                score_ou_three_layer_review["ou_by_league"].get(league_code, []),
            ),
        }

    overall_rule_review = calibration_rule_review["overall"]
    overall_miss_reasons = calibration_rule_review["miss_reason_overall"]
    for league_code in LEAGUE_ORDER:
        section = league_sections.get(league_code) or {}
        group_rows = _build_top_problem_groups(
            case_tag_counts={str(k): int(v) for k, v in (section.get("case_tags") or {}).items()},
            unpredicted_count=int(section.get("unpredicted_completed_count") or 0),
            miss_rows=section.get("calibration_rule_miss_reasons") or [],
        )
        section["top_problem_groups"] = group_rows
        section["top_problem_group_text"] = _format_top_problem_groups(group_rows)

    overall_case_tag_counts: Dict[str, int] = {}
    for league_code in LEAGUE_ORDER:
        section = league_sections.get(league_code) or {}
        for tag, count in (section.get("case_tags") or {}).items():
            overall_case_tag_counts[str(tag)] = overall_case_tag_counts.get(str(tag), 0) + int(count)
    overall_top_problem_groups = _build_top_problem_groups(
        case_tag_counts=overall_case_tag_counts,
        unpredicted_count=int(summary_unpredicted_count := len(unpredicted_matches)),
        miss_rows=overall_miss_reasons,
    )
    return {
        "window_start": cutoff,
        "window_end": window_end.isoformat(),
        "completed_match_count": len(completed_match_keys),
        "prediction_count": len(prediction_docs),
        "unpredicted_completed_count": summary_unpredicted_count,
        "unpredicted_completed_matches": unpredicted_matches,
        "win_hits": win_hits,
        "score_hits": score_hits,
        "ou_hits": ou_hits,
        "ou_samples": ou_samples,
        "league_sections": league_sections,
        "mismatches": mismatches,
        "calibration_rule_review": calibration_rule_review,
        "score_ou_three_layer_review": score_ou_three_layer_review,
        "overall_top_problem_groups": overall_top_problem_groups,
        "overall_top_problem_group_text": _format_top_problem_groups(overall_top_problem_groups),
        "auto_remediation_advice": _build_auto_remediation_advice(
            overall_rule_review,
            overall_miss_reasons,
            score_ou_three_layer_review["score_overall"],
            score_ou_three_layer_review["ou_overall"],
        ),
    }


def build_recent_five_leagues_review(
    base_dir: str,
    recent_days: int = 7,
    output_path: Optional[str] = None,
    as_of_date: Optional[object] = None,
) -> Dict[str, Any]:
    summary = summarize_recent_five_leagues_cases(base_dir, recent_days=recent_days, as_of_date=as_of_date)
    report_lines = [
        f"# 五大联赛近{recent_days}天已完赛复盘",
        "",
        f"> 统计窗口：{summary['window_start']} 至 {summary['window_end']}；正式已完赛样本 {summary['completed_match_count']} 场。",
        "",
        "## 总体快照",
        "",
        f"- 已完赛样本：`{summary['completed_match_count']}`；完成态 RAG 覆盖：`{summary['completed_match_count']}`。",
        f"- 可恢复预测样本：`{summary['prediction_count']}`；已完赛但无预测样本：`{summary['unpredicted_completed_count']}`。",
        f"- 胜平负：`{summary['win_hits']}/{summary['prediction_count']}`，命中率 `{(summary['win_hits'] / summary['prediction_count'] * 100) if summary['prediction_count'] else 0:.1f}%`。",
        f"- 比分：`{summary['score_hits']}/{summary['prediction_count']}`，命中率 `{(summary['score_hits'] / summary['prediction_count'] * 100) if summary['prediction_count'] else 0:.1f}%`。",
        f"- 大小球：`{summary['ou_hits']}/{summary['ou_samples']}`，命中率 `{(summary['ou_hits'] / summary['ou_samples'] * 100) if summary['ou_samples'] else 0:.1f}%`。",
        "",
        "## 全局 Top 问题",
        "",
    ]

    global_top_problem_lines = _build_global_top_problem_lines(summary)
    if global_top_problem_lines:
        report_lines.extend(f"- {item}" for item in global_top_problem_lines)
    else:
        report_lines.append("- 当前窗口暂无显著全局问题，继续累积样本观察。")

    report_lines.extend(["", "## 联赛 Top 问题一览", ""])
    for league_code in LEAGUE_ORDER:
        league_name = LEAGUE_CN_NAMES.get(league_code, league_code)
        section = summary["league_sections"].get(league_code) or {}
        report_lines.extend(
            [
                "",
                f"### {league_name}",
                "",
                f"- 核心表现：已完赛 `{section.get('completed_count', 0)}`，可复盘 `{section.get('prediction_count', 0)}`，无预测 `{section.get('unpredicted_completed_count', 0)}`；"
                f"胜平负 `{section.get('win_hits', 0)}/{section.get('prediction_count', 0)}`，比分 `{section.get('score_hits', 0)}/{section.get('prediction_count', 0)}`，"
                f"大小球 `{section.get('ou_hits', 0)}/{section.get('ou_samples', 0)}`。",
                f"- Top 问题：`{section.get('top_problem_group_text') or '覆盖：无明显缺口；方向：无显著偏差；比分：无显著偏差；盘口：无显著风险'}`。",
                f"- 结论：{_build_league_conclusion(league_code, section)}",
                f"- 代表样本：`{_build_league_representative_examples(section)}`。",
                f"- 行动项：{_build_league_action_line(section)}",
            ]
        )

    report_lines.extend(["", "## 三层校准观察", ""])
    overall_rule_review = summary.get("calibration_rule_review", {}).get("overall") or []
    overall_rule_miss_reasons = summary.get("calibration_rule_review", {}).get("miss_reason_overall") or []
    if not overall_rule_review:
        report_lines.append("- 当前窗口暂无命中三层校准规则的可复盘样本。")
    else:
        fragments = [
            f"{item.get('rule_label')} {item.get('effective_count', 0)}/{item.get('hit_count', 0)} ({item.get('verdict')})"
            for item in overall_rule_review[:3]
        ]
        report_lines.append(f"- 已命中规则：`{' / '.join(fragments)}`。")
    if overall_rule_miss_reasons:
        miss_parts = [f"{item.get('reason_label')} x{item.get('count', 0)}" for item in overall_rule_miss_reasons[:5]]
        report_lines.append(f"- 未命中主因：`{' / '.join(miss_parts)}`。")

    overall_score_review = summary.get("score_ou_three_layer_review", {}).get("score_overall") or []
    overall_ou_review = summary.get("score_ou_three_layer_review", {}).get("ou_overall") or []
    if not overall_score_review:
        report_lines.append("- 比分三层校准：当前窗口暂无可统计样本。")
    else:
        fragments = [
            f"{item.get('rule_label')} {item.get('effective_count', 0)}/{item.get('sample_count', 0)} ({item.get('verdict')})"
            for item in overall_score_review[:3]
        ]
        report_lines.append(f"- 比分三层校准：`{' / '.join(fragments)}`。")
    if not overall_ou_review:
        report_lines.append("- 大小球三层校准：当前窗口暂无可统计样本。")
    else:
        fragments = [
            f"{item.get('rule_label')} {item.get('effective_count', 0)}/{item.get('sample_count', 0)} ({item.get('verdict')})"
            for item in overall_ou_review[:3]
        ]
        report_lines.append(f"- 大小球三层校准：`{' / '.join(fragments)}`。")

    report_lines.extend(["", "## 统一整改优先级", ""])
    overall_advice = summary.get("auto_remediation_advice") or []
    if overall_advice:
        for index, item in enumerate(overall_advice[:5], start=1):
            report_lines.append(f"{index}. {item}")
    else:
        report_lines.append("1. 当前窗口暂无需要新增的整改建议，保持现有校准规则并继续观察后续样本。")

    resolved_output = output_path or str(Path(base_dir) / "runtime" / f"recent_five_leagues_review_{summary['window_end']}.md")
    target_path = Path(resolved_output)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(report_lines).rstrip() + "\n", encoding="utf-8")
    return {
        **summary,
        "output_path": str(target_path),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成五大联赛最近窗口已完赛复盘报告")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="europe_leagues 根目录")
    parser.add_argument("--recent-days", type=int, default=7, help="统计窗口天数")
    parser.add_argument("--output-path", default="", help="输出 markdown 路径")
    parser.add_argument("--as-of-date", default="", help="统计截止日期 YYYY-MM-DD，默认今天")
    return parser


if __name__ == "__main__":
    cli_args = _build_arg_parser().parse_args()
    payload = build_recent_five_leagues_review(
        cli_args.base_dir,
        recent_days=cli_args.recent_days,
        output_path=cli_args.output_path or None,
        as_of_date=cli_args.as_of_date or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
