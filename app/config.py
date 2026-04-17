"""
Cấu hình chung cho toàn bộ hệ thống BKBookBot.
Quản lý các biến môi trường, đường dẫn, và thiết lập bảo mật.
"""

import json
import os
from pathlib import Path
from typing import Optional

# === Đường dẫn gốc của dự án ===
BASE_DIR = Path(__file__).resolve().parent.parent


def _materialize_firebase_json_from_env() -> None:
    """Vercel / serverless: đặt JSON service account vào biến FIREBASE_SERVICE_ACCOUNT_JSON hoặc _B64."""
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        b64 = os.getenv("FIREBASE_SERVICE_ACCOUNT_B64", "").strip()
        if b64:
            import base64

            try:
                raw = base64.b64decode(b64).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return
    if not raw:
        return
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        print("[CONFIG] FIREBASE_SERVICE_ACCOUNT_JSON / _B64 không phải JSON hợp lệ — bỏ qua")
        return
    out = Path("/tmp/firebase-service-account.json")
    try:
        out.write_text(raw, encoding="utf-8")
    except OSError as exc:
        print(f"[CONFIG] Không ghi được service account vào /tmp: {exc}")
        return
    os.environ["FIREBASE_CREDENTIALS_PATH"] = str(out)
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(out))


_materialize_firebase_json_from_env()


def _resolve_firebase_credentials_path() -> str:
    """Tìm file JSON service account: env → file cố định trong thư mục gốc project."""
    for key in ("FIREBASE_CREDENTIALS_PATH", "GOOGLE_APPLICATION_CREDENTIALS"):
        p = os.getenv(key, "").strip()
        if p and Path(p).is_file():
            return str(Path(p).resolve())
    default_file = BASE_DIR / "firebase-service-account.json"
    if default_file.is_file():
        return str(default_file.resolve())
    return ""


