# Teams as a library of operations

Plan, 2026-10-24. Goal: make Darwin a template for future trading systems (gold, FX, equities). You should be
able to create teams, give each one its operations, rules, limits, tasks and reports, measure how each
operation performs, and tweak it to test improvements, all without a code release. Strategies follow the same pattern.

## The idea in one paragraph

Every piece of work a team does becomes an **operation**: a small, self-contained building block in its own
file, with a fixed shape (what it reads, what settings it takes, what it decides, what it reports). The engine
doesn't know about specific rules any more; it runs whatever operations the teams are given. Which operations
a team has, and their settings, are **definitions** stored in the database and edited from the dashboard. Code
holds the library; data decides how it's used. Strategies already work this way (a strategy = type + settings).

## Three layers

| Layer | What it is | How it changes |
|---|---|---|
| **Core** | Cycle runner, journal, scheduler, broker and data connections, dashboard, 3D office, activity log, and the **hard rules** (never naked, capped risk, never assigned, two switches for live) | Code + release, rarely |
| **Library** | Operations and strategy types, one file each, each registering itself | Code + release, but small and isolated: one new file, its own tests |
| **Definitions** | Teams, departments, which operations each team runs, settings, schedules, reports, strategies | Live from the dashboard: checked, versioned, logged, no release |

Hard rules stay in code on purpose so no setting can ever weaken them. In the dashboard they show as **locked**
operations: visible, measured, not removable.

## What an operation looks like

Each operation declares:

- **id and kind**: e.g. `risk.max_risk_per_trade`, kind `gate` (approve/reject an idea). Other kinds: `fetch`
  (get data), `build` (make trade ideas), `exit` (decide to close), `act` (place/cancel orders), `record`,
  `report`, `check` (health).
- **settings**: names, types, allowed ranges and defaults (e.g. `pct: 0.5–5%, default 3%`). The dashboard builds
  its form from this and refuses values outside the range.
- **inputs / outputs**: what it needs from the cycle (positions, quotes, account) and what it produces.
- **metrics**: what to count each time it runs (e.g. ideas rejected, P&L of trades it closed).
- **run(context, settings) → result**: the logic, plus a plain-English reason for anything it decides.

## Interface protocol: how teams talk to the Brain

Today the engine calls each part's code directly, so the "protocol" is implicit and differs per team. It becomes
explicit and the same for everyone:

**The Brain owns the cycle; teams never call each other.** Every exchange goes through the Brain:

1. **Task** (Brain → team): `{task_id, team, operation, cycle, step, deadline, settings_version}`.
2. **Context** (read-only view of the cycle board): account, positions, prices, ideas so far, today's limits,
   clock. A team sees only what its operations declare in `inputs`; it can't change the board directly.
3. **Result** (team → Brain): `{task_id, status: ok | warn | failed | timeout, decision, reason, outputs, metrics,
   duration_ms, error}`.
4. **Report**: the Brain logs the result (activity log + operation run log), applies `outputs` to the board for the
   next step, and passes `metrics` to Review.

**Each kind has a fixed decision/output shape**, so the Brain handles every team of a kind the same way:

| Kind | Decision / outputs |
|---|---|
| fetch | data + freshness (`as_of`, source: live / saved / delayed) |
| build | trade ideas (legs, price, max loss, EV, probability, reason) |
| gate | `approve` / `reject` + reason, per idea |
| exit | `close` / `hold` (+ which legs, urgency) + reason, per position |
| act | `placed` / `filled` / `cancelled` / `rejected` + order details |
| record | rows saved, where |
| check | `ok` / `warn` / `problem` + what's wrong |
| report | summary text + figures |

**Failure rules, the same for everyone:** an error or timeout never counts as permission. A failed gate is a
reject, a failed exit check means "hold and alert" (the locked never-assigned rule still runs), a failed fetch
means "no new trades this cycle". Each failure is logged and shown on the team.

**Built in code as** a few typed definitions (Task, Context, Result, one decision type per kind) plus each
operation's settings schema. The protocol carries a version number so older operations keep working when it grows.

