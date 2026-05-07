"""
Occupancy map PGM (P2/P5) cho Admin Tracking — chuyển PNG + meta bounds (mét).

Hỗ trợ file kèm ROS/nav2 ``map.yaml`` (resolution, origin) nếu có; không thì dùng
biến môi trường OCC_GRID_MAP_RESOLUTION / OCC_GRID_MAP_ORIGIN_X / OCC_GRID_MAP_ORIGIN_Y
với quy ước: góc trên-trái của ảnh PGM (dòng 0) = (origin_x, origin_y + height*res) theo trục
world giống layer Leaflet hiện tại (ymax phía trên ảnh).
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import (
    OCC_GRID_MAP_MAX_BYTES,
    OCC_GRID_MAP_ORIGIN_X,
    OCC_GRID_MAP_ORIGIN_Y,
    OCC_GRID_MAP_PATH,
    OCC_GRID_MAP_RESOLUTION,
    OCC_GRID_MAP_YAML_PATH,
)

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore


def _yaml_path_for_pgm(pgm: Path) -> Path:
    explicit = (OCC_GRID_MAP_YAML_PATH or "").strip()
    if explicit:
        return Path(explicit).resolve()
    return pgm.with_suffix(".yaml")


def _parse_ros_map_yaml(text: str) -> Tuple[Optional[float], Optional[List[float]]]:
    """Trích resolution và origin [x,y,yaw] từ map.yaml kiểu ROS (không cần PyYAML)."""
    res: Optional[float] = None
    origin: Optional[List[float]] = None
    m = re.search(r"^\s*resolution:\s*([0-9.eE+-]+)\s*$", text, re.MULTILINE)
    if m:
        try:
            res = float(m.group(1))
        except ValueError:
            res = None
    m2 = re.search(r"^\s*origin:\s*\[([^\]]+)\]", text, re.MULTILINE)
    if m2:
        parts = [p.strip() for p in m2.group(1).split(",")]
        vals: List[float] = []
        for p in parts[:3]:
            try:
                vals.append(float(p))
            except ValueError:
                vals.append(0.0)
        while len(vals) < 3:
            vals.append(0.0)
        origin = vals[:3]
    return res, origin


def _read_map_yaml_meta(pgm_path: Path) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Trả (resolution, ox, oy, yaw) từ file yaml nếu tồn tại; ngược lại (None,...).
    Với ROS: origin là góc dưới-trái của map trong world (y tăng lên bắc), ảnh PGM hàng 0 = bắc.
    """
    yp = _yaml_path_for_pgm(pgm_path)
    if not yp.is_file():
        return None, None, None, None
    try:
        raw = yp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None, None
    res, origin = _parse_ros_map_yaml(raw)
    if origin is None:
        return res, None, None, None
    ox, oy, yaw = float(origin[0]), float(origin[1]), float(origin[2])
    return res, ox, oy, yaw


def parse_pgm_header(path: Path) -> Tuple[str, int, int, int, int]:
    """
    Đọc magic P2/P5, width, height, maxval; trả về (magic, w, h, maxval, data_start_offset).
    Chỉ đọc đầu file (<= 1 MiB) để tránh tải toàn bộ map lớn vào RAM.
    """
    with path.open("rb") as f:
        data = f.read(1 << 20)
    if len(data) < 3 or data[:2] not in (b"P2", b"P5"):
        raise ValueError("Không phải PGM (cần P2 hoặc P5).")
    magic = data[:2].decode("ascii")
    i = 2
    ints: List[int] = []
    while i < len(data) and len(ints) < 3:
        while i < len(data) and data[i : i + 1] in b" \t\r\n":
            i += 1
        if i >= len(data):
            break
        if data[i : i + 1] == b"#":
            while i < len(data) and data[i : i + 1] not in b"\n\r":
                i += 1
            while i < len(data) and data[i : i + 1] in b"\n\r":
                i += 1
            continue
        if data[i : i + 1] not in b"0123456789":
            i += 1
            continue
        j = i
        while j < len(data) and data[j : j + 1] in b"0123456789":
            j += 1
        ints.append(int(data[i:j].decode("ascii")))
        i = j
    if len(ints) < 3:
        raise ValueError("Header PGM không đủ width, height, maxval (hoặc header > 1 MiB).")
    w, h, maxval = ints[0], ints[1], ints[2]
    while i < len(data) and data[i : i + 1] in b" \t\r\n":
        i += 1
    if magic == "P5" and i >= len(data):
        raise ValueError("Header PGM P5 không đầy đủ trong phần đầu file.")
    return magic, w, h, maxval, i


