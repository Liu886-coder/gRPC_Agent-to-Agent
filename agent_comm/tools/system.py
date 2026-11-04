import asyncio
from .base import object_schema


def register(registry):
    async def sleep(args):
        await asyncio.sleep(args["milliseconds"] / 1000)
        return f"Waited for {args['milliseconds']} milliseconds"
    registry.add("sleep", "等待 wait sleep 指定毫秒，用于测试超时",
                 object_schema({"milliseconds": {"type": "integer", "minimum": 0, "maximum": 5000}}), sleep)

    async def progress(args):
        await asyncio.sleep(0.01)
        return {"progress": 100, "total": 100, "message": "进度演示完成"}
    registry.add("progress_test", "测试任务进度 progress notification", object_schema({}), progress)

    async def logging(args):
        return "日志通知测试完成"
    registry.add("logging_test", "测试日志 logging notification", object_schema({}), logging)

