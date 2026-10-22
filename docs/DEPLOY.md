# Deploying to your Ubuntu PC with GitHub Actions

Every merge to `main` is tested on GitHub's servers, then deployed to your PC by a **self-hosted
runner**: a small GitHub agent running on the PC. It connects out to GitHub, so you don't open any
ports, and it only runs this repo's deploy workflow (the repo is private, and pull requests never run on it).

```
merge to main ──> CI on GitHub (lint, 100+ tests, UI build)
                    └─ passes ──> runner on your PC: scripts/deploy.sh
                                    build + test a new release folder
                                    switch ~/darwin/current to it
                                    restart services (trader finishes its cycle first)
                                    health check, roll back if it fails
```

## Fastest: from a fresh Ubuntu install

In a terminal on the machine (it will ask for your sudo password):

```bash
sudo apt update && sudo apt install -y git gh     # gh = GitHub CLI (Ubuntu 24.04+; older: see cli.github.com)
gh auth login                                     # GitHub.com → HTTPS → log in with a browser
gh repo clone Adhocracy2/Darwin-Options ~/Darwin-Options
cd ~/Darwin-Options && scripts/setup-pc.sh
```

Then put your IBKR **paper** login into `~/darwin/shared/ib-gateway/.env` and start IB Gateway:

```bash
nano ~/darwin/shared/ib-gateway/.env
cd ~/darwin/shared/ib-gateway && sudo docker compose up -d
cd ~/darwin/current && .venv/bin/darwin ibkr-check
```

`setup-pc.sh` does steps 1–4 below, plus Docker and IB Gateway: installs prerequisites, registers the PC as a runner
labelled `darwin-pc` and runs it as a service, does the first deploy into `~/darwin`, and installs
the Darwin Options services. It asks for your sudo password, and for a runner token if the GitHub CLI isn't
logged in. Re-running it skips whatever is already done.

## One-time setup by hand (about 15 minutes)

Do all of this as the normal Linux user that will run Darwin Options (not root).

### 1. Install prerequisites

```bash
sudo apt update
sudo apt install -y git curl python3 python3-venv python3-pip
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

### 2. Register the PC as a runner

1. On GitHub: **repo → Settings → Actions → Runners → New self-hosted runner**, choose **Linux / x64**.
2. Run the commands GitHub shows (download, then `./config.sh --url ... --token ...`), in a folder
   such as `~/actions-runner`. When `config.sh` asks:
   - runner group: press Enter
   - name: e.g. `trading-pc`
   - **additional labels: `darwin-pc`** (the deploy workflow looks for this label)
   - work folder: press Enter
3. Install it as a service so it starts on boot, running as **your** user:
   ```bash
   cd ~/actions-runner
   sudo ./svc.sh install "$USER"
   sudo ./svc.sh start
   ```
   The runner should show as **Idle** on the Runners page.

### 3. First deploy

On GitHub: **Actions → Deploy to trading PC → Run workflow** (choose `main`), or merge a pull
request into `main`. The first run creates:

```
~/darwin/shared/config.yaml   your settings (seeded from config.example.yaml)
~/darwin/shared/.env          secrets, e.g. DARWIN_ALLOW_LIVE (chmod 600)
~/darwin/shared/var/          trade journal (darwin.db) and logs
~/darwin/releases/<time>-<sha>/
~/darwin/current -> the release in use
```

Deploys never overwrite anything in `shared/`.

### 4. Configure, then install the services (once)

```bash
nano ~/darwin/shared/config.yaml        # mode: paper, ibkr port, capital, pilot, ...
sudo ~/darwin/current/scripts/install-services.sh
```

This installs `darwin-trader` and `darwin-web` and adds a sudoers rule that lets your user restart
exactly those two services without a password, which the runner needs. From now on every
deploy restarts them automatically.

### 5. Check it

```bash
cd ~/darwin/current && .venv/bin/darwin ibkr-check     # IB Gateway must be running (see README)
systemctl status darwin-trader darwin-web
journalctl -u darwin-trader -f
```

Dashboard: http://127.0.0.1:8000 on the PC. From another machine:
`ssh -L 8000:127.0.0.1:8000 you@trading-pc`, then open http://127.0.0.1:8000.

## Day to day

- **Deploy:** merge a pull request into `main`. Watch it under **Actions**.
- **Redeploy or deploy a branch for testing:** Actions → Deploy to trading PC → Run workflow → pick the branch.
- **Approve deploys by hand** (optional): repo → Settings → Environments → `trading-pc` → add
  yourself as a required reviewer. Each deploy then waits for your click.
- **Roll back by hand:** point `current` at an earlier release and restart:
  ```bash
  ls ~/darwin/releases
  ln -sfn ~/darwin/releases/<older> ~/darwin/current
  sudo systemctl restart darwin-trader darwin-web
  ```
- **Which version is running:** `cat ~/darwin/current/REVISION`

## What a deploy does (scripts/deploy.sh)

1. Unpacks the commit into a new `releases/` folder.
2. Creates its own Python environment, builds the dashboard and **runs the full test suite** there.
3. Checks that your `config.yaml` loads with the new code.
4. Only if all of that passes: switches `current` to the new release in one step.
5. Restarts the services. The trader **finishes its current cycle before stopping**, so an order
   waiting for a fill is completed or cancelled, never abandoned.
6. Waits 15 seconds and checks both services. If one crashed, it switches back to the previous
   release, restarts, and the workflow fails so you see it.
7. Keeps the last 3 releases (`DARWIN_KEEP_RELEASES`).

If anything in steps 1–3 fails, nothing changes on the PC; the running version keeps running.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Deploy job stuck on "Waiting for a runner" | Runner offline: `sudo ~/actions-runner/svc.sh status`, check the `darwin-pc` label |
| `sudo: a password is required` during deploy | Run step 4 (`install-services.sh`) once |
| `config.yaml doesn't load with this release` | A setting changed name; compare with `~/darwin/current/config.example.yaml` |
| Services `activating` after deploy | Usually IB Gateway isn't running or logged in; `journalctl -u darwin-trader -n 50` |
