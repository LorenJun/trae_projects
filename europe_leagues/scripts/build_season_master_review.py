from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.rag_store import LEAGUE_CN_NAMES
from scripts.build_recent_five_leagues_review import LEAGUE_ORDER, build_recent_five_leagues_review, summarize_recent_five_leagues_cases

DEFAULT_SEASON = "2025-26"


def _default_master_review_path(base_dir: str, season: str) -> Path:
    return Path(base_dir) / "runtime" / "season_reviews" / f"{season}_five_leagues_master_review.md"


def _resolve_as_of_date(as_of_date: Optional[object] = None) -> date:
    if isinstance(as_of_date, datetime):
        return as_of_date.date()
    if isinstance(as_of_date, date):
        return as_of_date
    if isinstance(as_of_date, str) and as_of_date.strip():
        text = as_of_date.strip()
        try:
            return date.fromisoformat(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    return date.fromisoformat(text[:10])
                except ValueError:
                    return date.today()
    return date.today()


def _format_backtick_value(value: str) -> str:
    return f"`{value}`"


def _display_path(path_text: str) -> str:
    path = Path(path_text)
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _render_initial_master_document(season: str) -> str:
    return (
        f"# {season} 五大联赛赛季统一复盘数据源\n\n"
        "> 本文档保留赛季累计视角下的核心轮次记录，重点沉淀每个联赛的 Top 问题、代表样本和下一步动作。\n\n"
        "## 赛季总览\n\n"
        f"- 赛季：`{season}`\n"
        "- 联赛范围：`英超 / 西甲 / 意甲 / 德甲 / 法甲`\n"
        "- 当前状态：`初始化`\n"
        "- 正式赛果以 `europe_leagues/<league>/teams_2025-26.md` 为准。\n"
        "- 预测与复盘补充来源为 `prediction_archive.json`、`prediction_memory_odds_samples.json`、`rag_cases.json` 和 `runtime/recent_five_leagues_review_<date>.md`。\n\n"
        "## 最近窗口基线\n\n"
        "- 来源文档：`待生成`\n"
        "- 已完赛样本：`0`\n"
        "- RAG 完成态覆盖：`0`\n"
        "- 可恢复预测样本：`0`\n"
        "- 已完赛但无预测：`0`\n"
        "- 当前窗口结论：`待更新`\n\n"
        "## 最新批次\n"
    )


def _replace_section_by_heading(document: str, heading: str, new_block: str) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\n.*?(?=^## |^### |\Z)"
    if re.search(pattern, document):
        return re.sub(pattern, new_block.rstrip() + "\n\n", document, count=1)
    return document.rstrip() + "\n\n" + new_block.rstrip() + "\n"


def _upsert_recent_baseline(document: str, summary: Dict[str, Any], recent_review_path: str) -> str:
    top_summary = str(summary.get("overall_top_problem_group_text") or "覆盖：无明显缺口；方向：无显著偏差；比分：无显著偏差；盘口：无显著风险")
    baseline_block = (
        "## 最近窗口基线\n\n"
        f"- 来源文档：`{_display_path(recent_review_path)}`\n"
        f"- 已完赛样本：`{summary['completed_match_count']}`\n"
        f"- RAG 完成态覆盖：`{summary['completed_match_count']}`\n"
        f"- 可恢复预测样本：`{summary['prediction_count']}`\n"
        f"- 已完赛但无预测：`{summary['unpredicted_completed_count']}`\n"
        f"- 当前窗口结论：`{top_summary}`\n"
    )
    return _replace_section_by_heading(document, "## 最近窗口基线", baseline_block)


def _find_existing_batch_heading(document: str, window_start: str, window_end: str) -> Optional[str]:
    match = re.search(
        rf"^###\s+(.+?（{re.escape(window_start)} 至 {re.escape(window_end)}）)\s*$",
        document,
        flags=re.MULTILINE,
    )
    if match:
        return f"### {match.group(1)}"
    return None


def _teams_file_path(base_dir: str, league_code: str) -> Path:
    return Path(base_dir) / league_code / "teams_2025-26.md"


def _extract_round_number(round_label: str) -> Optional[int]:
    match = re.search(r"第\s*(\d+)\s*轮", str(round_label or ""))
    if not match:
        return None
    return int(match.group(1))


def _parse_round_sections(teams_file: Path) -> List[Dict[str, Any]]:
    if not teams_file.exists():
        return []

    sections: List[Dict[str, Any]] = []
    current_section: Optional[Dict[str, Any]] = None
    try:
        lines = teams_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for raw_line in lines:
        line = raw_line.strip()
        heading_match = re.match(r"^###\s+(第\s*\d+\s*轮(?:（[^）]+）)?)\s*$", line)
        if heading_match:
            if current_section:
                sections.append(current_section)
            current_section = {"heading": heading_match.group(1), "dates": set()}
            continue

        if current_section is None:
            continue

        row_match = re.match(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line)
        if row_match:
            current_section["dates"].add(row_match.group(1))

    if current_section:
        sections.append(current_section)
    return sections


def _collapse_round_labels(round_labels: List[str]) -> str:
    unique_labels = []
    for label in round_labels:
        cleaned = re.sub(r"（[^）]+）", "", label).strip()
        if cleaned and cleaned not in unique_labels:
            unique_labels.append(cleaned)
    round_numbers = [_extract_round_number(label) for label in unique_labels]
    if unique_labels and all(number is not None for number in round_numbers):
        numbers = sorted(set(int(number) for number in round_numbers if number is not None))
        if len(numbers) == 1:
            return f"第{numbers[0]}轮"
        if numbers == list(range(numbers[0], numbers[-1] + 1)):
            return f"第{numbers[0]}-{numbers[-1]}轮"
    return "/".join(unique_labels)


def _resolve_league_round_label(base_dir: str, league_code: str, completed_dates: List[str]) -> str:
    if not completed_dates:
        return ""

    matched_rounds: List[str] = []
    for section in _parse_round_sections(_teams_file_path(base_dir, league_code)):
        if set(completed_dates) & set(section.get("dates") or set()):
            matched_rounds.append(str(section.get("heading") or ""))

    if not matched_rounds:
        return ""
    return _collapse_round_labels(matched_rounds)


def _build_auto_batch_label(base_dir: str, summary: Dict[str, Any]) -> str:
    league_parts = []
    for league_code in LEAGUE_ORDER:
        section = summary["league_sections"].get(league_code) or {}
        if not section.get("completed_count"):
            continue
        round_label = _resolve_league_round_label(base_dir, league_code, list(section.get("completed_dates") or []))
        if round_label:
            league_parts.append(f"{LEAGUE_CN_NAMES.get(league_code, league_code)}{round_label}")

    return " / ".join(league_parts)


def _next_batch_heading(base_dir: str, document: str, summary: Dict[str, Any], batch_label: Optional[str]) -> str:
    window_start = summary["window_start"]
    window_end = summary["window_end"]
    if batch_label:
        if "（" in batch_label or "(" in batch_label:
            return f"### {batch_label}"
        return f"### {batch_label}（{window_start} 至 {window_end}）"
    auto_label = _build_auto_batch_label(base_dir, summary)
    if auto_label:
        return f"### {auto_label}（{window_start} 至 {window_end}）"
    existing_numbers = [int(value) for value in re.findall(r"^### 第\s*(\d+)\s*批次", document, flags=re.MULTILINE)]
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1
    return f"### 第 {next_number} 批次（{window_start} 至 {window_end}）"


def _format_match_summary(item: Dict[str, Any]) -> str:
    return f"{item.get('home_team')} {item.get('actual_score') or '-'} {item.get('away_team')}"


def _render_unpredicted_completed_matches(section: Dict[str, Any]) -> str:
    matches = section.get("unpredicted_completed_matches") or []
    if not matches:
        return _format_backtick_value("无")
    values = [_format_match_summary(item) for item in matches]
    return _format_backtick_value(" / ".join(values))


def _build_top_problem_text(section: Dict[str, Any]) -> str:
    text = str(section.get("top_problem_group_text") or "").strip()
    if not text:
        text = "覆盖：无明显缺口；方向：无显著偏差；比分：无显著偏差；盘口：无显著风险"
    return _format_backtick_value(text)


def _build_league_conclusion(league_code: str, section: Dict[str, Any]) -> str:
    tags = []
    league_name = LEAGUE_CN_NAMES.get(league_code, league_code)
    for item in section.get("league_tags") or []:
        text = str(item).strip()
        prefix = f"{league_name}-"
        tags.append(text[len(prefix) :] if text.startswith(prefix) else text)
    if tags:
        core = "；".join(tags[:2])
    else:
        core = "当前窗口样本有限，先以补齐覆盖和继续观察为主"

    suffixes: List[str] = []
    prediction_count = int(section.get("prediction_count") or 0)
    if prediction_count > 0 and int(section.get("score_hits") or 0) == 0:
        suffixes.append("比分层明显弱于方向层")
    unpredicted = int(section.get("unpredicted_completed_count") or 0)
    if unpredicted > 0 and unpredicted >= max(2, prediction_count):
        suffixes.append(f"另有 {unpredicted} 场无预测样本")
    if suffixes:
        return f"{core}，{'，'.join(suffixes)}。"
    return f"{core}。"


def _render_auto_remediation_advice(section: Dict[str, Any]) -> str:
    rows = section.get("auto_remediation_advice") or []
    if not rows:
        return "保持现有校准配置，继续累积样本后再判断是否需要新增分层。"
    return " / ".join(str(item) for item in rows[:2])


def _render_representative_examples(section: Dict[str, Any]) -> str:
    mismatches = section.get("representative_mismatches") or []
    if mismatches:
        return _format_backtick_value(" / ".join(_format_match_summary(item) for item in mismatches[:3]))
    missing = section.get("unpredicted_completed_matches") or []
    if missing:
        return _format_backtick_value(" / ".join(_format_match_summary(item) for item in missing[:3]))
    return _format_backtick_value("无")


def _render_cross_league_conclusion(summary: Dict[str, Any]) -> str:
    grouped_text = str(summary.get("overall_top_problem_group_text") or "")
    lines = ["## 跨联赛整理结论", ""]
    if grouped_text:
        lines.append(f"- 当前窗口四组问题总览：`{grouped_text}`。")
    else:
        lines.append("- 当前窗口四组问题总览：`覆盖：无明显缺口；方向：无显著偏差；比分：无显著偏差；盘口：无显著风险`。")
    lines.append("- 下一轮更新时优先保留：`核心战绩 / Top 问题 / 下一步动作 / 代表样本`。")
    return "\n".join(lines)


def _render_league_block(league_code: str, section: Dict[str, Any]) -> str:
    league_name = LEAGUE_CN_NAMES.get(league_code, league_code)
    win_ratio = f"{section.get('win_hits', 0)}/{section.get('prediction_count', 0)}"
    score_ratio = f"{section.get('score_hits', 0)}/{section.get('prediction_count', 0)}"
    ou_ratio = f"{section.get('ou_hits', 0)}/{section.get('ou_samples', 0)}"
    return (
        f"#### {league_name}\n\n"
        f"- 核心战绩：已完赛 `{section.get('completed_count', 0)}`，可复盘 `{section.get('prediction_count', 0)}`，无预测 `{section.get('unpredicted_completed_count', 0)}`；"
        f"胜平负 `{win_ratio}`，比分 `{score_ratio}`，大小球 `{ou_ratio}`。\n"
        f"- Top 问题：{_build_top_problem_text(section)}\n"
        f"- 关键结论：{_build_league_conclusion(league_code, section)}\n"
        f"- 下一步动作：{_render_auto_remediation_advice(section)}\n"
        f"- 代表样本：{_render_representative_examples(section)}\n"
        f"- 无预测样本：{_render_unpredicted_completed_matches(section)}\n"
    )


def _render_batch_block(batch_heading: str, summary: Dict[str, Any]) -> str:
    lines = [
        batch_heading,
        "",
        f"> 统计窗口：{summary['window_start']} 至 {summary['window_end']}",
        "",
    ]
    for league_code in LEAGUE_ORDER:
        lines.append(_render_league_block(league_code, summary["league_sections"].get(league_code) or {}))
        lines.append("")
    lines.append(_render_cross_league_conclusion(summary))
    return "\n".join(lines).rstrip()


def _upsert_batch_block(document: str, batch_heading: str, batch_block: str) -> Tuple[str, str]:
    pattern = rf"(?ms)^{re.escape(batch_heading)}\n.*?(?=^### |\Z)"
    if re.search(pattern, document):
        updated = re.sub(pattern, batch_block.rstrip() + "\n\n", document, count=1)
        return updated, "replaced"

    container_heading = "## 最新批次" if "## 最新批次" in document else "## 已记录轮次"
    if container_heading not in document:
        container_heading = "## 最新批次"
        document = document.rstrip() + "\n\n## 最新批次\n"
    insert_at = document.find(container_heading)
    section_start = insert_at + len(container_heading)
    prefix = document[:section_start].rstrip()
    suffix = document[section_start:].strip()
    combined = prefix + "\n\n"
    if suffix:
        combined += batch_block.rstrip() + "\n\n" + suffix + "\n"
        return combined, "appended"
    combined += batch_block.rstrip() + "\n"
    return combined, "appended"


def build_season_master_review(
    base_dir: str,
    season: str = DEFAULT_SEASON,
    recent_days: int = 7,
    batch_label: Optional[str] = None,
    master_output_path: Optional[str] = None,
    recent_review_path: Optional[str] = None,
    as_of_date: Optional[object] = None,
) -> Dict[str, Any]:
    summary = summarize_recent_five_leagues_cases(base_dir, recent_days=recent_days, as_of_date=as_of_date)
    resolved_as_of_date = _resolve_as_of_date(as_of_date)
    recent_payload = None
    if recent_review_path:
        recent_review_file = Path(recent_review_path)
    else:
        recent_payload = build_recent_five_leagues_review(
            base_dir=base_dir,
            recent_days=recent_days,
            as_of_date=resolved_as_of_date,
        )
        recent_review_file = Path(recent_payload["output_path"])

    target_path = Path(master_output_path) if master_output_path else _default_master_review_path(base_dir, season)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        try:
            document = target_path.read_text(encoding="utf-8")
        except Exception:
            document = _render_initial_master_document(season)
    else:
        document = _render_initial_master_document(season)

    document = re.sub(
        r"- 当前状态：`.*?`",
        f"- 当前状态：`已更新至 {summary['window_end']}`",
        document,
        count=1,
    )
    document = _upsert_recent_baseline(document, summary, str(recent_review_file))

    existing_batch_heading = _find_existing_batch_heading(document, summary["window_start"], summary["window_end"])
    batch_heading = _next_batch_heading(base_dir, document, summary, batch_label)
    batch_block = _render_batch_block(batch_heading, summary)
    document, action = _upsert_batch_block(document, existing_batch_heading or batch_heading, batch_block)

    target_path.write_text(document.rstrip() + "\n", encoding="utf-8")
    return {
        "output_path": str(target_path),
        "recent_review_path": str(recent_review_file),
        "season": season,
        "batch_heading": batch_heading,
        "action": action,
        "window_start": summary["window_start"],
        "window_end": summary["window_end"],
        "completed_match_count": summary["completed_match_count"],
        "prediction_count": summary["prediction_count"],
        "league_sections": summary["league_sections"],
        "recent_review_generated": bool(recent_payload),
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将最近窗口复盘追加到赛季统一 master review 文档")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="europe_leagues 根目录")
    parser.add_argument("--season", default=DEFAULT_SEASON, help="赛季标识")
    parser.add_argument("--recent-days", type=int, default=7, help="统计窗口天数")
    parser.add_argument("--batch-label", default="", help="批次标题，如“第 2 轮”")
    parser.add_argument("--master-output-path", default="", help="目标 master review 路径")
    parser.add_argument("--recent-review-path", default="", help="已有最近窗口复盘文档路径")
    parser.add_argument("--as-of-date", default="", help="统计截止日期 YYYY-MM-DD，默认今天")
    return parser


if __name__ == "__main__":
    cli_args = _build_arg_parser().parse_args()
    payload = build_season_master_review(
        base_dir=cli_args.base_dir,
        season=cli_args.season,
        recent_days=cli_args.recent_days,
        batch_label=cli_args.batch_label or None,
        master_output_path=cli_args.master_output_path or None,
        recent_review_path=cli_args.recent_review_path or None,
        as_of_date=cli_args.as_of_date or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
