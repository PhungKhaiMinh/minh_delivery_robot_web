# Hướng dẫn chi tiết: Lưu dữ liệu BK BookBot trên Firebase (Firestore)

Tài liệu này dành cho người **chưa từng dùng Firebase**. Website của bạn hiện lưu **users**, **bookings**, **robots** dưới dạng file JSON trong `data/collections/`. Phần dưới hướng dẫn chuyển sang **Cloud Firestore** (cơ sở dữ liệu NoSQL của Firebase, rất giống cấu trúc bạn đang dùng).

---

## 1. Firebase là gì? Cần bật dịch vụ nào?

- **Firebase** là nền tảng của Google (hosting, auth, database, …).
- Bạn chỉ cần **Cloud Firestore** để thay cho file JSON trên máy.
- **Realtime Database** là sản phẩm khác — dự án này dùng **Firestore**, không bắt dùng Realtime DB.

**Dữ liệu nào “đưa lên Firebase”?**

| Dữ liệu | Collection Firestore | Ghi chú |
|--------|----------------------|--------|
| Người dùng (email, hash mật khẩu, role, …) | `users` | Nên bật Firestore, không public rules cho client |
| Đơn đặt sách | `bookings` | Toàn bộ field như bản local |
| Robot (pin, tọa độ, …) | `robots` | Ví dụ document `robot_01` |

**Không đưa lên Firebase (hoặc không bắt buộc):**

- File tĩnh (CSS/JS/template) vẫn nằm trên server.
- **SECRET_KEY**, file **service account JSON**: chỉ lưu trên server / biến môi trường, **không** đưa lên Git, **không** nhúng vào frontend.
- MQTT / robot server URL: vẫn là cấu hình server (`config` / `.env`).

---

## 2. Tạo project Firebase (từng bước)

1. Mở trình duyệt, vào **https://console.firebase.google.com/**
2. Đăng nhập tài khoản **Google**.
3. Bấm **Add project** / **Tạo dự án**.
4. Đặt tên dự án (ví dụ `bk-bookbot`), có thể tắt Google Analytics nếu không cần.
5. Chờ vài giây để Google tạo xong project.

---

## 3. Bật Cloud Firestore

1. Trong Firebase Console, chọn project vừa tạo.
2. Menu trái: **Build** → **Firestore Database**.
3. Bấm **Create database** / **Tạo cơ sở dữ liệu**.
4. Chế độ:
   - **Production mode** (an toàn hơn — sau đó bạn sửa rules).
   - Hoặc **Test mode** chỉ để thử nhanh (có thời hạn, **không** để lâu trên production).
5. Chọn **vùng (region)** gần bạn (ví dụ `asia-southeast1` — Singapore).

**Lưu ý quan trọng:** Ứng dụng FastAPI của bạn dùng **Firebase Admin SDK** trên server. Admin SDK **bỏ qua Firestore Security Rules** — rules chỉ áp dụng cho truy cập từ **app/web/mobile** trực tiếp vào Firestore. Vì vậy:

- Bảo mật chính là **giữ kín file service account** và **SECRET_KEY** trên server.
- Rules vẫn nên siết chặt (chỉ cho phép user đọc/ghi đúng phần của họ) nếu sau này bạn cho client gọi Firestore trực tiếp.

---

## 4. Tạo Service Account (khóa cho server Python)

Server không dùng “API key” công khai kiểu web; nó dùng **file JSON bí mật**.

1. Firebase Console → biểu tượng **bánh răng** → **Project settings** / **Cài đặt dự án**.
2. Tab **Service accounts**.
3. Bấm **Generate new private key** / **Tạo khóa riêng tư mới** → xác nhận → tải file `.json` về máy.
4. Đặt tên dễ nhớ, ví dụ: `bk-bookbot-firebase-adminsdk-xxxxx.json`.
5. Lưu file ở thư mục **ngoài Git** (ví dụ `~/secrets/` hoặc chỉ trên máy chủ deploy).

**Không** commit file này: đã thêm gợi ý ignore `*firebase-adminsdk*.json` trong `.gitignore`.

---

