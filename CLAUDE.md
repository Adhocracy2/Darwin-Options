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
- IBKR account has Level 4 options. Since 2026-10-23 the paper account itself is **US$10,000 in USD** (reset by the
  owner) and `risk.capital: 10000`, pilot off. Risk follows the owner's **3-5-7 rule**: at most 3% of the account per
  trade, 5% at risk across all open trades, and (credit trades) the planned take-profit must be at least 7% of the
  trade's risk (`risk.min_target_return_on_risk`). The 2-per-ticker limit was removed. No new trades in the first 30 minutes after the open
  (`risk.no_entries_after_open_min`); exits still run. Live option quotes (OPRA)
  since 2026-10-23 via `market_data_type: 3` (live where subscribed); the SPY stock price is still delayed (no US
  equity subscription), so same-day trading stays blocked (`ibkr.live_stock`). SPX/SPXW is recorded, not traded
  (`record.extra_symbols`); IBKR gives no SPX history without an index subscription.
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
6. The dashboard has no login. It may listen beyond localhost (`DARWIN_WEB_HOST`) only in paper/sim
   mode; `cli.web_host` refuses in live mode. Keep it that way unless real authentication is added.
7. Never push secrets. `config.yaml`, `.env`, and `deploy/ib-gateway/.env` are git-ignored.

## Architecture (src/darwin_options)

- `strategies/`: five strategy *types* (credit_spread, iron_condor, iron_butterfly, calendar,
  leaps_call). A *strategy* = type + its own entry settings under an id; positions record the id,
  exit rules and review results are per id. The five built-ins use the type name as id and the
  config section of that name; more come from `custom_strategies` or the dashboard's "Add strategy".
  `strategies/__init__.py`: registry, `instances()`, `build()`, `type_of()`, `sync_exit_rules()`.
  **0DTE is not a strategy**: any spread/condor/butterfly strategy with days to expiry 0 to 0 is
  same-day and automatically gets `strategies/sameday.py` rules (entries 10:00–13:30 NY, no delayed
  quotes, no half-days, looser wing liquidity) plus `close_by_time` (default 15:30) in its exit rules,
  hours-based valuation in `common.price_candidate` (`clock.py`, `analysis.evaluate_intraday`), and
  is never rolled. Calendars and LEAPS calls can't be same-day. Same-day also has its own risk budget (Risk Desk:
  1% of equity per trade, 3 trades/day, 2 open, stop for the day at 2% same-day loss, `same_day.skip_dates`) and
  profit plan (manager: take-profit falling 50% -> 35% after 13:00 -> 25% after 14:30, profit lock 40% -> 15%,
  close anything losing at 14:30), all in `same_day` config. Untested live: needs OPRA quotes.
- `analysis.py`: expected value / probability of profit. Lognormal model with forecast vol =
  blend of IV and 30-day realized vol; trades valued at the **planned exit** (close-at-DTE), legs
  repriced at their IV, never below intrinsic (American options); subtracts commissions and
  closing bid/ask.
- `risk.py` (gate, never-naked) + `portfolio.py`. Defaults follow the owner's $10k rules: max 3% and
  min 1% (`min_risk_per_trade_pct`) of equity per trade, up to 10 positions, 5% total at risk, 30% cap on the correlated SPY/QQQ/IWM/DIA group; 2% probation until 30 closed
  trades, then half-Kelly capped at 5%; drawdown brakes 15%/30%, 6% daily stop; pilot mode optional.
