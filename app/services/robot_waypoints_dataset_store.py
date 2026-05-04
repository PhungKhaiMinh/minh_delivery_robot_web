"""
Dataset waypoint robot: mỗi điểm có tên + tọa độ center (x,y) và right_side (x,y) riêng.
Đồ thị điều hướng: cạnh waypoint–waypoint + cạnh pickup↔waypoint vào mạng (pickup_portal_edges).
Lưu cạnh WP–WP Firestore dạng [{u,v}] — không dùng mảng lồng mảng.
Lưu admin_config / robot_waypoints_dataset — Firestore hoặc DB JSON local.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.db_service import db

ADMIN_CONFIG_COLLECTION = "admin_config"
WAYPOINT_DATASET_DOC_ID = "robot_waypoints_dataset"

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MAX_WAYPOINTS = 500
_MAX_WAYPOINT_EDGES = 4000
_MAX_PICKUP_PORTALS = 800


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


def _normalize_waypoint_edge(item: Any, valid_ids: Set[str]) -> Optional[Tuple[str, str]]:
    """
    Chuẩn hóa một cạnh từ client hoặc DB.
    Chấp nhận: [id1, id2], {"u","v"} (Firestore), {"from","to"} (tùy chọn).
    """
    a: Optional[str] = None
    b: Optional[str] = None
    if isinstance(item, (list, tuple)) and len(item) == 2:
        a, b = str(item[0]).strip(), str(item[1]).strip()
    elif isinstance(item, dict):
        if "u" in item and "v" in item:
            a, b = str(item["u"]).strip(), str(item["v"]).strip()
        elif "from" in item and "to" in item:
            a, b = str(item["from"]).strip(), str(item["to"]).strip()
    if a is None or b is None:
        return None
    if not _ID_RE.match(a) or not _ID_RE.match(b) or a == b:
        return None
    if a not in valid_ids or b not in valid_ids:
        return None
    return (a, b) if a < b else (b, a)


def _edges_for_storage(edges_pairs: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    """Firestore: không cho [[a,b],...]; chỉ cho [{u,v},...]."""
    return [{"u": p[0], "v": p[1]} for p in edges_pairs]


def _normalize_portal(item: Any, wp_ids: Set[str], pickup_ids: Set[str]) -> Optional[Tuple[str, str]]:
    if not isinstance(item, dict):
        return None
    pid = str(item.get("pickup_id", "")).strip()
    wid = str(item.get("waypoint_id", "")).strip()
    if not _ID_RE.match(pid) or not _ID_RE.match(wid):
        return None
    if pid not in pickup_ids or wid not in wp_ids:
        return None
    return (pid, wid)


def get_waypoints_bundle() -> Dict[str, Any]:
    """waypoints + edges (waypoint–waypoint) + pickup_portal_edges (pickup↔waypoint)."""
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(WAYPOINT_DATASET_DOC_ID).get()
    waypoints = get_waypoints_dataset()
    wp_ids = {str(w["id"]) for w in waypoints}
    edges: List[List[str]] = []
    portals: List[Dict[str, str]] = []
    if doc:
        raw_e = doc.get("edges")
        if isinstance(raw_e, list):
            seen: set[Tuple[str, str]] = set()
            for it in raw_e:
                p = _normalize_waypoint_edge(it, wp_ids)
                if p and p not in seen and len(seen) < _MAX_WAYPOINT_EDGES:
                    seen.add(p)
                    edges.append([p[0], p[1]])
        raw_p = doc.get("pickup_portal_edges")
        if isinstance(raw_p, list):
            from app.services.pickup_locations_store import list_pickup_locations_admin

            pids = {str(p["id"]) for p in list_pickup_locations_admin()}
            seen_p: set[Tuple[str, str]] = set()
            for it in raw_p:
                pr = _normalize_portal(it, wp_ids, pids)
                if pr and pr not in seen_p and len(seen_p) < _MAX_PICKUP_PORTALS:
                    seen_p.add(pr)
                    portals.append({"pickup_id": pr[0], "waypoint_id": pr[1]})
    return {"waypoints": waypoints, "edges": edges, "pickup_portal_edges": portals}


def set_waypoint_traversal_graph(edges_raw: Any, portals_raw: Any) -> tuple[bool, str]:
    """
    Ghi edges + pickup_portal_edges (merge). Chỉ giữ cạnh hợp lệ theo waypoint hiện tại và catalog pickup.
    Trả về (True, "") hoặc (False, thông báo lỗi ghi / chuẩn hóa).
    """
    waypoints = get_waypoints_dataset()
    wp_ids = {str(w["id"]) for w in waypoints}
    from app.services.pickup_locations_store import list_pickup_locations_admin

    pickup_ids = {str(p["id"]) for p in list_pickup_locations_admin()}

    edges_out_pairs: List[Tuple[str, str]] = []
    seen_e: set[Tuple[str, str]] = set()
    if isinstance(edges_raw, list):
        for it in edges_raw:
            p = _normalize_waypoint_edge(it, wp_ids)
            if p and p not in seen_e and len(seen_e) < _MAX_WAYPOINT_EDGES:
                seen_e.add(p)
                edges_out_pairs.append(p)

    portals_out: List[Dict[str, str]] = []
    seen_p: set[Tuple[str, str]] = set()
    if isinstance(portals_raw, list):
        for it in portals_raw:
            pr = _normalize_portal(it, wp_ids, pickup_ids)
            if pr and pr not in seen_p and len(seen_p) < _MAX_PICKUP_PORTALS:
                seen_p.add(pr)
                portals_out.append({"pickup_id": pr[0], "waypoint_id": pr[1]})

    if isinstance(edges_raw, list) and len(edges_raw) > 0 and len(edges_out_pairs) == 0:
        return (
            False,
            (
                "Không có cạnh waypoint–waypoint nào hợp lệ: id trên cạnh không khớp dataset trên server. "
                "Lưu dataset waypoint ở trang Orders trước, hoặc bấm Lưu đồ thị khi trang đã tải được waypoint từ server."
            ),
        )

    if isinstance(portals_raw, list) and len(portals_raw) > 0 and len(portals_out) == 0:
        return (
            False,
            "Không có cạnh pickup↔waypoint nào hợp lệ: id pickup hoặc waypoint không khớp catalog / dataset.",
        )

    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(WAYPOINT_DATASET_DOC_ID)
    ok = ref.set(
        {"edges": _edges_for_storage(edges_out_pairs), "pickup_portal_edges": portals_out},
        merge=True,
    )
    if ok:
        return True, ""
    err = (getattr(ref, "last_set_error", None) or "").strip()
    if err:
        print(f"[WAYPOINT GRAPH] set failed: {err}")
    return False, err or "Không ghi được document (Firestore hoặc file JSON)."


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
