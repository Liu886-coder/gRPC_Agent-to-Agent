"""工具检索：本地词元哈希演示 / 可选 DashScope 语义 Embedding，LRU+TTL。"""
from collections import OrderedDict
import hashlib
import math
import re
import time

import httpx


def cosine(left, right):
    if len(left) != len(right) or not left:
        return 0.0
    norm = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    return sum(a*b for a, b in zip(left, right)) / norm if norm else 0.0


class Retriever:
    def __init__(self, settings, dimension=256, max_cache=1000, ttl=3600):
        self.settings, self.dimension = settings, dimension
        self.max_cache, self.ttl = max_cache, ttl
        self.cache = OrderedDict()
        self.index = []
        self.hits = 0
        self.client = httpx.AsyncClient(timeout=settings.request_timeout, trust_env=False)

    async def embed(self, text):
        if text in self.cache:
            inserted, vector = self.cache.pop(text)
            if time.monotonic() - inserted < self.ttl:
                self.cache[text] = (inserted, vector)
                self.hits += 1
                return vector
        if self.settings.embedding_provider == "local":
            # 这是可复现的词法特征，不是训练好的语义模型。
            normalized = text.lower()
            tokens = re.findall(r"[a-z_]+|[\u4e00-\u9fff]", normalized)
            chinese = re.findall(r"[\u4e00-\u9fff]+", normalized)
            tokens += [part[i:i+2] for part in chinese for i in range(len(part)-1)]
            if re.search(r"\d\s*[+*/^%-]", text):
                tokens += ["calculator", "math", "数学", "计算"] * 3
            vector = [0.0] * self.dimension
            for token in tokens:
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                vector[int.from_bytes(digest, "big") % self.dimension] += 1
        else:
            if not self.settings.embedding_key:
                raise ValueError("DashScope Embedding 需要 DASHSCOPE_API_KEY")
            response = await self.client.post(self.settings.embedding_url,
                headers={"Authorization": f"Bearer {self.settings.embedding_key}"},
                json={"model": self.settings.embedding_model, "input": {"texts": [text]},
                      "parameters": {"text_type": "query"}})
            response.raise_for_status()
            body = response.json()
            if "code" in body:
                raise ValueError(body.get("message", "Embedding API error"))
            vector = body["output"]["embeddings"][0]["embedding"]
        self.cache[text] = (time.monotonic(), vector)
        while len(self.cache) > self.max_cache:
            self.cache.popitem(last=False)
        return vector

    async def index_tools(self, tools):
        indexed = []
        for tool in tools:
            text = f"{tool['name']} {tool['description']} {tool['inputSchema']}"
            indexed.append((tool, await self.embed(text)))
        self.index = indexed

    async def retrieve(self, question, top_k=None):
        k = self.settings.rag_top_k if top_k is None else top_k
        if k <= 0:
            raise ValueError("top_k 必须大于 0")
        query = await self.embed(question)
        scored = [{**tool, "score": cosine(query, vector)} for tool, vector in self.index]
        return sorted((row for row in scored if row["score"] >= self.settings.rag_threshold),
                      key=lambda row: (-row["score"], row["name"]))[:k]

    async def close(self):
        await self.client.aclose()

