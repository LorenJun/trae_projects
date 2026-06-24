"""比分精度 A/B 回测：对比 λ 联合拟合大小球线（OU 锚）开关前后的比分 top1/top3 命中率与 1X2 净效果。

复用 2022 世界杯 64 场样本外盘口（与 backtest_wc2022.py 同隔离口径）。
对每场分别在 OFF（LAMBDA_OU_ANCHOR_COST=0，等价旧行为）/ ON（当前常量）两套下跑同一预测核心，
用 score_projection.project_scores_for_side 还原用户可见的 top3 比分（与网页/MEMORY 同源），
对照真实赛果统计：
  - score_top1：最可能比分命中
  - score_top3：top3 含真实比分
  - 1X2：方向命中（确保比分修正不回归胜平负）
  - total_goals MAE/bias：模型期望总进球 vs 真实总进球（衡量低估是否缓解）
"""

from __future__ import annotations

import glob
import json
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
from domain.inference import InferencePipelineService  # noqa: E402
from domain.score_projection import project_scores_for_side  # noqa: E402

ODDS_DIR = os.path.join(BASE_DIR, ".okooo-scraper", "out_of_sample", "world_cup_2022", "odds")
LABEL_1X2 = {"主胜": "home", "平局": "draw", "客胜": "away"}

# OFF：关闭 OU 锚（cost 权重 0，等价于改造前只拟合 1X2）
OFF_LAMBDA_OU_ANCHOR_COST = 0.0
# ON：当前 inference.py 配置的 OU 锚权重
ON_LAMBDA_OU_ANCHOR_COST = InferencePipelineService.LAMBDA_OU_ANCHOR_COST


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


def actual_1x2(hg, ag):
    return "home" if hg > ag else ("away" if hg < ag else "draw")


def set_anchor(weight: float):
    InferencePipelineService.LAMBDA_OU_ANCHOR_COST = weight


def projected_scores(result):
    """还原用户可见 top3 比分（与网页/MEMORY 同源）。"""
    try:
        scored = project_scores_for_side(result)
    except Exception:
        scored = []
    out = []
    for item in scored[:3]:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            out.append(str(item[0]))
    return out


def run_variant(predictor, files, anchor_weight: float):
    set_anchor(anchor_weight)
    neutral_stakes = {"distortion": False, "type": "normal", "summary": "backtest-neutral"}
    rows = {}
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            snap = json.load(handle)
        score = parse_score(snap.get("score"))
        if score is None:
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
            continue
        key = os.path.basename(path)
        scores = projected_scores(result)
        eg = result.get("expected_goals") or {}
        rows[key] = {
            "actual_score": f"{hg}-{ag}",
            "actual_total": hg + ag,
            "act_1x2": actual_1x2(hg, ag),
            "pred_1x2": LABEL_1X2.get(result.get("prediction") or ""),
            "scores": scores,
            "exp_total": float(eg.get("total") or 0.0),
            "balanced": is_balanced(snap.get("欧赔")),
        }
    return rows


def is_balanced(europe_block):
    """均势场判定：欧赔收盘无强热门（三路最低赔率 >= 2.3）。"""
    if not isinstance(europe_block, dict):
        return False
    final = europe_block.get("final") if isinstance(europe_block.get("final"), dict) else {}
    consensus = europe_block.get("consensus") if isinstance(europe_block.get("consensus"), dict) else {}
    median = consensus.get("final_median") if isinstance(consensus.get("final_median"), dict) else None
    src = median if (isinstance(median, dict) and median) else final
    prices = []
    for k in ("home", "draw", "away"):
        try:
            v = float(src.get(k))
            if v > 0:
                prices.append(v)
        except (TypeError, ValueError):
            continue
    return len(prices) == 3 and min(prices) >= 2.3


def summarize(rows):
    n = len(rows)
    top1 = sum(1 for r in rows.values() if r["scores"] and r["scores"][0] == r["actual_score"])
    top3 = sum(1 for r in rows.values() if r["actual_score"] in r["scores"])
    hit_1x2 = sum(1 for r in rows.values() if r["pred_1x2"] == r["act_1x2"])
    mae = sum(abs(r["exp_total"] - r["actual_total"]) for r in rows.values()) / n if n else 0.0
    bias = sum(r["exp_total"] - r["actual_total"] for r in rows.values()) / n if n else 0.0
    return {
        "n": n,
        "score_top1": top1,
        "score_top1_pct": round(top1 / n * 100, 1) if n else 0.0,
        "score_top3": top3,
        "score_top3_pct": round(top3 / n * 100, 1) if n else 0.0,
        "hit_1x2": hit_1x2,
        "hit_1x2_pct": round(hit_1x2 / n * 100, 1) if n else 0.0,
        "total_mae": round(mae, 3),
        "total_bias": round(bias, 3),
    }


