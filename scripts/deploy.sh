#!/usr/bin/env bash
# Deploy one commit of Darwin Options to this machine, safely.
#
#   scripts/deploy.sh <source-checkout> [<commit-sha>]
#
# Layout (DARWIN_HOME, default ~/darwin):
#   shared/config.yaml, shared/.env, shared/var/   your settings, keys and journal (never touched)
#   releases/<time>-<sha>/                           one folder per deploy, own venv + built UI
#   current -> releases/...                          what the services run
#
# Steps: unpack -> install -> build UI -> run tests -> check config loads -> switch `current`
# atomically -> restart services (the trader finishes its cycle first) -> health check ->
# roll back to the previous release if anything after the switch fails.
set -euo pipefail

SRC="${1:?usage: deploy.sh <source-checkout> [sha]}"
SHA="${2:-$(git -C "$SRC" rev-parse HEAD)}"
DARWIN_HOME="${DARWIN_HOME:-$HOME/darwin}"
KEEP="${DARWIN_KEEP_RELEASES:-3}"
REL="$DARWIN_HOME/releases/$(date +%Y%m%d-%H%M%S)-${SHA:0:12}"
say() { printf '\n==> %s\n' "$*"; }

# ---- prerequisites ---------------------------------------------------------------
command -v python3 >/dev/null || { echo "python3 missing: sudo apt install python3 python3-venv"; exit 1; }
python3 -c "import venv, ensurepip" 2>/dev/null || { echo "venv missing: sudo apt install python3-venv"; exit 1; }
command -v npm >/dev/null || { echo "Node.js missing: see docs/DEPLOY.md"; exit 1; }
[ "$(node -v | cut -d. -f1 | tr -d v)" -ge 18 ] || { echo "Node.js 18+ needed (have $(node -v))"; exit 1; }

# ---- unpack ----------------------------------------------------------------------
say "Deploying ${SHA:0:12} to $REL"
mkdir -p "$DARWIN_HOME/releases" "$DARWIN_HOME/shared/var" "$REL"
git -C "$SRC" archive "$SHA" | tar -x -C "$REL"
echo "$SHA" > "$REL/REVISION"

# First deploy: seed settings from the examples. Never overwrite existing ones.
[ -f "$DARWIN_HOME/shared/config.yaml" ] || cp "$REL/config.example.yaml" "$DARWIN_HOME/shared/config.yaml"
if [ ! -f "$DARWIN_HOME/shared/.env" ]; then cp "$REL/.env.example" "$DARWIN_HOME/shared/.env"; chmod 600 "$DARWIN_HOME/shared/.env"; fi
ln -sfn "$DARWIN_HOME/shared/config.yaml" "$REL/config.yaml"
ln -sfn "$DARWIN_HOME/shared/.env" "$REL/.env"
ln -sfn "$DARWIN_HOME/shared/var" "$REL/var"

fail() { echo "DEPLOY FAILED: $*"; echo "Nothing was switched; the running release is unchanged."; rm -rf "$REL"; exit 1; }

# ---- build and test (the running system is untouched until this all passes) ---------
say "Installing Python packages"
python3 -m venv "$REL/.venv" || fail "venv"
"$REL/.venv/bin/pip" install -q --upgrade pip || fail "pip upgrade"
"$REL/.venv/bin/pip" install -q -e "$REL[dev]" || fail "pip install"

say "Building the dashboard"
(cd "$REL/ui" && npm ci --no-audit --no-fund --loglevel=error && npm run build >/dev/null) || fail "UI build"
rm -rf "$REL/ui/node_modules"  # only the built files are needed at run time

say "Running tests"
(cd "$REL" && .venv/bin/pytest -q -p no:cacheprovider) || fail "tests"

say "Checking your config loads with this release"
(cd "$REL" && .venv/bin/python -c "from darwin_options.config import load_config; c = load_config(); print('mode', c.mode, '| data', c.data)") \
  || fail "config.yaml doesn't load with this release; fix $DARWIN_HOME/shared/config.yaml"

# ---- switch ------------------------------------------------------------------------
PREV="$(readlink -f "$DARWIN_HOME/current" 2>/dev/null || true)"
switch_to() { ln -sfn "$1" "$DARWIN_HOME/current.new" && mv -T "$DARWIN_HOME/current.new" "$DARWIN_HOME/current"; }
switch_to "$REL"
say "current -> $(basename "$REL")"

services_installed() { systemctl list-unit-files darwin-trader.service darwin-web.service 2>/dev/null | grep -q darwin-; }
restart() { sudo -n systemctl restart darwin-trader && sudo -n systemctl restart darwin-web; }
healthy() {
  # "failed" means it crashed outright. "activating" (auto-restart) is allowed so a deploy while
  # IB Gateway is down (e.g. its nightly restart) isn't rolled back by mistake.
  sleep 15
  for s in darwin-trader darwin-web; do
    st="$(systemctl is-active "$s" || true)"
    [ "$st" = "active" ] || [ "$st" = "activating" ] || { echo "$s is $st"; return 1; }
  done
}

if services_installed; then
  say "Restarting services (the trader finishes its current cycle first)"
  if ! restart || ! healthy; then
    echo "Services unhealthy after deploy; rolling back."
    journalctl -u darwin-trader -u darwin-web -n 30 --no-pager || true
    if [ -n "$PREV" ] && [ -d "$PREV" ]; then
      switch_to "$PREV"; restart || true
      echo "Rolled back to $(basename "$PREV")."
    fi
    exit 1
  fi
  say "Services running"
else
  cat <<EOF

First deploy finished. One-time setup still needed:
  1. Edit $DARWIN_HOME/shared/config.yaml (and .env if going live)
  2. sudo $DARWIN_HOME/current/scripts/install-services.sh
After that, every deploy restarts the services automatically.
EOF
fi

# ---- tidy ------------------------------------------------------------------------
cur="$(readlink -f "$DARWIN_HOME/current")"
ls -1dt "$DARWIN_HOME"/releases/*/ 2>/dev/null | sed 's:/$::' | tail -n +"$((KEEP + 1))" | while read -r old; do
  [ "$old" = "$cur" ] || [ "$old" = "$PREV" ] || rm -rf "$old"
done
say "Deployed ${SHA:0:12}"
