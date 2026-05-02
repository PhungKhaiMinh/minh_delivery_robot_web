"""
Danh sách địa điểm nhận sách (catalog) + ghi đè tọa độ local (x, y).
Catalog: admin_config / pickup_locations_catalog — nếu trống thì dùng CAMPUS_LOCATIONS trong config.
XY: admin_config / pickup_locations_xy → overrides theo id.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from app.config import CAMPUS_LOCATIONS
from app.services.db_service import db
from app.services.pathfinding_service import gps_to_local

ADMIN_CONFIG_COLLECTION = "admin_config"
PICKUP_CATALOG_DOC_ID = "pickup_locations_catalog"
PICKUP_XY_DOC_ID = "pickup_locations_xy"

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def get_catalog_locations() -> List[Dict[str, Any]]:
    """Danh sách {id, name, lat, lng} — từ DB hoặc bản sao CAMPUS_LOCATIONS."""
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_CATALOG_DOC_ID).get()
    if not doc:
        return copy.deepcopy(list(CAMPUS_LOCATIONS))
    raw = doc.get("locations")
    if not isinstance(raw, list) or len(raw) == 0:
        return copy.deepcopy(list(CAMPUS_LOCATIONS))
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        norm = _normalize_catalog_item(item)
        if norm:
            out.append(norm)
    return out if out else copy.deepcopy(list(CAMPUS_LOCATIONS))


def _normalize_catalog_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lid = str(item.get("id", "")).strip()
    name = str(item.get("name", "")).strip()
    if not lid or not _ID_RE.match(lid):
        return None
    if not name or len(name) > 200:
        return None
    try:
        lat = float(item["lat"])
        lng = float(item["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    return {"id": lid, "name": name, "lat": lat, "lng": lng}


def set_pickup_catalog(locations: List[Dict[str, Any]]) -> bool:
    """Ghi đè toàn bộ catalog (đã chuẩn hóa, ít nhất 1 địa điểm)."""
    seen: set[str] = set()
    clean: List[Dict[str, Any]] = []
    for item in locations:
        if not isinstance(item, dict):
            continue
        norm = _normalize_catalog_item(item)
        if not norm:
            return False
        if norm["id"] in seen:
            return False
        seen.add(norm["id"])
        clean.append(norm)
    if len(clean) < 1:
        return False
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_CATALOG_DOC_ID)
    if not ref.set({"locations": clean}, merge=True):
        return False
    _prune_xy_overrides_to_ids(seen)
    return True


def _prune_xy_overrides_to_ids(allowed: set[str]) -> None:
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_XY_DOC_ID).get()
    if not doc:
        return
    raw = doc.get("overrides")
    if not isinstance(raw, dict):
        return
    pruned = {k: v for k, v in raw.items() if str(k) in allowed and isinstance(v, dict)}
    db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_XY_DOC_ID).set(
        {"overrides": pruned}, merge=True
    )


def _allowed_location_ids() -> set[str]:
    return {str(loc["id"]) for loc in get_catalog_locations()}


def _finite_xy_pair(v: Dict[str, Any], xk: str, yk: str) -> Optional[Tuple[float, float]]:
    try:
        x = float(v[xk])
        y = float(v[yk])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if abs(x) > 1e6 or abs(y) > 1e6:
        return None
    return (x, y)


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
        sl = str(lid)
        if sl not in allowed:
            continue
        if not isinstance(v, dict):
            continue
        pair = _finite_xy_pair(v, "x", "y")
        if pair is None:
            continue
        x, y = pair
        entry: Dict[str, float] = {"x": x, "y": y}
        mpair = _finite_xy_pair(v, "x_margin", "y_margin")
        if mpair is not None:
            entry["x_margin"], entry["y_margin"] = mpair
        out[sl] = entry
    return out


def list_pickup_locations_admin() -> List[Dict[str, Any]]:
    """Mỗi phần tử: id, name, lat, lng, default_x/y, default_x_margin/y_margin, x, y, x_margin, y_margin, overridden."""
    overrides = get_pickup_xy_overrides()
    rows: List[Dict[str, Any]] = []
    for loc in get_catalog_locations():
        lid = str(loc["id"])
        lat = float(loc["lat"])
        lng = float(loc["lng"])
        def_x, def_y = gps_to_local(lat, lng)
        def_mx, def_my = def_x, def_y
        ov = overrides.get(lid)
        if ov:
            eff_x, eff_y = float(ov["x"]), float(ov["y"])
            if "x_margin" in ov and "y_margin" in ov:
                eff_mx = float(ov["x_margin"])
                eff_my = float(ov["y_margin"])
            else:
                eff_mx, eff_my = eff_x, eff_y
            overridden = True
        else:
            eff_x, eff_y = def_x, def_y
            eff_mx, eff_my = def_mx, def_my
            overridden = False
        rows.append(
            {
                "id": lid,
                "name": loc["name"],
                "lat": lat,
                "lng": lng,
                "default_x": def_x,
                "default_y": def_y,
                "default_x_margin": def_mx,
                "default_y_margin": def_my,
                "x": eff_x,
                "y": eff_y,
                "x_margin": eff_mx,
                "y_margin": eff_my,
                "overridden": overridden,
            }
        )
    return rows


def set_pickup_xy_overrides(raw: Dict[str, Any]) -> bool:
    """Ghi đè toàn bộ map overrides (chỉ id thuộc catalog hiện tại). Có thể kèm x_margin, y_margin."""
    allowed = _allowed_location_ids()
    clean: Dict[str, Dict[str, float]] = {}
    for lid, v in raw.items():
        sl = str(lid)
        if sl not in allowed:
            continue
        if not isinstance(v, dict):
            continue
        pair = _finite_xy_pair(v, "x", "y")
        if pair is None:
            continue
        x, y = pair
        entry: Dict[str, float] = {"x": x, "y": y}
        mpair = _finite_xy_pair(v, "x_margin", "y_margin")
        if mpair is not None:
            entry["x_margin"], entry["y_margin"] = mpair
        clean[sl] = entry
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(PICKUP_XY_DOC_ID)
    return ref.set({"overrides": clean}, merge=True)


def apply_pickup_catalog_and_overrides(
    locations: List[Dict[str, Any]],
    overrides: Optional[Dict[str, Any]],
) -> bool:
    """Cập nhật catalog rồi ghi overrides (toàn bộ map overrides từ client)."""
    if not set_pickup_catalog(locations):
        return False
    if overrides is None:
        return True
    return set_pickup_xy_overrides(overrides)
