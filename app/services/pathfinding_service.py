"""Dijkstra shortest-path on the campus waypoint graph + ECEF→ENU conversion.

GPS→local XY dùng đúng thuật toán ECEF→ENU (WGS84) giống firmware robot
(serial_gps.hpp  SetReference / ConvertToLocal) để đảm bảo stage_x/stage_y
khớp chính xác với hệ tọa độ mà robot sử dụng.
"""

from __future__ import annotations

import heapq
import math
from typing import Optional

from app.config import (
    CAMPUS_WAYPOINTS,
    CAMPUS_EDGES,
    CAMPUS_LIBRARY_IDX,
    CAMPUS_ORIGIN_LAT,
    CAMPUS_ORIGIN_LON,
    CAMPUS_ORIGIN_ALT,
)

# ---------------------------------------------------------------------------
# Haversine (dùng cho trọng số Dijkstra — không ảnh hưởng stage_x/y)
# ---------------------------------------------------------------------------

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ---------------------------------------------------------------------------
# ECEF → ENU  (WGS84, khớp serial_gps.hpp trên robot firmware)
# ---------------------------------------------------------------------------

_WGS84_A = 6_378_137.0
_WGS84_E_SQ = 6.69437999014e-3


class _EcefEnuConverter:
    """Replicate deli::gps::SerialGps::SetReference + ConvertToLocal."""

    def __init__(self, ref_lat: float, ref_lon: float, ref_alt: float = 0.0) -> None:
        lat_rad = math.radians(ref_lat)
        lon_rad = math.radians(ref_lon)

        self._sin_lat = math.sin(lat_rad)
        self._cos_lat = math.cos(lat_rad)
        self._sin_lon = math.sin(lon_rad)
        self._cos_lon = math.cos(lon_rad)

        N = _WGS84_A / math.sqrt(1 - _WGS84_E_SQ * self._sin_lat ** 2)
        self._ref_x = (N + ref_alt) * self._cos_lat * self._cos_lon
        self._ref_y = (N + ref_alt) * self._cos_lat * self._sin_lon
        self._ref_z = (N * (1 - _WGS84_E_SQ) + ref_alt) * self._sin_lat

    def convert(self, lat: float, lon: float, alt: float = 0.0) -> tuple[float, float]:
        """Return (x_east, y_north) in metres — same as robot's Point(x, y)."""
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        s_lat = math.sin(lat_rad)
        c_lat = math.cos(lat_rad)
        s_lon = math.sin(lon_rad)
        c_lon = math.cos(lon_rad)

        N = _WGS84_A / math.sqrt(1 - _WGS84_E_SQ * s_lat ** 2)
        dx = (N + alt) * c_lat * c_lon - self._ref_x
        dy = (N + alt) * c_lat * s_lon - self._ref_y
        dz = (N * (1 - _WGS84_E_SQ) + alt) * s_lat - self._ref_z

        x = -self._sin_lon * dx + self._cos_lon * dy
        y = (-self._sin_lat * self._cos_lon * dx
             - self._sin_lat * self._sin_lon * dy
             + self._cos_lat * dz)
        return round(x, 3), round(y, 3)


_converter = _EcefEnuConverter(CAMPUS_ORIGIN_LAT, CAMPUS_ORIGIN_LON, CAMPUS_ORIGIN_ALT)


def gps_to_local(lat: float, lon: float, alt: float = 0.0) -> tuple[float, float]:
    """Convert GPS (lat, lon) → local ENU (x_east, y_north) in metres."""
    return _converter.convert(lat, lon, alt)

# ---------------------------------------------------------------------------
# Adjacency + Dijkstra
# ---------------------------------------------------------------------------

def _build_adjacency() -> dict[int, list[tuple[int, float]]]:
    adj: dict[int, list[tuple[int, float]]] = {}
    for i, j in CAMPUS_EDGES:
        wi = CAMPUS_WAYPOINTS[i]
        wj = CAMPUS_WAYPOINTS[j]
        d = _haversine(wi["lat"], wi["lon"], wj["lat"], wj["lon"])
        adj.setdefault(i, []).append((j, d))
        adj.setdefault(j, []).append((i, d))
    return adj


_ADJ = _build_adjacency()


