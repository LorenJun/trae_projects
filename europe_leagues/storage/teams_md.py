"""模块说明：读写各联赛 teams_2025-26.md 单一事实来源文件。"""

from __future__ import annotations

from typing import List, Optional

from runtime.paths import EuropeLeaguesPaths, get_default_paths


class TeamsMarkdownStore:
    def __init__(self, base_dir: Optional[str] = None):
        self.paths = get_default_paths(base_dir)

    def path_for_league(self, league_code: str) -> str:
        return str(self.paths.teams_file(league_code))

    def read_text(self, league_code: str, encoding: str = 'utf-8') -> str:
        return self.paths.teams_file(league_code).read_text(encoding=encoding)

    def read_lines(self, league_code: str, encoding: str = 'utf-8') -> List[str]:
        return self.read_text(league_code, encoding=encoding).splitlines(True)

    def write_text(self, league_code: str, content: str, encoding: str = 'utf-8') -> None:
        self.paths.teams_file(league_code).write_text(content, encoding=encoding)
