"""地號 → 地籍形狀、約略面積、面寬／深度（國土測繪中心公開圖台，免金鑰）。

流程：縣市名＋地段名 → 查地段代碼（ListCounty／ListTown1／ListLandSection，結果快取）
      → 地號形狀（地籍圖台 qryTileMapIndex；一筆地號可能由好幾塊組成，先用 flag=3 拿全部，失敗再退 flag=2）
      → 回傳每塊的外框、PNG 遮罩、約略面積（需 Pillow），前端用 Leaflet 疊在國土測繪底圖上。

注意：
- 面積是從圖台遮罩估算的**約略值**（實測誤差多在 5% 內），正式面積以土地謄本為準。
- 這是公開圖台，請一次查一筆、不要批次大量撈取；程式內建每 0.5 秒最多 1 次請求。
"""
import asyncio
import base64
import io
import json
import math
import re
import time
from pathlib import Path

import httpx

from .geo import _tls

API = "https://api.nlsc.gov.tw/other"
QRY = "https://landmaps.nlsc.gov.tw/S_Maps/qryTileMapIndex"
QUERY_SEARCH = "https://api.nlsc.gov.tw/MapSearch/QuerySearch"
HEADERS = {"User-Agent": "Mozilla/5.0 (tw-property-sheet)", "Referer": "https://maps.nlsc.gov.tw/",
           "Origin": "https://maps.nlsc.gov.tw"}
CACHE = Path(__file__).resolve().parents[1] / "data" / "nlsc_cache.json"
THROTTLE = 0.5
_last = [0.0]
_FW = str.maketrans("０１２３４５６７８９", "0123456789")


async def _wait():
    d = THROTTLE - (time.monotonic() - _last[0])
    if d > 0:
        await asyncio.sleep(d)
    _last[0] = time.monotonic()


def _client():
    return httpx.AsyncClient(timeout=25, verify=_tls(), headers=HEADERS)


def _load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(c):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


async def _cached_list(key, url, pattern):
    c = _load_cache()
    if key not in c:
        await _wait()
        async with _client() as cl:
            r = await cl.get(url)
            r.raise_for_status()
        c[key] = re.findall(pattern, r.text, re.S)
        _save_cache(c)
    return c[key]


async def counties():
    return await _cached_list("counties", f"{API}/ListCounty",
                              r"<countycode>(\w+)</countycode>\s*<countyname>(.*?)</countyname>")


async def towns(cc):
    return await _cached_list(f"towns_{cc}", f"{API}/ListTown1/{cc}",
                              r"<towncode01>(\w+)</towncode01>\s*<townname>(.*?)</townname>")


async def sections(cc, tc):
    return await _cached_list(f"sect_{cc}_{tc}", f"{API}/ListLandSection/{cc}/{tc}",
                              r"<office>(\w+)</office>.*?<sectcode>(\w+)</sectcode>\s*<sectstr>(.*?)</sectstr>")


def landno8(s: str) -> str:
    """'401' → '04010000'、'401-1'／'401之1'／'0401-0001' → '04010001'。"""
    m = re.match(r"\s*0*(\d+)(?:\s*[-－之]\s*0*(\d+))?", (s or "").translate(_FW))
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):04d}" if m else ""


def pick_section(sect_list, raw):
    """在一個鄉鎮的地段清單裡找最符合的地段（完全相同優先，其次結尾相同、取最長）。"""
    raw = raw.strip()
    best = None
    for office, sc, name in sect_list:
        if raw == name or raw.endswith(name) or name.endswith(raw):
            score = (raw == name, len(name))
            if not best or score > best[0]:
                best = (score, (office, sc, name))
    return best[1] if best else None


async def resolve_section(county: str, section: str, town: str = ""):
    """回 (office, sectcode, 鄉鎮名, 地段名)。county 可寫「台北市」或「臺北市」。"""
    county = (county or "").replace("台", "臺")
    cc = next((c for c, n in await counties() if n == county), None)
    if not cc:
        raise ValueError(f"找不到縣市「{county}」")
    tl = await towns(cc)
    order = [t for t in tl if town and town in t[1]] + [t for t in tl if not (town and town in t[1])]
    for tc, tn in order:
        hit = pick_section(await sections(cc, tc), section)
        if hit:
            return hit[0], hit[1], tn, hit[2]
    raise ValueError(f"在{county}找不到「{section}」")


def _flat(x):
    for y in (x if isinstance(x, list) else [x]):
        if isinstance(y, list):
            yield from _flat(y)
        elif isinstance(y, dict):
            yield y


