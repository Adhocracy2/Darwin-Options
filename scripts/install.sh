#!/usr/bin/env bash
# Local development setup: Python venv, backend, React build, config files.
# For the trading PC, use GitHub Actions deploys instead: see docs/DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip curl
if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'

(cd ui && npm ci && npm run build)

[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || { cp .env.example .env; chmod 600 .env; }
mkdir -p var

.venv/bin/pytest -q
echo
echo "Done. Next:"
echo "  1. Start IB Gateway logged into your paper account (see deploy/ib-gateway)"
echo "  2. .venv/bin/flick check"
echo "  3. .venv/bin/flick scan"
echo "For the trading PC, deploy with GitHub Actions instead: docs/DEPLOY.md"
