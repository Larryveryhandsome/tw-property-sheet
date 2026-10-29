"""物調表產生器：網頁表單 → 自動查公開資料 → 產生 Excel。

啟動：python main.py（Windows 背景執行請雙擊「背景啟動.vbs」）然後開 http://localhost:8000
"""
import asyncio
import base64
import binascii
import io
import json
import logging
import os
import secrets
import sys
from datetime import date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List
from urllib.parse import quote

BASE = Path(__file__).resolve().parent


def load_dotenv(path=BASE / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from services import ai, geo, moi, parcel, risk, tenement  # noqa: E402
from sheet.excel import generate_excel  # noqa: E402
from sheet.schemas import PropertyForm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
BRAND = os.getenv("PSHEET_BRAND", "示範不動產")
TEMPLATE = os.getenv("PSHEET_TEMPLATE", "")  # 自訂範本（版面要和 sheet/layout.py 一致），空白＝用內建
MAX_PDF = 15 * 1024 * 1024
PDF_TIMEOUT = float(os.getenv("PSHEET_PDF_TIMEOUT", "40"))   # 解析一份謄本最多等幾秒
# 解析 PDF／產生 Excel 是 CPU 密集工作：丟到專用執行緒池，不卡住其他經紀人的請求，也限制同時進行的數量
_heavy = ThreadPoolExecutor(max_workers=int(os.getenv("PSHEET_HEAVY_WORKERS", "3")), thread_name_prefix="heavy")


async def read_limited(f: UploadFile, limit: int):
    """最多讀 limit+1 位元組；超過回 None，不會把超大檔案整個讀進記憶體。"""
    data = await f.read(limit + 1)
    return None if len(data) > limit else data


def parse_json(text: str, what: str = "表單") -> dict:
    try:
        d = json.loads(text or "{}")
    except ValueError:
        raise HTTPException(400, f"{what} 格式錯誤")
    if not isinstance(d, dict):
        raise HTTPException(400, f"{what} 格式錯誤")
    return d

# ── 存取控制（決策 #8：不做完整登入系統）────────────────────────────────
# 預設只監聽本機 127.0.0.1。要讓區網的同事連進來（PSHEET_HOST=0.0.0.0 或指定 IP），
# 必須先設 PSHEET_PASSWORD（至少 8 個字），否則拒絕啟動。設了密碼後，所有頁面與 API 都要
# 用 HTTP Basic 帳密進入（帳號隨意填，只核對密碼）。
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MIN_PASSWORD_LEN = 8
PASSWORD = os.getenv("PSHEET_PASSWORD", "")


def bind_config(env=None):
    """讀監聽位址與埠號；沒設就是 127.0.0.1:8000（只有本機能連）。"""
    env = os.environ if env is None else env
    host = (env.get("PSHEET_HOST") or "").strip() or "127.0.0.1"
    return host, int((env.get("PSHEET_PORT") or "").strip() or "8000")


def startup_check(host: str, password: str):
    """回傳 (可以啟動嗎, 說明)。開放區網卻沒設夠長的密碼就不准啟動。"""
    if host in LOCAL_HOSTS:
        return True, "只監聽本機（" + host + "），區網其他電腦連不到。"
    if not password:
        return False, (f"拒絕啟動：PSHEET_HOST={host} 會讓同網路的其他電腦連進來，但沒有設定 PSHEET_PASSWORD。"
                       f"請在 .env 加上 PSHEET_PASSWORD=至少{MIN_PASSWORD_LEN}個字的密碼，"
                       "或把 PSHEET_HOST 改回 127.0.0.1（只給本機用）。")
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"拒絕啟動：PSHEET_PASSWORD 太短（至少 {MIN_PASSWORD_LEN} 個字）。"
    return True, f"開放區網（{host}），所有頁面與 API 都要輸入 PSHEET_PASSWORD 才能使用。"


def _authorized(header: str, password: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    _, sep, given = raw.partition(":")
    return bool(sep) and secrets.compare_digest(given.encode("utf-8"), password.encode("utf-8"))


app = FastAPI(title="tw-property-sheet 物調表產生器")


@app.middleware("http")
async def require_password(request: Request, call_next):
    """有設 PSHEET_PASSWORD 時，所有路徑（頁面、靜態檔、API、/health）都要先驗證。"""
    if PASSWORD and not _authorized(request.headers.get("authorization", ""), PASSWORD):
        return Response("需要密碼（PSHEET_PASSWORD）才能使用。", status_code=401,
                        media_type="text/plain; charset=utf-8",
                        headers={"WWW-Authenticate": 'Basic realm="tw-property-sheet", charset="UTF-8"'})
    return await call_next(request)


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(str(BASE / "static" / "index.html"))


@app.get("/health")
async def health():
    return {"ok": True, "ai": ai.enabled()}


@app.get("/api/settings")
async def settings():
    try:
        used = moi.used_today()
    except moi.MoiError:
        used = None
    return {"brand": BRAND, "ai": ai.enabled(), "moi": moi.enabled(),
            "moi_used_today": used, "moi_daily_limit": moi.daily_limit()}


@app.get("/api/dropdown-options")
async def options():
    return JSONResponse(json.loads((BASE / "options.json").read_text(encoding="utf-8")))


@app.post("/api/lookup-address")
async def lookup_address(address: str = Form(...)):
    """地址 → 座標、行政區、地段、周邊設施（全部是公開服務，查不到就留空）。"""
    # 先用國土測繪門牌定位（只接受門牌完全相符），查不到再用 ArcGIS
    pt = await parcel.geocode_address(address)
    source = "國土測繪門牌"
    if not pt:
        pt = await geo.geocode(address)
        source = "ArcGIS"
    if not pt:
        raise HTTPException(400, f"無法定位：{address}")
    out = {"lon": pt["lon"], "lat": pt["lat"], "geocoder": source}
    out.update(await geo.town_village(pt["lon"], pt["lat"]))
    places = await geo.nearby(pt["lat"], pt["lon"])
    out.update({"nearby_park": places.get("park", ""), "nearby_market": places.get("market", ""),
                "nearby_station": places.get("station", ""), "nearby_school": places.get("school", "")})
    return out


@app.post("/api/parse-tenement")
async def parse_tenement(file: UploadFile = File(...)):
    data = await read_limited(file, MAX_PDF)
    if data is None:
        raise HTTPException(400, "檔案太大（上限 15MB）")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "請上傳 PDF 檔")
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(_heavy, tenement.parse_pdf, data), PDF_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(504, "謄本解析逾時，請換一份較小或較單純的 PDF")
    except tenement.PdfTooLarge as e:
        raise HTTPException(400, str(e))
    except Exception as e:   # 壞掉／惡意的 PDF：不要把內部訊息丟給前端
        logging.getLogger("main").warning("謄本解析失敗：%s", type(e).__name__)
        raise HTTPException(400, "這份 PDF 讀不了（可能損毀或加密）")


@app.post("/api/parcel")
async def parcel_lookup(county: str = Form(...), section: str = Form(...), number: str = Form(...), town: str = Form("")):
    """地號 → 地籍形狀、約略面積、面寬深度（國土測繪公開圖台，一次一筆）。"""
    try:
        return await parcel.lookup(county, section, number, town)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"國土測繪服務暫時查不到：{type(e).__name__}")


