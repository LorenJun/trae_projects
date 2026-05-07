from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from .core import HarnessContext, HarnessPipeline, PipelineStage


PIPELINE_REGISTRY = {
    "match_prediction": {
        "intent": "专业纬度足彩数据精算师视角下的赛前单场预测",
        "description": "按 Harness 阶段执行 collect -> predict，要求区分模型、盘口与综合结论，并返回可审计结果。",
    },
    "result_recording": {
        "intent": "专业纬度足彩数据精算师视角下的赛后赛果回填",
        "description": "按 Harness 阶段执行 save-result -> accuracy，服务于统计验证、复盘优化与可审计结果沉淀。",
    },
}


def _pick_selected_match(context: HarnessContext) -> Dict[str, Any]:
    collect_data = context.get("collect_data", {})
    matches = collect_data.get("matches", []) if isinstance(collect_data, dict) else []
    home_team = str(context.get("home_team", "")).strip()
    away_team = str(context.get("away_team", "")).strip()
    match_time = str(context.get("match_time", "")).strip()
    for match in matches:
        if match.get("home_team") != home_team or match.get("away_team") != away_team:
            continue
        if match_time and match.get("match_time") != match_time:
            continue
        return match
    return {}


def _stage_collect_data(context: HarnessContext) -> Dict[str, Any]:
    from data_collector import DataCollector

    collector = DataCollector()
    league = context.get("league")
    date = context.get("date")
    no_cache = bool(context.get("no_cache", False))
    matches = asyncio.run(collector.collect_league_data(league, date, use_cache=not no_cache))
    payload = {
        "league": league,
        "date": date,
        "count": len(matches),
        "matches": [
            {
                "match_id": getattr(match, "match_id", "") or "",
                "home_team": match.home_team,
                "away_team": match.away_team,
                "match_time": match.match_time,
                "status": match.status,
                "sources": match.sources or [],
            }
            for match in matches
        ],
    }
    return payload


def _stage_predict_match(context: HarnessContext) -> Dict[str, Any]:
    from enhanced_prediction_workflow import EnhancedPredictor

    predictor = EnhancedPredictor()
    selected_match = _pick_selected_match(context)
    return predictor.predict_match(
        home_team=context.get("home_team"),
        away_team=context.get("away_team"),
        league_code=context.get("league"),
        match_date=context.get("date"),
        match_id=context.get("match_id", "") or selected_match.get("match_id", ""),
        force_refresh_odds=not bool(context.get("no_refresh_odds", False)),
        okooo_driver=context.get("okooo_driver", "browser-use"),
        okooo_headed=bool(context.get("okooo_headed", False)),
        match_time=context.get("match_time", "") or selected_match.get("match_time", ""),
        league_hint=context.get("league_hint", None),
        analysis_context=context.get("analysis_context", None),
    )


def _stage_save_result(context: HarnessContext) -> Dict[str, Any]:
    from result_manager import ResultManager

    manager = ResultManager()
    return manager.save_result(
        context.get("match_id"),
        int(context.get("home_score")),
        int(context.get("away_score")),
    )


def _stage_accuracy(context: HarnessContext) -> Dict[str, Any]:
    from result_manager import ResultManager

    manager = ResultManager()
    if bool(context.get("refresh", False)):
        return manager.update_accuracy_stats()
    with open(manager.accuracy_file, "r", encoding="utf-8") as f:
        import json

        return json.load(f)


def list_pipelines() -> List[Dict[str, Any]]:
    return [
        {"name": name, "intent": meta["intent"], "description": meta["description"]}
        for name, meta in PIPELINE_REGISTRY.items()
    ]


def build_pipeline(name: str) -> HarnessPipeline:
    if name == "match_prediction":
        return HarnessPipeline(
            name=name,
            intent=PIPELINE_REGISTRY[name]["intent"],
            description=PIPELINE_REGISTRY[name]["description"],
            runtime_agent_roles=["data_collector", "match_analyzer", "odds_analyzer"],
            stages=[
                PipelineStage(
                    name="collect_data",
                    description="以精算师身份收集赛程、match_id、赔率与上下文输入",
                    handler=_stage_collect_data,
                    required_inputs=["league", "date"],
                    artifact_key="collect_data",
                ),
                PipelineStage(
                    name="predict_match",
                    description="执行增强版单场预测，并服务于模型、盘口、综合结论的统一表达",
                    handler=_stage_predict_match,
                    required_inputs=["league", "date", "home_team", "away_team"],
                    artifact_key="predict_match",
                ),
            ],
        )
    if name == "result_recording":
        return HarnessPipeline(
            name=name,
            intent=PIPELINE_REGISTRY[name]["intent"],
            description=PIPELINE_REGISTRY[name]["description"],
            runtime_agent_roles=["result_tracker"],
            stages=[
                PipelineStage(
                    name="save_result",
                    description="写回比赛实际比分，服务于精算师的赛后验证闭环",
                    handler=_stage_save_result,
                    required_inputs=["match_id", "home_score", "away_score"],
                    artifact_key="save_result",
                ),
                PipelineStage(
                    name="refresh_accuracy",
                    description="更新准确率统计，支撑精算师的统计验证与策略复盘",
                    handler=_stage_accuracy,
                    required_inputs=[],
                    artifact_key="accuracy",
                ),
            ],
        )

    available = ", ".join(sorted(PIPELINE_REGISTRY.keys()))
    raise ValueError(f"未知 pipeline: {name}，可选: {available}")