def _parse_use_firestore_flag() -> Optional[bool]:
    """
    None = tự động (dùng Firestore nếu tìm thấy file khóa).
    True/False = bắt buộc theo biến USE_FIRESTORE.
    """
    raw = os.getenv("USE_FIRESTORE", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


FIREBASE_CREDENTIALS_PATH = _resolve_firebase_credentials_path()
_use_fs = _parse_use_firestore_flag()
if _use_fs is True:
    USE_FIRESTORE = True
elif _use_fs is False:
    USE_FIRESTORE = False
else:
    USE_FIRESTORE = bool(FIREBASE_CREDENTIALS_PATH)

# Vercel: filesystem deploy thường read-only — dùng /tmp cho DB file nếu không dùng Firestore.
IS_VERCEL = os.getenv("VERCEL", "").strip() == "1"
if IS_VERCEL and not USE_FIRESTORE:
    DATA_DIR = Path("/tmp/bookbot-data/collections")
else:
    DATA_DIR = BASE_DIR / "data" / "collections"

USERS_COLLECTION = DATA_DIR / "users"
BOOKINGS_COLLECTION = DATA_DIR / "bookings"
ROBOTS_COLLECTION = DATA_DIR / "robots"

# === Cấu hình bảo mật JWT ===
SECRET_KEY = os.getenv("SECRET_KEY", "bkbookbot-secret-key-change-in-production-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # Token hết hạn sau 24 giờ

# === Cấu hình server điều khiển Robot ===
ROBOT_SERVER_URL = os.getenv("ROBOT_SERVER_URL", "http://localhost:5001")

# === MQTT (WebSocket cho trình duyệt — mqtt.js) ===
# Robot UGV: broker TCP 1883 (Python); trình duyệt cần listener WebSocket trên cùng host (Mosquitto ví dụ cổng 9001).
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "45.117.177.157")
MQTT_BROKER_PORT_TCP = int(os.getenv("MQTT_BROKER_PORT_TCP", "1883"))
MQTT_WS_PORT = int(os.getenv("MQTT_WS_PORT", "9001"))
# Trình duyệt trên HTTPS (Vercel) chỉ cho phép wss:// — không dùng ws://.
_mqtt_ws_explicit = os.getenv("MQTT_WS_URL", "").strip()
if _mqtt_ws_explicit:
    MQTT_WS_URL = _mqtt_ws_explicit
elif IS_VERCEL:
    _wss_port = int(os.getenv("MQTT_WSS_PORT", "8884"))
    MQTT_WS_URL = f"wss://{MQTT_BROKER_HOST}:{_wss_port}"
else:
    MQTT_WS_URL = f"ws://{MQTT_BROKER_HOST}:{MQTT_WS_PORT}"
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "client")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "viam1234")
# Trình duyệt → WebSocket tới FastAPI → paho TCP 1883 (user/pass ở trên). Tắt nếu broker có MQTT-over-WS và dùng mqtt.js trực tiếp.
# Trên Vercel: mặc định BẬT bridge — trình duyệt chỉ cần wss://cùng host (HTTPS), server nối TCP tới broker (không bắt buộc wss trên broker).
# Đặt MQTT_USE_SERVER_BRIDGE=false + MQTT_WS_URL=wss://... nếu muốn mqtt.js nối thẳng broker.
_mqtt_bridge_raw = os.getenv("MQTT_USE_SERVER_BRIDGE", "true").strip().lower()
MQTT_USE_SERVER_BRIDGE = _mqtt_bridge_raw not in ("0", "false", "no", "off")
MQTT_BRIDGE_WEB_PATH = os.getenv("MQTT_BRIDGE_WEB_PATH", "/api/admin/mqtt-bridge").strip() or "/api/admin/mqtt-bridge"
MQTT_TOPIC_STATUS = os.getenv("MQTT_TOPIC_STATUS", "robot/status")
# Gói đầy đủ trạng thái robot (GPS, IMU, bánh, CAN, khóa, …) — trang Admin Robot Status đọc topic này
MQTT_TOPIC_TELEMETRY = os.getenv("MQTT_TOPIC_TELEMETRY", "robot/telemetry")
MQTT_TOPIC_POSITION = os.getenv("MQTT_TOPIC_POSITION", "UGV/position/gps")
MQTT_TOPIC_MOTORS = os.getenv("MQTT_TOPIC_MOTORS", "robot/motors")
MQTT_TOPIC_COMMAND = os.getenv("MQTT_TOPIC_COMMAND", "robot/command")
# Lệnh điều khiển chế độ manual/auto và tốc độ bánh (trang Admin Settings)
MQTT_TOPIC_CONTROL = os.getenv("MQTT_TOPIC_CONTROL", "robot/control")
MQTT_CLIENT_PREFIX = os.getenv("MQTT_CLIENT_PREFIX", "bookbot-admin")

# Topic UGV — trang Admin Robot Status (subscribe từng topic, JSON theo firmware)
ROBOT_STATUS_UGV_TOPICS = {
    "heading": os.getenv("MQTT_UGV_TOPIC_HEADING", "UGV/position/heading"),
    "heading_gps": os.getenv("MQTT_UGV_TOPIC_HEADING_GPS", "UGV/position/heading_gps"),
    "gps": os.getenv("MQTT_UGV_TOPIC_GPS", "UGV/position/gps"),
    "state_gps": os.getenv("MQTT_UGV_TOPIC_STATE_GPS", "UGV/state/gps"),
    "curr_vel": os.getenv("MQTT_UGV_TOPIC_CURR_VEL", "UGV/control/curr_vel"),
    "vel": os.getenv("MQTT_UGV_TOPIC_VEL", "UGV/control/vel"),
    "para": os.getenv("MQTT_UGV_TOPIC_PARA", "UGV/control/para"),
    "byte_per_sec": os.getenv("MQTT_UGV_TOPIC_BYTE_PER_SEC", "UGV/bytePerSecond"),
    "has_locked": os.getenv("MQTT_UGV_TOPIC_HAS_LOCKED", "UGV/status/has_locked"),
    "has_moving": os.getenv("MQTT_UGV_TOPIC_HAS_MOVING", "UGV/status/has_moving"),
    "arrival": os.getenv("MQTT_UGV_TOPIC_ARRIVAL", "UGV/status/arrial"),
}

