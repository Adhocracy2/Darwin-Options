#!/usr/bin/env bash
# Install systemd units that run the trading loop and the dashboard as the current user.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
USER_NAME="${SUDO_USER:-$USER}"
for unit in flick-trader flick-web; do
  sed -e "s|@DIR@|$DIR|g" -e "s|@USER@|$USER_NAME|g" "deploy/$unit.service" \
    | sudo tee "/etc/systemd/system/$unit.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now flick-trader flick-web
systemctl --no-pager status flick-trader flick-web | head -20
echo "Dashboard: http://127.0.0.1:8000  (from another machine: ssh -L 8000:127.0.0.1:8000 <this-box>)"
