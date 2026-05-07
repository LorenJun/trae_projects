"""模块说明：读写 prediction_archive.json 归档文件。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from runtime.paths import get_default_paths


class PredictionArchiveStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)
        self.path = self.paths.runtime_file('prediction_archive.json')

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, archive: Dict[str, Dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(archive, ensure_ascii=False, indent=2), encoding='utf-8')