- `manager.py`: exits; per-strategy overrides via `management.overrides`.
- `roller.py`: rolling credit spreads out in time (one 4-leg combo), only at the planned time exit
  (not when the strike is threatened: that's rolling a loser), at most once.
- `engine.py`: the cycle (reconcile → manage/roll → scan → gate → execute → snapshot); file lock
  so the trader and dashboard never trade at once; dashboard settings applied each cycle.
- `ibkr.py`, `data/ibkr.py`, `broker/ibkr.py`: ib_async on its own event-loop thread. Runs without IB Gateway: connect failures raise `GatewayUnavailable` (retry every 30s), cycles are skipped, the API returns 503 and `/api/status` reports `gateway`. Combos are
  BUY at a negative price for a credit. `broker/sim.py`: local simulator. `data/synthetic.py`:
  offline fake market (P&L meaningless).
- `settings.py`: dashboard-editable settings stored in the DB, laid over config.yaml each cycle:
  strategies (add/delete, on/off, entry settings, exit rules), tickers (`cfg.universe`), new-entries switch.
- `recorder.py`: every price from IBKR (spots, option quotes with greeks, realized vol, daily closes)
  is also saved to `var/marketdata.db` (`record:` in config). `ReplayMarketData` serves it back as
  of a time; `replay.py` / `darwin replay` reruns the engine on it with the sim broker and a fresh
  journal under `var/replays/`. `darwin data` shows totals. Replays: daily limits use the real clock,
  so pilot is off in replays.
- `history/`: importers for historical option data (plugins in `history.SOURCES`; first: `optionsdx`, which reads
  the .txt files or their .7z archives). `darwin import-history FILES --db var/history/X.db` writes the live
  recorder's format (+ an `imports` table so files aren't imported twice, + daily closes and realized vol);
  `darwin replay --data var/history/X.db` replays it one step per quote time (daily for EOD files). On AI1:
  `~/darwin/shared/var/history/spy_eod.db` = SPY end-of-day 2020-2023 from OptionsDX (owner downloaded).
  EOD caveat: exits are checked once a day, so intraday stops are missed (results optimistic on stops).
- `backtest.py`: the dashboard's Backtest tab. `BacktestRunner` runs `replay` in a background thread on a dataset
  (`var/history/*.db` or the live recordings) with the trader's current effective config, the simulator and its
  own journal in `var/backtests/<id>.db` (+ `<id>.json` results). One run at a time; `/api/status.backtest` shows
  progress (the 3D office banner + faster task relay). The journal clock (`Store.clock`) is set to history's time
  in replays, and the engine's daily rules use `Engine.now()`, so multi-year runs keep correct dates.
- `review.py`: trade review; suggests changes only with 20+ trades per group and ~2 standard errors.
- `payoff.py`: payoff diagrams; `position_summary` (opened for, cost to close now, exit levels) shown under an
  open position's Payoff in the dashboard, with `analysis.outlook`: a path simulation to the planned exit using
  the manager's rules (chance of profit, average result, odds of each exit), refreshed every minute. `ibkr_check.py`: `darwin ibkr-check` places an unfillable combo
  and cancels it to prove the order path.
- `supervisor.py`: the dashboard's Supervisor chat on Claude (`claude-opus-5-5`, SDK tool runner, server-side
  fallback). Read-only tools over the dashboard's own data (overview, positions, position detail, scan, events,
  risk, settings, saved prices); it can't trade or change settings. Needs `ANTHROPIC_API_KEY` in
  `~/darwin/shared/.env` (restart darwin-web after adding). No dashboard login: anyone on the LAN can use it
  and spend API credit.
- UI `ui/src/three/`: the desktop-only 3D office at `#/3d` (three.js, loaded only there): teams at desks around
  the engine, the Supervisor desk (opens chat), IB Gateway and IBKR with two-way data flows (yellow out, cyan
  in, red = down) from `/api/status` (`gateway.ibkr`/`farms` from IB Gateway's 1100/1102/210x notices in
  `ibkr.IBSession`, `activity` = last saved price / last order).
