#!/bin/bash
# Setup ai-wa-bot di VPS dev
# Jalankan: bash setup.sh

set -e

echo "=== AI WA Bot Setup ==="

# 1. Buat venv
cd /home/ubuntu01/ai-wa-bot
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠  PENTING: Edit .env dulu sebelum lanjut!"
    echo "   nano /home/ubuntu01/ai-wa-bot/.env"
    echo ""
    echo "   Isi minimal:"
    echo "   BOT_SERVICE_TOKEN=  (sama dengan di LapakinUMKM/backend/.env)"
    echo "   GEMINI_API_KEY=     (copy dari LapakinUMKM/backend/.env)"
    echo "   OPENAI_API_KEY=     (copy dari LapakinUMKM/backend/.env)"
    exit 0
fi

# 4. Install systemd service
sudo cp ai-wa-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ai-wa-bot
sudo systemctl start ai-wa-bot

echo ""
echo "=== Setup selesai! ==="
echo "Status: sudo systemctl status ai-wa-bot"
echo "Log:    sudo journalctl -u ai-wa-bot -f"
echo "Test:   curl http://127.0.0.1:8002/health"
