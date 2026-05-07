"""模块说明：封装 Sofascore 数据抓取与球队上下文读取逻辑。"""

from __future__ import annotations

from typing import Any, Dict

from sofascore_team_context import build_match_team_context


class SofascoreContextCollector:
    def build_match_team_context(self, *args, **kwargs) -> Dict[str, Any]:
        return build_match_team_context(*args, **kwargs)


__all__ = ["SofascoreContextCollector", "build_match_team_context"]
