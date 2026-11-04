from dataclasses import replace
import asyncio

import pytest
from agent_comm.config import Settings
from agent_comm.mcp_client import MCPClient, content_text
from agent_comm.rag import Retriever, cosine
from agent_comm.tools import build_registry


def test_cosine():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0)
    assert cosine([0, 0], [1, 1]) == 0
    assert cosine([1], [1, 2]) == 0


async def test_retrieval_sort_limit_and_cache():
    retriever = Retriever(Settings(), max_cache=30)
    try:
        await retriever.index_tools(build_registry().list_tools())
        rows = await retriever.retrieve("calculator 数学表达式 1+7", 3)
        assert len(rows) <= 3
        assert "calculator" in [row["name"] for row in rows]
        assert [row["score"] for row in rows] == sorted([row["score"] for row in rows], reverse=True)
        await retriever.embed("repeat")
        hits = retriever.hits
        await retriever.embed("repeat")
        assert retriever.hits == hits + 1
        with pytest.raises(ValueError):
            await retriever.retrieve("query", 0)
    finally:
        await retriever.close()


async def test_lru_and_ttl():
    retriever = Retriever(Settings(), max_cache=2, ttl=0.01)
    try:
        await retriever.embed("a")
        await retriever.embed("b")
        await retriever.embed("a")
        await retriever.embed("c")
        assert "b" not in retriever.cache
        await asyncio.sleep(0.02)
        before = retriever.hits
        await retriever.embed("a")
        assert retriever.hits == before
    finally:
        await retriever.close()


async def test_real_mcp_handshake_tools_errors_notifications_and_cleanup():
    client = MCPClient()
    async with client:
        process = client.process
        assert len(await client.list_tools()) == 14
        result = await client.call_tool("calculator", {"expression": "(1+7)*2"})
        assert not result["isError"]
        assert content_text(result).endswith("= 16")
        bad = await client.call_tool("divide", {"a": 1, "b": 0})
        assert bad["isError"]
        await client.call_tool("logging_test", {})
        assert any(note["method"] == "notifications/message" for note in client.notifications)
        results = await asyncio.gather(*(
            client.call_tool("add", {"a": index, "b": 1}) for index in range(5)))
        assert [content_text(result) for result in results] == [f"{i} + 1 = {i+1}" for i in range(5)]
    assert process.returncode is not None


async def test_mcp_timeout_does_not_misroute_late_response():
    async with MCPClient() as client:
        client.timeout = 0.02
        with pytest.raises(TimeoutError):
            await client.call_tool("sleep", {"milliseconds": 150})
        client.timeout = 2
        result = await client.call_tool("calculator", {"expression": "2+3"})
        assert content_text(result).endswith("= 5")

