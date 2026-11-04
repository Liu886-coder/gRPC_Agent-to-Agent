"""内存注册中心：TTL 心跳剔除、标签查询和轮询，不保存业务对话。"""
import asyncio
import time
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class Registration(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str
    address: str
    tags: list[str]


class Registry:
    def __init__(self, ttl=20):
        self.ttl = ttl
        self.agents = {}        # 注册中心保存的是不同 Agent 的“登记信息”，如编号、地址和能力标签。Agent 程序本身仍在各自的进程里运行。
        self.indices = {}
        self.lock = asyncio.Lock()

    def _prune(self):
        now = time.monotonic()
        for agent_id in list(self.agents):
            if now - self.agents[agent_id]["last_seen"] > self.ttl:
                del self.agents[agent_id]

    async def register(self, record: Registration):
        parsed = urlparse(record.address)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Agent address 必须是 HTTP/HTTPS 地址")
        async with self.lock:
            self.agents[record.id] = {**record.model_dump(), "last_seen": time.monotonic()}

    async def heartbeat(self, agent_id):
        async with self.lock:
            self._prune()
            if agent_id not in self.agents:
                return False
            self.agents[agent_id]["last_seen"] = time.monotonic()
            return True

    async def deregister(self, agent_id):
        async with self.lock:
            self.agents.pop(agent_id, None)

    async def find(self, tag=None):
        async with self.lock:
            self._prune()
            return [{k: v for k, v in row.items() if k != "last_seen"}
                    for row in self.agents.values() if tag is None or tag in row["tags"]]

    async def select(self, tag):
        async with self.lock:
            self._prune()
            rows = sorted((row for row in self.agents.values() if tag in row["tags"]), key=lambda row: row["id"])
            if not rows:
                return None
            index = self.indices.get(tag, 0)
            self.indices[tag] = index + 1
            return rows[index % len(rows)]["address"]


class AgentID(BaseModel):
    id: str


class FindRequest(BaseModel):
    tag: str


def create_registry_app(settings):
    app = FastAPI(title="Agent Registry")
    registry = Registry(settings.heartbeat_ttl)
    app.state.registry = registry

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "registry"}

    @app.post("/v1/agent/register")
    async def register(record: Registration):
        try:
            await registry.register(record)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"success": True}

    @app.post("/v1/agent/heartbeat")
    async def heartbeat(request: AgentID):
        return {"success": await registry.heartbeat(request.id)}

    @app.post("/v1/agent/deregister")
    async def deregister(request: AgentID):
        await registry.deregister(request.id)
        return {"success": True}

    @app.post("/v1/agent/find")
    async def find(request: FindRequest):
        return {"agents": await registry.find(request.tag)}

    @app.get("/v1/agents")
    async def agents():
        return {"agents": await registry.find()}

    @app.get("/v1/agent/select/{tag}")
    async def select(tag: str):
        address = await registry.select(tag)
        if address is None:
            raise HTTPException(404, f"没有在线的 {tag} Agent")
        return {"address": address}

    return app

