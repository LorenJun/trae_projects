"""综合准确率回测（单次运行，输出 JSON 摘要）：1X2 / 大小球 / 比分 top1·top3 / 总进球偏差。

用于「最新改动 vs 基线」对照：本脚本未跟踪（git stash 不影响），
在工作树与 git stash 后各跑一次，对比 JSON 摘要即可得到改动净效果。

2022 世界杯 64 场样本外，隔离口径与 backtest_wc2022.py 一致。
用法：python3 backtest_overall.py <输出json路径>
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
from domain.score_projection import project_scores_for_side  # noqa: E402

ODDS_DIR = os.path.join(BASE_DIR, ".okooo-scraper", "out_of_sample", "world_cup_2022", "odds")
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


def actual_1x2(hg, ag):
    return "home" if hg > ag else ("away" if hg < ag else "draw")


def settle_ou(total, line):
    if line is None:
        return None
    if abs(line - round(line)) < 1e-9:
        if total > line:
            return "over"
        if total < line:
            return "under"
        return "push"
    return "over" if total > line else "under"


def is_balanced(europe_block):
    if not isinstance(europe_block, dict):
        return False
    consensus = europe_block.get("consensus") if isinstance(europe_block.get("consensus"), dict) else {}
    median = consensus.get("final_median") if isinstance(consensus.get("final_median"), dict) else None
    final = europe_block.get("final") if isinstance(europe_block.get("final"), dict) else {}
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


def projected_scores(result):
    try:
        scored = project_scores_for_side(result)
    except Exception:
        scored = []
    out = []
    for item in scored[:3]:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            out.append(str(item[0]))
    return out


def pct(num, den):
    return round(num / den * 100.0, 1) if den else 0.0


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "backtest_overall.json")
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

        pred_1x2 = LABEL_1X2.get(result.get("prediction") or "")
        act = actual_1x2(hg, ag)
        ou = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
        line = ou.get("line")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        over_p = float(ou.get("over") or 0.0)
        under_p = float(ou.get("under") or 0.0)
        pred_ou = "over" if over_p >= under_p else "under"
        act_ou = settle_ou(hg + ag, line)
        hit_ou = None if (act_ou is None or act_ou == "push") else (pred_ou == act_ou)
        scores = projected_scores(result)
        eg = result.get("expected_goals") or {}
        rows.append({
            "score": f"{hg}-{ag}",
            "total": hg + ag,
            "hit_1x2": pred_1x2 == act,
            "hit_ou": hit_ou,
            "score_top1": bool(scores) and scores[0] == f"{hg}-{ag}",
            "score_top3": f"{hg}-{ag}" in scores,
            "exp_total": float(eg.get("total") or 0.0),
            "balanced": is_balanced(snap.get("欧赔")),
        })

    def summarize(rs):
        n = len(rs)
        ou_den = sum(1 for r in rs if r["hit_ou"] is not None)
        return {
            "n": n,
            "hit_1x2": sum(1 for r in rs if r["hit_1x2"]),
            "hit_1x2_pct": pct(sum(1 for r in rs if r["hit_1x2"]), n),
            "ou_den": ou_den,
            "hit_ou": sum(1 for r in rs if r["hit_ou"]),
            "hit_ou_pct": pct(sum(1 for r in rs if r["hit_ou"]), ou_den),
            "score_top1": sum(1 for r in rs if r["score_top1"]),
            "score_top1_pct": pct(sum(1 for r in rs if r["score_top1"]), n),
            "score_top3": sum(1 for r in rs if r["score_top3"]),
            "score_top3_pct": pct(sum(1 for r in rs if r["score_top3"]), n),
            "total_mae": round(sum(abs(r["exp_total"] - r["total"]) for r in rs) / n, 3) if n else 0.0,
            "total_bias": round(sum(r["exp_total"] - r["total"] for r in rs) / n, 3) if n else 0.0,
        }

    summary = {
        "overall": summarize(rows),
        "balanced": summarize([r for r in rows if r["balanced"]]),
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
