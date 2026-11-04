"""天气接口用真实格式的固定响应测试，不消耗真实天气 API 或千问配额。"""
from dataclasses import replace
from datetime import date, timedelta
import json

import httpx
import pytest

from agent_comm.agents import AgentEngine
from agent_comm.config import Settings
from agent_comm.llm import ModelClient
from agent_comm.protocol import Message, TextPart, SendParams, ProtocolError
from agent_comm.storage import SQLiteStore
from agent_comm.tools import build_registry
from agent_comm.tools.weather import WeatherService, DAILY_FIELDS, clothing_advice


def forecast_body():
    """响应起始日刻意固定，验证工具按服务端当地日期切片，而非拿电脑日期拼凑。"""
    daily = {"time": ["2030-01-01", "2030-01-02", "2030-01-03"]}
    for field in DAILY_FIELDS:
        daily[field] = {
            "weather_code": [0, 61, 3], "temperature_2m_max": [12, 10, 15],
            "temperature_2m_min": [2, 3, 4], "apparent_temperature_max": [10, 8, 14],
            "apparent_temperature_min": [0, 1, 3], "precipitation_probability_max": [10, 80, 30],
            "precipitation_sum": [0, 8, 0], "wind_speed_10m_max": [12, 35, 15], "uv_index_max": [2, 1, 3],
        }[field]
    return {"timezone": "Asia/Shanghai", "daily": daily, "daily_units": {"temperature_2m_max": "°C"}}


async def test_city_geocoding_local_dates_forecast_and_clothing():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            assert request.url.params["name"] == "宁波"
            assert request.url.params["countryCode"] == "CN"
            return httpx.Response(200, json={"results": [{"name": "宁波", "admin1": "浙江", "country": "中国",
                "country_code": "CN", "latitude": 29.87, "longitude": 121.55}]})
        assert request.url.params["timezone"] == "auto"
        assert request.url.params["forecast_days"] == "3"
        assert request.url.params["wind_speed_unit"] == "kmh"
        return httpx.Response(200, json=forecast_body())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await WeatherService("live", client).forecast("宁波市", days=2, start_offset=1, country_code="cn")
    assert len(requests) == 2
    assert data["source"] == "open-meteo"
    assert [day["date"] for day in data["daily"]] == ["2030-01-02", "2030-01-03"]
    assert data["daily"][0]["weather"] == "小雨"
    assert "带伞" in data["daily"][0]["clothing_advice"]
    assert "防风" in data["daily"][0]["clothing_advice"]


async def test_ambiguous_city_requests_confirmation_without_querying_weather():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [
            {"name": "Springfield", "admin1": "Illinois", "country": "美国", "latitude": 39.8, "longitude": -89.6},
            {"name": "Springfield", "admin1": "Missouri", "country": "美国", "latitude": 37.2, "longitude": -93.3},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await WeatherService("live", client).forecast("Springfield")
    assert data["status"] == "ambiguous" and len(data["candidates"]) == 2
    assert len(requests) == 1


async def test_municipality_does_not_confuse_shanghai_with_same_name_village():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [
                {"name": "上海", "admin1": "上海市", "country_code": "CN", "feature_code": "PPLA",
                 "latitude": 31.22, "longitude": 121.45},
                {"name": "上海", "admin1": "浙江", "country_code": "CN", "feature_code": "PPL",
                 "latitude": 29.33, "longitude": 121.05}]})
        assert request.url.params["latitude"] == "31.22"
        return httpx.Response(200, json=forecast_body())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await WeatherService("live", client).forecast("上海", days=1)
    assert data["status"] == "ok" and data["location"]["admin1"] == "上海市"


