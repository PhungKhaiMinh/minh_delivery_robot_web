"""
Đọc graph từ database RTAB-Map (vd. A5_night.db) để hiển thị trên Admin Tracking.

Pose Node: BLOB 12 float (3×4 row-major), translation tại chỉ số 3, 7, 11 → dùng tx, ty làm mặt phẳng 2D.

Điểm laser: RTAB-Map lưu ``scan_info`` (>=0.18) gồm format + range + **localTransform** 12 float;
world = ``node_pose @ localTransform @ (lx, ly, 0)`` — khớp DB Viewer, không chỉ ``node_pose @ (lx,ly)``.
Link type 0 = kNeighbor (theo RTAB-Map).

Sau tối ưu toàn cục, RTAB-Map lưu trong bảng **Admin**: ``opt_map`` (occupancy 2D đã nén),
``opt_map_x_min`` / ``opt_map_y_min`` / ``opt_map_resolution``, và ``opt_ids`` + ``opt_poses``
(poses đã tối ưu — Graph View dùng dữ liệu này).
"""

from __future__ import annotations

import base64
import binascii
import os
import sqlite3
import struct
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config import (
    RTAB_MAP_DB_MAX_BYTES,
    RTAB_MAP_DB_PATH,
    RTAB_MAP_ENV_MAX_POINTS,
    RTAB_MAP_ENV_RASTER_MAX_SIDE,
    RTAB_MAP_OPT_MAP_MAX_PIXELS,
    RTAB_MAP_OPT_MAP_MAX_SIDE,
    RTAB_MAP_OPT_MAP_INVERT_GREY,
)


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


_IDENTITY12: Tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def _parse_scan_local_transform(scan_info_b: Optional[bytes]) -> Tuple[float, ...]:
    """
    ``scan_info`` RTAB-Map >= 0.18.0: (7 + 12) float — 12 float cuối = Transform 3×4 laser→base.
    Xem DBDriverSqlite3.cpp (memcpy scanLocalTransform từ dataFloat+7).
    """
    if not scan_info_b or len(scan_info_b) < 76:
        return _IDENTITY12
    try:
        return struct.unpack("12f", bytes(scan_info_b)[28:76])
    except struct.error:
        return _IDENTITY12


def _world_xy_chain(node12: Tuple[float, ...], local12: Tuple[float, ...], lx: float, ly: float) -> Tuple[float, float]:
    """Áp localTransform lên điểm scan (x,y,z=0), rồi pose node — giống pipeline LaserScan trong RTAB-Map."""
    lz = 0.0
    x1 = local12[0] * lx + local12[1] * ly + local12[2] * lz + local12[3]
    y1 = local12[4] * lx + local12[5] * ly + local12[6] * lz + local12[7]
    z1 = local12[8] * lx + local12[9] * ly + local12[10] * lz + local12[11]
    wx = node12[0] * x1 + node12[1] * y1 + node12[2] * z1 + node12[3]
    wy = node12[4] * x1 + node12[5] * y1 + node12[6] * z1 + node12[7]
    return wx, wy


def _tx_ty_from_pose12(f12: Tuple[float, ...]) -> Tuple[float, float]:
    return (float(f12[3]), float(f12[7]))


def _load_optimized_poses_by_id(con: sqlite3.Connection) -> Dict[int, Tuple[float, ...]]:
    """Đọc ``Admin.opt_ids`` + ``opt_poses`` (zlib + cv::Mat) — poses sau Graph Optimizer."""
    try:
        row = con.execute("SELECT opt_ids, opt_poses FROM Admin LIMIT 1").fetchone()
    except sqlite3.Error:
        return {}
    if not row or not row[0] or not row[1]:
        return {}
    ids_um = _uncompress_rtab_cv_blob(bytes(row[0]))
    poses_um = _uncompress_rtab_cv_blob(bytes(row[1]))
    if not ids_um or not poses_um:
        return {}
    ir, ic, ityp, iraw = ids_um
    pr, pc, ptyp, praw = poses_um
    if _opencv_elem_size(ityp) != 4 or ir * ic * 4 != len(iraw):
        return {}
    if _opencv_elem_size(ptyp) != 4 or pr * pc * 4 != len(praw):
        return {}
    n_id = ir * ic
    try:
        ids = struct.unpack("<" + str(n_id) + "i", iraw)
        floats = struct.unpack("<" + str(pr * pc) + "f", praw)
    except struct.error:
        return {}
    if len(ids) * 12 != len(floats):
        return {}
    out: Dict[int, Tuple[float, ...]] = {}
    for i, nid in enumerate(ids):
        b = i * 12
        out[int(nid)] = tuple(float(x) for x in floats[b : b + 12])
    return out


