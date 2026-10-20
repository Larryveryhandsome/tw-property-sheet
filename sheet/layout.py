"""通用物調表的版面與欄位換算。

版面（LAYOUT）同時決定「範本長什麼樣」和「每個欄位寫到哪一格」，
所以範本和填寫程式永遠對得上。想改版面只要改這個檔。
"""
from datetime import date

# 每一列：("section", 標題) 或 ("row", [(key, 標籤), (key, 標籤)]) 或 ("text", key, 標籤, 列高)
LAYOUT = [
    ("section", "基本資料"),
    ("row", [("case_number", "物件編號"), ("date_today", "製表日期")]),
    ("row", [("case_name", "案名"), ("agent", "營業員")]),
    ("wide", "address", "地址／坐落"),
    ("row", [("total_price", "總價（萬）"), ("unit_price", "單價（萬／坪）")]),
    ("row", [("case_type", "案件型態"), ("status", "現況")]),
    ("row", [("exposure", "現場曝光"), ("escrow", "履約保證")]),
    ("row", [("handover", "交屋"), ("visit", "帶看方式")]),
    ("row", [("source", "客戶來源"), ("sell_reason", "售屋動機")]),
    ("section", "土地"),
    ("row", [("land_parcel", "段／地號"), ("land_share_text", "權利範圍")]),
    ("row", [("land_area_all", "土地坪數（全部）"), ("land_area_share", "土地坪數（持分）")]),
    ("wide", "land_use", "使用分區／編定"),
    ("row", [("land_value_text", "公告土地現值"), ("land_price_text", "公告地價")]),
    ("row", [("vacant_ratio", "空地比"), ("land_dims", "臨路／面寬／深度（米）")]),
    ("section", "建物"),
    ("row", [("build_number", "建號"), ("main_use", "主要用途")]),
    ("row", [("build_area_main", "主建物（坪）"), ("build_area_sub", "附屬建物（坪）")]),
    ("row", [("build_area_pub", "公設（坪）"), ("build_area_total", "權狀合計（坪）")]),
    ("row", [("floors", "樓層（所在／地上／地下）"), ("layout", "格局")]),
    ("row", [("finish_date", "完工日期（民國）"), ("age_years", "屋齡（年）")]),
    ("row", [("structure", "結構"), ("appearance", "外觀")]),
    ("row", [("orientation", "朝向"), ("corner", "邊間／中庭")]),
    ("row", [("addon_type", "加建"), ("utilities", "水電")]),
    ("row", [("parking_text", "車位"), ("mgmt_text", "管理")]),
    ("row", [("loan", "貸款"), ("village", "村里")]),
    ("section", "周邊"),
    ("row", [("school_elem", "國小學區"), ("school_mid", "國中學區")]),
    ("row", [("nearby_park", "公園"), ("nearby_station", "車站")]),
    ("row", [("nearby_market", "市場"), ("others", "其他機能")]),
    ("section", "產權與風險"),
    ("row", [("acquire_text", "取得日／持有"), ("encumbrance_text", "他項權利")]),
    ("row", [("build_share_text", "建物權利範圍"), ("deed_date", "謄本查詢日")]),
    ("text", "risk_notes", "上架前風險提示", 60),
    ("section", "說明"),
    ("text", "sell_points", "銷售重點", 96),
    ("text", "notes", "注意事項", 60),
]

# 必填：沒填就標黃
REQUIRED = ["case_number", "address", "total_price", "case_type", "build_area_main",
            "layout", "finish_date", "structure"]
# 土地物件（沒有建物）不需要的必填欄位
LAND_ONLY_SKIP = {"build_area_main", "layout", "finish_date", "structure"}

COLS = "ABCDEF"  # A 標籤｜B:C 值｜D 標籤｜E:F 值


def cell_map():
    """key → 值所在的儲存格（左上角）。"""
    m, row = {}, 3
    for item in LAYOUT:
        if item[0] == "section":
            pass
        elif item[0] == "row":
            (k1, _), (k2, _) = item[1]
            m[k1], m[k2] = f"B{row}", f"E{row}"
        else:  # wide / text
            m[item[1]] = f"B{row}"
        row += 1
    return m


def _n(v, digits=2):
    if v is None or v == "":
        return None
    v = round(float(v), digits)
    return int(v) if v == int(v) else v


def _join(parts, sep="，"):
    parts = [str(p) for p in parts if p not in (None, "")]
    return sep.join(parts) if parts else None


def age_years(year_roc, month=None, day=None, today=None):
    if not year_roc:
        return None
    today = today or date.today()
    try:
        built = date(int(year_roc) + 1911, int(month or 1), int(day or 1))
    except ValueError:
        return None
    return round((today - built).days / 365.25, 1)


