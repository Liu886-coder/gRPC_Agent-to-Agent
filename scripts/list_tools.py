"""真实启动 MCP 子进程，列出工具，并可执行一个表达式。"""
from pathlib import Path
import argparse
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_comm.mcp_client import MCPClient, content_text


async def run(expression):
    async with MCPClient() as client:
        tools = await client.list_tools()
        print(f"MCP 工具总数: {len(tools)}")
        for index, tool in enumerate(tools, 1):
            print(f"{index:2}. {tool['name']}: {tool['description']}")
        if expression:
            result = await client.call_tool("calculator", {"expression": expression})
            print("\n调用结果:", content_text(result))
            if result["isError"]:
                raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expression")
    asyncio.run(run(parser.parse_args().expression))

