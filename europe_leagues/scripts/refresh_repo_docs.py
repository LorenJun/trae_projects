#!/usr/bin/env python3
"""Repo docs refresh helper.

Purpose:
- Provide a single entrypoint to refresh the key generated markdown docs after code updates.
- Keep the skill doc(s) in sync with the latest generated structure.

Design notes:
- Default behavior is "quick": do NOT sync results and do NOT rebuild RAG (no network / no data mutation).
- Users can enable full refresh explicitly.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


def _project_root() -> Path:
    # .../europe_leagues/scripts/refresh_repo_docs.py -> .../europe_leagues
    return Path(__file__).resolve().parents[1]


PROJECT_ROOT = _project_root()


def _run(cmd: list[str], *, cwd: Path) -> Dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _replace_markdown_section(content: str, heading: str, new_block: str) -> str:
    # Replace from the heading until the next "## " section (or EOF).
    pattern = rf"(?ms)^{re.escape(heading)}\n.*?(?=^## |\Z)"
    if re.search(pattern, content):
        return re.sub(pattern, new_block.rstrip() + "\n\n", content, count=1)
    return content.rstrip() + "\n\n" + new_block.rstrip() + "\n"


def _update_five_leagues_skill_doc(repo_root: Path) -> Dict[str, Any]:
    skill_path = repo_root.parent / ".trae" / "skills" / "five-leagues-season-review-rag" / "SKILL.md"
    if not skill_path.exists():
        return {"updated": False, "path": str(skill_path), "reason": "missing"}

    original = skill_path.read_text(encoding="utf-8")
    updated = original

    canonical_block = (
        "## 推荐文档结构\n\n"
        "赛季总文档建议保持以下结构：\n\n"
        "```md\n"
        "# 2025-26 五大联赛赛季统一复盘数据源\n\n"
        "## 赛季总览\n\n"
        "## 最近窗口基线\n\n"
        "## 最新批次\n\n"
        "### 第 X 轮 / 第 X 批次（YYYY-MM-DD 至 YYYY-MM-DD）\n\n"
        "#### 英超\n\n"
        "#### 西甲\n\n"
        "#### 意甲\n\n"
        "#### 德甲\n\n"
        "#### 法甲\n\n"
        "## 跨联赛整理结论\n"
        "```\n\n"
        "最近窗口复盘文档默认建议保持以下结构：\n\n"
        "```md\n"
        "# 五大联赛近7天已完赛复盘\n\n"
        "## 总体快照\n\n"
        "## 全局 Top 问题\n\n"
        "## 联赛 Top 问题一览\n\n"
        "## 三层校准观察\n\n"
        "## 统一整改优先级\n"
        "```\n\n"
        "其中 `Top 问题` 必须按固定四组展示，便于跨联赛/跨窗口对比：\n\n"
        "- `覆盖`：无预测已完赛、归档覆盖不足\n"
        "- `方向`：主胜/平局/客胜方向偏差与冷门漏判\n"
        "- `比分`：比分未命中、比分模板偏置\n"
        "- `盘口`：亚盘/欧赔/大小球/实力差等市场与数据缺口，以及三层规则未命中原因\n\n"
        "最近窗口复盘文档和赛季总文档中，默认都应包含以下附加章节或字段（可精简展示，但不可缺失口径）：\n\n"
        "- `三层校准规则复盘`\n"
        "- `未命中原因分解`\n"
        "- `自动整改建议`\n"
    )

    updated = _replace_markdown_section(updated, "## 推荐文档结构", canonical_block)

    if updated != original:
        skill_path.write_text(updated, encoding="utf-8")
        return {"updated": True, "path": str(skill_path)}
    return {"updated": False, "path": str(skill_path), "reason": "no_change"}


def refresh_repo_docs(
    *,
    repo_root: Path,
    season: str = "2025-26",
    recent_days: int = 7,
    full: bool = False,
    update_skill_docs: bool = True,
    run_tests: bool = True,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "repo_root": str(repo_root),
        "season": season,
        "recent_days": recent_days,
        "mode": "full" if full else "quick",
        "skill_docs": None,
        "season_review": None,
        "tests": None,
    }

    if update_skill_docs:
        result["skill_docs"] = {
            "five_leagues_season_review_rag": _update_five_leagues_skill_doc(repo_root),
        }

    # Always rebuild the generated markdown docs via the official CLI command.
    cmd = [
        sys.executable,
        "prediction_system.py",
        "build-season-master-review",
        "--season",
        season,
        "--recent-days",
        str(recent_days),
        "--json",
    ]
    if not full:
        cmd.extend(["--skip-sync", "--skip-rag"])
    season_review_exec = _run(cmd, cwd=repo_root)
    result["season_review"] = {
        "returncode": season_review_exec["returncode"],
        "stdout_tail": season_review_exec["stdout"][-4000:],
        "stderr_tail": season_review_exec["stderr"][-4000:],
    }

    if run_tests:
        test_exec = _run([sys.executable, "-m", "unittest", "test_season_review_builder.py"], cwd=repo_root)
        result["tests"] = {
            "returncode": test_exec["returncode"],
            "stdout_tail": test_exec["stdout"][-4000:],
            "stderr_tail": test_exec["stderr"][-4000:],
        }

    result["success"] = bool(
        result["season_review"]["returncode"] == 0 and (not run_tests or result["tests"]["returncode"] == 0)
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="更新整仓库关键生成文档与相关 skill 文档（默认 quick 模式）")
    parser.add_argument("--season", default="2025-26", help="赛季标识")
    parser.add_argument("--recent-days", type=int, default=7, help="统计窗口天数")
    parser.add_argument("--full", action="store_true", help="全量刷新：包含赛果同步与 RAG 重建（较慢）")
    parser.add_argument("--skip-skill-docs", action="store_true", help="跳过 skill 文档同步")
    parser.add_argument("--skip-tests", action="store_true", help="跳过最小回归测试")
    args = parser.parse_args()

    payload = refresh_repo_docs(
        repo_root=PROJECT_ROOT,
        season=str(args.season),
        recent_days=int(args.recent_days),
        full=bool(args.full),
        update_skill_docs=not bool(args.skip_skill_docs),
        run_tests=not bool(args.skip_tests),
    )
    print(payload)
    if not payload.get("success"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
