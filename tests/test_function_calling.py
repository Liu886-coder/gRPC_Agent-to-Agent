"""模型响应使用 MockTransport；工具执行仍走真实 MCP 子进程。"""
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from agent_comm.agents import AgentEngine
from agent_comm.config import Settings
from agent_comm.llm import ModelClient
from agent_comm.protocol import Message, TextPart, SendParams, ProtocolError
from agent_comm.storage import SQLiteStore
from agent_comm.tools import build_registry


def tool_call(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


async def connect_transport(model, handler):
    await model.client.aclose()
    model.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_rag_candidates_reach_qwen_and_selected_tool_executes_via_real_mcp(tmp_path):
    settings = replace(Settings(), llm_provider="qwen", qwen_api_key="test-only")
    engine = AgentEngine("math", settings, SQLiteStore(str(tmp_path / "state.db")))
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            message = {"role": "assistant", "content": None,
                       "tool_calls": [tool_call("multiply", {"a": 6, "b": 5478})]}
        else:
            message = {"role": "assistant", "content": "6×5478=32868。"}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    try:
        await engine.start()
        multiply = next(tool for tool in engine.tools if tool["name"] == "multiply")
        engine.retriever.retrieve = AsyncMock(return_value=[{**multiply, "score": 0.91}])
        await connect_transport(engine.llm, handler)
        reply = await engine.process(SendParams(message=Message(parts=[TextPart(text="帮我算一下6×5478")])))
        assert "32868" in reply.text
        assert reply.metadata["tools_used"] == ["multiply"]
        assert reply.metadata["tool_source"] == "rag"
        assert reply.metadata["tool_selection"] == "qwen_function_calling"
        assert reply.metadata["tool_calls"] == [{"name": "multiply", "arguments": {"a": 6, "b": 5478}}]
        assert [tool["function"]["name"] for tool in seen[0]["tools"]] == ["multiply"]
        assert seen[0]["tools"][0]["function"]["parameters"] == multiply["inputSchema"]
        result = seen[1]["messages"][-1]
        assert result["role"] == "tool" and result["tool_call_id"] == "call-1"
        assert "32868" in json.loads(result["content"])["content"][0]["text"]
    finally:
        await engine.close()


async def test_multiple_tool_steps_and_original_assistant_message_are_preserved():
    model = ModelClient(replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"))
    tools = [tool for tool in build_registry().list_tools() if tool["name"] in {"factorial", "add"}]
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            message = {"role": "assistant", "content": None, "reasoning_content": "先求阶乘",
                       "tool_calls": [tool_call("factorial", {"n": 5})]}
        elif len(seen) == 2:
            assert json.loads(body["messages"][-1]["content"])["content"][0]["text"] == "120"
            assert body["messages"][-2]["reasoning_content"] == "先求阶乘"
            message = {"role": "assistant", "content": None,
                       "tool_calls": [tool_call("add", {"a": 120, "b": 2}, "call-2")]}
        else:
            message = {"role": "assistant", "content": "结果是122。"}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    try:
        await connect_transport(model, handler)
        answer, calls = await model.answer_with_tools("5的阶乘加2", [], tools, build_registry().call)
        assert answer == "结果是122。"
        assert [call["name"] for call in calls] == ["factorial", "add"]
    finally:
        await model.close()


@pytest.mark.parametrize("call", [
    tool_call("calculator", {"expression": "1+7"}),  # 未检索到的工具不能执行。
    tool_call("multiply", {"a": "6", "b": 5478}),
    tool_call("multiply", {"a": 6}),
    tool_call("multiply", {"a": 6, "b": 5478, "extra": 1}),
    {"id": "bad-json", "type": "function", "function": {"name": "multiply", "arguments": "{"}},
])
async def test_invalid_model_tool_calls_never_reach_mcp(call):
    model = ModelClient(replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"))
    tools = [tool for tool in build_registry().list_tools() if tool["name"] == "multiply"]
    execute = AsyncMock()
    try:
        await connect_transport(model, lambda _: httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None, "tool_calls": [call]}}]}))
        with pytest.raises(ProtocolError) as error:
            await model.answer_with_tools("帮我算一下6×5478", [], tools, execute)
        assert error.value.code == -32004
        execute.assert_not_awaited()
    finally:
        await model.close()


async def test_model_tool_loop_is_bounded():
    model = ModelClient(replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"))
    tools = [tool for tool in build_registry().list_tools() if tool["name"] == "multiply"]
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [tool_call("multiply", {"a": 1, "b": 2}, f"call-{len(seen)}")]}}]})
    execute = AsyncMock(return_value={"isError": False, "content": [{"type": "text", "text": "2"}]})
    try:
        await connect_transport(model, handler)
        with pytest.raises(ProtocolError, match="上限"):
            await model.answer_with_tools("1*2", [], tools, execute, max_rounds=1)
        assert execute.await_count == 1
        assert seen[-1]["tool_choice"] == "none"
    finally:
        await model.close()


async def test_empty_retrieval_does_not_restore_unretrieved_tools(tmp_path):
    engine = AgentEngine("math", replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"),
                         SQLiteStore(str(tmp_path / "state.db")))
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "当前没有合适的工具。"}}]})
    try:
        await engine.start()
        engine.retriever.retrieve = AsyncMock(return_value=[])
        engine.mcp.call_tool = AsyncMock()
        await connect_transport(engine.llm, handler)
        reply = await engine.process(SendParams(message=Message(parts=[TextPart(text="1+7")])))
        assert reply.metadata["tools_used"] == []
        assert reply.metadata["tool_source"] == "rag"
        assert "没有满足阈值" in reply.metadata["warning"]
        assert "tools" not in seen[0]
        engine.mcp.call_tool.assert_not_awaited()
    finally:
        await engine.close()


async def test_qwen_tool_execution_error_is_preserved(tmp_path):
    engine = AgentEngine("math", replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"),
                         SQLiteStore(str(tmp_path / "state.db")))
    try:
        await engine.start()
        divide = next(tool for tool in engine.tools if tool["name"] == "divide")
        engine.retriever.retrieve = AsyncMock(return_value=[{**divide, "score": 1.0}])
        await connect_transport(engine.llm, lambda _: httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [tool_call("divide", {"a": 1, "b": 0})]}}]}))
        with pytest.raises(ProtocolError) as error:
            await engine.process(SendParams(message=Message(parts=[TextPart(text="1/0")])))
        assert error.value.code == -32004
        assert "divide 执行失败" in str(error.value)
    finally:
        await engine.close()