**What it gives:** teams and operations are swappable (a Gold scanner plugs in without touching the Brain);
everything is logged, timed and measured the same way, which makes per-operation results and shadow testing
possible; and each task animated in the 3D office is exactly one protocol message.

## What today's code becomes

Most of it is already rule-shaped, so this is mainly re-wrapping, not rewriting:

| Team | Operations (from today's code) |
|---|---|
| Market Data | fetch spot, fetch chains, fetch option quotes (`data/ibkr.py`), record prices (`recorder.py`) |
| Scanner | one `build` operation per strategy type (`strategies/`), score with EV / probability (`analysis.py`) |
| Risk Desk | each check in `RiskGate.check` becomes its own gate: positive EV, min score, 7% target rule, max positions, max per ticker, risk per trade (min and max), portfolio risk 5%, correlated group cap, drawdown brakes, daily stop, same-day budget, pilot limits, **never naked (locked)** |
| Trade Desk | place combo order, work the fill, cancel stale orders (`broker/ibkr.py`, `engine.py`) |
| Exits & Rolls | each rule in `manager.py`: take profit, stop loss, strike guard, time exit (close at DTE), same-day profit plan, LEAPS rules, the one allowed roll (`roller.py`), **close before assignment (locked)** |
| Portfolio | reconcile with IBKR, snapshot equity |
| Review | scorecard, coach (`review.py`) |
| Technology | health checks: gateway, IBKR link, internet, data farms, IBC reconnect |

## Measuring and tweaking

- **Every operation run is logged** (team, operation, settings version, decision, reason, time taken). This extends
  today's activity log.
- **Per-operation results**: for gates, how many ideas each rule rejected and what those trades *would* have done
  (followed in the background); for exits, the P&L of the trades each rule closed and what holding longer would have
  given; for fetches, time taken and failures.
- **Shadow mode**: run a changed version of an operation alongside the real one. It decides but doesn't act, and the
  two are compared. This is the safe way to test a tweak live. Backtests do the same on history.
- **Review** reads these results, and later the champion/challenger cycle in `IMPROVEMENT_PLAN.md` proposes winners.

## Editing (dashboard)

The Teams panel grows a per-team **Operations** list: add from the library, remove, reorder, enable/disable,
edit settings (form built from each operation's settings), switch to shadow. Each save makes a new version with
who/when/what changed; any version can be restored. A change can be backtested before it's applied. Hard rules
show as locked. Strategies get the same editor (they already have most of it in Settings).

## Safety

- Hard rules in code and locked; settings can't relax them.
- Every setting checked against its operation's allowed range before it's saved.
- Versioned, logged, reversible. Changes apply at the start of the next cycle, never mid-cycle.
- New *code* (a new operation) still goes through tests and the normal deploy. Only definitions change live.
- Live mode: definition changes need confirming (paper can apply straight away).

## A future system (e.g. Gold)

The core stays shared. A gold system adds an **asset pack**: its instruments (futures contracts and rolls), data
and pricing, its own hard rules, and any operations it needs that don't exist yet. Then it's mostly definitions:
teams, operations and settings picked from the library. We'll pull the shared core out into its own package when
we start the second system, once its real differences show where the boundaries go.

## Phases

1. **Protocol (Task / Context / Result) + operation shape + registry + run log.** Wrap the Risk Desk's gates and the exit rules first: they're already
   separate rules, so it's low risk. Behaviour must stay identical: the existing tests prove it.
2. **Definitions in the database** + the Operations editor in the Teams panel (settings, enable/disable, versions).
3. **Per-operation metrics + shadow mode**, shown on each team's panel.
4. **Remaining teams**: Market Data, Scanner, Trade Desk, Portfolio, Technology, Review.
5. **Extract the core** when the second system starts.

## Questions for you

- Should shadow results be shown in the 3D office (e.g. a "testing" badge on a team) or only in the panels?
- Live mode: confirm every definition change, or only changes that loosen limits?
- Any team or task you already know a future system needs (news, calendar, alerts) that should shape the design now?
