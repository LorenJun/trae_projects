"""模块说明：初始化采集层包并导出常用采集与快照仓库接口。"""

from .aliasing import load_team_alias_map, normalize_team_name
from .odds_snapshots import OddsSnapshotRepository
from .okooo import OkoooSnapshotClient, extract_current_odds, find_snapshot_by_match_id, refresh_snapshot
from .sofascore import SofascoreContextCollector, build_match_team_context
from .sporttery import DataCollector, MatchData

__all__ = [
    "DataCollector",
    "MatchData",
    "OddsSnapshotRepository",
    "OkoooSnapshotClient",
    "SofascoreContextCollector",
    "build_match_team_context",
    "extract_current_odds",
    "find_snapshot_by_match_id",
    "load_team_alias_map",
    "normalize_team_name",
    "refresh_snapshot",
]
