"""
API JSON cho Admin Dashboard (yêu cầu role admin).
"""

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
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
    gps_to_local,
    set_campus_gps_origin,
    get_campus_gps_origin,
)
from app.services.mqtt_client import mqtt_service
from app.services.pickup_locations_store import (
    apply_pickup_catalog_and_overrides,
    list_pickup_locations_admin,
    set_pickup_xy_overrides,
)
from app.services.robot_waypoints_dataset_store import get_waypoints_dataset, set_waypoints_dataset
from app.services.admin_route_planner import plan_field_route
from app.services.rtab_map_graph_service import (
    build_rtab_graph_json,
    get_rtab_map_status,
    save_rtab_map_from_upload,
)

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


@router.get("/rtab-map/graph")
async def admin_rtab_map_graph(
    request: Request,
    env: int = Query(1, description="1 = gồm điểm scan/obstacle (môi trường), 0 = chỉ graph"),
):
    """Graph RTAB-Map (Node pose + Link neighbor; tùy chọn điểm laser/obstacle từ Data) cho Admin Tracking."""
    require_admin(request)
    return JSONResponse(content=build_rtab_graph_json(include_environment=(env != 0)))


@router.get("/rtab-map/status")
async def admin_rtab_map_status(request: Request):
    """Trạng thái file map trên disk (path, kích thước, hợp lệ RTAB)."""
    require_admin(request)
    return JSONResponse(content=get_rtab_map_status())


@router.post("/rtab-map/upload")
async def admin_rtab_map_upload(request: Request, file: UploadFile = File(...)):
    """Tải lên file .db RTAB-Map, ghi đè ``RTAB_MAP_DB_PATH`` (một lần, dùng lại mỗi lần mở Tracking)."""
    require_admin(request)
    name = (file.filename or "").strip().lower()
    if not name.endswith(".db"):
        raise HTTPException(status_code=400, detail="Chỉ chấp nhận file .db")
    try:
        ok, msg = await save_rtab_map_from_upload(file)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return JSONResponse(
            content={
                "success": True,
                "message": msg,
                "status": get_rtab_map_status(),
            }
        )
    finally:
        await file.close()


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
    """Cập nhật catalog địa điểm (nếu có `locations`) và/hoặc map ghi đè x,y (`overrides`)."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    locs = body.get("locations")
    if isinstance(locs, list):
        ovs = body.get("overrides")
        if not isinstance(ovs, dict):
            ovs = {}
        if not apply_pickup_catalog_and_overrides(locs, ovs):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": (
                        "Dữ liệu không hợp lệ: cần ít nhất một địa điểm; id (chữ, số, _,-) tối đa 64 ký tự, "
                        "tên không rỗng, lat/lon hợp lệ, không trùng id."
                    ),
                },
            )
        return JSONResponse(content={"success": True, "locations": list_pickup_locations_admin()})
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


@router.post("/test-route/plan")
async def admin_test_route_plan(request: Request):
    """Hoạch định lộ trình test: điểm đầu/cuối = pickup; đồ thị = pickup + waypoint center; Dijkstra."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    start_id = body.get("start_pickup_id", "")
    end_id = body.get("end_pickup_id", "")
    result = plan_field_route(str(start_id), str(end_id))
    if result is None:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    "Không hoạch định được: kiểm tra id đầu/cuối khác nhau, catalog pickup, "
                    "và dataset waypoint (center + right_side) không rỗng."
                ),
            },
        )
    return JSONResponse(content={"success": True, **result})


@router.post("/test-route/publish")
async def admin_test_route_publish(request: Request):
    """Gửi payload lộ trình đã kiểm tra lên MQTT ``UGV/path_topic`` (4 mảng song song nếu có margin)."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    pl = body.get("payload")
    if not isinstance(pl, dict):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Thiếu hoặc sai kiểu trường payload (object)."},
        )
    if not mqtt_service.publish_path(pl):
        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "message": "MQTT chưa kết nối hoặc payload không hợp lệ (độ dài stage_x/stage_y/margin).",
            },
        )
    return JSONResponse(content={"success": True, "message": "Đã publish lên path topic."})


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
                    "hoặc nhập lat, lon trong JSON."
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
# Dataset waypoint robot (Firestore / DB local)
# ---------------------------------------------------------------------------


@router.get("/waypoints-dataset")
async def admin_get_waypoints_dataset(request: Request):
    """Danh sách waypoint (id, name, kind, x, y) lưu trên server."""
    require_admin(request)
    return JSONResponse(content={"success": True, "waypoints": get_waypoints_dataset()})


@router.put("/waypoints-dataset")
async def admin_put_waypoints_dataset(request: Request):
    """Thay thế toàn bộ dataset waypoint (tối đa 500 điểm)."""
    require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    raw = body.get("waypoints")
    if not isinstance(raw, list):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "Thiếu hoặc sai kiểu trường waypoints (mảng)."},
        )
    if not set_waypoints_dataset(raw):
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    "Dữ liệu không hợp lệ: mỗi waypoint cần id (chữ, số, _,-), tên, "
                    "center {x,y} và right_side {x,y} (số hợp lệ); không trùng id."
                ),
            },
        )
    return JSONResponse(content={"success": True, "waypoints": get_waypoints_dataset()})
