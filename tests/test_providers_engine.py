"""远程 API 使用 MockTransport 验证协议；不消耗真实 API 配额。"""
import asyncio
from dataclasses import replace
import json
import sqlite3

import httpx
import pytest

from agent_comm.agents import AgentEngine
from agent_comm.config import Settings
from agent_comm.llm import ModelClient
from agent_comm.protocol import Message, TextPart, SendParams, ProtocolError
from agent_comm.rag import Retriever
from agent_comm.storage import SQLiteStore


async def test_qwen_request_shape_and_history_roles():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "回答"}}]})
    model = ModelClient(replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"))
    await model.client.aclose()
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        answer = await model.answer("问题", [{"role": "agent", "parts": [{"text": "之前回答"}]}])
        assert answer == "回答"
        assert [row["role"] for row in seen[0]["messages"]] == ["system", "assistant", "user"]
        assert seen[0]["model"] == "qwen-plus"
    finally:
        await model.close()


async def test_dashscope_embedding_response():
    def handler(request):
        assert json.loads(request.content)["input"]["texts"] == ["测试"]
        return httpx.Response(200, json={"output": {"embeddings": [{"embedding": [1.0, 0.0]}]}})
    retriever = Retriever(replace(Settings(), embedding_provider="dashscope", embedding_key="test-only"))
    await retriever.client.aclose()
    retriever.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await retriever.embed("测试") == [1.0, 0.0]
    finally:
        await retriever.close()


async def test_qwen_error_is_not_silently_mocked():
    model = ModelClient(replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"))
    await model.client.aclose()
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(401, json={"error": {"message": "invalid key"}})))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await model.answer("你好", [])
    finally:
        await model.close()


async def test_engine_deadline_and_cancellation_record_real_state(tmp_path):
    path = str(tmp_path / "state.db")
    engine = AgentEngine("math", replace(Settings(), enable_mcp=False), SQLiteStore(path))
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)
        return "late"
    engine.llm.answer = slow
    try:
        params = SendParams(message=Message(contextId="timeout", parts=[TextPart(text="1+7")]),
                            timeoutSeconds=0.03)
        with pytest.raises(ProtocolError) as timeout:
            await engine.process(params)
        assert timeout.value.code == -32001
        params = SendParams(message=Message(contextId="cancel", parts=[TextPart(text="1+7")]))
        running = asyncio.create_task(engine.process(params))
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        with sqlite3.connect(path) as db:
            records = [json.loads(row[0]) for row in db.execute("SELECT data FROM tasks").fetchall()]
        assert {record["contextId"]: record["status"]["state"] for record in records} == {
            "timeout": "failed", "cancel": "canceled"}
    finally:
        await engine.close()


async def test_math_without_mcp_explicitly_reports_demo_limit(tmp_path):
    engine = AgentEngine("math", replace(Settings(), enable_mcp=False), SQLiteStore(str(tmp_path / "state.db")))
    try:
        reply = await engine.process(SendParams(message=Message(parts=[TextPart(text="1+7")])))
        assert "未启用" in reply.text and reply.metadata["tools_used"] == []
    finally:
        await engine.close()

