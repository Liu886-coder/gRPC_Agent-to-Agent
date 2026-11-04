"""本项目支持的 A2A 风格 JSON-RPC 子集，不是完整 A2A SDK。"""
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# 定义 Agent 消息和请求格式
# 例如：
# class Message(BaseModel):
#     messageId: str       # 消息编号
#     role: ...            # 谁发的消息
#     contextId: str       # 属于哪个会话
#     taskId: ...          # 对应哪个任务
#     parts: list[TextPart]  # 消息内容
#     metadata: ...        # 额外信息


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class TextPart(BaseModel):
    kind: Literal["text"] = "text"
    text: str = Field(max_length=32000)


class Message(BaseModel):
    kind: Literal["message"] = "message"
    messageId: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["user", "agent", "system"] = "user"
    contextId: str = Field(default_factory=lambda: new_id("ctx"), min_length=1, max_length=200)
    taskId: str | None = None
    parts: list[TextPart] = Field(min_length=1, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self):
        return "\n".join(part.text for part in self.parts)


class SendParams(BaseModel):
    message: Message
    historyLength: int = Field(default=10, ge=0, le=100)
    timeoutSeconds: float = Field(default=30, gt=0, le=300)
    # 本机进程间传播统一截止时间；这是本项目的扩展字段。
    deadline: float | None = None


class RpcEnvelope(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class ProtocolError(Exception):
    def __init__(self, message: str, code: int = -32603):
        super().__init__(message)
        self.code = code


def success(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id, message, code=-32603):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}

