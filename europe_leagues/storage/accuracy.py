"""模块说明：读写 accuracy_stats.json 准确率统计文件。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from runtime.paths import get_default_paths
from ._jsonio import atomic_write_json, safe_read_json


class AccuracyStatsStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)
        self.path = self.paths.runtime_file('accuracy_stats.json')

    def load(self) -> Dict[str, Any]:
        payload = safe_read_json(self.path, {})
        return payload if isinstance(payload, dict) else {}

    def save(self, stats: Dict[str, Any]) -> None:
        atomic_write_json(self.path, stats)
