"""模块说明：初始化领域层包，并通过惰性导出方式避免循环导入。"""

from importlib import import_module

__all__ = [
    "MatchIntelligenceEngine",
    "InferencePipelineService",
    "LiveRefreshService",
    "PredictionPersistenceService",
    "PredictionPostprocessService",
    "PredictionReportService",
    "TeamStrengthService",
    "UpsetAnalyzer",
    "DomainPredictor",
    "LEAGUE_CONFIG",
    "TeamsWritebackGateway",
    "build_odds_runtime_options",
    "ensure_analysis_context",
    "load_analysis_context_file",
    "HybridRAGService",
    "LightweightRAGService",
]


def __getattr__(name: str):
    if name == "MatchIntelligenceEngine":
        return import_module(".intelligence", __name__).MatchIntelligenceEngine
    if name == "InferencePipelineService":
        return import_module(".inference", __name__).InferencePipelineService
    if name == "LiveRefreshService":
        return import_module(".live", __name__).LiveRefreshService
    if name == "TeamStrengthService":
        return import_module(".team_strength", __name__).TeamStrengthService
    if name == "PredictionPersistenceService":
        return import_module(".persistence", __name__).PredictionPersistenceService
    if name == "PredictionPostprocessService":
        return import_module(".postprocess", __name__).PredictionPostprocessService
    if name == "PredictionReportService":
        return import_module(".reporting", __name__).PredictionReportService
    if name == "UpsetAnalyzer":
        return import_module(".upset", __name__).UpsetAnalyzer
    if name in {"DomainPredictor", "LEAGUE_CONFIG"}:
        module = import_module(".predictor", __name__)
        return getattr(module, name)
    if name == "TeamsWritebackGateway":
        return import_module(".writeback", __name__).TeamsWritebackGateway
    if name == "build_odds_runtime_options":
        return import_module(".odds", __name__).build_odds_runtime_options
    if name in {"ensure_analysis_context", "load_analysis_context_file"}:
        module = import_module(".features", __name__)
        return getattr(module, name)
    if name == "HybridRAGService":
        return import_module(".rag", __name__).HybridRAGService
    if name == "LightweightRAGService":
        return import_module(".rag", __name__).LightweightRAGService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
