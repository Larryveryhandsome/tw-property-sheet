"""解析土地／建物謄本 PDF（你自己合法申請的電子謄本），抓出物調表要的欄位。

支援兩種常見文字格式：
  - 電傳版：標籤緊貼值、沒有全形冒號
  - 地政事務所正本：全形冒號「：」、對齊空格、部分內容以星號遮罩
掃描成圖片、沒有文字層的 PDF 抓不到東西（回空 dict，請手動填）。

只抓物件本身與交易風險需要的資料（面積、樓層、分區、公告現值、權利範圍、取得日、他項權利種類與金額），
**不抓所有權人、他項權利人等個人姓名與統一編號**。
"""
import io
import re
from datetime import date

SQM_TO_PING = 0.3025  # 1 平方公尺 = 0.3025 坪
_FW = str.maketrans("０１２３４５６７８９", "0123456789")
ZH_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def sqm_to_ping(sqm: float) -> float:
    return round(sqm * SQM_TO_PING, 2)


def zh_to_int(s: str):
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if s in ZH_NUM:
        return ZH_NUM[s]
    m = re.fullmatch(r"([一二三四五六七八九])?十([一二三四五六七八九])?", s)
    if m:
        return ZH_NUM.get(m.group(1), 1) * 10 + ZH_NUM.get(m.group(2), 0)
    return None


def normalise_structure(raw: str) -> str:
    if "鋼筋混凝土" in raw and ("加強" in raw or "磚" in raw):
        return "鋼筋混凝土加強磚造"
    if "鋼骨" in raw:
        return "鋼骨造"
    if "鋼筋混凝土" in raw:
        return "鋼筋混凝土"
    if "木" in raw:
        return "木造"
    if "鋼" in raw:
        return "鋼鐵"
    return raw


def _norm(t: str) -> str:
    """全形數字、冒號、斜線轉半形，去掉遮罩星號。"""
    t = (t or "").translate(_FW)
    return re.sub(r"[*＊]+", "", t.replace("：", ":").replace("／", "/"))


def _section_name(name: str) -> str:
    """「示範市示範區示範段」→「示範段」：去掉開頭的縣市、鄉鎮市區（地段名本身帶「區」的不動，例如「工業區段」）。"""
    name = re.sub(r"^[一-鿿]{2}[縣市]", "", name)
    return re.sub(r"^[一-鿿]{1,3}?[鄉鎮市區](?=[一-鿿]+段)", "", name)


def _clean(v):
    v = (v or "").strip()
    return "" if (not v or "空白" in v) else v


def _num(s):
    return float(s.replace(",", ""))


def _roc_iso(y, m, d):
    try:
        return date(int(y) + 1911, int(m), int(d)).isoformat()
    except ValueError:
        return None


def _section(t, start, ends):
    m = re.search(start + r"(.*?)(?:" + "|".join(ends) + r"|$)", t, re.S)
    return m.group(1) if m else ""


def _share(text):
    """權利範圍 → ('100分之5', 0.05) 或 ('全部', 1.0)。"""
    m = re.search(r"權利範圍\s*:?\s*([^\n]+)", text)
    if not m:
        return None, None
    rng = m.group(1)
    f = re.search(r"(\d+)\s*分之\s*(\d+)", rng)
    if "全部" in rng or (f and f.group(1) == f.group(2)):
        return "全部", 1.0
    if f and int(f.group(1)):
        return f"{f.group(1)}分之{f.group(2)}", int(f.group(2)) / int(f.group(1))
    if "全部" in rng:
        return "全部", 1.0
    return None, None


def acquire_info(text: str, today: date = None) -> dict:
    """從所有權部找現任所有權人的取得日（最新一次登記）與原因，判斷是否 1 年內取得。
    標示部的登記日期是地籍異動（重測、合併）日，不是取得日，所以只看所有權部。"""
    today = today or date.today()
    n = _norm(text)
    entries = []
    for seg in re.findall(r"(?:建物|土地)所有權部(.*?)(?=查詢時間|列印時間|(?:建物|土地)他項權利部|$)", n, re.S):
        for blk in re.split(r"(?=登記次序)", seg):
            dm = re.search(r"登記日期\s*:?\s*民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", blk)
            if not dm:
                continue
            iso = _roc_iso(*dm.groups())
            rm = re.search(r"登記原因\s*:?\s*([^\n:0-9\s]+)", blk)
            if iso:
                entries.append((iso, rm.group(1).strip() if rm else ""))
    if not entries:
        return {}
    iso, reason = max(entries)
    days = (today - date.fromisoformat(iso)).days
    out = {"acquire_date": iso, "acquire_reason": reason, "holding_years": round(days / 365.25, 1)}
    if 0 <= days < 365:
        out["short_term"] = True
    return out


