"""Background scheduler: dispatch bookings when their pickup time arrives.

Flow for each due booking:
1. Update status  pending/confirmed → in_progress
2. Compute Dijkstra route  robot → pickup → library
3. Publish {"stage_x":[], "stage_y":[]} via MQTT so the robot starts moving
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from app.config import SCHEDULER_INTERVAL_SEC
from app.services.db_service import db
from app.services.mqtt_client import mqtt_service
from app.services.pathfinding_service import build_dispatch_route

_TAG = "[SCHEDULER]"
_TZ_VN = timezone(timedelta(hours=7))
_task: asyncio.Task | None = None


def _get_due_bookings() -> list[dict]:
    """Return bookings whose pickup datetime <= now (VN timezone)."""
    now = datetime.now(_TZ_VN)
    today_str = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    try:
        all_bookings = db.collection("bookings").get_all()
    except Exception as exc:
        print(f"{_TAG} db error: {exc}")
        return []

    due: list[dict] = []
    for b in all_bookings:
        status = b.get("status", "")
        if status not in ("pending", "confirmed"):
            continue

        b_date = b.get("pickup_date", "")
        b_time = b.get("pickup_time", "")
        if not b_date or not b_time:
            continue

        if b_date < today_str or (b_date == today_str and b_time <= now_time_str):
            due.append(b)

    return due


async def _tick() -> None:
    due = _get_due_bookings()
    if not due:
        return

    for b in due:
        bid = b.get("_id", "???")
        loc_id = b.get("pickup_location_id", "")
        loc_name = b.get("pickup_location_name", loc_id)

        print(f"{_TAG} Đơn {bid} ({loc_name}) đến giờ — dispatching …")

        db.collection("bookings").document(bid).update({
            "status": "in_progress",
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        })

        payload = build_dispatch_route(
            mqtt_service.robot_lat,
            mqtt_service.robot_lon,
            loc_id,
        )
        if payload is None:
            print(f"{_TAG}   ⚠ không tìm được route cho {loc_id}")
            continue

        ok = mqtt_service.publish_path(payload)
        n = len(payload["stage_x"])
        if ok:
            print(f"{_TAG}   ✓ đã gửi {n} waypoints lên MQTT")
        else:
            print(f"{_TAG}   ✗ publish thất bại")


async def _loop() -> None:
    print(f"{_TAG} started (interval={SCHEDULER_INTERVAL_SEC}s)")
    while True:
        try:
            await _tick()
        except Exception as exc:
            print(f"{_TAG} tick error: {exc}")
        await asyncio.sleep(SCHEDULER_INTERVAL_SEC)


def start_scheduler() -> None:
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_loop())


def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    _task = None
    print(f"{_TAG} stopped")
