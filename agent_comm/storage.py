"""历史只由协调器写；任务与会话使用不同 UUID，避免原示例的重复历史。"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Protocol

from .protocol import Message, new_id


class Store(Protocol):
    async def create_task(self, context_id: str, agent_id: str) -> dict: ...
    async def update_task(self, task_id: str, state: str, **fields) -> None: ...
    async def get_task(self, task_id: str) -> dict | None: ...
    async def add_message(self, message: Message) -> None: ...
    async def history(self, context_id: str, limit: int = 10) -> list[dict]: ...
    async def close(self) -> None: ...


def task_record(context_id, agent_id):
    return {"id": new_id("task"), "contextId": context_id, "agentId": agent_id,
            "status": {"state": "running", "timestamp": time.time()},
            "answer": "", "error": "", "metadata": {}}


class SQLiteStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT UNIQUE NOT NULL, context_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_context ON messages(context_id, sequence);
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _create(self, record):
        with self._connection() as db:
            db.execute("INSERT INTO tasks VALUES (?, ?)",
                       (record["id"], json.dumps(record, ensure_ascii=False)))
        return record

    async def create_task(self, context_id, agent_id):
        return await asyncio.to_thread(self._create, task_record(context_id, agent_id))

    def _update(self, task_id, state, fields):
        with self._connection() as db:
            # 一个事务中读改写，避免并发更新覆盖。
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            record = json.loads(row[0])
            record.update(fields)
            record["status"] = {"state": state, "timestamp": time.time()}
            db.execute("UPDATE tasks SET data=? WHERE id=?",
                       (json.dumps(record, ensure_ascii=False), task_id))

    async def update_task(self, task_id, state, **fields):
        await asyncio.to_thread(self._update, task_id, state, fields)

    def _get(self, task_id):
        with self._connection() as db:
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    async def get_task(self, task_id):
        return await asyncio.to_thread(self._get, task_id)

    def _add(self, message):
        with self._connection() as db:
            db.execute("INSERT OR IGNORE INTO messages(message_id,context_id,data) VALUES (?,?,?)",
                       (message.messageId, message.contextId, message.model_dump_json()))
            db.execute("""DELETE FROM messages WHERE context_id=? AND sequence NOT IN
                (SELECT sequence FROM messages WHERE context_id=? ORDER BY sequence DESC LIMIT 1000)""",
                       (message.contextId, message.contextId))

    async def add_message(self, message):
        await asyncio.to_thread(self._add, message)

    def _history(self, context_id, limit):
        if limit == 0:
            return []
        with self._connection() as db:
            rows = db.execute("SELECT data FROM messages WHERE context_id=? ORDER BY sequence DESC LIMIT ?",
                              (context_id, limit)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    async def history(self, context_id, limit=10):
        return await asyncio.to_thread(self._history, context_id, limit)

    async def close(self):
        pass


class RedisStore:
    """可选实现：真实 Redis 服务需自行启动；默认使用 SQLite。"""
    def __init__(self, url):
        from redis.asyncio import Redis
        self.redis = Redis.from_url(url, decode_responses=True)

    async def create_task(self, context_id, agent_id):
        task = task_record(context_id, agent_id)
        await self.redis.set("ac:task:" + task["id"], json.dumps(task, ensure_ascii=False))
        return task

    async def update_task(self, task_id, state, **fields):
        from redis.exceptions import WatchError
        key = "ac:task:" + task_id
        for _ in range(10):
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        raise KeyError(task_id)
                    task = json.loads(raw)
                    task.update(fields)
                    task["status"] = {"state": state, "timestamp": time.time()}
                    pipe.multi()
                    pipe.set(key, json.dumps(task, ensure_ascii=False))
                    await pipe.execute()
                    return
                except WatchError:
                    continue
        raise RuntimeError("Redis task 更新冲突")

    async def get_task(self, task_id):
        raw = await self.redis.get("ac:task:" + task_id)
        return json.loads(raw) if raw else None

    async def add_message(self, message):
        # 在一个 Redis 脚本内完成去重、写入与截断。
        script = """
        if redis.call('SADD', KEYS[1], ARGV[1]) == 1 then
          redis.call('RPUSH', KEYS[2], ARGV[2])
          redis.call('LTRIM', KEYS[2], -1000, -1)
        end
        """
        key = "ac:history:{" + message.contextId + "}"
        await self.redis.eval(script, 2, key + ":ids", key, message.messageId, message.model_dump_json())

    async def history(self, context_id, limit=10):
        if limit == 0:
            return []
        rows = await self.redis.lrange("ac:history:{" + context_id + "}", -limit, -1)
        return [json.loads(row) for row in rows]

    async def close(self):
        await self.redis.aclose()


async def make_store(settings):
    if settings.storage_backend == "redis":
        store = RedisStore(settings.redis_url)
        await store.redis.ping()
        return store
    return SQLiteStore(settings.sqlite_path)

