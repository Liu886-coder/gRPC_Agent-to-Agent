"""空气质量/日出日落：官方格式响应、RAG 选择、多工具模型回调和真实 MCP。"""
from dataclasses import replace
import json

import httpx
import pytest

from agent_comm.agents import AgentEngine
from agent_comm.config import Settings
from agent_comm.protocol import Message, TextPart, SendParams
from agent_comm.rag import Retriever
from agent_comm.storage import SQLiteStore
from agent_comm.tools import build_registry, WEATHER_TOOL_NAMES
from agent_comm.tools.air_quality import AirQualityService, format_air_quality, aqi_category
from agent_comm.tools.sun_times import SunTimesService, format_sun_times
from agent_comm.weather_agent import demo_tool_names


def geocoding_response():
    return {"results": [{"name": "宁波", "admin1": "浙江", "country": "中国", "country_code": "CN",
                         "latitude": 29.87, "longitude": 121.55}]}


def air_response():
    return {"timezone": "Asia/Shanghai", "current": {"time": "2030-01-01T12:00", "us_aqi": 80, "pm2_5": 14, "pm10": 30},
            "hourly": {"time": [f"2030-01-0{day}T{hour}:00" for day in [1, 2] for hour in ["00", "01", "12"]],
                       "us_aqi": [30, None, 80, 160, None, 140], "pm2_5": [10, None, 14, 35, None, 45],
                       "pm10": [20, None, 30, 50, None, 70]}}


def sun_response():
    return {"timezone": "Asia/Shanghai", "daily": {
        "time": ["2030-01-01", "2030-01-02", "2030-01-03"],
        "sunrise": ["2030-01-01T06:00", None, "2030-01-03T06:02"],
        "sunset": ["2030-01-01T17:00", None, "2030-01-03T16:58"],
        "daylight_duration": [39600, 0, 39360]}}


def test_air_forecast_does_not_also_select_weather_in_demo():
    assert demo_tool_names("宁波明天空气质量预报", []) == ["get_air_quality"]
    assert demo_tool_names("宁波明天的天气预报", []) == ["get_weather"]


async def test_air_quality_aggregates_local_hours_without_turning_missing_into_zero():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=geocoding_response())
        assert request.url.host == "air-quality-api.open-meteo.com"
        assert request.url.params["forecast_days"] == "2"
        assert request.url.params["timezone"] == "auto"
        assert request.url.params["hourly"] == "us_aqi,pm2_5,pm10"
        return httpx.Response(200, json=air_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await AirQualityService("live", client).query("宁波", days=1, start_offset=1)
    day = data["daily"][0]
    assert day["date"] == "2030-01-02" and day["us_aqi_max"] == 160
    assert day["pm2_5_mean"] == 40 and day["pm10_mean"] == 60
    assert day["aqi_valid_hours"] == 2 and day["available_hours"] == 3
    text = format_air_quality(data)
    assert "当前时段" not in text and "CAMS ENSEMBLE" in text
    assert "美国标准" in text and "不是中国官方实测" in text


async def test_air_quality_all_missing_is_not_good_air():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=geocoding_response())
        body = air_response()
        for field in ["us_aqi", "pm2_5", "pm10"]:
            body["hourly"][field] = [None] * 6
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await AirQualityService("live", client).query("宁波")
    assert data["daily"][0]["us_aqi_max"] is None
    assert data["daily"][0]["category"] == "缺测"
    assert "最高 US AQI 缺测" in format_air_quality(data)


@pytest.mark.parametrize("value,category", [(None, "缺测"), (50, "良好"), (80, "中等"), (160, "不健康"), (350, "危险")])
def test_us_aqi_boundaries(value, category):
    assert aqi_category(value) == category


