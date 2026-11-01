## Music Search/Download API (FastAPI + yt-dlp)

API nhận request tìm nhạc trong thư mục local `music/`. Nếu không có, API sẽ tải từ YouTube (dùng cookie trong `cookies/`) và trả về **link stream** để nghe.

### Chuẩn bị

- Copy `env.example` thành `.env` (tự tạo file `.env` vì workspace đang chặn tạo dotfile tự động), rồi điền:
  - `TELEGRAM_BOT_TOKEN`
  - `TELEGRAM_CHAT_ID`
  - `MUSIC_DIR`, `COOKIES_DIR` (khuyến nghị dùng absolute path trên VPS)

### Cài đặt

```bash
# Linux/macOS:
python3 -m venv .venv
source .venv/bin/activate

# Windows (git bash):
python -m venv .venv
source .venv/Scripts/activate

pip install -r requirements.txt
mkdir -p music
```

### Chạy server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Gợi ý chạy nhanh hơn (đã có sẵn trong `uvicorn[standard]`):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop --http httptools
```

### API

- `POST /api/music` body JSON:
  - `{ "query": "tên bài hát" }` (tìm local trước; nếu không có sẽ search+download từ YouTube)
  - hoặc `{ "url": "https://www.youtube.com/watch?v=..." }` (tải trực tiếp)

Response:
- `found`: true/false
- `downloaded`: true/false
- `title`: tiêu đề
- `file_id`: id nội bộ (thường là YouTube video id)
- `stream_url`: link để stream, ví dụ `/stream/{file_id}`

Stream:
- `GET /stream/{file_id}` hỗ trợ HTTP Range (phù hợp cho audio player).



# fasttube