def _pgm_raw_pixels(path: Path, magic: str, w: int, h: int, maxval: int, data_start: int) -> bytes:
    with path.open("rb") as f:
        f.seek(data_start)
        raw = f.read()
    if magic == "P5":
        need = w * h * (2 if maxval > 255 else 1)
        if len(raw) < need:
            raise ValueError("Dữ liệu PGM P5 ngắn hơn kích thước khai báo.")
        return raw[:need]
    # P2 ASCII — chậm nhưng hiếm
    rest = raw.decode("ascii", errors="ignore").split()
    vals = [int(x) for x in rest[: w * h]]
    if len(vals) < w * h:
        raise ValueError("PGM P2 thiếu pixel.")
    if maxval <= 255:
        return bytes(max(0, min(255, v)) for v in vals)
    out = bytearray(w * h)
    for i, v in enumerate(vals):
        out[i] = min(255, int(round(v * 255.0 / maxval)))
    return bytes(out)


def pgm_to_png_bytes(path: Optional[Path] = None) -> Tuple[bytes, int, int]:
    if Image is None:
        raise RuntimeError("Thiếu thư viện Pillow (pip install Pillow).")
    p = Path(path or OCC_GRID_MAP_PATH).resolve()
    magic, w, h, maxval, off = parse_pgm_header(p)
    raw = _pgm_raw_pixels(p, magic, w, h, maxval, off)
    if maxval <= 255 and len(raw) == w * h:
        img = Image.frombytes("L", (w, h), raw)
    elif maxval > 255 and len(raw) == w * h * 2:
        import struct

        out8 = bytearray(w * h)
        scale = 255.0 / float(maxval)
        for pix in range(w * h):
            v = struct.unpack_from(">H", raw, pix * 2)[0]
            out8[pix] = min(255, int(round(v * scale)))
        img = Image.frombytes("L", (w, h), bytes(out8))
    else:
        raise ValueError("Kích thước buffer PGM không khớp width/height.")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), w, h


