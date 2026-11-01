#!/usr/bin/env bash
# ===================================================================
# FastTube Studio - One-Click VPS Production Deployment Script
# OS: Ubuntu 20.04 / 22.04 / 24.04, Debian 11 / 12
# ===================================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="$(whoami)"
SERVICE_NAME="fasttube"

echo "================================================================"
echo "🚀 Starting FastTube Studio Deployment..."
echo "📂 App Directory: ${APP_DIR}"
echo "👤 User: ${APP_USER}"
echo "================================================================"

# 1. Update and install system dependencies
echo "==> [1/6] Installing system packages (FFmpeg, Python3, Nginx)..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv ffmpeg curl git nginx certbot python3-certbot-nginx
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip ffmpeg curl git nginx certbot
fi

# 2. Setup storage directories
echo "==> [2/6] Setting up directories..."
mkdir -p "${APP_DIR}/music" "${APP_DIR}/cookies" "${APP_DIR}/cache" "${APP_DIR}/logs"
chmod -R 755 "${APP_DIR}/music" "${APP_DIR}/cache"

# 3. Setup Python Virtual Environment
echo "==> [3/6] Setting up Python virtual environment..."
cd "${APP_DIR}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 4. Setup Environment File
if [ ! -f ".env" ]; then
    echo "==> [4/6] Creating .env from .env.example..."
    cp .env.example .env
    echo "⚠️  LƯU Ý: Vui lòng mở file .env và điền GROQ_API_KEY nếu bạn muốn dùng tính năng AI Playlist!"
else
    echo "==> [4/6] .env file already exists."
fi

# 5. Create Systemd Service
echo "==> [5/6] Configuring Systemd Service (/etc/systemd/system/${SERVICE_NAME}.service)..."
cat <<EOF | sudo tee /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=FastTube Studio Audio & Video Engine
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --workers 2
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

# 6. Setup Nginx Reverse Proxy Template
echo "==> [6/6] Generating Nginx Configuration template..."
NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
cat <<'EOF' | sudo tee "${NGINX_CONF}"
server {
    listen 80;
    server_name _; # Thay bang ten mien cua ban (vi du: music.yourdomain.com)

    client_max_body_size 100M;

    # Anti-Scanner direct drop at Nginx level
    if ($http_user_agent ~* (sqlmap|nikto|dirbuster|nmap|masscan|wpscan|gobuster|censys)) {
        return 403;
    }

    location ~ /\.(env|git|aws) {
        deny all;
        return 404;
    }

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_http_version 1.1;

        # WebSocket & Upgrade Headers
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Forward real client IP behind Cloudflare/Nginx
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Streaming & Buffering optimizations
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
EOF

if [ ! -f "/etc/nginx/sites-enabled/${SERVICE_NAME}" ]; then
    sudo ln -s "${NGINX_CONF}" "/etc/nginx/sites-enabled/" || true
fi

# Test Nginx and reload if valid
if sudo nginx -t >/dev/null 2>&1; then
    sudo systemctl reload nginx
    echo "✅ Nginx reloaded successfully!"
else
    echo "⚠️ Nginx test had warnings, please verify /etc/nginx/sites-available/${SERVICE_NAME}"
fi

echo "================================================================"
echo "🎉 DEPLOYMENT COMPLETE!"
echo "📡 Service status:"
sudo systemctl status ${SERVICE_NAME} --no-pager | head -n 12
echo ""
echo "👉 Truy cập ứng dụng: http://IP_VPS:9000 hoặc qua domain Nginx port 80."
echo "👉 Xem log dịch vụ: sudo journalctl -u ${SERVICE_NAME} -f"
echo "👉 Cài SSL Let's Encrypt: sudo certbot --nginx -d yourdomain.com"
echo "================================================================"
