from __future__ import annotations

from pydantic import BaseModel # 只要是结构化就用BaseModel可以方便验证。

from typing import Literal,Any
class TraceRecord(BaseModel): # 追踪系统的基本载体，可以用来知道数据流的流向。
    # 记录发送时间，收发方向，layer层级？，信息类型，客户端id，发送步骤，发送信息本身，
    ts:str
    direction:Literal["CLIENT->CORE", "CORE->CLIENT","CORE","CORE->LLM","LLM->CORE"]
    layer:Literal["ipc", "event", "llm"]
    kind: str  # command / response / error / push / event / api_call / api_response
    run_id: str | None = None
    step: int | None = None
    client_id: str | None = None
    data: dict[str, Any]
