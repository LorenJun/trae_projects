"""模块说明：初始化运行时包并暴露缓存、路径与同步辅助组件。"""

from .cache import PredictionCache
from .paths import EuropeLeaguesPaths, get_default_paths

# rag_store 依赖 storage，而 storage 又会回头引用 runtime，eager import 会形成循环导入。
# 这里改为惰性导入：仅在首次访问 rag_store 的符号时才加载该模块，打破导入环。
_RAG_STORE_EXPORTS = {
    "build_hybrid_rag_index",
    "build_rag_cases",
    "load_rag_cases",
    "load_rag_index",
    "retrieve_hybrid_context",
    "retrieve_structured_cases",
    "sync_rag_index",
}

__all__ = [
    "PredictionCache",
    "EuropeLeaguesPaths",
    "get_default_paths",
    *sorted(_RAG_STORE_EXPORTS),
]


def __getattr__(name):
    if name in _RAG_STORE_EXPORTS:
        from . import rag_store

        return getattr(rag_store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