def encumbrance_info(text: str) -> dict:
    """他項權利部：只記「有沒有、什麼種類、擔保金額」，不記權利人。"""
    n = _norm(text)
    seg = _section(n, r"(?:建物|土地)他項權利部", [r"查詢時間", r"列印時間", r"(?:建物|土地)所有權部"])
    if not seg.strip():
        return {}
    flat = seg.replace("\n", " ")
    kinds = sorted(set(k.strip() for k in re.findall(r"權利種類\s*:?\s*([一-鿿]{2,10}?)(?=\s|收件|登記|$)", flat)))
    amounts = re.findall(r"擔保債權總金額\s*:?\s*(?:新臺幣|新台幣)?\s*([\d,]+)\s*元", flat)
    if not kinds and not amounts and "權利種類" not in flat:
        return {}
    out = {"encumbrance_kinds": kinds or ["（有他項權利，種類未解析）"]}
    if amounts:
        out["encumbrance_amount"] = sum(int(a.replace(",", "")) for a in amounts)
    return out


def query_date(text: str):
    """謄本的查詢／列印時間（判斷謄本新不新）。"""
    m = re.search(r"(?:查詢時間|列印時間|印表日期)\s*:?\s*民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", _norm(text))
    return _roc_iso(*m.groups()) if m else None


def parse_building_text(text: str, today: date = None) -> dict:
    """建物謄本文字 → 欄位 dict（key 對應 PropertyForm）。"""
    n = _norm(text)
    if "建物標示部" not in n and "建號" not in n:
        return {}
    seg = _section(n, r"建物標示部", [r"建物所有權部", r"查詢時間", r"列印時間"]) or n
    flat = re.sub(r"\s*\n\s*", " ", seg)
    r = {}
    m = re.search(r"([^\s:]{1,12}段(?:[一-鿿]{1,4}小段)?)\s*(\d{4,5})-(\d{3})\s*建號", n.replace("\n", " "))
    if m:
        r["build_section"] = _section_name(m.group(1))
        r["build_number"] = f"{int(m.group(2)):05d}-{m.group(3)}"
    m = re.search(r"建物坐落地號\s*:?\s*([^\s:]{1,12}段(?:[一-鿿]{1,4}小段)?)\s*(\d{4})-(\d{4})", flat)
    if m:
        r["land_section"], r["land_number"] = _section_name(m.group(1)), f"{m.group(2)}-{m.group(3)}"
    m = re.search(r"主要用途\s*:?\s*(\S+)", flat)
    if m and _clean(m.group(1)):
        r["main_use"] = _clean(m.group(1))
    m = re.search(r"主要建材\s*:?\s*(\S+)", flat)
    if m and _clean(m.group(1)):
        r["structure"] = normalise_structure(m.group(1))
    m = re.search(r"層\s*數\s*:?\s*0*(\d+)\s*層", flat)
    if m:
        r["floor_above"] = int(m.group(1))
    layers = re.findall(r"層\s*次\s*:?\s*(\S+?)\s*層次面積\s*:?\s*([\d,]+\.?\d*)", flat)
    if layers:
        r["layers"] = [{"floor": a, "ping": sqm_to_ping(_num(b))} for a, b in layers]
        first = re.sub(r"層$", "", layers[0][0])
        if zh_to_int(first) is not None:
            r["floor_current"] = zh_to_int(first)
    else:
        m = re.search(r"層\s*次\s*:?\s*(\S+?)層", flat)
        if m and zh_to_int(m.group(1)) is not None:
            r["floor_current"] = zh_to_int(m.group(1))
    m = re.search(r"總面積\s*:?\s*([\d,]+\.?\d*)\s*平方公尺", flat)
    if m:
        r["build_area_main"] = sqm_to_ping(_num(m.group(1)))
    sub = re.findall(r"附屬建物用途\s*:?\s*(\S+?)\s*面積\s*:?\s*([\d,]+\.?\d*)\s*平方公尺", flat)
    if sub:
        r["build_area_sub"] = round(sum(sqm_to_ping(_num(b)) for _, b in sub), 2)
        r["sub_uses"] = sorted(set(a for a, _ in sub))
    pub = 0.0
    for area, denom, numer in re.findall(
            r"共有部分[^\n]*?([\d,]+\.?\d*)\s*平方公\s*尺[\s\S]{0,60}?權利範圍\s*:?\s*(\d+)\s*分之\s*(\d+)", n):
        pub += _num(area) * int(numer) / int(denom)
    if pub > 0:
        r["build_area_pub"] = sqm_to_ping(pub)
    m = re.search(r"建築完成日期\s*:?\s*民國\s*0*(\d+)\s*年\s*0*(\d+)\s*月\s*0*(\d+)\s*日", flat)
    if m:
        r["finish_year"], r["finish_month"], r["finish_day"] = (int(x) for x in m.groups())
        from sheet.layout import age_years
        r["age_years"] = age_years(*m.groups(), today=today)
    own = _section(n, r"建物所有權部", [r"建物他項權利部", r"查詢時間", r"列印時間"])
    label, _ = _share(own)
    if label:
        r["build_share"] = label
    r.update(acquire_info(n, today))
    r.update(encumbrance_info(n))
    if query_date(n):
        r["deed_date"] = query_date(n)
    return r


