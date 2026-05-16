"""
Điều phối giao hàng tự động (server + MQTT + Firestore).

- Đến giờ hẹn: pending/confirmed → in_progress, hoạch định Thư viện → điểm client (plan_field_route), publish path.
- Theo dõi UGV/control/vel.has_moving: có chuyển động rồi dừng → coi như robot đến điểm client.
- Chờ client xác nhận (tối đa HANDOFF_TIMEOUT_SEC); quá hạn → về Thư viện rồi hủy đơn.
- Client xác nhận → về Thư viện → hoàn thành.

Chỉ một đơn điều phối robot tại một thời điểm (hàng chờ theo thời gian hẹn).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import DELIVERY_HANDOFF_TIMEOUT_SEC, DELIVERY_LIBRARY_PICKUP_NAME
from app.services.admin_route_planner import plan_field_route
from app.services.db_service import db
from app.services.mqtt_client import mqtt_service
from app.services.pickup_locations_store import list_pickup_locations_admin

_TAG = "[DELIVERY]"

_ACTIVE_PHASES = frozenset({"outbound_sent", "at_client", "return_sent"})


def _vn_tz() -> timezone:
    return timezone(timedelta(hours=7))


def _parse_booking_pickup_dt_vn(b: Dict[str, Any]) -> Optional[datetime]:
    """Giờ hẹn ở múi VN (naive local)."""
    ds = str(b.get("pickup_date", "")).strip()
    ts = str(b.get("pickup_time", "")).strip()
    if not ds or not ts:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", ds)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    parts = ts.replace(".", ":").split(":")
    if len(parts) < 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        return None
    try:
        return datetime(y, mo, d, hh, mm, ss)
    except ValueError:
        return None


def _now_vn_naive() -> datetime:
    return datetime.now(_vn_tz()).replace(tzinfo=None)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_library_pickup_id() -> Optional[str]:
    target = (DELIVERY_LIBRARY_PICKUP_NAME or "Thư viện").strip().lower()
    for p in list_pickup_locations_admin():
        name = str(p.get("name", "")).strip().lower()
        if name == target:
            return str(p.get("id", "")).strip() or None
    return None


def _has_active_orchestration(bookings: List[Dict[str, Any]]) -> bool:
    for b in bookings:
        if b.get("status") != "in_progress":
            continue
        ph = b.get("delivery_phase")
        if ph in _ACTIVE_PHASES:
            return True
    return False


def _moving_state() -> Optional[bool]:
    return mqtt_service.get_robot_vel_has_moving()


def _publish_path_safe(payload: Dict[str, Any]) -> bool:
    return mqtt_service.publish_path(payload)


def _start_outbound_for_booking(b: Dict[str, Any], bid: str, lib_id: str, client_pickup_id: str) -> None:
    if str(client_pickup_id).strip() == str(lib_id).strip():
        print(f"{_TAG} bỏ qua đơn {bid}: điểm hẹn trùng Thư viện.")
        return
    planned = plan_field_route(lib_id, client_pickup_id)
    if not planned or not isinstance(planned.get("payload"), dict):
        print(f"{_TAG} không hoạch định được đường Thư viện → {client_pickup_id} cho đơn {bid}")
        return
    payload = planned["payload"]
    if not _publish_path_safe(payload):
        print(f"{_TAG} publish MQTT thất bại cho đơn {bid}")
        return
    db.collection("bookings").document(bid).update(
        {
            "status": "in_progress",
            "dispatched_at": _utc_now_iso(),
            "delivery_phase": "outbound_sent",
            "delivery_outbound_saw_moving": False,
            "delivery_return_saw_moving": False,
            "delivery_return_is_timeout_cancel": False,
            "delivery_user_confirmed_handoff": None,
            "delivery_at_client_deadline": None,
            "delivery_last_path_payload": payload,
        }
    )
    print(f"{_TAG} đơn {bid}: outbound đã publish (Thư viện → khách).")


def _maybe_advance_outbound(b: Dict[str, Any], bid: str) -> None:
    saw = bool(b.get("delivery_outbound_saw_moving"))
    hm = _moving_state()
    if hm is True:
        if not saw:
            db.collection("bookings").document(bid).update({"delivery_outbound_saw_moving": True})
        return
    if saw and hm is False:
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=DELIVERY_HANDOFF_TIMEOUT_SEC)).isoformat()
        db.collection("bookings").document(bid).update(
            {
                "delivery_phase": "at_client",
                "delivery_at_client_deadline": deadline,
            }
        )
        print(f"{_TAG} đơn {bid}: robot đã dừng tại điểm hẹn — chờ xác nhận client (tối đa {DELIVERY_HANDOFF_TIMEOUT_SEC}s).")


def _maybe_leave_at_client(b: Dict[str, Any], bid: str, lib_id: str, client_pickup_id: str) -> None:
    deadline_iso = b.get("delivery_at_client_deadline")
    confirmed = b.get("delivery_user_confirmed_handoff")
    now = datetime.now(timezone.utc)
    timed_out = False
    if deadline_iso and not confirmed:
        try:
            dl = datetime.fromisoformat(str(deadline_iso).replace("Z", "+00:00"))
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            timed_out = now >= dl
        except (ValueError, TypeError):
            timed_out = False

    if confirmed:
        is_timeout = False
    elif timed_out:
        is_timeout = True
    else:
        return

    planned = plan_field_route(client_pickup_id, lib_id)
    if not planned or not isinstance(planned.get("payload"), dict):
        print(f"{_TAG} không hoạch định về Thư viện cho đơn {bid}")
        return
    payload = planned["payload"]
    if not _publish_path_safe(payload):
        print(f"{_TAG} publish return thất bại đơn {bid}")
        return

    db.collection("bookings").document(bid).update(
        {
            "delivery_phase": "return_sent",
            "delivery_return_saw_moving": False,
            "delivery_return_is_timeout_cancel": is_timeout,
            "delivery_at_client_deadline": None,
        }
    )
    print(f"{_TAG} đơn {bid}: return đã publish ({'timeout' if is_timeout else 'client xác nhận'}).")


def _maybe_finish_return(b: Dict[str, Any], bid: str) -> None:
    saw = bool(b.get("delivery_return_saw_moving"))
    hm = _moving_state()
    if hm is True:
        if not saw:
            db.collection("bookings").document(bid).update({"delivery_return_saw_moving": True})
        return
    if not saw or hm is not False:
        return

    is_timeout = bool(b.get("delivery_return_is_timeout_cancel"))
    if is_timeout:
        db.collection("bookings").document(bid).update(
            {
                "status": "cancelled",
                "cancelled_at": _utc_now_iso(),
                "delivery_phase": None,
                "delivery_auto_cancel_reason": "timeout_no_handoff",
            }
        )
        print(f"{_TAG} đơn {bid}: đã về Thư viện — trạng thái Đã hủy (timeout).")
    else:
        db.collection("bookings").document(bid).update(
            {
                "status": "completed",
                "completed_at": _utc_now_iso(),
                "delivery_phase": None,
            }
        )
        print(f"{_TAG} đơn {bid}: đã về Thư viện — Hoàn thành.")


def _process_single_booking(b: Dict[str, Any], lib_id: str) -> None:
    bid = str(b.get("_id", "")).strip()
    if not bid:
        return
    phase = b.get("delivery_phase")
    client_pickup_id = str(b.get("pickup_location_id", "")).strip()

    if phase == "outbound_sent":
        _maybe_advance_outbound(b, bid)
        return
    if phase == "at_client":
        _maybe_leave_at_client(b, bid, lib_id, client_pickup_id)
        return
    if phase == "return_sent":
        _maybe_finish_return(b, bid)


def _pick_next_due_booking(bookings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    now = _now_vn_naive()
    cands: List[Tuple[datetime, Dict[str, Any]]] = []
    for b in bookings:
        if b.get("status") not in ("pending", "confirmed"):
            continue
        if b.get("delivery_phase"):
            continue
        pdt = _parse_booking_pickup_dt_vn(b)
        if pdt is None:
            continue
        if pdt <= now:
            cands.append((pdt, b))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    return cands[0][1]


def run_delivery_tick() -> None:
    """Gọi định kỳ từ scheduler (sync)."""
    try:
        all_bookings: List[Dict[str, Any]] = db.collection("bookings").get_all()
    except Exception as exc:
        print(f"{_TAG} db error: {exc}")
        return

    lib_id = _resolve_library_pickup_id()
    if not lib_id:
        print(f"{_TAG} chưa có pickup tên «{DELIVERY_LIBRARY_PICKUP_NAME}» trong catalog — bỏ qua điều phối.")
        return

    for b in all_bookings:
        if b.get("status") != "in_progress":
            continue
        ph = b.get("delivery_phase")
        if ph in _ACTIVE_PHASES:
            _process_single_booking(b, lib_id)

    if _has_active_orchestration(all_bookings):
        return

    nxt = _pick_next_due_booking(all_bookings)
    if not nxt:
        return
    bid = str(nxt.get("_id", "")).strip()
    cid = str(nxt.get("pickup_location_id", "")).strip()
    if not bid or not cid:
        return
    _start_outbound_for_booking(nxt, bid, lib_id, cid)
