"""gRPC 客户端入口：交互/单次查询、任务状态与健康检查，明确显示 mock/qwen。"""
import argparse
import asyncio
import json

import grpc
from google.protobuf.json_format import MessageToDict

from .config import Settings
from .generated import ai_query_pb2 as pb
from .generated import ai_query_pb2_grpc as rpc
from .protocol import new_id

# 客户端发起 gRPC 调用
class RpcClient:
    def __init__(self, address):

        # 客户端建立连接并调用方法
        self.channel = grpc.aio.insecure_channel(address)   # 创建连接 RPC Server 的异步 gRPC 通道，使用明文通信（未启用 TLS） 目前是：127.0.0.1:50051
        self.stub = rpc.AIQueryServiceStub(self.channel)    # 基于该通道创建 AIQueryService 的客户端代理，提供远程方法的调用入口

    # 这句 self.stub.Query(...) 就是业务请求发起 RPC 调用的位置。
    async def query(self, question, context_id="", timeout=30, history_length=10):

        # 这个就是一次gRPC的远程调用
        return await self.stub.Query(pb.AIQueryRequest(
            request_id=new_id("req"), question=question, context_id=context_id,
            timeout_seconds=int(timeout), history_length=history_length), timeout=timeout)

    async def stream(self, question, context_id="", timeout=30):
        call = self.stub.QueryStream(pb.AIQueryRequest(
            request_id=new_id("req"), question=question, context_id=context_id,
            timeout_seconds=int(timeout), history_length=10), timeout=timeout)
        try:
            async for event in call:
                yield event
        finally:
            call.cancel()

    async def status(self, task_id):
        return await self.stub.GetQueryStatus(pb.QueryStatusRequest(task_id=task_id), timeout=5)

    async def health(self):
        return await self.stub.Health(pb.Empty(), timeout=5)

    async def close(self):
        await self.channel.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()


async def run(args):
    settings = Settings.from_env()
    if args.timeout is None:
        args.timeout = settings.request_timeout
    async with RpcClient(args.address or settings.grpc_address) as client:
        if args.health:
            result = await client.health()
            print(MessageToDict(result, preserving_proto_field_name=True))
            return 0 if result.ready else 1
        if args.task_id:
            print(json.dumps(MessageToDict(await client.status(args.task_id),
                                            preserving_proto_field_name=True), ensure_ascii=False, indent=2))
            return 0
        context_id = args.context or new_id("ctx")
        stream = args.stream

        async def send(question):
            if stream:
                print("Agent: ", end="", flush=True)
                async for event in client.stream(question, context_id, args.timeout):
                    if event.event_type == "partial":
                        print(event.content, end="", flush=True)
                    elif event.event_type == "complete":
                        print(f"\n[task_id={event.task_id}, context_id={event.context_id}]")
            else:
                result = await client.query(question, context_id, args.timeout)
                if args.json:
                    print(json.dumps(MessageToDict(result, preserving_proto_field_name=True),
                                     ensure_ascii=False, indent=2))
                else:
                    print(result.answer)
                    print(f"[agent={result.agent_id}, provider={result.provider}, tools={list(result.tools_used)}]")
                    print(f"[task_id={result.task_id}, context_id={result.context_id}, {result.processing_time_ms}ms]")
        if args.question is not None:
            await send(args.question)
            return 0
        print("交互模式：/quit 退出，/stream 切换分块输出，/context ID 切换会话")
        print("context_id =", context_id)
        while True:
            try:
                question = await asyncio.to_thread(input, "> ")
            except EOFError:
                break
            if question == "/quit":
                break
            if question == "/stream":
                stream = not stream
                print("stream =", stream, "（完整回答后分块）")
                continue
            if question.startswith("/context "):
                context_id = question.split(" ", 1)[1].strip() or new_id("ctx")
                print("context_id =", context_id)
                continue
            if not question.strip():
                continue
            try:
                await send(question)
            except grpc.aio.AioRpcError as exc:
                print(f"请求失败 [{exc.code().name}]: {exc.details()}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Python Agent RPC Client")
    parser.add_argument("--address")
    parser.add_argument("--question", "-q")
    parser.add_argument("--context")
    parser.add_argument("--timeout", type=float, help="单次请求秒数，默认读取 REQUEST_TIMEOUT")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except grpc.aio.AioRpcError as exc:
        print(f"请求失败 [{exc.code().name}]: {exc.details()}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

