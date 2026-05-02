"""
Dataset waypoint robot: mỗi điểm có tên + tọa độ center (x,y) và right_side (x,y) riêng.
Lưu admin_config / robot_waypoints_dataset — Firestore hoặc DB JSON local.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from app.services.db_service import db

ADMIN_CONFIG_COLLECTION = "admin_config"
WAYPOINT_DATASET_DOC_ID = "robot_waypoints_dataset"

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MAX_WAYPOINTS = 500


def _finite_xy(x: float, y: float) -> bool:
    return bool(math.isfinite(x) and math.isfinite(y) and abs(x) <= 1e6 and abs(y) <= 1e6)


def _normalize_xy_dict(d: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(d, dict):
        return None
    try:
        x = float(d["x"])
        y = float(d["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _finite_xy(x, y):
        return None
    return (x, y)


def _normalize_waypoint(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    lid = str(item.get("id", "")).strip()
    if not _ID_RE.match(lid):
        return None
    name = str(item.get("name", "")).strip()
    if not name or len(name) > 200:
        return None

    ce = item.get("center")
    rs = item.get("right_side")
    c = _normalize_xy_dict(ce)
    r = _normalize_xy_dict(rs)
    if c is not None and r is not None:
        return {
            "id": lid,
            "name": name,
            "center": {"x": c[0], "y": c[1]},
            "right_side": {"x": r[0], "y": r[1]},
        }

    # Legacy: kind + flat x, y → nhân đôi vào cả hai (để không mất dữ liệu; admin có thể sửa sau)
    try:
        ox = float(item["x"])
        oy = float(item["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _finite_xy(ox, oy):
        return None
    return {
        "id": lid,
        "name": name,
        "center": {"x": ox, "y": oy},
        "right_side": {"x": ox, "y": oy},
    }


def get_waypoints_dataset() -> List[Dict[str, Any]]:
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(WAYPOINT_DATASET_DOC_ID).get()
    if not doc:
        return []
    raw = doc.get("waypoints")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in raw:
        n = _normalize_waypoint(it)
        if n:
            out.append(n)
    return out


def set_waypoints_dataset(raw_list: Any) -> bool:
    if not isinstance(raw_list, list) or len(raw_list) > _MAX_WAYPOINTS:
        return False
    seen: set[str] = set()
    clean: List[Dict[str, Any]] = []
    for it in raw_list:
        n = _normalize_waypoint(it)
        if not n:
            return False
        if n["id"] in seen:
            return False
        seen.add(n["id"])
        clean.append(n)
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(WAYPOINT_DATASET_DOC_ID)
    return ref.set({"waypoints": clean}, merge=True)
