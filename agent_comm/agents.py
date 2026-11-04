
"""业务主线：协调器路由到数学/天气 Agent，共用 A2A、任务、历史和心跳框架。"""
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .a2a_client import A2AClient
from .llm import ModelClient
from .mcp_client import MCPClient, content_text
from .protocol import Message, SendParams, TextPart, RpcEnvelope, ProtocolError, failure, success
from .rag import Retriever
from .registry import Registration
from .storage import make_store
from .tools.calculator import extract_expression
from .tools import WEATHER_TOOL_NAMES
from .weather_agent import WeatherAgent

log = logging.getLogger(__name__)

# =========== 核心流程 ============ #
# create_agent_app(kind, settings)
#     │
#     ├─ 创建 AgentEngine 实例
#     ├─ 启动资源和注册心跳
#     └─ 提供 HTTP 请求入口 endpoint()
#                   │
#                   ▼
#               process()
#                   │
#           根据 kind 分发
#           ├─ _orchestrate()：协调器
#           ├─ _math()：数学业务
#           └─ WeatherAgent.answer()：天气与穿衣建议
# ================================ #

class AgentEngine:
    def __init__(self, kind, settings, store):
        self.kind, self.settings, self.store = kind, settings, store
        if kind not in {"orchestrator", "math", "weather"}:
            raise ValueError(f"未知 Agent 类型：{kind}")
        self.agent_id = f"{kind}-1"
        self.address = {"orchestrator": settings.orchestrator_url, "math": settings.math_url,
                        "weather": settings.weather_url}[kind]
        self.llm = ModelClient(settings)
        self.a2a = A2AClient()
        self.http = httpx.AsyncClient(timeout=5, trust_env=False)
        self.mcp = None
        self.retriever = None
        self.tools = []
        self.warning = ""


    # 专业 Agent 启动各自的 MCP 子进程；天气 Agent 只暴露天气工具给模型。

    async def start(self):
        # 与原项目一致：工具由专业 Agent 使用。协调器专注路由。
        if self.kind in {"math", "weather"} and self.settings.enable_mcp:
            timeout = self.settings.request_timeout if self.kind == "weather" else min(self.settings.request_timeout, 10)
            client = MCPClient(timeout=timeout)
            try:
                await client.start()
                self.tools = await client.list_tools()
                if self.kind == "weather":
                    self.tools = [tool for tool in self.tools if tool["name"] in WEATHER_TOOL_NAMES]
                self.mcp = client
            except Exception as exc:
                await client.close()
                self.warning = f"MCP 不可用: {type(exc).__name__}"
                log.warning(self.warning)
            if self.tools and self.settings.enable_rag:
                retriever = Retriever(self.settings)  # RAG检索发生在调用的 Retriever 中
                try:
                    await retriever.index_tools(self.tools)
                    self.retriever = retriever
                except Exception as exc:
                    await retriever.close()
                    self.warning = f"RAG 不可用，使用完整工具列表: {type(exc).__name__}"
                    log.warning(self.warning)

    async def close(self):
        if self.mcp:
            await self.mcp.close()
        if self.retriever:
            await self.retriever.close()
        await self.llm.close()
        await self.a2a.close()
        await self.http.aclose()
        await self.store.close()

    def remaining(self, params):
        deadline = params.deadline or (time.time() + params.timeoutSeconds)
        return max(0.001, min(self.settings.request_timeout, params.timeoutSeconds, deadline - time.time()))

    # 协调器、数学和天气 Agent 共用任务状态、历史读取和错误处理。
    async def process(self, params: SendParams):
        message = params.message
        if message.role != "user" or not message.text.strip():
            raise ProtocolError("请输入非空 user 消息", -32602)
        if params.deadline is None:
            params = params.model_copy(update={"deadline": time.time() + params.timeoutSeconds})
        task = await self.store.create_task(message.contextId, self.agent_id)
        try:
            async with asyncio.timeout(self.remaining(params)):
                # 先读历史，后保存当前输入，当前输入不会重复进入 prompt。
                history = await self.store.history(message.contextId, params.historyLength)
                history = [row for row in history if row["messageId"] != message.messageId]
                if self.kind == "orchestrator":
                    await self.store.add_message(message)
                    text, metadata = await self._orchestrate(params, history)
                elif self.kind == "math":
                    text, metadata = await self._math(params, history)
                else:
                    text, metadata = await WeatherAgent(self).answer(params, history)
                metadata.update({"agent_id": metadata.get("agent_id", self.agent_id),
                                 "provider": self.settings.llm_provider,
                                 "stream_mode": "chunked_after_completion"})
                reply = Message(role="agent", contextId=message.contextId, taskId=task["id"],
                                parts=[TextPart(text=text)], metadata=metadata)
                if self.kind == "orchestrator":
                    await self.store.add_message(reply)
                await self.store.update_task(task["id"], "completed", answer=text, metadata=metadata)
                log.info("request completed agent=%s task=%s context=%s tools=%s",
                         self.agent_id, task["id"], message.contextId, metadata.get("tools_used", []))
                return reply
        except asyncio.CancelledError:
            await self.store.update_task(task["id"], "canceled", error="请求取消")
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            await self.store.update_task(task["id"], "failed", error="请求超时")
            raise ProtocolError(f"请求超时；task_id={task['id']}", -32001) from exc
        except Exception as exc:
            await self.store.update_task(task["id"], "failed", error=str(exc))
            if isinstance(exc, ProtocolError):
                raise ProtocolError(f"{exc}; task_id={task['id']}", exc.code) from exc
            log.exception("agent request failed task=%s", task["id"])
            raise ProtocolError(f"处理失败: {type(exc).__name__}; task_id={task['id']}") from exc

    # 协调器 Agent 的核心业务处理函数。分类、路由、普通问答
    # 协调器Agent确定意图识别后会向注册中心找数学Agent
    async def _orchestrate(self, params, history):
        # 分类读取历史，让“那上海呢”“明天呢”可以延续上次天气查询。
        intent = await self.llm.classify(params.message.text, history)
        log.info("routing intent=%s context=%s", intent, params.message.contextId)
        # 两个专业 Agent 使用相同 A2A 转发流程，只是注册标签不同。
        if intent in {"math", "weather"}:
            # 向注册中心查找相对应的 Agent
            response = await self.http.get(self.settings.registry_url + f"/v1/agent/select/{intent}",
                                           timeout=self.remaining(params))
            if response.status_code == 404:
                raise ProtocolError(f"没有在线的 {intent} Agent", -32003)
            response.raise_for_status()

            # A2A：把请求发送到注册中心选出的专业 Agent 地址。
            result = await self.a2a.request(response.json()["address"], "message/send",
                                           params.model_dump(), timeout=self.remaining(params))
            # ================================================================================ #
            reply = Message.model_validate(result)
            metadata = {**reply.metadata, "intent": intent, "child_task_id": reply.taskId}
            return reply.text, metadata

        # 如果是普通问题/代码，走这个逻辑；由协调器自己调用模型回答
        text = await self.llm.answer(params.message.text, history)
        return text, {"agent_id": self.agent_id, "intent": intent, "tools_used": [],
                      "fallback": "general (code Agent 尚未配置)" if intent == "code" else ""}

    # 数学计算Agent的核心实现位置。检索候选、模型工具决策、MCP 调用
    async def _math(self, params, history):
        text = params.message.text
        tool_text, candidates, used, rag_warning = "", [], [], self.warning
        tool_calls, source = [], "disabled"
        if self.mcp:
            available = self.tools
            source = "all_tools"
            if self.retriever:
                try:
                    candidates = await self.retriever.retrieve(text)
                    available = candidates
                    source = "rag"
                    if not candidates:
                        rag_warning = "RAG 没有满足阈值的候选，当前请求不提供工具"
                except Exception as exc:
                    rag_warning = f"检索失败，使用完整工具列表: {type(exc).__name__}"
            log.info("tool candidates source=%s names=%s context=%s", source,
                     [tool["name"] for tool in available], params.message.contextId)
            if self.settings.llm_provider == "qwen":
                async def execute(name, arguments):
                    log.info("MCP tool call name=%s arguments=%s context=%s",
                             name, arguments, params.message.contextId)
                    result = await self.mcp.call_tool(name, arguments)
                    if result.get("isError", False):
                        raise ProtocolError(f"{name} 执行失败: " + content_text(result), -32004)
                    return result
                answer, tool_calls = await self.llm.answer_with_tools(text, history, available, execute)
                used = list(dict.fromkeys(call["name"] for call in tool_calls))
                return answer, {"agent_id": self.agent_id, "tools_used": used,
                    "tool_calls": tool_calls, "tool_selection": "qwen_function_calling",
                    "tool_source": source,
                    "rag_candidates": [{"name": row["name"], "score": row["score"]} for row in candidates],
                    "warning": rag_warning}
            # 离线 mock 保留确定性的 calculator 演示，不冒充模型自主决策。
            if any(tool["name"] == "calculator" for tool in available):
                expression = extract_expression(text)
                result = await self.mcp.call_tool("calculator", {"expression": expression})
                if result.get("isError", False):
                    raise ProtocolError("calculator 执行失败: " + content_text(result), -32004)
                tool_text = content_text(result)
                used.append("calculator")
        answer = await self.llm.answer(text, history, tool_text, math_agent=True)
        return answer, {"agent_id": self.agent_id, "tools_used": used,
                        "tool_selection": "mock_fixed" if self.mcp else "disabled", "tool_source": source,
                        "rag_candidates": [{"name": row["name"], "score": row["score"]} for row in candidates],
                        "warning": rag_warning}

    # 注册与心跳
    async def registration_loop(self):
        tags = {"math": ["math", "calculator"], "weather": ["weather", "forecast", "clothing", "air_quality", "sun_times"],
                "orchestrator": ["orchestrator"]}[self.kind]
        record = Registration(id=self.agent_id, name=self.kind, address=self.address, tags=tags)
        registered = False
        while True:
            try:
                if not registered:
                    response = await self.http.post(self.settings.registry_url + "/v1/agent/register",
                                                    json=record.model_dump())
                else:
                    response = await self.http.post(self.settings.registry_url + "/v1/agent/heartbeat",
                                                    json={"id": self.agent_id})
                response.raise_for_status()
                registered = response.json().get("success", False)
            except httpx.HTTPError:
                registered = False
                log.warning("注册中心暂不可达，稍后重试 agent=%s", self.agent_id)
            await asyncio.sleep(self.settings.heartbeat_interval)

