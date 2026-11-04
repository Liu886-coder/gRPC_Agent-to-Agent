"""天气 MCP 工具：城市定位 → Open-Meteo 逐日预报 → 可追溯的穿衣建议。"""
from datetime import datetime, timedelta, timezone

import httpx

from .base import object_schema
from .city import resolve_location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_FIELDS = ["weather_code", "temperature_2m_max", "temperature_2m_min",
                "apparent_temperature_max", "apparent_temperature_min",
                "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max", "uv_index_max"]
WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 56: "轻冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "轻冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨", 85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴冰雹",
}


def clothing_advice(day):
    """按体感温度、温差、降水和风力生成建议；缺测值不当成零。"""
    low = day.get("apparent_temperature_min")
    high = day.get("apparent_temperature_max")
    if low is None:
        low = day.get("temperature_2m_min")
    if high is None:
        high = day.get("temperature_2m_max")
    advice = []
    if low is None or high is None:
        advice.append("温度数据不完整，出门前请再确认当地温度")
    else:
        average = (low + high) / 2
        if high <= 5:
            advice.append("穿羽绒服或厚棉服，搭配保暖内层和长裤")
        elif average < 12:
            advice.append("穿厚外套或大衣，内搭毛衣和长裤")
        elif average < 18:
            advice.append("穿长袖搭配外套和长裤")
        elif average < 25:
            advice.append("穿轻薄长袖或短袖，早晚备一件薄外套")
        else:
            advice.append("穿透气短袖等轻薄衣物，注意补水")
        air_low, air_high = day.get("temperature_2m_min"), day.get("temperature_2m_max")
        # 穿衣厚度优先参考体感；温差提醒同时考虑实际气温，避免漏掉早晚温差。
        if high - low >= 8 or (air_low is not None and air_high is not None and air_high - air_low >= 8):
            advice.append("早晚温差较大，采用方便增减的分层穿法")
        if low <= 5 < high:
            advice.append("早晚偏冷，外出加保暖层")
    probability = day.get("precipitation_probability_max")
    if (probability is not None and probability >= 50) or day.get("weather_code") in {
        51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99
    }:
        advice.append("带伞或雨衣，选择防滑且不易进水的鞋")
    if (day.get("wind_speed_10m_max") or 0) >= 30:
        advice.append("风较大，选择防风外套")
    if (day.get("uv_index_max") or 0) >= 6:
        advice.append("紫外线较强，备遮阳帽和防晒衣")
    return "；".join(advice) + "。"


def format_forecast(data):
    """确定性展示真实字段和建议，禁止把模型补写的风向、预警或绝对无雨当成事实。"""
    if data.get("status") == "ambiguous":
        places = "、".join(" / ".join(filter(None, [row.get("name"), row.get("admin1"), row.get("country")]))
                           for row in data["candidates"])
        return f"找到多个同名地点：{places}。请补充省份或国家，例如“城市, 省份”。"
    location = data["location"]
    name = " / ".join(filter(None, [location.get("name"), location.get("admin1"), location.get("country")]))
    prefix = "[模拟天气，不代表真实天气] " if data["source"] == "mock" else ""
    lines = [f"{prefix}{name}；时区：{data['timezone']}；来源：{data['source']}。"]
    current = data.get("current", {})
    if current and data.get("start_offset", 0) == 0:
        # 明天/后天查询不把今天的实况当作未来数据展示。
        current_text = [f"当前时段 {current.get('time', '时间缺测')}："
                        + WEATHER_CODES.get(current.get("weather_code"), "天气状况缺测")]
        if current.get("temperature_2m") is not None:
            current_text.append(f"气温 {current['temperature_2m']}℃")
        if current.get("apparent_temperature") is not None:
            current_text.append(f"体感 {current['apparent_temperature']}℃")
        lines.append("，".join(current_text) + "。")
    for day in data["daily"]:
        low, high = day.get("temperature_2m_min"), day.get("temperature_2m_max")
        probability = day.get("precipitation_probability_max")
        wind = day.get("wind_speed_10m_max")
        lines.append(f"{day['date']} 全天预报：{day['weather']}，{low if low is not None else '缺测'}～"
                     f"{high if high is not None else '缺测'}℃，降水概率"
                     f"{probability if probability is not None else '缺测'}%，最大风速"
                     f"{wind if wind is not None else '缺测'} km/h。\n穿衣建议：{day['clothing_advice']}")
    if data["source"] != "mock":
        lines.append("日期按城市当地时区；全天预报与当前时段可能不同，降水概率不代表确定会下雨或绝对无雨。")
    return "\n".join(lines)


