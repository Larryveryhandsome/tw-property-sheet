"""物調表資料模型。所有欄位都可以留空，必填欄位缺的會在 Excel 標黃。"""
from typing import List, Optional

from pydantic import BaseModel


class PropertyForm(BaseModel):
    # 基本
    date_today: Optional[str] = None
    case_number: Optional[str] = None
    case_name: Optional[str] = None
    address: Optional[str] = None
    total_price: Optional[float] = None      # 萬元
    agent: Optional[str] = None
    source: Optional[str] = None
    sell_reason: Optional[str] = None
    # 銷售狀態
    case_type: Optional[str] = None
    status: Optional[str] = None
    exposure: Optional[str] = None
    escrow: Optional[str] = None
    handover: Optional[str] = None
    visit_method: Optional[str] = None
    visit_contact: Optional[str] = None
    visit_key_no: Optional[str] = None
    loan: Optional[str] = None
    # 土地
    land_section: Optional[str] = None
    land_number: Optional[str] = None
    land_ownership: Optional[str] = None
    land_area_all: Optional[float] = None    # 坪
    land_area_share: Optional[float] = None
    land_zone_type: Optional[str] = None     # 都計內／都計外
    land_zone_name: Optional[str] = None
    land_use_type: Optional[str] = None
    vacant_ratio: Optional[str] = None
    road_frontage: Optional[float] = None    # 米
    width: Optional[float] = None
    depth: Optional[float] = None
    # 建物
    build_number: Optional[str] = None
    build_area_main: Optional[float] = None
    build_area_sub: Optional[float] = None
    build_area_pub: Optional[float] = None
    floor_above: Optional[int] = None
    floor_below: Optional[int] = None
    floor_current: Optional[int] = None
    structure: Optional[str] = None
    main_use: Optional[str] = None
    finish_year: Optional[int] = None        # 民國年
    finish_month: Optional[int] = None
    finish_day: Optional[int] = None
    room_count: Optional[int] = None
    hall_count: Optional[int] = None
    bath_count: Optional[int] = None
    appearance: Optional[str] = None
    orientation_build: Optional[str] = None
    orientation_window: Optional[str] = None
    orientation_door: Optional[str] = None
    is_corner: Optional[str] = None
    has_courtyard: Optional[str] = None
    addon_type: Optional[str] = None
    # 車位、管理
    parking: Optional[str] = None
    parking_type: Optional[str] = None
    parking_no: Optional[str] = None
    parking_lane: Optional[str] = None
    parking_reg: Optional[str] = None
    parking_vehicle: Optional[str] = None
    management: Optional[str] = None
    mgmt_fee: Optional[float] = None
    mgmt_period: Optional[str] = None
    utilities: Optional[str] = None
    # 周邊
    village: Optional[str] = None
    school_elem: Optional[str] = None
    school_mid: Optional[str] = None
    nearby_park: Optional[str] = None
    nearby_station: Optional[str] = None
    nearby_market: Optional[str] = None
    others: Optional[str] = None
    # 文字
    sell_points: Optional[str] = None
    notes: Optional[str] = None

    missing_fields: List[str] = []
