"""
Tạo / cập nhật tài khoản demo (admin + client) để test RBAC.
Chạy khi startup — đặt lại mật khẩu demo theo spec để luôn đăng nhập được.
"""

from app.services.db_service import db
from app.services.auth_service import hash_password


DEMO_ACCOUNTS = [
    {
        "email": "admin@hcmut.edu.vn",
        "name": "Quản trị viên",
        "phone": "0900000001",
        "password": "123456",
        "role": "admin",
    },
    {
        "email": "client@hcmut.edu.vn",
        "name": "Sinh viên Demo",
        "phone": "0900000002",
        "password": "123456",
        "role": "client",
    },
]


def ensure_demo_users() -> None:
    col = db.collection("users")
    for acc in DEMO_ACCOUNTS:
        email = acc["email"].lower()
        found = col.where("email", "==", email)
        payload = {
            "name": acc["name"],
            "phone": acc["phone"],
            "email": email,
            "password_hash": hash_password(acc["password"]),
            "role": acc["role"],
        }
        if found:
            uid = found[0]["_id"]
            col.document(uid).set(payload, merge=True)
        else:
            col.add(payload)
    print("[SEED] Đã đồng bộ tài khoản demo: admin@ / client@ + mật khẩu 123456")
