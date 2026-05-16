"""
Xuất log hành trình test MQTT ra Excel dạng **wide / snapshot**:
- **10 Hz** (một dòng / 100 ms): bucket theo mốc thời gian neo từ **timestamp_local** đã parse;
  phần mili-giây trong cùng giây lấy từ **ISO ``t``** nếu có (để đủ độ phân giải khi ``t_local`` chỉ có tới giây).
- Cột thời gian: chỉ **timestamp_local** (không còn ``timestamp_iso``).
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import MQTT_TOPIC_STATUS, ROBOT_STATUS_UGV_TOPICS


def _topic_to_message_key() -> Dict[str, str]:
    """topic MQTT -> key nội bộ (pose, curr_vel, …)."""
    m: Dict[str, str] = {}
    for k, v in ROBOT_STATUS_UGV_TOPICS.items():
        if v:
            m[str(v)] = str(k)
    st = (MQTT_TOPIC_STATUS or "").strip()
    if st:
        m[st] = "robot_status"
    return m


def _json_obj(raw: str) -> Optional[dict]:
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _dig_float(o: dict, a: str, b: str) -> Optional[float]:
    sub = o.get(a)
    if isinstance(sub, dict) and b in sub:
        return _to_float(sub.get(b))
    return None


def _pick_vel_left(o: dict) -> Optional[float]:
    x = _to_float(o.get("left"))
    if x is not None:
        return x
    x = _to_float(o.get("left_vel"))
    if x is not None:
        return x
    return _dig_float(o, "real_vel", "left_vel")


def _pick_vel_right(o: dict) -> Optional[float]:
    x = _to_float(o.get("right"))
    if x is not None:
        return x
    x = _to_float(o.get("right_vel"))
    if x is not None:
        return x
    return _dig_float(o, "real_vel", "right_vel")


def _pick_ctrl_left(o: dict) -> Optional[float]:
    x = _to_float(o.get("left"))
    if x is not None:
        return x
    x = _to_float(o.get("left_vel"))
    if x is not None:
        return x
    return _dig_float(o, "control_vel", "left_vel")


def _pick_ctrl_right(o: dict) -> Optional[float]:
    x = _to_float(o.get("right"))
    if x is not None:
        return x
    x = _to_float(o.get("right_vel"))
    if x is not None:
        return x
    return _dig_float(o, "control_vel", "right_vel")


def _boolish(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def _merge_payload(state: Dict[str, Any], msg_key: str, o: dict) -> None:
    if msg_key == "pose":
        if "x" in o:
            state["pose_x"] = _to_float(o.get("x"))
        if "y" in o:
            state["pose_y"] = _to_float(o.get("y"))
        if "yaw" in o:
            state["pose_yaw"] = _to_float(o.get("yaw"))
        return
    if msg_key == "has_locked":
        if "lock" in o:
            state["has_locked"] = _boolish(o.get("lock"))
        elif "has_locked" in o:
            state["has_locked"] = _boolish(o.get("has_locked"))
        return
    if msg_key == "arrival":
        if "has_arrial" in o:
            state["has_arrival"] = _boolish(o.get("has_arrial"))
        elif "has_arrival" in o:
            state["has_arrival"] = _boolish(o.get("has_arrival"))
        return
    if msg_key == "curr_vel":
        lv = _pick_vel_left(o)
        rv = _pick_vel_right(o)
        if lv is not None:
            state["curr_vel_left"] = lv
        if rv is not None:
            state["curr_vel_right"] = rv
        return
    if msg_key == "vel":
        lv = _pick_ctrl_left(o)
        rv = _pick_ctrl_right(o)
        if lv is not None:
            state["vel_ctrl_left"] = lv
        if rv is not None:
            state["vel_ctrl_right"] = rv
        if "has_moving" in o:
            b = _boolish(o.get("has_moving"))
            if b is not None:
                state["has_moving"] = b
        return
    if msg_key == "para":
        la = o.get("look_ahead")
        if la is None:
            la = o.get("lookAhead")
        v = _to_float(la)
        if v is not None:
            state["para_look_ahead"] = v
        ct = _to_float(o.get("cross_track"))
        if ct is not None:
            state["para_cross_track"] = ct
        at = _to_float(o.get("along_track"))
        if at is not None:
            state["para_along_track"] = at
        dh = _to_float(o.get("desired_heading"))
        if dh is not None:
            state["para_desired_heading"] = dh
        return
    if msg_key == "byte_per_sec":
        bs = _to_float(o.get("byte_sensor"))
        if bs is None:
            bs = _to_float(o.get("byte_yaw"))
        if bs is not None:
            state["byte_sensor"] = bs
        if "byte_vel" in o:
            bv = _to_float(o.get("byte_vel"))
            if bv is not None:
                state["byte_vel"] = bv
        return
    if msg_key == "state_gps":
        if "mode_gps" in o:
            state["state_gps_mode"] = _to_float(o.get("mode_gps"))
        return
    if msg_key == "heading":
        if "heading" in o:
            state["heading"] = _to_float(o.get("heading"))
        return
    if msg_key == "heading_gps":
        if "heading" in o:
            state["heading_gps"] = _to_float(o.get("heading"))
        return
    if msg_key == "gps":
        try:
            state["gps_json"] = json.dumps(o, ensure_ascii=False)[:4000]
        except (TypeError, ValueError):
            state["gps_json"] = str(o)[:4000]
        return
    if msg_key == "robot_status":
        try:
            state["robot_status_json"] = json.dumps(o, ensure_ascii=False)[:8000]
        except (TypeError, ValueError):
            state["robot_status_json"] = str(o)[:8000]
        for k, dst in (
            ("has_moving", "has_moving"),
            ("has_locked", "has_locked"),
            ("lock", "has_locked"),
            ("x", "pose_x"),
            ("y", "pose_y"),
            ("yaw", "pose_yaw"),
        ):
            if k in o and o[k] is not None:
                if dst == "has_moving":
                    b = _boolish(o.get(k))
                    if b is not None:
                        state["has_moving"] = b
                elif dst == "has_locked":
                    b = _boolish(o.get(k))
                    if b is not None:
                        state["has_locked"] = b
                else:
                    f = _to_float(o.get(k))
                    if f is not None:
                        state[dst] = f
        return


_BUCKET_MS = 100
_EPOCH_NAIVE = datetime(1970, 1, 1)


def _naive_datetime_to_sort_ms(dt: datetime) -> int:
    """Độ lệch ms (lịch naive) từ 1970-01-01 — dùng cho `t_local` không gắn timezone máy chủ."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return int((dt - _EPOCH_NAIVE).total_seconds() * 1000)


