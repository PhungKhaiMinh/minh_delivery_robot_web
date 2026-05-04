"""
Hoạch định lộ trình test: điểm đầu / cuối = địa điểm nhận sách (tọa center x,y),
các đỉnh trung gian = waypoint robot (center).

- Cạnh waypoint–waypoint: do admin nối trên Tracking (đường robot được phép đi).
- Nối pickup đầu/cuối vào đồ thị: **tự động** tới mọi waypoint là **đầu mút** của ít nhất một cạnh,
  trọng số = khoảng cách Euclid (m) từ center pickup tới center waypoint (và ngược lại tới đích).
  Dijkstra chọn cặp vào/ra tối ưu theo **tổng** đường đi ngắn nhất.

Không có cạnh trực tiếp điểm đầu → điểm cuối.

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


def _dijkstra_sparse(
    adj: List[List[Tuple[int, float]]],
    src: int,
    dst: int,
) -> Optional[List[int]]:
    n = len(adj)
    if src < 0 or src >= n or dst < 0 or dst >= n:
        return None
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
        for v, w in adj[u]:
            nd = d + w
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

    bundle = get_waypoints_bundle()
    wps = bundle.get("waypoints") or []
    edges = bundle.get("edges") or []

    if not wps:
        return None

    # Chỉ các waypoint có center/right_side hợp lệ (đã lọc bởi store)
    wp_list: List[Dict[str, Any]] = list(wps)
    wp_ids = [str(w["id"]) for w in wp_list]
    id_to_wp = {str(w["id"]): w for w in wp_list}
    w = len(wp_ids)
    if w == 0:
        return None

    # Chỉ số: 0 = start pickup, 1..w = waypoint theo thứ tự wp_list, w+1 = end pickup
    SRC = 0
    DST = w + 1
    n = w + 2
    adj: List[List[Tuple[int, float]]] = [[] for _ in range(n)]

    def wp_index(wid: str) -> int:
        return 1 + wp_ids.index(wid)

    def center_xy(meta: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        c = meta.get("center") or {}
        try:
            return (float(c["x"]), float(c["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    def add_edge(u: int, v: int, d: float) -> None:
        if not math.isfinite(d) or d <= 0:
            return
        adj[u].append((v, d))
        adj[v].append((u, d))

    # Cạnh waypoint–waypoint + thu thập đỉnh thuộc đồ thị (đầu mút cạnh user)
    wp_on_graph: set[str] = set()
    for pair in edges:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        a, b = str(pair[0]), str(pair[1])
        if a not in id_to_wp or b not in id_to_wp:
            continue
        pa = center_xy(id_to_wp[a])
        pb = center_xy(id_to_wp[b])
        if pa is None or pb is None:
            continue
        wp_on_graph.add(a)
        wp_on_graph.add(b)
        ia, ib = wp_index(a), wp_index(b)
        add_edge(ia, ib, _euclid(pa, pb))

    if not wp_on_graph:
        return None

    sp = by_pid[sid]
    ep = by_pid[eid]
    start_pt = (float(sp["x"]), float(sp["y"]))
    end_pt = (float(ep["x"]), float(ep["y"]))

    for wid in wp_on_graph:
        wpt = id_to_wp.get(wid)
        if wpt is None:
            continue
        wc = center_xy(wpt)
        if wc is None:
            continue
        wi = wp_index(wid)
        add_edge(SRC, wi, _euclid(start_pt, wc))
        add_edge(wi, DST, _euclid(wc, end_pt))

    path_idx = _dijkstra_sparse(adj, SRC, DST)
    if path_idx is None:
        return None

    nodes_meta: List[Dict[str, Any]] = [
        {
            "kind": "pickup",
            "id": sid,
            "name": sp.get("name", sid),
            "cx": float(sp["x"]),
            "cy": float(sp["y"]),
            "mx": float(sp.get("x_margin", sp["x"])),
            "my": float(sp.get("y_margin", sp["y"])),
        }
    ]
    for wid in wp_ids:
        wp = id_to_wp[wid]
        c = wp.get("center") or {}
        rs = wp.get("right_side") or {}
        nodes_meta.append(
            {
                "kind": "waypoint",
                "id": wid,
                "name": str(wp.get("name", wid)),
                "cx": float(c["x"]),
                "cy": float(c["y"]),
                "mx": float(rs["x"]),
                "my": float(rs["y"]),
            }
        )
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

    def edge_len(u: int, v: int) -> float:
        pu = (nodes_meta[u]["cx"], nodes_meta[u]["cy"])
        pv = (nodes_meta[v]["cx"], nodes_meta[v]["cy"])
        return _euclid(pu, pv)

    total_len = sum(edge_len(path_idx[i], path_idx[i + 1]) for i in range(len(path_idx) - 1))

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
