"""上架前風險提示：用謄本解析結果＋你填的委託資料，抓幾個常見的上架地雷。

🔴＝建議先處理再上架　🟡＝請人工確認　🟢＝通過
提示原則上僅供內部檢查，實際仍以地政機關資料、法令與專業意見為準。
"""
from datetime import date

FRESH_DAYS = 30      # 謄本超過 30 天，很多流程會要求重新申請
AREA_TOL = 0.03      # 坪數落差容忍 3%


def check(deed: dict, form: dict = None, today: date = None) -> list:
    """deed：謄本解析結果（建物或土地，可以合併）。form：委託／表單資料（可選）。
    回傳 [{"level": "red|yellow|green", "item": 名稱, "msg": 說明}]。"""
    today = today or date.today()
    form = form or {}
    out = []

    def add(level, item, msg):
        out.append({"level": level, "item": item, "msg": msg})

    if not deed:
        add("yellow", "謄本", "還沒上傳謄本，以下風險都無法自動判斷")
        return out

    # 1. 短期交易（房地合一稅：持有期間短稅率高，售屋人需自行評估並申報）
    if deed.get("short_term"):
        add("red", "短期交易", f"現任所有權人 {deed.get('acquire_date')} 取得（{deed.get('acquire_reason') or '原因未解析'}），"
                             f"持有約 {deed.get('holding_years')} 年，未滿 1 年 → 請提醒屋主房地合一稅負與申報")
    elif deed.get("acquire_date"):
        add("green", "短期交易", f"{deed['acquire_date']} 取得，持有約 {deed.get('holding_years')} 年")
    else:
        add("yellow", "短期交易", "謄本沒有所有權部的取得日（可能只申請標示部）→ 請人工確認")

    # 2. 他項權利（抵押、查封、預告登記…需要向買方揭露）
    kinds = deed.get("encumbrance_kinds")
    if kinds:
        amt = deed.get("encumbrance_amount")
        add("red", "他項權利", f"有 {'、'.join(kinds)}" + (f"，擔保債權總金額約 {amt:,} 元" if amt else "")
                             + " → 需揭露，並核對屋主說的貸款餘額")
    elif deed.get("acquire_date"):
        add("green", "他項權利", "謄本沒有他項權利記載")
    else:
        add("yellow", "他項權利", "看不到他項權利部 → 無法判斷「沒有抵押」，請確認謄本是否完整")

    # 3. 謄本鮮度
    if deed.get("deed_date"):
        age = (today - date.fromisoformat(deed["deed_date"])).days
        if age > FRESH_DAYS:
            add("yellow", "謄本鮮度", f"謄本是 {age} 天前的（{deed['deed_date']}），超過 {FRESH_DAYS} 天，建議重新申請")
        else:
            add("green", "謄本鮮度", f"謄本 {age} 天前申請")
    else:
        add("yellow", "謄本鮮度", "謄本上找不到查詢／列印時間")

    # 4. 坪數落差（廣告坪數和謄本不符，單價也會算錯）
    for key, label in (("build_area_total", "建物權狀坪數"), ("land_area_share", "土地持分坪數")):
        a = deed.get(key)
        b = form.get(f"claimed_{key}")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0:
            diff = abs(a - b) / a
            add("red" if diff > AREA_TOL else "green", f"坪數核對（{label}）",
                f"謄本 {a} 坪、委託書 {b} 坪，相差 {diff:.1%}" + (" → 廣告不實風險" if diff > AREA_TOL else ""))

    # 5. 權利範圍是持分
    if deed.get("land_ownership") == "持分" or (deed.get("build_share") and deed["build_share"] not in ("全部", "1分之1")):
        add("yellow", "持分", "土地或建物是持分 → 確認所有共有人都同意出售（委託書是否都簽了）")
    return out


def summary(items: list) -> str:
    reds = sum(i["level"] == "red" for i in items)
    yellows = sum(i["level"] == "yellow" for i in items)
    if reds:
        return f"🔴 {reds} 項紅燈，建議先處理再上架"
    if yellows:
        return f"🟡 {yellows} 項請人工確認"
    return "🟢 全部通過"


def text_for_sheet(items: list) -> str:
    icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    return "\n".join(f"{icon[i['level']]} {i['item']}：{i['msg']}" for i in items if i["level"] != "green")