async def test_sun_times_use_city_dates_and_preserve_no_event():
    def handler(request):
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json=geocoding_response())
        assert request.url.params["daily"] == "sunrise,sunset,daylight_duration"
        assert request.url.params["forecast_days"] == "3"
        assert request.url.params["timezone"] == "auto"
        return httpx.Response(200, json=sun_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await SunTimesService("live", client).query("宁波", days=2, start_offset=1)
    assert [day["date"] for day in data["daily"]] == ["2030-01-02", "2030-01-03"]
    text = format_sun_times(data)
    assert "无事件/缺测" in text and "06:02" in text and "10小时56分" in text
    assert "日出 00:00" not in text


@pytest.mark.parametrize("service", [AirQualityService, SunTimesService])
async def test_new_tools_preserve_city_ambiguity_and_network_errors(service):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [{"name": "A", "latitude": 1, "longitude": 1},
                                                     {"name": "A", "latitude": 2, "longitude": 2}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await service("live", client).query("A")
    assert data["status"] == "ambiguous" and len(requests) == 1
    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
        with pytest.raises(ValueError, match="没有返回模拟数据"):
            await service("live", client).query("宁波")


@pytest.mark.parametrize("service,body", [(AirQualityService, {"hourly": {"time": ["2030-01-01T00:00"]}}),
                                        (SunTimesService, {"daily": {"time": ["2030-01-01"]}})])
async def test_incomplete_provider_data_is_rejected(service, body):
    def handler(request):
        return httpx.Response(200, json=geocoding_response() if request.url.host == "geocoding-api.open-meteo.com" else body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="不完整"):
            await service("live", client).query("宁波")


async def test_sun_times_rejects_a_timestamp_from_wrong_date():
    def handler(request):
        body = sun_response()
        body["daily"]["sunrise"][0] = "2029-12-31T06:00"
        return httpx.Response(200, json=geocoding_response() if request.url.host == "geocoding-api.open-meteo.com" else body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="日期不一致"):
            await SunTimesService("live", client).query("宁波")


@pytest.mark.parametrize("service,days,offset", [(AirQualityService, 6, 0), (AirQualityService, 5, 1),
                                               (SunTimesService, 16, 1)])
async def test_new_tool_forecast_limits(service, days, offset):
    with pytest.raises(ValueError, match="天以内"):
        await service("mock").query("宁波", days=days, start_offset=offset)


async def test_rag_can_rank_each_new_tool_first():
    retriever = Retriever(Settings())
    try:
        tools = [tool for tool in build_registry().list_tools() if tool["name"] in WEATHER_TOOL_NAMES]
        await retriever.index_tools(tools)
        for query, expected in [("get_air_quality 空气质量 AQI PM2.5 PM10", "get_air_quality"),
                                ("get_sun_times 日出 日落 白昼 sunrise sunset", "get_sun_times")]:
            assert (await retriever.retrieve(query, top_k=1))[0]["name"] == expected
    finally:
        await retriever.close()


async def test_qwen_can_select_three_independent_tools_via_real_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_MODE", "mock")
    engine = AgentEngine("weather", replace(Settings(), llm_provider="qwen", qwen_api_key="test-only"),
                         SQLiteStore(str(tmp_path / "state.db")))
    seen = []
    names = ["get_weather", "get_air_quality", "get_sun_times"]
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert {tool["function"]["name"] for tool in body["tools"]} == set(names)
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"env-{index}", "type": "function", "function": {"name": name,
                 "arguments": '{"city":"宁波","days":1}'}} for index, name in enumerate(names)]}
        else:
            assert [message["tool_call_id"] for message in body["messages"][-3:]] == ["env-0", "env-1", "env-2"]
            message = {"role": "assistant", "content": "模型猜测AQI9999，日出00:00。"}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    try:
        await engine.start()
        await engine.llm.client.aclose()
        engine.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        reply = await engine.process(SendParams(message=Message(parts=[TextPart(text="宁波今天的天气、空气质量和日出日落")])))
        assert reply.metadata["tools_used"] == names
        assert reply.metadata["tool_source"] == "rag"
        assert "穿衣建议" in reply.text and "US AQI" in reply.text and "日出 06:00" in reply.text
        assert "9999" not in reply.text and "日出00:00" not in reply.text
        assert len(reply.metadata["tool_calls"]) == 3
    finally:
        await engine.close()
