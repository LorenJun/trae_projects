"""模块说明：Store 层共享的 JSON 读写工具。

提供：
- atomic_write_json：写临时文件 + os.replace 原子替换，避免写到一半进程被杀
  导致 JSON 截断损坏。
- safe_read_json：解析失败时不静默丢数据，而是把损坏文件备份为 .corrupt
  并记录 error 日志，再返回默认值，便于事后排查与恢复。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def safe_read_json(path: Path, default: Any) -> Any:
    """读取 JSON；文件不存在返回 default；损坏时备份并告警后返回 default。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        # 不静默吞掉：备份损坏文件并记录，避免后续 save 用空数据覆盖造成永久丢失
        try:
            backup = path.with_suffix(path.suffix + '.corrupt')
            os.replace(str(path), str(backup))
            logger.error('JSON 文件损坏，已备份至 %s: %s', backup, exc)
        except Exception as backup_exc:
            logger.error('JSON 文件损坏且备份失败 %s: %s (备份错误: %s)', path, exc, backup_exc)
        return default


def atomic_write_json(path: Path, payload: Any) -> None:
    """原子写 JSON：先写同目录临时文件，再 os.replace 替换目标。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f'.tmp.{os.getpid()}')
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding='utf-8')
    os.replace(str(tmp), str(path))
