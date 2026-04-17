"""
Entry ASGI cho Vercel: Vercel tìm `app` trong `main.py` ở thư mục gốc.

Chạy local: uvicorn app.main:app --reload (không đổi).
"""

from app.main import app

__all__ = ["app"]
