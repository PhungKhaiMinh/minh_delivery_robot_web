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
)

_TAG = "[MQTT-CLIENT]"


class _MqttService:
    """Lightweight wrapper around a single paho.mqtt.Client."""

    def __init__(self) -> None:
        self._client: Optional[paho_mqtt.Client] = None
        self._connected = False
        self._lock = threading.Lock()

        self.robot_lat: Optional[float] = None
        self.robot_lon: Optional[float] = None
        self.robot_alt: Optional[float] = None

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

    # ------------------------------------------------------------------
    # paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client: paho_mqtt.Client, _ud: object, _flags: dict, rc: int) -> None:
        if rc == 0:
            self._connected = True
            client.subscribe(MQTT_TOPIC_POSITION, qos=0)
            print(f"{_TAG} connected, subscribed {MQTT_TOPIC_POSITION}")
        else:
            print(f"{_TAG} connect rc={rc}")

    def _on_message(self, _client: paho_mqtt.Client, _ud: object, msg: paho_mqtt.MQTTMessage) -> None:
        if msg.topic == MQTT_TOPIC_POSITION:
            try:
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
        """Publish ``{"stage_x":[…], "stage_y":[…]}`` to ``MQTT_TOPIC_PATH``."""
        if self._client is None or not self._connected:
            print(f"{_TAG} cannot publish — not connected")
            return False
        raw = json.dumps(payload)
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