@pytest.mark.parametrize("kind", ["missing_city", "network", "incomplete"])
async def test_weather_failure_does_not_become_mock_or_fake_forecast(kind):
    def handler(request):
        if kind == "network":
            raise httpx.ConnectError("unreachable", request=request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={} if kind == "missing_city" else {"results": [
                {"name": "宁波", "latitude": 29.87, "longitude": 121.55}]})
        return httpx.Response(200, json={"timezone": "Asia/Shanghai", "daily": {"time": []}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await WeatherService("live", client).forecast("宁波")


@pytest.mark.parametrize("arguments", [
    {"city": "宁波", "days": 17}, {"city": "宁波", "days": 16, "start_offset": 1},
    {"city": "宁波", "latitude": 29.87}, {"city": " "},
])
async def test_weather_validates_range_and_coordinates(arguments):
    with pytest.raises(ValueError):
        await WeatherService("mock").forecast(**arguments)


def test_clothing_handles_freezing_rain_wind_and_missing_values():
    advice = clothing_advice({"temperature_2m_min": -5, "temperature_2m_max": 1,
                             "weather_code": 71, "wind_speed_10m_max": 40})
    assert "羽绒服" in advice and "防滑" in advice and "防风" in advice
    assert "不完整" in clothing_advice({"temperature_2m_min": None, "temperature_2m_max": None})
    assert "分层" in clothing_advice({"temperature_2m_min": 12.4, "temperature_2m_max": 20.8,
                                     "apparent_temperature_min": 12.3, "apparent_temperature_max": 20.0})


async def test_mock_forecast_is_marked_and_contains_requested_days():
    data = await WeatherService("mock").forecast("上海", days=3, start_offset=1)
    assert data["source"] == "mock" and "不代表真实" in data["note"]
    assert len(data["daily"]) == 3
    assert date.fromisoformat(data["daily"][-1]["date"]) - date.fromisoformat(data["daily"][0]["date"]) == timedelta(days=2)
    assert all(day["clothing_advice"] for day in data["daily"])


async def test_qwen_weather_agent_receives_weather_schema_and_runs_real_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_MODE", "mock")
    engine = AgentEngine("weather", replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"),
                         SQLiteStore(str(tmp_path / "state.db")))
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert "天气预报助手" in body["messages"][0]["content"]
        if len(seen) == 1:
            assert {tool["function"]["name"] for tool in body["tools"]} == {"get_weather", "get_air_quality", "get_sun_times"}
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "weather-1", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"宁波","days":3}'}}]}
        else:
            data = json.loads(json.loads(body["messages"][-1]["content"])["content"][0]["text"])
            assert len(data["daily"]) == 3 and data["daily"][0]["clothing_advice"]
            # 模型即使自由补写错误天气，最终展示也必须使用工具字段。
            message = {"role": "assistant", "content": "宁波1000℃，东北风，本周最高温。"}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    try:
        await engine.start()
        await engine.llm.client.aclose()
        engine.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reply = await engine.process(SendParams(message=Message(parts=[TextPart(text="宁波未来三天穿什么")])))
        assert reply.metadata["agent_id"] == "weather-1"
        assert reply.metadata["tools_used"] == ["get_weather"]
        assert reply.metadata["tool_source"] == "rag"
        assert reply.metadata["tool_calls"][0]["arguments"] == {"city": "宁波", "days": 3}
        assert "1000" not in reply.text and "东北风" not in reply.text
        assert "模拟天气" in reply.text and "穿衣建议" in reply.text and "22℃" in reply.text
    finally:
        await engine.close()


async def test_weather_agent_requires_tools_instead_of_hallucinating(tmp_path):
    engine = AgentEngine("weather", replace(Settings(), enable_mcp=False), SQLiteStore(str(tmp_path / "state.db")))
    try:
        with pytest.raises(ProtocolError) as error:
            await engine.process(SendParams(message=Message(parts=[TextPart(text="宁波天气")])))
        assert error.value.code == -32003
    finally:
        await engine.close()


async def test_classification_uses_weather_context_for_follow_up():
    model = ModelClient(Settings())
    try:
        assert await model.classify("宁波未来3天天气") == "weather"
        history = [{"role": "agent", "parts": [{"text": "宁波预报"}], "metadata": {"intent": "weather"}}]
        assert await model.classify("那上海呢", history) == "weather"
        assert await model.classify("1+7", history) == "math"
        assert await model.classify("你好", history) == "general"
    finally:
        await model.close()
