"""Server-side MQTT client singleton.

Subscribes to robot GPS for position tracking.
Publishes waypoint paths (stage_x / stage_y) for the scheduler.
Runs paho network-loop in a daemon thread so it doesn't block asyncio.
"""

from __future__ import annotations

import json
import random
import string
import threading
from typing import Optional

import paho.mqtt.client as paho_mqtt

from app.config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT_TCP,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_TOPIC_PATH,
    MQTT_TOPIC_POSITION,
    MQTT_TOPIC_GPS_BASE,
    MQTT_UGV_TOPIC_POSE,
    MQTT_UGV_TOPIC_VEL,
)
from app.services.pathfinding_service import local_to_gps

_TAG = "[MQTT-CLIENT]"

_PATH_FLOAT_KEYS = frozenset({"stage_x", "stage_y", "stage_x_margin", "stage_y_margin"})


def mqtt_path_payload_as_float_dict(payload: dict) -> dict:
    """
    Shallow copy of a path payload where coordinate arrays are ``list[float]``.
    Ensures JSON encodes numbers as floats (e.g. ``0.0`` not ``0``).
    """
    out: dict = {}
    for k, v in payload.items():
        if k in _PATH_FLOAT_KEYS and isinstance(v, list):
            out[k] = [float(x) for x in v]
        else:
            out[k] = v
    return out