def dijkstra(src: int, dst: int) -> Optional[list[int]]:
    """Return list of waypoint indices from *src* to *dst*, or None if unreachable."""
    dist: dict[int, float] = {src: 0.0}
    prev: dict[int, int] = {}
    pq: list[tuple[float, int]] = [(0.0, src)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        if u == dst:
            break
        for v, w in _ADJ.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    if dst not in dist:
        return None

    path: list[int] = []
    cur = dst
    while cur != src:
        path.append(cur)
        cur = prev[cur]
    path.append(src)
    path.reverse()
    return path


def find_nearest_waypoint(lat: float, lon: float) -> int:
    """Return index of the CAMPUS_WAYPOINTS node closest to (lat, lon)."""
    best_idx = 0
    best_d = math.inf
    for wp in CAMPUS_WAYPOINTS:
        d = _haversine(lat, lon, wp["lat"], wp["lon"])
        if d < best_d:
            best_d = d
            best_idx = wp["idx"]
    return best_idx

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def find_waypoint_idx_by_location_id(location_id: str) -> Optional[int]:
    """Map a CAMPUS_LOCATIONS id (e.g. 'b1') to the corresponding CAMPUS_WAYPOINTS index."""
    from app.config import CAMPUS_LOCATIONS
    loc = next((l for l in CAMPUS_LOCATIONS if l["id"] == location_id), None)
    if loc is None:
        return None
    for wp in CAMPUS_WAYPOINTS:
        if abs(wp["lat"] - loc["lat"]) < 1e-6 and abs(wp["lon"] - loc["lng"]) < 1e-6:
            return wp["idx"]
    return None


def get_route_coords(location_id: str) -> Optional[list[dict]]:
    """Return a list of {lat, lon} dicts for the route from *location_id* to the library."""
    src = find_waypoint_idx_by_location_id(location_id)
    if src is None:
        return None
    path = dijkstra(src, CAMPUS_LIBRARY_IDX)
    if path is None:
        return None
    return [{"lat": CAMPUS_WAYPOINTS[i]["lat"], "lon": CAMPUS_WAYPOINTS[i]["lon"]} for i in path]

# ---------------------------------------------------------------------------
# Dispatch route: robot_pos → pickup → library  →  {"stage_x":[], "stage_y":[]}
# ---------------------------------------------------------------------------

def convert_gps_list_to_payload(points: list[tuple[float, float]]) -> dict:
    """Convert a list of (lat, lon) GPS points to ``{"stage_x": [], "stage_y": []}``."""
    sx: list[float] = []
    sy: list[float] = []
    for lat, lon in points:
        x, y = gps_to_local(lat, lon)
        sx.append(x)
        sy.append(y)
    return {"stage_x": sx, "stage_y": sy}


def build_dispatch_route(
    robot_lat: float | None,
    robot_lon: float | None,
    pickup_location_id: str,
) -> Optional[dict]:
    """Compute full path (robot → pickup → library) and return MQTT payload.

    Returns ``{"stage_x": [...], "stage_y": [...]}`` ready to JSON-serialise,
    or *None* when no valid path exists.
    """
    pickup_idx = find_waypoint_idx_by_location_id(pickup_location_id)
    if pickup_idx is None:
        return None

    # ---- leg 1: robot → pickup ----
    if robot_lat is not None and robot_lon is not None:
        start_idx = find_nearest_waypoint(robot_lat, robot_lon)
    else:
        start_idx = pickup_idx  # fallback: start directly at pickup

    if start_idx == pickup_idx:
        leg1: list[int] = [pickup_idx]
    else:
        p = dijkstra(start_idx, pickup_idx)
        if p is None:
            return None
        leg1 = p

    # ---- leg 2: pickup → library ----
    if pickup_idx == CAMPUS_LIBRARY_IDX:
        leg2: list[int] = []
    else:
        p = dijkstra(pickup_idx, CAMPUS_LIBRARY_IDX)
        if p is None:
            return None
        leg2 = p[1:]  # skip duplicate pickup node

    full_path = leg1 + leg2

    # ---- convert to local XY ----
    stage_x: list[float] = []
    stage_y: list[float] = []
    for idx in full_path:
        wp = CAMPUS_WAYPOINTS[idx]
        x, y = gps_to_local(wp["lat"], wp["lon"])
        stage_x.append(x)
        stage_y.append(y)

    return {"stage_x": stage_x, "stage_y": stage_y}
