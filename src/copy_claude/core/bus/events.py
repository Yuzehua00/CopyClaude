from __future__ import annotations
from pydantic import BaseModel,Discriminator
from typing import Annotated,Literal,Any

class RunStartedEvent(BaseModel):
    type: Literal["core.started"] = "run.started"
    run_id: str
    goal:str
    ts:str

class RunFinishedEvent(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    status: str  # "success" | "failed"
    reason: str | None = None  # "exceeded_max_steps" | "cancelled" | "llm_error" | ...
    steps: int
    ts: str

class LlmTokenEvent(BaseModel):
    type: Literal["llm.token"] = "llm.token"
    run_id: str
    token: str   # LLM 流式输出的单个文本片段
    ts: str

class ToolCallStartedEvent(BaseModel):
    type: Literal["tool.call.started"] = "tool.call.started"
    run_id: str
    tool_use_id:str
    tool_name:str
    params: dict[str, Any]
    ts:str

class ToolCallFinishedEvent(BaseModel):
    type: Literal["tool.call_finished"] = "tool.call_finished"
    run_id: str
    tool_use_id: str
    tool_name: str
    elapsed_ms: int
    ts: str

class ToolCallFailedEvent(BaseModel):
    type: Literal["tool.call_failed"] = "tool.call_failed"
    run_id: str
    tool_use_id: str
    tool_name: str
    # "runtime_error" | "timeout" | "schema_error" | "permission_denied" | "rate_limited"
    error_class: str
    error_message: str
    elapsed_ms: int
    attempt: int = 1  # 1=first attempt, 2=first retry, 3=second retry 重试次数
    ts:str

class StepStartedEvent(BaseModel):
    type: Literal["step.started"] = "step.started"
    run_id: str
    step:int
    ts: str

class StepFinishedEvent(BaseModel):
    type: Literal["step.finished"] = "step.finished"
    run_id: str
    step: int
    ts: str

class LlmModelSelectedEvent(BaseModel):
    type: Literal["llm.model_selected"] = "llm.model_selected"
    run_id: str
    model: str
    strategy: str  # "static" | "rule_based" | "cost_budget"
    ts: str

Event = Annotated[
    RunStartedEvent|
    RunFinishedEvent|
    LlmTokenEvent|
    ToolCallStartedEvent|
    ToolCallFinishedEvent|
    ToolCallFailedEvent|
    StepStartedEvent|
    StepFinishedEvent|
    LlmModelSelectedEvent,
    Discriminator("type")
]