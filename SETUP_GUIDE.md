# Hướng dẫn cài đặt BK BookBot Web — Dành cho thành viên team

Hướng dẫn từ A → Z để clone code, cấu hình Firebase, và chạy website trên máy cá nhân.
Tất cả thành viên sẽ **đọc/ghi chung một database Firebase** (Firestore).

---

## Bước 1: Cài đặt môi trường

### 1.1 Cài Git, Python, Conda

- **Git**: https://git-scm.com/downloads
- **Miniconda** (khuyên dùng): https://docs.conda.io/en/latest/miniconda.html
- Yêu cầu **Python ≥ 3.9**

### 1.2 Clone source code

```bash
git clone https://github.com/PhungKhaiMinh/minh_delivery_robot_web.git
cd minh_delivery_robot_web
```

### 1.3 Tạo môi trường Conda + cài thư viện

```bash
conda create -p .conda python=3.9 -y
conda activate ./.conda
pip install -r requirements.txt
```

> **Không dùng Conda?** Dùng venv cũng được:
> ```bash
> python -m venv venv
> source venv/bin/activate   # Linux/Mac
> venv\Scripts\activate      # Windows
> pip install -r requirements.txt
> ```

---

## Bước 2: Lấy Firebase Service Account Key (quan trọng nhất)

File này cho phép server Python kết nối tới Firestore database chung của team.

### 2.1 Truy cập Firebase Console

1. Mở trình duyệt, vào: https://console.firebase.google.com/
2. Đăng nhập bằng **tài khoản Google đã được cấp quyền** truy cập project.
3. Chọn project **bk-bookbot** (hoặc tên project mà team đang dùng).

### 2.2 Generate Private Key

1. Nhấn vào **biểu tượng bánh răng ⚙️** (góc trên bên trái, cạnh "Project Overview").
2. Chọn **Project settings** (Cài đặt dự án).
3. Chọn tab **Service accounts** (Tài khoản dịch vụ).
4. Ở mục "Firebase Admin SDK", đảm bảo đang chọn **Python**.
5. Nhấn nút **"Generate new private key"** (Tạo khóa riêng tư mới).
6. Một hộp thoại xác nhận hiện ra → nhấn **"Generate key"**.
7. Trình duyệt sẽ **tải về một file JSON** có tên dạng:
   ```
   bk-bookbot-firebase-adminsdk-xxxxx-xxxxxxxxxx.json
   ```

> ⚠️ **BẢO MẬT**: File này chứa khóa bí mật. **KHÔNG** commit lên Git, **KHÔNG** gửi qua chat công khai.

### 2.3 Đặt file vào project

1. **Copy** file JSON vừa tải về vào **thư mục gốc** của project (cùng cấp với `app/`, `requirements.txt`).

2. **Đổi tên** (hoặc tạo symlink) thành `firebase-service-account.json`:

   **Cách 1 — Đổi tên trực tiếp (đơn giản nhất):**
   ```bash
   # Thay tên file thật của bạn vào đây
   mv bk-bookbot-firebase-adminsdk-xxxxx-xxxxxxxxxx.json firebase-service-account.json
   ```

   **Cách 2 — Tạo symlink (giữ nguyên file gốc):**
   ```bash
   # Linux / Mac
   ln -s bk-bookbot-firebase-adminsdk-xxxxx-xxxxxxxxxx.json firebase-service-account.json

   # Windows (PowerShell, chạy với quyền Admin)
   New-Item -ItemType SymbolicLink -Path firebase-service-account.json -Target bk-bookbot-firebase-adminsdk-xxxxx-xxxxxxxxxx.json
   ```

3. **Kiểm tra** cấu trúc thư mục — file phải nằm đúng vị trí:
   ```
   minh_delivery_robot_web/
   ├── app/
   ├── requirements.txt
   ├── firebase-service-account.json   ← FILE NÀY
   └── ...
   ```

> File này đã được thêm vào `.gitignore` nên sẽ **không bị commit** lên Git.

---

## Bước 3: Chạy server

```bash
cd minh_delivery_robot_web
conda activate ./.conda
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Kiểm tra log khởi động

Khi server khởi động, trong terminal sẽ thấy dòng log:

```
[DB] Firestore Cloud (project: bk-bookbot)
```

✅ Nếu thấy dòng trên → **đã kết nối Firebase thành công**, dữ liệu đọc/ghi chung với cả team.

❌ Nếu thấy `[DB] File JSON local` → file key chưa đúng vị trí hoặc sai tên. Quay lại Bước 2.3.

### Truy cập website

- **Trên chính máy đang chạy**: http://localhost:8000
- **Từ máy khác cùng mạng LAN/WiFi**: `http://<IP-máy-chạy-server>:8000`
  - Tìm IP máy chạy server: `hostname -I` (Linux) hoặc `ipconfig` (Windows)
  - Ví dụ: http://192.168.100.76:8000