def parse_land_text(text: str, today: date = None) -> dict:
    """土地謄本文字 → 欄位 dict。"""
    n = _norm(text)
    if "土地標示部" not in n and "地號" not in n:
        return {}
    seg = _section(n, r"土地標示部", [r"土地所有權部", r"查詢時間", r"列印時間"]) or n
    flat = re.sub(r"\s*\n\s*", " ", seg)
    r = {}
    m = re.search(r"([^\s:]{1,12}段(?:[一-鿿]{1,4}小段)?)\s*(\d{4})-(\d{4})\s*地號", n.replace("\n", " "))
    if m:
        r["land_section"], r["land_number"] = _section_name(m.group(1)), f"{m.group(2)}-{m.group(3)}"
    m = re.search(r"使用分區\s*:?\s*(.*?)(?=\s*使用地類別|\s*面\s*積|$)", flat)
    if m and _clean(m.group(1)):
        r["land_zone_name"] = _clean(m.group(1))
    m = re.search(r"使用地類別\s*:?\s*(.*?)(?=\s*面\s*積|$)", flat)
    if m and _clean(m.group(1)):
        r["land_use_type"] = _clean(m.group(1))
    if r.get("land_use_type"):
        r["land_zone_type"] = "都計外"   # 有使用地編定＝非都市土地
    elif r.get("land_zone_name"):
        r["land_zone_type"] = "都計內"
    m = re.search(r"面\s*積\s*:?\s*([\d,]+\.?\d*)\s*平方公尺", flat)
    if m:
        sqm = _num(m.group(1))
        r["land_area_sqm"] = sqm
        r["land_area_all"] = sqm_to_ping(sqm)
    m = re.search(r"公告土地現值.*?([\d,]+\.?\d*)\s*元\s*/\s*平方公尺", flat)
    if m:
        r["land_value_per_sqm"] = _num(m.group(1))
    m = re.search(r"(?:公告地價|申報地價).*?([\d,]+\.?\d*)\s*元\s*/\s*平方公尺", n.replace("\n", " "))
    if m:
        r["land_price_per_sqm"] = _num(m.group(1))
    own = _section(n, r"土地所有權部", [r"土地他項權利部", r"查詢時間", r"列印時間"])
    label, ratio = _share(own or n)
    if label:
        r["land_ownership"] = "全部" if ratio == 1 else "持分"
        r["land_share"] = label
        if r.get("land_area_all"):
            r["land_area_share"] = round(r["land_area_all"] * ratio, 2)
    r.update(acquire_info(n, today))
    r.update(encumbrance_info(n))
    if query_date(n):
        r["deed_date"] = query_date(n)
    return r


MAX_PAGES = 40   # 謄本通常不到 10 頁；頁數異常多的檔案不處理


class PdfTooLarge(ValueError):
    pass


def pdf_text(pdf_bytes: bytes) -> str:
    """先用 PyMuPDF（文字順序較穩），沒裝就用 pdfplumber。"""
    try:
        import fitz
    except ImportError:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise PdfTooLarge(f"PDF 頁數太多（上限 {MAX_PAGES} 頁）")
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if doc.page_count > MAX_PAGES:
            raise PdfTooLarge(f"PDF 頁數太多（上限 {MAX_PAGES} 頁）")
        return "".join(p.get_text() for p in doc)


def parse_pdf(pdf_bytes: bytes, today: date = None) -> dict:
    """自動判斷土地或建物謄本。回傳 {"kind": "land"|"building"|"", ...欄位}。"""
    text = pdf_text(pdf_bytes)
    n = _norm(text)
    if "建物標示部" in n:
        return {"kind": "building", **parse_building_text(text, today)}
    if "土地標示部" in n:
        return {"kind": "land", **parse_land_text(text, today)}
    return {"kind": ""}


# 舊介面相容
def parse_text(text: str, today: date = None) -> dict:
    return parse_building_text(text, today)
