"""
Đọc / lưu point cloud PLY (ASCII hoặc binary little/big endian) cho Admin Tracking — viewer 3D.
"""

from __future__ import annotations

import base64
import math
import os
import struct
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import PLY_MAP_MAX_BYTES, PLY_MAP_MAX_PREVIEW_VERTICES, PLY_MAP_PATH


def get_ply_map_status(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path or PLY_MAP_PATH).resolve()
    out: Dict[str, Any] = {
        "path": str(p),
        "exists": False,
        "bytes": 0,
        "valid_ply": False,
        "vertex_count": 0,
        "format": None,
        "message": None,
    }
    try:
        if not p.is_file():
            return out
        out["exists"] = True
        out["bytes"] = int(p.stat().st_size)
        meta = _read_ply_vertex_meta(p)
        if meta is None:
            out["message"] = "Không đọc được header PLY hoặc thiếu element vertex."
            return out
        fmt, vcount, _props = meta
        out["valid_ply"] = True
        out["vertex_count"] = int(vcount)
        out["format"] = fmt
    except OSError as exc:
        out["message"] = str(exc)
    return out


def _read_ply_vertex_meta(path: Path) -> Optional[Tuple[str, int, List[Tuple[str, str]]]]:
    raw_acc = b""
    with path.open("rb") as f:
        while b"end_header" not in raw_acc and len(raw_acc) < 2_000_000:
            piece = f.read(65536)
            if not piece:
                break
            raw_acc += piece
    head = raw_acc
    if not head.startswith(b"ply"):
        return None
    end = head.find(b"end_header")
    if end < 0:
        return None
    nl = head.find(b"\n", end)
    if nl < 0:
        return None
    header_text = head[: nl + 1].decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in header_text.splitlines() if ln.strip()]
    fmt: Optional[str] = None
    elements: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for ln in lines:
        low = ln.lower()
        if low.startswith("format "):
            parts = ln.split()
            if len(parts) >= 2:
                fmt = parts[1].lower()
        elif low.startswith("element "):
            parts = ln.split()
            if len(parts) >= 3:
                cur = {"name": parts[1], "count": int(parts[2]), "props": []}
                elements.append(cur)
        elif low.startswith("property ") and cur is not None:
            parts = ln.split()
            if len(parts) >= 3:
                typ = parts[1].lower()
                if typ == "list":
                    if len(parts) >= 5:
                        cur["props"].append(("__list__", parts[2], parts[3], parts[4]))
                else:
                    name = parts[2]
                    cur["props"].append((name, typ))
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        return None
    vert = next((e for e in elements if e["name"] == "vertex"), None)
    if not vert or vert["count"] < 1:
        return None
    props = [(n, t) for (n, t) in vert["props"] if n != "__list__"]
    return fmt, int(vert["count"]), props


_PLY_STRUCT = {
    "char": "b",
    "uchar": "B",
    "short": "h",
    "ushort": "H",
    "int": "i",
    "uint": "I",
    "float": "f",
    "double": "d",
}


def _vertex_row_format(props: List[Tuple[str, str]], endian_prefix: str) -> Tuple[str, List[int]]:
    chars: List[str] = []
    need = {"x": None, "y": None, "z": None}
    idx = 0
    for name, typ in props:
        ch = _PLY_STRUCT.get(typ)
        if not ch:
            return "", []
        chars.append(ch)
        nl = name.lower()
        if nl in need and typ in ("float", "double"):
            need[nl] = idx
        idx += 1
    if need["x"] is None or need["y"] is None:
        floats = [i for i, (_, t) in enumerate(props) if t in ("float", "double")]
        if len(floats) >= 3:
            need["x"], need["y"], need["z"] = floats[0], floats[1], floats[2]
        else:
            return "", []
    if need["z"] is None:
        zf = next((i for i, (n, t) in enumerate(props) if t in ("float", "double") and n.lower() == "z"), None)
        need["z"] = zf if zf is not None else need["y"]
    fmt = endian_prefix + "".join(chars)
    xyz_idx = [int(need["x"]), int(need["y"]), int(need["z"])]
    return fmt, xyz_idx


def _ascii_vertex_line_to_xyz(
    line: str, props: List[Tuple[str, str]], xyz_idx: List[int]
) -> Optional[Tuple[float, float, float]]:
    parts = line.split()
    if len(parts) < len(props):
        return None
    vals: List[float] = []
    for i, (_, typ) in enumerate(props):
        if i >= len(parts):
            return None
        try:
            if typ in ("float", "double"):
                vals.append(float(parts[i]))
            elif typ in ("uchar", "uint", "ushort", "int", "short", "char"):
                vals.append(float(int(parts[i])))
            else:
                vals.append(float(parts[i]))
        except (ValueError, TypeError):
            return None
    try:
        return float(vals[xyz_idx[0]]), float(vals[xyz_idx[1]]), float(vals[xyz_idx[2]])
    except (IndexError, TypeError, ValueError):
        return None


