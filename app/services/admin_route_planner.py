"""
Hoạch định lộ trình test: điểm đầu / cuối = địa điểm nhận sách (tọa center x,y),
đi qua các waypoint robot (center) — Dijkstra trên đồ thị đầy đủ (khoảng cách Euclid).
Kết quả: stage_x/y = center, stage_x_margin/y_margin = right_side (waypoint) hoặc margin (pickup).
"""

from __future__ import annotations

import heapq
import math
from typing import Any, Dict, List, Optional, Tuple

from app.services.pickup_locations_store import list_pickup_locations_admin
from app.services.robot_waypoints_dataset_store import get_waypoints_dataset


def _euclid(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _dijkstra(
    n: int,
    dist_fn: Any,
    src: int,
    dst: int,
) -> Optional[List[int]]:
    if src == dst:
        return [src]
    dist: Dict[int, float] = {src: 0.0}
    prev: Dict[int, int] = {}
    pq: List[Tuple[float, int]] = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        if u == dst:
            break
        for v in range(n):
            if v == u:
                continue
            nd = d + dist_fn(u, v)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None
    path: List[int] = []
    cur = dst
    while cur != src:
        path.append(cur)
        cur = prev[cur]
    path.append(src)
    path.reverse()
    return path


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

    wps = get_waypoints_dataset()
    if wps is None:
        wps = []

    # nodes: [start_pickup, ...wp centers..., end_pickup] — end luôn index cuối
    nodes_meta: List[Dict[str, Any]] = []
    pts: List[Tuple[float, float]] = []

    sp = by_pid[sid]
    nodes_meta.append(
        {
            "kind": "pickup",
            "id": sid,
            "name": sp.get("name", sid),
            "cx": float(sp["x"]),
            "cy": float(sp["y"]),
            "mx": float(sp.get("x_margin", sp["x"])),
            "my": float(sp.get("y_margin", sp["y"])),
        }
    )
    pts.append((float(sp["x"]), float(sp["y"])))

    for wp in wps:
        wid = str(wp["id"])
        c = wp.get("center") or {}
        rs = wp.get("right_side") or {}
        try:
            cx = float(c["x"])
            cy = float(c["y"])
            mx = float(rs["x"])
            my = float(rs["y"])
        except (KeyError, TypeError, ValueError):
            continue
        nodes_meta.append(
            {
                "kind": "waypoint",
                "id": wid,
                "name": str(wp.get("name", wid)),
                "cx": cx,
                "cy": cy,
                "mx": mx,
                "my": my,
            }
        )
        pts.append((cx, cy))

    ep = by_pid[eid]
    nodes_meta.append(
        {
            "kind": "pickup",
            "id": eid,
            "name": ep.get("name", eid),
            "cx": float(ep["x"]),
            "cy": float(ep["y"]),
            "mx": float(ep.get("x_margin", ep["x"])),
            "my": float(ep.get("y_margin", ep["y"])),
        }
    )
    pts.append((float(ep["x"]), float(ep["y"])))

    n = len(pts)
    if n < 2:
        return None

    def dist_uv(u: int, v: int) -> float:
        return _euclid(pts[u], pts[v])

    path_idx = _dijkstra(n, dist_uv, 0, n - 1)
    if path_idx is None:
        return None

    ordered_meta = [nodes_meta[i] for i in path_idx]
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

    total_len = sum(dist_uv(path_idx[i], path_idx[i + 1]) for i in range(len(path_idx) - 1))

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
