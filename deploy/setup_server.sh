#!/bin/bash
# =============================================================
# ALONGSI - Server Setup Script for Ubuntu 22.04 / 24.04
# Run as root: bash setup_server.sh
# =============================================================
set -e

APP_USER="alongsi"
APP_DIR="/opt/alongsi"
DOMAIN=""  # Set this before running: e.g. monitor.alon-control.co.il

echo "=============================="
echo "  ALONGSI Server Setup"
echo "=============================="

# 1. System update
echo "[1/8] Updating system..."
apt-get update -qq && apt-get upgrade -y -qq

# 2. Install dependencies
echo "[2/8] Installing packages..."
apt-get install -y -qq python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git curl ufw

# 3. Create app user
echo "[3/8] Creating app user..."
id -u $APP_USER &>/dev/null || useradd -r -s /bin/bash -m -d /home/$APP_USER $APP_USER

# 4. Create app directory
echo "[4/8] Setting up directories..."
mkdir -p $APP_DIR
chown $APP_USER:$APP_USER $APP_DIR

# 5. Firewall
echo "[5/8] Configuring firewall..."
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo ""
echo "=============================="
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Clone code to $APP_DIR"
echo "  2. Run: bash $APP_DIR/deploy/configure_app.sh"
echo "  3. Run: bash $APP_DIR/deploy/configure_nginx.sh YOUR_DOMAIN"
echo "=============================="
