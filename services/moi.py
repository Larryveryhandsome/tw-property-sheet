"""選用：內政部「地政整合資訊服務共享協作平台」官方土地標示部核對（要自己申請金鑰、按筆付費）。

- 平台：https://cop.land.moi.gov.tw/ （申請步驟見 docs/金鑰申請.md）
- 驗證：Client ID ＋ 安全碼（Secret）→ /cp/getToken 換 access token → 查 LandDescription/1.0/QueryByLandNo
- 費用：每查一筆收費（目前約新臺幣 1 元，以平台公告為準）。所以：
    1. 沒設金鑰就完全不會呼叫
    2. 前端要按「確認」才查（API 也要帶 confirm=1）
    3. 同一筆地號查過就存在 data/moi_cache.json，之後不再收費
    4. 每天最多查 MOI_DAILY_LIMIT 筆（預設 20），避免程式出錯一直扣錢
- 只查土地「標示部」（面積、使用分區、公告現值等），不含所有權人等個資。
"""
import base64
import json
import os
import time
from datetime import date
from pathlib import Path

import httpx

COP = os.getenv("MOI_COP_BASE", "https://copapi.moi.gov.tw")
LAND_SERVICE = "LandDescription/1.0/QueryByLandNo"
DATA = Path(__file__).resolve().parents[1] / "data"
CACHE = DATA / "moi_cache.json"
USAGE = DATA / "moi_usage.json"
_token = {"value": None, "at": 0.0}


class MoiError(RuntimeError):
    pass


def credentials():
    return os.getenv("MOI_COP_CLIENT_ID", "").strip(), os.getenv("MOI_COP_SECRET", "").strip()


def enabled() -> bool:
    cid, sec = credentials()
    return bool(cid and sec)


def daily_limit() -> int:
    try:
        return max(0, int(os.getenv("MOI_DAILY_LIMIT", "20")))
    except ValueError:
        return 20


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path, data):
    DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def used_today(today: str = None) -> int:
    return _read(USAGE, {}).get(today or date.today().isoformat(), 0)


def _count(today: str = None):
    u = _read(USAGE, {})
    k = today or date.today().isoformat()
    u[k] = u.get(k, 0) + 1
    _write(USAGE, {d: n for d, n in u.items() if d >= k[:7]})   # 只留本月


def cache_key(office, sect, landno8):
    return f"{office}-{sect}-{landno8}"


def cached(office, sect, landno8):
    return _read(CACHE, {}).get(cache_key(office, sect, landno8))


def find_area(reg):
    """標示部裡的面積（平方公尺）：欄位名含 AREA 的第一個數字。"""
    if not isinstance(reg, dict):
        return None
    for k, v in reg.items():
        if "AREA" in str(k).upper():
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def get_token(client=None, sleep=time.sleep) -> str:
    """換 access token（快取 10 分鐘）。平台同時發太多 token 會拒絕，失敗就等一下重試。"""
    if _token["value"] and time.time() - _token["at"] < 600:
        return _token["value"]
    cid, sec = credentials()
    if not (cid and sec):
        raise MoiError("還沒設定地政平台金鑰（MOI_COP_CLIENT_ID／MOI_COP_SECRET），見 docs/金鑰申請.md")
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    client = client or httpx.Client(timeout=25)
    for attempt in range(3):
        try:
            r = client.get(f"{COP}/cp/getToken", headers={"Authorization": "Basic " + auth})
            tok = r.json().get("access_token") if r.status_code == 200 else None
            if tok:
                _token.update(value=tok, at=time.time())
                return tok
        except (httpx.HTTPError, ValueError):
            pass
        sleep(3 + attempt * 3)
    raise MoiError("地政平台驗證沒過：請檢查 Client ID 與安全碼是否正確、服務是否已開通、帳戶是否有餘額")


def query_land(office: str, sect: str, landno8: str, client=None, today: str = None) -> dict:
    """查一筆土地標示部。回 {"area_sqm", "area_ping", "raw", "cached"}。"""
    hit = cached(office, sect, landno8)
    if hit:
        return {**hit, "cached": True}
    if used_today(today) >= daily_limit():
        raise MoiError(f"今天已查 {used_today(today)} 筆，達到每日上限 {daily_limit()}（可在 .env 調整 MOI_DAILY_LIMIT）")
    client = client or httpx.Client(timeout=25)
    tok = get_token(client)
    r = client.post(f"{COP}/cp/api/{LAND_SERVICE}", headers={"Authorization": "Bearer " + tok},
                    json=[{"UNIT": office, "SEC": sect, "NO": landno8}])
    _count(today)   # 有送出就算一次（平台可能已計費）
    try:
        d = r.json()
    except ValueError:
        raise MoiError(f"平台回應看不懂（HTTP {r.status_code}）")
    if d.get("STATUS") != 1:
        raise MoiError(f"平台回覆錯誤 {d.get('CODE') or ''} {d.get('MESSAGE') or ''}".strip())
    reg = next((x.get("LANDREG") for x in d.get("RESPONSE") or [] if isinstance(x, dict)), None)
    area = find_area(reg)
    if not area:
        raise MoiError("平台查無這筆土地標示部（地段代碼或地號可能不對）")
    out = {"area_sqm": area, "area_ping": round(area * 0.3025, 2), "raw": reg, "at": date.today().isoformat()}
    c = _read(CACHE, {})
    c[cache_key(office, sect, landno8)] = out
    _write(CACHE, c)
    return {**out, "cached": False}


def compare(official_ping: float, other_ping) -> dict:
    """官方面積和謄本／圖面／表單面積比對。"""
    if not isinstance(other_ping, (int, float)) or not other_ping:
        return {}
    diff = abs(official_ping - other_ping) / official_ping
    return {"other_ping": other_ping, "diff_pct": round(diff * 100, 2), "ok": diff <= 0.03}