# 把 Agent 包装成 HTTP 服务
def create_agent_app(kind, settings):
    @asynccontextmanager
    async def lifespan(app):
        store = await make_store(settings)
        engine = AgentEngine(kind, settings, store)
        app.state.engine = engine
        heartbeat = None
        try:
            await engine.start()
            heartbeat = asyncio.create_task(engine.registration_loop())
            yield
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            try:
                await engine.http.post(settings.registry_url + "/v1/agent/deregister",
                                       json={"id": engine.agent_id}, timeout=1)
            except httpx.HTTPError:
                pass
            await engine.close()

    app = FastAPI(title=f"{kind.title()} Agent", lifespan=lifespan)

    @app.get("/health")
    async def health():
        engine = app.state.engine
        return {"status": "ok", "agent_id": engine.agent_id, "provider": settings.llm_provider,
                "mcp_enabled": engine.mcp is not None, "tools": len(engine.tools),
                "rag_enabled": engine.retriever is not None, "warning": engine.warning,
                "weather_mode": settings.weather_mode if kind == "weather" else None}

    @app.get("/.well-known/agent-card.json")
    async def card():
        return {"name": f"{kind.title()} Agent", "version": "1.0.0",
                "url": app.state.engine.address,
                "capabilities": {"streaming": True},
                "skills": [{"id": kind, "name": kind, "description": f"{kind} service",
                            "tags": [kind]}],
                "defaultInputModes": ["text"], "defaultOutputModes": ["text"],
                "description": "本项目 A2A 风格子集；流式为完整回答后的分块传输"}
    # 接收并解析 A2A 风格的请求
    # 处理过程：
    # 收到请求
    #     ↓
    # 解析 JSON-RPC 格式
    #     ↓
    # 判断 message/send 等方法
    #     ↓
    # 调用 engine.process(params)
    #     ↓
    # 返回 result 或 error

    @app.post("/")
    async def endpoint(request: Request):
        request_id = None
        try:
            envelope = RpcEnvelope.model_validate(await request.json())
            request_id = envelope.id
            engine = app.state.engine
            if envelope.method == "tasks/get":
                task_id = envelope.params.get("id", "")
                task = await engine.store.get_task(task_id)
                if task is None:
                    return failure(request_id, "Task not found", -32002)
                return success(request_id, task)
            if envelope.method not in {"message/send", "message/stream"}:
                return failure(request_id, "Method not found", -32601)
            params = SendParams.model_validate(envelope.params)
            if envelope.method == "message/send":
                return success(request_id, (await engine.process(params)).model_dump())

            async def events():
                def encode(payload):
                    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                yield encode(success(request_id, {"type": "status", "content": "processing",
                                                  "contextId": params.message.contextId}))
                try:
                    reply = await engine.process(params)
                    yield encode(success(request_id, {"type": "status",
                        "content": "tools_used=" + ",".join(reply.metadata.get("tools_used", [])),
                        "contextId": reply.contextId, "taskId": reply.taskId}))
                    for index in range(0, len(reply.text), 32):
                        yield encode(success(request_id, {"type": "partial", "content": reply.text[index:index+32],
                                                          "contextId": reply.contextId, "taskId": reply.taskId}))
                        await asyncio.sleep(0)
                    yield encode(success(request_id, {"type": "complete", "content": "",
                                                      "contextId": reply.contextId, "taskId": reply.taskId}))
                except ProtocolError as exc:
                    yield encode(failure(request_id, str(exc), exc.code))
            return StreamingResponse(events(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache"})
        except (ValueError, ValidationError, TypeError) as exc:
            return failure(request_id, f"Invalid params: {str(exc)[:300]}", -32602)
        except ProtocolError as exc:
            return failure(request_id, str(exc), exc.code)

    return app

