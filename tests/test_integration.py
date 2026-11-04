"""真实 gRPC/HTTP/stdio 全链路测试；模型与天气使用明确的本地演示模式。"""
import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid

import grpc
import httpx
import pytest

from agent_comm.rpc_client import RpcClient
from agent_comm.storage import SQLiteStore

pytestmark = pytest.mark.integration


async def test_full_math_request_and_real_task_status(system):
    async with RpcClient(system["address"]) as client:
        assert (await client.health()).ready
        result = await client.query("请计算 (1+7)*2", "math-" + uuid.uuid4().hex)
        assert result.status.code == 0
        assert result.agent_id == "math-1"
        assert result.provider == "mock"
        assert result.tools_used == ["calculator"]
        assert "= 16" in result.answer
        status = await client.status(result.task_id)
        assert status.task_state == "completed"
        assert status.answer == result.answer


async def test_history_isolation_and_no_duplicates(system):
    context = "history-" + uuid.uuid4().hex
    async with RpcClient(system["address"]) as client:
        await client.query("我叫小明", context)
        remembered = await client.query("我的名字是什么", context)
        assert "小明" in remembered.answer
        other = await client.query("我的名字是什么", "other-" + uuid.uuid4().hex)
        assert "没有提供" in other.answer
        await client.query("1+7", context)
    history = await SQLiteStore(system["db"]).history(context, 100)
    assert len(history) == 6  # 三次请求，每次一条 user + 一条 agent。
    assert [row["role"] for row in history] == ["user", "agent"] * 3


async def test_explicit_zero_history_is_respected(system):
    context = "no-history-" + uuid.uuid4().hex
    async with RpcClient(system["address"]) as client:
        await client.query("我叫小明", context)
        result = await client.query("我的名字是什么", context, history_length=0)
        assert "没有提供" in result.answer


async def test_stream_order_and_unicode(system):
    async with RpcClient(system["address"]) as client:
        events = [event async for event in client.stream("1+7", "stream-" + uuid.uuid4().hex)]
    types = [event.event_type for event in events]
    assert types[0] == "status"
    assert types[-1] == "complete"
    text = "".join(event.content for event in events if event.event_type == "partial")
    assert "= 8" in text and "本地演示" in text
    assert events[-1].task_id


async def test_invalid_question_and_task_error_codes(system):
    async with RpcClient(system["address"]) as client:
        with pytest.raises(grpc.aio.AioRpcError) as empty:
            await client.query("   ")
        assert empty.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        with pytest.raises(grpc.aio.AioRpcError) as missing:
            await client.status("does-not-exist")
        assert missing.value.code() == grpc.StatusCode.NOT_FOUND


async def test_tool_business_error_fails_task_and_stream(system):
    context = "failure-" + uuid.uuid4().hex
    async with RpcClient(system["address"]) as client:
        with pytest.raises(grpc.aio.AioRpcError) as unary:
            await client.query("1/0", context)
        assert unary.value.code() == grpc.StatusCode.FAILED_PRECONDITION
        with pytest.raises(grpc.aio.AioRpcError) as stream:
            _ = [event async for event in client.stream("1/0", context + "-stream")]
        assert stream.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    with sqlite3.connect(system["db"]) as db:
        rows = db.execute("SELECT data FROM tasks").fetchall()
    failed = [json.loads(row[0]) for row in rows if json.loads(row[0])["contextId"] == context]
    assert failed and all(task["status"]["state"] == "failed" for task in failed)


async def test_concurrent_mcp_responses_reach_correct_request(system):
    async with RpcClient(system["address"]) as client:
        results = await asyncio.gather(*(client.query(f"{i}+7", "parallel-" + uuid.uuid4().hex)
                                         for i in range(6)))
        for index, result in enumerate(results):
            assert f"= {index+7}" in result.answer
            assert result.tools_used == ["calculator"]


async def test_json_unicode_long_body_and_unknown_a2a_method(system):
    text = '你好 "引号" [方括号] \\路径\n' + "长消息" * 3000
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(system["orchestrator"], json={
            "jsonrpc": "2.0", "id": "long", "method": "message/send",
            "params": {"message": {"role": "user", "contextId": uuid.uuid4().hex,
                                   "parts": [{"kind": "text", "text": text}]}}})
        body = response.json()
        assert "result" in body, body
        assert text in body["result"]["parts"][0]["text"]
        response = await client.post(system["orchestrator"], json={
            "jsonrpc": "2.0", "id": "unknown", "method": "unknown"})
        assert response.json()["error"]["code"] == -32601


