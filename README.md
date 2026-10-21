# Booger-Flick

An options trading system that scans, analyses, paper-trades and manages
defined-risk options trades through **Interactive Brokers (IBKR)**, with a React dashboard.

## Strategies

Every strategy is defined-risk: the most it can lose is known before the order goes in.

| Strategy | Legs | Max loss | Managed by |
|---|---|---|---|
| **Credit spread** (`credit_spread`) | Bull put or bear call vertical, 30–50 DTE, short at 0.15–0.30 delta | width − credit | 50% profit, 2× credit stop, close at 21 DTE |
| **Iron condor** (`iron_condor`) | Put credit spread + call credit spread, same expiry and width, shorts at 0.10–0.20 delta | width − credit (only one side can lose) | same as credit spread |
| **Iron butterfly** (`iron_butterfly`) | Short ATM put + call at one strike, long wings 5 or 10 wide | wing width − credit | 25% profit, 2× credit stop, close at 21 DTE |
| **Calendar** (`calendar`) | Short front month (25–40 DTE), long back month at the same strike, puts or calls | debit paid | 25% profit, stop at 50% of debit, close at 5 DTE |
| **LEAPS-backed calls** (`leaps_call`) | Long deep-ITM LEAPS call, short 21–45 DTE OTM calls written against it, repeatedly | net debit | short call: 50% profit / 21 DTE; LEAPS sold at 120 DTE or −40% |

Turn strategies on and off, and change their exit rules, in the dashboard's **Strategies** tab
(see below), or with `enabled` and `management.overrides` in `config.yaml`.

Not included, because they have naked short legs: short strangles and straddles, jade lizards,
naked puts, ratio spreads.

## Safety rules (enforced in code)

| Rule | Where |
|---|---|
| **Never naked.** Every short option must be covered by a long option of the same type that expires no earlier. This is checked before *every* order, opens and closes alike, and the order is refused otherwise. | `risk.py` (`assert_never_naked`), `engine.py` |
| **Capped loss.** Each trade's max loss must be finite and under `max_loss_per_trade` (dollars) and `max_loss_per_trade_pct` of equity. Total open max loss stays under `max_portfolio_risk_pct`. Position size is derived from these. | `risk.py` |
| **Never assigned.** Short legs are closed (crossing the spread if needed) when the strike is threatened, when extrinsic value drops below `min_short_extrinsic` while in the money, at `close_at_dte`, and in all cases before the last day. Iron butterflies and calendars start with at-the-money shorts, so for them only the extrinsic-value and DTE checks apply. | `manager.py` |
| **LEAPS calls can't lose on assignment.** The short strike must be above LEAPS strike + net cost. | `strategies/leaps_call.py` |
| **Closes buy back shorts first.** Combo orders list buys first. If an urgent combo close doesn't fill, legs are closed one at a time, shorts first. | `broker/ibkr.py` |
| **Live trading is opt-in twice.** It needs `mode: live` *and* `FLICK_ALLOW_LIVE=yes-i-understand-real-money` in `.env`. | `config.py` |
| **Paper means paper.** In paper or sim mode Flick refuses to connect if the gateway is logged into a live account (IBKR paper account ids start with "D"). | `ibkr.py` |
| **No new trades while the journal and IBKR disagree.** | `engine.py` (`reconcile`) |

## How it works

Each cycle (every 5 minutes while the market is open):

