import json
import httpx
from .protocol import ProtocolError, new_id

# A2A服务端入口写在了agent.py的 'endpoint()'接收请求，交给 Agent 处理，再返回结果
class A2AClient:
    def __init__(self):
        self.http = httpx.AsyncClient(trust_env=False)

    # 封装JSON-RPC请求
    async def request(self, url, method, params, timeout=30):
        try:
            response = await self.http.post(url.rstrip("/") + "/", timeout=timeout,
                json={"jsonrpc": "2.0", "id": new_id("rpc"), "method": method, "params": params})     # 这里通过 HTTP 发送符合约定的 JSON
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                raise ProtocolError(body["error"]["message"], body["error"]["code"])
            return body["result"]
        except httpx.TimeoutException as exc:
            raise ProtocolError("下游请求超时", -32001) from exc
        except httpx.HTTPError as exc:
            raise ProtocolError("下游 Agent 不可达或 HTTP 错误", -32003) from exc

    async def stream(self, url, params, timeout=30):
        try:
            async with self.http.stream("POST", url.rstrip("/") + "/", timeout=timeout,
                    json={"jsonrpc": "2.0", "id": new_id("rpc"), "method": "message/stream", "params": params}) as response:
                response.raise_for_status()
                data = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        body = json.loads("\n".join(data))
                        data.clear()
                        if "error" in body:
                            raise ProtocolError(body["error"]["message"], body["error"]["code"])
                        yield body["result"]
        except httpx.TimeoutException as exc:
            raise ProtocolError("流式请求超时", -32001) from exc
        except httpx.HTTPError as exc:
            raise ProtocolError("流式下游不可达", -32003) from exc

    async def close(self):
        await self.http.aclose()