def compute_world_bounds(
    w: int,
    h: int,
    resolution: float,
    origin_x: float,
    origin_y: float,
    from_ros_yaml: bool,
) -> Dict[str, float]:
    """
    Trả bounds {xmin, ymin, xmax, ymax} trùng quy ước Leaflet Tracking cũ.

    Nếu from_ros_yaml: origin ROS = góc dưới-trái map (cell 0,0), trục y world lên bắc.
    Nếu không: origin_x, origin_y = góc trên-trái ảnh (PGM dòng 0 = phía ymax).
    """
    r = float(resolution)
    if r <= 0:
        raise ValueError("resolution phải > 0.")
    if from_ros_yaml:
        ox, oy = float(origin_x), float(origin_y)
        xmin = ox
        xmax = ox + w * r
        ymin = oy
        ymax = oy + h * r
        return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
    # OpenCV / top-left origin
    xmin = float(origin_x)
    ymax = float(origin_y)
    xmax = xmin + w * r
    ymin = ymax - h * r
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def build_occ_grid_meta(pgm_path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(pgm_path or OCC_GRID_MAP_PATH).resolve()
    out: Dict[str, Any] = {
        "success": False,
        "path": str(p),
        "exists": p.is_file(),
        "message": "",
        "bounds": None,
        "width": None,
        "height": None,
        "resolution": None,
        "origin_x": None,
        "origin_y": None,
        "yaml_used": False,
        "image_url": "/api/admin/occ-grid/image.png",
    }
    if not p.is_file():
        out["message"] = f"Không có file PGM: {p}"
        return out
    try:
        _, w, h, maxval, _ = parse_pgm_header(p)
    except (ValueError, OSError) as e:
        out["message"] = str(e)
        return out
    y_res, y_ox, y_oy, y_yaw = _read_map_yaml_meta(p)
    resolution = float(OCC_GRID_MAP_RESOLUTION)
    if y_res is not None and y_res > 0:
        resolution = float(y_res)
    from_yaml = y_ox is not None and y_oy is not None
    if from_yaml:
        ox, oy = float(y_ox), float(y_oy)
        out["yaml_used"] = True
        bounds = compute_world_bounds(w, h, resolution, ox, oy, from_ros_yaml=True)
        out["origin_x"], out["origin_y"] = ox, oy
        yaw_note = ""
        if y_yaw is not None and abs(float(y_yaw)) > 1e-6:
            yaw_note = f" (bỏ qua origin yaw={y_yaw:.4f} rad — chỉ hỗ trợ thẳng trục)"
        out["message"] = f"PGM {w}×{h}, max {maxval}, resolution={resolution} từ map.yaml{yaw_note}"
    else:
        ox = float(OCC_GRID_MAP_ORIGIN_X)
        oy = float(OCC_GRID_MAP_ORIGIN_Y)
        bounds = compute_world_bounds(w, h, resolution, ox, oy, from_ros_yaml=False)
        out["origin_x"], out["origin_y"] = ox, oy
        out["message"] = (
            f"PGM {w}×{h}, max {maxval}, resolution={resolution} "
            f"(góc trên-trái ảnh ≈ ({ox:g}, {oy:g}) m — đặt map.yaml hoặc OCC_GRID_MAP_ORIGIN_* nếu lệch)."
        )
    out["success"] = True
    out["bounds"] = bounds
    out["width"] = w
    out["height"] = h
    out["resolution"] = resolution
    try:
        st = p.stat()
        out["mtime"] = int(st.st_mtime)
    except OSError:
        out["mtime"] = 0
    return out


def get_occ_grid_status(pgm_path: Optional[Path] = None) -> Dict[str, Any]:
    meta = build_occ_grid_meta(pgm_path)
    p = Path(pgm_path or OCC_GRID_MAP_PATH).resolve()
    status: Dict[str, Any] = {
        "path": str(p),
        "exists": p.is_file(),
        "valid": bool(meta.get("success")),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "resolution": meta.get("resolution"),
        "yaml_used": meta.get("yaml_used"),
        "message": meta.get("message", ""),
    }
    if p.is_file():
        try:
            status["size_bytes"] = p.stat().st_size
        except OSError:
            status["size_bytes"] = None
    return status


async def save_pgm_map_from_upload(
    upload: Any,
    yaml_upload: Any = None,
    max_bytes: Optional[int] = None,
) -> Tuple[bool, str]:
    dest = Path(OCC_GRID_MAP_PATH).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    limit = max_bytes if max_bytes is not None else OCC_GRID_MAP_MAX_BYTES
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="occ_", suffix=".pgm", dir=str(dest.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        total = 0
        with tmp_path.open("wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if limit and total > limit:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return False, f"File vượt quá {limit // (1024 * 1024)} MB (OCC_GRID_MAP_MAX_BYTES)."
                out.write(chunk)
        with tmp_path.open("rb") as fh:
            head = fh.read(4)
        if head[:2] not in (b"P2", b"P5"):
            return False, "Không phải PGM (magic phải là P2 hoặc P5)."
        parse_pgm_header(tmp_path)
        os.replace(tmp_path, dest)
        msg = f"Đã lưu PGM → {dest}"
        if yaml_upload is not None:
            ydest = _yaml_path_for_pgm(dest)
            raw_y = await yaml_upload.read()
            if raw_y and len(raw_y) < 2 * 1024 * 1024:
                ydest.write_bytes(raw_y)
                msg += f" và {ydest.name}"
        return True, msg
    except Exception as e:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False, str(e)
    finally:
        await upload.close()
        if yaml_upload is not None:
            await yaml_upload.close()