def derive(form, today=None) -> dict:
    """把表單換算成要寫進 Excel 的值（合計坪數、單價、屋齡、組合字串…）。"""
    f = form.model_dump() if hasattr(form, "model_dump") else dict(form)
    today = today or date.today()
    out = {k: f.get(k) for k in cell_map() if k in f}
    out["date_today"] = f.get("date_today") or today.isoformat()

    parts = [f.get("build_area_main"), f.get("build_area_sub"), f.get("build_area_pub")]
    total_build = sum(float(p) for p in parts if p not in (None, "")) if any(p not in (None, "") for p in parts) else None
    out["build_area_total"] = _n(total_build)
    for k in ("build_area_main", "build_area_sub", "build_area_pub", "land_area_all", "land_area_share", "total_price"):
        out[k] = _n(f.get(k))

    # 單價：有建物用權狀合計坪數，純土地用土地坪數（持分優先）
    land = f.get("land_area_share") or f.get("land_area_all")
    base = total_build or (float(land) if land else None)
    out["unit_price"] = _n(float(f["total_price"]) / base) if f.get("total_price") and base else None

    out["visit"] = _join([f.get("visit_method"),
                          f"聯絡 {f['visit_contact']}" if f.get("visit_contact") else None,
                          f"鑰匙 #{f['visit_key_no']}" if f.get("visit_key_no") else None], "　")
    out["land_parcel"] = _join([f.get("land_section"), f.get("land_number") and f"{f['land_number']} 地號"], " ")
    out["land_use"] = _join([f.get("land_zone_type"), f.get("land_zone_name"), f.get("land_use_type")])
    if any(f.get(k) for k in ("road_frontage", "width", "depth")):
        out["land_dims"] = " / ".join("—" if f.get(k) in (None, "") else str(_n(f[k])) for k in ("road_frontage", "width", "depth"))
    if f.get("floor_current") or f.get("floor_above"):
        out["floors"] = f"{f.get('floor_current') or '—'} / {f.get('floor_above') or '—'} / {f.get('floor_below') or 0}"
    if any(f.get(k) not in (None, "") for k in ("room_count", "hall_count", "bath_count")):
        out["layout"] = f"{f.get('room_count') or 0}房{f.get('hall_count') or 0}廳{f.get('bath_count') or 0}衛"
    if f.get("finish_year"):
        out["finish_date"] = f"{f['finish_year']} 年 {f.get('finish_month') or '—'} 月 {f.get('finish_day') or '—'} 日"
        out["age_years"] = age_years(f["finish_year"], f.get("finish_month"), f.get("finish_day"), today)
    out["orientation"] = _join([f.get("orientation_build") and f"建物朝{f['orientation_build']}",
                                f.get("orientation_window") and f"落地窗朝{f['orientation_window']}",
                                f.get("orientation_door") and f"大門朝{f['orientation_door']}"], "　")
    out["corner"] = _join([f.get("is_corner"), f.get("has_courtyard")], "／")
    out["parking_text"] = _join([f.get("parking"), f.get("parking_type"), f.get("parking_lane"),
                                 f.get("parking_reg"), f.get("parking_vehicle"),
                                 f.get("parking_no") and f"編號 {f['parking_no']}"], " ")
    out["land_share_text"] = _join([f.get("land_ownership"), f.get("land_share")], " ")
    sqm2ping = lambda v: f"{_n(v, 0):,} 元/㎡（約 {_n(float(v) * 3.305785 / 10000, 2)} 萬/坪）"
    out["land_value_text"] = sqm2ping(f["land_value_per_sqm"]) if f.get("land_value_per_sqm") else None
    out["land_price_text"] = sqm2ping(f["land_price_per_sqm"]) if f.get("land_price_per_sqm") else None
    if f.get("acquire_date"):
        out["acquire_text"] = _join([f["acquire_date"], f.get("acquire_reason"),
                                     f.get("holding_years") is not None and f"持有約 {f['holding_years']} 年"], "　")
    kinds = f.get("encumbrance_kinds") or []
    if kinds:
        amt = f.get("encumbrance_amount")
        out["encumbrance_text"] = "、".join(kinds) + (f"（擔保約 {_n(float(amt) / 10000, 0):,} 萬）" if amt else "")
    out["build_share_text"] = f.get("build_share")
    out["deed_date"] = f.get("deed_date")
    out["risk_notes"] = f.get("risk_notes")
    fee = f.get("mgmt_fee")
    out["mgmt_text"] = _join([f.get("management"),
                              fee not in (None, "") and f"{_n(fee)} 元" + (f"／{f['mgmt_period']}" if f.get("mgmt_period") else "")], " ")
    return out


def missing_required(values: dict) -> list:
    land_only = not values.get("build_area_main") and not values.get("layout") and bool(
        values.get("land_area_all") or values.get("land_area_share"))
    return [k for k in REQUIRED
            if values.get(k) in (None, "") and not (land_only and k in LAND_ONLY_SKIP)]
