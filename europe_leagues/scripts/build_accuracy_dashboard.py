#!/usr/bin/env python3
"""预测准确率仪表盘生成器。

复用当前预测流程的 ResultManager 真实统计（overall / by_league / 已判定赛事明细），
导出一个**自包含、可在浏览器直接打开**的 HTML 仪表盘 accuracy_dashboard.html。

- 不发起任何网络请求、不改动任何数据，只读统计。
- 数据口径与 `prediction_system.py accuracy` 完全一致（同一 ResultManager）。

用法：
    python3 scripts/build_accuracy_dashboard.py
    python3 scripts/build_accuracy_dashboard.py --days 3650 --output accuracy_dashboard.html
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from domain.note_parsing import (  # noqa: E402
    parse_predicted_narrative,
    parse_predicted_ou,
    parse_predicted_scores,
    parse_predicted_stake,
    parse_predicted_winner,
)
from result_manager import LEAGUE_NAMES, LEAGUE_SHORT_NAMES, ResultManager  # noqa: E402

WINNER_TEXT = {"home": "主胜", "draw": "平局", "away": "客胜"}

# 三大统计分组
FIVE_LEAGUES = ("premier_league", "la_liga", "serie_a", "bundesliga", "ligue_1")


def _group_of(code: str | None) -> str:
    """把联赛代码归入三大分组之一：five / world_cup / others。"""
    if code in FIVE_LEAGUES:
        return "five"
    if code == "world_cup":
        return "world_cup"
    return "others"


def _empty_agg() -> dict:
    return {
        "total": 0,
        "correct": 0,
        "total_score": 0,
        "correct_score": 0,
        "total_ou": 0,
        "correct_ou": 0,
    }


def _finalize_agg(agg: dict) -> dict:
    agg["win_accuracy"] = round(agg["correct"] / agg["total"] * 100, 2) if agg["total"] else 0.0
    agg["score_accuracy"] = round(agg["correct_score"] / agg["total_score"] * 100, 2) if agg["total_score"] else 0.0
    agg["ou_accuracy"] = round(agg["correct_ou"] / agg["total_ou"] * 100, 2) if agg["total_ou"] else 0.0
    return agg


def _winner_label(home: str, away: str, code: str | None) -> str:
    if code == "home":
        return f"{home} 胜"
    if code == "away":
        return f"{away} 胜"
    if code == "draw":
        return "平局"
    return "—"


def _normalize_ou(value) -> dict | None:
    """把预测大小球归一为 {'side': '大'/'小', 'line': float}。兼容多种存储形态。"""
    if not value:
        return None
    if isinstance(value, dict):
        side = str(value.get("side") or "").strip()
        line = value.get("line")
        if side in ("大", "小") and line is not None:
            try:
                return {"side": side, "line": float(line)}
            except (TypeError, ValueError):
                return None
        return None
    text = str(value).strip()
    if not text:
        return None
    side = "大" if "大" in text else ("小" if "小" in text else "")
    if not side:
        return None
    import re as _re

    m = _re.search(r"(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    return {"side": side, "line": float(m.group(1))}


def _parse_score_pair(score: str) -> tuple[int, int] | None:
    import re as _re

    m = _re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(score or ""))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _fmt_line(line: float) -> str:
    return str(int(line)) if float(line).is_integer() else str(line)


def collect_dashboard_data(rm: ResultManager, days: int) -> dict:
    """从 ResultManager 拉取真实统计，整理为前端需要的结构。

    准确率拆为三大分组：five（五大联赛）/ world_cup（世界杯）/ others（非五大联赛）。
    另附赛程（未结束赛事）滚动数据，预测从赛程备注中解析。
    """
    overall = rm.calculate_accuracy(days=days)

    # 各联赛准确率（带分组归属）
    by_league = []
    for code in LEAGUE_NAMES:
        stats = rm.calculate_accuracy(league=code, days=days)
        if int(stats.get("total_predictions", 0) or 0) <= 0:
            continue
        by_league.append(
            {
                "code": code,
                "group": _group_of(code),
                "name": LEAGUE_NAMES.get(code, code),
                "short": LEAGUE_SHORT_NAMES.get(code, code),
                "total": int(stats.get("total_predictions", 0) or 0),
                "correct": int(stats.get("correct_predictions", 0) or 0),
                "win_accuracy": float(stats.get("win_accuracy", 0.0) or 0.0),
                "score_accuracy": float(stats.get("score_accuracy", 0.0) or 0.0),
                "ou_accuracy": float(stats.get("ou_accuracy", 0.0) or 0.0),
                "total_ou": int(stats.get("total_ou_predictions", 0) or 0),
            }
        )
    by_league.sort(key=lambda x: (-x["total"], -x["win_accuracy"]))

    # 三大分组聚合
    groups = {"five": _empty_agg(), "world_cup": _empty_agg(), "others": _empty_agg()}

    matches = []
    samples = rm._build_unified_prediction_samples(days=days)
    for mid, s in samples.items():
        pw = s.get("predicted_winner")
        aw = s.get("actual_winner")
        if pw not in WINNER_TEXT or aw not in WINNER_TEXT:
            continue
        code = s.get("league")
        grp = _group_of(code)
        pred_scores = [str(x).strip() for x in (s.get("predicted_scores") or []) if str(x).strip()]
        actual_score = str(s.get("actual_score") or "").strip()
        score_hit = bool(pred_scores) and actual_score in pred_scores

        # 逐场大小球判定：预测方向/盘口 vs 实际总进球
        ou = _normalize_ou(s.get("predicted_ou"))
        ou_pred_label = ""
        ou_actual_label = ""
        ou_status = ""  # hit / miss / push / ""
        score_pair = _parse_score_pair(actual_score)
        if ou:
            ou_pred_label = f"{ou['side']}{_fmt_line(ou['line'])}"
            if score_pair is not None:
                total = score_pair[0] + score_pair[1]
                line = float(ou["line"])
                if abs(total - line) < 1e-9:
                    ou_status = "push"
                    ou_actual_label = f"{total}球 走水"
                else:
                    actual_side = "大" if total > line else "小"
                    ou_status = "hit" if actual_side == ou["side"] else "miss"
                    ou_actual_label = f"{total}球·{actual_side}"

        # 累加到所属分组
        agg = groups[grp]
        agg["total"] += 1
        if pw == aw:
            agg["correct"] += 1
        if pred_scores:
            agg["total_score"] += 1
            if score_hit:
                agg["correct_score"] += 1

        matches.append(
            {
                "match_id": mid,
                "league": code,
                "group": grp,
                "league_short": LEAGUE_SHORT_NAMES.get(code, code or "—"),
                "date": s.get("match_date") or "",
                "time": s.get("match_time") or "",
                "home": s.get("home_team") or "",
                "away": s.get("away_team") or "",
                "predicted_winner": pw,
                "predicted_winner_label": _winner_label(s.get("home_team") or "", s.get("away_team") or "", pw),
                "actual_winner": aw,
                "actual_winner_label": _winner_label(s.get("home_team") or "", s.get("away_team") or "", aw),
                "win_hit": pw == aw,
                "predicted_scores": pred_scores,
                "actual_score": actual_score,
                "score_hit": score_hit,
                "has_score": bool(pred_scores) and bool(actual_score),
                "ou_pred_label": ou_pred_label,
                "ou_actual_label": ou_actual_label,
                "ou_status": ou_status,
            }
        )
    matches.sort(key=lambda m: (m["date"], m["time"]), reverse=True)

    # 大小球命中并入分组（calculate_accuracy 的口径，已含在 by_league 中，按联赛回填分组）
    for lg in by_league:
        agg = groups[lg["group"]]
        agg["total_ou"] += lg["total_ou"]
        # 由 ou_accuracy 反推命中场次
        agg["correct_ou"] += int(round(lg["ou_accuracy"] / 100.0 * lg["total_ou"]))

    for key in groups:
        _finalize_agg(groups[key])

    # 赛程（未结束赛事），预测从备注解析
    schedule = []
    for row in rm._iter_teams_rows():
        score_text = str(row.get("score_text") or "").strip()
        if score_text and score_text not in ("-", "—", "VS", "vs"):
            continue  # 已有比分=已结束，归入历史不进赛程
        note = str(row.get("note") or "")
        code = row.get("league")
        pw = parse_predicted_winner(note)
        ou = parse_predicted_ou(note)
        scores = parse_predicted_scores(note)
        stake = parse_predicted_stake(note)
        narrative = parse_predicted_narrative(note)
        schedule.append(
            {
                "league": code,
                "group": _group_of(code),
                "league_short": LEAGUE_SHORT_NAMES.get(code, code or "—"),
                "date": row.get("match_date") or "",
                "time": row.get("match_time") or "",
                "home": row.get("home_team") or "",
                "away": row.get("away_team") or "",
                "status": note.strip()[:12] if note.strip() else "未开始",
                "predicted_winner_label": _winner_label(
                    row.get("home_team") or "", row.get("away_team") or "", pw
                )
                if pw
                else "",
                "predicted_scores": scores,
                "predicted_ou": (f"{ou['side']}{ou['line']}" if ou else ""),
                "predicted_stake": stake or "",
                "narrative": narrative or "",
            }
        )
    schedule.sort(key=lambda m: (m["date"], m["time"]))

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "overall": {
            "total": int(overall.get("total_predictions", 0) or 0),
            "correct": int(overall.get("correct_predictions", 0) or 0),
            "win_accuracy": float(overall.get("win_accuracy", 0.0) or 0.0),
            "score_accuracy": float(overall.get("score_accuracy", 0.0) or 0.0),
            "correct_score": int(overall.get("correct_score_predictions", 0) or 0),
            "total_score": int(overall.get("total_score_predictions", 0) or 0),
            "ou_accuracy": float(overall.get("ou_accuracy", 0.0) or 0.0),
            "total_ou": int(overall.get("total_ou_predictions", 0) or 0),
            "correct_ou": int(overall.get("correct_ou_predictions", 0) or 0),
        },
        "groups": groups,
        "by_league": by_league,
        "matches": matches,
        "schedule": schedule,
    }


def render_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    # 数据通过 <script> JSON 注入，前端纯静态渲染，无外部依赖。
    return _HTML_TEMPLATE.replace("__DASHBOARD_DATA__", payload)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>足球预测准确率仪表盘</title>
<style>
  :root {
    --bg: #070b16;
    --bg-soft: #0e1626;
    --card: rgba(255,255,255,0.045);
    --card-border: rgba(255,255,255,0.09);
    --text: #eef2ff;
    --muted: #8a96b3;
    --accent: #5b8cff;
    --accent2: #22d3ee;
    --green: #34d399;
    --red: #fb7185;
    --amber: #fbbf24;
    --shadow: 0 18px 48px rgba(0,0,0,0.45);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: radial-gradient(1200px 700px at 12% -8%, #16224a 0%, transparent 55%),
                radial-gradient(1000px 600px at 100% 0%, #0c2e3a 0%, transparent 50%),
                var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 0 0 80px;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 22px; }

  header.hero {
    padding: 56px 0 30px;
    text-align: center;
  }
  .hero h1 {
    font-size: clamp(26px, 4.4vw, 44px);
    font-weight: 800;
    letter-spacing: 0.5px;
    background: linear-gradient(92deg, #fff 0%, #9ec2ff 55%, #5be0ff 100%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .hero p { margin-top: 12px; color: var(--muted); font-size: 14px; }
  .hero .meta { margin-top: 18px; display: inline-flex; gap: 10px; flex-wrap: wrap; justify-content: center; }
  .pill {
    font-size: 12px; color: #cdd8f5; padding: 6px 14px; border-radius: 999px;
    background: var(--card); border: 1px solid var(--card-border);
  }

  .kpis {
    display: grid; gap: 18px; margin-top: 14px;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  }
  .kpi {
    position: relative; overflow: hidden;
    background: var(--card); border: 1px solid var(--card-border);
    border-radius: 20px; padding: 24px 22px; box-shadow: var(--shadow);
    backdrop-filter: blur(8px);
    transition: transform .25s ease, border-color .25s ease;
  }
  .kpi:hover { transform: translateY(-4px); border-color: rgba(91,140,255,0.5); }
  .kpi .label { font-size: 13px; color: var(--muted); display: flex; align-items: center; gap: 8px; }
  .kpi .value { font-size: 40px; font-weight: 800; margin-top: 10px; line-height: 1; }
  .kpi .value small { font-size: 18px; font-weight: 700; opacity: .7; }
  .kpi .sub { margin-top: 10px; font-size: 12.5px; color: var(--muted); }
  .ring {
    position: absolute; right: -26px; top: -26px; width: 110px; height: 110px;
    border-radius: 50%; opacity: .22; filter: blur(2px);
  }
  .kpi.win .ring { background: radial-gradient(circle, var(--accent), transparent 70%); }
  .kpi.score .ring { background: radial-gradient(circle, var(--amber), transparent 70%); }
  .kpi.ou .ring { background: radial-gradient(circle, var(--accent2), transparent 70%); }
  .kpi.total .ring { background: radial-gradient(circle, var(--green), transparent 70%); }

  .progress { height: 7px; border-radius: 999px; background: rgba(255,255,255,0.08); margin-top: 14px; overflow: hidden; }
  .progress > span { display: block; height: 100%; border-radius: 999px; }
  .bar-win { background: linear-gradient(90deg, var(--accent), #8ab4ff); }
  .bar-score { background: linear-gradient(90deg, var(--amber), #ffe08a); }
  .bar-ou { background: linear-gradient(90deg, var(--accent2), #7defff); }

  .section { margin-top: 46px; }
  .section-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
  .section-head h2 { font-size: 19px; font-weight: 700; }
  .section-head .hint { font-size: 12.5px; color: var(--muted); }

  /* 联赛横向滑动 */
  .scroller {
    display: flex; gap: 16px; overflow-x: auto; padding: 6px 2px 18px;
    scroll-snap-type: x mandatory; -webkit-overflow-scrolling: touch;
  }
  .scroller::-webkit-scrollbar { height: 8px; }
  .scroller::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.16); border-radius: 999px; }
  .league-card {
    scroll-snap-align: start; flex: 0 0 230px; min-width: 230px;
    background: var(--card); border: 1px solid var(--card-border);
    border-radius: 18px; padding: 20px; box-shadow: var(--shadow);
    transition: transform .2s ease, border-color .2s ease;
  }
  .league-card:hover { transform: translateY(-3px); border-color: rgba(34,211,238,0.45); }
  .league-card .lg-name { font-size: 16px; font-weight: 700; display: flex; align-items: center; justify-content: space-between; }
  .league-card .badge { font-size: 11px; padding: 3px 9px; border-radius: 999px; background: rgba(91,140,255,0.16); color: #aec3ff; }
  .league-card .big { font-size: 32px; font-weight: 800; margin: 14px 0 2px; }
  .league-card .rowline { display: flex; justify-content: space-between; font-size: 12.5px; color: var(--muted); margin-top: 8px; }
  .league-card .rowline b { color: var(--text); font-weight: 600; }

  /* 过滤器 */
  .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .chip {
    font-size: 13px; padding: 7px 15px; border-radius: 999px; cursor: pointer; user-select: none;
    background: var(--card); border: 1px solid var(--card-border); color: var(--muted);
    transition: all .18s ease;
  }
  .chip:hover { color: var(--text); }
  .chip.active { background: linear-gradient(92deg, var(--accent), var(--accent2)); color: #04101f; border-color: transparent; font-weight: 700; }

  /* 赛事明细滚动列表 */
  .matches {
    max-height: 560px; overflow-y: auto; padding-right: 6px;
    display: flex; flex-direction: column; gap: 12px;
    scroll-snap-type: y proximity;
  }
  .matches::-webkit-scrollbar { width: 9px; }
  .matches::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 999px; }
  .match {
    scroll-snap-align: start;
    display: grid; grid-template-columns: 86px 1fr; gap: 14px; align-items: start;
    background: var(--card); border: 1px solid var(--card-border);
    border-radius: 16px; padding: 15px 18px;
    transition: transform .15s ease, border-color .15s ease;
    animation: rise .4s ease both;
  }
  .match:hover { transform: translateX(4px); border-color: rgba(91,140,255,0.4); }
  @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  .m-date { font-size: 12px; color: var(--muted); line-height: 1.5; padding-top: 2px; }
  .m-date .lg { display: inline-block; margin-top: 4px; font-size: 11px; color: #aec3ff; background: rgba(91,140,255,0.14); padding: 2px 8px; border-radius: 999px; }
  .m-core .teams { font-size: 15.5px; font-weight: 650; }
  .m-core .teams .vs { color: var(--muted); margin: 0 8px; font-weight: 400; }
  .m-dims { margin-top: 10px; display: flex; flex-direction: column; gap: 7px; }
  .dim {
    display: grid; grid-template-columns: 52px 1fr auto; gap: 10px; align-items: center;
    background: rgba(255,255,255,0.025); border: 1px solid rgba(255,255,255,0.05);
    border-left: 3px solid rgba(255,255,255,0.12);
    border-radius: 10px; padding: 7px 12px;
  }
  .dim.hit { border-left-color: var(--green); }
  .dim.miss { border-left-color: var(--red); }
  .dim.push { border-left-color: var(--amber); }
  .dim .d-label { font-size: 12px; font-weight: 700; color: #aec3ff; }
  .dim .d-flow { font-size: 12.5px; color: var(--muted); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .dim .d-flow b { color: #eef2ff; font-weight: 650; }
  .dim .d-pred b { color: #cdd8f5; }
  .dim .d-arrow { color: var(--muted); opacity: .7; }
  .dim .d-badge { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px; white-space: nowrap; }
  .d-badge.hit { background: rgba(52,211,153,0.16); color: var(--green); border: 1px solid rgba(52,211,153,0.35); }
  .d-badge.miss { background: rgba(251,113,133,0.14); color: var(--red); border: 1px solid rgba(251,113,133,0.32); }
  .d-badge.push { background: rgba(251,191,36,0.15); color: var(--amber); border: 1px solid rgba(251,191,36,0.33); }
  .d-badge.none { background: rgba(255,255,255,0.05); color: var(--muted); border: 1px solid rgba(255,255,255,0.08); }
  .empty { text-align: center; color: var(--muted); padding: 50px 0; font-size: 14px; }

  /* 分组切换标签 */
  .tabs { display: flex; gap: 10px; flex-wrap: wrap; margin: 4px 0 22px; }
  .tab {
    flex: 1; min-width: 160px; cursor: pointer; user-select: none;
    background: var(--card); border: 1px solid var(--card-border); border-radius: 16px;
    padding: 16px 18px; transition: all .2s ease;
  }
  .tab:hover { border-color: rgba(91,140,255,0.45); transform: translateY(-2px); }
  .tab.active { background: linear-gradient(120deg, rgba(91,140,255,0.22), rgba(34,211,238,0.16)); border-color: rgba(91,140,255,0.6); }
  .tab .t-name { font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
  .tab .t-acc { font-size: 28px; font-weight: 800; margin-top: 8px; }
  .tab .t-acc small { font-size: 14px; opacity: .6; }
  .tab .t-sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .tab .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
  .dot.five { background: var(--accent); }
  .dot.world_cup { background: var(--amber); }
  .dot.others { background: var(--accent2); }

  .group-detail { display: grid; gap: 16px; grid-template-columns: repeat(3, 1fr); margin-bottom: 8px; }
  @media (max-width: 720px){ .group-detail { grid-template-columns: 1fr; } }
  .mini {
    background: var(--card); border: 1px solid var(--card-border); border-radius: 14px; padding: 16px 18px;
  }
  .mini .m-label { font-size: 12.5px; color: var(--muted); }
  .mini .m-val { font-size: 26px; font-weight: 800; margin-top: 6px; }
  .mini .m-val small { font-size: 13px; opacity: .6; }

  /* 赛程滚动 */
  .schedule {
    max-height: 520px; overflow-y: auto; padding-right: 6px;
    display: flex; flex-direction: column; gap: 11px; scroll-snap-type: y proximity;
  }
  .schedule::-webkit-scrollbar { width: 9px; }
  .schedule::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 999px; }
  .sch {
    scroll-snap-align: start;
    display: grid; grid-template-columns: 92px 1fr auto; gap: 14px; align-items: center;
    background: var(--card); border: 1px solid var(--card-border); border-radius: 14px;
    padding: 13px 18px; transition: transform .15s ease, border-color .15s ease;
    animation: rise .4s ease both;
  }
  .sch:hover { transform: translateX(4px); border-color: rgba(34,211,238,0.42); }
  .sch .s-date { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .sch .s-date .lg { display: inline-block; margin-top: 4px; font-size: 11px; color: #aec3ff; background: rgba(91,140,255,0.14); padding: 2px 8px; border-radius: 999px; }
  .sch .s-teams { font-size: 15px; font-weight: 650; }
  .sch .s-teams .vs { color: var(--muted); margin: 0 8px; font-weight: 400; }
  .sch .s-pred { margin-top: 6px; font-size: 12px; color: var(--muted); display: flex; gap: 12px; flex-wrap: wrap; }
  .sch .s-pred b { color: #cdd8f5; font-weight: 600; }
  .sch .s-pred .stake { color: var(--green); }
  .sch .s-pred .stake b { color: var(--green); }
  .sch .s-note { margin-top: 6px; font-size: 11.5px; color: #9fb0d6; line-height: 1.5; background: rgba(91,140,255,0.08); border-left: 2px solid rgba(91,140,255,0.5); padding: 5px 10px; border-radius: 6px; }
  .sch .s-status { font-size: 11.5px; font-weight: 700; padding: 4px 11px; border-radius: 999px; white-space: nowrap; background: rgba(34,211,238,0.13); color: var(--accent2); border: 1px solid rgba(34,211,238,0.3); }
  .sch .s-status.live { background: rgba(251,113,133,0.15); color: var(--red); border-color: rgba(251,113,133,0.32); }

  footer { text-align: center; color: var(--muted); font-size: 12px; margin-top: 50px; }
  .scroll-top {
    position: fixed; right: 22px; bottom: 26px; width: 46px; height: 46px; border-radius: 50%;
    background: linear-gradient(92deg, var(--accent), var(--accent2)); color: #04101f;
    border: none; font-size: 20px; cursor: pointer; box-shadow: var(--shadow);
    opacity: 0; pointer-events: none; transition: opacity .25s ease; font-weight: 800;
  }
  .scroll-top.show { opacity: 1; pointer-events: auto; }
</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <h1>⚽ 足球预测准确率仪表盘</h1>
      <p>数据口径与 <code>prediction_system.py accuracy</code> 一致 · 仅统计已判定赛果的正式预测</p>
      <div class="meta" id="meta"></div>
    </header>

    <section class="kpis" id="kpis"></section>

    <section class="section">
      <div class="section-head">
        <h2>分组准确率</h2>
        <span class="hint">点击切换：五大联赛 / 世界杯 / 非五大联赛</span>
      </div>
      <div class="tabs" id="tabs"></div>
      <div class="group-detail" id="groupDetail"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>各联赛表现</h2>
        <span class="hint">← 左右滑动查看全部联赛 →</span>
      </div>
      <div class="scroller" id="leagues"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>逐场预测明细</h2>
        <span class="hint">↕ 上下滚动浏览每一场比赛</span>
      </div>
      <div class="filters" id="matchGroupFilters"></div>
      <div class="filters" id="filters"></div>
      <div class="matches" id="matches"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2>赛程（未结束赛事）</h2>
        <span class="hint">↕ 上下滚动浏览赛程及预测</span>
      </div>
      <div class="filters" id="schFilters"></div>
      <div class="schedule" id="schedule"></div>
    </section>

    <footer>
      由当前预测流程 ResultManager 真实统计生成 · 本页为静态快照，重新运行脚本可刷新数据
    </footer>
  </div>
  <button class="scroll-top" id="scrollTop" title="回到顶部">↑</button>

<script id="data" type="application/json">__DASHBOARD_DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById('data').textContent);

  const pct = (v) => (Math.round(v * 100) / 100).toFixed(1);
  const el = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; };

  // Meta
  document.getElementById('meta').innerHTML =
    `<span class="pill">生成时间：${DATA.generated_at}</span>` +
    `<span class="pill">统计窗口：近 ${DATA.days} 天</span>` +
    `<span class="pill">已判定样本：${DATA.overall.total} 场</span>`;

  // KPI 卡片
  const o = DATA.overall;
  const kpis = [
    { cls: 'win', label: '胜负命中率', value: pct(o.win_accuracy), unit: '%', sub: `${o.correct} / ${o.total} 场命中`, bar: 'bar-win', w: o.win_accuracy },
    { cls: 'score', label: '比分命中率', value: pct(o.score_accuracy), unit: '%', sub: `${o.correct_score} / ${o.total_score} 场命中`, bar: 'bar-score', w: o.score_accuracy },
    { cls: 'ou', label: '大小球命中率', value: pct(o.ou_accuracy), unit: '%', sub: `${o.correct_ou} / ${o.total_ou} 场命中`, bar: 'bar-ou', w: o.ou_accuracy },
    { cls: 'total', label: '累计预测场次', value: o.total, unit: '', sub: '已进入准确率统计', bar: null, w: 0 },
  ];
  const kpiWrap = document.getElementById('kpis');
  kpis.forEach(k => {
    const card = el('div', 'kpi ' + k.cls);
    card.appendChild(el('div', 'ring'));
    card.appendChild(el('div', 'label', k.label));
    card.appendChild(el('div', 'value', `${k.value}<small>${k.unit}</small>`));
    card.appendChild(el('div', 'sub', k.sub));
    if (k.bar) {
      const p = el('div', 'progress');
      const s = el('span', k.bar); s.style.width = '0%';
      p.appendChild(s); card.appendChild(p);
      requestAnimationFrame(() => setTimeout(() => { s.style.transition = 'width 1s cubic-bezier(.2,.8,.2,1)'; s.style.width = Math.min(100, k.w) + '%'; }, 80));
    }
    kpiWrap.appendChild(card);
  });

  // 分组标签（五大联赛 / 世界杯 / 非五大联赛）
  const GROUP_META = {
    five: { name: '五大联赛', icon: '🏆' },
    world_cup: { name: '世界杯', icon: '🌍' },
    others: { name: '非五大联赛', icon: '⚔️' },
  };
  const GROUP_ORDER = ['five', 'world_cup', 'others'];
  let activeGroup = 'five';

  const tabsWrap = document.getElementById('tabs');
  const groupDetail = document.getElementById('groupDetail');

  function renderTabs() {
    tabsWrap.innerHTML = '';
    GROUP_ORDER.forEach(key => {
      const g = DATA.groups[key] || {};
      const meta = GROUP_META[key];
      const tab = el('div', 'tab' + (key === activeGroup ? ' active' : ''));
      tab.innerHTML =
        `<div class="t-name"><span class="dot ${key}"></span>${meta.icon} ${meta.name}</div>` +
        `<div class="t-acc">${pct(g.win_accuracy || 0)}<small>%</small></div>` +
        `<div class="t-sub">${g.correct || 0}/${g.total || 0} 场命中 · 胜负命中率</div>`;
      tab.onclick = () => { activeGroup = key; renderTabs(); renderGroupDetail(); renderLeagues(); };
      tabsWrap.appendChild(tab);
    });
  }

  function renderGroupDetail() {
    const g = DATA.groups[activeGroup] || {};
    groupDetail.innerHTML = '';
    const cards = [
      { label: '胜负命中率', val: pct(g.win_accuracy || 0) + '%', sub: `${g.correct || 0}/${g.total || 0} 场` },
      { label: '比分命中率', val: pct(g.score_accuracy || 0) + '%', sub: `${g.correct_score || 0}/${g.total_score || 0} 场` },
      { label: '大小球命中率', val: g.total_ou ? pct(g.ou_accuracy || 0) + '%' : '—', sub: `${g.correct_ou || 0}/${g.total_ou || 0} 场` },
    ];
    cards.forEach(c => {
      const m = el('div', 'mini');
      m.innerHTML = `<div class="m-label">${c.label}</div><div class="m-val">${c.val}</div><div class="m-label" style="margin-top:4px">${c.sub}</div>`;
      groupDetail.appendChild(m);
    });
  }

  // 联赛卡片（按当前分组过滤）
  const lgWrap = document.getElementById('leagues');
  function renderLeagues() {
    lgWrap.innerHTML = '';
    const list = DATA.by_league.filter(l => l.group === activeGroup);
    if (!list.length) { lgWrap.appendChild(el('div', 'empty', '该分组暂无联赛级统计')); return; }
    list.forEach(l => {
      const c = el('div', 'league-card');
      c.innerHTML =
        `<div class="lg-name">${l.name}<span class="badge">${l.total} 场</span></div>` +
        `<div class="big">${pct(l.win_accuracy)}<small style="font-size:16px;opacity:.6">%</small></div>` +
        `<div style="font-size:12px;color:var(--muted)">胜负命中率</div>` +
        `<div class="rowline"><span>比分命中</span><b>${pct(l.score_accuracy)}%</b></div>` +
        `<div class="rowline"><span>大小球命中</span><b>${l.total_ou ? pct(l.ou_accuracy) + '%' : '—'}</b></div>` +
        `<div class="rowline"><span>命中场次</span><b>${l.correct}/${l.total}</b></div>`;
      lgWrap.appendChild(c);
    });
  }

  renderTabs();
  renderGroupDetail();
  renderLeagues();

  // 逐场明细：分组维度（五大联赛/世界杯/非五大联赛）
  const matchGroupDefs = [
    { key: 'all', label: '全部' },
    { key: 'five', label: '🏆 五大联赛' },
    { key: 'world_cup', label: '🌍 世界杯' },
    { key: 'others', label: '⚔️ 非五大联赛' },
  ];
  let activeMatchGroup = 'all';
  const matchGroupWrap = document.getElementById('matchGroupFilters');

  // 命中/联赛维度过滤器
  let activeFilter = 'all';
  const filterWrap = document.getElementById('filters');

  function matchesInGroup() {
    return activeMatchGroup === 'all'
      ? DATA.matches
      : DATA.matches.filter(m => m.group === activeMatchGroup);
  }

  // 命中维度过滤器随分组动态重建（联赛 chip 只显示当前分组内联赛）
  function renderFilterChips() {
    filterWrap.innerHTML = '';
    const pool = matchesInGroup();
    const leaguesInPool = [...new Set(pool.map(m => m.league))];
    const filterDefs = [
      { key: 'all', label: '全部' },
      { key: 'hit', label: '✓ 方向命中' },
      { key: 'miss', label: '✗ 方向未中' },
      { key: 'ou_hit', label: '✓ 大小球命中' },
      { key: 'ou_miss', label: '✗ 大小球未中' },
      { key: 'score', label: '★ 比分命中' },
    ].concat(leaguesInPool.map(code => {
      const m = pool.find(x => x.league === code);
      return { key: 'lg:' + code, label: m ? m.league_short : code };
    }));
    filterDefs.forEach((f, i) => {
      const chip = el('div', 'chip' + (f.key === activeFilter ? ' active' : (i === 0 && activeFilter === 'all' ? ' active' : '')), f.label);
      chip.onclick = () => {
        activeFilter = f.key;
        [...filterWrap.children].forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        renderMatches();
      };
      filterWrap.appendChild(chip);
    });
  }

  matchGroupDefs.forEach((f, i) => {
    const chip = el('div', 'chip' + (i === 0 ? ' active' : ''), f.label);
    chip.onclick = () => {
      activeMatchGroup = f.key;
      activeFilter = 'all';
      [...matchGroupWrap.children].forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      renderFilterChips();
      renderMatches();
    };
    matchGroupWrap.appendChild(chip);
  });

  const matchWrap = document.getElementById('matches');
  function renderMatches() {
    matchWrap.innerHTML = '';
    let list = matchesInGroup();
    if (activeFilter === 'hit') list = list.filter(m => m.win_hit);
    else if (activeFilter === 'miss') list = list.filter(m => !m.win_hit);
    else if (activeFilter === 'ou_hit') list = list.filter(m => m.ou_status === 'hit');
    else if (activeFilter === 'ou_miss') list = list.filter(m => m.ou_status === 'miss');
    else if (activeFilter === 'score') list = list.filter(m => m.score_hit);
    else if (activeFilter.startsWith('lg:')) list = list.filter(m => m.league === activeFilter.slice(3));

    if (!list.length) { matchWrap.appendChild(el('div', 'empty', '没有符合条件的比赛')); return; }

    const badge = (status) => {
      if (status === 'hit') return '<span class="d-badge hit">✓ 命中</span>';
      if (status === 'miss') return '<span class="d-badge miss">✗ 未中</span>';
      if (status === 'push') return '<span class="d-badge push">― 走水</span>';
      return '<span class="d-badge none">—</span>';
    };
    const dimRow = (label, predHtml, actualHtml, status) =>
      `<div class="dim ${status || 'none'}">` +
        `<span class="d-label">${label}</span>` +
        `<span class="d-flow"><span class="d-pred">${predHtml || '—'}</span>` +
        `<span class="d-arrow">→</span>` +
        `<span class="d-actual">${actualHtml || '—'}</span></span>` +
        badge(status) +
      `</div>`;

    list.forEach((m, idx) => {
      const row = el('div', 'match');
      row.style.animationDelay = Math.min(idx * 18, 400) + 'ms';

      const date = el('div', 'm-date',
        `${m.date || '—'}<br>${m.time || ''}<br><span class="lg">${m.league_short}</span>`);

      // 方向
      const dirRow = dimRow('方向',
        `<b>${m.predicted_winner_label}</b>`,
        `<b>${m.actual_winner_label}</b>`,
        m.win_hit ? 'hit' : 'miss');

      // 大小球
      const ouRow = m.ou_pred_label
        ? dimRow('大小球',
            `<b>${m.ou_pred_label}</b>`,
            m.ou_actual_label ? `<b>${m.ou_actual_label}</b>` : '—',
            m.ou_status || 'none')
        : '';

      // 比分
      const scoreRow = m.has_score
        ? dimRow('比分',
            `<b>${m.predicted_scores.join(' / ')}</b>`,
            `<b>${m.actual_score}</b>`,
            m.score_hit ? 'hit' : 'miss')
        : (m.actual_score
            ? dimRow('比分', '—', `<b>${m.actual_score}</b>`, 'none')
            : '');

      const core = el('div', 'm-core',
        `<div class="teams">${m.home}<span class="vs">vs</span>${m.away}</div>` +
        `<div class="m-dims">` + dirRow + ouRow + scoreRow + `</div>`);

      row.appendChild(date); row.appendChild(core);
      matchWrap.appendChild(row);
    });
  }
  renderFilterChips();
  renderMatches();

  // 赛程（未结束赛事）+ 分组过滤
  const schGroups = [
    { key: 'all', label: '全部' },
    { key: 'five', label: '🏆 五大联赛' },
    { key: 'world_cup', label: '🌍 世界杯' },
    { key: 'others', label: '⚔️ 非五大联赛' },
  ];
  let schFilter = 'all';
  const schFilterWrap = document.getElementById('schFilters');
  schGroups.forEach((f, i) => {
    const chip = el('div', 'chip' + (i === 0 ? ' active' : ''), f.label);
    chip.onclick = () => {
      schFilter = f.key;
      [...schFilterWrap.children].forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      renderSchedule();
    };
    schFilterWrap.appendChild(chip);
  });

  const schWrap = document.getElementById('schedule');
  function renderSchedule() {
    schWrap.innerHTML = '';
    let list = DATA.schedule;
    if (schFilter !== 'all') list = list.filter(s => s.group === schFilter);
    if (!list.length) { schWrap.appendChild(el('div', 'empty', '暂无赛程')); return; }

    list.forEach((s, idx) => {
      const row = el('div', 'sch');
      row.style.animationDelay = Math.min(idx * 12, 360) + 'ms';

      const date = el('div', 's-date',
        `${s.date || '待定'}<br>${s.time || ''}<br><span class="lg">${s.league_short}</span>`);

      const preds = [];
      if (s.predicted_winner_label) preds.push(`<span>预测 <b>${s.predicted_winner_label}</b></span>`);
      if (s.predicted_scores && s.predicted_scores.length) preds.push(`<span>比分 <b>${s.predicted_scores.join(' / ')}</b></span>`);
      if (s.predicted_ou) preds.push(`<span>大小 <b>${s.predicted_ou}</b></span>`);
      if (s.predicted_stake) preds.push(`<span class="stake">投注 <b>${s.predicted_stake}</b></span>`);
      const predLine = preds.length ? preds.join('') : '<span style="opacity:.5">尚无预测</span>';

      const narrativeLine = s.narrative
        ? `<div class="s-note">📊 ${s.narrative}</div>`
        : '';

      const core = el('div', '',
        `<div class="s-teams">${s.home}<span class="vs">vs</span>${s.away}</div>` +
        `<div class="s-pred">${predLine}</div>` +
        narrativeLine);

      const isLive = /进行中|加时|中场|上半场|下半场/.test(s.status || '');
      const status = el('span', 's-status' + (isLive ? ' live' : ''), s.status || '未开始');

      row.appendChild(date); row.appendChild(core); row.appendChild(status);
      schWrap.appendChild(row);
    });
  }
  renderSchedule();

  // 回到顶部
  const topBtn = document.getElementById('scrollTop');
  window.addEventListener('scroll', () => {
    topBtn.classList.toggle('show', window.scrollY > 400);
  });
  topBtn.onclick = () => window.scrollTo({ top: 0, behavior: 'smooth' });
</script>
</body>
</html>
"""


