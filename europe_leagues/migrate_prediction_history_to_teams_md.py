#!/usr/bin/env python3
"""
One-time migration:
  prediction_history/{predictions.json,results.json} -> europe_leagues/<league>/teams_2025-26.md

After this, the project should treat teams_2025-26.md as the single source of truth:
  - score column: actual score
  - note column: prediction info
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any, Dict, Optional, Tuple


LEAGUES = ["premier_league", "serie_a", "bundesliga", "ligue_1", "la_liga"]


def _norm(s: str) -> str:
    return (s or "").strip().replace(" ", "")


def _winner_cn(predicted_winner: str) -> str:
    return {"home": "主胜", "draw": "平局", "away": "客胜"}.get(predicted_winner or "", "")


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _update_teams_row(
    line: str,
    match_date: str,
    home_team: str,
    away_team: str,
    score: Optional[str] = None,
    pred_note: Optional[str] = None,
) -> Tuple[bool, str]:
    if not line.lstrip().startswith("|"):
        return False, line
    raw = line.rstrip("\n")
    cols = [c.strip() for c in raw.strip().strip("|").split("|")]
    if len(cols) != 6:
        return False, line
    date, tm, home, score_text, away, note = cols
    if date != match_date:
        return False, line
    if _norm(home) != _norm(home_team) or _norm(away) != _norm(away_team):
        return False, line

    changed = False

    # Fill actual score if requested and current score is empty/placeholder.
    if score and re.match(r"^\d+\s*-\s*\d+$", score):
        if not re.match(r"^\d+\s*-\s*\d+$", score_text):
            cols[3] = score
            changed = True

    if pred_note:
        base_note = note or ""
        # Make idempotent: if there's already a prediction, keep it.
        if "预测" not in base_note:
            base = base_note.rstrip("；; ").strip()
            cols[5] = f"{base}；{pred_note}" if base else pred_note
            changed = True

    if not changed:
        return False, line
    return True, "| " + " | ".join(cols) + " |\n"


def _update_teams_file(
    teams_path: str,
    match_date: str,
    home_team: str,
    away_team: str,
    score: Optional[str],
    pred_note: Optional[str],
) -> bool:
    try:
        lines = open(teams_path, "r", encoding="utf-8").read().splitlines(True)
    except Exception:
        return False

    out = []
    changed_any = False
    for line in lines:
        changed, new_line = _update_teams_row(line, match_date, home_team, away_team, score=score, pred_note=pred_note)
        out.append(new_line)
        if changed:
            changed_any = True

    if changed_any:
        with open(teams_path, "w", encoding="utf-8") as f:
            f.writelines(out)
    return changed_any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default="/Users/bytedance/trae_projects", help="项目根目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要变更的数量，不写文件")
    args = parser.parse_args()

    europe_dir = os.path.join(args.project_root, "europe_leagues")
    history_dir = os.path.join(args.project_root, "prediction_history")
    predictions_path = os.path.join(history_dir, "predictions.json")
    results_path = os.path.join(history_dir, "results.json")

    preds = _load_json(predictions_path) if os.path.exists(predictions_path) else []
    results = _load_json(results_path) if os.path.exists(results_path) else []

    # Index results by (league, date, home, away)
    res_index: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in results:
        if not isinstance(r, dict):
            continue
        league = r.get("league")
        if league not in LEAGUES:
            continue
        key = (league, r.get("match_date") or "", r.get("home_team") or "", r.get("away_team") or "")
        res_index[key] = r

    changed_files = 0
    changed_rows = 0
    total_preds = 0
    total_results = 0
    matched_preds = 0
    matched_results = 0
    missing = []

    for p in preds:
        if not isinstance(p, dict):
            continue
        league = p.get("league")
        if league not in LEAGUES:
            continue
        total_preds += 1
        match_date = p.get("match_date") or ""
        home = p.get("home_team") or ""
        away = p.get("away_team") or ""
        if not (match_date and home and away):
            continue

        teams_path = os.path.join(europe_dir, league, "teams_2025-26.md")
        if not os.path.exists(teams_path):
            continue

        pred_cn = _winner_cn(p.get("predicted_winner") or "")
        pred_note = ""
        if pred_cn:
            prob = p.get("predicted_probability")
            try:
                prob_f = float(prob) if prob not in ("", None) else None
            except Exception:
                prob_f = None
            pred_note = f"预测:{pred_cn}" + (f" 信心:{prob_f:.2f}" if isinstance(prob_f, float) else "")

        r = res_index.get((league, match_date, home, away))
        score = r.get("actual_score") if isinstance(r, dict) else None

        if args.dry_run:
            if pred_note or score:
                changed_rows += 1
            continue

        changed = _update_teams_file(teams_path, match_date, home, away, score=score, pred_note=pred_note or None)
        if changed:
            changed_files += 1
            changed_rows += 1
        else:
            # Not necessarily missing; could already be present. Track a small sample for debug.
            # We treat it as "matched" if the row exists and already contains prediction/score.
            try:
                content = open(teams_path, "r", encoding="utf-8").read()
                # crude existence check
                if match_date in content and home in content and away in content:
                    matched_preds += 1
                    if score:
                        matched_results += 1
                else:
                    missing.append({"league": league, "date": match_date, "home": home, "away": away})
            except Exception:
                missing.append({"league": league, "date": match_date, "home": home, "away": away})

    for r in results:
        if not isinstance(r, dict):
            continue
        league = r.get("league")
        if league not in LEAGUES:
            continue
        total_results += 1

    out = {
        "changed_files": changed_files,
        "changed_rows": changed_rows,
        "total_predictions": total_preds,
        "total_results": total_results,
        "matched_predictions_or_already_present": matched_preds,
        "missing_samples": missing[:10],
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
