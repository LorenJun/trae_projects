"""模块说明：读写 accuracy_stats.json 准确率统计文件。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from runtime.paths import get_default_paths


class AccuracyStatsStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)
        self.path = self.paths.runtime_file('accuracy_stats.json')

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, stats: Dict[str, Any]) -> None:
        self.path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
