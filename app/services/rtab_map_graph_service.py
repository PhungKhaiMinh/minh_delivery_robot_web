"""
Đọc graph từ database RTAB-Map (vd. A5_night.db) để hiển thị trên Admin Tracking.

Pose Node: BLOB 12 float (3×4 row-major), translation tại chỉ số 3, 7, 11 → dùng tx, ty làm mặt phẳng 2D.
Link type 0 = kNeighbor (theo RTAB-Map).
"""

from __future__ import annotations

import os
import sqlite3
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import RTAB_MAP_DB_MAX_BYTES, RTAB_MAP_DB_PATH


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

    con: Optional[sqlite3.Connection] = None
    try:
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        except (sqlite3.Error, ValueError, OSError):
            con = sqlite3.connect(str(path))

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
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass


def validate_rtab_sqlite_file(path: Path) -> Tuple[bool, str]:
    """Magic SQLite + bảng Node (RTAB-Map)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
        if len(head) < 16 or not head.startswith(b"SQLite format 3"):
            return False, "Không phải file SQLite hợp lệ."
        con = sqlite3.connect(str(path))
        try:
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Node' LIMIT 1"
            ).fetchone()
            if not row:
                return False, "Thiếu bảng Node — không phải RTAB-Map database."
        finally:
            con.close()
    except OSError as exc:
        return False, f"Đọc file: {exc}"
    except sqlite3.Error as exc:
        return False, f"SQLite: {exc}"
    return True, ""


def get_rtab_map_status(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Trạng thái file map trên server (cho Settings / debug)."""
    p = Path(db_path or RTAB_MAP_DB_PATH).resolve()
    out: Dict[str, Any] = {
        "success": True,
        "path": str(p),
        "exists": p.is_file(),
        "bytes": 0,
        "valid_rtab": False,
    }
    if not p.is_file():
        return out
    try:
        out["bytes"] = p.stat().st_size
    except OSError:
        return out
    ok, _ = validate_rtab_sqlite_file(p)
    out["valid_rtab"] = ok
    return out


async def save_rtab_map_from_upload(upload: Any, max_bytes: Optional[int] = None) -> Tuple[bool, str]:
    """
    Ghi upload vào ``RTAB_MAP_DB_PATH`` (thay thế nguyên file). Trả (ok, message).
    ``upload`` là FastAPI ``UploadFile`` (có ``read`` async).
    """
    limit = max_bytes if max_bytes is not None else RTAB_MAP_DB_MAX_BYTES
    dest = Path(RTAB_MAP_DB_PATH).resolve()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Không tạo được thư mục: {exc}"

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="rtab_", suffix=".db", dir=str(dest.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        total = 0
        chunk_size = 1024 * 1024
        with open(tmp_path, "wb") as out:
            while True:
                block = await upload.read(chunk_size)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    return False, f"File vượt quá {limit // (1024 * 1024)} MB."
                out.write(block)

        ok, err = validate_rtab_sqlite_file(tmp_path)
        if not ok:
            return False, err

        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        tmp_path.replace(dest)
        replaced = True
        return True, "Đã lưu map. Mở lại trang Tracking để tải graph."
    except OSError as exc:
        return False, str(exc)
    finally:
        if not replaced and tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass
