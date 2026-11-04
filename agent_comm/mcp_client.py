"""可跨 Windows/Linux 的 stdio 客户端；UUID 关联响应、初始化、超时、进程清理。"""
import asyncio
from collections import deque
import json
import os
import subprocess
import sys

from .config import ROOT
from .protocol import ProtocolError, new_id
from .mcp_server import PROTOCOL_VERSION


class MCPClient:
    def __init__(self, timeout=10, command=None):
        self.timeout = timeout
        self.command = command or [sys.executable, "-m", "agent_comm.mcp_server"]
        self.process = None
        self.pending = {}
        self.notifications = deque(maxlen=100)
        self.write_lock = asyncio.Lock()
        self.call_lock = asyncio.Lock()
        self.reader_task = None
        self.stderr_task = None
        self.stderr_tail = deque(maxlen=20)

    async def start(self):
        if self.process is not None:
            return
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=str(ROOT), creationflags=flags,
            limit=1024 * 1024, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.reader_task = asyncio.create_task(self._read())
        self.stderr_task = asyncio.create_task(self._read_stderr())
        try:
            result = await self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "agent-communication-python", "version": "1.0.0"},
            })
            if result.get("protocolVersion") != PROTOCOL_VERSION:
                raise ProtocolError("MCP 协议版本不受支持")
            await self.notify("notifications/initialized")
        except BaseException:
            await self.close()
            raise

    async def _read_stderr(self):
        while line := await self.process.stderr.readline():
            self.stderr_tail.append(line.decode("utf-8", errors="replace").strip())

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                request_id = message.get("id")
                if request_id is None:
                    self.notifications.append(message)
                    continue
                future = self.pending.get(request_id)
                if future and not future.done():
                    if "error" in message:
                        future.set_exception(ProtocolError(message["error"]["message"],
                                                           message["error"].get("code", -32603)))
                    else:
                        future.set_result(message.get("result", {}))
        except Exception as exc:
            self._fail_pending(f"MCP 协议读取失败: {exc}")
        finally:
            self._fail_pending("MCP 子进程已经关闭")

    def _fail_pending(self, text):
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(ProtocolError(text))

    async def _write(self, payload):
        if self.process is None or self.process.returncode is not None:
            raise ProtocolError("MCP 未连接")
        async with self.write_lock:
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await self.process.stdin.drain()

    async def notify(self, method, params=None):
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def request(self, method, params=None):
        request_id = new_id("mcp")
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(request_id, None)

    async def list_tools(self):
        return (await self.request("tools/list"))["tools"]

    async def call_tool(self, name, arguments):
        # 本服务端按序执行。即使调用方并发，也为每个调用完整保留超时预算。
        async with self.call_lock:
            return await self.request("tools/call", {"name": name, "arguments": arguments})

    async def close(self):
        if self.process is None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 2)
        except asyncio.TimeoutError:
            if self.process.returncode is None:
                self.process.terminate()
            await self.process.wait()
        for task in (self.reader_task, self.stderr_task):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (self.reader_task, self.stderr_task) if task),
                             return_exceptions=True)
        self._fail_pending("MCP 连接已关闭")
        self.process = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()


def content_text(result):
    return "\n".join(part.get("text", "") for part in result.get("content", [])
                     if part.get("type") == "text")

