"""独立验证天气、空气质量或日出日落 MCP 工具，无需启动 Agent 或调用千问。"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_comm.config import Settings
from agent_comm.mcp_client import MCPClient, content_text
from agent_comm.tools import ENVIRONMENT_FORMATTERS


async def main(args):
    settings = Settings.from_env()
    print(f"天气模式={settings.weather_mode}；通过真实 MCP 子进程查询。")
    async with MCPClient(timeout=settings.request_timeout) as client:
        result = await client.call_tool(args.tool, {"city": args.city, "days": args.days,
                                                  "start_offset": args.start_offset})
    if result.get("isError"):
        print("查询失败：", content_text(result))
        return 1
    data = json.loads(content_text(result))
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else ENVIRONMENT_FORMATTERS[args.tool](data))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="独立检查天气、空气质量或日出日落 MCP 工具")
    parser.add_argument("--city", default="宁波")
    parser.add_argument("--tool", choices=list(ENVIRONMENT_FORMATTERS), default="get_weather",
                        help="选择独立的天气、空气质量或日出日落工具")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
