"""模块说明：统一管理运行时目录、快照、归档与 MEMORY 文件路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, Path]


@dataclass(frozen=True)
class EuropeLeaguesPaths:
    base_dir: Path
    project_root: Path

    @classmethod
    def from_base_dir(cls, base_dir: Optional[PathLike] = None) -> "EuropeLeaguesPaths":
        resolved_base = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parents[1]
        return cls(base_dir=resolved_base, project_root=resolved_base.parent)

    @property
    def runtime_dir(self) -> Path:
        return self.base_dir / '.okooo-scraper' / 'runtime'

    @property
    def snapshots_dir(self) -> Path:
        return self.base_dir / '.okooo-scraper' / 'snapshots'

    @property
    def schedules_dir(self) -> Path:
        return self.base_dir / '.okooo-scraper' / 'schedules'

    @property
    def alias_map_path(self) -> Path:
        return self.base_dir / 'okooo_team_aliases.json'

    @property
    def memory_file(self) -> Path:
        return self.project_root / 'MEMORY.md'

    def ensure_runtime_dir(self) -> Path:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        return self.runtime_dir

    def teams_file(self, league_code: str) -> Path:
        return self.base_dir / league_code / 'teams_2025-26.md'

    def runtime_file(self, filename: str) -> Path:
        self.ensure_runtime_dir()
        return self.runtime_dir / filename

    def snapshot_dir(self, league_code: Optional[str] = None) -> Path:
        base = self.snapshots_dir
        if league_code:
            return base / league_code
        return base


def get_default_paths(base_dir: Optional[PathLike] = None) -> EuropeLeaguesPaths:
    return EuropeLeaguesPaths.from_base_dir(base_dir)
