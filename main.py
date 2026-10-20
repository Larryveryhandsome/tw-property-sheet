"""物調表產生器：網頁表單 → 自動查公開資料 → 產生 Excel。

啟動：python main.py（Windows 背景執行請雙擊「背景啟動.vbs」）然後開 http://localhost:8000
"""
import io
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from services import ai, geo, parcel, risk, tenement  # noqa: E402
from sheet.excel import generate_excel  # noqa: E402
from sheet.schemas import PropertyForm  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
BRAND = os.getenv("PSHEET_BRAND", "示範不動產")
TEMPLATE = os.getenv("PSHEET_TEMPLATE", "")  # 自訂範本（版面要和 sheet/layout.py 一致），空白＝用內建
MAX_PDF = 15 * 1024 * 1024

app = FastAPI(title="tw-property-sheet 物調表產生器")
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(str(BASE / "static" / "index.html"))


@app.get("/health")
async def health():
    return {"ok": True, "ai": ai.enabled()}


@app.get("/api/settings")
async def settings():
    return {"brand": BRAND, "ai": ai.enabled()}


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
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "請上傳 PDF 檔")
    if len(data) > MAX_PDF:
        raise HTTPException(400, "檔案太大（上限 15MB）")
    return tenement.parse_pdf(data)


@app.post("/api/parcel")
async def parcel_lookup(county: str = Form(...), section: str = Form(...), number: str = Form(...), town: str = Form("")):
    """地號 → 地籍形狀、約略面積、面寬深度（國土測繪公開圖台，一次一筆）。"""
    try:
        return await parcel.lookup(county, section, number, town)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"國土測繪服務暫時查不到：{type(e).__name__}")


@app.post("/api/risk")
async def risk_check(deed_json: str = Form("{}"), form_json: str = Form("{}")):
    items = risk.check(json.loads(deed_json), json.loads(form_json))
    return {"summary": risk.summary(items), "items": items, "text": risk.text_for_sheet(items)}


@app.post("/api/generate-name")
async def gen_name(form_json: str = Form(...)):
    return {"case_name": ai.case_name(json.loads(form_json))}


@app.post("/api/generate-sell-points")
async def gen_points(form_json: str = Form(...)):
    return {"sell_points": ai.sell_points(json.loads(form_json))}


MAX_PHOTOS, MAX_PHOTO = 6, 15 * 1024 * 1024


@app.post("/api/generate")
async def generate(form_json: str = Form(...), photos: List[UploadFile] = File(default=[])):
    try:
        form = PropertyForm(**json.loads(form_json))
    except Exception as e:
        raise HTTPException(400, f"表單錯誤：{e}")
    form.date_today = form.date_today or date.today().isoformat()
    pics = []
    for f in (photos or [])[:MAX_PHOTOS]:
        data = await f.read()
        if data and len(data) <= MAX_PHOTO:
            pics.append((f.filename or "照片", data))
    try:
        xlsx = generate_excel(form, Path(TEMPLATE) if TEMPLATE else None,
                              title="物件調查表", subtitle=BRAND, photos=pics)
    except Exception as e:  # 壞掉的圖片檔
        raise HTTPException(400, f"照片讀不了：{type(e).__name__}")
    name = f"物調表_{(form.address or form.case_number or '未命名')[:20]}.xlsx".replace("/", "_")
    return StreamingResponse(io.BytesIO(xlsx),
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                                      "X-Missing-Fields": ",".join(form.missing_fields)})


if __name__ == "__main__":
    import uvicorn
    host, port = os.getenv("PSHEET_HOST", "127.0.0.1"), int(os.getenv("PSHEET_PORT", "8000"))
    if sys.stdout is None or sys.stderr is None:  # pythonw 背景執行：紀錄寫檔
        (BASE / "data").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(BASE / "data" / "psheet.log", "a", encoding="utf-8", buffering=1)
        logging.getLogger().handlers[0].stream = sys.stderr
    (BASE / "data").mkdir(exist_ok=True)
    (BASE / "data" / "psheet.pid").write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(app, host=host, port=port, log_config=None)
