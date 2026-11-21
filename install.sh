#!/bin/bash
# Instalasi / update manager-kecil di VPS (Ubuntu/Debian, jalankan sebagai root).
#
# Pemakaian:
#   bash install.sh                      -> pakai REPO_URL di bawah
#   bash install.sh <url-repo-github>    -> pakai URL repo lain
#
# Aman dijalankan ulang: data (channel, token, secret, media) TIDAK dihapus.
set -e

# Repo private: login dulu (lihat catatan di bawah) atau pakai URL SSH git@github.com:mastoppink/manager-kecil.git
REPO_URL="${1:-https://github.com/mastoppink/manager-kecil.git}"
BRANCH="main"
APP_DIR="/root/manager-kecil"
SERVICE="manager-kecil"

echo "========================================"
echo "🚀 INSTALASI MANAGER-KECIL 🚀"
echo "========================================"

if [[ "$REPO_URL" == *GANTI_USERNAME* ]]; then
    echo "❌ URL repo belum diisi. Edit REPO_URL di install.sh atau jalankan: bash install.sh <url-repo>"
    exit 1
fi

# 1. Dependensi sistem
echo "⚙️ Menginstal FFmpeg dan dependensi sistem..."
apt-get update -y
apt-get install -y ffmpeg git curl python3-pip python3-venv

# 2. Ambil kode terbaru tanpa menghapus data
echo "📥 Mengunduh sistem dari GitHub..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git remote set-url origin "$REPO_URL" 2>/dev/null || git remote add origin "$REPO_URL"
    git fetch origin "$BRANCH"
    # Repo baru punya history berbeda: samakan kode dengan GitHub.
    # File data (channels_db.json, secrets/, uploads/, dll.) ada di .gitignore sehingga tetap aman.
    git checkout -B "$BRANCH" "origin/$BRANCH" --force
elif [ -d "$APP_DIR" ]; then
    cd "$APP_DIR"
    git init -q -b "$BRANCH"
    git remote add origin "$REPO_URL"
    git fetch origin "$BRANCH"
    git checkout -B "$BRANCH" "origin/$BRANCH" --force
else
    git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

# 3. Virtual environment & library Python
echo "📦 Menginstall library Python..."
if [ -x venv/bin/python ] && ! venv/bin/python -c "import sys" 2>/dev/null; then
    rm -rf venv
fi
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -U pip
venv/bin/pip install -r requirements.txt

# 4. Service systemd agar jalan 24/7
echo "⚙️ Menyiapkan service ${SERVICE}..."
cat <<EOF > "/etc/systemd/system/${SERVICE}.service"
[Unit]
Description=manager-kecil Web Panel
After=network.target

[Service]
User=root
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

IP_ADDRESS=$(curl -s ifconfig.me || echo "IP-VPS")

echo "========================================"
echo "🎉 INSTALASI SELESAI! 🎉"
echo "Service: systemctl status ${SERVICE}"
echo "Buka di browser: 👉 http://$IP_ADDRESS:5000"
echo "⚠️ Instalasi baru akan meminta pembuatan PIN keamanan saat pertama dibuka."
echo "========================================"