## 5. Cấu trúc dữ liệu trên Firestore (trùng với app)

Sau khi chạy app hoặc script migrate, bạn sẽ thấy:

- Collection **`users`**: mỗi document ID = id user (chuỗi hex), field: `name`, `email`, `phone`, `password_hash`, `role`, `_created_at`, `_updated_at`, …
- Collection **`bookings`**: mỗi document = một đơn, field như `user_id`, `status`, `pickup_date`, …
- Collection **`robots`**: ví dụ document id `robot_01`.

Bạn có thể xem trong Firestore Console tab **Data**.

---

## 6. Cấu hình trên máy bạn (biến môi trường)

Trong thư mục dự án, bạn **chỉ cần** để app **tìm thấy file Service Account** — Firestore sẽ được bật **tự động** (không bắt buộc `USE_FIRESTORE=true` nữa).

**Cách 1 — Biến môi trường (khuyên dùng):**

```bash
export FIREBASE_CREDENTIALS_PATH=/home/minh/secrets/bk-bookbot-firebase-adminsdk-xxxxx.json
```

Hoặc tên chuẩn Google:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/home/minh/secrets/bk-bookbot-firebase-adminsdk-xxxxx.json
```

**Cách 2 — File cố định trong project:** đặt file JSON vào thư mục gốc dự án với tên **`firebase-service-account.json`** (đã thêm vào `.gitignore`, không commit).

**Ép dùng JSON local** (bỏ qua Firestore dù có file khóa): `export USE_FIRESTORE=false`

**Ép bắt buộc Firestore** (lỗi rõ nếu thiếu khóa): `export USE_FIRESTORE=true`

Cài thư viện (một lần):

```bash
pip install -r requirements.txt
```

Chạy server:

```bash
cd ~/minh_delivery_robot_web
conda activate /path/to/.conda   # nếu bạn dùng conda
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Không có file khóa và không đặt `USE_FIRESTORE=true` → app dùng **`data/collections/*.json`**.
- Có file khóa (env hoặc `firebase-service-account.json`) → app **đọc/ghi Firestore** tự động.

---

## 7. Chuyển dữ liệu cũ từ máy lên Firestore (migrate)

Nếu bạn đã có sẵn file trong `data/collections/`:

```bash
export FIREBASE_CREDENTIALS_PATH=/đường/dẫn/serviceAccount.json
python scripts/migrate_local_to_firestore.py
```

Script đọc từng file `users/*.json`, `bookings/*.json`, `robots/*.json` và **ghi đè/ghi** document cùng ID trên Firestore.

Sau đó chạy app (đã có file khóa hoặc env trỏ tới khóa) — seed tài khoản demo vẫn chạy và **cập nhật** lên Firestore nếu trùng email.

---

## 8. Chi phí & giới hạn (tóm tắt)

- Firestore có **free tier** (hạn mức đọc/ghi/ngày). Với dự án nhỏ / demo thường đủ.
- Xem mức dùng tại **Firebase Console** → **Usage and billing**.

---

## 9. Checklist nhanh

- [ ] Tạo project Firebase  
- [ ] Bật Firestore, chọn region  
- [ ] Tải file JSON service account, **không** commit Git  
- [ ] `export USE_FIRESTORE=true` + `FIREBASE_CREDENTIALS_PATH`  
- [ ] `pip install -r requirements.txt`  
- [ ] (Tuỳ chọn) `python scripts/migrate_local_to_firestore.py`  
- [ ] Chạy `uvicorn` và đăng ký / đăng nhập thử  

Nếu bạn gặp lỗi `Permission denied` hoặc `invalid_grant`, thường là đường dẫn JSON sai hoặc file khóa bị xoá / tạo lại project — tải lại private key mới từ Console.

---

## 10. Hỗ trợ thêm

- Tài liệu Admin Python: https://firebase.google.com/docs/admin/setup  
- Firestore: https://firebase.google.com/docs/firestore  

Nếu bạn muốn bước tiếp theo (deploy lên **Cloud Run** + biến môi trường trên Google Cloud), có thể mô tả môi trường deploy để cấu hình chi tiết hơn.
