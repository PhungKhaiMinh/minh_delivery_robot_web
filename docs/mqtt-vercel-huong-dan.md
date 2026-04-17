# MQTT + Vercel — hướng dẫn ngắn (BK BookBot)

## Vì sao trên Vercel “không nối được MQTT”?

1. **Trang Vercel là HTTPS.** Trình duyệt **cấm** JavaScript mở kết nối **`ws://`** (không mã hóa) tới broker → phải dùng **`wss://`** (WebSocket có TLS).
2. **Chế độ “bridge”** (WebSocket tới chính app Vercel rồi server nối TCP 1883) **thường không chạy** trên serverless. Code đã mặc định **tắt bridge** khi deploy Vercel; trình duyệt nối **thẳng** broker bằng `mqtt.js`.

## Việc bạn cần làm (2 phần)

### A. Trên Vercel — thêm biến môi trường

Vào **Project → Settings → Environment Variables**, thêm (hoặc chỉnh) các biến sau, rồi **Redeploy**:

| Biến | Giá trị gợi ý (đúng với broker của bạn) |
|------|----------------------------------------|
| `MQTT_USE_SERVER_BRIDGE` | `false` |
| `MQTT_BROKER_HOST` | `45.117.177.157` |
| `MQTT_USERNAME` | `client` |
| `MQTT_PASSWORD` | `viam1234` |
| `MQTT_WS_URL` | **Bắt buộc** URL WebSocket TLS, ví dụ `wss://45.117.177.157:8884` (đổi cổng đúng với Mosquitto). Nếu không đặt, app trên Vercel sẽ thử mặc định `wss://MQTT_BROKER_HOST:8884` (xem `MQTT_WSS_PORT`). |
| `MQTT_WSS_PORT` | (tuỳ chọn) Cổng WSS trên broker, mặc định code là `8884` khi không có `MQTT_WS_URL`. |

**Lưu ý:** Nếu broker **chưa** mở cổng **WSS** thì dù điền URL đúng vẫn lỗi — phải làm phần B.

### B. Trên VPS (45.117.177.157) — bật WebSocket có TLS (WSS)

Mosquitto thường có:

- `listener 1883` — MQTT TCP (robot / Python).
- `listener 9001` — WebSocket **không** TLS (`ws://`) — **dùng được khi web chạy HTTP (local)**; **không** dùng được từ trang **HTTPS** (Vercel).

Bạn cần thêm một **listener WebSocket + chứng chỉ TLS**, ví dụ cổng **8884**:

1. Có file chứng chỉ **server** (nên dùng Let’s Encrypt nếu có tên miền trỏ về IP; hoặc chứng chỉ tự ký để thử — trình duyệt có thể chặn hoặc cảnh báo).
2. Trong `mosquitto.conf` thêm khối tương tự file mẫu trong repo: `deploy/mosquitto-wss-example.conf`.
3. Mở firewall/security group cho cổng **8884** (hoặc cổng bạn chọn).
4. Khởi động lại Mosquitto.

Sau đó trên Vercel đặt:

`MQTT_WS_URL=wss://45.117.177.157:8884`

(khớp cổng và giao thức `wss`).

## Kiểm tra nhanh

- Mở trang Admin (Robot status / Orders). Nếu vẫn thấy **banner vàng** về `ws://` nghĩa là URL WebSocket vẫn chưa là `wss://` — kiểm tra lại env.
- Mở **DevTools → Console / Network** xem lỗi kết nối (từ chối TLS, sai cổng, sai user/pass).

## English (short)

Vercel serves **HTTPS**, so the browser only allows **`wss://`** to your broker, not **`ws://`**. Set **`MQTT_WS_URL=wss://45.117.177.157:<your-wss-port>`** in Vercel and enable a **TLS WebSocket listener** on the broker (see `deploy/mosquitto-wss-example.conf`). Use **`MQTT_USE_SERVER_BRIDGE=false`**.
