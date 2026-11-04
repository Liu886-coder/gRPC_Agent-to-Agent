import pytest
from agent_comm.tools import build_registry
from agent_comm.tools.calculator import evaluate, extract_expression


@pytest.mark.parametrize("expression,expected", [
    ("1+7", 8), ("1+7*2", 15), ("(1+7)*2", 16), ("2^3^2", 512),
    ("sqrt(81)", 9), ("-2+3", 1), ("log10(100)", 2),
])
def test_calculator(expression, expected):
    assert evaluate(expression) == pytest.approx(expected)


@pytest.mark.parametrize("expression", [
    "__import__('os').getcwd()", "().__class__", "[1][0]", "True+1",
    "2**1000000", "1/0", "sqrt(-1)", "9" * 257,
])
def test_unsafe_or_invalid_expression_is_rejected(expression):
    with pytest.raises((ValueError, ArithmeticError)):
        evaluate(expression)


def test_natural_language_extraction():
    assert extract_expression("请帮我计算 (1+7)*2 等于多少？") == "(1+7)*2"


async def test_exact_tool_inventory():
    registry = build_registry()
    assert {row["name"] for row in registry.list_tools()} == {
        "calculator", "add", "subtract", "multiply", "divide", "power", "sqrt",
        "factorial", "get_weather", "get_air_quality", "get_sun_times", "sleep", "progress_test", "logging_test"}


@pytest.mark.parametrize("name,arguments", [
    ("divide", {"a": 1, "b": 0}), ("factorial", {"n": 21}),
    ("factorial", {"n": True}), ("calculator", {}),
    ("add", {"a": 1, "b": 2, "extra": 3}), ("missing", {}),
])
async def test_tool_errors_are_preserved(name, arguments):
    result = await build_registry().call(name, arguments)
    assert result["isError"] is True
    assert result["content"][0]["text"]


async def test_weather_demo_is_explicitly_labeled():
    result = await build_registry().call("get_weather", {"city": "上海", "latitude": 31.2, "longitude": 121.4})
    assert not result["isError"]
    assert "mock" in result["content"][0]["text"]