# === Cấu hình email sinh viên HCMUT ===
ALLOWED_EMAIL_DOMAIN = "@hcmut.edu.vn"

# === Địa điểm nhận sách (client chọn trong form booking) ===
CAMPUS_LOCATIONS = [
    {"id": "b1",       "name": "B1",       "lat": 10.77202433, "lng": 106.65860867},
    {"id": "circle_k", "name": "Circle K", "lat": 10.77288400, "lng": 106.65852917},
]

# === Đồ thị waypoint campus (Dijkstra) ===
# Mỗi node: index, lat, lon, tên (rỗng = waypoint trung gian).
# edges: danh sách (i, j) — robot có thể đi giữa node i ↔ j; trọng số = khoảng cách Haversine tự tính.
CAMPUS_WAYPOINTS = [
    {"idx": 0, "lat": 10.77202433, "lon": 106.65860867, "name": "B1"},
    {"idx": 1, "lat": 10.77213017, "lon": 106.65878283, "name": "Fablab"},
    {"idx": 2, "lat": 10.77225250, "lon": 106.65900900, "name": ""},
    {"idx": 3, "lat": 10.77217150, "lon": 106.65906250, "name": ""},
    {"idx": 4, "lat": 10.77276967, "lon": 106.66008117, "name": ""},
    {"idx": 5, "lat": 10.77295400, "lon": 106.65999417, "name": "Thư viện"},
    {"idx": 6, "lat": 10.77252733, "lon": 106.65899400, "name": ""},
    {"idx": 7, "lat": 10.77264000, "lon": 106.65881183, "name": ""},
    {"idx": 8, "lat": 10.77288400, "lon": 106.65864933, "name": ""},
    {"idx": 9, "lat": 10.77288400, "lon": 106.65852917, "name": "Circle K"},
]
CAMPUS_EDGES = [
    (0, 1), (1, 2), (2, 3), (2, 6), (3, 4), (4, 5),
    (6, 7), (7, 8), (8, 9), (6, 3),
]
# Đích cố định cho robot giao sách
CAMPUS_LIBRARY_IDX = 5

# === Gốc tọa độ (GPS → local ENU) — phải khớp ref_lat/ref_lon trên robot firmware ===
CAMPUS_ORIGIN_LAT = float(os.getenv("CAMPUS_ORIGIN_LAT", "10.77202433"))
CAMPUS_ORIGIN_LON = float(os.getenv("CAMPUS_ORIGIN_LON", "106.65860867"))
CAMPUS_ORIGIN_ALT = float(os.getenv("CAMPUS_ORIGIN_ALT", "0.0"))

# === MQTT topic gửi waypoints cho robot di chuyển ===
MQTT_TOPIC_PATH = os.getenv("MQTT_TOPIC_PATH", "UGV/path_topic")
# Gốc GPS (lat/lon/alt) — đồng bộ với firmware / scheduler khi admin đổi điểm base
MQTT_TOPIC_GPS_BASE = os.getenv("MQTT_UGV_TOPIC_GPS_BASE", "UGV/position/gps/base")

# === Scheduler: kiểm tra đơn hàng mỗi N giây ===
SCHEDULER_INTERVAL_SEC = int(os.getenv("SCHEDULER_INTERVAL_SEC", "30"))

# === Cấu hình ứng dụng ===
APP_NAME = "BK BookBot"
APP_DESCRIPTION = "Hệ thống quản lý Robot giao sách tự hành - ĐHBK TP.HCM"
APP_VERSION = "1.0.0"