def mask_area(part: dict) -> float:
    """PNG 遮罩的填色比例 × 外框的實際面積（平方公尺，約略值）。沒裝 Pillow 回 None。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(io.BytesIO(base64.b64decode(part["image"]))).convert("RGBA")
    hist = im.getchannel("A").histogram()
    top = max((i for i, n in enumerate(hist) if n), default=1) or 1
    filled = sum(i * n for i, n in enumerate(hist)) / top   # 邊緣半透明像素按比例算
    lat = (part["ly"] + part["ry"]) / 2
    w = abs(part["rx"] - part["lx"]) * 111320 * math.cos(math.radians(lat))
    h = abs(part["ry"] - part["ly"]) * 110574
    return round(w * h * filled / max(im.size[0] * im.size[1], 1), 1)


def bbox_dims(lx, ly, rx, ry):
    """外框的東西寬、南北深（公尺），給「面寬／深度」一個參考值。"""
    lat = (ly + ry) / 2
    return round(abs(rx - lx) * 111320 * math.cos(math.radians(lat)), 1), round(abs(ry - ly) * 110574, 1)


def parse_parcel_rows(rows: list) -> dict:
    parts = [{k: (float(x[k]) if k != "image" else x[k]) for k in ("image", "lx", "ly", "rx", "ry")}
             for x in rows if x.get("image") and "lx" in x]
    if not parts:
        return {}
    for p in parts:
        p["area_sqm"] = mask_area(p)
    out = {"parts": parts, "lx": min(p["lx"] for p in parts), "ly": min(p["ly"] for p in parts),
           "rx": max(p["rx"] for p in parts), "ry": max(p["ry"] for p in parts)}
    big = max(parts, key=lambda p: p["area_sqm"] or (p["rx"] - p["lx"]) * (p["ry"] - p["ly"]))
    out["lat"], out["lon"] = (big["ly"] + big["ry"]) / 2, (big["lx"] + big["rx"]) / 2
    areas = [p["area_sqm"] for p in parts if p["area_sqm"] is not None]
    if areas:
        out["area_sqm"] = round(sum(areas), 1)
        out["area_ping"] = round(out["area_sqm"] * 0.3025, 2)
    out["width_m"], out["depth_m"] = bbox_dims(big["lx"], big["ly"], big["rx"], big["ry"])
    return out


async def query_parcel(office: str, sect: str, number: str) -> dict:
    no8 = landno8(number)
    tries = [{"type": "2", "flag": "3", "county": office[:1], "parno": sect + no8, "alpah": "0.5f", "imgflag": "1"},
             {"type": "2", "flag": "2", "office": office, "sect": sect, "landno": no8, "alpah": "0.5f", "imgflag": "1"}]
    last = "查無此地號"
    async with _client() as cl:
        for data in tries:
            for attempt in range(3):
                await _wait()
                try:
                    r = await cl.post(QRY, data=data)
                    r.raise_for_status()
                    rows = list(_flat(r.json())) if r.text.strip() else []
                except Exception as e:
                    last = f"圖台回應異常（{type(e).__name__}）"
                    break
                msg = next((x.get("msg") for x in rows if x.get("msg")), None)
                if msg and "稍候" in str(msg):
                    await asyncio.sleep(1.5 + attempt)
                    continue
                if msg:
                    last = str(msg)
                    break
                out = parse_parcel_rows(rows)
                if out:
                    return out
                break
    raise ValueError(last)


async def lookup(county: str, section: str, number: str, town: str = "") -> dict:
    office, sc, tn, sname = await resolve_section(county, section, town)
    out = await query_parcel(office, sc, number)
    out.update({"county": county.replace("台", "臺"), "town": tn, "section": sname, "number": number,
                "map_url": f"https://maps.nlsc.gov.tw/T09/mapshow.action?In_type=web&Lat={out['lat']:.6f}&Lon={out['lon']:.6f}"})
    return out


# ── 門牌定位備援（國土測繪 QuerySearch，只接受門牌完全相符）──────────────
def normalise_address(addr: str) -> str:
    """給門牌查詢用：全形轉半形、去「○鄰」與行政村里（社區名後面直接接門牌號時保留）、截到「號」。"""
    a = (addr or "").translate(_FW)
    a = re.sub(r"([縣市鄉鎮區])(?:[一-鿿]{1,3}(?:里|村)(?!\d+號)|[一-鿿]{1,4}(?=\d+鄰))", r"\1", a)
    a = re.sub(r"\d+鄰", "", a)
    m = re.search(r"號", a)
    return a[:m.end()] if m else a


def address_core(s: str) -> str:
    """門牌核心＝路段巷弄號（去縣市、鄉鎮、村里、鄰、樓層），用來嚴格比對。"""
    s = (s or "").translate(_FW)
    s = re.sub(r"^[一-鿿]{2}[縣市]", "", s)
    s = re.sub(r"^[一-鿿]{1,3}[市鄉鎮區]", "", s)
    s = re.sub(r"[一-鿿]{1,4}(?:里|村)(?=\d+鄰|[一-鿿]{1,6}[路街道])|[一-鿿]{1,4}(?=\d+鄰)", "", s)
    s = re.sub(r"\d+鄰", "", s)
    m = re.search(r"號", s)
    return re.sub(r"\s", "", s[:m.end()] if m else s)


def parse_query_search(xml: str, want_core: str):
    for it in re.findall(r"<ITEM>(.*?)</ITEM>", xml or "", re.S):
        key = re.search(r"<KEY>(.*?)</KEY>", it, re.S)
        loc = re.search(r"<LOCATION>(.*?)</LOCATION>", it, re.S)
        content = re.search(r"<CONTENT>(.*?)</CONTENT>", it, re.S)
        if not (key and loc and content) or "ADDRESS" not in key.group(1) or "," not in loc.group(1):
            continue
        lon, lat = (float(x) for x in loc.group(1).split(",")[:2])
        if not (119 <= lon <= 122.5 and 21 <= lat <= 26.5):
            continue
        if want_core and address_core(content.group(1)) != want_core:
            continue   # 只認門牌完全相符，不用隔壁巷弄的座標
        return {"lat": round(lat, 6), "lon": round(lon, 6), "matched": content.group(1).translate(_FW)}
    return None


async def geocode_address(addr: str):
    from urllib.parse import quote
    await _wait()
    try:
        async with _client() as cl:
            r = await cl.post(QUERY_SEARCH, data={"word": quote(normalise_address(addr)), "feedback": "XML"})
            r.raise_for_status()
        return parse_query_search(r.text, address_core(addr))
    except Exception:
        return None