---

## Bước 4: Tạo tài khoản & Đăng nhập

1. Truy cập http://localhost:8000/register
2. Đăng ký bằng email `@hcmut.edu.vn`
3. Đăng nhập tại http://localhost:8000/login

### Tài khoản Admin

Để có quyền Admin (xem trang quản lý), cần sửa trường `role` trong Firestore:

1. Vào https://console.firebase.google.com/ → chọn project → **Firestore Database**
2. Tìm collection `users` → tìm document của tài khoản cần cấp quyền
3. Sửa trường `role` thành `"admin"`
4. Truy cập trang admin: http://localhost:8000/admin/

---

## Tóm tắt cấu trúc project

```
minh_delivery_robot_web/
├── app/
│   ├── config.py                  # Cấu hình MQTT, Firebase, waypoints
│   ├── main.py                    # Entry point FastAPI
│   ├── models/                    # Pydantic data models
│   ├── services/
│   │   ├── auth_service.py        # Xác thực JWT
│   │   ├── booking_service.py     # Quản lý đơn hàng
│   │   ├── db_service.py          # Kết nối database (Firestore / JSON local)
│   │   ├── mqtt_client.py         # MQTT client server-side
│   │   ├── pathfinding_service.py # Dijkstra + GPS→local XY (ECEF→ENU)
│   │   └── scheduler_service.py   # Tự động dispatch đơn hàng
│   ├── routers/                   # API endpoints + page routes
│   └── templates/                 # Jinja2 HTML templates
├── requirements.txt
├── firebase-service-account.json  # ← BẠN TẠO Ở BƯỚC 2 (không có sẵn trong git)
└── .gitignore
```

---

## Câu hỏi thường gặp

### Q: Server báo `[DB] File JSON local`, không kết nối được Firebase?
**A:** File `firebase-service-account.json` chưa có hoặc sai vị trí. Kiểm tra:
```bash
ls -la firebase-service-account.json
```
File phải tồn tại ở thư mục gốc project.

### Q: Lỗi `ModuleNotFoundError: No module named 'xxx'`?
**A:** Chưa activate môi trường hoặc chưa cài thư viện:
```bash
conda activate ./.conda
pip install -r requirements.txt
```

### Q: Máy khác cùng mạng không truy cập được?
**A:** Kiểm tra:
- Hai máy cùng mạng WiFi/LAN (cùng dải IP `192.168.x.x`)
- Firewall không chặn port 8000
- Router không bật "AP Isolation"

### Q: Tôi muốn dùng port khác (không phải 8000)?
**A:** Thay số port:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload
```

### Q: MQTT broker là gì? Có cần cài không?
**A:** MQTT broker (`45.117.177.157:1883`) đã được cấu hình sẵn trong `config.py`, chạy trên server riêng. Bạn **không cần cài** broker trên máy mình.

---

## Deploy lên Railway.com

Railway chạy **một container Linux** với **uvicorn** (không phải serverless như Vercel): MQTT nền, scheduler và WebSocket bridge đều hoạt động bình thường khi **không** đặt biến `VERCEL=1`.

### Bước A — Tạo project trên Railway

1. Đăng nhập **https://railway.com** → **New Project** → **Deploy from GitHub repo** → chọn repo `minh_delivery_robot_web`.
2. Railway tạo **service** (web). Vào **Variables** và thêm các biến giống production: `SECRET_KEY`, Firebase (`FIREBASE_SERVICE_ACCOUNT_JSON` hoặc file + `USE_FIRESTORE`), `MQTT_*`, v.v. (xem `app/config.py`).

### Bước B — Port

Railway inject sẵn **`PORT`**. Repo đã có:

- **`Procfile`**: `web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- **`railway.toml`**: `startCommand` tương đương với `--host 0.0.0.0 --port $PORT`

Nếu trong UI Railway bạn đặt **Custom Start Command**, hãy để trống hoặc trùng với trên để tránh ghi đè sai.

### Bước C — Generate domain

Trong service → **Settings** → **Networking** → **Generate Domain** (HTTPS). Mở URL public để kiểm tra.

### Bước D — Firebase trên Railway

- Không dùng file `firebase-service-account.json` trên đĩa container trừ khi bạn commit (không nên). Nên dán JSON vào biến **`FIREBASE_SERVICE_ACCOUNT_JSON`** hoặc **`FIREBASE_SERVICE_ACCOUNT_B64`** (code trong `app/config.py` đã hỗ trợ ghi ra `/tmp` khi deploy).

### Ghi chú

- **Không** cần `vercel.json` / `main.py` gốc repo — đã gỡ để tránh nhầm với deploy Vercel.
- Nếu sau này bạn vẫn deploy song song lên Vercel, đặt `VERCEL=1` trên Vercel; Railway **không** set biến này.
