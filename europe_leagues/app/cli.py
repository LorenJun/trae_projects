#!/usr/bin/env python3
"""模块说明：提供正式 CLI 子命令入口，负责命令路由、JSON 输出与自动化调用适配。

足球预测系统 - 统一入口
提供交互模式和面向自动化编排的 CLI 子命令。"""

import argparse
import asyncio
import contextlib
import io
import importlib.util
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目路径
EUROPE_LEAGUES_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(EUROPE_LEAGUES_ROOT)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, EUROPE_LEAGUES_ROOT)

from agent_runtime_registry import get_runtime_profile
from collectors.okooo import DEFAULT_CHROME_PATH
from collectors.okooo import get_okooo_driver_status
from domain.features import load_analysis_context_file


def print_header():
    """打印系统标题"""
    print("=" * 80)
    print("⚽ 足球比赛预测系统 ⚽")
    print("=" * 80)
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()


def add_json_flag(parser):
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于 openclaw/脚本调用")


def emit_response(payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if isinstance(payload, str):
        print(payload)
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_quietly(func):
    """捕获 stdout/stderr，避免 JSON 输出被模块日志污染。"""
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        result = func()
    return result, stdout_buffer.getvalue(), stderr_buffer.getvalue()


COMMAND_AGENT_ROLES = {
    "list-leagues": [],
    "predict-match": ["data_collector", "match_analyzer", "odds_analyzer"],
    "predict-match-lite": ["data_collector", "odds_analyzer"],
    "predict-schedule": ["data_collector", "match_analyzer", "odds_analyzer"],
    "collect-data": ["data_collector"],
    "pending-results": ["result_tracker"],
    "save-result": ["result_tracker"],
    "auto-sync-results": ["result_tracker"],
    "result-sync-daemon": ["result_tracker"],
    "accuracy": ["result_tracker"],
    "sync-pending-results-review": ["result_tracker"],
    "build-season-master-review": ["result_tracker"],
    "purge-nonreal-data": ["result_tracker"],
    "rag-rebuild": ["result_tracker"],
    "rag-diagnose": ["result_tracker"],
    "sync-memory-rag": ["result_tracker"],
    "health-check": [],
    "setup-openclaw": [],
    "harness-list": [],
    "harness-run": [],
}


def get_command_runtime_profile(command):
    return get_runtime_profile(COMMAND_AGENT_ROLES.get(command, []))


def build_json_result(
    command,
    data=None,
    captured_stdout="",
    captured_stderr="",
    success=True,
    runtime_profile=None,
):
    payload = {
        "success": success,
        "command": command,
        "generated_at": datetime.now().isoformat(),
        "runtime_profile": runtime_profile or get_command_runtime_profile(command),
    }
    if data is not None:
        payload["data"] = data
    if captured_stdout.strip():
        payload["captured_stdout"] = captured_stdout.strip()
    if captured_stderr.strip():
        payload["captured_stderr"] = captured_stderr.strip()
    return payload


def serialize_match_data(match):
    return {
        "match_id": getattr(match, "match_id", "") or "",
        "home_team": match.home_team,
        "away_team": match.away_team,
        "league": match.league,
        "match_date": match.match_date,
        "match_time": match.match_time,
        "status": match.status,
        "score": list(match.score) if match.score else None,
        "odds_data": match.odds_data if getattr(match, "odds_data", None) else None,
        "sources": match.sources or [],
        "update_time": match.update_time,
    }


def _parse_winner_from_score(score_text):
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(score_text or ""))
    if not match:
        return None
    home_score = int(match.group(1))
    away_score = int(match.group(2))
    if home_score > away_score:
        return "主胜"
    if home_score < away_score:
        return "客胜"
    return "平局"


def _split_predicted_scores(raw_text):
    raw = str(raw_text or "").strip()
    if not raw:
        return []
    parts = re.split(r"\s*>\s*|\s*/\s*", raw)
    return [re.sub(r"\s+", "", item) for item in parts if item.strip()]


def _parse_over_under_pick(raw_text):
    raw = str(raw_text or "").strip()
    if not raw:
        return None
    match = re.search(r"([大小])球?\s*([0-9.]+)", raw)
    if not match:
        return None
    return {"side": match.group(1), "line": float(match.group(2))}


def _extract_prediction_memory_completed_entries(memory_text):
    start_marker = "<!-- prediction-memory:start -->"
    end_marker = "<!-- prediction-memory:end -->"
    start_index = memory_text.find(start_marker)
    end_index = memory_text.find(end_marker)
    if start_index == -1 or end_index == -1 or end_index <= start_index:
        return []

    block = memory_text[start_index:end_index]
    lines = block.splitlines()
    in_completed = False
    entries = []
    current = []

    for line in lines:
        stripped = line.strip()
        if stripped == "#### 已完赛":
            in_completed = True
            current = []
            continue
        if not in_completed:
            continue
        if stripped.startswith("#### ") and stripped != "#### 已完赛":
            break
        if line.startswith("- ["):
            if current:
                entries.append("\n".join(current).rstrip())
            current = [line]
            continue
        if current:
            current.append(line)

    if current:
        entries.append("\n".join(current).rstrip())
    return [item for item in entries if item.strip()]


def _parse_memory_completed_entry(entry_text):
    lines = [line.rstrip() for line in entry_text.splitlines() if line.strip()]
    if not lines:
        return None

    headline = lines[0].strip()
    pred_line = next((line.strip() for line in lines if "预测:" in line), "")
    result_line = next((line.strip() for line in lines if "赛果:" in line), headline)
    meta_line = next((line.strip() for line in lines if "记忆ID:" in line), "")

    memory_key = ""
    memory_key_match = re.match(r"- \[(?P<key>[^\]]+)\]", headline)
    if memory_key_match:
        memory_key = memory_key_match.group("key").strip()
    memory_id_match = re.search(r"记忆ID:\s*(.+?)(?:\s*\|\s*更新时间:|$)", meta_line)
    memory_id = memory_id_match.group(1).strip() if memory_id_match else ""

    after_bracket = headline.split("] ", 1)[1] if "] " in headline else headline.lstrip("- ").strip()
    headline_tokens = after_bracket.split()
    match_date = headline_tokens[0] if headline_tokens and re.match(r"\d{4}-\d{2}-\d{2}", headline_tokens[0]) else ""
    if " vs " in after_bracket:
        left, right = after_bracket.split(" vs ", 1)
        away_team = re.split(r"\s+\|", right, maxsplit=1)[0].strip()
        left_tokens = left.split()
        home_team = " ".join(left_tokens[2:]).strip() if len(left_tokens) >= 3 else left.strip()
    else:
        home_team = ""
        away_team = ""

    predicted_winner_match = re.search(r"预测:\s*(主胜|客胜|平局)", pred_line)
    predicted_winner = predicted_winner_match.group(1) if predicted_winner_match else None
    confidence_match = re.search(r"预测:\s*(?:主胜|客胜|平局)\s*\(([\d.]+)%\)", pred_line)
    confidence_pct = float(confidence_match.group(1)) if confidence_match else None

    score_match = re.search(r"比分:\s*([^|；]+)", pred_line)
    predicted_scores = _split_predicted_scores(score_match.group(1) if score_match else "")

    ou_match = re.search(r"大小球:\s*([^|；]+)", pred_line)
    predicted_ou = _parse_over_under_pick(ou_match.group(1) if ou_match else "")

    actual_result_match = re.search(r"赛果:\s*(主胜|客胜|平局)\s+(\d+\s*-\s*\d+)", result_line)
    actual_winner = None
    actual_score = ""
    if actual_result_match:
        actual_winner = actual_result_match.group(1)
        actual_score = re.sub(r"\s+", "", actual_result_match.group(2))

    if not actual_winner and actual_score:
        actual_winner = _parse_winner_from_score(actual_score)

    win_hit = bool(predicted_winner and actual_winner and predicted_winner == actual_winner)
    score_hit = bool(actual_score and predicted_scores and actual_score in predicted_scores)

    ou_status = "缺失"
    if predicted_ou and actual_score:
        home_score, away_score = [int(item) for item in actual_score.split("-")]
        total_goals = home_score + away_score
        line = float(predicted_ou["line"])
        if abs(total_goals - line) < 1e-9:
            ou_status = "走水"
        else:
            actual_side = "大" if total_goals > line else "小"
            ou_status = "命中" if actual_side == predicted_ou["side"] else "未中"

    return {
        "headline": headline,
        "memory_key": memory_key,
        "memory_id": memory_id,
        "match_id": memory_id or memory_key,
        "league": memory_key.split("|", 1)[0].strip() if "|" in memory_key else "",
        "match_date": match_date,
        "home_team": home_team,
        "away_team": away_team,
        "predicted_winner": predicted_winner,
        "confidence_pct": confidence_pct,
        "predicted_scores": predicted_scores,
        "predicted_scores_display": " / ".join(predicted_scores) if predicted_scores else "缺失",
        "predicted_ou": predicted_ou,
        "predicted_ou_display": ou_match.group(1).strip() if ou_match else "缺失",
        "actual_winner": actual_winner,
        "actual_score": actual_score,
        "win_hit": win_hit,
        "score_hit": score_hit,
        "ou_status": ou_status,
    }


