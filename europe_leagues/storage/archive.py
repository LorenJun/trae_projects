"""模块说明：读写 prediction_archive.json 归档文件。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from runtime.paths import get_default_paths
from ._jsonio import atomic_write_json, safe_read_json


class PredictionArchiveStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)
        self.path = self.paths.runtime_file('prediction_archive.json')

    def load(self) -> Dict[str, Dict[str, Any]]:
        payload = safe_read_json(self.path, {})
        return payload if isinstance(payload, dict) else {}

    def save(self, archive: Dict[str, Dict[str, Any]]) -> None:
        atomic_write_json(self.path, archive)
