"""Dijkstra shortest-path on the campus waypoint graph + ECEF→ENU conversion.

GPS→local XY dùng đúng thuật toán ECEF→ENU (WGS84) giống firmware robot
(serial_gps.hpp  SetReference / ConvertToLocal) để đảm bảo stage_x/stage_y
khớp chính xác với hệ tọa độ mà robot sử dụng.
"""

from __future__ import annotations

import heapq
import json
import math
import os
import threading
from pathlib import Path
from typing import Optional

from app.config import (
    CAMPUS_WAYPOINTS,
    CAMPUS_EDGES,
    CAMPUS_LIBRARY_IDX,
    CAMPUS_ORIGIN_LAT,
    CAMPUS_ORIGIN_LON,
    CAMPUS_ORIGIN_ALT,
    BASE_DIR,
)

_RUNTIME_ORIGIN_PATH = (
    Path("/tmp/runtime_gps_origin.json")
    if os.getenv("VERCEL", "").strip() == "1"
    else (BASE_DIR / "data" / "runtime_gps_origin.json")
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
        """Return (x, y) matching robot firmware convention (x=north, y=east)."""
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

        east = -self._sin_lon * dx + self._cos_lon * dy
        north = (-self._sin_lat * self._cos_lon * dx
                 - self._sin_lat * self._sin_lon * dy
                 + self._cos_lat * dz)
        return north, east


_converter_lock = threading.Lock()
_origin_lat = CAMPUS_ORIGIN_LAT
_origin_lon = CAMPUS_ORIGIN_LON
_origin_alt = CAMPUS_ORIGIN_ALT
_converter = _EcefEnuConverter(_origin_lat, _origin_lon, _origin_alt)


def _try_load_runtime_origin() -> None:
    """Apply persisted campus origin from disk (if present) after env defaults."""
    global _converter, _origin_lat, _origin_lon, _origin_alt
    if not _RUNTIME_ORIGIN_PATH.is_file():
        return
    try:
        raw = _RUNTIME_ORIGIN_PATH.read_text(encoding="utf-8")
        o = json.loads(raw)
        la = float(o["lat"])
        lo = float(o["lon"])
        al = float(o.get("alt", 0.0))
    except (OSError, ValueError, KeyError, TypeError):
        return
    _converter = _EcefEnuConverter(la, lo, al)
    _origin_lat, _origin_lon, _origin_alt = la, lo, al


_try_load_runtime_origin()


def set_campus_gps_origin(lat: float, lon: float, alt: float = 0.0) -> None:
    """Set the ECEF→ENU reference (GPS base) used for all lat/lon → x,y conversions."""
    global _converter, _origin_lat, _origin_lon, _origin_alt
    with _converter_lock:
        _converter = _EcefEnuConverter(lat, lon, alt)
        _origin_lat, _origin_lon, _origin_alt = lat, lon, alt
    _RUNTIME_ORIGIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _RUNTIME_ORIGIN_PATH.write_text(
            json.dumps({"lat": lat, "lon": lon, "alt": alt}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def get_campus_gps_origin() -> tuple[float, float, float]:
    """Current reference lat, lon, alt (degrees / metres)."""
    with _converter_lock:
        return _origin_lat, _origin_lon, _origin_alt


def gps_to_local(lat: float, lon: float, alt: float = 0.0) -> tuple[float, float]:
    """Convert GPS (lat, lon) → local (x=north, y=east) matching robot firmware."""
    with _converter_lock:
        return _converter.convert(lat, lon, alt)


def local_to_gps(north: float, east: float) -> tuple[float, float]:
    """Gần đúng: (north, east) m trong ENU tại gốc hiện tại → (lat, lon) WGS84 (bản đồ OSM)."""
    with _converter_lock:
        rlat, rlon = _origin_lat, _origin_lon
    # mét / độ — đủ chính xác với khoảng cách campus
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(rlat))
    if abs(m_per_deg_lon) < 1e-6:
        m_per_deg_lon = 111_320.0
    return rlat + north / m_per_deg_lat, rlon + east / m_per_deg_lon

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
    """Map a pickup location id to the corresponding CAMPUS_WAYPOINTS index (theo lat/lon)."""
    from app.services.pickup_locations_store import get_catalog_locations

    loc = next((l for l in get_catalog_locations() if l["id"] == location_id), None)
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
