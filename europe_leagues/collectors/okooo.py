"""模块说明：封装澳客相关的快照提取、driver 选择与实时抓取辅助逻辑。"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Dict, List, Optional

from okooo_live_snapshot import extract_current_odds, find_snapshot_by_match_id, find_snapshot_for_match, refresh_snapshot, snapshot_matches_request
from runtime.paths import get_default_paths


DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def get_okooo_driver_status(chrome_path: str = DEFAULT_CHROME_PATH) -> Dict[str, Dict[str, object]]:
    browser_use_module = importlib.util.find_spec("browser_use") is not None
    browser_use_cli = shutil.which("browser-use") is not None
    local_chrome_binary = os.path.exists(chrome_path)
    local_chrome_cdp = local_chrome_binary
    browser_use_available = browser_use_module and browser_use_cli
    local_chrome_available = local_chrome_cdp
    return {
        "browser-use": {
            "available": browser_use_available,
            "module_available": browser_use_module,
            "cli_available": browser_use_cli,
            "reason": (
                ""
                if browser_use_available
                else "browser_use Python 模块缺失" if not browser_use_module
                else "browser-use CLI 不在 PATH 中"
            ),
        },
        "local-chrome": {
            "available": local_chrome_available,
            "chrome_binary": chrome_path,
            "chrome_binary_exists": local_chrome_binary,
            "reason": "" if local_chrome_available else f"Chrome 不存在: {chrome_path}",
        },
    }


def build_okooo_driver_chain(preferred: str, chrome_path: str = DEFAULT_CHROME_PATH) -> List[str]:
    status = get_okooo_driver_status(chrome_path=chrome_path)
    if preferred not in {"local-chrome", "browser-use"}:
        preferred = "local-chrome"
    order = [preferred]
    return [driver for driver in order if status.get(driver, {}).get("available")]


def describe_unavailable_okooo_drivers(preferred: str, chrome_path: str = DEFAULT_CHROME_PATH) -> List[Dict[str, str]]:
    status = get_okooo_driver_status(chrome_path=chrome_path)
    warnings: List[Dict[str, str]] = []
    requested = status.get(preferred, {})
    if requested and not requested.get("available"):
        warnings.append(
            {
                "driver": preferred,
                "warning": f"请求的 {preferred} 不可用，原因: {requested.get('reason')}",
            }
        )
    if not build_okooo_driver_chain(preferred, chrome_path=chrome_path):
        for driver, info in status.items():
            if not info.get("available"):
                warnings.append(
                    {
                        "driver": driver,
                        "warning": f"{driver} 不可用，原因: {info.get('reason')}",
                    }
                )
    return warnings


class OkoooSnapshotClient:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)

    def refresh_snapshot(self, *args, **kwargs):
        return refresh_snapshot(*args, **kwargs)

    def extract_current_odds(self, snapshot):
        return extract_current_odds(snapshot)

    def find_snapshot_by_match_id(self, *args, **kwargs):
        return find_snapshot_by_match_id(*args, **kwargs)

    def find_snapshot_for_match(self, *args, **kwargs):
        return find_snapshot_for_match(*args, **kwargs)

    def snapshot_root(self, league_code: str = '') -> str:
        return str(self.paths.snapshot_dir(league_code))


__all__ = [
    "OkoooSnapshotClient",
    "DEFAULT_CHROME_PATH",
    "build_okooo_driver_chain",
    "describe_unavailable_okooo_drivers",
    "extract_current_odds",
    "find_snapshot_by_match_id",
    "find_snapshot_for_match",
    "get_okooo_driver_status",
    "refresh_snapshot",
    "snapshot_matches_request",
]
