"""
Lưu payload LOS / CAN cuối cùng đã publish (MQTT) để hiển thị lại trên Admin Settings.
Dùng cùng `db` với Firestore cloud hoặc JSON local (app.services.db_service).
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Dict, Optional

from app.services.db_service import db

ADMIN_CONFIG_COLLECTION = "admin_config"
LOS_LAST_DOC_ID = "los_para_last"
CAN_LAST_DOC_ID = "can_para_last"

LOS_PARAM_KEYS = frozenset(
    {
        "MAX_Linear",
        "MAX_Angular",
        "MAX_Pulse",
        "Period_Control_guidance",
        "Period_Guidance",
        "LOS_Radius",
        "LOS_CteScale_",
        "LOS_MaxDelta_",
        "LOS_MinDelta_",
        "PID_Kp",
        "PID_Kd",
        "Accel_Linear",
        "Accel_Angular",
    }
)

CAN_PARAM_KEYS = frozenset(
    {
        "LoopSerialCan",
        "LoopSendVel",
        "safe_angle",
        "safe_distance",
        "loop_calib_yaw",
    }
)


def _coerce_param_value(v: Any) -> Optional[Any]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if isfinite(v) else None
    if isinstance(v, str):
        s = v.strip()
        if not s or len(s) > 64:
            return None
        try:
            n = float(s.replace(",", "."))
        except ValueError:
            return s
        if not isfinite(n):
            return None
        return int(n) if n == int(n) else n
    return None


def sanitize_los_params_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k not in LOS_PARAM_KEYS:
            continue
        cv = _coerce_param_value(v)
        if cv is not None:
            out[k] = cv
    return out


def sanitize_can_params_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if k not in CAN_PARAM_KEYS:
            continue
        cv = _coerce_param_value(v)
        if cv is not None:
            out[k] = cv
    return out


def get_los_last_params() -> Dict[str, Any]:
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(LOS_LAST_DOC_ID).get()
    if not doc:
        return {}
    params = doc.get("params")
    if not isinstance(params, dict):
        return {}
    return sanitize_los_params_payload(params)


def set_los_last_params(params: Dict[str, Any]) -> bool:
    clean = sanitize_los_params_payload(params)
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(LOS_LAST_DOC_ID)
    return ref.set({"params": clean}, merge=True)


def get_can_last_params() -> Dict[str, Any]:
    doc = db.collection(ADMIN_CONFIG_COLLECTION).document(CAN_LAST_DOC_ID).get()
    if not doc:
        return {}
    params = doc.get("params")
    if not isinstance(params, dict):
        return {}
    return sanitize_can_params_payload(params)


def set_can_last_params(params: Dict[str, Any]) -> bool:
    clean = sanitize_can_params_payload(params)
    ref = db.collection(ADMIN_CONFIG_COLLECTION).document(CAN_LAST_DOC_ID)
    return ref.set({"params": clean}, merge=True)
