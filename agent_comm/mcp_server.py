"""MCP 2025-06-18 stdio tools 子集：初始化、列工具、调用工具。"""
import asyncio
import json
import sys

from .config import Settings
from .protocol import failure, success
from .tools import build_registry

PROTOCOL_VERSION = "2025-06-18"


def emit(payload):
    # stdout 只输出协议 JSON；不打印调试信息。
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


async def serve():
    tools = build_registry(Settings.from_env().weather_mode) # 获取工具清单
    initialized, ready = False, False
    while line := await asyncio.to_thread(sys.stdin.buffer.readline):
        request_id = None
        try:
            if len(line) > 1024 * 1024:
                raise ValueError("请求过大")
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("无效 JSON-RPC 请求")
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ValueError("params 必须为 object")
            if method == "notifications/initialized":
                ready = initialized
                continue
            if request_id is None:
                continue
            if method == "initialize":
                initialized = True
                emit(success(request_id, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "agent-communication-tools", "version": "1.0.0"},
                }))
            elif method == "ping":
                emit(success(request_id, {}))
            elif not ready:
                emit(failure(request_id, "请先完成 initialize / initialized", -32002))
            elif method == "tools/list":
                emit(success(request_id, {"tools": tools.list_tools()}))
            elif method == "tools/call":
                name = params.get("name", "")
                if name == "logging_test":
                    emit({"jsonrpc": "2.0", "method": "notifications/message",
                          "params": {"level": "info", "logger": "demo", "data": "logging_test"}})
                token = params.get("_meta", {}).get("progressToken")
                if name == "progress_test" and token is not None:
                    emit({"jsonrpc": "2.0", "method": "notifications/progress",
                          "params": {"progressToken": token, "progress": 100, "total": 100}})
                result = await tools.call(name, params.get("arguments", {}))
                emit(success(request_id, result))
            else:
                emit(failure(request_id, "Method not found", -32601))
        except json.JSONDecodeError:
            emit(failure(request_id, "Parse error", -32700))
        except Exception as exc:
            emit(failure(request_id, str(exc), -32602))


if __name__ == "__main__":
    asyncio.run(serve())

