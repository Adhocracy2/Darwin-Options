#!/usr/bin/env bash
# One-time: install the systemd services and let the deploy user restart them.
#
#   sudo ~/darwin/current/scripts/install-services.sh [user]
#
# - darwin-trader: the trading loop     - darwin-web: dashboard on 127.0.0.1:8000
# - Both run as <user> (default: whoever ran sudo) from ~<user>/darwin/current.
# - Adds /etc/sudoers.d/darwin-deploy so that user (and the GitHub runner running as that user)
#   can restart exactly these two services without a password, and nothing else.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
USER_NAME="${1:-${SUDO_USER:?run with sudo from your normal account, or pass the user}}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
DARWIN_HOME="${DARWIN_HOME:-$HOME_DIR/darwin}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
[ -e "$DARWIN_HOME/current" ] || { echo "$DARWIN_HOME/current not found: run a deploy first"; exit 1; }

for unit in darwin-trader darwin-web; do
  sed -e "s|@DARWIN_HOME@|$DARWIN_HOME|g" -e "s|@USER@|$USER_NAME|g" "$SRC/deploy/$unit.service" \
    > "/etc/systemd/system/$unit.service"
done

SYSTEMCTL="$(command -v systemctl)"
RULE="/etc/sudoers.d/darwin-deploy"
cat > "$RULE.tmp" <<EOF
# Lets $USER_NAME (and its GitHub Actions runner) restart Darwin Options during deploys. Nothing else.
$USER_NAME ALL=(root) NOPASSWD: $SYSTEMCTL restart darwin-trader, $SYSTEMCTL restart darwin-web
EOF
visudo -cf "$RULE.tmp" >/dev/null && install -m 0440 "$RULE.tmp" "$RULE" && rm -f "$RULE.tmp"

systemctl daemon-reload
systemctl enable --now darwin-trader darwin-web
systemctl --no-pager --lines=0 status darwin-trader darwin-web || true
echo
echo "Dashboard: http://127.0.0.1:8000   (from another machine: ssh -L 8000:127.0.0.1:8000 $USER_NAME@<this-pc>)"
echo "Logs:      journalctl -u darwin-trader -f"
