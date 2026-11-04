"""城市查询公共代码：三个环境工具共用定位、日期范围和网络错误处理。"""
from datetime import datetime, timedelta, timezone
import re

import httpx

from .base import object_schema

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


async def resolve_location(client, city, country_code="", latitude=None, longitude=None):
    """城市名转坐标；直辖市与同名村镇区分，真正重名时返回候选让用户确认。"""
    if latitude is not None:
        return {"name": city, "latitude": latitude, "longitude": longitude}
    search = re.sub(r"市(?=,|，|$)", "", city).replace("，", ",")
    params = {"name": search, "count": 5, "language": "zh", "format": "json"}
    if country_code:
        params["countryCode"] = country_code.upper()
    response = await client.get(GEOCODING_URL, params=params)
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        raise ValueError(f"未找到城市“{city}”，请补充省份/国家或尝试城市的英文名称")
    unique = {(round(row["latitude"], 3), round(row["longitude"], 3)): row for row in results}
    bare_city = re.sub(r"市$", "", search.split(",")[0].strip())
    municipality = [row for row in unique.values() if row.get("country_code") == "CN"
                    and re.sub(r"市$", "", row.get("admin1", "")) == bare_city
                    and row.get("feature_code") in {"PPLC", "PPLA", "ADM1"}]
    if len(municipality) == 1:
        row = municipality[0]
        unique = {(round(row["latitude"], 3), round(row["longitude"], 3)): row}
    fields = ["name", "admin1", "country", "country_code", "latitude", "longitude"]
    if len(unique) > 1:
        return {"status": "ambiguous", "source": "open-meteo", "query": city,
                "candidates": [{key: row.get(key) for key in fields} for row in unique.values()]}
    return {key: next(iter(unique.values())).get(key) for key in fields}


def city_schema(max_days):
    """新工具各自注册自己的 Schema，只有城市必填，日期偏移按当地今天计算。"""
    return object_schema({
        "city": {"type": "string", "minLength": 1, "maxLength": 120},
        "days": {"type": "integer", "minimum": 1, "maximum": max_days, "default": 1},
        "start_offset": {"type": "integer", "minimum": 0, "maximum": max_days - 1, "default": 0},
        "country_code": {"type": "string", "pattern": "^[A-Za-z]{2}$"},
    }, required=["city"])


def demo_dates(days, offset):
    today = datetime.now(timezone(timedelta(hours=8))).date()
    return [(today + timedelta(days=index)).isoformat() for index in range(offset, offset + days)]


def result_header(data, title):
    """统一展示城市、时区、来源；模拟数据与城市歧义不会伪装成真实结果。"""
    if data.get("status") == "ambiguous":
        places = "、".join(" / ".join(filter(None, [row.get("name"), row.get("admin1"), row.get("country")]))
                           for row in data["candidates"])
        return f"找到多个同名地点：{places}。请补充省份或国家。"
    place = data["location"]
    name = " / ".join(filter(None, [place.get("name"), place.get("admin1"), place.get("country")]))
    prefix = "[模拟数据，不代表真实情况] " if data["source"] == "mock" else ""
    return f"{prefix}{name} · {title}；时区：{data['timezone']}；来源：{data['source']}。"


class CityDataService:
    """空气质量和日出日落的数据服务基类；模拟模式完全离线，真实失败不降级为模拟。"""
    max_days = 16
    label = "城市数据"

    def __init__(self, mode="live", client=None):
        self.mode, self.client = mode, client

    async def query(self, city, days=1, start_offset=0, country_code=""):
        city = city.strip()
        if not city:
            raise ValueError("请提供城市名称")
        if not 1 <= days <= self.max_days or not 0 <= start_offset < self.max_days or days + start_offset > self.max_days:
            raise ValueError(f"{self.label}仅支持从当地今天起 {self.max_days} 天以内")
        if self.mode == "mock":
            return self._demo(city, days, start_offset)
        try:
            if self.client is not None:
                return await self._query(self.client, city, days, start_offset, country_code)
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                return await self._query(client, city, days, start_offset, country_code)
        except httpx.TimeoutException as exc:
            raise ValueError(f"{self.label}服务请求超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise ValueError(f"无法访问{self.label}或城市定位接口，没有返回模拟数据") from exc

    async def _query(self, client, city, days, offset, country_code):
        location = await resolve_location(client, city, country_code)
        if location.get("status") == "ambiguous":
            return location
        return await self._fetch(client, location, days, offset)
