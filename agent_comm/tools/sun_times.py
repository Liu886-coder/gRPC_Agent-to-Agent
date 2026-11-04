"""独立 MCP 日出日落工具：返回城市当地时间和白昼时长，不从 LLM 常识估算。"""
from datetime import datetime, timezone

from .city import CityDataService, city_schema, demo_dates, result_header

SUN_URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = ["sunrise", "sunset", "daylight_duration"]


def format_sun_times(data):
    lines = [result_header(data, "日出日落")]
    if data.get("status") == "ambiguous":
        return lines[0]
    for day in data["daily"]:
        def clock(value):
            return value.split("T", 1)[-1] if value else "无事件/缺测"
        duration = day["daylight_duration"]
        daylight = f"{int(duration) // 3600}小时{int(duration) % 3600 // 60}分" if duration is not None else "缺测"
        lines.append(f"{day['date']} 日出 {clock(day['sunrise'])}，日落 {clock(day['sunset'])}，白昼 {daylight}。")
    lines.append("时间按查询城市当地时区；无事件或缺测不会自动填为00:00。")
    return "\n".join(lines)


class SunTimesService(CityDataService):
    max_days = 16
    label = "日出日落"

    async def _fetch(self, client, location, days, offset):
        response = await client.get(SUN_URL, params={
            "latitude": location["latitude"], "longitude": location["longitude"], "timezone": "auto",
            "forecast_days": days + offset, "daily": ",".join(FIELDS),
        })
        response.raise_for_status()
        body = response.json()
        daily = body.get("daily", {})
        dates = daily.get("time", [])
        if len(dates) < days + offset or any(len(daily.get(field, [])) != len(dates) for field in FIELDS):
            raise ValueError("日出日落接口返回日期或数据不完整，没有估算填补")
        rows = []
        for index in range(offset, offset + days):
            row = {field: daily[field][index] for field in FIELDS}
            row["date"] = dates[index]
            # null 保留为缺测/无事件；极地地区可能没有当日日出或日落。
            for key in ["sunrise", "sunset"]:
                if row[key] is not None and datetime.fromisoformat(row[key]).date().isoformat() != row["date"]:
                    raise ValueError("日出日落时间与返回日期不一致")
            rows.append(row)
        return {"status": "ok", "source": "open-meteo", "location": location, "timezone": body["timezone"],
                "daily": rows, "start_offset": offset, "queried_at_utc": datetime.now(timezone.utc).isoformat(),
                "data_url": str(response.request.url)}

    def _demo(self, city, days, offset):
        return {"status": "ok", "source": "mock", "location": {"name": city}, "timezone": "Asia/Shanghai",
                "daily": [{"date": date, "sunrise": date + "T06:00", "sunset": date + "T17:00",
                           "daylight_duration": 39600} for date in demo_dates(days, offset)], "start_offset": offset}


def register(registry, mode):
    """独立注册，查询日出/日落不再混进 get_weather 的参数和返回值。"""
    async def sun_times(args):
        return await SunTimesService(mode).query(**args)
    registry.add("get_sun_times", "查询城市日出时间、日落时间、天亮天黑、白昼时长 sunrise sunset daylight。"
                 "仅问日出日落时选本工具。city 为城市名，days 默认1，start_offset=0今天/1明天；"
                 "从当地今天起最多16天，时间按城市当地时区。", city_schema(16), sun_times)
