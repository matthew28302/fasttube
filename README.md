# ⚡ FastTube Studio — Audio & Video High-Performance Engine

<div align="center">

**Nền tảng tìm kiếm, stream và trích xuất Audio / Video YouTube tốc độ cao tích hợp AI Playlist và Trình phát đa phương tiện.**

[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 🌟 Điểm nổi bật & Tính năng chính

### 1. 🚀 Động cơ tải & Stream siêu tốc (2-3s)
- **Đa định dạng linh hoạt**:
  - `M4A 128k` (Siêu tốc 2-3s, tối ưu streaming di động).
  - `MP3 320kbps` (Chất lượng âm thanh phòng thu tiêu chuẩn).
  - `FLAC Lossless` (Âm thanh gốc nguyên bản không nén).
  - `MP4 Video 360p / 720p HD / 1080p Full HD` (Tự động ghép Audio AAC/Opus với Video, tương thích mọi trình phát).
- **Hỗ trợ HTTP Range Requests**: Tua nhạc/video mượt mà không cần tải hết toàn bộ file về bộ nhớ.

### 2. 🤖 Trợ lý AI Playlist Thông Minh
- **Động cơ Groq Compound AI & Web Search**:
  - Tự động tìm kiếm thông tin mới nhất trên Google & YouTube để gợi ý bài hát chuẩn xác, không bị ảo giác.
  - Tự động chuyển đổi giữa `groq/compound` (Web Search) và các mô hình open-source tốc độ cao.
  - Tạo danh sách 8–10 bài hát theo mọi chủ đề, tâm trạng, ca sĩ, thể loại (Chill, Lofi, Gym, Nhạc Tết, Ballad, v.v.).
  - Phát liên tục tự động chuyển bài (Continuous Playlist Player) hoặc tải toàn bộ danh sách chỉ bằng 1 cú nhấp.

### 3. 🎨 Giao diện 3 Chế Độ (Tối / Sáng / Xám) Siêu Nét
- **🌙 Tối (Dark Obsidian)**: Tông màu đen không gian sâu thẳm, huyền ảo và dịu mắt khi nghe nhạc ban đêm.
- **☀️ Sáng (Light High-Contrast)**: Tông sáng hiện đại được tinh chỉnh độ tương phản cao, chữ đen đậm sắc nét (`#0f172a`), không bị mờ hay chói mắt, viền card rõ ràng.
- **🌫️ Xám (Gray Slate)**: Tông xám than trung tính trang nhã, cân bằng hoàn hảo giữa sáng và tối.
- **Huy hiệu Thống kê Thời gian thực**: Hiển thị số người online cùng lúc (`🟢 X online`) và tổng số lượt xem (`• Y lượt xem`) chính xác bằng Persistent Client ID.

### 4. 🛡️ Bảo mật, Chặn Bot & Giới hạn Địa lý (Chỉ cho phép Việt Nam)
- **Chặn toàn bộ quốc tế truy cập (Vietnam Only)**:
  - Tích hợp luật GeoIP tự động phát hiện mã quốc gia qua Cloudflare `CF-IPCountry` hoặc bộ phân giải GeoIP máy chủ.
  - Tự động chặn ngay lập tức mọi truy vấn từ nước ngoài (trả về mã `403 Forbidden`).
  - Cho phép cấu hình bật/tắt dễ dàng qua biến `ALLOW_ONLY_VN=true` trong `.env`.
- **Anti-Bot & Anti-Crawler Hardening**:
  - Chặn đứng các công cụ dò quét lỗ hổng (`sqlmap`, `nikto`, `nmap`, `masscan`, `wpscan`, `gobuster`, `censys`, `shodan`,...).
  - Chặn các bot thu thập nội dung và AI scrapers (`Googlebot`, `Bingbot`, `Yandex`, `Baiduspider`, `AhrefsBot`, `SemrushBot`, `Bytespider`, `GPTBot`, `ClaudeBot`,...).
  - Chặn các thư viện script tự động (`python-requests`, `aiohttp`, `scrapy`, `curl`, `wget`, `go-http-client`).
- **Rate Limiting & Semaphore Concurrency**:
  - Giới hạn tần suất gọi API theo IP chống spam.
  - Giới hạn luồng tải yt-dlp đồng thời (`MAX_CONCURRENT_DOWNLOADS=3`), chống tràn RAM và sập CPU máy chủ khi đông người dùng.

---

## 🛠️ Cài đặt & Chạy trên máy Local

### Yêu cầu hệ thống:
- Python 3.10 trở lên.
- FFmpeg (để ghép video và chuyển đổi bitrate âm thanh).

### Các bước cài đặt:
```bash
# 1. Clone repository
git clone https://github.com/matthew28302/fasttube.git
cd fasttube

# 2. Tạo môi trường ảo
# Trên Linux/macOS:
python3 -m venv .venv
source .venv/bin/activate

# Trên Windows (Powershell):
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Cài đặt thư viện
pip install -r requirements.txt

# 4. Sao chép file cấu hình
cp .env.example .env
# (Mở file .env và điền GROQ_API_KEY nếu muốn dùng tính năng AI Playlist)

# 5. Khởi động máy chủ
python -m app.main
```
Mở trình duyệt truy cập: **`http://localhost:9000`**

---

## 🐳 Triển khai nhanh bằng Docker & Docker Compose

FastTube đã được đóng gói sẵn Dockerfile tối ưu trên nền `python:3.12-slim` kèm FFmpeg hệ thống:

```bash
# Sao chép file cấu hình
cp .env.example .env

# Chạy bằng Docker Compose ở chế độ nền
docker compose up -d --build
```
- Kiểm tra trạng thái: `docker compose ps`
- Xem log: `docker compose logs -f`
- Dừng ứng dụng: `docker compose down`

---

## 🚀 Hướng dẫn Triển khai Public trên VPS Linux (Ubuntu / Debian)

FastTube cung cấp script triển khai tự động 1-click `scripts/deploy.sh`:

### Bước 1: Trỏ Domain về IP của VPS
Trên bảng quản lý DNS (Cloudflare, Namecheap, v.v.), tạo bản ghi `A` trỏ về IP VPS của bạn:
- Tên: `music` (hoặc `@`)
- Giá trị: `IP_VPS_CỦA_BẠN`

### Bước 2: Chạy Script Triển khai
Truy cập SSH vào VPS và chạy:
```bash
cd /var/www
git clone https://github.com/matthew28302/fasttube.git
cd fasttube

# Phân quyền thực thi và chạy deploy
chmod +x scripts/deploy.sh
bash scripts/deploy.sh
```

Script sẽ tự động:
1. Cài đặt FFmpeg, Python 3 venv, Nginx, Certbot.
2. Thiết lập Systemd Service `fasttube.service` tự động khởi động cùng hệ thống và tự phục hồi khi crash.
3. Tạo cấu hình Reverse Proxy Nginx tối ưu cho video streaming và WebSocket.

### Bước 3: Cài đặt chứng chỉ SSL HTTPS miễn phí (Let's Encrypt)
```bash
sudo certbot --nginx -d music.yourdomain.com
```

### Các lệnh quản trị hữu ích trên VPS:
- Xem trạng thái máy chủ: `sudo systemctl status fasttube`
- Khởi động lại: `sudo systemctl restart fasttube`
- Xem log thời gian thực: `sudo journalctl -u fasttube -f`

---

## ⚙️ Bảng biến môi trường (.env)

| Biến | Mặc định | Mô tả |
| :--- | :--- | :--- |
| `RUN_HOST` | `0.0.0.0` | Địa chỉ IP lắng nghe |
| `RUN_PORT` | `9000` | Cổng dịch vụ |
| `BASE_URL` | *(Tự động)* | Domain public (tự động nhận qua Nginx/Cloudflare headers) |
| `GROQ_API_KEY` | `""` | Khóa API Groq dùng cho tính năng tạo AI Playlist |
| `MAX_CONCURRENT_DOWNLOADS`| `3` | Giới hạn số bài hát tải cùng lúc từ yt-dlp để chống quá tải VPS |
| `YTDLP_CONCURRENT_FRAGMENTS`| `4` | Số phân mảnh tải song song trên mỗi luồng download |
| `MAX_VIDEO_DURATION_S` | `1200` | Thời lượng video tối đa cho phép tải (giây) |
| `FAST_MODE` | `true` | Tối ưu hóa tải luồng m4a trực tiếp không encode lại |
| `PREFER_ABR_128` | `true` | Ưu tiên stream bitrate 128k để phản hồi tức thì |
| `ALLOW_ONLY_VN` | `true` | Chỉ cho phép lưu lượng từ Việt Nam, chặn toàn bộ quốc tế |
| `LOG_LEVEL` | `INFO` | Mức độ chi tiết log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 📄 Bản quyền & Đóng góp

- Dự án được phát hành dưới giấy phép **MIT License**.
- Đóng góp mã nguồn và báo lỗi vui lòng gửi Pull Request hoặc Issue trên GitHub: [FastTube Repo](https://github.com/matthew28302/fasttube).
