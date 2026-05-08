#!/usr/bin/env bash
# Install the Copilot Usage collector as a systemd timer on Linux.
# Usage:
#   sudo INGEST_URL=... INGEST_KEY=... FUNCTION_KEY=... CLIENT_ID=openclaw \
#        ./install-linux-systemd.sh
set -euo pipefail

: "${INGEST_URL:?INGEST_URL must be set}"
: "${INGEST_KEY:?INGEST_KEY must be set}"
: "${FUNCTION_KEY:?FUNCTION_KEY must be set}"
: "${CLIENT_ID:=openclaw}"        # openclaw | hermes
: "${INTERVAL:=5min}"
: "${SERVICE_USER:=$(whoami)}"
: "${HOME_DIR:=/home/$SERVICE_USER}"

case "$CLIENT_ID" in
  openclaw) LOG_DIR="$HOME_DIR/.openclaw/agents/main/sessions" ;;
  hermes)   LOG_DIR="$HOME_DIR/.hermes/state.db" ;;
  *) echo "Unknown CLIENT_ID: $CLIENT_ID" >&2; exit 2 ;;
esac

INSTALL_DIR=/opt/copilot-usage-collector
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r "$(dirname "$0")/../collector/." "$INSTALL_DIR/"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# Install Python deps in a venv to avoid touching system python
if ! [ -d "$INSTALL_DIR/.venv" ]; then
  python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# Drop env file (mode 0600)
ENV_FILE=/etc/copilot-usage-collector.env
sudo tee "$ENV_FILE" >/dev/null <<EOF
COPILOT_USAGE_INGEST_URL=$INGEST_URL
COPILOT_USAGE_INGEST_KEY=$INGEST_KEY
COPILOT_USAGE_FUNCTION_KEY=$FUNCTION_KEY
EOF
sudo chmod 600 "$ENV_FILE"

UNIT_NAME=copilot-usage-collector
sudo tee "/etc/systemd/system/${UNIT_NAME}.service" >/dev/null <<EOF
[Unit]
Description=Copilot Usage Portal collector ($CLIENT_ID)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$SERVICE_USER
EnvironmentFile=$ENV_FILE
WorkingDirectory=$INSTALL_DIR
ExecStart=/bin/sh -c '\
  case "\${COPILOT_USAGE_INGEST_URL}" in \
    *\\?code=*) URL="\${COPILOT_USAGE_INGEST_URL}" ;; \
    *\\?*)      URL="\${COPILOT_USAGE_INGEST_URL}&code=\${COPILOT_USAGE_FUNCTION_KEY}" ;; \
    *)          URL="\${COPILOT_USAGE_INGEST_URL}?code=\${COPILOT_USAGE_FUNCTION_KEY}" ;; \
  esac; \
  exec $INSTALL_DIR/.venv/bin/python $INSTALL_DIR/collector.py \
    --client $CLIENT_ID \
    --source "$LOG_DIR" \
    --ingest-url "\$URL" \
    --ingest-key "\${COPILOT_USAGE_INGEST_KEY}" \
    --once'
EOF

sudo tee "/etc/systemd/system/${UNIT_NAME}.timer" >/dev/null <<EOF
[Unit]
Description=Run Copilot Usage collector every $INTERVAL

[Timer]
OnBootSec=2min
OnUnitActiveSec=$INTERVAL
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now "${UNIT_NAME}.timer"

echo
echo "==> Installed. Status:"
systemctl status "${UNIT_NAME}.timer" --no-pager | head -20
echo
echo "Logs:    journalctl -u ${UNIT_NAME}.service -e"
echo "Trigger: sudo systemctl start ${UNIT_NAME}.service"
