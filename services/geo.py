"""地址定位與行政區、地號查詢（都是免金鑰的公開服務）。

- 地址轉座標：ArcGIS World Geocoding（findAddressCandidates，不儲存結果的查詢不需金鑰）
- 行政區、地段：內政部國土測繪中心開放 API（api.nlsc.gov.tw）
- 周邊設施：OpenStreetMap Overpass API（公開站台，依序嘗試，可在 .env 改）

公開服務可能改版、限流或暫停，查不到就留空，表單照樣可以手動填。
"""
import asyncio
import logging
import math
import os
import ssl

import httpx

log = logging.getLogger(__name__)

ARCGIS_URL = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
NLSC_URL = "https://api.nlsc.gov.tw/other"
OVERPASS_URLS = [u.strip() for u in os.getenv(
    "PSHEET_OVERPASS_URLS",
    "https://overpass-api.de/api/interpreter,https://overpass.private.coffee/api/interpreter,"
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter").split(",") if u.strip()]
NEARBY_BUDGET = float(os.getenv("PSHEET_NEARBY_BUDGET", "12"))  # 周邊查詢總共最多等幾秒
UA = os.getenv("PSHEET_USER_AGENT", "tw-property-sheet/1.0 (+https://github.com/Larryveryhandsome/tw-property-sheet)")


def _tls():
    """照常驗證憑證，只關掉 Python 3.13+ 新增的「嚴格模式」。
    部分政府網站的憑證缺少 Subject Key Identifier，嚴格模式會擋，但憑證本身是有效的。"""
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    return ctx


def _client(timeout=15.0):
    return httpx.AsyncClient(timeout=timeout, verify=_tls(), headers={"User-Agent": UA})


async def geocode(address: str):
    """地址 → {"lon", "lat", "score"}；查不到回 None。"""
    params = {"SingleLine": address, "countryCode": "TWN", "maxLocations": 1,
              "outFields": "Match_addr", "f": "json"}
    try:
        async with _client() as c:
            r = await c.get(ARCGIS_URL, params=params)
            r.raise_for_status()
            cands = r.json().get("candidates", [])
    except Exception as e:
        log.warning("geocode 失敗：%s", e)
        return None
    if not cands:
        return None
    loc = cands[0].get("location", {})
    if loc.get("x") is None:
        return None
    return {"lon": loc["x"], "lat": loc["y"], "score": cands[0].get("score", 0)}


def parse_town_village(d) -> dict:
    """TownVillagePointQuery 結果 → 縣市、鄉鎮、村里、地段（欄位名有新舊兩種）。"""
    if isinstance(d, list):
        d = d[0] if d else {}
    if not isinstance(d, dict):
        return {}
    out = {"county": d.get("ctyName", ""), "town": d.get("townName", ""),
           "village": d.get("villageName") or d.get("villName", ""), "land_section": d.get("sectName", "")}
    return {k: v for k, v in out.items() if v}


async def town_village(lon: float, lat: float) -> dict:
    """座標 → 行政區與地段。地號要看謄本或地籍圖，這個 API 不提供。"""
    try:
        async with _client() as c:
            r = await c.get(f"{NLSC_URL}/TownVillagePointQuery/{lon}/{lat}/4326", headers={"Accept": "application/json"})
            r.raise_for_status()
            return parse_town_village(r.json())
    except Exception as e:
        log.warning("行政區查詢失敗：%s", e)
        return {}


def distance_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + \
        math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_places(elements, lat, lon) -> dict:
    """Overpass 結果 → 每一類最近的一個（含距離）。"""
    buckets = {"school": [], "station": [], "market": [], "park": []}
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:zh") or tags.get("name")
        if not name or "lat" not in el:
            continue
        d = distance_m(lat, lon, el["lat"], el["lon"])
        if tags.get("amenity") == "school":
            kind = "school"
        elif tags.get("railway") == "station" or tags.get("station") == "subway" or tags.get("public_transport") == "station":
            kind = "station"
        elif tags.get("amenity") == "marketplace" or tags.get("shop") == "supermarket":
            kind = "market"
        elif tags.get("leisure") == "park":
            kind = "park"
        else:
            continue
        buckets[kind].append((d, f"{name}（約 {int(round(d, -1))} 公尺）"))
    return {k: min(v)[1] if v else "" for k, v in buckets.items()}


async def nearby(lat: float, lon: float, radius: int = 1000) -> dict:
    q = f"""[out:json][timeout:10];(
node["amenity"="school"](around:{radius},{lat},{lon});
node["railway"="station"](around:{radius * 2},{lat},{lon});
node["station"="subway"](around:{radius * 2},{lat},{lon});
node["amenity"="marketplace"](around:{radius},{lat},{lon});
node["shop"="supermarket"](around:{radius},{lat},{lon});
node["leisure"="park"](around:{radius},{lat},{lon});
way["leisure"="park"](around:{radius},{lat},{lon});
);out center;"""
    async def _try_all():
        async with _client(8.0) as c:
            for url in OVERPASS_URLS:  # 公開站台常限流，失敗就換下一個
                try:
                    r = await c.post(url, data={"data": q})
                    r.raise_for_status()
                    return r.json().get("elements", [])
                except Exception as e:
                    log.warning("周邊查詢失敗（%s）：%s", url, e)
        return None

    try:  # 公開站台不穩時不要讓使用者一直等，超過總時限就留空
        els = await asyncio.wait_for(_try_all(), NEARBY_BUDGET)
    except asyncio.TimeoutError:
        log.warning("周邊查詢超過 %s 秒，先留空（可手動填）", NEARBY_BUDGET)
        els = None
    if els is None:
        return {}
    for el in els:  # way 的座標在 center
        if "lat" not in el and "center" in el:
            el["lat"], el["lon"] = el["center"]["lat"], el["center"]["lon"]
    return nearest_places(els, lat, lon)
