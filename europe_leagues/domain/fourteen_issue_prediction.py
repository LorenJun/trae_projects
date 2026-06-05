from __future__ import annotations

import html as html_module
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from domain.lightweight_prediction import predict_lightweight_match
from domain.predictor import DomainPredictor


SPORTTERY_NOTICE_INDEX_URL = "https://www.sporttery.cn/ctzc/zcgg/"
DEFAULT_OUTPUT_RELATIVE_PATH = Path("fourteen_matches") / "14场赛事预测.md"

FORMAL_LEAGUE_CODES = {
    "英超": "premier_league",
    "西甲": "la_liga",
    "意甲": "serie_a",
    "德甲": "bundesliga",
    "法甲": "ligue_1",
    "欧冠": "champions_league",
    "欧联": "europa_league",
    "欧协联": "conference_league",
    "世界杯": "world_cup",
}

LIGHTWEIGHT_LEAGUE_CODES = {
    "瑞超": "allsvenskan",
    "挪超": "eliteserien",
    "芬超": "veikkausliiga",
    "英冠": "championship",
    "葡超": "primeira_liga",
    "土超": "super_lig",
}


def _fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    return raw.decode("utf-8", "ignore")


def _clean_html_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    text = re.sub(r"</p\s*>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _latest_notice_url() -> str:
    html = _fetch_text(SPORTTERY_NOTICE_INDEX_URL)
    links = re.findall(r'<a href="([^"]+\.html)">([^<]+)</a>', html)
    for href, title in links:
        if "奖期竞猜场次安排" in title:
            return urljoin(SPORTTERY_NOTICE_INDEX_URL, href)
    raise RuntimeError("未找到最新足彩奖期竞猜场次安排公告")


def parse_issue_notice(issue: str, notice_url: str = "") -> Dict[str, Any]:
    normalized_issue = str(issue or "").strip().replace("第", "").replace("期", "")
    if not normalized_issue:
        raise ValueError("issue 不能为空")
    resolved_notice_url = str(notice_url or "").strip() or _latest_notice_url()
    html = _fetch_text(resolved_notice_url)
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.S | re.I)

    current_issue = ""
    current_league_name = ""
    matches: List[Dict[str, Any]] = []
    for row in rows:
        cells = [_clean_html_text(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        head = cells[0]
        if re.fullmatch(r"第?\d{5}期?", head):
            current_issue = head.replace("第", "").replace("期", "")
            current_league_name = ""
            cells = cells[1:]
        if current_issue != normalized_issue:
            continue
        if len(cells) >= 5:
            league_name, slot, home_team, away_team, match_date = cells[:5]
            current_league_name = league_name
        elif len(cells) == 4 and current_league_name:
            league_name = current_league_name
            slot, home_team, away_team, match_date = cells[:4]
        else:
            continue
        if not slot.isdigit():
            continue
        matches.append(
            {
                "issue": normalized_issue,
                "slot": int(slot),
                "league_name": league_name,
                "home_team": home_team,
                "away_team": away_team,
                "match_date": match_date,
            }
        )

    matches.sort(key=lambda item: item["slot"])
    if not matches:
        raise RuntimeError(f"未能在公告中解析到第{normalized_issue}期 14 场赛程")
    return {
        "issue": normalized_issue,
        "notice_url": resolved_notice_url,
        "match_count": len(matches),
        "matches": matches,
    }


def _issue_output_path(base_dir: str, explicit_output: str = "") -> Path:
    if explicit_output:
        return Path(explicit_output).expanduser().resolve()
    return (Path(base_dir).resolve() / DEFAULT_OUTPUT_RELATIVE_PATH).resolve()


def _resolve_mode(league_name: str) -> Dict[str, str]:
    if league_name in FORMAL_LEAGUE_CODES:
        return {
            "mode": "formal",
            "league_code": FORMAL_LEAGUE_CODES[league_name],
            "league_name": league_name,
        }
    return {
        "mode": "lightweight",
        "league_code": LIGHTWEIGHT_LEAGUE_CODES.get(league_name, "runtime_only"),
        "league_name": league_name,
    }


def _ensure_schedule_cache(base_dir: str, league_name: str, match_date: str) -> None:
    script_path = Path(base_dir).resolve() / "okooo_fetch_daily_schedule.py"
    command = [
        sys.executable,
        str(script_path),
        "--league",
        league_name,
        "--date",
        match_date,
    ]
    subprocess.run(
        command,
        cwd=base_dir,
        check=False,
        capture_output=True,
        text=True,
        timeout=240,
    )


def _probability_summary(result: Dict[str, Any]) -> str:
    final_probabilities = result.get("final_probabilities") if isinstance(result.get("final_probabilities"), dict) else {}
    all_probabilities = result.get("all_probabilities") if isinstance(result.get("all_probabilities"), dict) else {}
    if final_probabilities:
        return (
            f"主{float(final_probabilities.get('home_win') or 0.0):.1%} / "
            f"平{float(final_probabilities.get('draw') or 0.0):.1%} / "
            f"客{float(final_probabilities.get('away_win') or 0.0):.1%}"
        )
    if all_probabilities:
        return (
            f"主{float(all_probabilities.get('主胜') or 0.0):.1%} / "
            f"平{float(all_probabilities.get('平局') or 0.0):.1%} / "
            f"客{float(all_probabilities.get('客胜') or 0.0):.1%}"
        )
    return "-"


def _score_summary(result: Dict[str, Any]) -> str:
    top_scores = result.get("top_scores") if isinstance(result.get("top_scores"), list) else []
    scores: List[str] = []
    for item in top_scores[:3]:
        if isinstance(item, (list, tuple)) and item:
            scores.append(str(item[0]))
        else:
            text = str(item or "").strip()
            if text:
                scores.append(text)
    return " > ".join(scores) if scores else "-"


def _ou_summary(result: Dict[str, Any]) -> str:
    over_under = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
    if over_under.get("available"):
        line = over_under.get("line")
        line_label = f"{float(line):g}" if isinstance(line, (int, float)) else "?"
        over = float(over_under.get("over") or 0.0)
        under = float(over_under.get("under") or 0.0)
        direction = "大球" if over > under else "小球"
        confidence = max(over, under)
        return f"{direction} {line_label} ({confidence:.1%})"
    reason = str(over_under.get("reason") or "").strip()
    return f"待补真实盘口({reason})" if reason else "-"


def _note_summary(result: Dict[str, Any]) -> str:
    if result.get("prediction_blocked"):
        reason = str(result.get("blocked_reason") or "missing_real_market_line").strip()
        return f"数据不完整: {reason}"
    risk = result.get("upset_potential") if isinstance(result.get("upset_potential"), dict) else {}
    factors = [str(item).strip() for item in (risk.get("factors") or []) if str(item).strip()]
    if factors:
        return " / ".join(factors[:2]).replace("|", "/")
    explanation = str(result.get("retrieved_memory_explanation") or "").strip()
    return explanation.replace("|", "/") if explanation else "-"


def _prediction_label(result: Dict[str, Any]) -> str:
    return str(result.get("prediction") or "数据不完整").strip() or "数据不完整"


def _confidence_value(result: Dict[str, Any]) -> float:
    try:
        return float(result.get("confidence") or 0.0)
    except Exception:
        return 0.0


def _final_probabilities(result: Dict[str, Any]) -> Dict[str, float]:
    raw = result.get("final_probabilities") if isinstance(result.get("final_probabilities"), dict) else {}
    if raw:
        return {
            "主胜": float(raw.get("home_win") or 0.0),
            "平局": float(raw.get("draw") or 0.0),
            "客胜": float(raw.get("away_win") or 0.0),
        }
    all_probs = result.get("all_probabilities") if isinstance(result.get("all_probabilities"), dict) else {}
    return {
        "主胜": float(all_probs.get("主胜") or 0.0),
        "平局": float(all_probs.get("平局") or 0.0),
        "客胜": float(all_probs.get("客胜") or 0.0),
    }


def _ou_detail(result: Dict[str, Any]) -> Dict[str, Any]:
    over_under = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
    if not over_under.get("available"):
        return {"available": False, "direction": "缺失", "line": None, "confidence": 0.0}
    over = float(over_under.get("over") or 0.0)
    under = float(over_under.get("under") or 0.0)
    return {
        "available": True,
        "direction": "大球" if over > under else "小球",
        "line": over_under.get("line"),
        "confidence": max(over, under),
    }


def _confidence_bucket(value: float) -> str:
    if value >= 0.6:
        return "高置信(>=60%)"
    if value >= 0.45:
        return "中高置信(45%-60%)"
    if value >= 0.38:
        return "均衡偏向(38%-45%)"
    return "低置信(<38%)"


def _safe_pct(value: float) -> str:
    return f"{value:.1%}"


def _ordered_probability_labels(probs: Dict[str, float]) -> List[str]:
    return [label for label, _ in sorted(probs.items(), key=lambda item: item[1], reverse=True)]


def _basic_surface_label(prediction: str, confidence: float, gap: float) -> str:
    if gap < 0.08:
        return "基础面均衡，首选优势很薄"
    if confidence >= 0.60 and gap >= 0.30:
        return f"{prediction}基础面压制明显"
    if confidence >= 0.50:
        return f"{prediction}基础面占优但非绝对"
    return f"{prediction}轻微占优，容错偏低"


def _upset_reassessment(row: Dict[str, Any]) -> Dict[str, Any]:
    prediction = str(row.get("prediction") or "")
    confidence = float(row.get("confidence") or 0.0)
    gap = float(row.get("gap") or 0.0)
    note = str(row.get("note") or "")
    league = str(row.get("league") or "")
    ou = row.get("ou") if isinstance(row.get("ou"), dict) else {}
    probs = row.get("probs") if isinstance(row.get("probs"), dict) else {}
    ranked_labels = _ordered_probability_labels(probs)
    second_choice = ranked_labels[1] if len(ranked_labels) > 1 else "平局"
    third_choice = ranked_labels[2] if len(ranked_labels) > 2 else "客胜"
    high_variance_leagues = {"瑞超", "挪超", "芬超", "英冠", "葡超", "土超"}

    score = 18
    triggers: List[str] = []
    if league in high_variance_leagues:
        score += 8
        triggers.append("次级/北欧联赛波动")
    if gap < 0.06:
        score += 30
        triggers.append("概率差极小")
    elif gap < 0.10:
        score += 22
        triggers.append("概率差偏小")
    elif gap < 0.15:
        score += 14
        triggers.append("首选优势不厚")
    if confidence < 0.45:
        score += 18
        triggers.append("首选置信不足45%")
    elif confidence < 0.55:
        score += 10
        triggers.append("中低置信")
    if "欧赔走弱主队" in note or "盘口退盘" in note:
        score += 18
        triggers.append("热门方向赔率/盘口逆向")
    if "防热度集中" in note:
        score += 8
        triggers.append("热门热度集中")
    if "升盘配高水" in note:
        score += 10
        triggers.append("让步阻力")
    if bool(ou.get("available")):
        try:
            line = float(ou.get("line") or 0.0)
        except Exception:
            line = 0.0
        if line >= 3.0:
            score += 5
            triggers.append("高进球线增加赛果离散")
    if confidence >= 0.60 and gap >= 0.35 and "热门方向赔率/盘口逆向" not in triggers:
        score -= 12
        triggers.append("基础面压制抵消部分冷意")

    if score >= 58:
        level = "高"
    elif score >= 46:
        level = "中高"
    elif score >= 34:
        level = "中"
    else:
        level = "低"

    if prediction == "主胜":
        if gap < 0.08:
            cover = f"首选3，重点防{second_choice}，大复式带{third_choice}"
        elif "欧赔走弱主队" in note or "盘口退盘" in note:
            cover = "首选3，降胆，优先防平(3/1)"
        else:
            cover = "主胜可保留，冷门只做小防平"
    elif prediction == "客胜":
        if gap < 0.08:
            cover = f"首选0，重点防{second_choice}，大复式带{third_choice}"
        else:
            cover = "客胜方向可留，优先防平(0/1)"
    elif prediction == "平局":
        cover = "平局作冷门核心，胜负两端按赔率补防"
    else:
        cover = "数据不足，建议全包或跳过"

    return {
        "level": level,
        "score": max(0, min(100, score)),
        "basic": _basic_surface_label(prediction, confidence, gap),
        "triggers": "、".join(triggers[:4]) if triggers else "暂无明显冷门触发",
        "cover": cover,
    }


def _selection_advice(result: Dict[str, Any]) -> str:
    label = _prediction_label(result)
    confidence = _confidence_value(result)
    probs = _final_probabilities(result)
    ranked = sorted(probs.values(), reverse=True)
    gap = ranked[0] - ranked[1] if len(ranked) >= 2 else 0.0
    note = _note_summary(result)
    has_home_drift = "欧赔走弱主队" in note or "盘口退盘" in note
    has_hot_risk = "防热度集中" in note

    if gap < 0.08:
        return f"{label}仅小幅领先，建议复选/防冷"
    if label == "主胜":
        if confidence >= 0.60 and has_home_drift:
            return "主胜仍为首选，但降胆级别，复式优先防平(3/1)"
        if confidence >= 0.60:
            return "主胜可作首选胆，热度高时防平"
        if has_home_drift:
            return "主胜倾向但盘口有逆向信号，建议防平"
        if has_hot_risk:
            return "主胜首选，防热门过热"
        return "主胜首选，谨慎单选"
    if label == "客胜":
        if confidence >= 0.55:
            return "客胜首选，防平优先"
        return "客胜倾向，建议复选防平"
    if label == "平局":
        return "平局为首选信号，建议搭配胜负复选"
    return "数据不足，不建议单选"


def _render_detailed_analysis(report: Dict[str, Any]) -> str:
    predictions = [item for item in report.get("predictions", []) if isinstance(item, dict) and not item.get("error")]
    if not predictions:
        return "### 数据分析\n\n- 暂无可分析的成功预测场次。\n"

    prediction_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    league_counts: Counter[str] = Counter()
    confidence_buckets: Counter[str] = Counter()
    ou_counts: Counter[str] = Counter()
    risk_counts: Counter[str] = Counter()
    league_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "confidence_sum": 0.0, "predictions": Counter()})
    rows_for_ranking: List[Dict[str, Any]] = []
    missing_ou = 0
    total_confidence = 0.0

    for item in predictions:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        label = _prediction_label(result)
        confidence = _confidence_value(result)
        total_confidence += confidence
        league = str(item.get("league_name") or "-")
        mode = str(item.get("mode") or "-")
        ou = _ou_detail(result)
        note = _note_summary(result)

        prediction_counts[label] += 1
        mode_counts[mode] += 1
        league_counts[league] += 1
        confidence_buckets[_confidence_bucket(confidence)] += 1
        if ou["available"]:
            ou_counts[str(ou["direction"])] += 1
        else:
            missing_ou += 1
        for factor in [part.strip() for part in note.split("/") if part.strip() and part.strip() != "-"]:
            risk_counts[factor] += 1

        league_stats[league]["count"] += 1
        league_stats[league]["confidence_sum"] += confidence
        league_stats[league]["predictions"][label] += 1
        probs = _final_probabilities(result)
        ranked_probs = sorted(probs.values(), reverse=True)
        gap = ranked_probs[0] - ranked_probs[1] if len(ranked_probs) >= 2 else 0.0
        rows_for_ranking.append(
            {
                "slot": item.get("slot"),
                "league": league,
                "match": f"{item.get('home_team')} vs {item.get('away_team')}",
                "prediction": label,
                "confidence": confidence,
                "gap": gap,
                "probs": probs,
                "ou": ou,
                "note": note,
                "advice": _selection_advice(result),
            }
        )

    total = len(predictions)
    avg_confidence = total_confidence / total if total else 0.0
    high_confidence = sorted(rows_for_ranking, key=lambda row: row["confidence"], reverse=True)[:5]
    balanced = sorted(rows_for_ranking, key=lambda row: row["gap"])[:5]
    ou_total = sum(ou_counts.values())

    lines = [
        "### 数据分析",
        "",
        "#### 总体倾向",
        "",
        f"- 本期成功纳入分析 `{total}` 场，平均置信度 `{_safe_pct(avg_confidence)}`。",
        "- 胜平负倾向: "
        + " / ".join(f"{key}{value}场({_safe_pct(value / total)})" for key, value in prediction_counts.most_common()),
        "- 预测模式: " + " / ".join(f"{key}{value}场" for key, value in mode_counts.most_common()),
        "- 置信度分层: "
        + " / ".join(f"{key}{value}场" for key, value in confidence_buckets.most_common()),
        "",
        "#### 联赛分布",
        "",
        "| 联赛 | 场次 | 主胜 | 平局 | 客胜 | 平均置信度 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for league, stat in sorted(league_stats.items(), key=lambda kv: (-int(kv[1]["count"]), kv[0])):
        count = int(stat["count"])
        pred_counter = stat["predictions"]
        avg = float(stat["confidence_sum"]) / count if count else 0.0
        lines.append(
            f"| {league} | {count} | {pred_counter.get('主胜', 0)} | {pred_counter.get('平局', 0)} | {pred_counter.get('客胜', 0)} | {_safe_pct(avg)} |"
        )

    lines.extend(
        [
            "",
            "#### 大小球与进球线",
            "",
            "- 大小球倾向: "
            + (" / ".join(f"{key}{value}场({_safe_pct(value / ou_total)})" for key, value in ou_counts.most_common()) if ou_total else "暂无真实大小球线"),
            f"- 缺失真实大小球盘口: `{missing_ou}` 场。",
            "",
            "| 场次 | 对阵 | 方向 | 盘口 | 强度 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows_for_ranking:
        ou = row["ou"]
        if not ou["available"]:
            continue
        line = ou.get("line")
        line_label = f"{float(line):g}" if isinstance(line, (int, float)) else "-"
        lines.append(
            f"| {row['slot']} | {row['match']} | {ou['direction']} | {line_label} | {_safe_pct(float(ou.get('confidence') or 0.0))} |"
        )

    lines.extend(
        [
            "",
            "#### 高置信方向",
            "",
            "| 场次 | 联赛 | 对阵 | 预测 | 置信度 | 概率差 | 主要风险提示 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in high_confidence:
        lines.append(
            f"| {row['slot']} | {row['league']} | {row['match']} | {row['prediction']} | {_safe_pct(row['confidence'])} | {_safe_pct(row['gap'])} | {str(row['note']).replace('|', '/')}；{row['advice']} |"
        )

    lines.extend(
        [
            "",
            "#### 均衡与防冷场次",
            "",
            "| 场次 | 联赛 | 对阵 | 首选 | 概率差 | 处理建议 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in balanced:
        advice = "建议复选/防平负波动" if row["gap"] < 0.08 else "首选可用，但不宜做强胆"
        lines.append(
            f"| {row['slot']} | {row['league']} | {row['match']} | {row['prediction']} | {_safe_pct(row['gap'])} | {advice} |"
        )

    upset_rows = sorted(
        [{**row, "upset": _upset_reassessment(row)} for row in rows_for_ranking],
        key=lambda row: (-int(row["upset"]["score"]), int(row["slot"] or 0)),
    )
    lines.extend(
        [
            "",
            "#### 爆冷重评",
            "",
            "- 口径: 次级/北欧联赛默认提高波动权重，再叠加基础面强弱、概率差、赔率/盘口逆向、热度集中和大小球离散度。",
            "- 爆冷定义: 相对当前首选赛果的反向或平局风险，不等于直接推翻首选。",
            "",
            "| 场次 | 联赛 | 对阵 | 首选 | 爆冷等级 | 冷门指数 | 基础数据面 | 触发因素 | 复选建议 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in upset_rows:
        upset = row["upset"]
        lines.append(
            f"| {row['slot']} | {row['league']} | {row['match']} | {row['prediction']} | {upset['level']} | {upset['score']} | {upset['basic']} | {upset['triggers']} | {upset['cover']} |"
        )

    if risk_counts:
        lines.extend(["", "#### 风险信号 Top", ""])
        for factor, count in risk_counts.most_common(8):
            lines.append(f"- `{factor}`: {count} 场")

    lines.append("")
    return "\n".join(lines)


def _render_issue_section(report: Dict[str, Any]) -> str:
    issue = report["issue"]
    generated_at = report["generated_at"]
    notice_url = report["notice_url"]
    rows = [
        f"## 第{issue}期",
        "",
        f"- 生成时间: `{generated_at}`",
        f"- 公告来源: [sporttery]({notice_url})",
        f"- 预测场次: `{report['match_count']}`",
        f"- 成功: `{report['success_count']}`",
        f"- 失败: `{report['error_count']}`",
        f"- 阻断: `{report['blocked_count']}`",
        "",
        "| 场次 | 联赛 | 对阵 | 日期 | 模式 | 预测 | 概率 | 比分 | 大小球 | 备注 | 处理建议 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["predictions"]:
        match_label = f"{item['home_team']} vs {item['away_team']}"
        if item.get("error"):
            rows.append(
                f"| {item['slot']} | {item['league_name']} | {match_label} | {item['match_date']} | {item['mode']} | 失败 | - | - | - | {str(item['error']).replace('|', '/')} | 暂不选择 |"
            )
            continue
        result = item["result"]
        prediction = str(result.get("prediction") or "数据不完整")
        confidence = float(result.get("confidence") or 0.0)
        rows.append(
            "| {slot} | {league} | {match_label} | {match_date} | {mode} | {prediction} ({confidence:.1%}) | {probabilities} | {scores} | {ou} | {note} | {advice} |".format(
                slot=item["slot"],
                league=item["league_name"],
                match_label=match_label,
                match_date=item["match_date"],
                mode=item["mode"],
                prediction=prediction,
                confidence=confidence,
                probabilities=_probability_summary(result).replace("|", "/"),
                scores=_score_summary(result).replace("|", "/"),
                ou=_ou_summary(result).replace("|", "/"),
                note=_note_summary(result).replace("|", "/"),
                advice=_selection_advice(result).replace("|", "/"),
            )
        )
    rows.append("")
    rows.append(_render_detailed_analysis(report))
    return "\n".join(rows)


def write_issue_markdown(report: Dict[str, Any], base_dir: str, output_path: str = "") -> str:
    path = _issue_output_path(base_dir, output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    start_marker = f"<!-- ctzc14:start:{report['issue']} -->"
    end_marker = f"<!-- ctzc14:end:{report['issue']} -->"
    section = f"{start_marker}\n{_render_issue_section(report)}\n{end_marker}\n"
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = "# 14场赛事预测\n\n> 该文件用于独立记录传统足彩 14 场期次预测，不写入五大联赛 `teams_*.md`。\n\n"
    block_pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}\n?",
        flags=re.S,
    )
    if block_pattern.search(content):
        content = block_pattern.sub(section, content, count=1)
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + section
    path.write_text(content, encoding="utf-8")
    return str(path)


def analyze_issue(
    *,
    base_dir: str,
    issue: str,
    notice_url: str = "",
    output_path: str = "",
    force_refresh_odds: bool = True,
    okooo_driver: str = "local-chrome",
    okooo_headed: bool = False,
    write_output: bool = True,
) -> Dict[str, Any]:
    issue_payload = parse_issue_notice(issue, notice_url=notice_url)
    predictor = DomainPredictor(base_dir=base_dir)

    unique_pairs = {(item["league_name"], item["match_date"]) for item in issue_payload["matches"]}
    for league_name, match_date in sorted(unique_pairs):
        try:
            _ensure_schedule_cache(base_dir, league_name, match_date)
        except (OSError, subprocess.SubprocessError, URLError):
            continue

    predictions: List[Dict[str, Any]] = []
    success_count = 0
    blocked_count = 0
    error_count = 0

    for match in issue_payload["matches"]:
        mode_info = _resolve_mode(match["league_name"])
        try:
            if mode_info["mode"] == "formal":
                result = predictor.predict_match(
                    home_team=match["home_team"],
                    away_team=match["away_team"],
                    league_code=mode_info["league_code"],
                    match_date=match["match_date"],
                    match_time="",
                    match_id="",
                    force_refresh_odds=force_refresh_odds,
                    okooo_driver=okooo_driver,
                    okooo_headed=okooo_headed,
                    persist=False,
                )
            else:
                result = predict_lightweight_match(
                    base_dir=base_dir,
                    league_name=mode_info["league_name"],
                    league_code=mode_info["league_code"],
                    home_team=match["home_team"],
                    away_team=match["away_team"],
                    match_date=match["match_date"],
                    match_time="",
                    match_id="",
                    okooo_driver=okooo_driver,
                    okooo_headed=okooo_headed,
                )
            result["issue"] = issue_payload["issue"]
            result["issue_slot"] = match["slot"]
            result["issue_mode"] = mode_info["mode"]
            predictions.append({**match, "mode": mode_info["mode"], "result": result})
            if result.get("prediction_blocked"):
                blocked_count += 1
            else:
                success_count += 1
        except Exception as exc:
            error_count += 1
            predictions.append(
                {
                    **match,
                    "mode": mode_info["mode"],
                    "error": str(exc),
                }
            )

    report = {
        "issue": issue_payload["issue"],
        "notice_url": issue_payload["notice_url"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "match_count": len(issue_payload["matches"]),
        "success_count": success_count,
        "blocked_count": blocked_count,
        "error_count": error_count,
        "predictions": predictions,
        "output_path": "",
    }
    if write_output:
        report["output_path"] = write_issue_markdown(report, base_dir=base_dir, output_path=output_path)
    return report


def analyze_issue_as_json(
    *,
    base_dir: str,
    issue: str,
    notice_url: str = "",
    output_path: str = "",
    force_refresh_odds: bool = True,
    okooo_driver: str = "local-chrome",
    okooo_headed: bool = False,
    write_output: bool = True,
) -> Dict[str, Any]:
    report = analyze_issue(
        base_dir=base_dir,
        issue=issue,
        notice_url=notice_url,
        output_path=output_path,
        force_refresh_odds=force_refresh_odds,
        okooo_driver=okooo_driver,
        okooo_headed=okooo_headed,
        write_output=write_output,
    )
    return json.loads(json.dumps(report, ensure_ascii=False))
