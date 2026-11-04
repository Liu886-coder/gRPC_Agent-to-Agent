"""gRPC 入口：Protobuf -> A2A，SSE -> gRPC stream，真实任务状态查询。"""
import asyncio
import logging
import time

import grpc
import httpx

from .a2a_client import A2AClient
from .generated import ai_query_pb2 as pb
from .generated import ai_query_pb2_grpc as rpc
from .protocol import Message, TextPart, ProtocolError, new_id

log = logging.getLogger(__name__)

# RPC Server由如下提供相对应的实现。
class QueryService(rpc.AIQueryServiceServicer):
    def __init__(self, settings):
        self.settings = settings
        self.client = A2AClient()

    def params(self, request, context):
        if not request.question.strip():
            raise ValueError("question 不能为空")
        if len(request.question) > 32000:
            raise ValueError("question 最多 32000 字符")
        if not 0 <= request.history_length <= 100 or not 0 <= request.timeout_seconds <= 300:
            raise ValueError("history_length 范围 0~100，timeout_seconds 范围 0~300")
        timeout = min(request.timeout_seconds or self.settings.request_timeout,
                      self.settings.request_timeout)
        remaining = context.time_remaining()
        if remaining is not None:
            timeout = max(0.001, min(timeout, remaining))
        message = Message(contextId=request.context_id or new_id("ctx"),
                          parts=[TextPart(text=request.question)])
        history_length = request.history_length if request.HasField("history_length") else 10
        return {"message": message.model_dump(), "historyLength": history_length,
                "timeoutSeconds": timeout, "deadline": time.time() + timeout}, timeout

    async def abort_error(self, context, exc):
        code = grpc.StatusCode.INTERNAL
        if isinstance(exc, ValueError):
            code = grpc.StatusCode.INVALID_ARGUMENT
        elif isinstance(exc, ProtocolError):
            code = {-32602: grpc.StatusCode.INVALID_ARGUMENT,
                    -32001: grpc.StatusCode.DEADLINE_EXCEEDED,
                    -32002: grpc.StatusCode.NOT_FOUND,
                    -32003: grpc.StatusCode.UNAVAILABLE,
                    -32004: grpc.StatusCode.FAILED_PRECONDITION}.get(exc.code, code)
        await context.abort(code, str(exc))

    async def Query(self, request, context):
        start = time.perf_counter()
        try:
            # 收到 gRPC 请求，转换参数
            params, timeout = self.params(request, context)
            # 改用 A2A 请求协调器, 去找协调器Agent. *************************非常重要！！！！！！
            result = await self.client.request(self.settings.orchestrator_url, "message/send", params, timeout)
            message = Message.model_validate(result)
            return pb.AIQueryResponse(request_id=request.request_id or new_id("req"),
                status=pb.Status(code=0, message="OK"), answer=message.text,
                context_id=message.contextId, task_id=message.taskId or "",
                agent_id=message.metadata.get("agent_id", ""),
                tools_used=message.metadata.get("tools_used", []),
                provider=message.metadata.get("provider", ""),
                processing_time_ms=int((time.perf_counter()-start)*1000))
        except (ValueError, ProtocolError) as exc:
            await self.abort_error(context, exc)

    async def QueryStream(self, request, context):
        try:
            params, timeout = self.params(request, context)
            async for event in self.client.stream(self.settings.orchestrator_url, params, timeout):
                yield pb.AIStreamEvent(event_id=new_id("event"), event_type=event["type"],
                    content=event.get("content", ""), task_id=event.get("taskId", ""),
                    context_id=event.get("contextId", params["message"]["contextId"]),
                    timestamp=int(time.time()*1000))
        except (ValueError, ProtocolError) as exc:
            await self.abort_error(context, exc)

    async def GetQueryStatus(self, request, context):
        if not request.task_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "task_id 不能为空")
        try:
            task = await self.client.request(self.settings.orchestrator_url, "tasks/get",
                                             {"id": request.task_id}, 5)
            return pb.QueryStatusResponse(status=pb.Status(code=0, message="OK"),
                task_state=task["status"]["state"], answer=task.get("answer", ""),
                context_id=task["contextId"], error=task.get("error", ""))
        except ProtocolError as exc:
            await self.abort_error(context, exc)

    async def Health(self, request, context):
        try:
            response = await self.client.http.get(self.settings.orchestrator_url + "/health", timeout=2)
            response.raise_for_status()
            return pb.HealthResponse(ready=response.json().get("status") == "ok", message="orchestrator reachable")
        except httpx.HTTPError:
            return pb.HealthResponse(ready=False, message="orchestrator unavailable")


async def serve(settings):
    server = grpc.aio.server(options=[
        ("grpc.max_receive_message_length", 1024*1024),
        ("grpc.max_send_message_length", 1024*1024),
    ])
    service = QueryService(settings)

    # 把具体实现注册给 gRPC 框架
    # 告诉 gRPC：收到请求后，交给 service 中对应的方法处理
    rpc.add_AIQueryServiceServicer_to_server(service, server)
    # 在指定地址和端口上监听
    port = server.add_insecure_port(settings.grpc_address)  # 这个地址在config.py下获取
    if port == 0:
        raise RuntimeError(f"无法监听 {settings.grpc_address}")
    await server.start()
    log.info("gRPC listening at %s", settings.grpc_address)
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=2)
        await service.client.close()

