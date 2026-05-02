"""
API JSON cho Admin Dashboard (yêu cầu role admin).
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import (
    MQTT_WS_URL,
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT_TCP,
    MQTT_USERNAME,
    MQTT_USE_SERVER_BRIDGE,
    MQTT_BRIDGE_WEB_PATH,
    MQTT_TOPIC_STATUS,
    MQTT_TOPIC_TELEMETRY,
    MQTT_TOPIC_POSITION,
    MQTT_TOPIC_MOTORS,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_CONTROL,
    MQTT_CLIENT_PREFIX,
    ROBOT_STATUS_UGV_TOPICS,
    MQTT_TOPIC_GPS_BASE,
)
from app.services.admin_settings_store import (
    get_can_last_params,
    get_los_last_params,
    set_can_last_params,
    set_los_last_params,
)
from app.services.auth_service import require_admin
from app.services.booking_service import get_admin_queue_bookings
from app.services.pathfinding_service import (
    get_route_coords,
    convert_gps_list_to_payload,
    gps_to_local,
    set_campus_gps_origin,
    get_campus_gps_origin,
)
from app.services.mqtt_client import mqtt_service
from app.services.pickup_locations_store import list_pickup_locations_admin, set_pickup_xy_overrides

router = APIRouter(prefix="/api/admin", tags=["Admin API"])


@router.get("/config")
async def admin_mqtt_config(request: Request):
    """Cấu hình MQTT công khai cho mqtt.js (không chứa mật khẩu broker)."""
    require_admin(request)
    return JSONResponse(
        content={
            "success": True,
            "mqtt": {
                "ws_url": MQTT_WS_URL,
                "broker_host": MQTT_BROKER_HOST,
                "broker_port_tcp": MQTT_BROKER_PORT_TCP,
                "username": MQTT_USERNAME,
                "use_server_bridge": MQTT_USE_SERVER_BRIDGE,
                "bridge_ws_path": MQTT_BRIDGE_WEB_PATH,
                "client_id_prefix": MQTT_CLIENT_PREFIX,
                "topics": {
                    "status": MQTT_TOPIC_STATUS,
                    "telemetry": MQTT_TOPIC_TELEMETRY,
                    "position": MQTT_TOPIC_POSITION,
                    "motors": MQTT_TOPIC_MOTORS,
                    "command": MQTT_TOPIC_COMMAND,
                    "control": MQTT_TOPIC_CONTROL,
                    "gps_base": MQTT_TOPIC_GPS_BASE,
                    "robot_status_ugv": ROBOT_STATUS_UGV_TOPICS,
                },
            },
        }
    )


@router.get("/bookings/active")
async def admin_active_bookings(request: Request):
    """Danh sách đơn Pending / Shipping cho bảng Admin."""
    require_admin(request)
    rows = get_admin_queue_bookings()
    return JSONResponse(content={"success": True, "bookings": rows})


@router.delete("/bookings/{booking_id}")
async def admin_delete_booking(request: Request, booking_id: str):
    """Xóa vĩnh viễn một đơn hàng (dùng khi test)."""
    require_admin(request)
    from app.services.db_service import db
    doc = db.collection("bookings").document(booking_id).get()
    if not doc:
        return JSONResponse(status_code=404, content={"success": False, "message": "Không tìm thấy đơn"})
    ok = db.collection("bookings").document(booking_id).delete()
    if not ok:
        return JSONResponse(status_code=500, content={"success": False, "message": "Lỗi khi xóa"})
    return JSONResponse(content={"success": True, "message": "Đã xóa đơn " + booking_id})


@router.get("/bookings/routes")
async def admin_booking_routes(request: Request):
    """Trả danh sách route Dijkstra cho các đơn active (pickup → Thư viện)."""
    require_admin(request)
    bookings = get_admin_queue_bookings()
    routes = []
    for b in bookings:
        loc_id = b.get("pickup_location_id", "")
        coords = get_route_coords(loc_id)
        if coords is None:
            continue
        routes.append({
            "booking_id": b.get("_id", b.get("id", "")),
            "pickup_name": b.get("pickup_location_name", loc_id),
            "status": b.get("status", ""),
            "coords": coords,
        })
    return JSONResponse(content={"success": True, "routes": routes})


@router.get("/settings/los-last")
async def admin_get_los_last(request: Request):
    """Tham số LOS đã gửi MQTT lần gần nhất (Firestore hoặc DB local)."""
    require_admin(request)
    return JSONResponse(content={"success": True, "params": get_los_last_params()})


@router.post("/settings/los-last")
async def admin_save_los_last(request: Request):
    """Lưu snapshot payload LOS vừa publish (chỉ key trong allowlist)."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = body.get("params")
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Thiếu hoặc sai kiểu trường params (object)."},
        )
    if not set_los_last_params(raw):
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Lưu thất bại."},
        )
    return JSONResponse(content={"success": True, "params": get_los_last_params()})


@router.get("/settings/can-last")
async def admin_get_can_last(request: Request):
    """Tham số CAN đã gửi MQTT lần gần nhất (Firestore hoặc DB local)."""
    require_admin(request)
    return JSONResponse(content={"success": True, "params": get_can_last_params()})


