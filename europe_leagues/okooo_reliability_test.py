#!/usr/bin/env python3
"""
澳客抓取链路稳定性测试工具（面向沉淀成熟方案）。

用法示例:
  python3 okooo_reliability_test.py --cases cases.json --repeat 5 --driver browser-use --headed

cases.json 示例:
[
  {"league": "la_liga", "league_hint": "西甲", "home_team": "巴塞罗那", "away_team": "皇家马德里", "date": "2026-04-23"},
  {"league": "premier_league", "league_hint": "英超", "home_team": "切尔西", "away_team": "阿森纳", "date": "2026-04-23"}
]
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from enhanced_prediction_workflow import EnhancedPredictor


def _norm_err(e: str) -> str:
    if not e:
        return ""
    text = " ".join(str(e).split())
    for k in [
        "访问被阻断",
        "安全威胁",
        "captcha",
        "验证码",
        "timed out",
        "未在联赛赛程中找到",
    ]:
        if k in text:
            return k
    return text[:160]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, help="用例 JSON 文件路径")
    parser.add_argument("--repeat", type=int, default=3, help="每个用例重复次数")
    parser.add_argument("--driver", default="browser-use", choices=["browser-use", "local-chrome"], help="优先 driver")
    parser.add_argument("--headed", action="store_true", help="browser-use 有头模式")
    parser.add_argument("--sleep", type=float, default=1.0, help="每次调用间隔秒数（降低被封风险）")
    parser.add_argument("--out", default="", help="可选：输出报告 JSON 路径")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    predictor = EnhancedPredictor()

    overall = {
        "cases": len(cases),
        "repeat": args.repeat,
        "driver": args.driver,
        "headed": bool(args.headed),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    results = []
    err_counter = Counter()

    for case in cases:
        league = case["league"]
        home = case["home_team"]
        away = case["away_team"]
        date = case.get("date") or time.strftime("%Y-%m-%d")

        stats = {
            "league": league,
            "home_team": home,
            "away_team": away,
            "date": date,
            "attempts": 0,
            "success": 0,
            "okooo_refreshed": 0,
            "drivers_used": Counter(),
            "errors": Counter(),
        }

        for _ in range(args.repeat):
            stats["attempts"] += 1
            r = predictor.predict_match(
                home_team=home,
                away_team=away,
                league_code=league,
                match_date=date,
                force_refresh_odds=True,
                okooo_driver=args.driver,
                okooo_headed=bool(args.headed),
            )
            ok = bool(r.get("realtime", {}).get("okooo", {}).get("refreshed"))
            stats["okooo_refreshed"] += 1 if ok else 0
            stats["success"] += 1 if ok else 0

            drv = r.get("realtime", {}).get("okooo", {}).get("driver") or ""
            if drv:
                stats["drivers_used"][drv] += 1

            if not ok:
                errs = r.get("realtime", {}).get("okooo", {}).get("errors") or []
                if not errs:
                    stats["errors"]["unknown"] += 1
                    err_counter["unknown"] += 1
                else:
                    for item in errs:
                        key = _norm_err(item.get("error", ""))
                        if key:
                            stats["errors"][key] += 1
                            err_counter[key] += 1
            time.sleep(max(0.0, float(args.sleep)))

        # Convert counters for JSON
        stats["drivers_used"] = dict(stats["drivers_used"])
        stats["errors"] = dict(stats["errors"])
        results.append(stats)

    overall["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    overall["error_summary"] = dict(err_counter)
    overall["success_rate_by_case"] = [
        {
            "league": r["league"],
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "success_rate": round((r["success"] / max(1, r["attempts"])) * 100, 2),
            "attempts": r["attempts"],
        }
        for r in results
    ]

    payload = {"overall": overall, "results": results}
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    print(out)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
