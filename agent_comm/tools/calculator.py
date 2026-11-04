"""白名单 AST 求值：不用 eval，不允许属性访问、导入、索引或任意函数执行。"""
import ast
import math
import operator
import re

from .base import object_schema

FUNCTIONS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
             "tan": math.tan, "abs": abs, "log": math.log, "log10": math.log10}
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
             ast.Div: operator.truediv, ast.Mod: operator.mod}


def extract_expression(question: str) -> str:
    text = question.strip().replace("×", "*").replace("÷", "/").replace("－", "-")
    text = re.sub(r"^(?:请|帮我|计算一下|计算|算一下|算|求)\s*", "", text)
    # 可出现“请帮我计算”，反复移除明确前缀而非任意删除中间文本。
    for _ in range(4):
        newer = re.sub(r"^(?:请|帮我|计算一下|计算|算一下|算|求)\s*", "", text)
        if newer == text:
            break
        text = newer
    return re.sub(r"\s*(?:等于多少|是多少|等于|=)?[？?。！!]*$", "", text).strip()


def evaluate(expression: str) -> float:
    if len(expression) > 256:
        raise ValueError("表达式最多 256 个字符")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("请输入有效数学表达式，例如 (1+7)*2") from exc
    if len(list(ast.walk(tree))) > 100:
        raise ValueError("表达式过于复杂")

    def visit(node, depth=0):
        if depth > 20:
            raise ValueError("表达式嵌套过深")
        if isinstance(node, ast.Expression):
            value = visit(node.body, depth + 1)
        elif isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.Name) and node.id in {"pi", "e"}:
            value = {"pi": math.pi, "e": math.e}[node.id]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            if isinstance(node.op, ast.USub):
                value = -value
        elif isinstance(node, ast.BinOp):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 12:
                    raise ValueError("幂指数绝对值不能超过 12")
                value = left ** right
            elif type(node.op) in OPERATORS:
                value = OPERATORS[type(node.op)](left, right)
            else:
                raise ValueError("不支持该运算符")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords):
            value = FUNCTIONS[node.func.id](visit(node.args[0], depth + 1))
        else:
            raise ValueError("只允许数字、数学运算、括号和白名单数学函数")
        if isinstance(value, complex) or not math.isfinite(value) or abs(value) > 1e100:
            raise ValueError("计算结果超出支持范围")
        return value
    return visit(tree)


def display(value):
    return format(value, ".15g")


def register(registry):
    async def calculator(args):
        expression = args["expression"]
        return f"{expression} = {display(evaluate(expression))}"
    registry.add("calculator", "数学表达式 calculator arithmetic math 计算 加减乘除 括号 幂",
                 object_schema({"expression": {"type": "string", "minLength": 1, "maxLength": 256}}), calculator)

    # 注意：这里的四则运算是用循环注册
    for name, symbol, operation in [
        ("add", "+", operator.add), ("subtract", "-", operator.sub),
        ("multiply", "*", operator.mul), ("divide", "/", operator.truediv),
    ]:
        async def binary(args, op=operation, sign=symbol):
            value = op(args["a"], args["b"])
            if not math.isfinite(value) or abs(value) > 1e100:
                raise ValueError("结果超出范围")
            return f"{display(args['a'])} {sign} {display(args['b'])} = {display(value)}"
        registry.add(name, f"数学 math {name} 两个数的 {symbol} 运算",
                     object_schema({"a": {"type": "number", "minimum": -1e12, "maximum": 1e12},
                                    "b": {"type": "number", "minimum": -1e12, "maximum": 1e12}}), binary)

    async def power(args):
        return display(evaluate(f"({args['base']})**({args['exponent']})"))
    registry.add("power", "数学 幂运算 power",
                 object_schema({"base": {"type": "number", "minimum": -1e12, "maximum": 1e12},
                                "exponent": {"type": "number", "minimum": -12, "maximum": 12}}), power)

    async def square_root(args):
        return display(math.sqrt(args["number"]))
    registry.add("sqrt", "数学 平方根 square root",
                 object_schema({"number": {"type": "number", "minimum": 0, "maximum": 1e12}}), square_root)

    async def factorial(args):
        return str(math.factorial(args["n"]))
    registry.add("factorial", "数学 阶乘 factorial",
                 object_schema({"n": {"type": "integer", "minimum": 0, "maximum": 20}}), factorial)

