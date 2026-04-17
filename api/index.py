"""
Vercel serverless entry: ASGI app qua Mangum (FastAPI toàn site).

Chạy local vẫn dùng: uvicorn app.main:app
"""

from __future__ import annotations

from mangum import Mangum

from app.main import app as fastapi_app

# Biến `app` là convention Vercel Python / ASGI adapter
app = Mangum(fastapi_app, lifespan="auto")