def _build_memory_review_summary(entries, sample_limit=8):
    parsed = []
    for item in entries[: max(0, int(sample_limit or 0)) or 8]:
        parsed_entry = _parse_memory_completed_entry(item)
        if parsed_entry and parsed_entry.get("actual_score"):
            parsed.append(parsed_entry)

    sample_count = len(parsed)
    if sample_count == 0:
        return {
            "sample_count": 0,
            "markdown": "### 滚动记忆复盘总结（最近 0 场已完赛）\n\n> 当前滚动记忆区块中暂无可用于复盘的已完赛样本。\n",
        }

    win_hits = sum(1 for item in parsed if item["win_hit"])
    score_candidates = sum(1 for item in parsed if item["predicted_scores"])
    score_hits = sum(1 for item in parsed if item["score_hit"])
    ou_candidates = sum(1 for item in parsed if item["ou_status"] in ("命中", "未中"))
    ou_hits = sum(1 for item in parsed if item["ou_status"] == "命中")

    win_rate = round(win_hits / sample_count * 100, 1) if sample_count else 0.0
    score_rate = round(score_hits / score_candidates * 100, 1) if score_candidates else 0.0
    ou_rate = round(ou_hits / ou_candidates * 100, 1) if ou_candidates else 0.0

    actual_distribution = {"主胜": 0, "平局": 0, "客胜": 0}
    for item in parsed:
        if item["actual_winner"] in actual_distribution:
            actual_distribution[item["actual_winner"]] += 1

    non_zero_distribution = [f"{key}{value}场" for key, value in actual_distribution.items() if value > 0]
    if len(non_zero_distribution) == 1:
        sample_distribution_summary = f"样本结果单侧集中，当前仅出现{non_zero_distribution[0]}，平衡性仍不足。"
    else:
        sample_distribution_summary = f"结果分布为{' / '.join(non_zero_distribution)}，样本结构较前更均衡。"

    if win_rate >= 80 and score_rate < 30 and ou_rate < 50:
        overall_summary = "胜平负方向稳定，但比分与大小球仍偏弱，尤其对进球放大场景的刻画不足。"
    elif win_rate >= 80 and score_rate < 30:
        overall_summary = "胜平负方向稳定，但比分排序仍偏保守。"
    elif win_rate >= 60:
        overall_summary = "胜平负方向整体可用，但近期样本出现回撤，比分与大小球仍需继续优化。"
    else:
        overall_summary = "近期样本回撤明显，需要同时复查方向判断、比分排序和总进球弹性。"

    win_summary = "主方向判断较稳，可继续保持当前主链结构。" if win_rate >= 80 else "主方向判断有回撤，需要回看盘口与临场信号的权重。"
    score_summary = "Top 比分排序仍偏保守，常低估强队兑现和比赛后段放大。" if score_rate < 30 else "比分排序已有一定可用性，但仍需优化高比分落点。"
    ou_summary = "总进球弹性判断仍偏弱，需减少系统性偏小。" if ou_rate < 50 else "大小球方向基本可用，但还要继续提升稳定性。"

    win_action = "保持当前主链方向判断权重，优先避免因少量异常样本过度调参。" if win_rate >= 80 else "回看近期未命中样本中的盘口退让、平赔下修和临场结构，修正主方向判断。"
    score_action = "增强 `2-0`、`3-0`、`3-1` 一类强队兑现型比分的排序权重，降低过度保守的 `1-0/1-1` 默认占位。"
    ou_action = "提高开放局、强队主场和高节奏联赛场景的总进球弹性，减少系统性偏小。"
    risk_action = "当胜平负方向成立但总进球预期偏保守时，明确提示大小球存在上修风险。"

    review_lines = []
    for item in parsed:
        actual_label = f"{item['home_team']} {item['actual_score']} {item['away_team']}".strip()
        status_bits = [
            "胜平负命中" if item["win_hit"] else "胜平负未中",
            "比分命中" if item["score_hit"] else "比分未中",
            f"大小球{item['ou_status']}",
        ]
        if item["win_hit"] and item["score_hit"]:
            conclusion = "方向和比分都落在有效区间，说明当前主链对这类比赛的刻画较为充分。"
        elif item["win_hit"] and item["ou_status"] == "命中":
            conclusion = "方向与节奏判断基本正确，但比分排序仍有前移空间。"
        elif item["win_hit"]:
            conclusion = "主方向判断正确，但比分与总进球弹性仍偏保守。"
        else:
            conclusion = "主方向判断失真，需要回看盘口变化、临场信号和样本相似性。"
        review_lines.append(f"- `{actual_label}`")
        review_lines.append(
            f"  赛前预测为 `{item['predicted_winner'] or '缺失'}`，Top 比分为 `{item['predicted_scores_display']}`，大小球倾向为 `{item['predicted_ou_display']}`；最终赛果为 `{item['actual_winner']} {item['actual_score']}`。复盘结论：{' '.join(status_bits)}；{conclusion}"
        )

    markdown_lines = [
        f"### 滚动记忆复盘总结（最近 {sample_count} 场已完赛）",
        "",
        f"> 基于滚动记忆区块中的 {sample_count} 场已完赛样本生成。当前结论：{overall_summary}",
        "",
        "#### 总体复盘",
        "",
        f"- 已完赛 `{sample_count}` 场，胜平负命中 `{win_hits}/{sample_count}`，命中率 `{win_rate}%`；核心结论：{win_summary}",
        f"- 比分命中 `{score_hits}/{score_candidates}`，命中率 `{score_rate}%`；核心结论：{score_summary}",
        f"- 大小球命中 `{ou_hits}/{ou_candidates}`，命中率 `{ou_rate}%`；核心结论：{ou_summary}",
        f"- 样本分布：{sample_distribution_summary}",
        "",
        "#### 单场复盘",
        "",
        *review_lines,
        "",
        "#### 优化方向",
        "",
        f"- 胜平负：{win_action}",
        f"- 比分：{score_action}",
        f"- 大小球：{ou_action}",
        f"- 风险提示：{risk_action}",
    ]
    return {
        "sample_count": sample_count,
        "win_hits": win_hits,
        "score_hits": score_hits,
        "score_candidates": score_candidates,
        "ou_hits": ou_hits,
        "ou_candidates": ou_candidates,
        "parsed_entries": parsed,
        "markdown": "\n".join(markdown_lines).rstrip() + "\n",
    }


def _update_memory_review_section(memory_file, review_markdown):
    with open(memory_file, "r", encoding="utf-8") as handle:
        original = handle.read()

    review_header = "### 滚动记忆复盘总结（最近 "
    template_header = "### 标准化复盘模板"
    existing_start = original.find(review_header)
    template_start = original.find(template_header, existing_start if existing_start != -1 else 0)

    if existing_start != -1 and template_start != -1 and template_start > existing_start:
        updated = original[:existing_start].rstrip() + "\n\n" + review_markdown.rstrip() + "\n\n" + original[template_start:]
    else:
        memory_end_marker = "<!-- prediction-memory:end -->"
        marker_index = original.find(memory_end_marker)
        if marker_index == -1:
            raise ValueError(f"找不到滚动记忆结束标记: {memory_file}")
        insert_at = marker_index + len(memory_end_marker)
        updated = original[:insert_at] + "\n\n" + review_markdown.rstrip() + "\n\n" + original[insert_at:].lstrip("\n")

    with open(memory_file, "w", encoding="utf-8") as handle:
        handle.write(updated)