@router.post("/settings/can-last")
async def admin_save_can_last(request: Request):
    """Lưu snapshot payload CAN vừa publish (chỉ key trong allowlist)."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = body.get("params")
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Thiếu hoặc sai kiểu trường params (object)."},
        )
    if not set_can_last_params(raw):
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Lưu thất bại."},
        )
    return JSONResponse(content={"success": True, "params": get_can_last_params()})


@router.get("/pickup-locations")
async def admin_get_pickup_locations(request: Request):
    """Địa điểm nhận sách + tọa độ local (x, y) mặc định từ GPS và ghi đè (nếu có)."""
    require_admin(request)
    return JSONResponse(content={"success": True, "locations": list_pickup_locations_admin()})


@router.put("/pickup-locations/overrides")
async def admin_put_pickup_xy_overrides(request: Request):
    """Thay thế toàn bộ map ghi đè x,y (met, Bắc/Đông) cho từng địa điểm nhận sách."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = body.get("overrides")
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Thiếu hoặc sai kiểu trường overrides (object)."},
        )
    if not set_pickup_xy_overrides(raw):
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": "Lưu thất bại."},
        )
    return JSONResponse(content={"success": True, "locations": list_pickup_locations_admin()})


# ---------------------------------------------------------------------------
# Campus GPS origin (lat/lon → local x,y reference)
# ---------------------------------------------------------------------------


@router.get("/campus-gps-origin")
async def admin_get_campus_gps_origin(request: Request):
    """Trả về gốc GPS hiện tại mà server dùng cho chuyển đổi lat/lon → x,y."""
    require_admin(request)
    lat, lon, alt = get_campus_gps_origin()
    return JSONResponse(
        content={"success": True, "origin": {"lat": lat, "lon": lon, "alt": alt}}
    )


@router.post("/campus-gps-origin")
async def admin_set_campus_gps_origin(request: Request):
    """Đặt gốc GPS mới (body hoặc từ bản tin MQTT server đã nhận trên UGV/position/gps), publish UGV/position/gps/base."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    lat = body.get("lat")
    lon = body.get("lon", body.get("lng"))
    alt = body.get("alt")

    if lat is None or lon is None:
        if mqtt_service.robot_lat is not None and mqtt_service.robot_lon is not None:
            lat = mqtt_service.robot_lat
            lon = mqtt_service.robot_lon
            if alt is None and mqtt_service.robot_alt is not None:
                alt = mqtt_service.robot_alt

    if lat is None or lon is None:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    "Thiếu lat/lon — chờ bản tin trên UGV/position/gps (server) "
                    "hoặc mở phần kịch bản để nhận GPS, rồi thử lại / gửi lat, lon trong JSON."
                ),
            },
        )

    if alt is None:
        alt = 0.0
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        alt_f = float(alt)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "lat, lon hoặc alt không hợp lệ"},
        )

    set_campus_gps_origin(lat_f, lon_f, alt_f)
    mqtt_ok = mqtt_service.publish_gps_base(lat_f, lon_f, alt_f)
    if not mqtt_ok:
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "message": "Đã cập nhật gốc trên server nhưng không publish được MQTT (broker chưa kết nối?).",
                "origin": {"lat": lat_f, "lon": lon_f, "alt": alt_f},
            },
        )

    return JSONResponse(
        content={
            "success": True,
            "message": "Đã đặt điểm gốc GPS→xy và gửi lên topic gps/base.",
            "origin": {"lat": lat_f, "lon": lon_f, "alt": alt_f},
        }
    )


@router.post("/gps-to-local")
async def admin_gps_to_local(request: Request):
    """Lat/lon (WGS84) + alt → x,y local (m, North/East) theo gốc server hiện tại."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    lat = body.get("lat")
    lon = body.get("lon", body.get("lng"))
    if lat is None or lon is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Cần lat và lon (hoặc lng)"},
        )
    alt = body.get("alt", 0.0)
    try:
        la = float(lat)
        lo = float(lon)
        al = float(alt) if alt is not None else 0.0
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "lat, lon, alt phải là số hợp lệ"},
        )
    x, y = gps_to_local(la, lo, al)
    return JSONResponse(
        content={"success": True, "x": x, "y": y},
    )


# ---------------------------------------------------------------------------
# Test scenarios — kịch bản kiểm tra thực địa
# ---------------------------------------------------------------------------

TEST_SCENARIOS: dict[str, dict] = {
    "football_field": {
        "name": "Chạy quanh sân bóng đá",
        "description": "Robot chạy quanh 4 góc sân bóng đá trong khuôn viên trường",
        "waypoints": [
            (10.77202433, 106.65860867),  # B1 (xuất phát)
            (10.77225250, 106.65900900),  # Waypoint trung gian
            (10.7721896, 106.6590731),    # Góc 3
            (10.7726199, 106.6598401),    # Góc 2
            (10.7729915, 106.65962716),   # Góc 1
            (10.7725605, 106.6588595),    # Góc 4
            (10.77225250, 106.65900900),  # Quay về waypoint trung gian
        ],
    },
}