@app.post("/api/official-land")
async def official_land(county: str = Form(...), section: str = Form(...), number: str = Form(...),
                        confirm: str = Form(""), compare_ping: str = Form("")):
    """選用：內政部地政平台官方土地標示部（要自備金鑰、每筆收費）。必須帶 confirm=1 才會真的查。"""
    if not moi.enabled():
        raise HTTPException(400, "還沒設定地政平台金鑰（MOI_COP_CLIENT_ID／MOI_COP_SECRET），申請方式見 docs/金鑰申請.md")
    try:
        office, sect, town, sname = await parcel.resolve_section(county, section)
    except ValueError as e:
        raise HTTPException(400, str(e))
    no8 = parcel.landno8(number)
    hit = moi.cached(office, sect, no8)
    if not hit and confirm != "1":
        raise HTTPException(409, "這筆會向地政平台查詢並收費，請確認後再按一次")
    try:
        r = hit and {**hit, "cached": True} or await asyncio.to_thread(moi.query_land, office, sect, no8)
        used = moi.used_today()
    except moi.MoiError as e:
        raise HTTPException(400, str(e))
    try:
        cmp_val = float(compare_ping) if compare_ping else None
    except ValueError:
        cmp_val = None
    return {**r, "town": town, "section": sname, "number": number, "compare": moi.compare(r["area_ping"], cmp_val),
            "used_today": used, "daily_limit": moi.daily_limit()}


