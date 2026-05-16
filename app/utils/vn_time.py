"""Format stored UTC timestamps for display in Vietnam (UTC+7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_VN = timezone(timedelta(hours=7))


def format_iso_utc_to_vn_display(value: Any) -> str:
    """
    Chuyển chuỗi ISO (thường là UTC từ server) sang giờ hiển thị theo VN.
    Trả về chuỗi rỗng nếu không có dữ liệu; giữ bản gốc rút gọn nếu parse lỗi.
    """
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return s[:19] if len(s) >= 19 else s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    vn = dt.astimezone(_VN)
    return vn.strftime("%Y-%m-%d %H:%M:%S (Giờ VN, UTC+7)")