def _try_parse_t_local(s: str) -> Optional[datetime]:
    """Parse chuỗi kiểu trình duyệt vi-VN, ví dụ ``16:18:17 9/5/2026`` hoặc ``9/5/2026, 16:18:17``."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{1,2}):(\d{1,2}):(\d{1,2})\s+(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        h, mi, se, d, mo, y = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d, h, mi, se)
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})[,\s]+(\d{1,2}):(\d{1,2}):(\d{1,2})", s)
    if m:
        d, mo, y, h, mi, se = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d, h, mi, se)
        except ValueError:
            return None
    return None


def _iso_fraction_ms(iso: str) -> int:
    """Phần mili-giây 0..999 từ chuỗi ISO (vd ``...17.123Z``) nếu có."""
    s = (iso or "").strip()
    if "." not in s:
        return 0
    try:
        after = s.split(".", 1)[1]
        for sep in ("Z", "+", "-"):
            if sep in after:
                after = after.split(sep, 1)[0]
                break
        digits = "".join(ch for ch in after if ch.isdigit())
        if not digits:
            return 0
        digits = (digits + "000")[:3]
        return int(digits)
    except (ValueError, IndexError):
        return 0


def _try_iso_to_epoch_ms(s: str) -> Optional[int]:
    t = (s or "").strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OSError):
        return None


def _row_timeline_ms(row: dict) -> int:
    """Trục thời gian cho sắp xếp và bucket 10 Hz: neo theo ``t_local`` (lịch), bù phần ms từ ``t`` ISO nếu có."""
    loc = str(row.get("t_local") or "").strip()
    iso = str(row.get("t") or "").strip()
    dt_loc = _try_parse_t_local(loc) if loc else None
    if dt_loc is not None:
        base = (_naive_datetime_to_sort_ms(dt_loc) // 1000) * 1000
        return base + min(_iso_fraction_ms(iso), 999)
    return _try_iso_to_epoch_ms(iso) or 0


def _sorted_dict_rows(rows: List[Any]) -> List[dict]:
    pairs = [(i, r) for i, r in enumerate(rows) if isinstance(r, dict)]
    pairs.sort(key=lambda ir: (_row_timeline_ms(ir[1]), ir[0]))
    return [r for _, r in pairs]


def _ingest_log_row(state: Dict[str, Any], r: dict, topic_map: Dict[str, str]) -> None:
    topic = str(r.get("topic") or "")
    payload = str(r.get("payload") or "")
    msg_key = topic_map.get(topic)
    if msg_key:
        o = _json_obj(payload)
        if o is not None:
            _merge_payload(state, msg_key, o)
        elif msg_key in ("heading", "heading_gps"):
            n = _to_float(str(payload).strip())
            if n is not None:
                if msg_key == "heading":
                    state["heading"] = n
                else:
                    state["heading_gps"] = n
    hm = r.get("has_moving")
    if hm is True or hm is False:
        state["has_moving"] = hm


def _append_snapshot_row(ws: Any, state: Dict[str, Any], loc: str, topic: str, data_cols: Tuple[str, ...]) -> None:
    out_row: List[Any] = [loc, topic]
    for col in data_cols:
        v = state.get(col)
        if v is None:
            out_row.append("")
        elif isinstance(v, bool):
            out_row.append("TRUE" if v else "FALSE")
        elif isinstance(v, float):
            out_row.append(v)
        else:
            out_row.append(str(v))
    max_cell = 32000
    for i, cell in enumerate(out_row):
        if isinstance(cell, str) and len(cell) > max_cell:
            out_row[i] = cell[: max_cell - 24] + "…(truncated)"
    ws.append(out_row)


# Cột dữ liệu: desired_heading kề pose_yaw để so sánh heading điều khiển vs pose.
_DATA_COLS: Tuple[str, ...] = (
    "pose_x",
    "pose_y",
    "para_cross_track",
    "para_along_track",
    "para_desired_heading",
    "pose_yaw",
    "has_moving",
    "has_locked",
    "has_arrival",
    "curr_vel_left",
    "curr_vel_right",
    "vel_ctrl_left",
    "vel_ctrl_right",
    "para_look_ahead",
    "byte_sensor",
    "byte_vel",
    "state_gps_mode",
    "heading",
    "heading_gps",
    "gps_json",
    "robot_status_json",
)


def build_test_journey_xlsx_bytes(rows: List[Any], max_input_rows: int = 50_000) -> bytes:
    from openpyxl import Workbook  # type: ignore
    from openpyxl.styles import Font  # type: ignore

    if not isinstance(rows, list):
        raise ValueError("rows phải là list.")
    if len(rows) > max_input_rows:
        raise ValueError(f"Tối đa {max_input_rows} dòng đầu vào.")

    topic_map = _topic_to_message_key()
    state: Dict[str, Any] = {c: None for c in _DATA_COLS}

    sorted_rows = _sorted_dict_rows(rows)

    wb = Workbook()
    ws = wb.active
    ws.title = "robot_snapshot"
    head = ["timestamp_local", "topic"] + list(_DATA_COLS)
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)

    prev_bid: Optional[int] = None
    emit_loc, emit_topic = "", ""

    for r in sorted_rows:
        bid = _row_timeline_ms(r) // _BUCKET_MS
        if prev_bid is not None and bid != prev_bid:
            _append_snapshot_row(ws, state, emit_loc, emit_topic, _DATA_COLS)
        _ingest_log_row(state, r, topic_map)
        emit_loc = str(r.get("t_local") or "")
        emit_topic = str(r.get("topic") or "")
        prev_bid = bid
    if prev_bid is not None:
        _append_snapshot_row(ws, state, emit_loc, emit_topic, _DATA_COLS)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