def _ply_data_start_byte(path: Path) -> Optional[int]:
    """Byte offset trong file ngay sau dòng ``end_header``."""
    raw_acc = b""
    with path.open("rb") as f:
        while b"end_header" not in raw_acc and len(raw_acc) < 2_000_000:
            piece = f.read(65536)
            if not piece:
                break
            raw_acc += piece
    end = raw_acc.find(b"end_header")
    if end < 0:
        return None
    nl = raw_acc.find(b"\n", end)
    if nl < 0:
        return None
    return nl + 1


def build_ply_preview_payload(path: Optional[str] = None) -> Dict[str, Any]:
    """Trả bounds + positions float32 xyz (base64), giới hạn số điểm bằng stride. Đọc stream — không nạp cả file vào RAM."""
    p = Path(path or PLY_MAP_PATH).resolve()
    if not p.is_file():
        return {"success": False, "message": "Không có file PLY trên server."}
    meta = _read_ply_vertex_meta(p)
    if not meta:
        return {"success": False, "message": "File không phải PLY hợp lệ hoặc thiếu vertex."}
    fmt, vcount, props = meta
    max_v = max(1000, int(PLY_MAP_MAX_PREVIEW_VERTICES))
    stride = max(1, int(math.ceil(vcount / max_v)))

    if fmt == "binary_little_endian":
        ep = "<"
    elif fmt == "binary_big_endian":
        ep = ">"
    else:
        ep = "<"
    row_fmt, xyz_idx = _vertex_row_format(props, ep)
    if not row_fmt:
        return {"success": False, "message": "PLY: không suy ra được x,y,z từ các property."}
    row_size = struct.calcsize(row_fmt)

    xs: List[float] = []
    ys: List[float] = []
    zs: List[float] = []
    out_xyz = bytearray()

    try:
        data_start = _ply_data_start_byte(p)
        if data_start is None:
            return {"success": False, "message": "Thiếu end_header / header PLY lỗi."}

        if fmt == "ascii":
            with p.open("rb") as f:
                f.seek(data_start)
                for vi in range(vcount):
                    raw_line = f.readline()
                    if not raw_line:
                        break
                    if vi % stride != 0:
                        continue
                    try:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                    except UnicodeError:
                        continue
                    if not line:
                        continue
                    t = _ascii_vertex_line_to_xyz(line, props, xyz_idx)
                    if not t:
                        continue
                    x, y, z = t
                    xs.append(x)
                    ys.append(y)
                    zs.append(z)
                    out_xyz.extend(struct.pack("<fff", x, y, z))
        else:
            need_bytes = vcount * row_size
            if p.stat().st_size < data_start + need_bytes:
                return {"success": False, "message": "File PLY binary ngắn hơn số vertex khai báo."}
            with p.open("rb") as f:
                f.seek(data_start)
                for vi in range(vcount):
                    if vi % stride != 0:
                        f.seek(row_size, os.SEEK_CUR)
                        continue
                    row = f.read(row_size)
                    if len(row) < row_size:
                        break
                    try:
                        vals = struct.unpack(row_fmt, row)
                    except struct.error:
                        continue
                    x = float(vals[xyz_idx[0]])
                    y = float(vals[xyz_idx[1]])
                    z = float(vals[xyz_idx[2]])
                    xs.append(x)
                    ys.append(y)
                    zs.append(z)
                    out_xyz.extend(struct.pack("<fff", x, y, z))
    except (OSError, MemoryError) as exc:
        return {"success": False, "message": str(exc)}

    if not xs:
        return {"success": False, "message": "Không đọc được điểm vertex nào."}

    n = len(xs)
    return {
        "success": True,
        "vertex_count_raw": vcount,
        "vertex_count_preview": n,
        "stride": stride,
        "format": fmt,
        "bounds": {
            "xmin": min(xs),
            "xmax": max(xs),
            "ymin": min(ys),
            "ymax": max(ys),
            "zmin": min(zs),
            "zmax": max(zs),
        },
        "positions_b64": base64.standard_b64encode(bytes(out_xyz)).decode("ascii"),
        "colors_b64": None,
    }


async def save_ply_map_from_upload(upload: Any, max_bytes: Optional[int] = None) -> Tuple[bool, str]:
    """Ghi upload vào ``PLY_MAP_PATH`` (ghi đè)."""
    dest = Path(PLY_MAP_PATH).resolve()
    limit = max_bytes if max_bytes is not None else PLY_MAP_MAX_BYTES
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Không tạo được thư mục: {exc}"

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="ply_", suffix=".ply", dir=str(dest.parent))
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
                    return False, f"File vượt quá {limit // (1024 * 1024)} MB (PLY_MAP_MAX_BYTES)."
                out.write(block)

        head = tmp_path.read_bytes()[:65536]
        if not head.startswith(b"ply"):
            return False, "Không phải file PLY (thiếu magic 'ply')."
        if b"element vertex" not in head:
            return False, "PLY không chứa element vertex trong phần đầu file."

        meta = _read_ply_vertex_meta(tmp_path)
        if not meta:
            return False, "PLY không hợp lệ (header / vertex)."

        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        tmp_path.replace(dest)
        replaced = True
        return True, f"Đã lưu PLY ({meta[1]} vertex). Mở Tracking → tab Đám mây PLY để xem."
    except OSError as exc:
        return False, str(exc)
    finally:
        if not replaced and tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass
