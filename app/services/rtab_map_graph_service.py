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
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import RTAB_MAP_DB_MAX_BYTES, RTAB_MAP_DB_PATH, RTAB_MAP_ENV_MAX_POINTS


def _pose_tx_ty(blob: bytes) -> Optional[Tuple[float, float]]:
    if not blob or len(blob) < 48:
        return None
    try:
        f = struct.unpack("12f", blob[:48])
    except struct.error:
        return None
    return (float(f[3]), float(f[7]))


def _opencv_elem_size(type_code: int) -> int:
    """Số byte / phần tử cv::Mat từ type (depth + channels)."""
    depth = type_code & 7
    ch = ((type_code >> 3) & 511) + 1
    if depth > 6 or ch < 1:
        return 0
    depth_bytes = (1, 1, 2, 2, 4, 4, 8)[depth]
    return depth_bytes * ch


def _uncompress_rtab_cv_blob(blob: bytes) -> Optional[Tuple[int, int, int, bytes]]:
    """
    Giải nén cv::Mat do RTAB-Map ``compressData2`` lưu: zlib(payload) + 3 int32 (rows, cols, type) ở cuối.
    """
    if not blob or len(blob) < 16:
        return None
    try:
        rows, cols, typ = struct.unpack_from("<iii", blob, len(blob) - 12)
    except struct.error:
        return None
    if rows < 1 or cols < 1 or rows > 100000 or cols > 100000:
        return None
    comp = blob[:-12]
    try:
        raw = zlib.decompress(comp)
    except zlib.error:
        return None
    esz = _opencv_elem_size(typ)
    if esz <= 0 or rows * cols * esz != len(raw):
        return None
    return rows, cols, typ, raw


def _world_xy_from_pose_and_local(f12: Tuple[float, ...], lx: float, ly: float) -> Tuple[float, float]:
    """Áp pose 3×4 (12 float row-major) lên điểm local (x,y), z=0."""
    wx = f12[0] * lx + f12[1] * ly + f12[3]
    wy = f12[4] * lx + f12[5] * ly + f12[7]
    return wx, wy


def _collect_env_xy_points(con: sqlite3.Connection, max_points: int) -> Tuple[List[List[float]], int, int]:
    """
    Gom điểm laser / obstacle (CV_32FC2) vào world XY. Trả (points, nodes_used, pair_samples).
    """
    rows = list(
        con.execute(
            "SELECT n.pose, d.scan, d.obstacle_cells FROM Node n "
            "INNER JOIN Data d ON n.id = d.id"
        )
    )
    if not rows or max_points < 1:
        return [], 0, 0

    node_step = max(1, len(rows) // max(1, min(500, max(50, max_points // 50))))
    per_node_pairs = max(24, max_points // max(1, (len(rows) // node_step) + 1) // 3)

    out: List[List[float]] = []
    nodes_used = 0
    pair_samples = 0

    for idx, (pose_b, scan_b, obs_b) in enumerate(rows):
        if idx % node_step != 0:
            continue
        if not pose_b or len(pose_b) < 48:
            continue
        try:
            f12 = struct.unpack("12f", bytes(pose_b)[:48])
        except struct.error:
            continue
        nodes_used += 1

        for blob in (scan_b, obs_b):
            if not blob or len(out) >= max_points:
                break
            mat = _uncompress_rtab_cv_blob(bytes(blob))
            if not mat:
                continue
            h, w, typ, raw = mat
            if _opencv_elem_size(typ) != 8:
                continue
            n_float = h * w * 2
            if n_float * 4 != len(raw):
                continue
            try:
                floats = struct.unpack("<" + str(n_float) + "f", raw)
            except struct.error:
                continue
            n_pairs = len(floats) // 2
            if n_pairs < 1:
                continue
            pair_step = max(1, n_pairs // per_node_pairs)
            for pi in range(0, n_pairs, pair_step):
                j = pi * 2
                if j + 1 >= len(floats):
                    break
                wx, wy = _world_xy_from_pose_and_local(f12, floats[j], floats[j + 1])
                out.append([wx, wy])
                pair_samples += 1
                if len(out) >= max_points:
                    return out, nodes_used, pair_samples

    return out, nodes_used, pair_samples


def build_rtab_graph_json(
    db_path: Optional[str] = None,
    include_environment: bool = True,
) -> Dict[str, Any]:
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
            "env_points": [],
            "env_nodes_sampled": 0,
            "env_pair_samples": 0,
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
                "env_points": [],
                "env_nodes_sampled": 0,
                "env_pair_samples": 0,
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

        env_points: List[List[float]] = []
        env_nodes_used = 0
        env_pairs = 0
        if include_environment:
            try:
                env_points, env_nodes_used, env_pairs = _collect_env_xy_points(
                    con, max_points=max(1000, min(RTAB_MAP_ENV_MAX_POINTS, 100000))
                )
            except (sqlite3.Error, struct.error, zlib.error, MemoryError):
                env_points = []

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        for q in env_points:
            xs.append(q[0])
            ys.append(q[1])
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        pad = max((xmax - xmin), (ymax - ymin)) * 0.05 + 0.5
        bounds = {
            "xmin": xmin - pad,
            "xmax": xmax + pad,
            "ymin": ymin - pad,
            "ymax": ymax + pad,
        }

        return {
            "success": True,
            "message": None,
            "bounds": bounds,
            "nodes": nodes_out,
            "links": links,
            "source": path.name,
            "env_points": env_points,
            "env_nodes_sampled": env_nodes_used,
            "env_pair_samples": env_pairs,
        }
    except sqlite3.Error as exc:
        return {
            "success": False,
            "message": f"Lỗi SQLite: {exc}",
            "bounds": None,
            "nodes": [],
            "links": [],
            "source": str(path),
            "env_points": [],
            "env_nodes_sampled": 0,
            "env_pair_samples": 0,
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
