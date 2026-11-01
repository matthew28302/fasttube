# Deploy Music API len VPS


Ban nay da duoc sua de chay duoc tren may moi, khac ban goc o 3 cho:
- Telegram timeout tang tu 3s len 15s trong `app/telegram_notify.py` (gui Telegram qua proxy cham se khong mat tin ngay).
- Fast mode truoc day bo qua cookie, nay da cho dung cookie trong `app/downloader.py`.
- Lenh yt-dlp them `--remote-components ejs:github` va `--no-check-certificates`, JS runtime doi tu deno o duong dan cu sang `node` co san tren VPS.
- `requirements.txt` ghim yt-dlp 2026.10.26, vi ban 2025.12.8 khong giai duoc challenge moi cua YouTube.
- `requirements.txt` ghim yt-dlp-ejs 0.8.0 (ban 0.3.2 cu bi canh bao khong ho tro, du thoi gian tai khong doi).
- `.env.example` dat san `YTDLP_PLAYER_CLIENT=tv_samsung`: chi tv_samsung/tv_downgraded kem cookie moi tra format DASH https 249/140 that tren may test 28/10, cac client ios/android chi tra SABR hoac bi bot check.
- Duong dan theo project root (config.py tu lay thu muc cha cua app/): MUSIC_DIR/COOKIES_DIR/LOG_FILE de dang tuong doi ./music, ./cookies, ./logs/app.log, khong can sua tay khi sang may moi. Chi tiet o muc cuoi file.
- Che do mac dinh la opus 249 (~47k) theo lua chon ngay 2026-10-28: file moi tai ve dang .webm, khoang 2 MB cho bai 5 phut thay vi 4-5 MB dang m4a 128k, khong transcode. Neu muon quay lai m4a 128k thi doi AUDIO_MODE=opus thanh AUDIO_MODE=m4a trong .env va restart service. File cu dang m4a trong thu muc music van giu nguyen va van tra ve ngay khi trung local.
- Search nhanh bang innertube truc tiep (app/fastsearch.py, ~1.3s thay cho ytsearch ~4s cua yt-dlp), co cham diem tieu de de chon dung ban Official MV va tu dong fallback ve ytsearch1 neu search nhanh loi.
- Toi uu toc do (do ngay 2026-10-27 tren may tro ly): luot tai chinh bo qua HLS (`youtube:skip=hls`, tiet kiem ~2s buoc "Downloading m3u8 information") va bo buoc ffmpeg FixupM4a cho che do m4a (`--fixup never`, file van dung chuan theo ffprobe, tiet kiem ~1-2s). Luot fallback video cuoi cung van giu HLS de khong mat duong lui.
- Da thu va LOAI BO: aria2c song song 16 ket noi (cham hon, 23.6s so voi 12.7s), player_skip webpage+configs (loi video unavailable), ep player_client thanh web/tv/android/ios rieng le (loi hoac khong nhanh hon), tai song song bang Range 8 luong (4.5s so voi ~3s cua yt-dlp goc).

Goi nay KHONG chua: file `.env` that, file cookie that, file nhac da tai, venv, cache, log. Ban phai tu tao lai 3 thu do tren VPS.

## 1. Upload va giai nen

```bash
# tren VPS, vi du dat o /home/<user>/new_music
unzip music_api_vps_deploy.zip
cd new_music
```

## 2. Cai dat

```bash
bash scripts/build.sh
```

Script nay cai `python3-venv`, `ffmpeg`, `build-essential`, `python3-dev` (Ubuntu/Debian), tao `.venv`, cai `requirements.txt`, tao thu muc `music`, `cache`, `cookies`, `logs`.

Can them Node.js de giai JS challenge cua YouTube:

```bash
sudo apt install -y nodejs
node --version
```

## 3. Cau hinh .env

```bash
cp .env.example .env
nano .env
```

Dien:
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (neu muon bao Telegram).
- `MUSIC_DIR`, `COOKIES_DIR`, `LOG_FILE` bang absolute path tren VPS.
- `BASE_URL=http://api.imfishball.id.vn` (neu dung HTTPS thi doi thanh `https://api.imfishball.id.vn`). De trong se khien `stream_url` tra ve dang localhost, khong dung duoc tu ngoai.
- `FAST_MODE=1` (da sua de dung cookie).
- `YTDLP_JS_RUNTIMES=node`, `YTDLP_JS_RUNTIMES_PATH=` de trong.

## 4. Cookie YouTube moi

- Tren trinh duyet PC dang dang nhap dung tai khoan YouTube, cai extension Get cookies.txt, mo youtube.com, bam Export Current Tab.
- Copy file `www.youtube.com_cookies.txt` vao `cookies/` tren VPS, dat quyen:

```bash
chmod 600 cookies/www.youtube.com_cookies.txt
```

Cookie se het han / bi xoay theo thoi gian, khi yt-dlp bao cookie khong hop le thi xuat lai file moi va thay vao.

## 5. Chay thu

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop --http httptools
```

Kiem tra tu chinh VPS:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/music -H 'Content-Type: application/json' -d '{"query":"Ten bai hat"}'
```

Bai chua co local se tra `downloaded: true` va gui Telegram "Not found locally..." roi "Download completed". Bai da co local se tra `found: true, downloaded: false` va KHONG gui tin download, dung thiet ke code.

## 6. Chay nen bang systemd

```bash
# sua <user> va duong dan trong music-api.service cho dung VPS cua ban
sudo cp music-api.service /etc/systemd/system/music-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now music-api
sudo systemctl status music-api
sudo journalctl -u music-api -f
```

## 7. Nginx + domain

- Tro DNS cua `api.imfishball.id.vn` ve IP VPS.
- Copy `nginx.conf.example` vao `/etc/nginx/sites-available/api.imfishball.id.vn`, symlink sang `sites-enabled`, kiem tra va reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

- Neu muon HTTPS: `sudo certbot --nginx -d api.imfishball.id.vn`, roi doi `BASE_URL` trong `.env` sang `https://...` va restart service.
- Endpoint stream cua code hien tai la `/stream/<file_id>`. File nginx mau da co rewrite `/stream_pcm/<id>` ve `/stream/<id>` de tuong thich voi player cu goi dang `/stream_pcm` nhu vi du thang 2.

## Luu y

- Khong chia se file `.env` that va file cookie that cho nguoi khac, vi trong do co token va session YouTube.
- Dung luong o `music/` se tang theo so bai tai, can theo doi dia VPS.
- Neu YouTube doi player / challenge, buoc dau tien la nang cap yt-dlp: `source .venv/bin/activate && pip install --upgrade yt-dlp`, roi restart service.


## KHONG CAN SUA DUONG DAN (tu 2026-10-28)
Code tu lay thu muc goc project lam goc (config.py: project_root = thu muc cha cua app/).
- MUSIC_DIR, COOKIES_DIR, LOG_FILE trong .env cu de dang tuong doi ./music, ./cookies,
  ./logs/app.log nhu trong .env.example, hoac xoa han 3 dong do thi code tu dung mac dinh
  music/, cookies/, logs/app.log trong project.
- .env cung duoc doc tu thu muc goc project, khong phu thuoc thu muc dang chay lenh.
- Cache search (cache/ytsearch.json) va cache yt-dlp (cache/ytdlp) cung theo project root.
- Chi can sua duong dan neu muon de nhac o cho khac, luc do moi dung absolute path,
  hoac dat bien PROJECT_ROOT=/duong/dan/project.
- Service: neu dung music-api-user.service thi khong can sua gi ca (dung %h), chi can
  dat project o ~/new_music. Neu dung music-api.service (system) thi chi sua dong User=.
