#!/usr/bin/env python3
"""模块说明：将 prediction_archive.json 中已有的 RAG 记忆解释回填到 MEMORY.md 对应预测记录。"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _europe_root() -> Path:
    return Path(__file__).resolve().parents[1]


EUROPE_ROOT = _europe_root()
if str(EUROPE_ROOT) not in sys.path:
    sys.path.insert(0, str(EUROPE_ROOT))


def _sanitize_explanation(text: str) -> str:
    return str(text or "").replace("|", "/").strip()


def _unescape_memory_line(text: str) -> str:
    return str(text or "").replace("\\[", "[").replace("\\]", "]").replace("\\_", "_")


def _load_archive(base_dir: str) -> Dict[str, Dict[str, Any]]:
    from storage.archive import PredictionArchiveStore

    return PredictionArchiveStore(base_dir).load()


def _extract_prediction_payload(archived: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(archived or {})
    full_prediction = archived.get("full_prediction")
    if isinstance(full_prediction, dict):
        merged = dict(full_prediction)
        merged.update({k: v for k, v in payload.items() if v not in (None, "", {}, [])})
        payload = merged
    return payload


def _iter_archive_explanations(base_dir: str) -> List[Tuple[Set[str], Set[str], str]]:
    from domain.persistence import PredictionPersistenceService
    from domain.postprocess import PredictionPostprocessService

    archive = _load_archive(base_dir)
    items_by_identity: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], str] = {}
    for archived in archive.values():
        if not isinstance(archived, dict):
            continue
        payload = _extract_prediction_payload(archived)
        retrieved_memory = payload.get("retrieved_memory")
        if not isinstance(retrieved_memory, dict):
            retrieved_memory = archived.get("retrieved_memory") if isinstance(archived.get("retrieved_memory"), dict) else {}
        explanation = _sanitize_explanation(
            PredictionPostprocessService.build_retrieved_memory_explanation(retrieved_memory)
            or payload.get("retrieved_memory_explanation")
            or archived.get("retrieved_memory_explanation")
            or ""
        )
        if not explanation:
            continue
        entry_keys, memory_ids = PredictionPersistenceService._memory_identity_aliases(payload)
        identity = (tuple(sorted(str(v) for v in entry_keys if v)), tuple(sorted(str(v) for v in memory_ids if v)))
        items_by_identity[identity] = explanation
    return [
        (set(entry_keys), set(memory_ids), explanation)
        for (entry_keys, memory_ids), explanation in items_by_identity.items()
    ]


def _find_prediction_block(content: str) -> re.Match[str] | None:
    start_marker = "<!-- prediction-memory:start -->"
    end_marker = "<!-- prediction-memory:end -->"
    return re.search(
        rf"{re.escape(start_marker)}\n(?P<body>.*?){re.escape(end_marker)}",
        content,
        re.DOTALL,
    )


def _inject_explanation(line: str, explanation: str) -> str:
    lines = [
        _unescape_memory_line(raw).rstrip()
        for raw in str(line or '').splitlines()
        if raw.strip()
    ]
    cleaned = [item for item in lines if not item.strip().startswith('RAG记忆:')]
    insert_at = len(cleaned)
    for idx, item in enumerate(cleaned):
        if '记忆ID:' in item or '更新时间:' in item or item.strip().startswith('赛果:'):
            insert_at = idx
            break
    cleaned.insert(insert_at, f'  RAG记忆: {explanation}')
    return '\n'.join(cleaned)


def sync_memory_rag_explanations(base_dir: str, memory_path: Path, dry_run: bool = False) -> Dict[str, Any]:
    from domain.persistence import PredictionPersistenceService

    if not memory_path.exists():
        raise FileNotFoundError(f"MEMORY.md 不存在: {memory_path}")

    content = memory_path.read_text(encoding="utf-8")
    marker = _find_prediction_block(content)
    if not marker:
        raise ValueError("未找到 prediction-memory 区块")

    entry_lines = PredictionPersistenceService._extract_memory_entry_lines(marker.group("body"))
    targets = _iter_archive_explanations(base_dir)
    updated_count = 0
    matched_count = 0
    new_lines: List[str] = []

    for line in entry_lines:
        updated_line = line
        normalized_line = _unescape_memory_line(line)
        normalized_first_line = normalized_line.splitlines()[0].strip() if normalized_line.splitlines() else normalized_line.strip()
        for entry_keys, memory_ids, explanation in targets:
            if any(f"记忆ID: {memory_id}" in normalized_line for memory_id in memory_ids) or any(
                normalized_first_line.startswith(f"- [{entry_key}]") for entry_key in entry_keys
            ):
                matched_count += 1
                candidate = _inject_explanation(updated_line, explanation)
                if candidate != updated_line:
                    updated_count += 1
                updated_line = candidate
                break
        new_lines.append(updated_line)

    replacement = PredictionPersistenceService.render_prediction_memory_block(
        new_lines,
        "<!-- prediction-memory:start -->",
        "<!-- prediction-memory:end -->",
    )
    new_content = re.sub(
        rf"{re.escape('<!-- prediction-memory:start -->')}\n.*?{re.escape('<!-- prediction-memory:end -->')}",
        replacement,
        content,
        count=1,
        flags=re.DOTALL,
    )
    if updated_count > 0:
        footer_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        footer_matches = list(re.finditer(r"\*预测记录更新时间: .*?\*", new_content))
        if footer_matches:
            last_match = footer_matches[-1]
            new_content = new_content[: last_match.start()] + f"*预测记录更新时间: {footer_ts}*" + new_content[last_match.end() :]

    if not dry_run and new_content != content:
        memory_path.write_text(new_content, encoding="utf-8")

    return {
        "targets_with_explanation": len(targets),
        "matched_entries": matched_count,
        "updated_entries": updated_count,
        "changed": new_content != content,
        "memory_file": str(memory_path),
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="将 prediction_archive.json 中的 RAG 记忆解释回填到 MEMORY.md")
    parser.add_argument("--base-dir", default=str(_europe_root()), help="europe_leagues 项目根目录")
    parser.add_argument("--memory-file", default=str(_project_root() / "MEMORY.md"), help="MEMORY.md 路径")
    parser.add_argument("--dry-run", action="store_true", help="仅检查可更新条目，不写文件")
    args = parser.parse_args()

    payload = sync_memory_rag_explanations(
        base_dir=str(Path(args.base_dir).resolve()),
        memory_path=Path(args.memory_file).resolve(),
        dry_run=bool(args.dry_run),
    )
    print(payload)


if __name__ == "__main__":
    main()
