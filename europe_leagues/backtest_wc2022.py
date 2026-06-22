"""2022 卡塔尔世界杯 64 场样本外回测。

用真实预测核心（EnhancedPredictor.predict_match → InferencePipelineService.run），
把已采集的 2022 历史盘口注入 current_odds，逐场产出 1X2 与大小球预测，对照真实赛果统计命中率。

样本外隔离（防 2026 泄漏）：
  - review_learning_service.build_prediction_context 桩为不可用（不读 2026 复盘聚合）
  - rag_service.retrieve_match_memory / build_lightweight_decision 桩为空（不注入 2026 相似案例）
  - analysis_context.stakes_scenario 中性占位（绕过 assess_stakes_scenario 读 2026 teams md）
  - 环境变量 OKOOO_REFRESH_LIVE=0 / ENABLE_TEAM_CONTEXT=0 杜绝任何实时网络抓取
  - persist=False, force_refresh_odds=False
注：team_strength 仍可能读 world_cup/players/*.json（2026 名单），但 λ 由市场盘口校准锚定，
   下游强度失真被显著抑制；这是已知的、被市场校准约束的近似项。
"""

from __future__ import annotations

import glob
import json
import math
import os
import sys

os.environ.setdefault("OKOOO_REFRESH_LIVE", "0")
os.environ.setdefault("ENABLE_TEAM_CONTEXT", "0")
os.environ.setdefault("ENABLE_PREDICTION_CACHE", "0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from enhanced_prediction_workflow import EnhancedPredictor  # noqa: E402
from okooo_live_snapshot import extract_current_odds  # noqa: E402

ODDS_DIR = os.path.join(BASE_DIR, ".okooo-scraper", "out_of_sample", "world_cup_2022", "odds")
OUT_DIR = os.path.join(BASE_DIR, ".okooo-scraper", "out_of_sample", "world_cup_2022")
LABEL_1X2 = {"主胜": "home", "平局": "draw", "客胜": "away"}


def parse_score(raw):
    if not raw:
        return None
    text = str(raw).replace("：", ":").replace("-", ":").strip()
    parts = [p for p in text.split(":") if p != ""]
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def actual_1x2(home_goals, away_goals):
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def settle_over_under(total_goals, line):
    """按模型采用的盘口线结算实际大小球。整数线可能走盘(push)。"""
    if line is None:
        return None
    is_integer = abs(line - round(line)) < 1e-9
    if is_integer:
        if total_goals > line:
            return "over"
        if total_goals < line:
            return "under"
        return "push"
    return "over" if total_goals > line else "under"


def market_favorite_1x2(europe_block):
    """从欧赔收盘价取市场热门（最低赔率方）。优先用多家中位数。"""
    if not isinstance(europe_block, dict):
        return None
    consensus = europe_block.get("consensus") if isinstance(europe_block.get("consensus"), dict) else {}
    final = consensus.get("final_median") if isinstance(consensus.get("final_median"), dict) else None
    if not (isinstance(final, dict) and final):
        final = europe_block.get("final") if isinstance(europe_block.get("final"), dict) else None
    if not (isinstance(final, dict) and final):
        return None
    prices = {}
    for key in ("home", "draw", "away"):
        try:
            val = float(final.get(key))
            if val > 0:
                prices[key] = val
        except (TypeError, ValueError):
            continue
    if not prices:
        return None
    return min(prices, key=prices.get)


def market_implied_ou(totals_block):
    """从大小球收盘 over/under 赔率取市场隐含方向（较低赔率方）。"""
    if not isinstance(totals_block, dict):
        return None
    final = totals_block.get("final") if isinstance(totals_block.get("final"), dict) else {}
    try:
        over = float(final.get("over"))
        under = float(final.get("under"))
    except (TypeError, ValueError):
        return None
    if over <= 0 or under <= 0:
        return None
    if abs(over - under) < 1e-9:
        return None
    return "over" if over < under else "under"


def pct(num, den):
    return (num / den * 100.0) if den else 0.0


def main():
    files = sorted(glob.glob(os.path.join(ODDS_DIR, "*.json")))
    if not files:
        print(f"未找到盘口文件: {ODDS_DIR}")
        return 1

    predictor = EnhancedPredictor(base_dir=BASE_DIR)

    # —— 样本外隔离：桩掉 2026 复盘/RAG 注入 ——
    predictor.review_learning_service.build_prediction_context = (
        lambda **_kw: {"available": False}
    )
    predictor.rag_service.retrieve_match_memory = (
        lambda **_kw: {"summary": {}, "similar_cases": [], "market_cases": [], "upset_cases": []}
    )
    predictor.rag_service.build_lightweight_decision = lambda **_kw: {}

    neutral_stakes = {"distortion": False, "type": "normal", "summary": "backtest-neutral"}

    rows = []
    blocked = []
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            snap = json.load(handle)

        score = parse_score(snap.get("score"))
        if score is None:
            blocked.append({"file": os.path.basename(path), "reason": "no_score"})
            continue
        hg, ag = score

        current_odds = extract_current_odds(snap)
        result = predictor.predict_match(
            home_team=snap.get("home_team", ""),
            away_team=snap.get("away_team", ""),
            league_code="world_cup",
            match_date=snap.get("match_date"),
            current_odds=current_odds,
            match_id=str(snap.get("match_id") or ""),
            force_refresh_odds=False,
            analysis_context={"stakes_scenario": neutral_stakes},
            persist=False,
        )

        if result.get("prediction_blocked"):
            blocked.append(
                {
                    "file": os.path.basename(path),
                    "reason": result.get("blocked_reason") or "prediction_blocked",
                }
            )
            continue

        # —— 1X2 ——
        pred_1x2_cn = result.get("prediction") or ""
        pred_1x2 = LABEL_1X2.get(pred_1x2_cn)
        act_1x2 = actual_1x2(hg, ag)
        hit_1x2 = (pred_1x2 == act_1x2)
        mkt_1x2 = market_favorite_1x2(snap.get("欧赔"))
        mkt_1x2_hit = (mkt_1x2 == act_1x2) if mkt_1x2 else None

        # —— 大小球 ——
        ou = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
        line = ou.get("line")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        over_p = float(ou.get("over") or 0.0)
        under_p = float(ou.get("under") or 0.0)
        pred_ou = "over" if over_p >= under_p else "under"
        ou_neutral = bool(ou.get("ou_neutral"))
        total_goals = hg + ag
        act_ou = settle_over_under(total_goals, line)
        ou_push = (act_ou == "push")
        hit_ou = None if (act_ou is None or ou_push) else (pred_ou == act_ou)
        mkt_ou = market_implied_ou(snap.get("大小球"))
        mkt_ou_hit = None
        if mkt_ou and act_ou and not ou_push:
            mkt_ou_hit = (mkt_ou == act_ou)

        rows.append(
            {
                "match_id": str(snap.get("match_id") or ""),
                "date": snap.get("match_date"),
                "stage": snap.get("round") or snap.get("stage") or "",
                "home": snap.get("home_team", ""),
                "away": snap.get("away_team", ""),
                "score": f"{hg}-{ag}",
                "total_goals": total_goals,
                # 1X2
                "pred_1x2": pred_1x2,
                "act_1x2": act_1x2,
                "hit_1x2": hit_1x2,
                "confidence": round(float(result.get("confidence") or 0.0), 4),
                "mkt_1x2": mkt_1x2,
                "mkt_1x2_hit": mkt_1x2_hit,
                # OU
                "ou_line": line,
                "pred_ou": pred_ou,
                "ou_over_p": round(over_p, 4),
                "ou_under_p": round(under_p, 4),
                "ou_neutral": ou_neutral,
                "act_ou": act_ou,
                "ou_push": ou_push,
                "hit_ou": hit_ou,
                "mkt_ou": mkt_ou,
                "mkt_ou_hit": mkt_ou_hit,
            }
        )
        print(
            f"[{len(rows):2d}] {snap.get('match_date')} {snap.get('home_team')} {hg}-{ag} {snap.get('away_team')} "
            f"| 1X2 模型={pred_1x2}/{'✓' if hit_1x2 else '✗'} 市场={mkt_1x2} "
            f"| OU线={line} 模型={pred_ou}/{'-' if hit_ou is None else ('✓' if hit_ou else '✗')}"
            f"{' (push)' if ou_push else ''}{' (neutral)' if ou_neutral else ''}"
        )

    # —— 汇总 ——
    n = len(rows)
    model_1x2_hits = sum(1 for r in rows if r["hit_1x2"])
    mkt_1x2_den = sum(1 for r in rows if r["mkt_1x2_hit"] is not None)
    mkt_1x2_hits = sum(1 for r in rows if r["mkt_1x2_hit"])

    ou_den = sum(1 for r in rows if r["hit_ou"] is not None)
    ou_hits = sum(1 for r in rows if r["hit_ou"])
    ou_push_n = sum(1 for r in rows if r["ou_push"])
    ou_neutral_n = sum(1 for r in rows if r["ou_neutral"])
    mkt_ou_den = sum(1 for r in rows if r["mkt_ou_hit"] is not None)
    mkt_ou_hits = sum(1 for r in rows if r["mkt_ou_hit"])

    # 朴素基线
    always_over = sum(1 for r in rows if r["hit_ou"] is not None and r["act_ou"] == "over")
    always_under = sum(1 for r in rows if r["hit_ou"] is not None and r["act_ou"] == "under")
    always_home = sum(1 for r in rows if r["act_1x2"] == "home")

    # 分类正确分布（模型 1X2 各类）
    by_act = {"home": 0, "draw": 0, "away": 0}
    by_act_hit = {"home": 0, "draw": 0, "away": 0}
    for r in rows:
        by_act[r["act_1x2"]] += 1
        if r["hit_1x2"]:
            by_act_hit[r["act_1x2"]] += 1

    summary = {
        "total_matches_scored": n,
        "blocked": blocked,
        "win_draw_loss": {
            "model_hits": model_1x2_hits,
            "model_n": n,
            "model_hit_rate": round(pct(model_1x2_hits, n), 1),
            "market_favorite_hits": mkt_1x2_hits,
            "market_favorite_n": mkt_1x2_den,
            "market_favorite_hit_rate": round(pct(mkt_1x2_hits, mkt_1x2_den), 1),
            "always_home_hit_rate": round(pct(always_home, n), 1),
            "model_recall_by_outcome": {
                k: {
                    "actual_count": by_act[k],
                    "model_correct": by_act_hit[k],
                    "recall_pct": round(pct(by_act_hit[k], by_act[k]), 1),
                }
                for k in ("home", "draw", "away")
            },
        },
        "over_under": {
            "model_hits": ou_hits,
            "model_n": ou_den,
            "model_hit_rate": round(pct(ou_hits, ou_den), 1),
            "push_excluded": ou_push_n,
            "neutral_flagged": ou_neutral_n,
            "market_implied_hits": mkt_ou_hits,
            "market_implied_n": mkt_ou_den,
            "market_implied_hit_rate": round(pct(mkt_ou_hits, mkt_ou_den), 1),
            "always_over_hit_rate": round(pct(always_over, ou_den), 1),
            "always_under_hit_rate": round(pct(always_under, ou_den), 1),
        },
    }

    out_rows = os.path.join(OUT_DIR, "backtest_results.jsonl")
    with open(out_rows, "w", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    out_summary = os.path.join(OUT_DIR, "backtest_summary.json")
    with open(out_summary, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    print("\n" + "=" * 64)
    print("2022 世界杯样本外回测汇总")
    print("=" * 64)
    print(f"完成评分场次: {n} / 64   被拦截: {len(blocked)}")
    print("\n[胜平负 1X2]")
    print(f"  模型命中率:       {model_1x2_hits}/{n} = {pct(model_1x2_hits, n):.1f}%")
    print(f"  市场热门命中率:   {mkt_1x2_hits}/{mkt_1x2_den} = {pct(mkt_1x2_hits, mkt_1x2_den):.1f}%")
    print(f"  全押主胜基线:     {pct(always_home, n):.1f}%")
    print("  模型分类召回:")
    for k, cn in (("home", "主胜"), ("draw", "平局"), ("away", "客胜")):
        d = summary["win_draw_loss"]["model_recall_by_outcome"][k]
        print(f"    {cn}: {d['model_correct']}/{d['actual_count']} = {d['recall_pct']:.1f}%")
    print("\n[大小球 O/U]")
    print(f"  模型命中率:       {ou_hits}/{ou_den} = {pct(ou_hits, ou_den):.1f}%  (走盘排除 {ou_push_n} 场, 低信心中性 {ou_neutral_n} 场)")
    print(f"  市场隐含命中率:   {mkt_ou_hits}/{mkt_ou_den} = {pct(mkt_ou_hits, mkt_ou_den):.1f}%")
    print(f"  全押大球基线:     {pct(always_over, ou_den):.1f}%   全押小球基线: {pct(always_under, ou_den):.1f}%")
    print(f"\n逐场明细: {out_rows}")
    print(f"汇总: {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