def main():
    files = sorted(glob.glob(os.path.join(ODDS_DIR, "*.json")))
    if not files:
        print(f"未找到盘口文件: {ODDS_DIR}")
        return 1

    predictor = EnhancedPredictor(base_dir=BASE_DIR)
    predictor.review_learning_service.build_prediction_context = lambda **_kw: {"available": False}
    predictor.rag_service.retrieve_match_memory = (
        lambda **_kw: {"summary": {}, "similar_cases": [], "market_cases": [], "upset_cases": []}
    )
    predictor.rag_service.build_lightweight_decision = lambda **_kw: {}

    old_rows = run_variant(predictor, files, OFF_LAMBDA_OU_ANCHOR_COST)
    new_rows = run_variant(predictor, files, ON_LAMBDA_OU_ANCHOR_COST)
    set_anchor(ON_LAMBDA_OU_ANCHOR_COST)  # 恢复

    old_sum = summarize(old_rows)
    new_sum = summarize(new_rows)

    # 逐场变化归因
    common = sorted(set(old_rows) & set(new_rows))
    score_gain, score_loss, dir_gain, dir_loss = [], [], [], []
    for k in common:
        o, nw = old_rows[k], new_rows[k]
        o_t3 = o["actual_score"] in o["scores"]
        n_t3 = nw["actual_score"] in nw["scores"]
        if n_t3 and not o_t3:
            score_gain.append(k)
        elif o_t3 and not n_t3:
            score_loss.append(k)
        o_dir = o["pred_1x2"] == o["act_1x2"]
        n_dir = nw["pred_1x2"] == nw["act_1x2"]
        if n_dir and not o_dir:
            dir_gain.append(k)
        elif o_dir and not n_dir:
            dir_loss.append(k)

    print("=" * 70)
    print("比分精度 A/B 回测（2022 世界杯 64 场样本外，OU 锚 OFF vs ON）")
    print("=" * 70)
    header = f"{'指标':<18}{'OFF(无锚)':>14}{'ON(OU锚)':>14}{'净变化':>12}"
    print(header)
    print("-" * 70)

    def row(label, o, nw, suffix=""):
        delta = nw - o
        sign = "+" if delta > 0 else ""
        print(f"{label:<18}{o:>14}{nw:>14}{sign + format(delta, '.1f') + suffix:>12}")

    print(f"{'评分场次':<18}{old_sum['n']:>14}{new_sum['n']:>14}{'':>12}")
    row("比分 top1 命中率", old_sum["score_top1_pct"], new_sum["score_top1_pct"], "%")
    row("比分 top3 命中率", old_sum["score_top3_pct"], new_sum["score_top3_pct"], "%")
    row("1X2 命中率", old_sum["hit_1x2_pct"], new_sum["hit_1x2_pct"], "%")
    row("总进球 MAE", old_sum["total_mae"], new_sum["total_mae"])
    row("总进球偏差(模型-真实)", old_sum["total_bias"], new_sum["total_bias"])
    print("-" * 70)
    print(f"比分 top3：净新增命中 {len(score_gain)} 场，净丢失 {len(score_loss)} 场")
    if score_gain:
        print(f"  新增命中: {', '.join(score_gain)}")
    if score_loss:
        print(f"  丢失命中: {', '.join(score_loss)}")
    print(f"1X2：净新增命中 {len(dir_gain)} 场，净丢失 {len(dir_loss)} 场")
    if dir_gain:
        print(f"  新增命中: {', '.join(dir_gain)}")
    if dir_loss:
        print(f"  丢失命中: {', '.join(dir_loss)}")

    # 均势场子集（修正主要目标人群）
    bal_keys = [k for k in common if old_rows[k].get("balanced")]
    old_bal = summarize({k: old_rows[k] for k in bal_keys})
    new_bal = summarize({k: new_rows[k] for k in bal_keys})
    print("\n" + "=" * 70)
    print(f"均势场子集（无强热门，最低欧赔>=2.3）：{len(bal_keys)} 场")
    print("=" * 70)
    print(header)
    print("-" * 70)
    row("比分 top1 命中率", old_bal["score_top1_pct"], new_bal["score_top1_pct"], "%")
    row("比分 top3 命中率", old_bal["score_top3_pct"], new_bal["score_top3_pct"], "%")
    row("1X2 命中率", old_bal["hit_1x2_pct"], new_bal["hit_1x2_pct"], "%")
    row("总进球 MAE", old_bal["total_mae"], new_bal["total_mae"])
    row("总进球偏差(模型-真实)", old_bal["total_bias"], new_bal["total_bias"])

    # —— OU 锚权重扫描：表征"低估缓解 vs 1X2 回归"的权衡曲线 ——
    print("\n" + "=" * 70)
    print("OU 锚权重扫描（仅变 LAMBDA_OU_ANCHOR_COST）")
    print("=" * 70)
    print(f"{'权重':>6}{'全1X2%':>10}{'全top3%':>10}{'全偏差':>10}{'均势偏差':>10}{'均势MAE':>10}")
    print("-" * 70)
    for w in (0.0, 0.10, 0.15, 0.20, 0.30, 0.45):
        r = run_variant(predictor, files, w)
        s = summarize(r)
        bk = [k for k in r if r[k].get("balanced")]
        sb = summarize({k: r[k] for k in bk})
        print(f"{w:>6.2f}{s['hit_1x2_pct']:>10}{s['score_top3_pct']:>10}{s['total_bias']:>10}{sb['total_bias']:>10}{sb['total_mae']:>10}")
    set_anchor(ON_LAMBDA_OU_ANCHOR_COST)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
