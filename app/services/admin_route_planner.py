"""
Hoạch định lộ trình test: điểm đầu / cuối = địa điểm nhận sách (pickup, center x,y);
đỉnh trung gian = waypoint (center / right_side).

Đồ thị do admin định nghĩa trên Tracking:
  - Cạnh **waypoint–waypoint** (trọng số Euclid mét giữa center).
  - Cạnh **pickup ↔ waypoint** (`pickup_portal_edges`, trọng số Euclid pickup–center WP).

Pickup được coi như đỉnh trên cùng mạng: chỉ đi được qua các cạnh đã khai báo.
Dijkstra trên đồ thị vô hướng (mỗi cạnh thêm hai chiều) tìm đường ngắn nhất giữa hai pickup.

Kết quả: stage_x/y = center, stage_x_margin/y_margin = right_side (waypoint) hoặc margin (pickup).
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Dict, List, Optional, Tuple

from app.services.pickup_locations_store import list_pickup_locations_admin
from app.services.robot_waypoints_dataset_store import get_waypoints_bundle


def _euclid(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dijkstra_str(
    adj: Dict[str, List[Tuple[str, float]]],
    src: str,
    dst: str,
) -> Optional[List[str]]:
    if src == dst:
        return [src]
    dist: Dict[str, float] = {src: 0.0}
    prev: Dict[str, str] = {}
    pq: List[Tuple[float, str]] = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        if u == dst:
            break
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None
    path: List[str] = []
    cur = dst
    while cur != src:
        path.append(cur)
        cur = prev[cur]
    path.append(src)
    path.reverse()
    return path


def _add_undirected(adj: Dict[str, List[Tuple[str, float]]], u: str, v: str, weight: float) -> None:
    if not math.isfinite(weight) or weight <= 0:
        return
    adj.setdefault(u, []).append((v, weight))
    adj.setdefault(v, []).append((u, weight))


def plan_field_route(start_pickup_id: str, end_pickup_id: str) -> Optional[Dict[str, Any]]:
    """
    Trả về:
      ``ordered_stops``: danh sách {kind, id, name, x, y, x_margin, y_margin} theo thứ tự robot,
      ``payload``: {stage_x, stage_y, stage_x_margin, stage_y_margin} cùng độ dài.
    """
    sid = str(start_pickup_id).strip()
    eid = str(end_pickup_id).strip()
    if not sid or not eid or sid == eid:
        return None

    pickups = list_pickup_locations_admin()
    by_pid = {str(p["id"]): p for p in pickups}
    if sid not in by_pid or eid not in by_pid:
        return None

    bundle = get_waypoints_bundle()
    wps = bundle.get("waypoints") or []
    edges = bundle.get("edges") or []
    portals = bundle.get("pickup_portal_edges") or []

    if not wps:
        return None

    wp_list: List[Dict[str, Any]] = list(wps)
    id_to_wp = {str(w["id"]): w for w in wp_list}

    def center_xy_wp(meta: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        c = meta.get("center") or {}
        try:
            return (float(c["x"]), float(c["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    def pickup_xy(p: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        try:
            return (float(p["x"]), float(p["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    adj: Dict[str, List[Tuple[str, float]]] = {}

    for pair in edges:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        a, b = str(pair[0]), str(pair[1])
        if a not in id_to_wp or b not in id_to_wp:
            continue
        pa = center_xy_wp(id_to_wp[a])
        pb = center_xy_wp(id_to_wp[b])
        if pa is None or pb is None:
            continue
        _add_undirected(adj, a, b, _euclid(pa, pb))

    for pr in portals:
        if not isinstance(pr, dict):
            continue
        pid = str(pr.get("pickup_id", "")).strip()
        wid = str(pr.get("waypoint_id", "")).strip()
        if pid not in by_pid or wid not in id_to_wp:
            continue
        pp = pickup_xy(by_pid[pid])
        pw = center_xy_wp(id_to_wp[wid])
        if pp is None or pw is None:
            continue
        _add_undirected(adj, pid, wid, _euclid(pp, pw))

    if not adj:
        return None

    path_ids = _dijkstra_str(adj, sid, eid)
    if path_ids is None:
        return None

    def node_meta(nid: str) -> Optional[Dict[str, Any]]:
        if nid in id_to_wp:
            wp = id_to_wp[nid]
            c = wp.get("center") or {}
            rs = wp.get("right_side") or {}
            try:
                return {
                    "kind": "waypoint",
                    "id": nid,
                    "name": str(wp.get("name", nid)),
                    "cx": float(c["x"]),
                    "cy": float(c["y"]),
                    "mx": float(rs["x"]),
                    "my": float(rs["y"]),
                }
            except (KeyError, TypeError, ValueError):
                return None
        if nid in by_pid:
            p = by_pid[nid]
            try:
                return {
                    "kind": "pickup",
                    "id": nid,
                    "name": p.get("name", nid),
                    "cx": float(p["x"]),
                    "cy": float(p["y"]),
                    "mx": float(p.get("x_margin", p["x"])),
                    "my": float(p.get("y_margin", p["y"])),
                }
            except (KeyError, TypeError, ValueError):
                return None
        return None

    ordered_meta: List[Dict[str, Any]] = []
    for nid in path_ids:
        m = node_meta(nid)
        if m is None:
            return None
        ordered_meta.append(m)

    ordered_stops: List[Dict[str, Any]] = []
    for st in ordered_meta:
        ordered_stops.append(
            {
                "kind": st["kind"],
                "id": st["id"],
                "name": st["name"],
                "x": st["cx"],
                "y": st["cy"],
                "x_margin": st["mx"],
                "y_margin": st["my"],
            }
        )
    sx: List[float] = []
    sy: List[float] = []
    smx: List[float] = []
    smy: List[float] = []
    for st in ordered_meta:
        sx.append(st["cx"])
        sy.append(st["cy"])
        smx.append(st["mx"])
        smy.append(st["my"])

    total_len = sum(
        _euclid(
            (ordered_meta[i]["cx"], ordered_meta[i]["cy"]),
            (ordered_meta[i + 1]["cx"], ordered_meta[i + 1]["cy"]),
        )
        for i in range(len(ordered_meta) - 1)
    )

    return {
        "ordered_stops": ordered_stops,
        "total_length_m": total_len,
        "payload": {
            "stage_x": sx,
            "stage_y": sy,
            "stage_x_margin": smx,
            "stage_y_margin": smy,
        },
    }
