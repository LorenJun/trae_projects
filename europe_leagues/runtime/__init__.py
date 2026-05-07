"""模块说明：初始化运行时包并暴露缓存、路径与同步辅助组件。"""

from .cache import PredictionCache
from .paths import EuropeLeaguesPaths, get_default_paths

__all__ = ["PredictionCache", "EuropeLeaguesPaths", "get_default_paths"]
