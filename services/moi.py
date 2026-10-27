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
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

COP = os.getenv("MOI_COP_BASE", "https://copapi.moi.gov.tw")
LAND_SERVICE = "LandDescription/1.0/QueryByLandNo"
DATA = Path(__file__).resolve().parents[1] / "data"
CACHE = DATA / "moi_cache.json"
USAGE = DATA / "moi_usage.json"
_token = {"value": None, "at": 0.0}
# 付費查詢的臨界區：檢查快取 → 檢查每日上限 → 預先記一筆 → 送出 → 寫快取，整段一次只讓一個執行緒進來。
# 這樣多位經紀人同時按（或同一筆被連按）時，不會重複扣款、也不會衝破每日上限。
# 注意：這是「單一程序」的鎖；請不要用 uvicorn --workers N 多程序跑（見 README）。
_lock = threading.RLock()
TZ = timezone(timedelta(hours=8))   # 每日上限以台灣時間（Asia/Taipei）換日，不受伺服器時區影響


def today_str() -> str:
    return datetime.now(TZ).date().isoformat()


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


def _read(path, default, strict=False):
    """讀 JSON 檔。檔案不存在回 default；檔案壞掉時：strict=True 直接擋下（用量檔，寧可不查也不能歸零），
    否則把壞檔改名保留（.bad）再當空的，不會靜默覆蓋掉。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as e:
        if strict:
            raise MoiError(f"讀不到 {path.name}（{type(e).__name__}），為避免重複扣款先停止查詢")
        return default
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        raise ValueError("not a dict")
    except ValueError:
        if strict:
            raise MoiError(f"{path.name} 內容損毀，為避免用量歸零後重複扣款，已停止查詢；請檢查或刪除該檔後重試")
        try:
            os.replace(path, path.with_name(path.name + f".bad{int(time.time())}"))
        except OSError:
            pass
        return default


def _write(path, data):
    """原子寫入：先寫暫存檔再 os.replace，當機或同時讀取都不會看到寫一半的檔。"""
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def used_today(today: str = None) -> int:
    with _lock:
        return _read(USAGE, {}, strict=True).get(today or today_str(), 0)


def _count(today: str = None, delta: int = 1):
    with _lock:
        u = _read(USAGE, {}, strict=True)
        k = today or today_str()
        u[k] = max(0, u.get(k, 0) + delta)
        _write(USAGE, {d: n for d, n in u.items() if d >= k[:7]})   # 只留本月


def cache_key(office, sect, landno8):
    return f"{office}-{sect}-{landno8}"


def cached(office, sect, landno8):
    with _lock:
        return _read(CACHE, {}).get(cache_key(office, sect, landno8))


def area_field() -> str:
    """要讀的面積欄位名稱。PSHEET_MOI_AREA_FIELD 留白＝自動找（欄位名稱含 AREA 的第一個數字）。"""
    return os.getenv("PSHEET_MOI_AREA_FIELD", "").strip()


def find_area(reg, field: str = None):
    """標示部裡的面積（平方公尺）：有指定欄位就只看那個欄位，否則找欄位名含 AREA 的第一個數字。找不到回 None。"""
    if not isinstance(reg, dict):
        return None
    field = area_field() if field is None else field
    if field:
        for k, v in reg.items():
            if str(k).upper() == field.upper():
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None
    for k, v in reg.items():
        if "AREA" in str(k).upper():
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def extract_area(reg) -> float:
    """嚴格版：一定回正數面積，否則丟 MoiError（絕不默默回 0 或 None）。"""
    if not isinstance(reg, dict) or not reg:
        raise MoiError("平台查無這筆土地標示部（地段代碼或地號可能不對）")
    field = area_field()
    keys = ", ".join(sorted(str(k) for k in reg)[:20])
    if field:
        match = next((k for k in reg if str(k).upper() == field.upper()), None)
        if match is None:
            raise MoiError(f"核對失敗：找不到面積欄位「{field}」（平台回傳的欄位：{keys}）；請檢查 .env 的 PSHEET_MOI_AREA_FIELD")
        cands = [match]
    else:
        cands = [k for k in reg if "AREA" in str(k).upper()]
        if not cands:
            raise MoiError(f"核對失敗：找不到面積欄位（平台回傳的欄位：{keys}）；可在 .env 設定 PSHEET_MOI_AREA_FIELD 指定欄位名稱")
    for k in cands:
        try:
            v = float(str(reg[k]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if v > 0 and v == v and v != float("inf"):
            return v
    raise MoiError(f"核對失敗：面積欄位「{cands[0]}」的值不是有效的正數（{reg.get(cands[0])!r}）")


def get_token(client=None, sleep=time.sleep, force=False) -> str:
    """換 access token（快取 10 分鐘；force=True 略過快取）。平台同時發太多 token 會拒絕，失敗就等一下重試。"""
    with _lock:
        return _get_token(client, sleep, force)


def _get_token(client, sleep, force) -> str:
    if not force and _token["value"] and time.time() - _token["at"] < 600:
        return _token["value"]
    _token.update(value=None, at=0.0)
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
    """查一筆土地標示部。回 {"area_sqm", "area_ping", "raw", "cached"}。
    整段在鎖內執行：同一筆併發只會送出（收費）一次，每日上限在併發下也守得住。"""
    with _lock:
        hit = cached(office, sect, landno8)
        if hit:
            return {**hit, "cached": True}
        used = used_today(today)
        if used >= daily_limit():
            raise MoiError(f"今天已查 {used} 筆，達到每日上限 {daily_limit()}（可在 .env 調整 MOI_DAILY_LIMIT）")
        client = client or httpx.Client(timeout=25)
        r = _paid_post(client, office, sect, landno8, today)
        try:
            d = r.json()
        except ValueError:
            raise MoiError(f"平台回應看不懂（HTTP {r.status_code}）")
        if not isinstance(d, dict) or d.get("STATUS") != 1:
            d = d if isinstance(d, dict) else {}
            raise MoiError(f"平台回覆錯誤 {d.get('CODE') or ''} {d.get('MESSAGE') or ''}".strip())
        resp = d.get("RESPONSE")
        reg = next((x.get("LANDREG") for x in resp if isinstance(x, dict)), None) if isinstance(resp, list) else None
        area = extract_area(reg)
        out = {"area_sqm": area, "area_ping": round(area * 0.3025, 2), "raw": reg, "at": today_str()}
        c = _read(CACHE, {})
        c[cache_key(office, sect, landno8)] = out
        _write(CACHE, c)
        return {**out, "cached": False}


def _paid_post(client, office, sect, landno8, today):
    """送出付費查詢。先記帳再送（送出後即使逾時，平台也可能已計費，所以不能事後才記）。
    token 過期（401/403）時換新 token 重送一次：被拒絕的那次沒有查詢、不計費，所以把記帳還回去。"""
    body = [{"UNIT": office, "SEC": sect, "NO": landno8}]
    for attempt in range(2):
        tok = get_token(client, force=attempt > 0)
        _count(today)
        try:
            r = client.post(f"{COP}/cp/api/{LAND_SERVICE}", headers={"Authorization": "Bearer " + tok}, json=body)
        except httpx.HTTPError as e:
            raise MoiError(f"連不上地政平台（{type(e).__name__}）；這次可能已被計費，請先看平台用量再重試")
        if r.status_code in (401, 403) and attempt == 0:
            _count(today, -1)
            _token.update(value=None, at=0.0)
            continue
        return r
    return r


def compare(official_ping: float, other_ping) -> dict:
    """官方面積和謄本／圖面／表單面積比對。"""
    if not isinstance(other_ping, (int, float)) or not other_ping:
        return {}
    diff = abs(official_ping - other_ping) / official_ping
    return {"other_ping": other_ping, "diff_pct": round(diff * 100, 2), "ok": diff <= 0.03}
