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
from app.services.auth_service import require_admin
from app.services.booking_service import get_admin_queue_bookings
from app.services.pathfinding_service import (
    get_route_coords,
    convert_gps_list_to_payload,
    set_campus_gps_origin,
    get_campus_gps_origin,
)
from app.services.mqtt_client import mqtt_service

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


@router.post("/test-scenario/dispatch-custom")
async def admin_dispatch_custom_scenario(request: Request):
    """Nhận danh sách tọa độ GPS tùy chỉnh, convert → local XY, publish MQTT."""
    require_admin(request)
    body = await request.json()
    waypoints = body.get("waypoints", [])

    if not waypoints or len(waypoints) < 2:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Cần ít nhất 2 waypoints"},
        )

    points = []
    for i, wp in enumerate(waypoints):
        lat = wp.get("lat")
        lon = wp.get("lon")
        if lat is None or lon is None:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"Waypoint {i+1} thiếu lat hoặc lon"},
            )
        points.append((float(lat), float(lon)))

    payload = convert_gps_list_to_payload(points)
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