def build_dashboard(
    base_dir: str | None = None,
    days: int = 3650,
    output: str | None = None,
) -> dict:
    """生成准确率仪表盘 HTML，返回统计数据 dict。

    可复用入口：CLI 与数据更新链（赛果回填后自动重生成）均调用此函数。
    """
    rm = ResultManager(base_dir) if base_dir else ResultManager()
    data = collect_dashboard_data(rm, days=int(days))
    html = render_html(data)
    if output:
        out_path = Path(output)
    elif base_dir:
        out_path = Path(base_dir) / "accuracy_dashboard.html"
    else:
        out_path = PROJECT_ROOT / "accuracy_dashboard.html"
    out_path.write_text(html, encoding="utf-8")
    data["_output"] = str(out_path)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="生成预测准确率 HTML 仪表盘（复用 ResultManager 真实统计）")
    parser.add_argument("--days", type=int, default=3650, help="统计窗口天数（默认 3650，约等于全部历史）")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "accuracy_dashboard.html"),
        help="输出 HTML 路径",
    )
    args = parser.parse_args()

    data = build_dashboard(days=int(args.days), output=args.output)
    g = data["groups"]
    print(
        f"已生成仪表盘: {data['_output']}\n"
        f"  总计: {data['overall']['total']} 场已判定 | 胜负 {data['overall']['win_accuracy']}%\n"
        f"  五大联赛: {g['five']['win_accuracy']}% ({g['five']['correct']}/{g['five']['total']}) | "
        f"世界杯: {g['world_cup']['win_accuracy']}% ({g['world_cup']['correct']}/{g['world_cup']['total']}) | "
        f"非五大: {g['others']['win_accuracy']}% ({g['others']['correct']}/{g['others']['total']})\n"
        f"  赛程: {len(data['schedule'])} 场未结束 | 联赛卡片 {len(data['by_league'])} 个"
    )


if __name__ == "__main__":
    main()
