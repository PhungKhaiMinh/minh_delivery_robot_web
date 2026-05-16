"""
Router cho các trang giao diện (HTML pages).
Render template Jinja2 và truyền dữ liệu cần thiết.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
from typing import Optional

from app.services.pickup_locations_store import get_catalog_locations, list_pickup_locations_admin
from app.services.auth_service import get_current_user
from app.services.booking_service import get_user_bookings, get_active_bookings, get_booking_by_id, get_booking_awaiting_robot_handoff
from app.services.db_service import db
from app.utils.vn_time import format_iso_utc_to_vn_display

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["vn_local_dt"] = format_iso_utc_to_vn_display

router = APIRouter(tags=["Pages"])


def _redirect_if_admin(user):
    """Giao diện client chỉ dành cho role client."""
    if user and getattr(user, "role", "client") == "admin":
        return RedirectResponse(url="/admin", status_code=302)
    return None


def _get_robot_info() -> dict:
    """Lấy thông tin robot mặc định."""
    robot = db.collection("robots").document("robot_01").get()
    if robot:
        status_map = {"idle": "Rảnh", "busy": "Đang giao", "charging": "Đang sạc"}
        return {
            "battery": robot.get("battery", 0),
            "status": status_map.get(robot.get("status", "idle"), robot.get("status")),
            "location": "Thư viện Trung tâm",
        }
    return {"battery": 87, "status": "Rảnh", "location": "Thư viện Trung tâm"}


@router.get("/", response_class=HTMLResponse)
async def page_home(request: Request):
    """Trang chủ — redirect đến dashboard hoặc admin nếu đã đăng nhập."""
    user = get_current_user(request)
    if user:
        if user.role == "admin":
            return RedirectResponse(url="/admin", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    user = get_current_user(request)
    if user:
        if user.role == "admin":
            return RedirectResponse(url="/admin", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "auth/login.html")


@router.get("/register", response_class=HTMLResponse)
async def page_register(request: Request):
    user = get_current_user(request)
    if user:
        if user.role == "admin":
            return RedirectResponse(url="/admin", status_code=302)
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "auth/register.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    redir = _redirect_if_admin(user)
    if redir:
        return redir

    bookings = get_user_bookings(user.id)

    # Tính thống kê đơn hàng
    stats = {"pending": 0, "in_progress": 0, "completed": 0, "total": len(bookings)}
    for b in bookings:
        s = b.get("status", "")
        if s in ("pending", "confirmed"):
            stats["pending"] += 1
        elif s == "in_progress":
            stats["in_progress"] += 1
        elif s == "completed":
            stats["completed"] += 1

    current_date = datetime.now().strftime("%A, %d/%m/%Y")
    handoff_booking = get_booking_awaiting_robot_handoff(user.id)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "active_page": "dashboard",
            "stats": stats,
            "recent_bookings": bookings[:5],
            "current_date": current_date,
            "handoff_booking": handoff_booking,
        },
    )


@router.get("/booking", response_class=HTMLResponse)
async def page_booking(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    redir = _redirect_if_admin(user)
    if redir:
        return redir

    today = datetime.now().strftime("%Y-%m-%d")

    return templates.TemplateResponse(
        request,
        "booking.html",
        {
            "user": user,
            "active_page": "booking",
            "locations": get_catalog_locations(),
            "today": today,
            "current_date": datetime.now().strftime("%A, %d/%m/%Y"),
        },
    )


@router.get("/order/{booking_id}", response_class=HTMLResponse)
async def page_order_detail(booking_id: str, request: Request):
    """Chi tiết đơn hàng — tích hợp bản đồ theo dõi robot nếu đơn đang active."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    redir = _redirect_if_admin(user)
    if redir:
        return redir

    booking = get_booking_by_id(booking_id)
    if not booking or booking.get("user_id") != user.id:
        return RedirectResponse(url="/history", status_code=302)

    active_statuses = {"pending", "confirmed", "in_progress"}
    is_active = booking.get("status") in active_statuses
    robot = _get_robot_info()

    pickup_map_x: Optional[float] = None
    pickup_map_y: Optional[float] = None
    pid = booking.get("pickup_location_id")
    if pid:
        for loc in list_pickup_locations_admin():
            if str(loc.get("id")) == str(pid):
                try:
                    pickup_map_x = float(loc["x"])
                    pickup_map_y = float(loc["y"])
                except (KeyError, TypeError, ValueError):
                    pickup_map_x = pickup_map_y = None
                break

    return templates.TemplateResponse(
        request,
        "order_detail.html",
        {
            "user": user,
            "active_page": "history",
            "booking": booking,
            "is_active": is_active,
            "robot_status": robot["status"],
            "robot_battery": robot["battery"],
            "locations": get_catalog_locations(),
            "current_date": datetime.now().strftime("%A, %d/%m/%Y"),
            "pickup_map_x": pickup_map_x,
            "pickup_map_y": pickup_map_y,
        },
    )


@router.get("/history", response_class=HTMLResponse)
async def page_history(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    redir = _redirect_if_admin(user)
    if redir:
        return redir

    bookings = get_user_bookings(user.id)

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "user": user,
            "active_page": "history",
            "bookings": bookings,
            "current_date": datetime.now().strftime("%A, %d/%m/%Y"),
        },
    )


@router.get("/profile", response_class=HTMLResponse)
async def page_profile(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    redir = _redirect_if_admin(user)
    if redir:
        return redir

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "active_page": "profile",
            "current_date": datetime.now().strftime("%A, %d/%m/%Y"),
        },
    )
