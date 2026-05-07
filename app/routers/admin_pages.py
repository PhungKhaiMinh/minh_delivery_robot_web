"""
Trang HTML Admin — layout riêng, RBAC.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import (
    IS_VERCEL,
    MQTT_WS_URL,
    MQTT_USERNAME,
    MQTT_PASSWORD,
    MQTT_USE_SERVER_BRIDGE,
    MQTT_BRIDGE_WEB_PATH,
    MQTT_TOPIC_STATUS,
    MQTT_TOPIC_TELEMETRY,
    MQTT_TOPIC_POSITION,
    MQTT_UGV_TOPIC_POSE,
    MQTT_UGV_TOPIC_AVOIDANCE_WAYPOINT,
    MQTT_TOPIC_MOTORS,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_CONTROL,
    MQTT_CLIENT_PREFIX,
    ROBOT_STATUS_UGV_TOPICS,
    CAMPUS_ORIGIN_LAT,
    CAMPUS_ORIGIN_LON,
    CAMPUS_ORIGIN_ALT,
    OCC_GRID_MAP_PATH,
)
from app.services.admin_settings_store import get_can_last_params, get_los_last_params
from app.services.auth_service import get_current_user
from app.services.booking_service import get_admin_queue_bookings
from app.services.pickup_locations_store import list_pickup_locations_admin
from app.services.robot_waypoints_dataset_store import get_waypoints_dataset

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(prefix="/admin", tags=["Admin Pages"])


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def _redirect_client_home() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=302)


def _require_admin_page(request: Request):
    user = get_current_user(request)
    if not user:
        return None, _redirect_login()
    if user.role != "admin":
        return None, _redirect_client_home()
    return user, None


def _mqtt_ctx() -> dict:
    return {
        "mqtt_ws_url": MQTT_WS_URL,
        "mqtt_client_prefix": MQTT_CLIENT_PREFIX,
        "mqtt_username": MQTT_USERNAME,
        "mqtt_password": MQTT_PASSWORD,
        "mqtt_use_server_bridge": MQTT_USE_SERVER_BRIDGE,
        "mqtt_bridge_path": MQTT_BRIDGE_WEB_PATH,
        "is_vercel_deploy": IS_VERCEL,
        "mqtt_topic_status": MQTT_TOPIC_STATUS,
        "mqtt_topic_telemetry": MQTT_TOPIC_TELEMETRY,
        "mqtt_topic_position": MQTT_TOPIC_POSITION,
        "mqtt_topic_pose": MQTT_UGV_TOPIC_POSE,
        "mqtt_topic_motors": MQTT_TOPIC_MOTORS,
        "mqtt_topic_command": MQTT_TOPIC_COMMAND,
        "mqtt_topic_control": MQTT_TOPIC_CONTROL,
    }


@router.get("", response_class=HTMLResponse)
async def admin_root(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    return RedirectResponse(url="/admin/orders", status_code=302)


@router.get("/orders", response_class=HTMLResponse)
async def admin_orders(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    bookings = get_admin_queue_bookings()
    ctx = {
        "user": user,
        "admin_page": "orders",
        "bookings": bookings,
        "bookings_json": json.dumps(bookings, ensure_ascii=False, default=str),
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "pickup_locations_for_admin": list_pickup_locations_admin(),
        "waypoints_dataset_for_admin": get_waypoints_dataset(),
        "robot_status_ugv_topics": ROBOT_STATUS_UGV_TOPICS,
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/orders.html", ctx)


@router.get("/robot", response_class=HTMLResponse)
async def admin_robot(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    ctx = {
        "user": user,
        "admin_page": "robot",
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "robot_status_ugv_topics": ROBOT_STATUS_UGV_TOPICS,
        "campus_origin_lat": CAMPUS_ORIGIN_LAT,
        "campus_origin_lon": CAMPUS_ORIGIN_LON,
        "campus_origin_alt": CAMPUS_ORIGIN_ALT,
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/robot_status.html", ctx)


@router.get("/tracking", response_class=HTMLResponse)
async def admin_tracking(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    ctx = {
        "user": user,
        "admin_page": "tracking",
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "campus_origin_lat": CAMPUS_ORIGIN_LAT,
        "campus_origin_lon": CAMPUS_ORIGIN_LON,
        "mqtt_topic_pose": MQTT_UGV_TOPIC_POSE,
        "mqtt_topic_avoidance_waypoint": MQTT_UGV_TOPIC_AVOIDANCE_WAYPOINT,
        "occ_grid_map_path": OCC_GRID_MAP_PATH,
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/tracking.html", ctx)


@router.get("/plotting", response_class=HTMLResponse)
async def admin_plotting(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    ctx = {
        "user": user,
        "admin_page": "plotting",
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "robot_status_ugv_topics": ROBOT_STATUS_UGV_TOPICS,
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/plotting.html", ctx)


@router.get("/settings", response_class=HTMLResponse)
async def admin_settings(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    ctx = {
        "user": user,
        "admin_page": "settings",
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "los_saved": get_los_last_params(),
        "can_saved": get_can_last_params(),
        "occ_grid_map_path": OCC_GRID_MAP_PATH,
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/settings.html", ctx)


@router.get("/emergency", response_class=HTMLResponse)
async def admin_emergency(request: Request):
    user, redir = _require_admin_page(request)
    if redir:
        return redir
    ctx = {
        "user": user,
        "admin_page": "emergency",
        "current_date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        **_mqtt_ctx(),
    }
    return templates.TemplateResponse(request, "admin/emergency.html", ctx)
