#!/usr/bin/env python3
"""世界杯每日预测网页生成器。

显式传入当天比赛清单，对每场重跑正式 `predict-match --json` 取最新完整数据，
渲染暗色卡片 HTML（胜平负概率 + 比分 + 大小球 + 真实欧赔/亚盘/凯利原始盘口
+ 三轴研判 + 临场资金质检 + 爆冷预警）。

- 数据源：每场重新调用 `prediction_system.py predict-match`（用户口径，盘口最新）。
- 输出：world_cup/analysis/predictions/<date>_predictions.html。
- 不发起额外网络请求，仅复用正式预测链；盘口缺失时如实标注。

用法：
    python3 scripts/build_world_cup_daily_html.py --date 2026-06-17 \
        --match 法国,塞内加尔,03:00 \
        --match 伊拉克,挪威,06:00 \
        --match 阿根廷,阿尔及利亚,09:00 \
        --match 奥地利,约旦,12:00

    # 或从 JSON 文件读清单：[{"home":"法国","away":"塞内加尔","time":"03:00"}, ...]
    python3 scripts/build_world_cup_daily_html.py --date 2026-06-17 --matches-file matches.json
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRED_DIR = PROJECT_ROOT / "world_cup" / "analysis" / "predictions"

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _fmt_pct(x: float) -> str:
    return f"{round(float(x or 0.0) * 100, 1)}%"


def _fmt_num(x, nd: int = 2) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_line(line) -> str:
    try:
        f = float(line)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return "—"


def _company_mode_label(mode: str, consensus: dict | None) -> str:
    consensus = consensus or {}
    if mode == "multi_company_consensus":
        n = consensus.get("company_count") or len(consensus.get("all_companies") or [])
        return f"{n}家共识" if n else "多家共识"
    if mode == "single_company":
        return "单家"
    if mode == "average_row_fallback":
        return "均盘兜底"
    return mode or "—"


def run_prediction(home: str, away: str, date: str, time: str | None) -> dict:
    """调用正式 predict-match 取单场完整 JSON。"""
    cmd = [
        sys.executable,
        "prediction_system.py",
        "predict-match",
        "--league",
        "world_cup",
        "--home-team",
        home,
        "--away-team",
        away,
        "--date",
        date,
        "--json",
    ]
    if time:
        cmd += ["--time", time]
    proc = subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"predict-match 失败 ({home} vs {away}):\n{proc.stderr[-2000:]}")
    # stdout 可能含非 JSON 前缀，定位首个 '{'
    out = proc.stdout
    idx = out.find("{")
    if idx < 0:
        raise RuntimeError(f"未取到 JSON 输出 ({home} vs {away})")
    payload = json.loads(out[idx:])
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(f"预测结果结构异常 ({home} vs {away})")
    return data


def _verdict_block(d: dict) -> tuple[str, str]:
    pred = d.get("prediction") or ""
    conf = d.get("confidence") or 0.0
    cls = "v-draw"
    if pred == "主胜":
        cls = "v-home"
    elif pred == "客胜":
        cls = "v-away"
    return cls, f"{pred} · {_fmt_pct(conf)}"


def _odds_box(d: dict) -> str:
    ms = d.get("market_snapshot") or {}
    eu = ms.get("欧赔") or {}
    ya = ms.get("亚值") or {}
    ke = ms.get("凯利") or {}
    ou = ms.get("大小球") or {}

    lines = []
    # 欧赔
    ei, ef = eu.get("initial") or {}, eu.get("final") or {}
    if ef:
        mode = _company_mode_label(eu.get("company_mode"), eu.get("consensus"))
        lines.append(
            f'<div class="odds-line"><span class="k">欧赔</span><span class="v">'
            f'开 {_fmt_num(ei.get("home"))} / {_fmt_num(ei.get("draw"))} / {_fmt_num(ei.get("away"))} '
            f'→ 终 <b>{_fmt_num(ef.get("home"))} / {_fmt_num(ef.get("draw"))} / {_fmt_num(ef.get("away"))}</b> '
            f'<em>（{mode}）</em></span></div>'
        )
    # 亚盘
    ai, af = ya.get("initial") or {}, ya.get("final") or {}
    if af:
        mode = _company_mode_label(ya.get("company_mode"), ya.get("consensus"))
        it = ai.get("handicap_text") or "—"
        ft = af.get("handicap_text") or "—"
        fv = af.get("handicap_value")
        ft_full = f"{ft}({_fmt_num(fv)})" if fv is not None else ft
        hw, aw = af.get("home_water"), af.get("away_water")
        water = f" 水位 {_fmt_num(hw)}/{_fmt_num(aw)}" if hw is not None else ""
        if it == ft:
            body = f"{ft_full}未动{water}"
        else:
            iv = ai.get("handicap_value")
            it_full = f"{it}({_fmt_num(iv)})" if iv is not None else it
            body = f"开 {it_full} → 终 <b>{ft_full}</b>{water}"
        lines.append(
            f'<div class="odds-line"><span class="k">亚盘</span><span class="v">{body} <em>（{mode}）</em></span></div>'
        )
    # 凯利
    kf = ke.get("final") or {}
    if kf:
        lines.append(
            f'<div class="odds-line"><span class="k">凯利</span><span class="v">'
            f'主 {_fmt_num(kf.get("home"))} / 平 {_fmt_num(kf.get("draw"))} / 客 {_fmt_num(kf.get("away"))}</span></div>'
        )
    # 大小
    of = ou.get("final") or {}
    if of:
        lines.append(
            f'<div class="odds-line"><span class="k">大小</span><span class="v">'
            f'大 {_fmt_num(of.get("over"))} / 小 {_fmt_num(of.get("under"))} @ {_fmt_line(of.get("line"))}</span></div>'
        )
    if not lines:
        return ""
    return (
        '<div class="odds-box">\n'
        '        <div class="obx-title">真实盘口原始数据 <span>· 澳客实时采集</span></div>\n        '
        + "\n        ".join(lines)
        + "\n      </div>"
    )


def _ou_row(d: dict) -> str:
    ou = d.get("over_under") or {}
    over = ou.get("over") or 0.0
    under = ou.get("under") or 0.0
    line = ou.get("line")
    side = "大球" if over >= under else "小球"
    main = max(over, under)
    other = min(over, under)
    other_label = "小球" if side == "大球" else "大球"
    # 盘口漂移
    market = ou.get("market") or {}
    fi = (market.get("initial") or {}).get("line")
    ff = (market.get("final") or {}).get("line")
    drift_txt = "盘口未动 " + _fmt_line(line)
    if fi is not None and ff is not None and float(fi) != float(ff):
        arrow = "升" if float(ff) > float(fi) else "降"
        drift_txt = f"盘口{arrow} {_fmt_line(fi)}→{_fmt_line(ff)}"
    return (
        f'<div class="ou-row">大小球：<b>{side} {_fmt_pct(main)}</b> / {other_label} {_fmt_pct(other)} '
        f'@ {_fmt_line(line)} <span style="color:var(--draw)">（{drift_txt}）</span></div>'
    )


def _total_goals_row(d: dict) -> str:
    tg = d.get("total_goals") or {}
    tops = tg.get("top_totals") or []
    if not tops:
        return ""
    head = tops[0]
    rest = " / ".join(f"{t.get('total')}球({_fmt_pct(t.get('prob'))})" for t in tops[1:3])
    body = f"<b>{head.get('total')} 球</b>({_fmt_pct(head.get('prob'))})"
    if rest:
        body += f" / {rest}"
    return f'<div class="total-row">最可能总进球：{body}</div>'


def _scores_row(d: dict) -> str:
    scores = d.get("top_scores") or []
    chips = []
    for item in scores[:3]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            sc, p = item[0], item[1]
        elif isinstance(item, dict):
            sc, p = item.get("score"), item.get("prob")
        else:
            continue
        chips.append(f'<span class="chip"><b>{html.escape(str(sc))}</b><i>{_fmt_pct(p)}</i></span>')
    if not chips:
        return ""
    return '<div class="scores-row">\n        ' + "\n        ".join(chips) + "\n      </div>"


def _bars(d: dict) -> str:
    probs = d.get("all_probabilities") or {}
    h = (probs.get("主胜") or 0.0) * 100
    dr = (probs.get("平局") or 0.0) * 100
    a = (probs.get("客胜") or 0.0) * 100
    return (
        '<div class="bar">\n'
        f'        <span class="b-home" style="width:{h:.1f}%">{round(h)}%</span>\n'
        f'        <span class="b-draw" style="width:{dr:.1f}%">{round(dr)}%</span>\n'
        f'        <span class="b-away" style="width:{a:.1f}%">{round(a)}%</span>\n'
        "      </div>"
    )


def _drift_commentary(d: dict) -> str:
    tri = d.get("tri_axis_consistency") or {}
    md = tri.get("market_drift") or {}
    up = d.get("upset_potential") or {}
    fav_side = md.get("favorite_side")
    fav_name = d.get("home_team") if fav_side == "home_win" else (
        d.get("away_team") if fav_side == "away_win" else "热门方"
    )
    drift = md.get("drift")
    conf = md.get("drift_confidence")
    conf_cn = {"high": "共振（high）", "low": "孤证（low）", "n/a": "n/a"}.get(conf, conf or "n/a")
    parts = []
    if drift is not None:
        sign = "+" if float(drift) >= 0 else "−"
        trend = "微升" if abs(float(drift)) < 0.05 and float(drift) >= 0 else (
            "走低" if float(drift) < 0 else "走高"
        )
        parts.append(f"封盘热门（{fav_name}）赔率{trend} {sign}{abs(float(drift)):.3f}（{conf_cn}）")
    ld = (md.get("ou_line_drift") or {}).get("line_drift")
    if ld is not None and abs(float(ld)) > 1e-9:
        parts.append("总进球盘有漂移")
    else:
        parts.append("总进球盘未漂移")
    idx = up.get("index")
    lvl = up.get("level")
    if idx is not None and lvl in ("低", "中", "高"):
        parts.append(f"爆冷{lvl}({int(idx)})")
    return "；".join(parts) + "。"


def _tri_block(d: dict) -> str:
    tri = d.get("tri_axis_consistency") or {}
    agreement = tri.get("agreement")
    summary = tri.get("verdict_summary") or ""
    cls = "tri-mixed"
    tag = "部分一致"
    if agreement == "divergent":
        cls, tag = "tri-diverge", "三轴背离"
    elif agreement == "aligned":
        cls, tag = "tri-aligned", "三轴共振"
    # verdict_summary 已含「方向…大小球…操盘…⇒…」，拆成 tag + 正文
    body = summary
    if "⇒" in summary:
        left, right = summary.split("⇒", 1)
        body = f"{left.strip()} ⇒ <b>{right.strip()}</b>"
    return (
        f'<div class="tri-verdict {cls}">\n'
        f'        <span class="tag">{tag}</span>：{body}<br>\n'
        f'        <span style="color:var(--muted)">临场资金：{_drift_commentary(d)}</span>\n'
        "      </div>"
    )


def _upset_block(d: dict) -> str:
    up = d.get("upset_potential") or {}
    lvl = up.get("level")
    if lvl not in ("中", "高"):
        return ""
    idx = int(up.get("index") or 0)
    emoji = "🔴" if lvl == "高" else "🟡"
    cls = "upset-high" if lvl == "高" else "upset-mid"
    factors = up.get("factors") or []
    hsm = (up.get("handicap_strength_mismatch") or {}).get("warning_factors") or []
    detail = "；".join([str(x) for x in (factors[:2] + hsm[:1])]) or "综合风险偏高，留意冷门方向。"
    return (
        f'<div class="upset-row {cls}">\n'
        f'        <b>{emoji} 爆冷预警 {lvl}({idx})</b>：{html.escape(detail)}\n'
        "      </div>"
    )


def render_card(d: dict, time: str | None) -> str:
    home = html.escape(d.get("home_team") or "")
    away = html.escape(d.get("away_team") or "")
    cls, vtext = _verdict_block(d)
    mt = d.get("match_time") or time or ""
    date = d.get("match_date") or ""
    date_short = date[5:] if len(date) >= 10 else date
    time_label = f"{date_short} {mt}".strip()

    parts = [
        f'    <!-- {home} vs {away} -->',
        '    <div class="card">',
        f'      <div class="matchup"><span class="team" style="color:var(--home)">{home}</span>'
        f'<span class="vs">VS</span><span class="team" style="color:var(--away)">{away}</span></div>',
        f'      <div class="time">{html.escape(time_label)}</div>',
        f'      <div style="text-align:center;"><span class="verdict {cls}">{html.escape(vtext)}</span></div>',
        f"      {_bars(d)}",
        '      <div class="prob-legend"><span>主胜</span><span>平局</span><span>客胜</span></div>',
        f"      {_total_goals_row(d)}",
        f"      {_scores_row(d)}",
        f"      {_ou_row(d)}",
        f"      {_odds_box(d)}",
    ]
    upset = _upset_block(d)
    if upset:
        parts.append(f"      {upset}")
    parts.append(f"      {_tri_block(d)}")
    parts.append("    </div>")
    return "\n".join(p for p in parts if p.strip())


def render_html(date: str, cards: list[str]) -> str:
    dt = datetime.strptime(date, "%Y-%m-%d")
    weekday = WEEKDAY_CN[dt.weekday()]
    today = datetime.now().strftime("%Y-%m-%d")
    n = len(cards)
    cards_html = "\n\n".join(cards)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>世界杯 {date} 预测</title>
<style>
  :root {{
    --bg: #FFE6EF; --card: #FFF8FB; --card-strong: #FFD6E4; --line: #FFD0E0;
    --text: #665860; --muted: #A89098; --title: #A86480; --accent: #F8C8DC;
    --home: #B5708F; --draw: #E6A9C6; --away: #D38BA9;
    --bar-text: #5C3A4A; --shadow: rgba(248,200,220,0.25);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: linear-gradient(180deg, #FFE6EF 0%, #FFF0F7 100%); background-attachment: fixed; color: var(--text); font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; padding: 28px 16px; line-height: 1.5; }}
  header {{ text-align: center; margin-bottom: 24px; }}
  header h1 {{ font-size: 22px; font-weight: 800; color: var(--title); }}
  header .sub {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
  .meta-note {{ max-width: 880px; margin: 0 auto 24px; color: var(--muted); font-size: 12px; text-align: center; line-height: 1.7; }}
  .legend-banner {{
    max-width: 880px; margin: 0 auto 24px; padding: 14px 18px;
    background: linear-gradient(180deg, #FFF8FB 0%, #FFF0F7 100%); border: 1px solid var(--line);
    border-radius: 16px; font-size: 12.5px; color: var(--text); line-height: 1.7;
    box-shadow: 0 4px 14px var(--shadow);
  }}
  .legend-banner b {{ color: var(--title); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 24px; max-width: 880px; margin: 0 auto; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 20px 20px 18px; box-shadow: 0 6px 18px var(--shadow); }}
  .matchup {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }}
  .matchup .team {{ font-size: 17px; font-weight: 700; }}
  .matchup .vs {{ color: var(--muted); font-size: 12px; }}
  .time {{ color: var(--muted); font-size: 12px; margin-bottom: 14px; }}
  .verdict {{ display: inline-block; font-size: 13px; font-weight: 700; padding: 4px 14px; border-radius: 16px; margin-bottom: 12px; }}
  .v-home {{ background: rgba(181,112,143,.16); color: var(--home); }}
  .v-draw {{ background: rgba(230,169,198,.28); color: #B26088; }}
  .v-away {{ background: rgba(211,139,169,.20); color: var(--away); }}
  .bar {{ display: flex; height: 26px; border-radius: 10px; overflow: hidden; font-size: 11px; font-weight: 700; color: var(--bar-text); margin-bottom: 4px; }}
  .b-home {{ background: var(--home); display: flex; align-items: center; justify-content: center; color: #FFF8FB; }}
  .b-draw {{ background: var(--draw); display: flex; align-items: center; justify-content: center; }}
  .b-away {{ background: var(--away); display: flex; align-items: center; justify-content: center; color: #FFF8FB; }}
  .prob-legend {{ display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); margin-bottom: 14px; }}
  .total-row {{ font-size: 12.5px; color: var(--text); margin-bottom: 10px; }}
  .total-row b {{ color: var(--title); }}
  .scores-row {{ display: flex; gap: 8px; margin-bottom: 12px; }}
  .chip {{ flex: 1; text-align: center; background: #FFF0F6; border: 1px solid var(--line); border-radius: 12px; padding: 8px 4px; }}
  .chip b {{ display: block; font-size: 15px; color: var(--title); }}
  .chip i {{ font-style: normal; color: var(--muted); font-size: 11px; }}
  .ou-row {{ font-size: 12px; color: var(--muted); margin-bottom: 12px; }}
  .ou-row b {{ color: var(--text); }}
  .odds-box {{
    background: #FFF0F6; border: 1px solid var(--line);
    border-radius: 12px; padding: 12px 14px; margin-bottom: 12px; font-size: 11.5px;
  }}
  .odds-box .obx-title {{ font-size: 11px; color: var(--title); font-weight: 700; margin-bottom: 7px; letter-spacing: .3px; }}
  .odds-box .obx-title span {{ color: var(--muted); font-weight: 400; }}
  .odds-line {{ display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; color: var(--muted); }}
  .odds-line .k {{ color: var(--muted); flex: 0 0 38px; }}
  .odds-line .v {{ color: var(--text); text-align: right; flex: 1; font-variant-numeric: tabular-nums; }}
  .odds-line .v b {{ color: var(--title); }}
  .odds-line .v em {{ font-style: normal; color: var(--muted); }}
  .tri-verdict {{
    font-size: 12px; color: var(--text); line-height: 1.55;
    border-radius: 12px; padding: 10px 12px; margin: 6px 0 2px;
  }}
  .tri-diverge {{ background: rgba(211,139,169,.14); border-left: 3px solid var(--away); }}
  .tri-aligned {{ background: rgba(181,112,143,.12); border-left: 3px solid var(--home); }}
  .tri-mixed {{ background: rgba(168,144,152,.12); border-left: 3px solid var(--muted); }}
  .tri-verdict .tag {{ font-weight: 700; }}
  .tri-diverge .tag {{ color: var(--away); }}
  .tri-aligned .tag {{ color: var(--home); }}
  .tri-mixed .tag {{ color: var(--muted); }}
  .upset-row {{ font-size: 12px; margin: 8px 0 2px; padding: 9px 12px; border-radius: 12px; }}
  .upset-high {{ background: rgba(211,139,169,.20); border-left: 3px solid var(--away); color: var(--text); }}
  .upset-high b {{ color: var(--away); }}
  .upset-mid {{ background: rgba(230,169,198,.26); border-left: 3px solid var(--draw); color: var(--text); }}
  .upset-mid b {{ color: #B26088; }}
  footer {{ text-align: center; color: var(--muted); font-size: 12px; margin-top: 30px; }}
</style>
</head>
<body>
  <header>
    <h1>世界杯小组赛 · 预测比分</h1>
    <div class="sub">{date}（{weekday}）· 共 {n} 场 · 数据源：澳客实时盘口 + DomainPredictor 正式预测链</div>
  </header>
  <p class="meta-note">
    比分为模型最可能比分（Dixon-Coles），胜平负为综合概率。本场次队力多走 FIFA 排名兜底（fallback），
    结论以市场盘口与模型综合为准，仅供研究参考、非投注建议。
  </p>

  <div class="legend-banner">
    <b>三轴综合研判说明</b>：把「方向支持率 + 大小球支持率 + 庄家操盘手法」三轴融合为一句研判，<b>仅诊断、不修改预测方向</b>。
    <span style="color:var(--away)">背离</span>＝方向与进球/操盘自相矛盾（如强主胜却判小球，需警惕）；
    <span style="color:var(--home)">共振</span>＝三轴一致看好同一方；
    <span style="color:var(--muted)">部分一致</span>＝无明显矛盾、信号偏弱。
    <br><b>临场资金质检层</b>：封盘热门赔率漂移用亚值/凯利同向印证——<span style="color:var(--home)">共振</span>＝真实资金流，<span style="color:var(--muted)">孤证</span>＝单家欧赔疑似噪声、已降级不触发背离。
  </div>

  <div class="grid">

{cards_html}

  </div>

  <footer>
    更新时间 {today}（已按最新临场盘口刷新）· europe_leagues 正式预测链 · 三轴综合研判 + 临场资金质检层（仅诊断、不改方向）· 仅供研究参考，非投注建议
  </footer>
</body>
</html>
"""


