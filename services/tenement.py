"""解析建物謄本 PDF（你自己合法申請下載的電子謄本），抓出物調表要的欄位。

只抓物件本身的資料（建號、面積、樓層、完工日、結構…），
**不抓所有權人、他項權利人等個人資料**。
"""
import io
import re
from datetime import date

SQM_TO_PING = 0.3025  # 1 平方公尺 = 0.3025 坪
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


def parse_text(text: str, today: date = None) -> dict:
    """謄本文字 → 欄位 dict（key 對應 PropertyForm）。"""
    r = {}
    t = text or ""
    m = re.search(r"([^\s]+段)\s*(\d{5}-\d{3})\s*建號", t)
    if m:
        r["build_number"] = m.group(2)
        r["build_section"] = m.group(1)
    m = re.search(r"建物坐落地號[\s\S]{0,30}?([^\s]+段)\s*\n?\s*(\d{4}-\d{4})", t)
    if m:
        r["land_section"], r["land_number"] = m.group(1), m.group(2)
    m = re.search(r"主要用途\s+(\S+)", t)
    if m:
        r["main_use"] = m.group(1)
    m = re.search(r"主要建材\s+(\S+)", t)
    if m:
        r["structure"] = normalise_structure(m.group(1))
    m = re.search(r"層數\s+0*(\d+)層", t)
    if m:
        r["floor_above"] = int(m.group(1))
    m = re.search(r"層次\s+(\S+?)層", t)
    if m and zh_to_int(m.group(1)) is not None:
        r["floor_current"] = zh_to_int(m.group(1))
    m = re.search(r"總面積\s+([\d.]+)\s*平方公尺", t)
    if m:
        r["build_area_main"] = sqm_to_ping(float(m.group(1)))
    m = re.search(r"附屬建物用途[^\n]*\s*面積\s+([\d.]+)\s*平方公尺", t)
    if m:
        r["build_area_sub"] = sqm_to_ping(float(m.group(1)))
    pub = 0.0
    for area, denom, numer in re.findall(
            r"共有部分[^\n]*?([\d.]+)\s*平方公\s*尺[\s\S]{0,60}?權利範圍\s+(\d+)分之(\d+)", t):
        pub += float(area) * int(numer) / int(denom)
    if pub > 0:
        r["build_area_pub"] = sqm_to_ping(pub)
    m = re.search(r"建築完成日期\s+民國\s*0*(\d+)\s*年\s*0*(\d+)\s*月\s*0*(\d+)\s*日", t)
    if m:
        r["finish_year"], r["finish_month"], r["finish_day"] = (int(x) for x in m.groups())
        from sheet.layout import age_years
        r["age_years"] = age_years(*m.groups(), today=today)
    return r


def parse_pdf(pdf_bytes: bytes) -> dict:
    import pdfplumber  # 只有真的解析 PDF 才需要
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_text(text)