# Cùng thứ tự với TEST_SCENARIOS["football_field"]["waypoints"]
FOOTBALL_WAYPOINT_NAMES: list[str] = [
    "B1 (xuất phát)",
    "Waypoint trung gian",
    "Góc 3",
    "Góc 2",
    "Góc 1",
    "Góc 4",
    "→ Quay về WP trung gian",
]


def get_football_scenario_display_rows() -> list[dict]:
    """Các điểm sân bóng với (x, y) local tại thời điểm render (theo gốc server hiện tại)."""
    sc = TEST_SCENARIOS.get("football_field") or {}
    wps: list = sc.get("waypoints") or []
    rows: list[dict] = []
    for i, w in enumerate(wps):
        if len(w) < 2:
            continue
        lat, lon = float(w[0]), float(w[1])
        x, y = gps_to_local(lat, lon)
        name = (
            FOOTBALL_WAYPOINT_NAMES[i]
            if i < len(FOOTBALL_WAYPOINT_NAMES)
            else f"Điểm {i + 1}"
        )
        rows.append(
            {
                "idx": i + 1,
                "name": name,
                "x": x,
                "y": y,
            }
        )
    return rows


@router.post("/test-scenario/dispatch")
async def admin_dispatch_test_scenario(request: Request):
    """Chạy một kịch bản test: convert GPS → local XY, publish MQTT."""
    require_admin(request)
    body = await request.json()
    scenario_id = body.get("scenario_id", "")

    scenario = TEST_SCENARIOS.get(scenario_id)
    if scenario is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": f"Không tìm thấy kịch bản '{scenario_id}'"},
        )

    payload = convert_gps_list_to_payload(scenario["waypoints"])
    ok = mqtt_service.publish_path(payload)
    if not ok:
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": "Không gửi được MQTT (broker chưa kết nối?)"},
        )

    return JSONResponse(content={
        "success": True,
        "message": f"Đã gửi {len(payload['stage_x'])} waypoints cho kịch bản \"{scenario['name']}\"",
        "payload": payload,
    })


def _as_float(v):
    if v is None:
        return None
    if isinstance(v, str) and not str(v).strip():
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _payload_from_local_xy_waypoints(waypoints: list) -> dict:
    """Build MQTT path from local ENU (x=North, y=East) metres — trùng firmware/convert_gps_list_to_payload output."""
    sx: list[float] = []
    sy: list[float] = []
    for i, wp in enumerate(waypoints):
        x = _as_float(wp.get("x"))
        y = _as_float(wp.get("y"))
        if x is None or y is None:
            raise ValueError(f"Waypoint {i+1} cần x, y số hợp lệ (local, mét)")
        sx.append(x)
        sy.append(y)
    return {"stage_x": sx, "stage_y": sy}


def _all_waypoints_valid_local_xy(waypoints: list) -> bool:
    for w in waypoints:
        if _as_float(w.get("x")) is None or _as_float(w.get("y")) is None:
            return False
    return bool(waypoints)


def _all_waypoints_valid_gps(waypoints: list) -> bool:
    for w in waypoints:
        if _as_float(w.get("lat")) is None or _as_float(w.get("lon")) is None:
            return False
    return bool(waypoints)


@router.post("/test-scenario/dispatch-custom")
async def admin_dispatch_custom_scenario(request: Request):
    """Nhận waypoints: (1) tọa độ local x,y (m) hoặc (2) lat,lon — publish MQTT path."""
    require_admin(request)
    body = await request.json()
    waypoints = body.get("waypoints", [])

    if not waypoints or len(waypoints) < 2:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Cần ít nhất 2 waypoints"},
        )

    if _all_waypoints_valid_local_xy(waypoints):
        try:
            payload = _payload_from_local_xy_waypoints(waypoints)
        except (ValueError, TypeError) as e:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": str(e)},
            )
    elif _all_waypoints_valid_gps(waypoints):
        points: list[tuple[float, float]] = []
        for wp in waypoints:
            la = _as_float(wp.get("lat"))
            lo = _as_float(wp.get("lon"))
            if la is None or lo is None:  # pragma: no cover
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "message": "Waypoint thiếu lat hoặc lon hợp lệ"},
                )
            points.append((la, lo))
        payload = convert_gps_list_to_payload(points)
    else:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    "Các điểm phải cùng dạng: tất cả (x, y) local hợp lệ (m) **hoặc** tất cả (lat, lon) GPS. "
                    "Nếu trộn dạng hoặc thiếu số, hãy kiểm tra từng dòng."
                ),
            },
        )
    ok = mqtt_service.publish_path(payload)
    if not ok:
        return JSONResponse(
            status_code=502,
            content={"success": False, "message": "Không gửi được MQTT (broker chưa kết nối?)"},
        )

    return JSONResponse(content={
        "success": True,
        "message": f"Đã gửi {len(payload['stage_x'])} waypoints tùy chỉnh",
        "payload": payload,
    })
