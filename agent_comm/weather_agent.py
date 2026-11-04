"""天气 Agent 的业务核心；A2A、任务和心跳仍由 agents.py 的共用框架负责。"""
import json
import logging
import re

from .mcp_client import content_text
from .protocol import ProtocolError
from .tools import WEATHER_TOOL_NAMES, ENVIRONMENT_FORMATTERS

log = logging.getLogger(__name__)


def demo_arguments(question, history):
    """仅供 mock 测试的规则提取；真实 qwen 模式由模型理解城市、日期和天数。"""
    city = next((name for name in ["宁波", "北京", "上海", "杭州", "广州", "深圳", "成都", "武汉", "南京", "西安"]
                 if name in question), "")
    if not city:
        # 多轮“那明天呢”复用上一条天气工具请求中的城市，不凭空猜测。
        for message in reversed(history):
            calls = message.get("metadata", {}).get("tool_calls", [])
            previous = next((call["arguments"]["city"] for call in reversed(calls)
                             if call["name"] in WEATHER_TOOL_NAMES), "")
            if previous:
                city = previous
                break
    if not city:
        match = re.search(r"([\u4e00-\u9fff]{2,8})市", question)
        if match:
            city = re.sub(r"^(?:请|查询|今天|明天|后天|那)+", "", match[1])
    numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "十": 10}
    match = re.search(r"(?:未来|接下来|往后)\s*(\d+|[一二两三四五六七十])\s*天", question)
    days = (int(match[1]) if match[1].isdigit() else numbers[match[1]]) if match else (3 if "几天" in question else 1)
    offset = 2 if "后天" in question else (1 if "明天" in question else 0)
    return {"city": city, "days": days, "start_offset": offset} if city else None


def demo_tool_names(question, history):
    """离线演示也区分三个工具，组合问题可调用多个；真实模式由千问自主选择。"""
    names = []
    if re.search(r"天气|气温|降雨|下雨|下雪|降水|穿衣|穿什么|怎么穿|weather", question, re.I):
        names.append("get_weather")
    if re.search(r"空气|污染|AQI|PM\s*2[._]?5|PM\s*10|air quality", question, re.I):
        names.append("get_air_quality")
    if re.search(r"日出|日落|天亮|天黑|白昼|昼长|sunrise|sunset", question, re.I):
        names.append("get_sun_times")
    # “空气质量预报”仍只查询空气；泛指预报且未指向其他工具时才按天气处理。
    if not names and re.search(r"预报|forecast", question, re.I):
        return ["get_weather"]
    if not names:
        for message in reversed(history):
            previous = [call["name"] for call in message.get("metadata", {}).get("tool_calls", [])
                        if call["name"] in WEATHER_TOOL_NAMES]
            if previous:
                return list(dict.fromkeys(previous))
    return names or ["get_weather"]


class WeatherAgent:
    """天气业务：RAG 限定候选 → 千问生成工具参数 → MCP 查询 → 解释天气与穿衣建议。"""

    def __init__(self, engine):
        self.engine = engine

    async def answer(self, params, history):
        engine = self.engine
        if not engine.mcp:
            raise ProtocolError("天气 Agent 的 MCP 不可用，请检查 ENABLE_MCP 和 weather.log", -32003)
        candidates, warning, source = [], engine.warning, "all_tools"
        available = engine.tools
        if engine.retriever:
            try:
                candidates = await engine.retriever.retrieve(params.message.text)
                available, source = candidates, "rag"
            except Exception as exc:
                warning = f"天气工具检索失败，使用天气工具清单: {type(exc).__name__}"
        log.info("weather candidates source=%s names=%s", source, [tool["name"] for tool in available])
        results = []

        async def execute(name, arguments):
            # 业务层再限制一次，天气 Agent 不执行计算器、sleep 等其他工具。
            if name not in WEATHER_TOOL_NAMES:
                raise ProtocolError("天气 Agent 只允许调用天气、空气质量和日出日落工具", -32004)
            log.info("weather MCP call name=%s arguments=%s", name, arguments)
            result = await engine.mcp.call_tool(name, arguments)
            if result.get("isError"):
                raise ProtocolError("天气查询失败: " + content_text(result), -32004)
            results.append((name, json.loads(content_text(result))))
            return result

        if engine.settings.llm_provider == "qwen":
            answer, calls = await engine.llm.answer_with_tools(
                params.message.text, history, available, execute, agent_kind="weather")
        else:
            # 离线模式也经过真实 MCP，只替换模型决策和天气数据，保留模拟标签。
            arguments = demo_arguments(params.message.text, history)
            calls = []
            if arguments and available:
                allowed = {tool["name"] for tool in available}
                for name in demo_tool_names(params.message.text, history):
                    if name in allowed:
                        await execute(name, arguments)
                        calls.append({"name": name, "arguments": arguments})
            else:
                answer = "请告诉我城市和查询范围，例如“宁波未来三天天气，怎么穿衣”。"
        if not calls:
            # 没有实际查询时，不能把模型凭常识生成的温度当成天气预报。
            answer = ("没有检索到可用天气工具，请检查 RAG_THRESHOLD 或天气服务。" if not available else
                      "请提供要查询的城市和日期范围，例如“宁波今天的天气和穿衣建议”。")
        else:
            # 千问负责理解城市/日期和决策；最终事实及穿衣建议以工具结构化数据为准。
            # 不直接展示模型自由生成的天气段落，防止补写未查询的风向、雷电或逐小时信息。
            answer = "\n\n".join(ENVIRONMENT_FORMATTERS[name](result) for name, result in results)
        return answer, {"agent_id": engine.agent_id, "tools_used": list(dict.fromkeys(call["name"] for call in calls)),
                        "tool_calls": calls, "tool_selection": "qwen_function_calling" if engine.settings.llm_provider == "qwen" else "mock_rules",
                        "tool_source": source, "rag_candidates": [{"name": tool["name"], "score": tool["score"]} for tool in candidates],
                        "weather_mode": engine.settings.weather_mode,
                        "weather_source": results[-1][1].get("source", "") if results else "", "warning": warning}
