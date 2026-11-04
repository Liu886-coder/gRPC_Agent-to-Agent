"""独立 MCP 空气质量工具；数据来自 Open-Meteo/CAMS，US AQI 不冒充中国 AQI。"""
from datetime import datetime, timezone

from .city import CityDataService, city_schema, demo_dates, result_header

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FIELDS = ["us_aqi", "pm2_5", "pm10"]


def aqi_category(value):
    """按接口文档的美国 AQI 区间标注等级；缺测不按 0（良好）处理。"""
    if value is None:
        return "缺测"
    for limit, name in [(50, "良好"), (100, "中等"), (150, "对敏感人群不健康"),
                        (200, "不健康"), (300, "非常不健康")]:
        if value <= limit:
            return name
    return "危险"


def format_air_quality(data):
    lines = [result_header(data, "空气质量")]
    if data.get("status") == "ambiguous":
        return lines[0]
    current = data.get("current", {})
    if current and data.get("start_offset", 0) == 0:
        value = current.get("us_aqi")
        lines.append(f"当前时段 {current.get('time', '时间缺测')}：US AQI {value if value is not None else '缺测'}"
                     f"（{aqi_category(value)}），PM2.5 {current.get('pm2_5') if current.get('pm2_5') is not None else '缺测'}"
                     f" μg/m³，PM10 {current.get('pm10') if current.get('pm10') is not None else '缺测'} μg/m³。")
    for day in data["daily"]:
        def show(key):
            return day[key] if day[key] is not None else "缺测"
        lines.append(f"{day['date']} 小时预报汇总：最高 US AQI {show('us_aqi_max')}（{day['category']}），"
                     f"PM2.5 均值 {show('pm2_5_mean')} μg/m³，PM10 均值 {show('pm10_mean')} μg/m³；"
                     f"有效 AQI 时段 {day['aqi_valid_hours']}/{day['available_hours']}。")
    lines.append("来源：Open-Meteo / CAMS ENSEMBLE（Copernicus 大气监测）。这是模型估算/预报，"
                 "US AQI 为美国标准，不是中国官方实测 AQI；缺测未补零。")
    return "\n".join(lines)


class AirQualityService(CityDataService):
    # 全球 CAMS 预报按稳定可用的 5 天范围提供，避免把更远的缺测天数当成有效预报。
    max_days = 5
    label = "空气质量"

    async def _fetch(self, client, location, days, offset):
        response = await client.get(AIR_QUALITY_URL, params={
            "latitude": location["latitude"], "longitude": location["longitude"], "timezone": "auto",
            "forecast_days": days + offset, "hourly": ",".join(FIELDS), "current": ",".join(FIELDS),
        })
        response.raise_for_status()
        body = response.json()
        hourly = body.get("hourly", {})
        times = hourly.get("time", [])
        if not times or any(len(hourly.get(field, [])) != len(times) for field in FIELDS):
            raise ValueError("空气质量接口返回的小时数据不完整，没有生成虚构 AQI")
        grouped = {}
        for index, timestamp in enumerate(times):
            date = datetime.fromisoformat(timestamp).date().isoformat()
            grouped.setdefault(date, []).append(index)
        dates = sorted(grouped)
        if len(dates) < days + offset:
            raise ValueError("空气质量可用预报日期不足，请缩短查询范围")
        rows = []
        for date in dates[offset:offset + days]:
            values = {field: [hourly[field][index] for index in grouped[date] if hourly[field][index] is not None]
                      for field in FIELDS}
            maximum = max(values["us_aqi"]) if values["us_aqi"] else None
            rows.append({"date": date, "us_aqi_max": maximum, "category": aqi_category(maximum),
                         "pm2_5_mean": round(sum(values["pm2_5"]) / len(values["pm2_5"]), 1) if values["pm2_5"] else None,
                         "pm10_mean": round(sum(values["pm10"]) / len(values["pm10"]), 1) if values["pm10"] else None,
                         "aqi_valid_hours": len(values["us_aqi"]), "available_hours": len(grouped[date])})
        return {"status": "ok", "source": "open-meteo-cams", "location": location, "timezone": body["timezone"],
                "current": body.get("current", {}), "daily": rows, "start_offset": offset,
                "queried_at_utc": datetime.now(timezone.utc).isoformat(), "data_url": str(response.request.url)}

    def _demo(self, city, days, offset):
        rows = [{"date": date, "us_aqi_max": 65, "category": aqi_category(65), "pm2_5_mean": 14.2,
                 "pm10_mean": 25.4, "aqi_valid_hours": 24, "available_hours": 24} for date in demo_dates(days, offset)]
        return {"status": "ok", "source": "mock", "location": {"name": city}, "timezone": "Asia/Shanghai",
                "current": {}, "daily": rows, "start_offset": offset}


def register(registry, mode):
    """独立注册，RAG 和模型可仅选择空气质量，不必同时查询天气。"""
    async def air_quality(args):
        return await AirQualityService(mode).query(**args)
    registry.add("get_air_quality", "查询城市空气质量、US AQI、PM2.5、PM10、污染程度 air quality pollution。"
                 "仅查询空气时选本工具。city 为城市名，days 默认1，start_offset=0今天/1明天；"
                 "从当地今天起最多5天，US AQI 为美国标准，不是中国官方AQI。",
                 city_schema(5), air_quality)
