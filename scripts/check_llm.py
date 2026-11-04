"""直接检查真实千问：分类和回答各调用一次 API，不启动本地服务。"""
from pathlib import Path
import asyncio
import sys

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_comm.config import Settings
from agent_comm.llm import ModelClient


async def main():
    settings = Settings.from_env()
    if settings.llm_provider != "qwen":
        print("当前不是 qwen 模式。请在 .env 设置 LLM_PROVIDER=qwen 和 QWEN_API_KEY。")
        return 1
    print(f"provider=qwen; model={settings.qwen_model}")
    print(f"endpoint={settings.qwen_base_url}/chat/completions")
    print("将发送两次真实 API 请求：意图分类、回答生成。")
    model = ModelClient(settings)
    try:
        intent = await model.classify("1+7")
        if intent != "math":
            print(f"分类接口已响应，但 1+7 被分类为 {intent}；请检查模型行为。")
            return 1
        print("[OK] 意图分类：math")
        answer = await model.answer("请用一句中文简短确认你已经收到这条 API 测试消息。", [])
        print("[OK] 回答生成：", answer)
        print("千问 API 检查通过。下一步启动 scripts/start_system.py 验证完整链路。")
        return 0
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hints = {
            400: "检查模型是否支持当前 Chat Completions 参数。",
            401: "检查 Key 是否正确，以及 Key 和 Base URL 是否属于同一地域。",
            403: "检查模型权限、业务空间授权和访问 IP 限制。",
            404: "检查 Base URL、模型名称和该地域的模型可用性。",
            429: "检查请求限流、账户额度和服务端错误信息。",
        }
        print(f"API 检查失败：HTTP {status}。" + hints.get(status, "请检查服务商状态及账户配置。"))
        return 1
    except httpx.TimeoutException:
        print("API 请求超时。检查网络和地域；可在 .env 增加 REQUEST_TIMEOUT。")
        return 1
    except httpx.RequestError as exc:
        print(f"API 连接失败：{type(exc).__name__}。检查网络、DNS 和 Base URL。")
        print("当前 ModelClient 使用 trust_env=False，不读取 HTTP_PROXY/HTTPS_PROXY 环境变量。")
        return 1
    except (ValueError, KeyError, IndexError, TypeError):
        print("API 返回格式不符合预期，或分类结果无法解析；请检查模型和接口兼容性。")
        return 1
    finally:
        await model.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ValueError as exc:
        print("配置错误：", exc)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
