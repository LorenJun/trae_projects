"""模块说明：提供预测过程使用的轻量缓存能力。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PredictionCache:
    """轻量 JSON 文件缓存，默认关闭。"""

    def __init__(self, cache_dir: str = '.prediction_cache', enabled: Optional[bool] = None):
        if enabled is None:
            enabled = os.getenv('ENABLE_PREDICTION_CACHE', '0') == '1'
        self.enabled = bool(enabled) and bool(cache_dir)
        self.cache_dir = cache_dir
        if self.enabled:
            os.makedirs(cache_dir, exist_ok=True)

    def _get_cache_key(self, func_name: str, params: Dict[str, Any]) -> str:
        params_str = json.dumps(params, sort_keys=True, default=str)
        key_str = f"{func_name}_{params_str}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, func_name: str, params: Dict[str, Any], ttl_hours: int = 24) -> Optional[Any]:
        if not self.enabled:
            return None
        cache_key = self._get_cache_key(func_name, params)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        if not os.path.exists(cache_file):
            return None
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - cache_time > timedelta(hours=ttl_hours):
                os.remove(cache_file)
                return None
            return cache_data['data']
        except Exception as exc:
            logger.warning("读取缓存失败: %s", exc)
            return None

    def set(self, func_name: str, params: Dict[str, Any], data: Any):
        if not self.enabled:
            return
        cache_key = self._get_cache_key(func_name, params)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'data': data,
            }
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, default=str)
        except Exception as exc:
            logger.warning("写入缓存失败: %s", exc)