def _occ_byte_to_grey(v: int) -> int:
    """
    Occupancy RTAB (Graph View style): 0 free, 100 obstacle, 255 unknown.
    Tông tối cho ô trống, tường sáng rõ, unknown tách biệt — gần DB Viewer.
    """
    if v == 100:
        return 250
    if v == 0:
        return 11
    if v == 255:
        return 44
    if 1 <= v <= 99:
        # xác suất chiếm: ramp mượt từ nền tối → tường sáng
        t = v / 100.0
        base = 11 + int(t * (250 - 11))
        return min(255, max(11, base))
    return 38


def _opt_map_upscale_factor(cols: int, rows: int) -> int:
    """Integer nearest-neighbor scale sao cho không vượt max cạnh / max pixel (payload hợp lý)."""
    max_side = max(512, RTAB_MAP_OPT_MAP_MAX_SIDE)
    max_px = max(500_000, RTAB_MAP_OPT_MAP_MAX_PIXELS)
    best = 1
    for s in range(2, 12):
        nw, nh = cols * s, rows * s
        if nw * nh > max_px or max(nw, nh) > max_side:
            break
        best = s
    return best


def _nearest_upscale_grey8(grey: bytes, cw: int, ch: int, scale: int) -> Tuple[int, int, bytes]:
    if scale <= 1 or len(grey) != cw * ch:
        return cw, ch, grey
    nw, nh = cw * scale, ch * scale
    out = bytearray(nw * nh)
    for y in range(ch):
        y0 = y * scale
        src_row = y * cw
        for x in range(cw):
            v = grey[src_row + x]
            x0 = x * scale
            for dy in range(scale):
                row_off = (y0 + dy) * nw + x0
                for dx in range(scale):
                    out[row_off + dx] = v
    return nw, nh, bytes(out)


def _grey_dilate_max(grey: bytes, cw: int, ch: int, neighbor_sub: int = 8) -> bytes:
    """Giãn vùng obstacle một nếp để tường liền hơn (grid nhỏ)."""
    src = memoryview(grey)
    out = bytearray(grey)
    for y in range(ch):
        for x in range(cw):
            k = y * cw + x
            m = int(src[k])
            if m < 90:
                continue
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= ch:
                    continue
                base = yy * cw
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if xx < 0 or xx >= cw:
                        continue
                    nk = base + xx
                    boost = m - neighbor_sub if (dx != 0 or dy != 0) else m
                    if boost > 0:
                        out[nk] = min(255, max(out[nk], boost))
    return bytes(out)


# opt_map trong DB: hàng OpenCV tăng theo y; Leaflet ImageOverlay + bounds NW/SE — nếu lệch dọc, thử đổi cờ này.
_OPT_MAP_FLIP_ROWS_FOR_LEAFLET = True