async def test_agent_card_registry_and_mcp_health(system):
    async with httpx.AsyncClient(trust_env=False) as client:
        card = (await client.get(system["math"] + "/.well-known/agent-card.json")).json()
        assert card["capabilities"]["streaming"] is True
        health = (await client.get(system["math"] + "/health")).json()
        assert health["tools"] == 14 and health["mcp_enabled"] and health["rag_enabled"]
        agents = (await client.get(system["registry"] + "/v1/agents")).json()["agents"]
        assert {"math-1", "weather-1", "orchestrator-1"} <= {agent["id"] for agent in agents}
        weather = (await client.get(system["weather"] + "/health")).json()
        assert weather["tools"] == 3 and weather["mcp_enabled"] and weather["rag_enabled"]


def test_cli_single_query(system):
    completed = subprocess.run([sys.executable, "-m", "agent_comm.rpc_client",
        "--address", system["address"], "--question", "1+7", "--json"],
        text=True, encoding="utf-8", capture_output=True, timeout=15,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["tools_used"] == ["calculator"]


def test_cli_interactive_accepts_plain_chinese_questions(system):
    completed = subprocess.run([sys.executable, "-m", "agent_comm.rpc_client", "--address", system["address"]],
        input="帮我算一下6×5478\n/quit\n", text=True, encoding="utf-8", capture_output=True, timeout=15,
        env={**__import__("os").environ, "LLM_PROVIDER": "mock", "PYTHONIOENCODING": "utf-8"})
    assert completed.returncode == 0, completed.stderr
    assert "32868" in completed.stdout and "calculator" in completed.stdout


async def test_weather_routes_via_a2a_and_mcp_and_handles_followup(system):
    context = "weather-" + uuid.uuid4().hex
    async with RpcClient(system["address"]) as client:
        result = await client.query("宁波未来三天天气和穿衣建议", context)
        assert result.agent_id == "weather-1" and result.tools_used == ["get_weather"]
        assert "模拟天气" in result.answer and "宁波" in result.answer and "穿衣建议" in result.answer
        assert result.answer.count("℃") == 3
        assert (await client.status(result.task_id)).task_state == "completed"
        other = await client.query("那上海呢", context)
        assert other.agent_id == "weather-1" and "上海" in other.answer
        tomorrow = await client.query("明天呢", context)
        assert tomorrow.agent_id == "weather-1" and "上海" in tomorrow.answer
    history = await SQLiteStore(system["db"]).history(context, 10)
    assert len(history) == 6
    assert history[-1]["metadata"]["tool_calls"][0]["arguments"]["start_offset"] == 1


async def test_weather_without_city_asks_for_city(system):
    async with RpcClient(system["address"]) as client:
        result = await client.query("今天天气如何", "no-city-" + uuid.uuid4().hex)
        assert result.agent_id == "weather-1" and not result.tools_used
        assert "城市" in result.answer


async def test_air_sun_and_combined_queries_route_to_independent_tools(system):
    async with RpcClient(system["address"]) as client:
        context = "environment-" + uuid.uuid4().hex
        air = await client.query("宁波今天空气质量和PM2.5", context)
        assert air.agent_id == "weather-1" and air.tools_used == ["get_air_quality"]
        assert "US AQI" in air.answer and "模拟" in air.answer
        tomorrow = await client.query("明天呢", context)
        assert tomorrow.tools_used == ["get_air_quality"]
        sun = await client.query("上海明天的日出日落时间", context)
        assert sun.agent_id == "weather-1" and sun.tools_used == ["get_sun_times"]
        assert "日出 06:00" in sun.answer and "白昼" in sun.answer
        combined = await client.query("北京今天的天气、空气质量、日出日落一起查", context)
        assert set(combined.tools_used) == {"get_weather", "get_air_quality", "get_sun_times"}
        assert "穿衣建议" in combined.answer and "US AQI" in combined.answer and "日落" in combined.answer

