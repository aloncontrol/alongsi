#!/bin/bash
# =============================================================
# Configure nginx + SSL for ALONGSI
# Usage: bash configure_nginx.sh monitor.yourdomain.com
# =============================================================
set -e

DOMAIN=$1
if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <domain>"
    echo "Example: $0 monitor.alon-control.co.il"
    exit 1
fi

echo "[1/3] Creating nginx config for $DOMAIN..."
cat > /etc/nginx/sites-available/alongsi << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        client_max_body_size 10M;
    }

    # Static files (served directly by nginx for speed)
    location /static/ {
        alias /opt/alongsi/dashboard/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

ln -sf /etc/nginx/sites-available/alongsi /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "[2/3] Getting SSL certificate from Let's Encrypt..."
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$(echo $DOMAIN | cut -d'.' -f2-)

echo "[3/3] Setting up auto-renewal..."
systemctl enable certbot.timer
systemctl start certbot.timer

echo ""
echo "=============================="
echo "  Done! Your app is live at:"
echo "  https://$DOMAIN"
echo "=============================="