class WeatherService:
    """只负责天气数据，不负责 Agent 路由；client 可注入以测试外部接口。"""

    def __init__(self, mode="live", client=None):
        self.mode, self.client = mode, client

    async def forecast(self, city, days=3, start_offset=0, country_code="", latitude=None, longitude=None):
        """days 为返回天数；offset=0 为当地今天，1 为明天，总范围不超过 16 天。"""
        city = city.strip()
        if not city:
            raise ValueError("请提供城市名称，例如宁波、北京或 Paris, France")
        if not 1 <= days <= 16 or not 0 <= start_offset <= 15 or days + start_offset > 16:
            raise ValueError("预报仅支持从当地今天起 16 天以内，请缩短天数或日期范围")
        if (latitude is None) != (longitude is None):
            raise ValueError("经纬度必须同时提供；通常只提供城市即可自动定位")
        if self.mode == "mock":
            return self._demo(city, days, start_offset)
        try:
            if self.client is not None:
                return await self._live(self.client, city, days, start_offset, country_code, latitude, longitude)
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                return await self._live(client, city, days, start_offset, country_code, latitude, longitude)
        except httpx.TimeoutException as exc:
            raise ValueError("天气或城市定位服务请求超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise ValueError("无法访问 Open-Meteo 天气/城市定位服务，请检查网络；没有返回模拟天气") from exc

    async def _live(self, client, city, days, offset, country_code, latitude, longitude):
        """先用城市定位 API 获取坐标，再用城市当地时区查询每日天气。"""
        location = await resolve_location(client, city, country_code, latitude, longitude)
        if location.get("status") == "ambiguous":
            return location
        response = await client.get(FORECAST_URL, params={
            "latitude": location["latitude"], "longitude": location["longitude"],
            "timezone": "auto", "forecast_days": days + offset,
            "temperature_unit": "celsius", "wind_speed_unit": "kmh",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": ",".join(DAILY_FIELDS),
        })
        response.raise_for_status()
        body = response.json()
        daily = body.get("daily", {})
        dates = daily.get("time", [])
        if len(dates) < days + offset or any(len(daily.get(key, [])) != len(dates) for key in DAILY_FIELDS):
            raise ValueError("天气服务返回的逐日预报不完整，未生成虚构预报")
        rows = []
        for index in range(offset, offset + days):
            row = {key: daily[key][index] for key in DAILY_FIELDS}
            row.update(date=dates[index], weather=WEATHER_CODES.get(row["weather_code"], "天气状况暂无说明"))
            row["clothing_advice"] = clothing_advice(row)
            rows.append(row)
        return {"status": "ok", "source": "open-meteo", "city": city, "location": location,
                "timezone": body["timezone"], "queried_at_utc": datetime.now(timezone.utc).isoformat(),
                "current": body.get("current", {}), "current_units": body.get("current_units", {}),
                "daily_units": body.get("daily_units", {}), "daily": rows,
                "forecast_url": str(response.request.url), "start_offset": offset,
                "note": "日期按城市当地时区；current 为当前时段，daily 天气代码为全天最显著天气，两者可能不同。"
                        "天气为预报，穿衣建议按体感温度、温差、降水、风和紫外线生成。"}

    def _demo(self, city, days, offset):
        """离线测试专用的固定演示数据，始终保留醒目的 mock 标记。"""
        today = datetime.now(timezone(timedelta(hours=8))).date()
        rows = []
        for index in range(offset, offset + days):
            row = {"date": (today + timedelta(days=index)).isoformat(), "weather_code": 61 if index % 2 else 2,
                   "temperature_2m_min": 12, "temperature_2m_max": 22,
                   "apparent_temperature_min": 10, "apparent_temperature_max": 21,
                   "precipitation_probability_max": 70 if index % 2 else 20,
                   "precipitation_sum": 3 if index % 2 else 0, "wind_speed_10m_max": 15, "uv_index_max": 3}
            row["weather"] = WEATHER_CODES[row["weather_code"]]
            row["clothing_advice"] = clothing_advice(row)
            rows.append(row)
        return {"status": "ok", "source": "mock", "city": city, "location": {"name": city},
                "timezone": "Asia/Shanghai", "daily": rows, "start_offset": offset,
                "note": "这是离线演示天气，不代表真实天气"}


def register(registry, mode):
    """保留 get_weather 名称，升级为可直接传城市名的多日预报工具。"""
    async def weather(args):
        return await WeatherService(mode).forecast(**args)

    registry.add("get_weather", "查询不同城市当天天气、未来多日预报、气温、降水、风及穿衣建议 weather forecast clothing。"
                 "city 填城市名，重名可写城市,省份或国家；不要猜经纬度。days 是天数（1~16），"
                 "start_offset=0 从当地今天开始、1 从明天开始、2 从后天开始；总范围不超过16天。",
                 object_schema({
                     "city": {"type": "string", "minLength": 1, "maxLength": 120},
                     "days": {"type": "integer", "minimum": 1, "maximum": 16, "default": 3},
                     "start_offset": {"type": "integer", "minimum": 0, "maximum": 15, "default": 0},
                     "country_code": {"type": "string", "pattern": "^[A-Za-z]{2}$"},
                     "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                     "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                 }, required=["city"]), weather)