@app.post("/api/risk")
async def risk_check(deed_json: str = Form("{}"), form_json: str = Form("{}")):
    items = risk.check(parse_json(deed_json, "謄本資料"), parse_json(form_json))
    return {"summary": risk.summary(items), "items": items, "text": risk.text_for_sheet(items)}


@app.post("/api/generate-name")
async def gen_name(form_json: str = Form(...)):
    return {"case_name": ai.case_name(parse_json(form_json))}


@app.post("/api/generate-sell-points")
async def gen_points(form_json: str = Form(...)):
    return {"sell_points": ai.sell_points(parse_json(form_json))}


MAX_PHOTOS, MAX_PHOTO = 6, 15 * 1024 * 1024


@app.post("/api/generate")
async def generate(form_json: str = Form(...), photos: List[UploadFile] = File(default=[])):
    try:
        form = PropertyForm(**parse_json(form_json))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"表單錯誤：{e}")
    form.date_today = form.date_today or date.today().isoformat()
    pics = []
    for f in (photos or [])[:MAX_PHOTOS]:
        data = await read_limited(f, MAX_PHOTO)
        if data:
            pics.append((f.filename or "照片", data))
    try:
        loop = asyncio.get_running_loop()
        xlsx = await loop.run_in_executor(_heavy, lambda: generate_excel(
            form, Path(TEMPLATE) if TEMPLATE else None, title="物件調查表", subtitle=BRAND, photos=pics))
    except Exception as e:  # 壞掉的圖片檔
        raise HTTPException(400, f"照片讀不了：{type(e).__name__}")
    name = f"物調表_{(form.address or form.case_number or '未命名')[:20]}.xlsx".replace("/", "_")
    return StreamingResponse(io.BytesIO(xlsx),
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                                      "X-Missing-Fields": ",".join(form.missing_fields)})


# 開源版作者的客製化服務資訊（網頁頁尾也有一行；依 NOTICE 轉散布請保留）
CUSTOM_AD = ("需要改成你們公司的版本、幫忙安裝或串接？加 LINE 林其衞（0928-880-392），"
             "原則上 1～2 個工作天回覆報價。https://line.me/ti/p/xzHT2IywOM")

if __name__ == "__main__":
    import uvicorn
    host, port = bind_config()
    if sys.stdout is None or sys.stderr is None:  # pythonw 背景執行：紀錄寫檔
        (BASE / "data").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(BASE / "data" / "psheet.log", "a", encoding="utf-8", buffering=1)
        logging.getLogger().handlers[0].stream = sys.stderr
    ok, msg = startup_check(host, PASSWORD)
    log = logging.getLogger("main")
    if not ok:
        log.error(msg)
        sys.exit(2)
    log.info(msg)
    log.info(CUSTOM_AD)
    if "--check" in sys.argv[1:]:   # 背景啟動.vbs 先用這個檢查設定，不通過就跳視窗說明
        sys.exit(0)
    if host not in LOCAL_HOSTS:
        log.warning("密碼只擋住區網裡沒密碼的人；請勿直接架到公開網路，見 README「多人使用與對外架設」。")
    (BASE / "data").mkdir(exist_ok=True)
    (BASE / "data" / "psheet.pid").write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(app, host=host, port=port, log_config=None)