def run_openclaw_sync_pending_results_review(args):
    def _execute():
        from domain.review_learning import PredictionReviewLearningService
        from result_manager import ResultManager
        from runtime.result_sync import sync_due_prediction_results

        manager = ResultManager()
        pending_before = manager.get_pending_matches(days_back=args.days_back)
        sync_report = sync_due_prediction_results(limit=args.limit)
        pending_after = manager.get_pending_matches(days_back=args.days_back)
        accuracy_stats = manager.update_accuracy_stats()
        memory_reconcile = manager.reconcile_memory_pending_entries(days_back=args.days_back)

        memory_file = args.memory_file or os.path.join(PROJECT_ROOT, "MEMORY.md")
        with open(memory_file, "r", encoding="utf-8") as handle:
            memory_text = handle.read()
        completed_entries = _extract_prediction_memory_completed_entries(memory_text)
        review_summary = _build_memory_review_summary(completed_entries, sample_limit=args.review_sample_limit)
        upset_sync = manager.sync_upset_cases_from_review_entries(review_summary.get("parsed_entries", []))
        if not args.no_write_review:
            _update_memory_review_section(memory_file, review_summary["markdown"])
        review_learning_service = PredictionReviewLearningService(EUROPE_LEAGUES_ROOT)
        review_learning_payload = review_learning_service.refresh_summary(
            days=args.days_back,
            sample_limit=max(args.review_sample_limit, 12),
        )

        return {
            "pending_before_count": len(pending_before),
            "pending_after_count": len(pending_after),
            "updated_count": max(0, len(pending_before) - len(pending_after)),
            "remaining_pending": pending_after,
            "auto_sync": sync_report,
            "accuracy": accuracy_stats.get("overall", {}),
            "review": {
                "memory_file": memory_file,
                "written": not args.no_write_review,
                "sample_count": review_summary["sample_count"],
                "win_hits": review_summary.get("win_hits", 0),
                "score_hits": review_summary.get("score_hits", 0),
                "score_candidates": review_summary.get("score_candidates", 0),
                "ou_hits": review_summary.get("ou_hits", 0),
                "ou_candidates": review_summary.get("ou_candidates", 0),
            },
            "upset_sync": upset_sync,
            "review_learning": {
                "summary_path": str(review_learning_service.summary_path()),
                "reviewed_sample_count": int(review_learning_payload.get("reviewed_sample_count") or 0),
                "completed_sample_count": int(review_learning_payload.get("completed_sample_count") or 0),
                "days": int(review_learning_payload.get("days") or args.days_back),
            },
            "memory_reconcile": memory_reconcile,
            "runtime_profile": get_command_runtime_profile("sync-pending-results-review"),
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("sync-pending-results-review", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"待回填: {result['pending_before_count']} -> {result['pending_after_count']}")
    print(f"自动/统一处理后减少: {result['updated_count']} 场")
    print(f"复盘样本: {result['review']['sample_count']} 场")
    overall = result.get("accuracy") or {}
    if overall:
        print(
            f"准确率: 胜平负 {overall.get('win_accuracy', 0)}% "
            f"({overall.get('correct_predictions', 0)}/{overall.get('total_predictions', 0)})"
        )
    if result["remaining_pending"]:
        print("仍待回填比赛:")
        for item in result["remaining_pending"][:10]:
            print(f"- {item['match_date']} {item['home_team']} vs {item['away_team']}")


def get_openclaw_dependency_report():
    repo_root = PROJECT_ROOT
    setup_script = os.path.join(repo_root, "scripts", "setup_openclaw_env.sh")
    requirements_file = os.path.join(repo_root, "requirements.txt")
    openclaw_requirements_file = os.path.join(repo_root, "requirements-openclaw.txt")

    python_version = sys.version_info
    python_ok = python_version >= (3, 9)
    driver_status = get_okooo_driver_status()
    dependency_specs = [
        {"name": "requests", "module": "requests", "required_for": "HTTP 数据抓取"},
        {"name": "playwright", "module": "playwright", "required_for": "浏览器自动化抓取"},
        {"name": "websocket-client", "module": "websocket", "required_for": "实时快照脚本"},
        {
            "name": "browser-use",
            "module": "browser_use",
            "required_for": "真实 user-like 浏览器采集",
            "available_override": bool(driver_status["browser-use"]["available"]),
            "details": {
                "python_module_available": bool(driver_status["browser-use"]["module_available"]),
                "cli_available": bool(driver_status["browser-use"]["cli_available"]),
            },
        },
    ]

    dependencies = []
    missing_dependencies = []
    for item in dependency_specs:
        available = item.get("available_override")
        if available is None:
            available = importlib.util.find_spec(item["module"]) is not None
        dependencies.append(
            {
                "name": item["name"],
                "module": item["module"],
                "available": available,
                "required_for": item["required_for"],
                **({"details": item["details"]} if item.get("details") else {}),
            }
        )
        if not available:
            missing_dependencies.append(item["name"])

    npm_available = shutil.which("npm") is not None
    install_commands = [
        f"bash {setup_script}",
        f"python3 -m pip install -r {openclaw_requirements_file}",
        "python3 -m playwright install chromium",
    ]

    return {
        "python_version": sys.version.split()[0],
        "python_supported": python_ok,
        "dependencies": dependencies,
        "missing_dependencies": missing_dependencies,
        "requirements_file": requirements_file,
        "openclaw_requirements_file": openclaw_requirements_file,
        "setup_script": setup_script,
        "package_json_exists": os.path.exists(os.path.join(repo_root, "package.json")),
        "npm_setup_optional": npm_available,
        "openclaw_full_ready": python_ok and not missing_dependencies,
        "bootstrap_commands": install_commands,
        "okooo_driver_status": driver_status,
        "preferred_okooo_driver": "local-chrome" if driver_status["local-chrome"]["available"] else "browser-use",
        "chrome_path": DEFAULT_CHROME_PATH,
    }


def validate_leagues(league_code=None):
    from domain.predictor import LEAGUE_CONFIG

    if league_code:
        if league_code not in LEAGUE_CONFIG:
            raise ValueError(f"无效的联赛代码: {league_code}")
        return [league_code], LEAGUE_CONFIG

    return list(LEAGUE_CONFIG.keys()), LEAGUE_CONFIG


def list_leagues_data():
    from domain.predictor import LEAGUE_CONFIG

    return [
        {
            "code": code,
            "name": config["name"],
            "team_count": len(config.get("teams", [])),
            "avg_goals": config.get("avg_goals"),
        }
        for code, config in LEAGUE_CONFIG.items()
    ]


def list_leagues(json_output=False):
    leagues = list_leagues_data()
    if json_output:
        emit_response(build_json_result("list-leagues", leagues), as_json=True)
        return

    print("📋 可用联赛:")
    for league in leagues:
        print(f"  - {league['name']} ({league['code']})")
    print()


def run_enhanced_system(league_code=None, days=3):
    """运行增强版预测系统"""
    print("🚀 启动增强版预测系统...")
    print()

    try:
        from domain.predictor import DomainPredictor, LEAGUE_CONFIG

        predictor = DomainPredictor()
        if league_code:
            leagues = [league_code] if league_code in LEAGUE_CONFIG else []
        else:
            leagues = list(LEAGUE_CONFIG.keys())

        if not leagues:
            print(f"❌ 无效的联赛代码: {league_code}")
            return

        base_date = datetime.now()
        updated_files = []

        for l_code in leagues:
            print(f"📊 处理 {LEAGUE_CONFIG[l_code]['name']}...")
            for day_offset in range(days):
                match_date = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                print(f"  📅 生成 {match_date} 的预测...")
                teams_file = predictor.generate_prediction_report(l_code, match_date)
                if teams_file:
                    print(f"  ✅ 已更新 {os.path.basename(teams_file)}")
                    updated_files.append(teams_file)

        print()
        print("=" * 80)
        print(f"🎉 完成！共更新 {len(updated_files)} 个 teams_2025-26.md 文件")
        print("=" * 80)

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()


def run_original_system():
    """运行原始版预测系统"""
    print("📦 启动原始版预测系统...")
    print()

    try:
        from optimized_prediction_workflow import main as original_main
        original_main()
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()


def run_ml_test():
    """测试机器学习模型"""
    print("🤖 测试多模型融合系统...")
    print()

    try:
        from models.fusion import MultiModelFusion

        fusion = MultiModelFusion()
        result = fusion.predict(
            home_team="切尔西",
            away_team="曼联",
            home_strength=65,
            away_strength=70,
            home_form=3,
            away_form=4,
            home_injuries=2,
            away_injuries=3,
            h2h_home_wins=2,
            h2h_away_wins=3,
            h2h_draws=1,
            home_motivation=80,
            away_motivation=85,
            home_xg=1.5,
            away_xg=1.8,
            home_attack=1.2,
            home_defense=0.9,
            away_attack=1.4,
            away_defense=0.8,
        )

        print("=" * 60)
        print("多模型融合预测结果")
        print("=" * 60)

        final = result["final"]
        print("\n最终预测:")
        print(f"  主胜概率: {final['home_win']:.1%}")
        print(f"  平局概率: {final['draw']:.1%}")
        print(f"  客胜概率: {final['away_win']:.1%}")

        print("\n预期进球:")
        print(f"  主队: {result['home_lambda']:.2f}")
        print(f"  客队: {result['away_lambda']:.2f}")

        print("\n各模型预测详情:")
        for model_name, prediction in result["all_models"].items():
            print(
                f"  {model_name}: 主胜{prediction['home_win']:.1%}, "
                f"平局{prediction['draw']:.1%}, 客胜{prediction['away_win']:.1%}"
            )

        print()
        print("=" * 60)
        print("✅ 测试完成！")
        print("=" * 60)

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


def run_openclaw_list_leagues(json_output):
    if json_output:
        result, captured_stdout, captured_stderr = run_quietly(list_leagues_data)
        emit_response(
            build_json_result("list-leagues", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return
    list_leagues()


def run_openclaw_predict_match(args):
    def _execute():
        from domain.predictor import DomainPredictor

        predictor = DomainPredictor()
        ctx = load_analysis_context_file(getattr(args, "context_file", ""))
        result = predictor.predict_match(
            home_team=args.home_team,
            away_team=args.away_team,
            league_code=args.league,
            match_date=args.date,
            match_id=getattr(args, "match_id", "") or "",
            force_refresh_odds=not getattr(args, "no_refresh_odds", False),
            okooo_driver=getattr(args, "okooo_driver", "local-chrome"),
            okooo_headed=bool(getattr(args, "okooo_headed", False)),
            match_time=getattr(args, "match_time", "") or "",
            league_hint=getattr(args, "league_hint", None),
            analysis_context=ctx,
            persist=not bool(getattr(args, "no_write", False)),
        )
        if args.no_write:
            result["persisted"] = {"enabled": False, "archived": False, "memory_updated": False}
        else:
            result.setdefault("persisted", {"enabled": True, "archived": False, "memory_updated": False})
        result.setdefault("runtime_profile", get_command_runtime_profile("predict-match"))
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("predict-match", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"比赛: {result['home_team']} vs {result['away_team']}")
    print(f"联赛: {result['league_name']}  日期: {result['match_date']}")
    if result.get("prediction_blocked"):
        reason = str(result.get("blocked_reason") or "missing_real_market_line").strip()
        print(f"预测: 数据不完整  原因: {reason}")
        print(f"主胜/平局/客胜(预估): {result['final_probabilities']}")
        return
    print(f"预测: {result['prediction']}  信心: {result['confidence']:.2%}")
    print(f"主胜/平局/客胜: {result['final_probabilities']}")
    over_under = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
    if over_under.get("available"):
        line = over_under.get("line")
        line_label = f"{float(line):g}" if isinstance(line, (int, float)) else "?"
        print(
            f"大小球: 大球 {float(over_under.get('over') or 0.0):.2%} / "
            f"小球 {float(over_under.get('under') or 0.0):.2%} @ {line_label} "
            f"[{over_under.get('line_source', 'unknown')}]"
        )
    else:
        reason = str(over_under.get("reason") or "missing_real_market_line").strip()
        print(f"大小球: 待补真实盘口 ({reason})")
    explanation = str(result.get("retrieved_memory_explanation") or "").strip()
    if explanation:
        print(f"RAG记忆: {explanation}")


def run_openclaw_predict_match_lite(args):
    def _execute():
        from domain.lightweight_prediction import predict_lightweight_match
        from domain.persistence import PredictionPersistenceService
        from result_manager import ResultManager

        result = predict_lightweight_match(
            base_dir=EUROPE_LEAGUES_ROOT,
            league_name=args.league_name,
            league_code=getattr(args, "league", "") or "",
            home_team=args.home_team,
            away_team=args.away_team,
            match_date=args.date,
            match_time=getattr(args, "match_time", "") or "",
            match_id=getattr(args, "match_id", "") or "",
            okooo_driver=getattr(args, "okooo_driver", "local-chrome"),
            okooo_headed=bool(getattr(args, "okooo_headed", False)),
        )
        if args.no_write:
            result["persisted"] = {"enabled": False, "archived": False, "memory_updated": False}
        else:
            manager = ResultManager(EUROPE_LEAGUES_ROOT)
            service = PredictionPersistenceService(EUROPE_LEAGUES_ROOT, cache=None, result_manager=manager)
            result = service.persist_memory_only_prediction(result, getattr(args, "league", "") or result.get("league_code") or "")
        result.setdefault("runtime_profile", get_command_runtime_profile("predict-match-lite"))
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("predict-match-lite", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"比赛: {result['home_team']} vs {result['away_team']}")
    print(f"联赛: {result['league_name']}  日期: {result['match_date']}")
    print(f"预测: {result['prediction']}  信心: {result['confidence']:.2%}")
    print(f"主胜/平局/客胜: {result['all_probabilities']}")
    over_under = result.get("over_under") if isinstance(result.get("over_under"), dict) else {}
    if over_under.get("available"):
        line = over_under.get("line")
        line_label = f"{float(line):g}" if isinstance(line, (int, float)) else "?"
        print(
            f"大小球: 大球 {float(over_under.get('over') or 0.0):.2%} / "
            f"小球 {float(over_under.get('under') or 0.0):.2%} @ {line_label} "
            f"[{over_under.get('line_source', 'unknown')}]"
        )
    else:
        reason = str(over_under.get("reason") or "missing_real_market_line").strip()
        print(f"大小球: 待补真实盘口 ({reason})")


def run_openclaw_predict_schedule(args):
    def _execute():
        from domain.predictor import DomainPredictor

        leagues, league_config = validate_leagues(args.league)
        predictor = DomainPredictor()
        base_date = datetime.strptime(args.date, "%Y-%m-%d")
        updates = []
        runtime_profile = get_command_runtime_profile("predict-schedule")

        for league_code in leagues:
            for day_offset in range(args.days):
                match_date = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                teams_file = predictor.generate_prediction_report(league_code, match_date)
                updates.append(
                    {
                        "league": league_code,
                        "league_name": league_config[league_code]["name"],
                        "match_date": match_date,
                        "teams_file": teams_file,
                        "updated": bool(teams_file),
                        "runtime_profile": runtime_profile,
                    }
                )
        return updates

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("predict-schedule", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    updates = _execute()
    print("📅 预测写回结果:")
    for item in updates:
        status = "已更新" if item["updated"] else "无数据"
        print(f"- {item['league_name']} {item['match_date']}: {status}")


def run_openclaw_collect_data(args):
    def _execute():
        from collectors.sporttery import DataCollector

        collector = DataCollector()
        matches = asyncio.run(collector.collect_league_data(args.league, args.date, use_cache=not args.no_cache))
        return {
            "league": args.league,
            "date": args.date,
            "count": len(matches),
            "matches": [serialize_match_data(match) for match in matches],
            "use_cache": not args.no_cache,
            "runtime_profile": get_command_runtime_profile("collect-data"),
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("collect-data", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"已收集 {result['league']} {result['date']} 比赛 {result['count']} 场")
    for match in result["matches"]:
        print(f"- {match['match_time']} {match['home_team']} vs {match['away_team']} [{match['status']}]")


def run_openclaw_pending_results(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        return {
            "matches": manager.get_pending_matches(days_back=args.days_back),
            "runtime_profile": get_command_runtime_profile("pending-results"),
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("pending-results", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    pending = _execute()
    print(f"待更新结果比赛: {len(pending['matches'])} 场")
    for item in pending["matches"]:
        print(f"- {item['match_date']} {item['home_team']} vs {item['away_team']}")


def run_openclaw_save_result(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        result = manager.save_result(
            args.match_id,
            args.home_score,
            args.away_score,
        )
        if isinstance(result, dict):
            result.setdefault("runtime_profile", get_command_runtime_profile("save-result"))
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("save-result", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"已保存比分: {result['home_team']} {result['actual_score']} {result['away_team']}")


def run_openclaw_auto_sync_results(args):
    def _execute():
        from runtime.result_sync import sync_due_prediction_results

        result = sync_due_prediction_results(limit=args.limit)
        result["runtime_profile"] = get_command_runtime_profile("auto-sync-results")
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("auto-sync-results", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(f"到期任务: {report['due_count']} 场")
    print(f"成功更新: {report['updated_count']} 场")
    for item in report["updates"]:
        if item.get("updated"):
            print(f"- 已同步: {item.get('home_team')} vs {item.get('away_team')} -> {item.get('actual_score')}")
        else:
            print(f"- 未更新: {item.get('match_id')} ({item.get('reason')})")


def run_openclaw_result_sync_daemon(args):
    def _execute():
        from runtime.result_sync import run_result_sync_daemon

        result = run_result_sync_daemon(
            interval_minutes=args.interval_minutes,
            max_cycles=args.max_cycles,
        )
        result["runtime_profile"] = get_command_runtime_profile("result-sync-daemon")
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("result-sync-daemon", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_accuracy(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        if args.refresh:
            result = manager.update_accuracy_stats()
        else:
            result = manager.accuracy_store.load()
            if not result:
                result = manager.update_accuracy_stats()
        if isinstance(result, dict):
            result.setdefault("runtime_profile", get_command_runtime_profile("accuracy"))
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("accuracy", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    stats = _execute()
    overall = stats["overall"]
    print(f"总体准确率: {overall['win_accuracy']}% ({overall['correct_predictions']}/{overall['total_predictions']})")
    print(f"统一大小球准确率: {overall.get('ou_accuracy', 0)}% ({overall.get('correct_ou_predictions', 0)}/{overall.get('total_ou_predictions', 0)})")
    ou_report = stats.get("over_under_report") or {}
    by_line_source = ou_report.get("by_line_source") or {}
    if by_line_source:
        print("大小球分层:")
        for source_name, payload in list(by_line_source.items())[:5]:
            print(
                f"  - {source_name}: {payload.get('hit_rate', 0)}% "
                f"({payload.get('hit_count', 0)}/{payload.get('sample_count', 0)})"
            )


def run_openclaw_rag_rebuild(args):
    def _execute():
        from domain.rag import HybridRAGService

        service = HybridRAGService(EUROPE_LEAGUES_ROOT)
        result = service.refresh(limit=getattr(args, "limit", 200))
        return {
            "rag_mode": result.get("rag_mode"),
            "case_count": result.get("case_count"),
            "registry": result.get("registry", {}),
            "runtime_profile": get_command_runtime_profile("rag-rebuild"),
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("rag-rebuild", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_build_season_master_review(args):
    def _execute():
        result = {
            "sync": None,
            "rag": None,
        }

        if not getattr(args, "skip_sync", False):
            from result_manager import ResultManager
            from runtime.result_sync import sync_due_prediction_results

            manager = ResultManager()
            pending_before = manager.get_pending_matches(days_back=args.days_back)
            sync_report = sync_due_prediction_results(limit=args.limit)
            pending_after = manager.get_pending_matches(days_back=args.days_back)
            accuracy_stats = manager.update_accuracy_stats()
            memory_reconcile = manager.reconcile_memory_pending_entries(days_back=args.days_back)

            memory_file = args.memory_file or os.path.join(PROJECT_ROOT, "MEMORY.md")
            with open(memory_file, "r", encoding="utf-8") as handle:
                memory_text = handle.read()
            completed_entries = _extract_prediction_memory_completed_entries(memory_text)
            review_summary = _build_memory_review_summary(completed_entries, sample_limit=args.review_sample_limit)
            upset_sync = manager.sync_upset_cases_from_review_entries(review_summary.get("parsed_entries", []))
            if not args.no_write_review:
                _update_memory_review_section(memory_file, review_summary["markdown"])

            result["sync"] = {
                "pending_before_count": len(pending_before),
                "pending_after_count": len(pending_after),
                "updated_count": max(0, len(pending_before) - len(pending_after)),
                "remaining_pending": pending_after,
                "auto_sync": sync_report,
                "accuracy": accuracy_stats.get("overall", {}),
                "review": {
                    "memory_file": memory_file,
                    "written": not args.no_write_review,
                    "sample_count": review_summary["sample_count"],
                    "win_hits": review_summary.get("win_hits", 0),
                    "score_hits": review_summary.get("score_hits", 0),
                    "score_candidates": review_summary.get("score_candidates", 0),
                    "ou_hits": review_summary.get("ou_hits", 0),
                    "ou_candidates": review_summary.get("ou_candidates", 0),
                },
                "upset_sync": upset_sync,
                "memory_reconcile": memory_reconcile,
            }

        if not getattr(args, "skip_rag", False):
            from domain.rag import HybridRAGService

            service = HybridRAGService(EUROPE_LEAGUES_ROOT)
            rag_result = service.refresh(limit=getattr(args, "rag_limit", 300))
            result["rag"] = {
                "rag_mode": rag_result.get("rag_mode"),
                "case_count": rag_result.get("case_count"),
                "registry": rag_result.get("registry", {}),
            }

        from scripts.build_recent_five_leagues_review import build_recent_five_leagues_review
        from scripts.build_season_master_review import build_season_master_review

        recent_review = build_recent_five_leagues_review(
            base_dir=EUROPE_LEAGUES_ROOT,
            recent_days=args.recent_days,
            output_path=getattr(args, "recent_review_output", "") or None,
        )
        season_review = build_season_master_review(
            base_dir=EUROPE_LEAGUES_ROOT,
            season=args.season,
            recent_days=args.recent_days,
            batch_label=getattr(args, "batch_label", "") or None,
            master_output_path=getattr(args, "master_output_path", "") or None,
            recent_review_path=recent_review["output_path"],
        )
        return {
            **result,
            "recent_review": {
                "output_path": recent_review["output_path"],
                "window_start": recent_review["window_start"],
                "window_end": recent_review["window_end"],
                "completed_match_count": recent_review["completed_match_count"],
                "prediction_count": recent_review["prediction_count"],
                "league_sections": recent_review["league_sections"],
            },
            "season_review": season_review,
            "runtime_profile": get_command_runtime_profile("build-season-master-review"),
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("build-season-master-review", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    season_review = report["season_review"]
    print(f"赛季主文档: {season_review['output_path']}")
    print(f"窗口: {season_review['window_start']} -> {season_review['window_end']}")
    print(f"批次: {season_review['batch_heading']} ({season_review['action']})")
    print(f"已完赛样本: {season_review['completed_match_count']} 场")
    print(f"可复盘预测样本: {season_review['prediction_count']} 场")


def run_openclaw_rag_diagnose(args):
    def _execute():
        from domain.rag import HybridRAGService

        service = HybridRAGService(EUROPE_LEAGUES_ROOT)
        result = service.diagnose(limit=getattr(args, "limit", 200))
        result["runtime_profile"] = get_command_runtime_profile("rag-diagnose")
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("rag-diagnose", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_sync_memory_rag(args):
    def _execute():
        from pathlib import Path

        from scripts.sync_memory_rag_explanations import sync_memory_rag_explanations

        result = sync_memory_rag_explanations(
            base_dir=EUROPE_LEAGUES_ROOT,
            memory_path=Path(getattr(args, "memory_file", "") or os.path.join(PROJECT_ROOT, "MEMORY.md")).resolve(),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        result["runtime_profile"] = get_command_runtime_profile("sync-memory-rag")
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("sync-memory-rag", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(f"目标条目: {report['targets_with_explanation']}")
    print(f"命中记录: {report['matched_entries']}")
    print(f"更新记录: {report['updated_entries']}")
    print(f"已变更: {'是' if report['changed'] else '否'}")
    print(f"MEMORY 文件: {report['memory_file']}")
    print(f"模式: {'dry-run' if report['dry_run'] else 'write'}")


def run_openclaw_purge_nonreal_data(args):
    def _execute():
        from pathlib import Path

        from scripts.purge_nonreal_data import purge_nonreal_data

        result = purge_nonreal_data(
            base_dir=Path(EUROPE_LEAGUES_ROOT).resolve(),
            memory_path=Path(getattr(args, "memory_file", "") or os.path.join(PROJECT_ROOT, "MEMORY.md")).resolve(),
            confirm=bool(getattr(args, "yes", False)),
            sample_limit=int(getattr(args, "sample_limit", 100)),
            rag_limit=int(getattr(args, "rag_limit", 200)),
            refresh_accuracy=not bool(getattr(args, "no_refresh_accuracy", False)),
        )
        result["runtime_profile"] = get_command_runtime_profile("purge-nonreal-data")
        return result

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("purge-nonreal-data", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    summary = report.get("scan", {}).get("summary", {})
    print(f"扫描结果: MEMORY {summary.get('memory_blocks', 0)} 条 | archive {summary.get('archive_records', 0)} 条 | "
          f"registry {summary.get('registry_records', 0)} 条 | snapshots {summary.get('snapshot_files', 0)} 个 | "
          f"schedules {summary.get('schedule_files', 0)} 个")
    if not report.get("confirmed"):
        print(report.get("message") or "当前为预览模式。")
        return
    removed = report.get("removed", {})
    print(f"已清理: MEMORY {removed.get('memory_blocks', 0)} 条 | archive {removed.get('archive_records', 0)} 条 | "
          f"registry {removed.get('registry_records', 0)} 条 | snapshots {removed.get('snapshot_files', 0)} 个 | "
          f"schedules {removed.get('schedule_files', 0)} 个")
    rebuild = report.get("rebuild") or {}
    rag_rebuild = rebuild.get("rag_rebuild") or {}
    print(
        f"已重建: memory_samples={((rebuild.get('memory_samples') or {}).get('completed_samples', 0))} | "
        f"rag_cases={rag_rebuild.get('case_count', 0)} | "
        f"accuracy_refreshed={'是' if rebuild.get('accuracy_refresh') is not None else '否'}"
    )


def run_openclaw_health_check(args):
    def _execute():
        report = {
            "cwd": os.getcwd(),
            "python": sys.version.split()[0],
            "timestamp": datetime.now().isoformat(),
        }

        from result_manager import ResultManager
        from collectors.sporttery import DataCollector
        from domain.predictor import LEAGUE_CONFIG

        manager = ResultManager()
        collector = DataCollector()
        driver_status = get_okooo_driver_status()
        report["accuracy_file_exists"] = os.path.exists(manager.accuracy_file)
        report["browser_use_available"] = collector.browser_use_available
        report["browser_use_python_module_available"] = bool(driver_status["browser-use"]["module_available"])
        report["browser_use_cli_available"] = bool(driver_status["browser-use"]["cli_available"])
        report["local_chrome_available"] = bool(driver_status["local-chrome"]["available"])
        report["preferred_okooo_driver"] = "local-chrome" if driver_status["local-chrome"]["available"] else "browser-use"
        report["league_codes"] = list(LEAGUE_CONFIG.keys())
        report["scrapers"] = [scraper.name for scraper in collector.scrapers]
        report["openclaw_dependency_report"] = get_openclaw_dependency_report()
        if not driver_status["browser-use"]["available"]:
            report["recommended_action"] = {
                "owner": "openclaw",
                "reason": f"browser-use 不完整可用：{driver_status['browser-use']['reason']}；预测主链将优先使用 local-chrome",
                "install_command": f"bash {os.path.join(PROJECT_ROOT, 'scripts', 'setup_openclaw_env.sh')}",
            }
        if driver_status["local-chrome"]["available"]:
            report["driver_hint"] = "已检测到 local-chrome 可用，主预测链默认优先走 local-chrome 以减少等待时间"
        report["runtime_profile"] = get_command_runtime_profile("health-check")
        return report

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("health-check", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_refresh_repo_docs(args):
    def _execute():
        from scripts.refresh_repo_docs import refresh_repo_docs

        payload = refresh_repo_docs(
            repo_root=Path(EUROPE_LEAGUES_ROOT).resolve(),
            season=getattr(args, "season", "2025-26"),
            recent_days=int(getattr(args, "recent_days", 7)),
            full=bool(getattr(args, "full", False)),
            update_skill_docs=not bool(getattr(args, "skip_skill_docs", False)),
            run_tests=not bool(getattr(args, "skip_tests", False)),
        )
        payload["runtime_profile"] = get_command_runtime_profile("refresh-repo-docs")
        return payload

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("refresh-repo-docs", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_migrate_archive(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        return manager.migrate_prediction_archive_runtime_profiles()

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("migrate-archive", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_setup_guide(args):
    payload = {
        "project_root": PROJECT_ROOT,
        "venv_path": os.path.join(PROJECT_ROOT, ".venv"),
        "dependency_report": get_openclaw_dependency_report(),
        "next_command": "cd /Users/bytedance/trae_projects/europe_leagues && python3 prediction_system.py health-check --json",
    }
    emit_response(build_json_result("setup-openclaw", payload), as_json=args.json)


def run_harness_list(args):
    from harness import list_pipelines

    payload = list_pipelines()
    if args.json:
        emit_response(
            build_json_result(
                "harness-list",
                payload,
                runtime_profile=get_command_runtime_profile("harness-list"),
            ),
            as_json=True,
        )
        return

    print("可用 Harness Pipelines:")
    for item in payload:
        print(f"- {item['name']}: {item['description']}")


def run_harness_pipeline(args):
    from harness import build_pipeline

    analysis_context = load_analysis_context_file(getattr(args, "context_file", ""))

    inputs = {
        "league": getattr(args, "league", ""),
        "date": getattr(args, "date", ""),
        "home_team": getattr(args, "home_team", ""),
        "away_team": getattr(args, "away_team", ""),
        "match_id": getattr(args, "match_id", ""),
        "match_time": getattr(args, "match_time", ""),
        "league_hint": getattr(args, "league_hint", ""),
        "okooo_driver": getattr(args, "okooo_driver", "local-chrome"),
        "okooo_headed": bool(getattr(args, "okooo_headed", False)),
        "no_refresh_odds": bool(getattr(args, "no_refresh_odds", False)),
        "no_cache": bool(getattr(args, "no_cache", False)),
        "analysis_context": analysis_context,
        "home_score": getattr(args, "home_score", None),
        "away_score": getattr(args, "away_score", None),
        "refresh": bool(getattr(args, "refresh", False)),
    }
    pipeline = build_pipeline(args.pipeline)
    result = pipeline.execute(inputs)

    if args.json:
        emit_response(
            build_json_result(
                "harness-run",
                result,
                runtime_profile=result.get("runtime_profile") or get_command_runtime_profile("harness-run"),
            ),
            as_json=True,
        )
        return

    print(f"Pipeline: {result['pipeline']}")
    print(f"状态: {result['status']}")
    if result["error"]:
        print(f"错误: {result['error']}")
    for stage in result["stages"]:
        print(f"- {stage['stage']}: {stage['status']}")


def show_interactive_menu():
    """显示交互式菜单"""
    while True:
        print()
        print("╔" + "═" * 50 + "╗")
        print("║" + " " * 10 + "🏆 足球预测系统主菜单" + " " * 17 + "║")
        print("╚" + "═" * 50 + "╝")
        print()
        print("【预测功能】")
        print("  1. 运行增强版预测系统（推荐）")
        print("  2. 运行原始版预测系统")
        print("  3. 测试机器学习模型")
        print()
        print("【结果管理】")
        print("  4. 结果管理（录入比赛结果）")
        print("  5. 查看准确率统计")
        print("  6. 更新准确率统计")
        print()
        print("【其他功能】")
        print("  7. 列出可用联赛")
        print()
        print("  0. 退出")
        print()

        choice = input("请输入选项 (0-7): ").strip()
        if choice == "1":
            list_leagues()
            league_input = input("请输入联赛代码（留空则处理所有联赛）: ").strip()
            league_code = league_input if league_input else None
            days_input = input("请输入预测天数（默认3天）: ").strip()
            days = int(days_input) if days_input.isdigit() else 3
            run_enhanced_system(league_code, days)
        elif choice == "2":
            run_original_system()
        elif choice == "3":
            run_ml_test()
        elif choice == "4":
            from result_manager import interactive_update
            interactive_update()
        elif choice == "5":
            from result_manager import ResultManager, print_accuracy_report

            manager = ResultManager()
            try:
                with open(manager.accuracy_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
                    print_accuracy_report(stats)
            except Exception:
                stats = manager.update_accuracy_stats()
                print_accuracy_report(stats)
        elif choice == "6":
            from result_manager import ResultManager, print_accuracy_report

            manager = ResultManager()
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
        elif choice == "7":
            list_leagues()
        elif choice == "0":
            print("👋 再见！")
            break
        else:
            print("❌ 无效选项，请重试")


def build_parser():
    parser = argparse.ArgumentParser(
        description="足球比赛预测系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用示例:
  %(prog)s predict-match --league la_liga --home-team 巴塞罗那 --away-team 皇家马德里 --date 2026-05-11 --json
  %(prog)s predict-schedule --league la_liga --date 2026-05-11 --days 2 --json
  %(prog)s collect-data --league la_liga --date 2026-05-11 --json
  %(prog)s pending-results --days-back 14 --json
  %(prog)s save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
  %(prog)s accuracy --refresh --json
  %(prog)s sync-pending-results-review --days-back 30 --limit 20 --json
  %(prog)s purge-nonreal-data --yes --json
  %(prog)s setup-openclaw --json
        """,
    )

    subparsers = parser.add_subparsers(title="子命令", dest="command")

    parser_enhanced = subparsers.add_parser("enhanced", help="运行增强版预测系统")
    parser_enhanced.add_argument("-l", "--league", help="指定联赛代码")
    parser_enhanced.add_argument("-d", "--days", type=int, default=3, help="预测天数（默认3）")

    subparsers.add_parser("original", help="运行原始版预测系统")
    subparsers.add_parser("ml-test", help="测试机器学习模型")
    subparsers.add_parser("results", help="结果管理交互式菜单")
    subparsers.add_parser("show-accuracy", help="显示准确率统计")
    subparsers.add_parser("update-accuracy", help="更新准确率统计")

    parser_list = subparsers.add_parser("list-leagues", help="列出联赛，适合自动化调用")
    add_json_flag(parser_list)

    parser_predict_match = subparsers.add_parser("predict-match", help="预测单场比赛")
    parser_predict_match.add_argument("--league", required=True, help="联赛代码")
    parser_predict_match.add_argument("--home-team", required=True, help="主队名称")
    parser_predict_match.add_argument("--away-team", required=True, help="客队名称")
    parser_predict_match.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    parser_predict_match.add_argument("--match-id", default="", help="澳客 MatchID（可选；不传则自动搜索）")
    parser_predict_match.add_argument("--league-hint", default="", help="澳客联赛提示（可选，例如 西甲/英超）")
    parser_predict_match.add_argument("--okooo-driver", default="local-chrome", help="刷新澳客快照的 driver（默认 local-chrome，更快；不可用时再回退）")
    parser_predict_match.add_argument("--okooo-headed", action="store_true", help="browser-use 以有头模式运行（仅 browser-use 生效，更稳但更慢）")
    parser_predict_match.add_argument("--time", dest="match_time", default="", help="比赛时间 HH:MM（用于赛程精准定位）")
    parser_predict_match.add_argument("--no-refresh-odds", action="store_true", help="不刷新澳客实时赔率（仅使用本地/空赔率）")
    parser_predict_match.add_argument("--context-file", default="", help="补充信息 JSON 文件（战术/战意/首发/临场等）")
    parser_predict_match.add_argument("--no-write", action="store_true", help="只输出预测结果，不写入 MEMORY.md/归档")
    add_json_flag(parser_predict_match)

    parser_predict_match_lite = subparsers.add_parser("predict-match-lite", help="轻量预测单场比赛并写入滚动记忆（适合非正式支持联赛）")
    parser_predict_match_lite.add_argument("--league-name", required=True, help="联赛中文名，例如 葡超/英冠")
    parser_predict_match_lite.add_argument("--league", default="", help="联赛代码（可选；仅用于滚动记忆标识）")
    parser_predict_match_lite.add_argument("--home-team", required=True, help="主队名称")
    parser_predict_match_lite.add_argument("--away-team", required=True, help="客队名称")
    parser_predict_match_lite.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    parser_predict_match_lite.add_argument("--match-id", default="", help="澳客 MatchID（可选；不传则由快照脚本自行定位）")
    parser_predict_match_lite.add_argument("--okooo-driver", default="local-chrome", help="抓取澳客快照的 driver（默认 local-chrome）")
    parser_predict_match_lite.add_argument("--okooo-headed", action="store_true", help="browser-use 以有头模式运行（更稳但更慢）")
    parser_predict_match_lite.add_argument("--time", dest="match_time", default="", help="比赛时间 HH:MM（用于赛程精准定位）")
    parser_predict_match_lite.add_argument("--no-write", action="store_true", help="只输出预测结果，不写入 MEMORY.md")
    add_json_flag(parser_predict_match_lite)

    parser_predict_schedule = subparsers.add_parser("predict-schedule", help="按日期批量生成预测并写回")
    parser_predict_schedule.add_argument("--league", help="联赛代码；留空表示全部联赛")
    parser_predict_schedule.add_argument("--date", required=True, help="开始日期 YYYY-MM-DD")
    parser_predict_schedule.add_argument("--days", type=int, default=1, help="连续处理天数")
    add_json_flag(parser_predict_schedule)

    parser_collect = subparsers.add_parser("collect-data", help="抓取或降级采集比赛数据")
    parser_collect.add_argument("--league", required=True, help="联赛代码")
    parser_collect.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    parser_collect.add_argument("--no-cache", action="store_true", help="跳过缓存")
    add_json_flag(parser_collect)

    parser_pending = subparsers.add_parser("pending-results", help="列出待更新结果的比赛")
    parser_pending.add_argument("--days-back", type=int, default=7, help="向前查询的天数")
    add_json_flag(parser_pending)

    parser_save = subparsers.add_parser("save-result", help="写入单场比赛结果")
    parser_save.add_argument("--match-id", required=True, help="比赛 ID")
    parser_save.add_argument("--home-score", required=True, type=int, help="主队进球")
    parser_save.add_argument("--away-score", required=True, type=int, help="客队进球")
    parser_save.add_argument("--force", action="store_true", help="覆盖已存在的比分并重建复盘备注")
    add_json_flag(parser_save)

    parser_auto_sync = subparsers.add_parser("auto-sync-results", help="自动同步到期的比赛结果")
    parser_auto_sync.add_argument("--limit", type=int, default=20, help="单次最多处理的到期比赛数")
    add_json_flag(parser_auto_sync)

    parser_sync_daemon = subparsers.add_parser("result-sync-daemon", help="常驻轮询并自动同步赛果")
    parser_sync_daemon.add_argument("--interval-minutes", type=int, default=10, help="轮询间隔分钟数")
    parser_sync_daemon.add_argument("--max-cycles", type=int, default=0, help="最大轮询次数，0 表示持续运行")
    add_json_flag(parser_sync_daemon)

    parser_accuracy = subparsers.add_parser("accuracy", help="获取准确率统计")
    parser_accuracy.add_argument("--refresh", action="store_true", help="重新计算准确率")
    add_json_flag(parser_accuracy)

    parser_sync_review = subparsers.add_parser("sync-pending-results-review", help="一键执行待回填检查、结果刷新与复盘总结更新")
    parser_sync_review.add_argument("--days-back", type=int, default=30, help="向前查询的天数")
    parser_sync_review.add_argument("--limit", type=int, default=20, help="单次最多处理的到期比赛数")
    parser_sync_review.add_argument("--memory-file", default=os.path.join(PROJECT_ROOT, "MEMORY.md"), help="目标 MEMORY.md 路径")
    parser_sync_review.add_argument("--review-sample-limit", type=int, default=8, help="复盘总结纳入的最近已完赛样本数")
    parser_sync_review.add_argument("--no-write-review", action="store_true", help="只生成复盘数据，不写回 MEMORY.md")
    add_json_flag(parser_sync_review)

    parser_season_review = subparsers.add_parser("build-season-master-review", help="同步赛果、重建 RAG 并更新五大联赛赛季主复盘文档")
    parser_season_review.add_argument("--season", default="2025-26", help="赛季标识")
    parser_season_review.add_argument("--recent-days", type=int, default=7, help="赛季主文档批次使用的统计窗口天数")
    parser_season_review.add_argument("--days-back", type=int, default=30, help="同步赛果时向前查询的天数")
    parser_season_review.add_argument("--limit", type=int, default=50, help="单次最多处理的到期比赛数")
    parser_season_review.add_argument("--review-sample-limit", type=int, default=8, help="MEMORY 复盘总结纳入的最近样本数")
    parser_season_review.add_argument("--batch-label", default="", help="可选批次标题，如“第 2 轮”")
    parser_season_review.add_argument("--recent-review-output", default="", help="最近窗口复盘 markdown 输出路径")
    parser_season_review.add_argument("--master-output-path", default="", help="赛季总文档输出路径")
    parser_season_review.add_argument("--rag-limit", type=int, default=300, help="重建 RAG 时最多读取的样本数")
    parser_season_review.add_argument("--memory-file", default=os.path.join(PROJECT_ROOT, "MEMORY.md"), help="目标 MEMORY.md 路径")
    parser_season_review.add_argument("--no-write-review", action="store_true", help="只生成复盘数据，不写回 MEMORY.md")
    parser_season_review.add_argument("--skip-sync", action="store_true", help="跳过赛果同步与 MEMORY 复盘写回")
    parser_season_review.add_argument("--skip-rag", action="store_true", help="跳过 RAG 重建")
    add_json_flag(parser_season_review)

    parser_refresh_docs = subparsers.add_parser("refresh-repo-docs", help="一键刷新仓库关键生成文档与相关 skill 文档（默认 quick 模式）")
    parser_refresh_docs.add_argument("--season", default="2025-26", help="赛季标识")
    parser_refresh_docs.add_argument("--recent-days", type=int, default=7, help="统计窗口天数")
    parser_refresh_docs.add_argument("--full", action="store_true", help="全量刷新：包含赛果同步与 RAG 重建（较慢）")
    parser_refresh_docs.add_argument("--skip-skill-docs", action="store_true", help="跳过 skill 文档同步")
    parser_refresh_docs.add_argument("--skip-tests", action="store_true", help="跳过最小回归测试")
    add_json_flag(parser_refresh_docs)

    parser_rag_rebuild = subparsers.add_parser("rag-rebuild", help="重建完整 RAG 混合检索索引")
    parser_rag_rebuild.add_argument("--limit", type=int, default=200, help="构建时最多读取的滚动记忆样本数")
    add_json_flag(parser_rag_rebuild)

    parser_rag_diagnose = subparsers.add_parser("rag-diagnose", help="查看 RAG 索引状态与规模")
    parser_rag_diagnose.add_argument("--limit", type=int, default=200, help="诊断时缺索引则自动构建的样本数")
    add_json_flag(parser_rag_diagnose)

    parser_sync_memory_rag = subparsers.add_parser("sync-memory-rag", help="将归档中的 RAG 记忆解释回填到 MEMORY.md")
    parser_sync_memory_rag.add_argument("--memory-file", default=os.path.join(PROJECT_ROOT, "MEMORY.md"), help="目标 MEMORY.md 路径")
    parser_sync_memory_rag.add_argument("--dry-run", action="store_true", help="仅检查可更新条目，不写文件")
    add_json_flag(parser_sync_memory_rag)

    parser_purge_nonreal = subparsers.add_parser("purge-nonreal-data", help="全盘扫描并清理非真实赛程/缓存/归档数据")
    parser_purge_nonreal.add_argument("--memory-file", default=os.path.join(PROJECT_ROOT, "MEMORY.md"), help="目标 MEMORY.md 路径")
    parser_purge_nonreal.add_argument("--sample-limit", type=int, default=100, help="重建滚动样本时最多读取的样本数")
    parser_purge_nonreal.add_argument("--rag-limit", type=int, default=200, help="重建 RAG 时最多读取的样本数")
    parser_purge_nonreal.add_argument("--no-refresh-accuracy", action="store_true", help="清理后不刷新准确率")
    parser_purge_nonreal.add_argument("--yes", action="store_true", help="确认执行删除与重建；不传时仅预览扫描结果")
    add_json_flag(parser_purge_nonreal)

    parser_health = subparsers.add_parser("health-check", help="检查核心依赖与运行状态")
    add_json_flag(parser_health)

    parser_migrate_archive = subparsers.add_parser("migrate-archive", help="迁移并补全 prediction_archive.json 元数据")
    add_json_flag(parser_migrate_archive)

    parser_setup = subparsers.add_parser("setup-openclaw", help="输出 openclaw 初始化安装指引")
    add_json_flag(parser_setup)

    parser_harness_list = subparsers.add_parser("harness-list", help="列出 Harness 风格的可编排 pipeline")
    add_json_flag(parser_harness_list)

    parser_harness_run = subparsers.add_parser("harness-run", help="通过 Harness pipeline 执行任务")
    parser_harness_run.add_argument("--pipeline", required=True, help="pipeline 名称")
    parser_harness_run.add_argument("--league", default="", help="联赛代码")
    parser_harness_run.add_argument("--date", default="", help="比赛日期 YYYY-MM-DD")
    parser_harness_run.add_argument("--home-team", default="", help="主队名称")
    parser_harness_run.add_argument("--away-team", default="", help="客队名称")
    parser_harness_run.add_argument("--match-id", default="", help="比赛 ID")
    parser_harness_run.add_argument("--league-hint", default="", help="联赛提示")
    parser_harness_run.add_argument("--time", dest="match_time", default="", help="比赛时间 HH:MM")
    parser_harness_run.add_argument("--okooo-driver", default="local-chrome", help="赔率刷新 driver（默认 local-chrome）")
    parser_harness_run.add_argument("--okooo-headed", action="store_true", help="是否有头运行（仅 browser-use 生效）")
    parser_harness_run.add_argument("--no-refresh-odds", action="store_true", help="不刷新赔率")
    parser_harness_run.add_argument("--no-cache", action="store_true", help="跳过采集缓存")
    parser_harness_run.add_argument("--context-file", default="", help="补充信息 JSON 文件")
    parser_harness_run.add_argument("--home-score", type=int, help="主队进球")
    parser_harness_run.add_argument("--away-score", type=int, help="客队进球")
    parser_harness_run.add_argument("--refresh", action="store_true", help="result_recording 后刷新准确率")
    add_json_flag(parser_harness_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    os.chdir(EUROPE_LEAGUES_ROOT)

    if args.command is None:
        print_header()
        show_interactive_menu()
        return

    if args.command == "enhanced":
        print_header()
        run_enhanced_system(args.league, args.days)
    elif args.command == "original":
        print_header()
        run_original_system()
    elif args.command == "ml-test":
        print_header()
        run_ml_test()
    elif args.command == "results":
        print_header()
        from result_manager import interactive_update

        interactive_update()
    elif args.command == "show-accuracy":
        print_header()
        from result_manager import ResultManager, print_accuracy_report

        manager = ResultManager()
        try:
            with open(manager.accuracy_file, "r", encoding="utf-8") as f:
                stats = json.load(f)
                print_accuracy_report(stats)
        except Exception:
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
    elif args.command == "update-accuracy":
        print_header()
        from result_manager import ResultManager, print_accuracy_report

        manager = ResultManager()
        stats = manager.update_accuracy_stats()
        print_accuracy_report(stats)
    elif args.command == "list-leagues":
        run_openclaw_list_leagues(args.json)
    elif args.command == "predict-match":
        run_openclaw_predict_match(args)
    elif args.command == "predict-match-lite":
        run_openclaw_predict_match_lite(args)
    elif args.command == "predict-schedule":
        run_openclaw_predict_schedule(args)
    elif args.command == "collect-data":
        run_openclaw_collect_data(args)
    elif args.command == "pending-results":
        run_openclaw_pending_results(args)
    elif args.command == "save-result":
        run_openclaw_save_result(args)
    elif args.command == "auto-sync-results":
        run_openclaw_auto_sync_results(args)
    elif args.command == "result-sync-daemon":
        run_openclaw_result_sync_daemon(args)
    elif args.command == "accuracy":
        run_openclaw_accuracy(args)
    elif args.command == "sync-pending-results-review":
        run_openclaw_sync_pending_results_review(args)
    elif args.command == "build-season-master-review":
        run_openclaw_build_season_master_review(args)
    elif args.command == "refresh-repo-docs":
        run_openclaw_refresh_repo_docs(args)
    elif args.command == "rag-rebuild":
        run_openclaw_rag_rebuild(args)
    elif args.command == "rag-diagnose":
        run_openclaw_rag_diagnose(args)
    elif args.command == "sync-memory-rag":
        run_openclaw_sync_memory_rag(args)
    elif args.command == "purge-nonreal-data":
        run_openclaw_purge_nonreal_data(args)
    elif args.command == "health-check":
        run_openclaw_health_check(args)
    elif args.command == "migrate-archive":
        run_openclaw_migrate_archive(args)
    elif args.command == "setup-openclaw":
        run_openclaw_setup_guide(args)
    elif args.command == "harness-list":
        run_harness_list(args)
    elif args.command == "harness-run":
        run_harness_pipeline(args)


if __name__ == "__main__":
    main()