class _MqttService:
    """Lightweight wrapper around a single paho.mqtt.Client."""

    def __init__(self) -> None:
        self._client: Optional[paho_mqtt.Client] = None
        self._connected = False
        self._lock = threading.Lock()

        self.robot_lat: Optional[float] = None
        self.robot_lon: Optional[float] = None
        self.robot_alt: Optional[float] = None
        self.robot_pose_x: Optional[float] = None
        self.robot_pose_y: Optional[float] = None
        self.robot_pose_yaw: Optional[float] = None
        self._vel_lock = threading.Lock()
        self.robot_vel_has_moving: Optional[bool] = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._client is not None:
            return
        cid = "bookbot-svc-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        c = paho_mqtt.Client(client_id=cid, protocol=paho_mqtt.MQTTv311)
        if MQTT_USERNAME:
            c.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD or "")
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.on_disconnect = self._on_disconnect
        try:
            c.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT_TCP, keepalive=60)
        except Exception as exc:
            print(f"{_TAG} connect failed: {exc}")
            return
        c.loop_start()
        self._client = c
        print(f"{_TAG} started (cid={cid})")

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._client = None
        self._connected = False
        print(f"{_TAG} stopped")

    @property
    def connected(self) -> bool:
        return self._connected

    def get_robot_vel_has_moving(self) -> Optional[bool]:
        """Trạng thái di chuyển từ JSON ``UGV/control/vel`` (field ``has_moving``), hoặc None nếu chưa nhận."""
        with self._vel_lock:
            return self.robot_vel_has_moving

    # ------------------------------------------------------------------
    # paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client: paho_mqtt.Client, _ud: object, _flags: dict, rc: int) -> None:
        if rc == 0:
            self._connected = True
            client.subscribe(MQTT_TOPIC_POSITION, qos=0)
            client.subscribe(MQTT_UGV_TOPIC_POSE, qos=0)
            client.subscribe(MQTT_UGV_TOPIC_VEL, qos=0)
            print(
                f"{_TAG} connected, subscribed {MQTT_TOPIC_POSITION} + {MQTT_UGV_TOPIC_POSE} + {MQTT_UGV_TOPIC_VEL}"
            )
        else:
            print(f"{_TAG} connect rc={rc}")

    def _on_message(self, _client: paho_mqtt.Client, _ud: object, msg: paho_mqtt.MQTTMessage) -> None:
        try:
            if msg.topic == MQTT_UGV_TOPIC_POSE:
                data = json.loads(msg.payload)
                if not isinstance(data, dict):
                    return
                x = data.get("x")
                y = data.get("y")
                yw = data.get("yaw")
                if x is not None and y is not None:
                    fx, fy = float(x), float(y)
                    self.robot_pose_x, self.robot_pose_y = fx, fy
                    try:
                        self.robot_lat, self.robot_lon = local_to_gps(fx, fy)
                    except Exception:
                        pass
                if yw is not None:
                    try:
                        self.robot_pose_yaw = float(yw)
                    except (TypeError, ValueError):
                        self.robot_pose_yaw = None
                return
            if msg.topic == MQTT_UGV_TOPIC_VEL:
                data = json.loads(msg.payload)
                if isinstance(data, dict) and "has_moving" in data:
                    try:
                        hm = bool(data.get("has_moving"))
                    except (TypeError, ValueError):
                        hm = None
                    if hm is not None:
                        with self._vel_lock:
                            self.robot_vel_has_moving = hm
                return
            if msg.topic == MQTT_TOPIC_POSITION:
                data = json.loads(msg.payload)
                lat = data.get("lat")
                lon = data.get("lon", data.get("lng"))
                if lat is not None and lon is not None:
                    self.robot_lat = float(lat)
                    self.robot_lon = float(lon)
                alt = data.get("alt", data.get("altitude", data.get("msl")))
                if alt is not None:
                    try:
                        self.robot_alt = float(alt)
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

    def _on_disconnect(self, _client: paho_mqtt.Client, _ud: object, rc: int) -> None:
        self._connected = False
        print(f"{_TAG} disconnected rc={rc}")

    # ------------------------------------------------------------------
    # publish
    # ------------------------------------------------------------------

    def publish_path(self, payload: dict) -> bool:
        """Publish path JSON to ``MQTT_TOPIC_PATH`` (all path coordinates as JSON floats)."""
        if self._client is None or not self._connected:
            print(f"{_TAG} cannot publish — not connected")
            return False
        try:
            pl = mqtt_path_payload_as_float_dict(payload)
        except (TypeError, ValueError) as exc:
            print(f"{_TAG} publish_path: cannot coerce path numbers to float: {exc}")
            return False
        sx = pl.get("stage_x")
        sy = pl.get("stage_y")
        if not isinstance(sx, list) or not isinstance(sy, list) or len(sx) != len(sy):
            print(f"{_TAG} publish_path: invalid stage_x/stage_y")
            return False
        smx = pl.get("stage_x_margin")
        smy = pl.get("stage_y_margin")
        if smx is not None or smy is not None:
            if not isinstance(smx, list) or not isinstance(smy, list):
                print(f"{_TAG} publish_path: invalid margin arrays")
                return False
            n = len(sx)
            if len(smx) != n or len(smy) != n:
                print(f"{_TAG} publish_path: margin array length mismatch")
                return False
        raw = json.dumps(pl)
        info = self._client.publish(MQTT_TOPIC_PATH, raw, qos=1)
        print(f"{_TAG} published path → {MQTT_TOPIC_PATH} ({len(raw)} bytes)")
        return info.rc == paho_mqtt.MQTT_ERR_SUCCESS

    def publish_json(self, topic: str, payload: dict, qos: int = 1) -> bool:
        """Publish a JSON object to an arbitrary topic."""
        if self._client is None or not self._connected:
            print(f"{_TAG} cannot publish — not connected")
            return False
        raw = json.dumps(payload)
        info = self._client.publish(topic, raw, qos=qos)
        print(f"{_TAG} published → {topic} ({len(raw)} bytes)")
        return info.rc == paho_mqtt.MQTT_ERR_SUCCESS

    def publish_gps_base(self, lat: float, lon: float, alt: float = 0.0) -> bool:
        """Publish campus GPS origin to ``MQTT_TOPIC_GPS_BASE`` (UGV/position/gps/base)."""
        return self.publish_json(MQTT_TOPIC_GPS_BASE, {"lat": lat, "lon": lon, "alt": alt}, qos=1)


mqtt_service = _MqttService()
