"""選用：用 Claude 產生案名與銷售重點。沒設 ANTHROPIC_API_KEY 就用簡單規則代替。"""
import json
import logging
import os

log = logging.getLogger(__name__)

MODEL = os.getenv("PSHEET_AI_MODEL", "claude-sonnet-5-5")
GUIDE = ("你是台灣的資深房仲文案。只根據提供的物件資料寫，不要編造資料裡沒有的優點、"
         "學區、景觀或數字；不寫保證增值、投報率等誇大或不實的字眼。")

_client = None


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _ask(prompt: str, max_tokens: int = 512) -> str:
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()
    msg = _client.messages.create(model=MODEL, max_tokens=max_tokens, system=GUIDE,
                                  messages=[{"role": "user", "content": prompt}],
                                  output_config={"effort": "low"})
    if msg.stop_reason == "refusal":
        return ""
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def _facts(data: dict) -> dict:
    keep = ("address", "case_type", "total_price", "build_area_main", "build_area_sub", "build_area_pub",
            "land_area_all", "land_area_share", "floor_current", "floor_above", "room_count", "hall_count",
            "bath_count", "structure", "finish_year", "parking", "management", "is_corner",
            "nearby_park", "nearby_station", "nearby_market", "school_elem", "school_mid", "land_zone_name")
    return {k: data[k] for k in keep if data.get(k) not in (None, "")}


def fallback_name(data: dict) -> str:
    """不用 AI 的案名：區域＋型態＋格局。"""
    import re
    addr = data.get("address") or ""
    m = re.search(r"^(?:[一-鿿]{2}[縣市])?([一-鿿]{1,3}?[鄉鎮市區])", addr)
    area = m.group(1) if m else ""
    layout = f"{data['room_count']}房" if data.get("room_count") else ""
    return f"{area}{layout}{data.get('case_type') or '好屋'}".strip() or "精選物件"


def case_name(data: dict) -> str:
    if not enabled():
        return fallback_name(data)
    try:
        prompt = ("請為這個物件取一個 10～18 個中文字的案名，突出資料中真實存在的特色。只回傳案名本身。\n"
                  + json.dumps(_facts(data), ensure_ascii=False))
        return _ask(prompt, 128).strip("「」『』\"' \n") or fallback_name(data)
    except Exception as e:
        log.warning("AI 案名失敗：%s", e)
        return fallback_name(data)


def sell_points(data: dict) -> str:
    facts = _facts(data)
    if not enabled():
        lines = []
        if facts.get("room_count"):
            lines.append(f"◆ {facts['room_count']}房格局，生活機能完整")
        if facts.get("nearby_station"):
            lines.append(f"◆ 鄰近 {facts['nearby_station']}")
        if facts.get("parking") == "有":
            lines.append("◆ 附車位")
        return "\n".join(lines)
    try:
        prompt = ("請根據物件資料寫 3～5 條銷售重點，每條以「◆」開頭，15～30 字。只回傳條列。\n"
                  + json.dumps(facts, ensure_ascii=False))
        return _ask(prompt, 600)
    except Exception as e:
        log.warning("AI 銷售重點失敗：%s", e)
        return ""