- `network.py`: the dashboard pings `network.internet_host` (google.com) every 30s; if that fails, the router
  (`router_host: auto` = AI1's default gateway, 192.168.1.1 as of 2026-10-23). Shown in the 3D office as
  router and ISP with plain green/red lines, and in `/api/status.network`.
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
pytest -q                          # ~130 tests, ~50s
cd ui && npx tsc -b && npm run build
```

Don't pipe pytest into `tail` and then chain `&&`: the exit code is lost. On AI1 there's no dev venv:
use `PYTHONPATH=src ~/darwin/current/.venv/bin/python -m pytest -q`. Without `PYTHONPATH=src` the
tests silently run against the deployed release, not your edits. Pushing to `main`
deploys to AI1 once its runner is online, so only push tested work there.

## Current state (2026-10-22, evening AEST)

- AI1 is set up: services run under systemd, deploys work, IB Gateway is logged in to paper
  account DUR295184 (AUD base currency). The trader ran its first market-hours cycles.
- No OPRA subscription yet: `ibkr.market_data_type: 3` (delayed) in shared config. Same-day
  strategies refuse to trade on delayed data.
- Shared config switched to $10k / pilot off / new rules on 2026-10-23 (backup next to it).
- First paper trade: SPY Nov 27 787/792 bear call spread (`credit_spread`), opened 2026-10-22.
- First contact found: bogus strike requests (fixed: `listed_options`) and log spam from repeated
  delayed-data notices (now logged once).
- **Never tested on real systemd:** service restart and deploy rollback (the build sandbox had no systemd).
- `Booger-Flick` cleanup: the owner was asked to delete branch `ccr-13b4f904-k46h0h` there by hand.

## Findings worth remembering

- **Don't flood IB Gateway with quote requests.** On 2026-10-22/23 every cycle requested thousands
  of option snapshots (5 chains × 3 tickers, both sides of the money); by midnight the gateway had
  ~56k orphaned market-data entries and stopped answering anything, and deploys hit systemd's
  5-minute stop timeout (trader SIGKILLed mid-cycle). Now: only out-of-the-money strikes (LEAPS
  chains excepted), chains reused for `ibkr.chain_cache_s` (600s; same-day never), and an
  unanswered request raises `GatewayNotAnswering` and skips the rest of the cycle.
- **Option quote snapshots take ~11s per batch on live data** (IBKR waits before saying the snapshot is done), which
  made cycles ~10 minutes long on 2026-10-23. `_tickers` now subscribes each batch of 50, takes prices as they
  arrive (up to 3 s), then cancels (`ibkr.quote_mode: stream`; `snapshot` is the fallback). Untested in market
  hours as of 2026-10-24 (weekend): check cycle times on Monday.
- IB Gateway needs a full manual login (password + phone) about weekly; its daily auto-restart can't do that.
- **Stock snapshots take ~11s on IBKR** (delayed data: it waits for fields that never arrive). `spot()` keeps one
  streaming subscription per ticker instead (restarted after a reconnect or 5 min of silence in market hours);
  daily closes are cached for an hour. The dashboard reads prices from `var/marketdata.db` first (saved by
  the trader each cycle; `record.dashboard_max_age_s`, any age when the market is closed) and asks IBKR only
  when they're stale; it shows how old the prices are.
- AI1 runs the **desktop IB Gateway** (~/Jts, logged in by hand, paper port 4002), not the Docker
  one in deploy/ib-gateway. Its auto-restart/log-off time and memory (-Xmx768m) matter.

- Simulations here use a synthetic market where no strategy is profitable; use it to compare
  settings, never to predict profit.
- $1-wide spreads lose to commissions (~$2.60 round trip vs ~$15–20 credit). With $10k and the
  2% probation cap ($200), $2–3-wide spreads are the working size until 30 trades are closed.
- With the threatened-strike exit, 0.25–0.30-delta shorts get closed at a loss ~40% of the time
  (touch probability ≈ 2× delta). Lowering short delta to ~0.10–0.16 cut losses by more than half
  in simulation.

## The plan

`docs/IMPROVEMENT_PLAN.md` (2026-10-23) is the agreed roadmap: foundations and a Decisions view, an honest
(path-aware, calibrated) model, historical end-of-day option data for backtests, regime/event/volatility
filters in shadow mode, then a champion/challenger self-improvement cycle. Work through it in order.

## Earlier suggested next steps (folded into the plan)

1. Make lower short delta (0.10–0.16) the default for credit spreads / iron condors.
2. Path-aware EV: account for profit-target, stop and threatened exits in the model (it currently
   overstates win rate; it predicted 67% vs 49% actual in simulation).
3. Only sell when options are rich (IV/realized-vol floor or IV rank).
4. Better fills: start at mid and step toward the market instead of one fixed limit.
5. Earnings/ex-dividend calendars, alerts, a historical backtester.
