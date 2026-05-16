"""Background scheduler: điều phối giao hàng tự động (đến giờ hẹn, MQTT, trạng thái đơn)."""

from __future__ import annotations

import asyncio

from app.config import SCHEDULER_INTERVAL_SEC
from app.services.delivery_orchestration import run_delivery_tick

_TAG = "[SCHEDULER]"
_task: asyncio.Task | None = None


async def _loop() -> None:
    print(f"{_TAG} started (interval={SCHEDULER_INTERVAL_SEC}s)")
    while True:
        try:
            await asyncio.to_thread(run_delivery_tick)
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
