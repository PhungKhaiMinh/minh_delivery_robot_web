from __future__ import annotations

"""
Module giao tiếp với Server điều khiển Robot.
Gửi thông tin đơn hàng qua POST request tới Robot Control Server.
Nhận vị trí robot real-time để hiển thị trên bản đồ.
"""

import httpx
from typing import Optional
from datetime import datetime, timezone

from app.config import ROBOT_SERVER_URL
from app.services.db_service import db
from app.models.robot import RobotStatus
from app.services.mqtt_client import mqtt_service


async def send_booking_to_robot(booking_id: str, booking_data: dict) -> tuple[bool, str]:
    """
    Gửi thông tin đơn hàng đến Robot Control Server.
    Endpoint: POST {ROBOT_SERVER_URL}/api/tasks
    Trả về (success, message).
    """
    try:
        payload = {
            "task_id": booking_id,
            "pickup_location": {
                "id": booking_data.get("pickup_location_id"),
                "name": booking_data.get("pickup_location_name"),
                "lat": booking_data.get("pickup_lat"),
                "lng": booking_data.get("pickup_lng"),
            },
            "book_count": booking_data.get("book_count", 0),
            "pickup_date": booking_data.get("pickup_date"),
            "pickup_time": booking_data.get("pickup_time"),
            "requester_id": booking_data.get("user_id"),
            "note": booking_data.get("note", ""),
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{ROBOT_SERVER_URL}/api/tasks",
                json=payload,
            )

        if response.status_code == 200:
            result = response.json()
            return True, result.get("message", "Đã gửi đơn đến robot")
        else:
            return False, f"Robot server phản hồi lỗi: {response.status_code}"

    except httpx.ConnectError:
        # Robot server chưa chạy — vẫn ghi nhận đơn, đánh dấu chờ gửi lại
        print(f"[ROBOT] Không thể kết nối Robot Server tại {ROBOT_SERVER_URL}")
        return False, "Robot server đang offline. Đơn hàng sẽ được gửi khi server hoạt động."
    except httpx.TimeoutException:
        return False, "Robot server không phản hồi (timeout)"
    except Exception as e:
        print(f"[ROBOT ERROR] Lỗi gửi đơn đến robot: {e}")
        return False, "Lỗi hệ thống khi giao tiếp với robot"


def _pose_fields() -> dict:
    out: dict = {}
    if mqtt_service.robot_pose_x is not None:
        out["x"] = mqtt_service.robot_pose_x
    if mqtt_service.robot_pose_y is not None:
        out["y"] = mqtt_service.robot_pose_y
    if mqtt_service.robot_pose_yaw is not None:
        out["yaw"] = mqtt_service.robot_pose_yaw
    if mqtt_service.robot_lat is not None:
        out["lat"] = mqtt_service.robot_lat
    if mqtt_service.robot_lon is not None:
        out["lng"] = mqtt_service.robot_lon
    return out


async def get_robot_position(robot_id: str) -> Optional[dict]:
    """
    Ưu tiên tọa độ từ MQTT UGV/localization/pose (x, y, yaw) và lat/lng suy từ gốc
    (local_to_gps) cho bản đồ. Bổ sung pin/tốc độ từ server hoặc DB khi có.
    """
    pose = _pose_fields()
    if pose.get("x") is not None and pose.get("y") is not None:
        out = {
            "robot_id": robot_id,
            **pose,
            "battery": None,
            "speed": 0.0,
        }
        try:
            rdoc = db.collection("robots").document(robot_id).get()
            if rdoc and rdoc.get("battery") is not None:
                out["battery"] = rdoc.get("battery")
        except Exception:
            pass
        return out

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{ROBOT_SERVER_URL}/api/robots/{robot_id}/position"
            )
        if response.status_code == 200:
            d = response.json()
            if isinstance(d, dict):
                d.update(_pose_fields())
            return d
    except Exception:
        pass

    robot_data = db.collection("robots").document(robot_id).get()
    if robot_data:
        return {
            "robot_id": robot_id,
            "lat": robot_data.get("current_lat", 10.7724),
            "lng": robot_data.get("current_lng", 106.6580),
            "battery": robot_data.get("battery", 0),
            "speed": 0.0,
            **_pose_fields(),
        }
    if pose:
        return {"robot_id": robot_id, **pose, "battery": None, "speed": 0.0}
    return None


async def get_robot_eta(robot_id: str, booking_id: str) -> Optional[int]:
    """
    Lấy thời gian dự kiến đến (ETA) từ Robot Server.
    Trả về số phút ước tính.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{ROBOT_SERVER_URL}/api/robots/{robot_id}/eta",
                params={"task_id": booking_id},
            )
        if response.status_code == 200:
            return response.json().get("eta_minutes")
    except Exception:
        pass
    return None


def init_default_robot():
    """
    Khởi tạo robot mặc định trong hệ thống (dùng cho demo/dev).
    Chỉ tạo nếu chưa có robot nào.
    """
    robots_col = db.collection("robots")
    if robots_col.count() == 0:
        robots_col.add(
            {
                "name": "BK-Bot 01",
                "status": RobotStatus.IDLE,
                "battery": 87,
                "current_lat": 10.7724,
                "current_lng": 106.6580,
                "current_task_id": None,
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
            doc_id="robot_01",
        )
        print("[ROBOT] Đã khởi tạo robot mặc định: BK-Bot 01")
