"""验收当前配置：mock 离线运行，qwen 会请求真实模型和配置的 Embedding。"""
from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_comm.rpc_client import RpcClient
from agent_comm.config import Settings
from agent_comm.protocol import new_id


async def main():
    settings = Settings.from_env()
    async with RpcClient(settings.grpc_address) as client:
        assert (await client.health()).ready, "RPC 下游未就绪"
        result = await client.query("1+7", new_id("smoke"), timeout=settings.request_timeout)
        assert result.status.code == 0 and result.answer, "没有有效回答"
        math_tools = {"calculator", "add", "subtract", "multiply", "divide", "power", "sqrt", "factorial"}
        assert result.tools_used and set(result.tools_used) <= math_tools, "未通过 MCP 使用数学工具"
        status = await client.status(result.task_id)
        assert status.task_state == "completed", "任务状态不是 completed"
        events = [event async for event in client.stream("(1+7)*2", new_id("smoke"),
                                                       timeout=settings.request_timeout)]
        assert events[-1].event_type == "complete", "流式事件没有 complete"
        assert any(event.event_type == "partial" for event in events), "没有正文事件"
        weather = await client.query("宁波今天的天气、空气质量和日出日落，并给穿衣建议", new_id("weather-smoke"),
                                     timeout=settings.request_timeout)
        assert weather.agent_id == "weather-1" and set(weather.tools_used) == {
            "get_weather", "get_air_quality", "get_sun_times"}, "未通过天气 Agent 调用三个独立 MCP 工具"
        assert weather.answer, "没有天气回答"
        print("冒烟测试通过：gRPC -> A2A -> Math Agent -> MCP -> " + ",".join(result.tools_used))
        print("状态查询=completed；流式事件=status/partial/complete")
        print("示例回答:", result.answer)
        print("天气验收通过：A2A -> weather-1 -> MCP -> 天气/空气质量/日出日落三个独立工具")


if __name__ == "__main__":
    asyncio.run(main())

