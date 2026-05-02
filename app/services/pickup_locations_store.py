"""
Tọa độ local (x, y) ghi đè cho địa điểm nhận sách (CAMPUS_LOCATIONS).
Lưu trong admin_config / pickup_locations_xy — dùng cho kịch bản tùy chỉnh trên Admin Orders.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

from app.config import CAMPUS_LOCATIONS
from app.services.db_service import db
from app.services.pathfinding_service import gps_to_local

ADMIN_CONFIG_COLLECTION = "admin_config"
PICKUP_XY_DOC_ID = "pickup_locations_xy"


def _allowed_location_ids() -> set[str]:
    return {str(loc["id"]) for loc in CAMPUS_LOCATIONS}


def get_pickup_xy_overrides() -> Dict[str, Dict[str, float]]:
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_XY_DOC_ID).get()
    if not doc:
        return {}
    raw = doc.get("overrides")
    if not isinstance(raw, dict):
        return {}
    allowed = _allowed_location_ids()
    out: Dict[str, Dict[str, float]] = {}
    for lid, v in raw.items():
        if lid not in allowed:
            continue
        if not isinstance(v, dict):
            continue
        try:
            x = float(v["x"])
            y = float(v["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if abs(x) > 1e6 or abs(y) > 1e6:
            continue
        out[str(lid)] = {"x": x, "y": y}
    return out


def list_pickup_locations_admin() -> List[Dict[str, Any]]:
    """Mỗi phần tử: id, name, lat, lng, default_x, default_y, x, y, overridden."""
    overrides = get_pickup_xy_overrides()
    rows: List[Dict[str, Any]] = []
    for loc in CAMPUS_LOCATIONS:
        lid = str(loc["id"])
        lat = float(loc["lat"])
        lng = float(loc["lng"])
        def_x, def_y = gps_to_local(lat, lng)
        ov = overrides.get(lid)
        if ov:
            eff_x, eff_y = float(ov["x"]), float(ov["y"])
            overridden = True
        else:
            eff_x, eff_y = def_x, def_y
            overridden = False
        rows.append(
            {
                "id": lid,
                "name": loc["name"],
                "lat": lat,
                "lng": lng,
                "default_x": def_x,
                "default_y": def_y,
                "x": eff_x,
                "y": eff_y,
                "overridden": overridden,
            }
        )
    return rows


def set_pickup_xy_overrides(raw: Dict[str, Any]) -> bool:
    """Ghi đè toàn bộ map overrides (chỉ id thuộc CAMPUS_LOCATIONS)."""
    allowed = _allowed_location_ids()
    clean: Dict[str, Dict[str, float]] = {}
    for lid, v in raw.items():
        sl = str(lid)
        if sl not in allowed:
            continue
        if not isinstance(v, dict):
            continue
        try:
            x = float(v["x"])
            y = float(v["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if abs(x) > 1e6 or abs(y) > 1e6:
            continue
        clean[sl] = {"x": x, "y": y}
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_XY_DOC_ID)
    return ref.set({"overrides": clean}, merge=True)
