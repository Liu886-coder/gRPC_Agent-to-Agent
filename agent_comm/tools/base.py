from dataclasses import dataclass
from typing import Awaitable, Callable
import json

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Awaitable[str | dict]]

    def definition(self):
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def add(self, name, description, schema, handler):
        if name in self.tools:
            raise ValueError(f"重复工具名称: {name}")
        Draft202012Validator.check_schema(schema)
        self.tools[name] = Tool(name, description, schema, handler)

    def list_tools(self):
        return [tool.definition() for tool in self.tools.values()]

    async def call(self, name, arguments):
        if name not in self.tools:
            return {"isError": True, "content": [{"type": "text", "text": f"未知工具: {name}"}]}
        tool = self.tools[name]
        try:
            Draft202012Validator(tool.schema).validate(arguments)
            result = await tool.handler(arguments)
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return {"isError": False, "content": [{"type": "text", "text": text}]}
        except (ValidationError, ValueError, ArithmeticError, TypeError, KeyError) as exc:
            message = exc.message if isinstance(exc, ValidationError) else str(exc)
            return {"isError": True, "content": [{"type": "text", "text": message[:500]}]}
        except Exception as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"工具执行失败: {type(exc).__name__}"}]}


def object_schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}

