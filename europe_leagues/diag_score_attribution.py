"""比分误差归因诊断：区分两个假设——
  H1「λ 质量（模型相互影响）」：模型算出的主客 λ 是否本就把真实比分排到高位；
  H2「代码固定数值（方向/大小球硬切 + rerank 惩罚）」：λ 排得好，却被下游过滤器/硬编码扔掉。

方法：对 2022 世界杯 64 场，每场比较真实比分在三个层级的命中/排名：
  L0 raw-λ：仅用最终 home/away λ 的纯泊松 8x8 网格 top-k（无任何方向/OU/rerank 过滤）；
  L1 displayed：project_scores_for_side（方向 + 大小球硬切 + 救回 + 补足，用户实际所见）；
并记录 λ 期望总进球 vs 真实总进球（衡量低估）。

归因口径：
  - true ∈ L0_top3 且 true ∉ L1_top3 → 过滤器/硬编码丢分（H2，代码侧）；
  - true ∉ L0_top3（即 λ 本身没把真实比分排进前三）→ λ 质量问题（H1，模型侧）；
  - true ∈ 两者 → 已命中。
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
from domain.score_projection import project_scores_for_side  # noqa: E402

ODDS_DIR = os.path.join(BASE_DIR, ".okooo-scraper", "out_of_sample", "world_cup_2022", "odds")


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


def _pmf(lam, k):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def raw_lambda_grid(lam_h, lam_a, k=3):
    grid = []
    for i in range(8):
        for j in range(8):
            grid.append((f"{i}-{j}", _pmf(lam_h, i) * _pmf(lam_a, j)))
    grid.sort(key=lambda x: x[1], reverse=True)
    return [sc for sc, _ in grid[:k]]


def raw_lambda_rank(lam_h, lam_a, target):
    grid = []
    for i in range(8):
        for j in range(8):
            grid.append((f"{i}-{j}", _pmf(lam_h, i) * _pmf(lam_a, j)))
    grid.sort(key=lambda x: x[1], reverse=True)
    for idx, (sc, _) in enumerate(grid):
        if sc == target:
            return idx + 1
    return None


def is_balanced(europe_block):
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
    neutral_stakes = {"distortion": False, "type": "normal", "summary": "backtest-neutral"}

    rows = []
    for path in files:
        with open(path, "r", encoding="utf-8") as handle:
            snap = json.load(handle)
        sc = parse_score(snap.get("score"))
        if sc is None:
            continue
        hg, ag = sc
        true_score = f"{hg}-{ag}"
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
        eg = result.get("expected_goals") or {}
        lam_h = float(eg.get("home") or 0.0)
        lam_a = float(eg.get("away") or 0.0)
        l0 = raw_lambda_grid(lam_h, lam_a, 3)
        l0_rank = raw_lambda_rank(lam_h, lam_a, true_score)
        l1 = [str(s) for s, _ in project_scores_for_side(result)[:3]]
        rows.append({
            "match": f"{snap.get('home_team')}-{snap.get('away_team')}",
            "true": true_score,
            "true_total": hg + ag,
            "lam_h": round(lam_h, 2),
            "lam_a": round(lam_a, 2),
            "exp_total": round(lam_h + lam_a, 2),
            "l0_top3": l0,
            "l0_rank": l0_rank,
            "l1_top3": l1,
            "in_l0": true_score in l0,
            "in_l1": true_score in l1,
            "balanced": is_balanced(snap.get("欧赔")),
        })

    n = len(rows)
    in_l0 = sum(1 for r in rows if r["in_l0"])
    in_l1 = sum(1 for r in rows if r["in_l1"])
    # 归因桶
    hit = [r for r in rows if r["in_l1"]]
    filter_loss = [r for r in rows if r["in_l0"] and not r["in_l1"]]   # H2 代码侧
    lambda_miss = [r for r in rows if not r["in_l0"]]                  # H1 模型侧
    # λ 偏差
    mae = sum(abs(r["exp_total"] - r["true_total"]) for r in rows) / n
    bias = sum(r["exp_total"] - r["true_total"] for r in rows) / n
    underest = sum(1 for r in rows if r["exp_total"] < r["true_total"])

    print("=" * 72)
    print(f"比分误差归因（2022 世界杯 {n} 场样本外）")
    print("=" * 72)
    print(f"真实比分 ∈ raw-λ top3 (L0)     : {in_l0}/{n} = {in_l0/n*100:.1f}%")
    print(f"真实比分 ∈ displayed top3 (L1) : {in_l1}/{n} = {in_l1/n*100:.1f}%")
    print("-" * 72)
    print("归因分桶：")
    print(f"  已命中(显示 top3 含真实)      : {len(hit)} 场")
    print(f"  H2 代码侧丢分(λ 排进前三但被过滤器/硬切丢) : {len(filter_loss)} 场")
    print(f"  H1 模型侧(λ 本就没把真实排进前三)          : {len(lambda_miss)} 场")
    print("-" * 72)
    print(f"λ 期望总进球 MAE              : {mae:.3f}")
    print(f"λ 期望总进球偏差(模型-真实)   : {bias:.3f}  (低估场次 {underest}/{n})")
    print("-" * 72)
    # true 在 L0 的平均排名（衡量 λ 把真实比分排多高）
    ranks = [r["l0_rank"] for r in rows if r["l0_rank"] is not None]
    if ranks:
        print(f"真实比分在 raw-λ 网格的中位排名: {sorted(ranks)[len(ranks)//2]}  (越小越说明 λ 排得好)")

    if filter_loss:
        print("\n[H2 代码侧丢分明细] λ 排进前三但显示层丢失：")
        for r in filter_loss:
            print(f"  {r['match']:<28} 真实{r['true']} | λ={r['lam_h']}/{r['lam_a']} "
                  f"L0={r['l0_top3']} → L1={r['l1_top3']}")

    bal = [r for r in rows if r["balanced"]]
    if bal:
        b_l0 = sum(1 for r in bal if r["in_l0"])
        b_l1 = sum(1 for r in bal if r["in_l1"])
        b_bias = sum(r["exp_total"] - r["true_total"] for r in bal) / len(bal)
        print(f"\n[均势场子集 {len(bal)} 场] L0={b_l0} L1={b_l1} λ偏差={b_bias:.3f}")
        for r in bal:
            print(f"  {r['match']:<28} 真实{r['true']}(总{r['true_total']}) λ合计={r['exp_total']} "
                  f"L0rank={r['l0_rank']} L1={r['l1_top3']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
