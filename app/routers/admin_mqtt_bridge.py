"""
Cầu MQTT: Trình duyệt WebSocket → FastAPI → broker TCP 1883 (paho).
"""

from __future__ import annotations

import asyncio
import json
import random
import string
import threading

import paho.mqtt.client as paho_mqtt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import MQTT_BROKER_HOST, MQTT_BROKER_PORT_TCP, MQTT_USERNAME, MQTT_PASSWORD
from app.services.auth_service import decode_access_token

router = APIRouter(tags=["Admin MQTT Bridge"])

_TAG = "[MQTT-BRIDGE]"


def _random_cid() -> str:
    return "bookbot-br-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


@router.websocket("/api/admin/mqtt-bridge")
async def admin_mqtt_bridge(websocket: WebSocket) -> None:
    print(f"{_TAG} WebSocket mở từ {websocket.client}")
    await websocket.accept()

    token = websocket.cookies.get("access_token")
    if not token:
        print(f"{_TAG} Không có cookie access_token — đóng 4401")
        await websocket.close(code=4401)
        return
    payload = decode_access_token(token)
    if not payload or payload.get("role") != "admin":
        print(f"{_TAG} Token không hợp lệ hoặc không phải admin — đóng 4403")
        await websocket.close(code=4403)
        return

    print(f"{_TAG} Admin xác thực OK, kết nối tới broker {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT_TCP} ...")

    loop = asyncio.get_running_loop()
    outq: asyncio.Queue = asyncio.Queue()
    conn_event = threading.Event()
    conn_rc_box: list = [None]

    cid = _random_cid()
    client = paho_mqtt.Client(client_id=cid, protocol=paho_mqtt.MQTTv311)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or "")

    def _on_connect(c, ud, flags, rc):
        conn_rc_box[0] = rc
        conn_event.set()
        print(f"{_TAG} paho on_connect rc={rc} ({'OK' if rc == 0 else 'FAIL'})")
        asyncio.run_coroutine_threadsafe(
            outq.put({"type": "connack", "ok": rc == 0, "rc": rc}), loop
        )

    def _on_message(c, ud, msg):
        text = msg.payload.decode("utf-8", errors="replace")
        asyncio.run_coroutine_threadsafe(
            outq.put({"type": "msg", "t": msg.topic, "p": text}), loop
        )

    def _on_disconnect(c, ud, rc):
        print(f"{_TAG} paho on_disconnect rc={rc}")
        asyncio.run_coroutine_threadsafe(
            outq.put({"type": "disconnected", "rc": rc}), loop
        )

    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect

    try:
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT_TCP, 60)
    except Exception as e:
        print(f"{_TAG} connect() exception: {e}")
        await websocket.send_json({"type": "error", "message": f"Lỗi kết nối broker: {e}"})
        await websocket.close(code=4500)
        return

    client.loop_start()

    ok = conn_event.wait(timeout=15)
    if not ok or conn_rc_box[0] != 0:
        rc = conn_rc_box[0]
        print(f"{_TAG} CONNACK thất bại: timeout={not ok} rc={rc}")
        await websocket.send_json({"type": "error", "message": f"Broker từ chối hoặc timeout (rc={rc})"})
        client.loop_stop()
        client.disconnect()
        await websocket.close(code=4500)
        return

    print(f"{_TAG} Đã kết nối broker thành công — bắt đầu bridge")

    async def _sender():
        try:
            while True:
                item = await outq.get()
                if item is None:
                    break
                await websocket.send_json(item)
        except Exception:
            pass

    sender_task = asyncio.create_task(_sender())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                cmd = json.loads(raw)
            except json.JSONDecodeError:
                continue
            typ = cmd.get("type")

            if typ == "sub":
                topics = cmd.get("topics") or []
                qos = int(cmd.get("qos", 0))
                for t in topics:
                    if isinstance(t, str) and t:
                        client.subscribe(t, qos)
                        print(f"{_TAG} subscribe: {t}")

            elif typ == "pub":
                t = cmd.get("topic", "")
                p = cmd.get("payload", "")
                if isinstance(p, (dict, list)):
                    p = json.dumps(p, ensure_ascii=False)
                qos = int(cmd.get("qos", 0))
                if t:
                    client.publish(t, str(p).encode("utf-8"), qos)

            elif typ == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        print(f"{_TAG} WebSocket đóng bởi client")
    except Exception as e:
        print(f"{_TAG} Lỗi: {e}")
    finally:
        await outq.put(None)
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
        client.loop_stop()
        client.disconnect()
        print(f"{_TAG} Đã ngắt paho — bridge kết thúc")
