import asyncio
import time
import pytest

from agent_comm.protocol import Message, TextPart
from agent_comm.registry import Registry, Registration
from agent_comm.storage import SQLiteStore


async def test_history_deduplicated_and_isolated(tmp_path):
    store = SQLiteStore(str(tmp_path / "state.db"))
    message = Message(contextId="one", parts=[TextPart(text='中文 "引号" [括号]\\换行\n')])
    await asyncio.gather(*(store.add_message(message) for _ in range(4)))
    assert len(await store.history("one")) == 1
    assert (await store.history("one"))[0]["parts"][0]["text"] == message.text
    assert await store.history("two") == []
    assert await store.history("one", 0) == []


async def test_task_persists_across_store_instances(tmp_path):
    path = str(tmp_path / "state.db")
    store = SQLiteStore(path)
    task = await store.create_task("context", "math-1")
    assert task["id"] != "context"
    await store.update_task(task["id"], "completed", answer="8")
    other = SQLiteStore(path)
    loaded = await other.get_task(task["id"])
    assert loaded["status"]["state"] == "completed"
    assert loaded["answer"] == "8"
    assert await other.get_task("unknown") is None


async def test_registry_round_robin_and_replace_tags():
    registry = Registry(30)
    for index in range(2):
        await registry.register(Registration(id=str(index), name="math",
            address=f"http://localhost:{5001+index}", tags=["math"]))
    assert await registry.select("math") == "http://localhost:5001"
    assert await registry.select("math") == "http://localhost:5002"
    await registry.register(Registration(id="0", name="general", address="http://localhost:5001", tags=["general"]))
    assert len(await registry.find("math")) == 1
    assert await registry.select("missing") is None


async def test_registry_ttl(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    registry = Registry(20)
    await registry.register(Registration(id="one", name="math", address="http://localhost:5001", tags=["math"]))
    now[0] = 110
    assert await registry.heartbeat("one")
    now[0] = 131
    assert await registry.find("math") == []
    assert not await registry.heartbeat("one")