def parse_matches(args) -> list[dict]:
    matches: list[dict] = []
    if args.matches_file:
        data = json.loads(Path(args.matches_file).read_text(encoding="utf-8"))
        for item in data:
            matches.append(
                {"home": item["home"], "away": item["away"], "time": item.get("time")}
            )
    for spec in args.match or []:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) < 2:
            raise SystemExit(f"--match 格式应为 主队,客队[,时间]，收到：{spec}")
        matches.append(
            {"home": parts[0], "away": parts[1], "time": parts[2] if len(parts) > 2 else None}
        )
    if not matches:
        raise SystemExit("未提供比赛清单：请用 --match 或 --matches-file")
    return matches


def main() -> int:
    ap = argparse.ArgumentParser(description="世界杯每日预测网页生成器")
    ap.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    ap.add_argument(
        "--match",
        action="append",
        help="比赛 主队,客队[,时间]，可重复",
    )
    ap.add_argument("--matches-file", help="比赛清单 JSON 文件")
    ap.add_argument("--output", help="输出 HTML 路径（默认 predictions/<date>_predictions.html）")
    args = ap.parse_args()

    matches = parse_matches(args)
    output = Path(args.output) if args.output else PRED_DIR / f"{args.date}_predictions.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    cards = []
    for m in matches:
        print(f"[预测] {m['home']} vs {m['away']} ...", file=sys.stderr)
        d = run_prediction(m["home"], m["away"], args.date, m.get("time"))
        cards.append(render_card(d, m.get("time")))

    output.write_text(render_html(args.date, cards), encoding="utf-8")
    print(f"[完成] 已生成 {output}（{len(cards)} 场）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
