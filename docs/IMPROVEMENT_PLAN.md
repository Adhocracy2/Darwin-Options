# Darwin Options: plan to test and improve the system over time

Written 2026-10-23 from the owner's discussion with Claude. Work through it in order; each phase
says what to build, how to know it worked, and what the owner needs to do.

## Principles (apply to every phase)

1. **Honest numbers.** Every figure says whether it's live, delayed, simulated or a model estimate.
2. **A calibrated model beats an optimistic one.** When the model says 70%, about 70% should win.
   If it must be wrong, it should be slightly pessimistic.
3. **The hard rules never change** (never naked, defined and capped risk, never assigned, two
   switches for live, never roll a stop loss). No experiment may touch them.
4. **One change at a time, proven on data it wasn't tuned on**, before it changes real decisions.
5. **Paper first.** Nothing changes live-money behaviour automatically.
6. **Show the decision, not just the data.** The owner must be able to see what the team looked
   at, the rule it applied and what it decided, and which metrics are used versus context only.

## Where we are (October 2026)

How the team decides today:
- The Scanner builds every idea for every enabled strategy on both sides of the market (bull puts
  and bear calls, condors, butterflies, calendars).
- Short strikes are chosen by **delta**. Delta measures distance (the market's odds of a strike
  finishing in the money), not direction: equal-delta strikes on both sides are direction-neutral.
- Ideas are scored by expected value (EV) and chance of profit from a lognormal model with **no
  drift** (up and down equally likely), volatility = 50/50 blend of each option's implied
  volatility and SPY's 30-day realized volatility, valued at the planned exit, after fees and
  bid/ask costs.
- The Risk Desk approves the best ideas within the $10k-account limits; exits follow fixed rules.

Known weaknesses:
- **The model is optimistic**: it predicted 67% winners vs 49% actual in simulation, because it
  ignores stops, threatened-strike exits and other early closes.
- **No market regime check**: the team never asks whether SPY is trending or channelling.
- **No event check** (Fed, CPI, jobs reports) and **no volatility filter** (IV Rank).
- **Accidental direction**: both open positions are bear call spreads, so the portfolio is short
  (net delta about -8) by chance, not by decision.
- **Little history**: one trading day of saved option prices; IBKR doesn't provide prices of
  expired options, so long backtests need bought data.
- **Delayed quotes** (no OPRA subscription): entry orders rarely fill; 0DTE is blocked.
- **Market metrics (IV Rank, RSI, MACD, put-call ratio, news) are display only**: no decision uses
  them yet.
- Review suggests changes only after 20+ closed trades per group; we have 0 closed.

## Phase 0: Foundations (now, 1-2 weeks)

Owner:
- [ ] Subscribe to OPRA live options data; then set `ibkr.market_data_type: 1` (Claude does this).
- [ ] Reset the paper account balance (USD switch is in progress) and confirm it reads in USD.
- [ ] Add `ANTHROPIC_API_KEY` to `~/darwin/shared/.env` so the Supervisor works.

Build:
- [ ] **Decisions view**, laid out in cycle order, with "needs attention" at the top:
  1. Market data in use and how old it is.
  2. Market regime (Phase 3; placeholder until then).
  3. Scanner: settings per strategy, ideas built, best ideas with credit, max loss, chance of
     profit, EV, score.
  4. Risk Desk funnel (ideas -> positive EV -> approved -> ordered -> filled) and block reasons
     counted; limits used (positions, $ at risk, per-trade cap, trial period, group cap, net delta).
  5. Trading: orders, limit vs mid, filled or not, slippage.
  6. Exits: per position, the exit rule closest to firing and how close it is; projected outcome.
  7. Review: predicted vs actual win rate (Phase 1).
  8. Account and system health.
  Every metric tagged **used in decisions** or **context only**.
- [ ] **Per-cycle decision record**: store each cycle's inputs, funnel, decisions with reasons and
  order results, so any past moment can be replayed and explained ("why didn't it trade at 10:40?").
- [ ] Keep recording every price received (already on) and back up `var/marketdata.db` weekly.

Done when: the owner can open one screen and see why the team did or didn't trade in the last cycle.

## Phase 1: Make the model honest (2-4 weeks)

- [ ] **Path-aware scoring**: score new trades with the price-path simulation already used for the
  Payoff panel's projected outcome (`analysis.outlook`), so EV counts take-profits, stops,
  threatened-strike exits, time exits, fees and slippage.
- [ ] **Calibration tracking in Review**: predicted chance of profit and EV vs actual results, by
  strategy and by prediction bucket (e.g. 60-70%), with a calibration score and an alert when the
  model drifts.
- [ ] **Better fills**: start entry orders at mid and step toward the market; record fill quality.
- [ ] Record the model's prediction on every trade (already partly in entry notes) so calibration
  can be measured later.

