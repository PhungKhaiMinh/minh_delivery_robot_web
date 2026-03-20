# BK BookBot - Website quản lý Robot giao sách tự hành

Hệ thống website quản lý robot giao sách tự hành trong khuôn viên ĐHBK TP.HCM.

## Tech Stack

- **Backend**: FastAPI (Python) + WebSocket
- **Frontend**: Jinja2 + Tailwind CSS + Alpine.js + Leaflet.js
- **Auth**: JWT (HttpOnly cookie) + bcrypt
- **Database**: JSON offline mô phỏng Firebase Firestore (dễ migration)
- **Real-time**: WebSocket + Polling fallback

## Chức năng

- Đăng ký / Đăng nhập (email @hcmut.edu.vn)
- Đặt lịch giao sách (chọn địa điểm, ngày giờ, số sách)
- Theo dõi Robot real-time trên bản đồ (tích hợp trong chi tiết đơn hàng)
- Lịch sử đơn hàng (filter, hủy đơn)
- Quản lý thông tin cá nhân

## Cài đặt & Chạy

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Truy cập: http://localhost:8000

## Cấu trúc thư mục

```
app/
├── config.py              # Cấu hình chung
├── main.py                # Entry point FastAPI
├── models/                # Pydantic data models
├── services/              # Business logic (auth, booking, robot, db)
├── routers/               # API endpoints + page routes
├── templates/             # Jinja2 HTML templates
└── static/                # CSS + JS
data/collections/          # JSON database (Firebase-like)
```
