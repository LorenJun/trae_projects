#!/usr/bin/env python3
"""脚本说明：清理 MEMORY.md 中的重复预测条目。

使用方法:
    python scripts/clean_memory_duplicates.py [--base-dir BASE_DIR] [--dry-run]

选项:
    --base-dir BASE_DIR  项目根目录 (默认: /Users/bytedance/trae_projects/europe_leagues)
    --dry-run            只显示将要删除的条目，不实际修改文件
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from runtime.memory_dedupe import (
    clean_memory_duplicates,
    find_duplicate_entries,
    normalize_memory_entry_key,
)
from domain.persistence import PredictionPersistenceService


def main():
    parser = argparse.ArgumentParser(description='清理 MEMORY.md 中的重复预测条目')
    parser.add_argument(
        '--base-dir',
        type=str,
        default=str(project_root.parent),  # 使用父目录作为默认（MEMORY.md在trae_projects根目录）
        help='项目根目录',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='只显示将要删除的条目，不实际修改文件',
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    memory_path = base_dir / 'MEMORY.md'

    if not memory_path.exists():
        print(f'错误: 未找到 MEMORY.md 文件: {memory_path}')
        sys.exit(1)

    # 读取内容
    content = memory_path.read_text(encoding='utf-8')

    # 提取预测记忆区块
    start_marker = '<!-- prediction-memory:start -->'
    end_marker = '<!-- prediction-memory:end -->'
    pattern = rf'{re.escape(start_marker)}\n(?P<body>.*?){re.escape(end_marker)}'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        print('未找到预测记忆区块')
        sys.exit(0)

    # 提取所有条目
    memory_content = match.group('body')
    entries = PredictionPersistenceService._extract_memory_entry_lines(memory_content)

    # 收集所有条目的key
    entry_keys = []
    for entry in entries:
        first_line = entry.split('\n')[0] if entry else ''
        key_match = re.match(r'- \[([^\]]+)\]', first_line)
        if key_match:
            entry_keys.append(key_match.group(1))
        else:
            entry_keys.append('')

    # 查找重复
    duplicates = find_duplicate_entries([k for k in entry_keys if k])

    if not duplicates:
        print('未发现重复条目')
        sys.exit(0)

    print(f'发现 {len(duplicates)} 组重复条目:\n')

    for canonical_key, dup_keys in duplicates.items():
        norm = normalize_memory_entry_key(canonical_key)
        print(f'比赛: {norm[2]} vs {norm[3]} ({norm[0]})')
        print(f'  保留: {canonical_key}')
        for dup in dup_keys:
            print(f'  删除: {dup}')
        print()

    if args.dry_run:
        print('(试运行模式，未实际修改文件)')
        sys.exit(0)

    # 执行清理
    new_content, deleted_count = clean_memory_duplicates(content)

    if deleted_count > 0:
        memory_path.write_text(new_content, encoding='utf-8')
        print(f'已清理 {deleted_count} 个重复条目')
    else:
        print('未删除任何条目')


if __name__ == '__main__':
    main()
