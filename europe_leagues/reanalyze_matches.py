#!/usr/bin/env python3
"""回放已完赛样本并对比历史预测与当前模型输出。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

from domain.predictor import DomainPredictor
from okooo_live_snapshot import extract_current_odds, find_snapshot_for_match
from result_manager import ResultManager


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回放已完赛样本并输出新旧预测对比")
    parser.add_argument("--league", default="", help="联赛代码；留空表示全部联赛")
    parser.add_argument("--days", type=int, default=30, help="向前回放最近多少天的已完赛样本")
    parser.add_argument("--limit", type=int, default=0, help="最多回放多少场，0 表示不限制")
    parser.add_argument("--allow-missing-snapshot", action="store_true", help="允许在缺少历史快照时仍然回放")
    parser.add_argument("--output", default=os.path.join(BASE_DIR, "reanalysis_results.json"), help="输出 JSON 文件路径")
    parser.add_argument("--json", action="store_true", help="同时将完整结果打印为 JSON")
    return parser.parse_args()


def _winner_cn_to_key(value: Any) -> str:
    mapping = {
        "主胜": "home",
        "平局": "draw",
        "客胜": "away",
        "home": "home",
        "draw": "draw",
        "away": "away",
    }
    return mapping.get(str(value or "").strip(), "")


def _winner_key_to_cn(value: Any) -> str:
    mapping = {
        "home": "主胜",
        "draw": "平局",
        "away": "客胜",
    }
    return mapping.get(str(value or "").strip(), "")


def _parse_score_pair(score_text: str) -> Optional[tuple[int, int]]:
    raw = str(score_text or "").strip()
    if not re.match(r"^\d+\s*-\s*\d+$", raw):
        return None
    try:
        home_score, away_score = [int(item.strip()) for item in raw.split("-")]
    except Exception:
        return None
    return home_score, away_score


def _iter_completed_rows(manager: ResultManager, league: str, days: int) -> Iterable[Dict[str, Any]]:
    samples = manager._build_unified_prediction_samples(days=days)
    rows_by_id = {
        str(row.get("match_id") or "").strip(): row
        for row in manager._iter_teams_rows()
    }
    for match_id, sample in samples.items():
        if league and str(sample.get("league") or "").strip() != league:
            continue
        actual_score = re.sub(r"\s+", "", str(sample.get("actual_score") or "").strip())
        if not _parse_score_pair(actual_score):
            continue
        predicted_winner_key = str(sample.get("predicted_winner") or "").strip()
        actual_winner_key = str(sample.get("actual_winner") or "").strip()
        if predicted_winner_key not in {"home", "draw", "away"} or actual_winner_key not in {"home", "draw", "away"}:
            continue
        match_id = str(match_id or "").strip()
        row = rows_by_id.get(match_id, {})
        yield {
            "match_id": match_id,
            "league": str(sample.get("league") or row.get("league") or "").strip(),
            "league_name": str(sample.get("league_name") or row.get("league_name") or "").strip(),
            "match_date": str(sample.get("match_date") or row.get("match_date") or "").strip(),
            "match_time": str(sample.get("match_time") or row.get("match_time") or "").strip(),
            "home_team": str(sample.get("home_team") or row.get("home_team") or "").strip(),
            "away_team": str(sample.get("away_team") or row.get("away_team") or "").strip(),
            "score_text": actual_score,
            "note": str(row.get("note") or ""),
            "baseline_sample": sample,
            "source_presence": list(sample.get("source_presence") or []),
            "storage_mode": str(sample.get("storage_mode") or "").strip(),
        }


def _build_baseline(manager: ResultManager, row: Dict[str, Any]) -> Dict[str, Any]:
    sample = row.get("baseline_sample") if isinstance(row.get("baseline_sample"), dict) else {}
    predicted_scores = [str(score).strip() for score in (sample.get("predicted_scores") or []) if str(score).strip()]
    predicted_winner_key = str(sample.get("predicted_winner") or "").strip()
    predicted_ou = sample.get("predicted_ou") if isinstance(sample.get("predicted_ou"), dict) else None
    actual_score = re.sub(r"\s+", "", str(sample.get("actual_score") or row.get("score_text") or "").strip())
    actual_winner_key = str(sample.get("actual_winner") or manager._parse_score_to_winner(actual_score) or "").strip()
    return {
        "predicted_winner": _winner_key_to_cn(predicted_winner_key),
        "predicted_winner_key": predicted_winner_key or "",
        "predicted_scores": predicted_scores,
        "predicted_scores_top1": predicted_scores[0] if predicted_scores else "",
        "predicted_ou": predicted_ou,
        "actual_score": actual_score,
        "actual_winner": _winner_key_to_cn(actual_winner_key),
        "actual_winner_key": actual_winner_key or "",
        "win_hit": bool(predicted_winner_key and actual_winner_key and predicted_winner_key == actual_winner_key),
        "score_hit": bool(actual_score and predicted_scores and actual_score in predicted_scores[:3]),
    }


def _extract_snapshot_market(row: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    found = find_snapshot_for_match(
        BASE_DIR,
        str(row.get("league") or "").strip(),
        match_id="",
        home_team=str(row.get("home_team") or "").strip(),
        away_team=str(row.get("away_team") or "").strip(),
        match_date=str(row.get("match_date") or "").strip(),
    )
    if not found:
        return None, {"available": False, "snapshot_path": "", "match_id": ""}
    path, payload = found
    odds = extract_current_odds(payload)
    return odds, {
        "available": True,
        "snapshot_path": str(path or ""),
        "match_id": str(payload.get("match_id") or ""),
    }


def _build_replay_result(result: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    actual_score = str(baseline.get("actual_score") or "")
    actual_winner_key = str(baseline.get("actual_winner_key") or "")
    predicted_winner = str(result.get("prediction") or "").strip()
    predicted_winner_key = _winner_cn_to_key(predicted_winner)
    top_scores = result.get("top_scores") if isinstance(result.get("top_scores"), list) else []
    predicted_scores = [str(score) for score, _prob in top_scores[:3] if str(score).strip()]
    realtime = result.get("realtime") if isinstance(result.get("realtime"), dict) else {}
    context_applied = realtime.get("context_applied") if isinstance(realtime.get("context_applied"), dict) else {}
    return {
        "predicted_winner": predicted_winner,
        "predicted_winner_key": predicted_winner_key,
        "predicted_scores": predicted_scores,
        "predicted_scores_top1": predicted_scores[0] if predicted_scores else "",
        "final_probabilities": result.get("final_probabilities") or {},
        "all_probabilities": result.get("all_probabilities") or {},
        "confidence": float(result.get("confidence") or 0.0),
        "prediction_blocked": bool(result.get("prediction_blocked")),
        "blocked_reason": str(result.get("blocked_reason") or ""),
        "review_outcome_adjustment": context_applied.get("review_outcome_adjustment") or {},
        "draw_confirmation_guard": context_applied.get("draw_confirmation_guard") or {},
        "review_score_rerank": context_applied.get("review_score_rerank") or {},
        "win_hit": bool(predicted_winner_key and actual_winner_key and predicted_winner_key == actual_winner_key),
        "score_hit": bool(actual_score and predicted_scores and actual_score in predicted_scores),
    }


def _confusion_key(predicted_key: str, actual_key: str) -> str:
    if not predicted_key or not actual_key:
        return "unknown"
    return f"{predicted_key}->{actual_key}"


def _summarize_matches(matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "replayed_matches": len(matches),
        "baseline_win_hits": 0,
        "replay_win_hits": 0,
        "baseline_score_hits": 0,
        "replay_score_hits": 0,
        "baseline_away_predictions": 0,
        "replay_away_predictions": 0,
        "baseline_top1_1_0": 0,
        "replay_top1_1_0": 0,
        "baseline_home_to_away_errors": 0,
        "replay_home_to_away_errors": 0,
        "baseline_home_to_draw_errors": 0,
        "replay_home_to_draw_errors": 0,
        "improved_matches": 0,
        "regressed_matches": 0,
        "snapshot_backed_matches": 0,
        "blocked_predictions": 0,
        "baseline_confusion": {},
        "replay_confusion": {},
    }
    for item in matches:
        baseline = item.get("baseline") or {}
        replay = item.get("replay") or {}
        snapshot = item.get("snapshot") or {}
        if baseline.get("win_hit"):
            summary["baseline_win_hits"] += 1
        if replay.get("win_hit"):
            summary["replay_win_hits"] += 1
        if baseline.get("score_hit"):
            summary["baseline_score_hits"] += 1
        if replay.get("score_hit"):
            summary["replay_score_hits"] += 1
        if baseline.get("predicted_winner_key") == "away":
            summary["baseline_away_predictions"] += 1
        if replay.get("predicted_winner_key") == "away":
            summary["replay_away_predictions"] += 1
        if baseline.get("predicted_scores_top1") == "1-0":
            summary["baseline_top1_1_0"] += 1
        if replay.get("predicted_scores_top1") == "1-0":
            summary["replay_top1_1_0"] += 1
        if baseline.get("predicted_winner_key") == "home" and baseline.get("actual_winner_key") == "away":
            summary["baseline_home_to_away_errors"] += 1
        if replay.get("predicted_winner_key") == "home" and baseline.get("actual_winner_key") == "away":
            summary["replay_home_to_away_errors"] += 1
        if baseline.get("predicted_winner_key") == "home" and baseline.get("actual_winner_key") == "draw":
            summary["baseline_home_to_draw_errors"] += 1
        if replay.get("predicted_winner_key") == "home" and baseline.get("actual_winner_key") == "draw":
            summary["replay_home_to_draw_errors"] += 1
        if bool(snapshot.get("available")):
            summary["snapshot_backed_matches"] += 1
        if replay.get("prediction_blocked"):
            summary["blocked_predictions"] += 1

        baseline_confusion_key = _confusion_key(
            str(baseline.get("predicted_winner_key") or ""),
            str(baseline.get("actual_winner_key") or ""),
        )
        replay_confusion_key = _confusion_key(
            str(replay.get("predicted_winner_key") or ""),
            str(baseline.get("actual_winner_key") or ""),
        )
        summary["baseline_confusion"][baseline_confusion_key] = summary["baseline_confusion"].get(baseline_confusion_key, 0) + 1
        summary["replay_confusion"][replay_confusion_key] = summary["replay_confusion"].get(replay_confusion_key, 0) + 1

        baseline_hit = bool(baseline.get("win_hit"))
        replay_hit = bool(replay.get("win_hit"))
        if replay_hit and not baseline_hit:
            summary["improved_matches"] += 1
        elif baseline_hit and not replay_hit:
            summary["regressed_matches"] += 1

    total = max(1, summary["replayed_matches"])
    summary["baseline_win_accuracy"] = round(summary["baseline_win_hits"] / total * 100, 2)
    summary["replay_win_accuracy"] = round(summary["replay_win_hits"] / total * 100, 2)
    summary["baseline_score_accuracy"] = round(summary["baseline_score_hits"] / total * 100, 2)
    summary["replay_score_accuracy"] = round(summary["replay_score_hits"] / total * 100, 2)
    return summary


def _build_preview(matches: List[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    for item in matches:
        baseline = item.get("baseline") or {}
        replay = item.get("replay") or {}
        changed = (
            baseline.get("predicted_winner_key") != replay.get("predicted_winner_key")
            or baseline.get("predicted_scores") != replay.get("predicted_scores")
        )
        improved = bool(replay.get("win_hit")) and not bool(baseline.get("win_hit"))
        regressed = bool(baseline.get("win_hit")) and not bool(replay.get("win_hit"))
        if not (changed or improved or regressed):
            continue
        preview.append(
            {
                "match_id": item.get("match_id"),
                "league": item.get("league"),
                "match_date": item.get("match_date"),
                "teams": f"{item.get('home_team')} vs {item.get('away_team')}",
                "actual_score": baseline.get("actual_score"),
                "actual_winner": baseline.get("actual_winner"),
                "baseline_predicted_winner": baseline.get("predicted_winner"),
                "replay_predicted_winner": replay.get("predicted_winner"),
                "baseline_top_scores": baseline.get("predicted_scores") or [],
                "replay_top_scores": replay.get("predicted_scores") or [],
                "review_outcome_signals": (replay.get("review_outcome_adjustment") or {}).get("signals") or [],
                "draw_guard_signals": (replay.get("draw_confirmation_guard") or {}).get("signals") or [],
                "score_rerank_signals": (replay.get("review_score_rerank") or {}).get("signals") or [],
                "baseline_win_hit": baseline.get("win_hit"),
                "replay_win_hit": replay.get("win_hit"),
                "improved": improved,
                "regressed": regressed,
            }
        )
        if len(preview) >= limit:
            break
    return preview


def _group_matches_by_league(matches: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in matches:
        league = str(item.get("league") or "").strip() or "unknown"
        grouped.setdefault(league, []).append(item)
    return grouped


def _group_skipped_by_league(skipped: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in skipped:
        league = str(item.get("league") or "").strip() or "unknown"
        grouped.setdefault(league, []).append(item)
    return grouped


def _build_league_summaries(matches: List[Dict[str, Any]], skipped: List[Dict[str, Any]]) -> Dict[str, Any]:
    match_groups = _group_matches_by_league(matches)
    skipped_groups = _group_skipped_by_league(skipped)
    leagues = sorted(set(match_groups.keys()) | set(skipped_groups.keys()))
    result: Dict[str, Any] = {}
    for league in leagues:
        league_matches = match_groups.get(league) or []
        league_skipped = skipped_groups.get(league) or []
        summary = _summarize_matches(league_matches)
        result[league] = {
            "summary": {
                **summary,
                "skipped_matches": len(league_skipped),
            },
            "changed_preview": _build_preview(league_matches),
        }
    return result


def main() -> int:
    args = _parse_args()
    manager = ResultManager(BASE_DIR)
    predictor = DomainPredictor(base_dir=BASE_DIR)

    processed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    rows = _iter_completed_rows(manager, args.league, args.days)

    for row in rows:
        if args.limit and len(processed) >= int(args.limit):
            break
        baseline = _build_baseline(manager, row)
        current_odds, snapshot_meta = _extract_snapshot_market(row)
        if not snapshot_meta.get("available") and not args.allow_missing_snapshot:
            skipped.append(
                {
                    "match_id": row.get("match_id"),
                    "league": row.get("league"),
                    "match_date": row.get("match_date"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "reason": "missing_snapshot",
                }
            )
            continue
        try:
            replay_raw = predictor.predict_match(
                home_team=str(row.get("home_team") or "").strip(),
                away_team=str(row.get("away_team") or "").strip(),
                league_code=str(row.get("league") or "").strip(),
                match_date=str(row.get("match_date") or "").strip(),
                current_odds=current_odds,
                match_id=str(snapshot_meta.get("match_id") or "").strip(),
                force_refresh_odds=False,
                match_time=str(row.get("match_time") or "").strip(),
                persist=False,
            )
            replay = _build_replay_result(replay_raw, baseline)
            processed.append(
                {
                    "match_id": row.get("match_id"),
                    "league": row.get("league"),
                    "league_name": row.get("league_name"),
                    "match_date": row.get("match_date"),
                    "match_time": row.get("match_time"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "source_presence": row.get("source_presence") or [],
                    "storage_mode": row.get("storage_mode") or "",
                    "snapshot": snapshot_meta,
                    "baseline": baseline,
                    "replay": replay,
                }
            )
        except Exception as exc:
            skipped.append(
                {
                    "match_id": row.get("match_id"),
                    "league": row.get("league"),
                    "match_date": row.get("match_date"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "reason": "replay_error",
                    "error": str(exc),
                    "snapshot": snapshot_meta,
                }
            )

    summary = _summarize_matches(processed)
    league_summaries = _build_league_summaries(processed, skipped)
    payload = {
        "generated_at": datetime.now().isoformat(),
        "scope": {
            "league": args.league or None,
            "days": int(args.days),
            "limit": int(args.limit),
            "allow_missing_snapshot": bool(args.allow_missing_snapshot),
        },
        "summary": {
            **summary,
            "skipped_matches": len(skipped),
        },
        "league_summaries": league_summaries,
        "changed_preview": _build_preview(processed),
        "matches": processed,
        "skipped": skipped,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=" * 80)
        print("历史样本重放完成")
        print("=" * 80)
        print(f"回放样本: {summary['replayed_matches']} 场")
        print(f"跳过样本: {len(skipped)} 场")
        print(f"基线胜平负准确率: {summary['baseline_win_accuracy']}%")
        print(f"重放胜平负准确率: {summary['replay_win_accuracy']}%")
        print(f"基线 Top2 比分命中率: {summary['baseline_score_accuracy']}%")
        print(f"重放 Top2 比分命中率: {summary['replay_score_accuracy']}%")
        print(f"基线客胜预测数: {summary['baseline_away_predictions']}")
        print(f"重放客胜预测数: {summary['replay_away_predictions']}")
        print(f"基线 1-0 Top1 次数: {summary['baseline_top1_1_0']}")
        print(f"重放 1-0 Top1 次数: {summary['replay_top1_1_0']}")
        print(f"基线 home->away 误差: {summary['baseline_home_to_away_errors']}")
        print(f"重放 home->away 误差: {summary['replay_home_to_away_errors']}")
        print(f"基线 home->draw 误差: {summary['baseline_home_to_draw_errors']}")
        print(f"重放 home->draw 误差: {summary['replay_home_to_draw_errors']}")
        print(f"改善场次: {summary['improved_matches']}")
        print(f"回归场次: {summary['regressed_matches']}")
        print(f"快照支撑场次: {summary['snapshot_backed_matches']}")
        print(f"阻断预测场次: {summary['blocked_predictions']}")
        if not args.league and league_summaries:
            print("-" * 80)
            print("分联赛回放汇总")
            print("-" * 80)
            for league, league_payload in league_summaries.items():
                league_summary = league_payload.get("summary") or {}
                print(
                    f"[{league}] 回放 {league_summary.get('replayed_matches', 0)} 场 / "
                    f"跳过 {league_summary.get('skipped_matches', 0)} 场 / "
                    f"胜平负 {league_summary.get('replay_win_accuracy', 0.0)}% / "
                    f"Top2 比分 {league_summary.get('replay_score_accuracy', 0.0)}%"
                )
        print(f"结果文件: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