1. **Reconcile** the journal against IBKR's positions.
2. **Manage** open positions: profit target (50% of credit), stop (2× credit), DTE exit, assignment risk.
3. **Scan** the universe. Each strategy proposes candidates priced at mid minus expected slippage.
4. **Analyse.** Expected value and probability of profit come from a lognormal model of the price,
   using a volatility that blends implied and 30-day realized volatility. Premium selling only has an
   edge when IV is above the volatility that actually shows up; this blend is where that edge enters
   the model. Each trade is valued at its **planned exit** (the strategy's close window), not at
   expiry, with remaining options repriced at today's IV. Commissions and the bid/ask spread on both
   entry and exit are subtracted. Candidates are ranked by EV per dollar risked.
5. **Risk gate** and sizing, then **execute** as a single IBKR combo limit order. Unfilled orders are cancelled.
6. **Snapshot** equity, open risk and P&L for the dashboard.

```
src/flick/
  config.py            settings (config.yaml + .env)
  models.py            contracts, quotes, legs, candidates, positions
  pricing.py           Black-Scholes, greeks, IV, probabilities, realized vol
  analysis.py          fill estimates, EV/POP integration, liquidity filters
  strategies/          credit_spread, iron_condor, iron_butterfly, calendar, leaps_call
  risk.py              never-naked check, risk gate, sizing
  manager.py           exit and assignment-avoidance rules
  engine.py            the cycle
  ibkr.py              IB Gateway connection on its own event-loop thread
  data/                ibkr.py (market data), synthetic.py (offline/tests)
  broker/              ibkr.py (paper/live), sim.py (local simulator)
  store.py             SQLite journal
  report.py            win rate, expectancy, profit factor, drawdown
  api/app.py           FastAPI backend for the dashboard
  cli.py               `flick` command
ui/                    React + Vite dashboard
deploy/, scripts/      Ubuntu install, systemd units, IB Gateway (Docker)
```

## Setup on Ubuntu

### 1. Your IBKR account

- Options trading permission that allows spreads (on both the live and paper accounts).
- An **OPRA** options market data subscription. To test without one, set
  `ibkr.market_data_type: 3` for free delayed data. The paper account shares the live account's data subscriptions.

### 2. IB Gateway

Flick needs no API keys. It talks to a running, logged-in **IB Gateway** (or TWS) on the same machine.
The simplest headless setup is Docker with automatic login:

```bash
cd deploy/ib-gateway
cp .env.example .env && nano .env        # your IBKR *paper* username/password
sudo apt-get install -y docker.io docker-compose-v2
sudo docker compose up -d
```

Approve the login on the IBKR mobile app if asked. The paper API is then on `127.0.0.1:4002`.
Alternatively, install IB Gateway from ibkr.com and enable the API on port 4002 in its settings.

### 3. Flick

```bash
scripts/install.sh           # venv, deps, builds the UI, runs tests
nano config.yaml             # universe, risk limits, strategy settings
.venv/bin/flick check        # should print broker=ibkr_paper and your equity
.venv/bin/flick scan         # ranked candidates and why each passes/fails the risk gate
.venv/bin/flick run --force  # one cycle by hand
scripts/install-services.sh  # run trader + dashboard as systemd services
```

Logs: `journalctl -u flick-trader -f` and `var/flick.log`. Journal database: `var/flick.db`.

### IBKR notes

- IBKR prices a credit spread as a combo **BUY at a negative price** (a 1.10 credit is `BUY @ -1.10`).
  This is handled for you and tested.
- The trader uses `ibkr.client_id`, the dashboard uses `client_id + 1`, so both can connect at once.
- IB Gateway restarts once a day. Flick reconnects on the next cycle.
- Open interest isn't fetched from IBKR, so that filter is skipped. The bid/ask spread filter still applies.
- To keep request counts within IBKR's limits, chains are cut to the nearest `ibkr.max_expiries` expiries
  and to strikes with an estimated delta between 0.03 and 0.97.
- Commission is estimated at $0.65/contract until IBKR reports the actual figure.

## Dashboard

`flick serve` serves the built UI and API at <http://127.0.0.1:8000>. It binds to localhost because
it can place trades and has no login. Use an SSH tunnel (`ssh -L 8000:127.0.0.1:8000 box`) to view it remotely.

Tabs: Overview (equity, risk, P&L), Positions (with manual close), Scanner (candidates + risk gate
reasons), Strategies (on/off and exit rules), Performance (by strategy and exit reason), Journal
(every open, close, error and settings change).

### Payoff diagrams

Every strategy, scanner candidate and open position has a payoff diagram (the **Payoff** button
on Scanner and Positions rows; the Strategies tab shows one per strategy). Each diagram shows:

- **At expiry** (solid): P&L at the first leg's expiry. For calendars and LEAPS calls the later
  leg is valued with Black-Scholes at its current implied volatility.
- **At exit** (dashed): P&L at the strategy's planned exit date (its close-at-DTE), which is when
  the manager will close it if no other rule fires first.
- Profit and loss zones, break-evens (circles), strikes (ticks), today's price, and hover values.
- Max profit and loss are taken over a much wider price range than the plot, so a loss that only
  shows up far away is still reported. "Unlimited" means the profit keeps rising (a LEAPS with no
  call written against it).

Long legs valued before their expiry are never priced below intrinsic value (US options are
American), so a calendar's worst case is its debit. The same rule is used in the expected-value
model that ranks trades.

### Strategies tab

- **New entries** master switch: off means nothing new opens; open positions are still managed.
- Per strategy: an **on/off switch** and its **exit rules** (take profit, stop loss, close-at-DTE,
  minimum short time value, strike-threatened check, and the LEAPS rules for LEAPS calls).
- Switching a strategy off stops new trades only. Its open positions keep being managed until they close.
- Each value shows its `config.yaml` default and a Reset link. Out-of-range values can't be saved
  (for example, close-at-DTE can't go below 1, so shorts are never held to expiry).
- Settings are stored in the journal database and picked up by the trader at its next cycle, no restart.
  Every change is logged in the Journal.
- Never-naked, max-loss and sizing limits are not editable from the dashboard. Change those in `config.yaml`.

For UI development: `flick serve` in one terminal, `cd ui && npm run dev` in another.

## Try it offline

```bash
printf 'mode: sim\ndata: synthetic\n' > /tmp/sim.yaml
.venv/bin/flick -c /tmp/sim.yaml scan
.venv/bin/flick -c /tmp/sim.yaml serve
```

Synthetic data is for checking that everything runs. Its P&L means nothing.
`mode: sim` with `data: ibkr` simulates fills locally using real IBKR quotes.

## Commands

| Command | |
|---|---|
| `flick check` | test the IBKR connection |
| `flick scan` | scan + risk-check, no trading |
| `flick run [--force]` | one full cycle |
| `flick loop --interval 300` | run forever (what the service does) |
| `flick serve` | API + dashboard |
| `flick positions` / `flick report` | open positions / performance |
| `flick close <id>` | close a position now |

## Before trusting it

- **The IBKR code has only been tested against a simulated gateway.** Watch the first paper orders in
  TWS or the IBKR app and check the combo legs and prices.
- **Paper trade for months** and compare live paper results to what the scanner predicted.
- **Not built yet:** earnings and ex-dividend calendars (use ETFs until then), IV rank, a historical
  backtester, alerts, a daily loss limit, and rolling instead of closing.
- The model's probabilities are estimates. Defined risk caps the loss per trade, but it doesn't make a strategy profitable.