Done when: in simulation and replay, predicted win rates land within a few points of actual.

## Phase 2: Historical data and backtesting (in parallel, 2-6 weeks)

Owner:
- [ ] Choose a source of end-of-day SPY option history and pay if needed. Start cheap
  (OptionsDX), move to ORATS or ThetaData if results are promising; CBOE DataShop is the official
  but expensive option. Check current prices and terms.

Build:
- [ ] **Importer** that loads end-of-day chains (bid, ask, IV, Greeks, open interest, volume) into
  the same format as `var/marketdata.db`, so `darwin replay` runs on years of data, one step a day.
- [ ] **Conservative backtest assumptions**: fills cross part of the bid/ask spread; because exits
  are only checked once a day, intraday stops are missed, so treat stop results as optimistic.
- [ ] **Baseline report** for the current settings over 2008 (or 2018) to now, by year and by market
  regime: net P&L after fees, return on risk, max drawdown, win rate vs predicted, profit factor,
  average win vs average loss, share of trades closed by each exit rule.
- [ ] Check the backtest against live paper results for the same period once they overlap.

Limits: daily data can't test 0DTE (needs intraday data) and understates intraday stop-outs.

Done when: any settings change can be scored on years of real prices in minutes.

## Phase 3: Decision context, in shadow mode first (4-8 weeks)

Each of these is first computed and shown on the Decisions view **without changing trades**,
with a record of what the team would have done differently. It's switched on (with a setting to
turn it off) only if backtests and paper results show it helps.

- [ ] **Market regime**: uptrend / downtrend / channel from the 50- and 200-day averages and their
  slope, trend strength (ADX), and range behaviour. Used as a **veto**, not a forecast: e.g. no bear
  call spreads in a strong uptrend; condors and butterflies preferred in a channel.
- [ ] **Volatility regime**: IV Rank, IV Percentile and IV/HV; e.g. sell premium mainly when options
  are rich.
- [ ] **Event calendar**: Fed meetings, CPI, jobs reports (and earnings and ex-dividend dates for
  single stocks): stand aside or reduce size around them.
- [ ] **Portfolio balance**: keep net delta within a band; when the portfolio leans one way, prefer
  the side that rebalances it (e.g. add a bull put to two bear calls, making a condor).

Done when: each rule has a measured effect (better, worse or no difference) on history and paper.

## Phase 4: The self-improvement cycle (8+ weeks, then continuous)

The loop the owner asked for: constantly try strategies, change them, retest them, and keep what
works.

1. **Generate variants**, one change at a time and within set bounds: short delta (0.10-0.30),
   days to expiry, spread widths, take-profit / stop / time-exit levels, regime and volatility
   filters, which tickers.
2. **Test on history** with walk-forward testing: tune on one period, judge on a later one it
   never saw. Require enough trades and a clear margin (about 2 standard errors, as Review already
   uses) over the current settings on risk-adjusted return (return vs max drawdown), with the model
   still calibrated.
3. **Prove on paper as a challenger**: run the winner at small size beside the current strategy
   (the champion).
4. **Promote or retire**: after enough trades, a better challenger becomes the champion (owner
   approves with one click); otherwise it's dropped. Repeat.
5. **Experiment log** on the dashboard: every variant, the data it was tested on, results and
   decision. The Supervisor can explain any of them.

Guardrails: hard rules are immutable; settings move only within bounds; minimum trade counts;
out-of-sample proof; watch for overfitting (many variants tried means some look good by luck, so
raise the bar as the number tried grows); nothing changes live money automatically.

Done when: the system proposes, tests and (with approval) adopts improvements on its own, and the
experiment log shows steady, honest progress.

## Phase 5: Expand carefully (after Phase 4 is producing results)

- [ ] More, less-correlated tickers (QQQ, IWM; GLD, TLT, XLE) and index options (XSP/SPX).
- [ ] Raise the 2-per-ticker limit only if tests show it's safe.
- [ ] 0DTE strategies once live quotes and intraday history are available.
- [ ] **Live-trading readiness checklist**: enough paper trades, model calibrated, drawdown within
  limits, all systems monitored, a login on the dashboard, both live switches set by the owner.

## How we judge progress

Track monthly on the Decisions and Review views:
- Net P&L after fees and return on capital at risk.
- Max drawdown and worst day.
- Win rate vs predicted (calibration), profit factor, average win vs average loss.
- Share of trades closed by each exit rule (too many stops = strikes too close or wrong regime).
- Fill rate and slippage.
- Experiments run, promoted and retired.

## Owner actions, in one place

1. Subscribe to OPRA live options data.
2. Reset the paper account balance (and confirm USD).
3. Add the Claude API key for the Supervisor.
4. Choose and buy a historical options data source.
5. Approve promotions from the self-improvement cycle.
