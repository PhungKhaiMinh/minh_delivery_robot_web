"""
Đọc graph từ database RTAB-Map (vd. A5_night.db) để hiển thị trên Admin Tracking.

Pose Node: BLOB 12 float (3×4 row-major), translation tại chỉ số 3, 7, 11 → dùng tx, ty làm mặt phẳng 2D.
Link type 0 = kNeighbor (theo RTAB-Map).
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import RTAB_MAP_DB_PATH


def _pose_tx_ty(blob: bytes) -> Optional[Tuple[float, float]]:
    if not blob or len(blob) < 48:
        return None
    try:
        f = struct.unpack("12f", blob[:48])
    except struct.error:
        return None
    return (float(f[3]), float(f[7]))


def build_rtab_graph_json(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Trả JSON cho Leaflet CRS.Simple: bounds, nodes, links (neighbor, deduped)."""
    path = Path(db_path or RTAB_MAP_DB_PATH).resolve()
    if not path.is_file():
        return {
            "success": False,
            "message": f"Không tìm thấy file map: {path}",
            "bounds": None,
            "nodes": [],
            "links": [],
            "source": str(path),
        }

    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except (sqlite3.Error, ValueError):
        con = sqlite3.connect(str(path))

    try:
        positions: Dict[int, Tuple[float, float]] = {}
        cur = con.execute("SELECT id, pose FROM Node WHERE pose IS NOT NULL")
        for nid, pose in cur:
            pt = _pose_tx_ty(bytes(pose))
            if pt is None:
                continue
            positions[int(nid)] = pt

        if not positions:
            return {
                "success": False,
                "message": "Database không có Node.pose hợp lệ.",
                "bounds": None,
                "nodes": [],
                "links": [],
                "source": str(path),
            }

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        pad = max((xmax - xmin), (ymax - ymin)) * 0.05 + 0.5
        bounds = {
            "xmin": xmin - pad,
            "xmax": xmax + pad,
            "ymin": ymin - pad,
            "ymax": ymax + pad,
        }

        seen: Set[Tuple[int, int]] = set()
        links: List[List[int]] = []
        for a, b, typ in con.execute(
            "SELECT from_id, to_id, type FROM Link WHERE type = 0 AND from_id != to_id"
        ):
            u, v = int(a), int(b)
            if u not in positions or v not in positions:
                continue
            key = (u, v) if u < v else (v, u)
            if key in seen:
                continue
            seen.add(key)
            links.append([u, v])

        nodes_out: List[Dict[str, Any]] = [
            {"id": nid, "x": positions[nid][0], "y": positions[nid][1]}
            for nid in sorted(positions.keys())
        ]

        return {
            "success": True,
            "message": None,
            "bounds": bounds,
            "nodes": nodes_out,
            "links": links,
            "source": path.name,
        }
    except sqlite3.Error as exc:
        return {
            "success": False,
            "message": f"Lỗi SQLite: {exc}",
            "bounds": None,
            "nodes": [],
            "links": [],
            "source": str(path),
        }
    finally:
        con.close()
