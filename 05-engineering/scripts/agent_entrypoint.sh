#!/bin/bash
set -e

# Configure git identity (required for commits inside container)
git config --global user.email "agent@stock-engine.local"
git config --global user.name "Stock Agent"

# Wire GITHUB_TOKEN into the remote URL so git push works without a keychain
if [ -n "$GITHUB_TOKEN" ] && [ -n "$GITHUB_REPO" ]; then
    cd /workspace
    git remote set-url origin "https://${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" 2>/dev/null || true
    cd /workspace/05-engineering
fi

# Ensure we're on main before the agent starts iterating
cd /workspace
git checkout main 2>/dev/null || true
cd /workspace/05-engineering

echo "=== Stock Agent starting ==="
echo "Working dir: $(pwd)"
echo "Git branch:  $(git -C /workspace branch --show-current)"
echo "NTFY_TOPIC:  ${NTFY_TOPIC:-<not set>}"
if [ -z "$GITHUB_TOKEN" ]; then
    echo "GITHUB_TOKEN: not set — branches will be committed locally but not pushed"
fi
echo "==============================="

# If WHATSAPP_SETUP=1, run the bridge interactively for QR scanning only (no Claude)
if [ "${WHATSAPP_SETUP:-0}" = "1" ]; then
    echo "[wa] Setup mode — scan the QR code, then Ctrl+C when connected"
    exec node /app/whatsapp/index.js
fi

# Normal mode: start bridge in background before Claude
if [ -n "$WHATSAPP_TO" ]; then
    echo "[wa] Starting WhatsApp bridge..."
    node /app/whatsapp/index.js &
    WA_PID=$!
    sleep 3
    echo "[wa] Bridge running (PID $WA_PID)"
else
    echo "[wa] WHATSAPP_TO not set — WhatsApp notifications disabled"
fi

exec claude --dangerously-skip-permissions -p "$(cat AGENT_TASK.md)"
