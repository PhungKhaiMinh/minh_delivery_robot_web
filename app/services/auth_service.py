from __future__ import annotations

"""
Module xác thực người dùng (Authentication).
Quản lý đăng ký, đăng nhập, và JWT token.
Mật khẩu được hash bằng bcrypt trước khi lưu trữ.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from fastapi import Request, HTTPException

from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
from app.services.db_service import db
from app.models.user import UserRegister, UserProfile


def hash_password(password: str) -> str:
    """Mã hóa mật khẩu bằng bcrypt (trực tiếp qua thư viện bcrypt, tương thích mọi phiên bản)."""
    pw = password.encode("utf-8")
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """So sánh mật khẩu nhập vào với hash đã lưu."""
    try:
        if not hashed_password:
            return False
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def create_access_token(user_id: str, email: str, role: str = "client") -> str:
    """
    Tạo JWT access token chứa thông tin user.
    Token có thời hạn được cấu hình trong config.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role if role in ("admin", "client") else "client",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """
    Giải mã JWT token. Trả về payload nếu hợp lệ, None nếu không.
    Tự động kiểm tra hạn sử dụng (expiration).
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def register_user(data: UserRegister) -> tuple[Optional[UserProfile], str]:
    """
    Đăng ký tài khoản mới.
    Kiểm tra email trùng lặp trước khi tạo.
    Trả về (UserProfile, error_message).
    """
    try:
        users_col = db.collection("users")

        # Kiểm tra email đã tồn tại chưa
        existing = users_col.where("email", "==", data.email)
        if existing:
            return None, "Email này đã được đăng ký"

        # Kiểm tra SĐT đã tồn tại chưa
        existing_phone = users_col.where("phone", "==", data.phone)
        if existing_phone:
            return None, "Số điện thoại này đã được sử dụng"

        # Tạo document user mới
        user_data = {
            "name": data.name,
            "phone": data.phone,
            "email": data.email,
            "password_hash": hash_password(data.password),
            "role": "client",
        }

        user_id, success = users_col.add(user_data)
        if not success:
            return None, "Lỗi hệ thống khi tạo tài khoản"

        profile = UserProfile(
            id=user_id,
            name=data.name,
            phone=data.phone,
            email=data.email,
            role="client",
        )
        return profile, ""

    except Exception as e:
        print(f"[AUTH ERROR] Lỗi đăng ký: {e}")
        return None, "Đã xảy ra lỗi không mong muốn"


def login_user(email: str, password: str) -> tuple[Optional[str], Optional[UserProfile], str]:
    """
    Xác thực đăng nhập.
    Trả về (token, UserProfile, error_message).
    """
    try:
        users_col = db.collection("users")
        users = users_col.where("email", "==", email.lower())

        if not users:
            return None, None, "Email hoặc mật khẩu không đúng"

        user_data = users[0]
        if not verify_password(password, user_data.get("password_hash", "")):
            return None, None, "Email hoặc mật khẩu không đúng"

        user_id = user_data["_id"]
        role = user_data.get("role") or "client"
        if role not in ("admin", "client"):
            role = "client"
        token = create_access_token(user_id, email, role)

        profile = UserProfile(
            id=user_id,
            name=user_data["name"],
            phone=user_data["phone"],
            email=user_data["email"],
            role=role,
            created_at=user_data.get("_created_at"),
        )

        return token, profile, ""

    except Exception as e:
        print(f"[AUTH ERROR] Lỗi đăng nhập: {e}")
        return None, None, "Đã xảy ra lỗi không mong muốn"


def get_current_user(request: Request) -> Optional[UserProfile]:
    """
    Lấy thông tin user hiện tại từ JWT cookie.
    Dùng trong middleware để xác thực các request.
    """
    token = request.cookies.get("access_token")
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    try:
        user_data = db.collection("users").document(user_id).get()
        if not user_data:
            return None

        role = user_data.get("role") or "client"
        if role not in ("admin", "client"):
            role = "client"

        return UserProfile(
            id=user_id,
            name=user_data["name"],
            phone=user_data["phone"],
            email=user_data["email"],
            role=role,
            created_at=user_data.get("_created_at"),
        )
    except Exception:
        return None


def require_auth(request: Request) -> UserProfile:
    """
    Middleware bắt buộc đăng nhập.
    Raise HTTPException 401 nếu chưa xác thực.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập")
    return user


def require_admin(request: Request) -> UserProfile:
    """API: bắt buộc đăng nhập và role admin."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Không có quyền quản trị")
    return user
