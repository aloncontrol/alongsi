#!/bin/bash
# =============================================================
# Configure the ALONGSI application (run after cloning code)
# Run as root from /opt/alongsi
# =============================================================
set -e

APP_DIR="/opt/alongsi"
APP_USER="alongsi"

echo "[1/4] Creating Python virtual environment..."
cd $APP_DIR
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "[2/4] Setting up .env file..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp $APP_DIR/.env.example $APP_DIR/.env
    echo ""
    echo "  *** IMPORTANT: Edit the .env file with your credentials! ***"
    echo "  Run: nano $APP_DIR/.env"
    echo ""
fi

echo "[3/4] Setting permissions..."
chown -R $APP_USER:$APP_USER $APP_DIR
chmod 600 $APP_DIR/.env

echo "[4/4] Installing systemd service..."
cat > /etc/systemd/system/alongsi.service << EOF
[Unit]
Description=ALONGSI GSI Monitor
After=network.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable alongsi
systemctl start alongsi

echo ""
echo "=============================="
echo "  App configured!"
echo "  Status: systemctl status alongsi"
echo "  Logs:   journalctl -u alongsi -f"
echo "=============================="
