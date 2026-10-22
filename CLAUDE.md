# DARWIN OPTIONS: context for Claude

This file carries over the decisions and state from the session that built this repo, so a new
session can continue without the original chat. Read it fully before changing anything.

## What this is

An options trading system for **Interactive Brokers (IBKR) only**: scans option chains, scores
trades, paper/live trades them through IB Gateway, manages exits, and shows everything in a
React dashboard. Python 3.10+ backend (`src/darwin_options`), React + Vite UI (`ui/`), SQLite
journal (`var/darwin.db`). CLI: `darwin`. The owner runs it on an Ubuntu machine called **AI1**.

It used to live in the unrelated `Booger-Flick` repo under the name "Flick"; it was moved here and
renamed. Don't reintroduce "flick" names.

## Owner and how to work with them

- Reads on a phone, often between other things: keep replies short, outcome first, plain
  language, no jargon without a gloss.
- Not a professional developer; explain options/trading concepts briefly when they matter.
- Wants honest numbers. Say clearly when something is untested, simulated, or a guess.
- IBKR account has Level 4 options. Plan: fund $2,000 (IBKR's margin minimum for spreads) but let
  the system trade only part of it (`risk.capital`, default 500) in **pilot mode** first.
- The "$100 → $1,000 → $10,000" growth goal was dropped at their request. Don't bring it back.

## Hard rules (never weaken these)

1. **Never naked.** Every short option must be covered by a long option of the same type that
   expires no earlier. Checked before every order, opens and closes alike
   (`risk.assert_never_naked`). Level 4 permission on the account doesn't change this.
2. **Defined, capped risk.** Every trade's max loss is known and within limits before it's placed.
3. **Never assigned.** Short legs are closed before expiry, when time value runs out in the money,
   or when the strike is threatened (`manager.py`).
4. **Live trading needs two switches:** `mode: live` and `DARWIN_ALLOW_LIVE=yes-i-understand-real-money`.
   Paper mode refuses to connect to a live IBKR account (paper ids start with "D").
5. Never roll a stop loss. Rolls only when paid for, positive EV, and passing the risk gate.
6. Never push secrets. `config.yaml`, `.env`, and `deploy/ib-gateway/.env` are git-ignored.

## Architecture (src/darwin_options)

- `strategies/`: credit_spread, iron_condor, iron_butterfly, calendar, leaps_call (poor man's
  covered call), zero_dte (same-day SPY spreads/condors; off by default; entries 10:00–13:30 NY,
  forced exit at `management.overrides.zero_dte.close_by_time` 15:30; refuses delayed quotes and
  half-days; valued by hours via `clock.py` + `analysis.evaluate_intraday`). Registry in `strategies/__init__.py`. Shared pricing in `strategies/common.py`.
- `analysis.py`: expected value / probability of profit. Lognormal model with forecast vol =
  blend of IV and 30-day realized vol; trades valued at the **planned exit** (close-at-DTE), legs
  repriced at their IV, never below intrinsic (American options); subtracts commissions and
  closing bid/ask.
- `risk.py` (gate, never-naked) + `portfolio.py` (stages by account size, half-Kelly from own
  results after 30 trades, 5% probation, drawdown brakes 15%/30%, 6% daily stop, correlated
  group cap for SPY/QQQ/IWM/DIA, trading capital, pilot mode).
- `manager.py`: exits; per-strategy overrides via `management.overrides`.
- `roller.py`: rolling credit spreads out in time (one 4-leg combo).
- `engine.py`: the cycle (reconcile → manage/roll → scan → gate → execute → snapshot); file lock
  so the trader and dashboard never trade at once; dashboard settings applied each cycle.
- `ibkr.py`, `data/ibkr.py`, `broker/ibkr.py`: ib_async on its own event-loop thread. Runs without IB Gateway: connect failures raise `GatewayUnavailable` (retry every 30s), cycles are skipped, the API returns 503 and `/api/status` reports `gateway`. Combos are
  BUY at a negative price for a credit. `broker/sim.py`: local simulator. `data/synthetic.py`:
  offline fake market (P&L meaningless).
- `settings.py`: dashboard-editable strategy on/off, exit rules and tickers (on/off, add new),
  stored in the DB and applied to `cfg.universe` each cycle.
- `recorder.py`: every price from IBKR (spots, option quotes with greeks, realized vol, daily closes)
  is also saved to `var/marketdata.db` (`record:` in config). `ReplayMarketData` serves it back as
  of a time; `replay.py` / `darwin replay` reruns the engine on it with the sim broker and a fresh
  journal under `var/replays/`. `darwin data` shows totals. Replays: daily limits use the real clock,
  so pilot is off in replays.
- `review.py`: trade review; suggests changes only with 20+ trades per group and ~2 standard errors.
- `payoff.py`: payoff diagrams. `ibkr_check.py`: `darwin ibkr-check` places an unfillable combo
  and cancels it to prove the order path.
- `api/app.py`: FastAPI for the UI. `cli.py`: `darwin check|scan|run|loop|serve|positions|report|review|ibkr-check|close|data|replay`.

## Deploy (CI/CD)

- `.github/workflows/ci.yml`: lint + tests + UI build on GitHub runners.
- `.github/workflows/deploy.yml`: on push to `main`, after CI, runs on the self-hosted runner
  labelled **`darwin-pc`** (AI1) → `scripts/deploy.sh`.
- `scripts/deploy.sh`: release folders under `~/darwin/releases`, each with its own venv and built
  UI; runs the full test suite and a config check before atomically switching `~/darwin/current`;
  restarts `darwin-trader` / `darwin-web`; rolls back if unhealthy. `~/darwin/shared/` holds
  config.yaml, .env, var/ (journal) and ib-gateway/; deploys never touch it.
- `scripts/setup-pc.sh`: one-shot machine setup from vanilla Ubuntu (prereqs, runner, first
  deploy, services, Docker + IB Gateway). Needs sudo, so the owner runs it in their own terminal.
- The trader stops gracefully on SIGTERM after its current cycle (an order may be mid-fill).
- Full guide: `docs/DEPLOY.md`.

## Checks before every push

```bash
python -m pyflakes src tests
pytest -q                          # ~123 tests, ~40s
cd ui && npx tsc -b && npm run build
```

Don't pipe pytest into `tail` and then chain `&&`: the exit code is lost. On AI1 there's no dev venv:
use `PYTHONPATH=src ~/darwin/current/.venv/bin/python -m pytest -q`. Without `PYTHONPATH=src` the
tests silently run against the deployed release, not your edits. Pushing to `main`
deploys to AI1 once its runner is online, so only push tested work there.

## Current state (2026-10-22, evening AEST)

- AI1 is set up: services run under systemd, deploys work, IB Gateway is logged in to paper
  account DUR295184 (AUD base currency). The trader ran its first market-hours cycles.
- No OPRA subscription yet: `ibkr.market_data_type: 3` (delayed) in shared config. 0DTE refuses
  to trade on delayed data.
- First contact found: bogus strike requests (fixed: `listed_options`) and log spam from repeated
  delayed-data notices (now logged once).
- Local commits after 8895544 (chain fix, 0DTE, tickers, recording) were not pushed at the time of
  writing; check `git status` / `git log origin/main..`.
- **Never tested on real systemd:** service restart and deploy rollback (the build sandbox had no systemd).
- `Booger-Flick` cleanup: the owner was asked to delete branch `ccr-13b4f904-k46h0h` there by hand.

## Findings worth remembering

- Simulations here use a synthetic market where no strategy is profitable; use it to compare
  settings, never to predict profit.
- $1-wide spreads lose to commissions (~$2.60 round trip vs ~$15–20 credit). The smallest
  sensible trade is one $5-wide spread (~$400–450 max loss); pilot cap defaults to $500.
- With the threatened-strike exit, 0.25–0.30-delta shorts get closed at a loss ~40% of the time
  (touch probability ≈ 2× delta). Lowering short delta to ~0.10–0.16 cut losses by more than half
  in simulation.

## Suggested next steps (owner hasn't chosen yet)

1. Make lower short delta (0.10–0.16) the default for credit spreads / iron condors.
2. Path-aware EV: account for profit-target, stop and threatened exits in the model (it currently
   overstates win rate; it predicted 67% vs 49% actual in simulation).
3. Only sell when options are rich (IV/realized-vol floor or IV rank).
4. Better fills: start at mid and step toward the market instead of one fixed limit.
5. Earnings/ex-dividend calendars, alerts, a historical backtester.
