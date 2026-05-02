"""
Dataset waypoint (tên, loại center|right_side, x, y local) cho robot.
Lưu admin_config / robot_waypoints_dataset — Firestore hoặc DB JSON local.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from app.services.db_service import db

ADMIN_CONFIG_COLLECTION = "admin_config"
WAYPOINT_DATASET_DOC_ID = "robot_waypoints_dataset"

VALID_KINDS = frozenset({"center", "right_side"})
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MAX_WAYPOINTS = 500


def _normalize_waypoint(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    lid = str(item.get("id", "")).strip()
    if not _ID_RE.match(lid):
        return None
    name = str(item.get("name", "")).strip()
    if not name or len(name) > 200:
        return None
    kind = str(item.get("kind", "")).strip()
    if kind not in VALID_KINDS:
        return None
    try:
        x = float(item["x"])
        y = float(item["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if abs(x) > 1e6 or abs(y) > 1e6:
        return None
    return {"id": lid, "name": name, "kind": kind, "x": x, "y": y}


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