def _try_load_admin_opt_map_surface(
    con: sqlite3.Connection,
) -> Optional[Tuple[int, int, bytes, Dict[str, float]]]:
    """
    ``Admin.opt_map`` — cùng dữ liệu 2D map sau tối ưu như Graph View (DBDriverSqlite3::load2DMapQuery).

    Trả (png_width, png_height, grey8_rowmajor, bounds_xy) hoặc None.
    """
    try:
        row = con.execute(
            "SELECT opt_map, opt_map_x_min, opt_map_y_min, opt_map_resolution FROM Admin LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    x_min = float(row[1])
    y_min = float(row[2])
    res = float(row[3])
    if res <= 0:
        return None
    um = _uncompress_rtab_cv_blob(bytes(row[0]))
    if not um:
        return None
    n_rows, n_cols, typ, raw = um
    if _opencv_elem_size(typ) != 1 or n_rows < 1 or n_cols < 1 or n_rows * n_cols != len(raw):
        return None
    xmax = x_min + n_cols * res
    ymax = y_min + n_rows * res
    bmap = {"xmin": x_min, "xmax": xmax, "ymin": y_min, "ymax": ymax}
    grey = bytearray(n_cols * n_rows)
    for r in range(n_rows):
        base = r * n_cols
        if _OPT_MAP_FLIP_ROWS_FOR_LEAFLET:
            iy = n_rows - 1 - r
        else:
            iy = r
        for c in range(n_cols):
            grey[iy * n_cols + c] = _occ_byte_to_grey(raw[base + c])
    # Không dilate: giữ biên ô occupancy sắc như Graph View; upscale sau để zoom web mịn.
    grey_b = bytes(grey)
    sc = _opt_map_upscale_factor(n_cols, n_rows)
    nw, nh, grey_b = _nearest_upscale_grey8(grey_b, n_cols, n_rows, sc)
    if RTAB_MAP_OPT_MAP_INVERT_GREY:
        grey_b = bytes(255 - v for v in grey_b)
    return nw, nh, grey_b, bmap


def _world_xy_from_pose_and_local(f12: Tuple[float, ...], lx: float, ly: float) -> Tuple[float, float]:
    """Áp pose 3×4 (12 float row-major) lên điểm local (x,y), z=0."""
    wx = f12[0] * lx + f12[1] * ly + f12[3]
    wy = f12[4] * lx + f12[5] * ly + f12[7]
    return wx, wy


def _collect_env_xy_points(con: sqlite3.Connection, max_points: int) -> Tuple[List[List[float]], int, int]:
    """
    Gom điểm laser (scan) + occupancy obstacle (CV_32FC2, local) vào world XY.

    Phân bổ đều theo **mọi node** (không còn bỏ qua 3/4 node): mỗi node nhận phần ngân sách
    còn lại / số node chưa xử lý — tránh chỉ dày ở đầu danh sách DB.
    """
    rows = list(
        con.execute(
            "SELECT n.pose, d.scan, d.obstacle_cells, d.scan_info FROM Node n "
            "INNER JOIN Data d ON n.id = d.id ORDER BY n.id"
        )
    )
    if not rows or max_points < 1:
        return [], 0, 0

    n_rows = len(rows)
    out: List[List[float]] = []
    nodes_used = 0
    pair_samples = 0

    for idx, (pose_b, scan_b, obs_b, scan_info_b) in enumerate(rows):
        if len(out) >= max_points:
            break
        if not pose_b or len(pose_b) < 48:
            continue
        try:
            f12 = struct.unpack("12f", bytes(pose_b)[:48])
        except struct.error:
            continue

        local12 = _parse_scan_local_transform(scan_info_b if scan_info_b else None)

        remaining = max_points - len(out)
        nodes_left = n_rows - idx
        budget_here = max(4, remaining // max(1, nodes_left))

        blobs: List[bytes] = []
        if scan_b:
            blobs.append(bytes(scan_b))
        if obs_b:
            blobs.append(bytes(obs_b))
        if not blobs:
            continue

        per_blob = max(2, budget_here // len(blobs))
        nodes_used += 1

        for blob in blobs:
            if len(out) >= max_points:
                break
            mat = _uncompress_rtab_cv_blob(blob)
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

            room = max_points - len(out)
            take = min(per_blob, room, n_pairs)
            if take < 1:
                continue
            pair_step = max(1, n_pairs // take)
            for pi in range(0, n_pairs, pair_step):
                j = pi * 2
                if j + 1 >= len(floats):
                    break
                wx, wy = _world_xy_chain(f12, local12, floats[j], floats[j + 1])
                out.append([wx, wy])
                pair_samples += 1
                if len(out) >= max_points:
                    return out, nodes_used, pair_samples

    return out, nodes_used, pair_samples


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _png_grey8(width: int, height: int, grey: bytes, compress_level: int = 6) -> bytes:
    if len(grey) != width * height:
        raise ValueError("raster size mismatch")
    buf = bytearray()
    for y in range(height):
        buf.append(0)
        buf.extend(grey[y * width : (y + 1) * width])
    z = zlib.compress(bytes(buf), max(1, min(9, int(compress_level))))
    ihdr = struct.pack(">2I5B", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", z)
        + _png_chunk(b"IEND", b"")
    )


def _raster_env_to_grey_bytes(
    env_points: List[List[float]],
    bounds: Dict[str, float],
    max_side: int,
) -> Optional[Tuple[int, int, bytes]]:
    """Tích lũy điểm vào lưới greyscale 8-bit (PNG), trả (cw, ch, pixels) hoặc None."""
    if not env_points or max_side < 64:
        return None
    xmin, xmax = float(bounds["xmin"]), float(bounds["xmax"])
    ymin, ymax = float(bounds["ymin"]), float(bounds["ymax"])
    rw = xmax - xmin
    rh = ymax - ymin
    if rw <= 0 or rh <= 0:
        return None
    ar = rw / rh if rh else 1.0
    if ar >= 1.0:
        cw = min(max_side, max(320, max_side))
        ch = max(1, int(round(cw / ar)))
    else:
        ch = min(max_side, max(320, max_side))
        cw = max(1, int(round(ch * ar)))
    cw = max(1, min(cw, max_side))
    ch = max(1, min(ch, max_side))
    acc = bytearray(cw * ch)
    step = 16
    for q in env_points:
        try:
            wx, wy = float(q[0]), float(q[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Leaflet ImageOverlay: pixel row 0 = bounds NorthWest = max(lat) = ymax - wy → wy = ymin.
        # Do not use (ymax - wy) here or the PNG is flipped vs CRS.Simple + graph polylines.
        ix = int((wx - xmin) / rw * (cw - 1))
        iy = int((wy - ymin) / rh * (ch - 1))
        if 0 <= ix < cw and 0 <= iy < ch:
            k = iy * cw + ix
            acc[k] = min(255, acc[k] + step)
    if max(acc) == 0:
        return None
    grey = bytes(acc)
    # Làm dày nhẹ (tường mảnh / laser thưa) — một bước hàng xóm
    out = bytearray(grey)
    for y in range(ch):
        for x in range(cw):
            k = y * cw + x
            m = grey[k]
            if m == 0:
                continue
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= ch:
                    continue
                base = yy * cw
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if xx < 0 or xx >= cw:
                        continue
                    nk = base + xx
                    boost = m - 18 if (dy != 0 or dx != 0) else m
                    if boost > 0:
                        out[nk] = min(255, max(out[nk], boost))
    # Lần 2: làm liền cấu trúc giống Graph View (tường mảnh)
    grey2 = bytes(out)
    out3 = bytearray(grey2)
    for y in range(ch):
        for x in range(cw):
            k = y * cw + x
            m = grey2[k]
            if m == 0:
                continue
            for dy in (-1, 0, 1):
                yy = y + dy
                if yy < 0 or yy >= ch:
                    continue
                base = yy * cw
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if xx < 0 or xx >= cw:
                        continue
                    nk = base + xx
                    boost = m - 16 if (dy != 0 or dx != 0) else m
                    if boost > 0:
                        out3[nk] = min(255, max(out3[nk], boost))
    return cw, ch, bytes(out3)


def build_rtab_graph_json(
    db_path: Optional[str] = None,
    include_environment: bool = True,
    include_raster: bool = True,
    prefer_admin_opt_map: bool = True,
) -> Dict[str, Any]:
    """Trả JSON cho Leaflet CRS.Simple: bounds, nodes, links; môi trường mặc định là PNG raster (nhẹ)."""
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
            "env_raster_w": 0,
            "env_raster_h": 0,
            "env_raster_png_b64": "",
            "env_raster_source": "",
            "opt_map_bounds": None,
            "optimized_graph_only": False,
        }

    con: Optional[sqlite3.Connection] = None
    try:
        try:
            uri = path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
        except (sqlite3.Error, ValueError, OSError):
            con = sqlite3.connect(str(path))

        opt_by_id = _load_optimized_poses_by_id(con)

        opt_surface: Optional[Tuple[int, int, bytes, Dict[str, float]]] = None
        opt_map_bounds: Optional[Dict[str, float]] = None
        if include_environment:
            opt_surface = _try_load_admin_opt_map_surface(con)
            if opt_surface:
                opt_map_bounds = opt_surface[3]

        use_admin_raster = bool(
            prefer_admin_opt_map
            and opt_surface
            and opt_by_id
            and include_raster
            and include_environment
        )

        positions: Dict[int, Tuple[float, float]] = {}
        cur = con.execute("SELECT id, pose FROM Node WHERE pose IS NOT NULL")
        for nid, pose in cur:
            nid = int(nid)
            if use_admin_raster:
                if nid not in opt_by_id:
                    continue
                positions[nid] = _tx_ty_from_pose12(opt_by_id[nid])
            else:
                if nid in opt_by_id:
                    positions[nid] = _tx_ty_from_pose12(opt_by_id[nid])
                else:
                    pt = _pose_tx_ty(bytes(pose))
                    if pt is None:
                        continue
                    positions[nid] = pt

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
                "env_raster_w": 0,
                "env_raster_h": 0,
                "env_raster_png_b64": "",
                "env_raster_source": "",
                "opt_map_bounds": None,
                "optimized_graph_only": False,
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
        if include_environment and not use_admin_raster:
            try:
                env_points, env_nodes_used, env_pairs = _collect_env_xy_points(
                    con, max_points=max(4000, min(RTAB_MAP_ENV_MAX_POINTS, 400000))
                )
            except (sqlite3.Error, struct.error, zlib.error, MemoryError):
                env_points = []

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        if opt_map_bounds:
            xs.extend([opt_map_bounds["xmin"], opt_map_bounds["xmax"]])
            ys.extend([opt_map_bounds["ymin"], opt_map_bounds["ymax"]])
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

        env_raster_w = 0
        env_raster_h = 0
        env_raster_png_b64 = ""
        env_raster_source = ""
        if include_environment and include_raster:
            if use_admin_raster and opt_surface:
                try:
                    cw_r, ch_r, grey, _ob = opt_surface
                    png_bytes = _png_grey8(cw_r, ch_r, grey, compress_level=9)
                    env_raster_w = cw_r
                    env_raster_h = ch_r
                    env_raster_png_b64 = base64.standard_b64encode(png_bytes).decode("ascii")
                    env_raster_source = "admin_opt_map"
                    env_points = []
                    env_pairs = 0
                    env_nodes_used = len(positions)
                except (ValueError, MemoryError, zlib.error, OSError):
                    env_raster_source = ""
            elif env_points:
                try:
                    rast = _raster_env_to_grey_bytes(
                        env_points,
                        bounds,
                        max(256, min(4096, RTAB_MAP_ENV_RASTER_MAX_SIDE)),
                    )
                    if rast is not None:
                        cw_r, ch_r, grey = rast
                        png_bytes = _png_grey8(cw_r, ch_r, grey)
                        env_raster_w = cw_r
                        env_raster_h = ch_r
                        env_raster_png_b64 = base64.standard_b64encode(png_bytes).decode("ascii")
                        env_raster_source = "point_accum"
                        env_points = []
                except (ValueError, MemoryError, zlib.error, OSError):
                    pass

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
            "env_raster_w": env_raster_w,
            "env_raster_h": env_raster_h,
            "env_raster_png_b64": env_raster_png_b64,
            "env_raster_source": env_raster_source,
            "opt_map_bounds": opt_map_bounds,
            "optimized_graph_only": use_admin_raster,
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
            "env_raster_w": 0,
            "env_raster_h": 0,
            "env_raster_png_b64": "",
            "env_raster_source": "",
            "opt_map_bounds": None,
            "optimized_graph_only": False,
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
    ``max_bytes``: nếu > 0 thì giới hạn kích thước; None dùng ``RTAB_MAP_DB_MAX_BYTES`` (0 = không giới hạn).
    """
    # 0 = không giới hạn (chỉ giới hạn đĩa / proxy nếu có).
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
                if limit > 0 and total > limit:
                    return False, f"File vượt quá {limit // (1024 * 1024)} MB (cấu hình RTAB_MAP_DB_MAX_BYTES)."
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
