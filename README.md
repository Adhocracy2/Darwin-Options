# Booger-Flick

An options trading system that scans, analyses, paper-trades and manages
defined-risk options trades through **Alpaca** or **Interactive Brokers**, with a React dashboard.

It starts with two strategies:

- **Credit spreads.** Bull put and bear call verticals, 30–50 DTE, short leg at 0.15–0.30 delta.
- **LEAPS-backed calls** (poor man's covered call). Buy a deep-ITM LEAPS call and sell
  near-term OTM calls against it. When a short call is closed, a new one is written against the same LEAPS.

## Safety rules (enforced in code)

| Rule | Where |
|---|---|
| **Never naked.** Every short option must be covered by a long option of the same type that expires no earlier. This is checked before *every* order, opens and closes alike, and the order is refused otherwise. | `risk.py` (`assert_never_naked`), `engine.py` |
| **Capped loss.** Each trade's max loss must be finite and under `max_loss_per_trade` (dollars) and `max_loss_per_trade_pct` of equity. Total open max loss stays under `max_portfolio_risk_pct`. Position size is derived from these. | `risk.py` |
| **Never assigned.** Short legs are closed (crossing the spread if needed) when the strike is threatened, when extrinsic value drops below `min_short_extrinsic` while in the money, at `close_at_dte`, and in all cases before the last day. | `manager.py` |
| **LEAPS calls can't lose on assignment.** The short strike must be above LEAPS strike + net cost. | `strategies/leaps_call.py` |
| **Closes buy back shorts first.** Multi-leg orders list buys first. If Alpaca rejects a combo close, legs are closed one at a time, shorts first. | `broker/alpaca.py` |
| **Live trading is opt-in twice.** It needs `mode: live` *and* `FLICK_ALLOW_LIVE=yes-i-understand-real-money` in `.env`. | `config.py` |
| **Paper means paper on IBKR too.** In paper mode Flick refuses to connect if the gateway is logged into a live account (IBKR paper account ids start with "D"). | `ibkr.py` |
| **No new trades while the journal and broker disagree.** | `engine.py` (`reconcile`) |

## How it works

Each cycle (every 5 minutes while the market is open):

1. **Reconcile** the journal against Alpaca's positions.
2. **Manage** open positions: profit target (50% of credit), stop (2× credit), DTE exit, assignment risk.
3. **Scan** the universe. Each strategy proposes candidates priced at mid minus expected slippage.
4. **Analyse.** Expected value and probability of profit come from a lognormal model whose volatility
   blends implied and 30-day realized volatility. Premium selling only has an edge when IV is above the
   volatility that actually shows up; this blend is where that edge enters the model. Candidates are ranked by EV per dollar risked.
5. **Risk gate** and sizing, then **execute** as a single multi-leg limit order (an Alpaca `mleg` order or an
   IBKR combo). Unfilled orders are cancelled.
6. **Snapshot** equity, open risk and P&L for the dashboard.

```
src/flick/
  config.py            settings (config.yaml + .env)
  models.py            contracts, quotes, legs, candidates, positions
  pricing.py           Black-Scholes, greeks, IV, probabilities, realized vol
  analysis.py          fill estimates, EV/POP integration, liquidity filters
  strategies/          credit_spread.py, leaps_call.py
  risk.py              never-naked check, risk gate, sizing
  manager.py           exit and assignment-avoidance rules
  engine.py            the cycle
  data/                alpaca.py, ibkr.py, synthetic.py (offline/tests)
  broker/              alpaca.py, ibkr.py (paper/live), sim.py (local simulator)
  ibkr.py              IB Gateway connection on its own event-loop thread
  store.py             SQLite journal
  report.py            win rate, expectancy, profit factor, drawdown
  api/app.py           FastAPI backend for the dashboard
  cli.py               `flick` command
ui/                    React + Vite dashboard
deploy/, scripts/      Ubuntu install + systemd units
```

## Setup on Ubuntu

```bash
git clone <this repo> && cd Booger-Flick
scripts/install.sh           # venv, deps, builds the UI, runs tests
nano .env                    # Alpaca *paper* API keys
nano config.yaml             # universe, risk limits, strategy settings
.venv/bin/flick check        # connectivity
.venv/bin/flick scan         # see ranked candidates and why each passes/fails the risk gate
.venv/bin/flick run --force  # one cycle by hand
scripts/install-services.sh  # run trader + dashboard as systemd services
```

Your Alpaca account needs **options level 3** for spreads. Paper accounts can request it in the dashboard.

### Using Interactive Brokers instead

Set `broker: ibkr` in `config.yaml`. IBKR needs no API keys; Flick talks to a running, logged-in
**IB Gateway** (or TWS) on the same machine.

1. **Account permissions:** options trading level that allows spreads, and an **OPRA** options market
   data subscription (or set `ibkr.market_data_type: 3` for free delayed data while testing).
   Do this on your *paper* account too; it shares market data with your live account.
2. **Run IB Gateway on Ubuntu.** The simplest headless route is Docker with automatic login:
   ```bash
   cd deploy/ib-gateway
   cp .env.example .env && nano .env        # your IBKR *paper* username/password
   sudo apt-get install -y docker.io docker-compose-v2
   sudo docker compose up -d
   ```
   Approve the login on the IBKR mobile app if asked. Paper API is then on `127.0.0.1:4002`.
   Alternatively install IB Gateway from ibkr.com and enable the API on port 4002 in its settings.
3. `.venv/bin/flick check` should print `broker=ibkr_paper` and your account equity.

Notes:
- IBKR prices a credit spread as a combo **BUY at a negative price** (a 1.10 credit is `BUY @ -1.10`).
  This is handled for you and tested.
- The trader uses `ibkr.client_id`, the dashboard uses `client_id + 1`, so both can connect at once.
- IB Gateway restarts once a day. Flick reconnects on the next cycle.
- Open interest isn't fetched from IBKR, so that filter is skipped there. The bid/ask spread filter still applies.
- To keep request counts within IBKR's limits, chains are cut to the nearest `ibkr.max_expiries` expiries
  and to strikes with an estimated delta between 0.03 and 0.97.

Logs: `journalctl -u flick-trader -f` and `var/flick.log`. Journal database: `var/flick.db`.

### Dashboard

`flick serve` serves the built UI and API at <http://127.0.0.1:8000>. It binds to localhost because
it can place trades and has no login. Use an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 box`) to view it remotely.

Tabs: Overview (equity, risk, P&L), Positions (with manual close), Scanner (candidates + risk gate
reasons), Performance (by strategy and exit reason), Journal (every open, close, error).

For UI development: `flick serve` in one terminal, `cd ui && npm run dev` in another.

### Try it offline

```bash
printf 'mode: sim\ndata: synthetic\n' > /tmp/sim.yaml
.venv/bin/flick -c /tmp/sim.yaml scan
.venv/bin/flick -c /tmp/sim.yaml serve
```

Synthetic data is for checking that everything runs. Its P&L means nothing.

## Commands

| Command | |
|---|---|
| `flick check` | test broker and data connections |
| `flick scan` | scan + risk-check, no trading |
| `flick run [--force]` | one full cycle |
| `flick loop --interval 300` | run forever (what the service does) |
| `flick serve` | API + dashboard |
| `flick positions` / `flick report` | open positions / performance |
| `flick close <id>` | close a position now |

## Before trusting it

- **Check the multi-leg price sign on paper (Alpaca).** Alpaca treats a negative `limit_price` on a
  multi-leg order as a credit; `execution.mleg_credit_sign` controls this. Confirm one paper fill
  before going live.
- **IBKR has only been tested against a simulated gateway.** Watch the first paper orders in TWS or
  the IBKR app and check the combo legs and prices.
- **Paper trade for months** and compare live paper results to what the scanner predicted.
- **Not built yet:** earnings and ex-dividend calendars (use ETFs until then), IV rank, a historical
  backtester, alerts, a daily loss limit, and rolling instead of closing.
- The model's probabilities are estimates. Defined risk caps the loss per trade, but it doesn't make a strategy profitable.
