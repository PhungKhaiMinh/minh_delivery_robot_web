"""
Thư viện nhiều bản đồ occupancy (PGM + YAML) — lưu dưới ``data/occ_maps/<id>/`` + ``registry.json``.
Tracking chọn ``map_id``; không có id hợp lệ thì dùng ``active_id`` hoặc ``OCC_GRID_MAP_PATH`` (fallback env).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import BASE_DIR, OCC_GRID_MAP_MAX_BYTES, OCC_GRID_MAP_PATH
from app.services.pgm_map_service import parse_pgm_header

REGISTRY_FN = "registry.json"
PGM_FN = "map.pgm"
YAML_FN = "map.yaml"
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def storage_root() -> Path:
    d = (BASE_DIR / "data" / "occ_maps").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return storage_root() / REGISTRY_FN


def _default_registry() -> Dict[str, Any]:
    return {"version": 1, "active_id": None, "maps": []}


def load_registry() -> Dict[str, Any]:
    p = _registry_path()
    if not p.is_file():
        return _default_registry()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return _default_registry()
        raw.setdefault("version", 1)
        raw.setdefault("active_id", None)
        raw.setdefault("maps", [])
        if not isinstance(raw["maps"], list):
            raw["maps"] = []
        return raw
    except (json.JSONDecodeError, OSError, TypeError):
        return _default_registry()


def save_registry(data: Dict[str, Any]) -> None:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def validate_map_id(map_id: str) -> bool:
    return bool(map_id and _ID_RE.match(map_id))


def map_dir(map_id: str) -> Path:
    return storage_root() / map_id


def resolve_occ_pgm_path(map_id: Optional[str]) -> Optional[Path]:
    """
    Trả đường dẫn PGM để đọc meta/PNG.
    ``map_id`` rỗng: active trong registry nếu có file; không thì ``OCC_GRID_MAP_PATH``.
    """
    rid = (map_id or "").strip()
    reg = load_registry()
    if rid:
        if not validate_map_id(rid):
            return None
        cand = (map_dir(rid) / PGM_FN).resolve()
        return cand if cand.is_file() else None
    aid = reg.get("active_id")
    if isinstance(aid, str) and aid.strip():
        cand = (map_dir(aid.strip()) / PGM_FN).resolve()
        if cand.is_file():
            return cand
    env_p = Path(OCC_GRID_MAP_PATH).resolve()
    return env_p if env_p.is_file() else None


def list_maps_public() -> Dict[str, Any]:
    reg = load_registry()
    out_maps: List[Dict[str, Any]] = []
    for m in reg.get("maps") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        mid = str(m["id"])
        if not validate_map_id(mid):
            continue
        d = map_dir(mid)
        pf = d / PGM_FN
        yf = d / YAML_FN
        out_maps.append(
            {
                "id": mid,
                "label": str(m.get("label") or mid),
                "created_ms": int(m.get("created_ms") or 0),
                "has_yaml": yf.is_file(),
                "has_pgm": pf.is_file(),
            }
        )
    env_p = Path(OCC_GRID_MAP_PATH).resolve()
    return {
        "success": True,
        "maps": out_maps,
        "active_id": reg.get("active_id"),
        "env_fallback_path": str(env_p),
        "env_fallback_exists": env_p.is_file(),
    }


def set_active_map(map_id: Optional[str]) -> Tuple[bool, str]:
    """``map_id`` rỗng = bỏ chọn thư viện (dùng env PGM)."""
    reg = load_registry()
    if not map_id or not str(map_id).strip():
        reg["active_id"] = None
        save_registry(reg)
        return True, "Đã chọn bản đồ mặc định (OCC_GRID_MAP_PATH)."
    mid = str(map_id).strip()
    if not validate_map_id(mid):
        return False, "map_id không hợp lệ."
    if not (map_dir(mid) / PGM_FN).is_file():
        return False, "Không tìm thấy PGM cho map_id này."
    reg["active_id"] = mid
    save_registry(reg)
    return True, f"Đã đặt bản đồ active: {mid}"


def delete_map(map_id: str) -> Tuple[bool, str]:
    mid = str(map_id).strip()
    if not validate_map_id(mid):
        return False, "map_id không hợp lệ."
    reg = load_registry()
    maps: List[Dict[str, Any]] = [m for m in (reg.get("maps") or []) if isinstance(m, dict) and str(m.get("id")) != mid]
    if len(maps) == len(reg.get("maps") or []):
        return False, "Không có map này trong registry."
    d = map_dir(mid)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    reg["maps"] = maps
    if reg.get("active_id") == mid:
        reg["active_id"] = str(maps[0]["id"]) if maps and maps[0].get("id") else None
    save_registry(reg)
    return True, f"Đã xóa map {mid}"


async def create_map_from_upload(
    label: str,
    pgm_upload: Any,
    yaml_upload: Any = None,
    max_bytes: Optional[int] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Lưu PGM (+ yaml) vào thư mục mới, đặt active, cập nhật registry."""
    limit = max_bytes if max_bytes is not None else OCC_GRID_MAP_MAX_BYTES
    for _ in range(20):
        mid = secrets.token_hex(8)
        if not map_dir(mid).exists():
            break
    else:
        return False, "Không tạo được id map.", None

    d = map_dir(mid)
    try:
        d.mkdir(parents=True, exist_ok=False)
    except OSError:
        return False, "Không tạo thư mục map.", None

    dest_pgm = d / PGM_FN
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="occ_", suffix=".pgm", dir=str(d))
    try:
        os.close(tmp_fd)
    except OSError:
        pass
    tmp_path = Path(tmp_name)
    try:
        total = 0
        with tmp_path.open("wb") as out:
            while True:
                chunk = await pgm_upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if limit and total > limit:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    shutil.rmtree(d, ignore_errors=True)
                    return False, f"File vượt quá {limit // (1024 * 1024)} MB (OCC_GRID_MAP_MAX_BYTES).", None
                out.write(chunk)
        with tmp_path.open("rb") as fh:
            head = fh.read(4)
        if head[:2] not in (b"P2", b"P5"):
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            shutil.rmtree(d, ignore_errors=True)
            return False, "Không phải PGM (magic phải là P2 hoặc P5).", None
        parse_pgm_header(tmp_path)
        tmp_path.replace(dest_pgm)
        msg = f"Đã lưu PGM → {dest_pgm}"
        if yaml_upload is not None:
            raw_y = await yaml_upload.read()
            if raw_y and len(raw_y) < 2 * 1024 * 1024:
                (d / YAML_FN).write_bytes(raw_y)
                msg += f" và {YAML_FN}"
        lbl = (label or "").strip() or mid
        reg = load_registry()
        maps = [m for m in (reg.get("maps") or []) if isinstance(m, dict) and str(m.get("id")) != mid]
        maps.append({"id": mid, "label": lbl, "created_ms": int(__import__("time").time() * 1000)})
        reg["maps"] = maps
        reg["active_id"] = mid
        save_registry(reg)
        return True, msg, mid
    except Exception as e:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(d, ignore_errors=True)
        return False, str(e), None
    finally:
        await pgm_upload.close()
        if yaml_upload is not None:
            await yaml_upload.close()
