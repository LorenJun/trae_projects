from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import traceback
from typing import Any, Callable, Dict, List, Optional
import uuid


def _utcnow() -> str:
    return datetime.now().isoformat()


@dataclass
class StageRecord:
    stage: str
    description: str
    status: str
    started_at: str
    finished_at: str
    output_keys: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "description": self.description,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "output_keys": list(self.output_keys),
            "error": self.error,
        }


@dataclass
class HarnessContext:
    pipeline: str
    intent: str
    inputs: Dict[str, Any]
    runtime_profile: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=_utcnow)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    stage_records: List[StageRecord] = field(default_factory=list)

    def require(self, *keys: str) -> None:
        missing = [key for key in keys if self.get(key) in (None, "")]
        if missing:
            raise ValueError(f"缺少必需输入: {', '.join(missing)}")

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.artifacts:
            return self.artifacts.get(key, default)
        return self.inputs.get(key, default)

    def set_artifact(self, key: str, value: Any) -> None:
        self.artifacts[key] = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "pipeline": self.pipeline,
            "intent": self.intent,
            "started_at": self.started_at,
            "runtime_profile": self.runtime_profile,
            "inputs": self.inputs,
            "artifacts": self.artifacts,
            "stage_records": [record.to_dict() for record in self.stage_records],
        }


StageHandler = Callable[[HarnessContext], Dict[str, Any]]


@dataclass
class PipelineStage:
    name: str
    description: str
    handler: StageHandler
    required_inputs: List[str] = field(default_factory=list)
    artifact_key: Optional[str] = None

    def execute(self, context: HarnessContext) -> Dict[str, Any]:
        started_at = _utcnow()
        try:
            if self.required_inputs:
                context.require(*self.required_inputs)
            output = self.handler(context) or {}
            if not isinstance(output, dict):
                raise TypeError(f"阶段 {self.name} 必须返回 dict，实际为 {type(output).__name__}")
            if self.artifact_key:
                context.set_artifact(self.artifact_key, output)
            context.stage_records.append(
                StageRecord(
                    stage=self.name,
                    description=self.description,
                    status="success",
                    started_at=started_at,
                    finished_at=_utcnow(),
                    output_keys=sorted(output.keys()),
                )
            )
            return output
        except Exception as exc:
            context.stage_records.append(
                StageRecord(
                    stage=self.name,
                    description=self.description,
                    status="failed",
                    started_at=started_at,
                    finished_at=_utcnow(),
                    error="".join(traceback.format_exception_only(type(exc), exc)).strip(),
                )
            )
            raise


@dataclass
class HarnessPipeline:
    name: str
    intent: str
    description: str
    stages: List[PipelineStage]
    runtime_agent_roles: List[str] = field(default_factory=list)

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        runtime_profile: Dict[str, Any] = {}
        try:
            from agent_runtime_registry import get_runtime_profile

            runtime_profile = get_runtime_profile(self.runtime_agent_roles)
        except Exception:
            runtime_profile = {}

        context = HarnessContext(
            pipeline=self.name,
            intent=self.intent,
            inputs=inputs,
            runtime_profile=runtime_profile,
        )
        status = "success"
        error = ""
        try:
            for stage in self.stages:
                stage.execute(context)
        except Exception as exc:
            status = "failed"
            error = "".join(traceback.format_exception_only(type(exc), exc)).strip()

        return {
            "success": status == "success",
            "status": status,
            "pipeline": self.name,
            "intent": self.intent,
            "description": self.description,
            "request_id": context.request_id,
            "started_at": context.started_at,
            "finished_at": _utcnow(),
            "runtime_profile": context.runtime_profile,
            "inputs": context.inputs,
            "artifacts": context.artifacts,
            "stages": [record.to_dict() for record in context.stage_records],
            "error": error,
        }
