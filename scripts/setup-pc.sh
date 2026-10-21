#!/usr/bin/env bash
# One-shot setup of the trading PC for CI/CD. Safe to re-run: finished steps are skipped.
#
#   git clone https://github.com/Adhocracy2/Booger-Flick.git && cd Booger-Flick
#   scripts/setup-pc.sh
#
# Run as your normal user (not root); it uses sudo where needed. It:
#   1. installs Python, venv and Node.js 22 if missing
#   2. registers this PC as a GitHub Actions self-hosted runner (label: flick-pc) and runs it
#      as a service under your user
#   3. does a first deploy from this clone into ~/flick (build, test, switch)
#   4. installs the flick-trader and flick-web services and the narrow sudoers rule deploys need
#
# The runner registration token comes from the GitHub CLI if it's logged in (`gh auth login`),
# otherwise paste it from: repo → Settings → Actions → Runners → New self-hosted runner.
set -euo pipefail
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root."; exit 1; }
cd "$(dirname "$0")/.."
SRC="$(pwd)"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
FLICK_HOME="${FLICK_HOME:-$HOME/flick}"
say() { printf '\n==> %s\n' "$*"; }

remote="$(git remote get-url origin)"
REPO="$(sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##' <<<"$remote")"
[[ "$REPO" == */* ]] || { echo "Can't work out the GitHub repo from origin: $remote"; exit 1; }

# ---- 1. prerequisites ---------------------------------------------------------------
say "Prerequisites"
need_apt=()
for p in git curl jq python3 python3-venv python3-pip; do dpkg -s "$p" >/dev/null 2>&1 || need_apt+=("$p"); done
if [ ${#need_apt[@]} -gt 0 ]; then sudo apt-get update -qq && sudo apt-get install -y -qq "${need_apt[@]}"; fi
if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y -qq nodejs
fi
echo "python $(python3 --version | cut -d' ' -f2), node $(node -v)"

# ---- 2. GitHub Actions runner ---------------------------------------------------------
say "GitHub Actions runner for $REPO"
if [ -f "$RUNNER_DIR/.runner" ]; then
  echo "Already registered ($RUNNER_DIR)."
else
  case "$(uname -m)" in x86_64) arch=x64 ;; aarch64|arm64) arch=arm64 ;; *) echo "Unsupported CPU $(uname -m)"; exit 1 ;; esac
  ver="$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | tr -d v)"
  mkdir -p "$RUNNER_DIR"
  curl -fsSL "https://github.com/actions/runner/releases/download/v${ver}/actions-runner-linux-${arch}-${ver}.tar.gz" \
    | tar -xz -C "$RUNNER_DIR"
  sudo "$RUNNER_DIR/bin/installdependencies.sh" >/dev/null
  token=""
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    token="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)"
  fi
  if [ -z "$token" ]; then
    echo "Open https://github.com/$REPO/settings/actions/runners/new and copy the token from the"
    echo "'./config.sh --url ... --token XXXX' line (it's valid for one hour)."
    read -r -p "Runner token: " token
  fi
  (cd "$RUNNER_DIR" && ./config.sh --unattended --replace --url "https://github.com/$REPO" --token "$token" \
     --name "$(hostname)-flick" --labels flick-pc --work _work)
  (cd "$RUNNER_DIR" && sudo ./svc.sh install "$USER" && sudo ./svc.sh start)
fi
(cd "$RUNNER_DIR" && sudo ./svc.sh status | grep -E "Active:" || true)

# ---- 3. first deploy --------------------------------------------------------------------
say "First deploy into $FLICK_HOME"
if [ -e "$FLICK_HOME/current" ]; then
  echo "Already deployed: $(cat "$FLICK_HOME/current/REVISION" 2>/dev/null | cut -c1-12). Later deploys come from GitHub."
else
  FLICK_HOME="$FLICK_HOME" scripts/deploy.sh "$SRC"
fi

# ---- 4. services ----------------------------------------------------------------------
say "Services"
if systemctl list-unit-files flick-trader.service 2>/dev/null | grep -q flick-trader; then
  echo "Already installed."
else
  echo "Settings are in $FLICK_HOME/shared/config.yaml (defaults: paper mode, \$500 trading capital, pilot on)."
  sudo FLICK_HOME="$FLICK_HOME" "$FLICK_HOME/current/scripts/install-services.sh" "$USER"
fi

cat <<EOF

Setup complete.
  Runner:     shows as Idle at https://github.com/$REPO/settings/actions/runners
  Deployed:   $(cat "$FLICK_HOME/current/REVISION" | cut -c1-12) in $FLICK_HOME/current
  Dashboard:  http://127.0.0.1:8000
  Logs:       journalctl -u flick-trader -f

Next:
  - Start IB Gateway logged into your PAPER account (see deploy/ib-gateway), then:
      cd $FLICK_HOME/current && .venv/bin/flick ibkr-check
  - Merge to main on GitHub: CI runs, then this PC deploys it automatically.
EOF
