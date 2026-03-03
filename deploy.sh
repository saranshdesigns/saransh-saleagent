#!/bin/bash
# ============================================================
# SaranshDesigns AI Agent — Deploy Script
# Usage: bash deploy.sh
# Run this when you say "deploy karo" to Claude.
# ============================================================

set -e

DROPLET_IP="165.232.178.128"
REMOTE_DIR="/opt/saransh-saleagent"

echo ""
echo "============================================"
echo " SaranshDesigns AI Agent — Deploying..."
echo "============================================"

# --- Step 1: Commit any local changes ---
echo ""
echo "[1/4] Committing changes..."
git add -A
if git diff --cached --quiet; then
    echo "      (nothing new to commit — already up to date)"
else
    git commit -m "deploy: $(date '+%Y-%m-%d %H:%M')"
    echo "      Committed."
fi

# --- Step 2: Push to GitHub ---
echo ""
echo "[2/4] Pushing to GitHub..."
git push origin main
echo "      Pushed."

# --- Step 3: SSH into server and pull + restart ---
echo ""
echo "[3/4] Deploying to server..."
ssh root@$DROPLET_IP "
    cd $REMOTE_DIR &&
    git pull origin main &&
    source venv/bin/activate &&
    pip install -r requirements.txt -q &&
    systemctl restart saransh-agent &&
    echo '      Server restarted.'
"

# --- Step 4: Done ---
echo ""
echo "[4/4] Verifying..."
ssh root@$DROPLET_IP "systemctl is-active saransh-agent && echo '      Agent is RUNNING ✅' || echo '      ⚠️  Agent may have issues — check: ssh root@$DROPLET_IP journalctl -u saransh-agent -n 30'"

echo ""
echo "============================================"
echo " Deploy complete! Live at:"
echo " http://$DROPLET_IP:8000/"
echo " http://$DROPLET_IP:8000/dashboard/"
echo "============================================"
echo ""
