"""工具扩展点：新增模块并在 build_registry() 中注册。"""
from .base import ToolRegistry
from .calculator import register as register_math
from .system import register as register_system
from .weather import register as register_weather
from .weather import format_forecast
from .air_quality import register as register_air_quality, format_air_quality
from .sun_times import register as register_sun_times, format_sun_times

# 天气 Agent 的独立工具范围；同一集合用于加载、执行校验和结果展示。
ENVIRONMENT_FORMATTERS = {"get_weather": format_forecast, "get_air_quality": format_air_quality,
                          "get_sun_times": format_sun_times}
WEATHER_TOOL_NAMES = frozenset(ENVIRONMENT_FORMATTERS)


def build_registry(weather_mode="mock"):
    registry = ToolRegistry()
    register_math(registry)
    register_system(registry)
    register_weather(registry, weather_mode)
    register_air_quality(registry, weather_mode)
    register_sun_times(registry, weather_mode)
    return registry

