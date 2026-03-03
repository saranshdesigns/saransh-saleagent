#!/bin/bash
# ============================================================
# SaranshDesigns AI Agent — ONE-TIME Server Setup
# Run this ONCE on your DigitalOcean droplet via SSH:
#   ssh root@165.232.178.128
#   bash server_setup.sh
# ============================================================

set -e

REPO_URL="https://github.com/saranshdesigns/saransh-saleagent.git"
APP_DIR="/opt/saransh-saleagent"

echo ""
echo "============================================"
echo " SaranshDesigns — Server Setup"
echo "============================================"

# --- 1. System packages ---
echo ""
echo "[1/6] Installing system packages..."
apt update -qq
apt install -y python3.11 python3.11-venv python3-pip git ufw
echo "      Done."

# --- 2. Clone repo ---
echo ""
echo "[2/6] Cloning repository..."
if [ -d "$APP_DIR" ]; then
    echo "      Directory exists. Pulling latest..."
    cd $APP_DIR && git pull origin main
else
    git clone $REPO_URL $APP_DIR
    cd $APP_DIR
fi
echo "      Done."

# --- 3. Python virtual environment ---
echo ""
echo "[3/6] Setting up Python virtual environment..."
cd $APP_DIR
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "      Done."

# --- 4. Create runtime directories ---
echo ""
echo "[4/6] Creating runtime directories..."
mkdir -p $APP_DIR/data/conversations
mkdir -p $APP_DIR/data/portfolio_cache
mkdir -p $APP_DIR/credentials
echo "      Done."

# --- 5. Firewall ---
echo ""
echo "[5/6] Configuring firewall..."
ufw allow ssh
ufw allow 8000
ufw --force enable
echo "      Port 8000 open. Done."

# --- 6. Systemd service ---
echo ""
echo "[6/6] Installing systemd service..."
cat > /etc/systemd/system/saransh-agent.service << 'EOF'
[Unit]
Description=SaranshDesigns AI Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/saransh-saleagent
ExecStart=/opt/saransh-saleagent/venv/bin/python main.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable saransh-agent
echo "      Service installed."

echo ""
echo "============================================"
echo " Setup complete!"
echo ""
echo " NEXT STEPS (required before starting):"
echo " 1. Upload your .env file:"
echo "    scp .env root@165.232.178.128:$APP_DIR/.env"
echo ""
echo " 2. Upload Google credentials:"
echo "    scp credentials/google_service_account.json root@165.232.178.128:$APP_DIR/credentials/"
echo ""
echo " 3. Then start the agent:"
echo "    systemctl start saransh-agent"
echo "    systemctl status saransh-agent"
echo "============================================"
echo ""
