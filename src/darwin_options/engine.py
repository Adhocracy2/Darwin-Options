"""The trading loop: manage open positions, then scan, check risk, and open new trades."""
from __future__ import annotations

import copy
import fcntl
import logging
import math
from dataclasses import asdict
from functools import reduce
from pathlib import Path

from .broker.base import Broker
from .config import Config
from .ibkr import GatewayUnavailable
from .data.base import MarketData, ScanCache
from .manager import ExitAction, PositionManager
from .models import Candidate, Fill, Leg, OptionContract, OrderIntent, Position, PositionLeg
from .portfolio import RiskState, assess
from .roller import find_roll, roll_count
from .risk import RiskGate, assert_never_naked, holdings_of
from .store import Store
from .strategies.base import StrategyContext
from .settings import SETTINGS_KEY, SettingsError, apply as apply_settings, validate as validate_settings
from . import strategies as strategy_registry
from .strategies import sameday
from . import analysis

RISK_RESET_KEY = "risk_reset"
CYCLE_KEY = "cycle_progress"  # the current/last cycle's steps, for the dashboard
# which team does each cycle step, and how the activity log describes it
STEP_ACTIVITY = {
    "reconcile": ("portfolio", "checking positions against IBKR"),
    "manage": ("exits", "checking open trades against the exit rules"),
    "scan": ("scanner", "fetching option chains and building trade ideas"),
    "risk": ("risk", "checking ideas against the risk rules"),
    "orders": ("trading", "placing an order"),
    "collect": ("data", "saving extra option prices for backtests"),
    "snapshot": ("portfolio", "recording equity"),
}

log = logging.getLogger(__name__)


def candidate_row(c: Candidate, decision=None) -> dict:
    return {
        "strategy": c.strategy, "underlying": c.underlying, "description": c.describe(),
        "legs": [{"symbol": l.contract.symbol, "ratio": l.ratio, "strike": l.contract.strike,
                  "expiry": l.contract.expiry.isoformat(), "right": l.contract.right.value} for l in c.legs],
        "net_credit": c.net_credit, "max_loss": round(c.max_loss, 2), "max_profit": round(c.max_profit, 2),
        "pop": round(c.pop, 4), "expected_value": round(c.expected_value, 2), "score": round(c.score, 4),
        "spot": c.spot, "attach_to": c.attach_to, "notes": c.notes,
        "approved": decision.approved if decision else None,
        "qty": decision.qty if decision else None,
        "reasons": decision.reasons if decision else [],
    }


def trim_scan(rows: list[dict], top: int = 150, per_strategy: int = 10) -> list[dict]:
    """Rows to keep for the dashboard: the best overall plus the best few of every strategy,
    so a strategy that ranks low still shows up (e.g. in its payoff diagram). Order is kept."""
    keep = set(range(min(top, len(rows))))
    seen: dict[str, int] = {}
    for i, r in enumerate(rows):
        if seen.get(r["strategy"], 0) < per_strategy:
            keep.add(i)
            seen[r["strategy"]] = seen.get(r["strategy"], 0) + 1
    return [r for i, r in enumerate(rows) if i in keep]


class Engine:
    session = None  # IBSession when IBKR is used (set by factory.build_engine)

    def gateway(self) -> dict | None:
        """IB Gateway connection state for the dashboard; None when IBKR isn't used."""
        if self.session is None:
            return None
        return {"connected": self.session.connected, "error": self.session.last_error, **self.session.link_status()}

    def __init__(self, cfg: Config, md: MarketData, broker: Broker, store: Store):
        self.cfg, self.md, self.broker, self.store = cfg, md, broker, store
        self.gate = RiskGate(cfg)
        self.manager = PositionManager(cfg)
        self.base_cfg = copy.deepcopy(cfg)  # config.yaml as loaded; dashboard settings go on top
        self._settings_version = 0
        self.stop_requested = False  # set on SIGTERM: place no new orders, finish the cycle
        self._daily_collected: dict = {}  # ticker -> date its daily chain was recorded
        self._cycle_state: dict = {}
        self.loop_interval_s: int | None = None  # set by `darwin loop`
        self._build_strategies()
        self.refresh_settings()

    def _build_strategies(self) -> None:
        strategy_registry.sync_exit_rules(self.cfg)
        self.strategies = strategy_registry.build(self.cfg)

    def refresh_settings(self) -> bool:
        """Apply dashboard settings if they changed since we last looked. Returns True if applied."""
        overlay, version = self.store.get_setting(SETTINGS_KEY)
        if version == self._settings_version:
            return False
        apply_settings(self.cfg, self.base_cfg, overlay)
        self._settings_version = version
        self._build_strategies()
        log.info("dashboard settings v%s applied", version)
        return True

    def update_settings(self, overlay: dict) -> dict:
        """Validate, save and apply new dashboard settings. Returns the stored overlay."""
        clean = validate_settings(overlay, self.base_cfg)
        kept = set(clean.get("strategies", {})) | {d.get("id") for d in self.base_cfg.custom_strategies or []}
        in_use = {p.strategy for p in self.store.positions("open")}
        for inst in strategy_registry.instances(self.cfg):
            if not inst.builtin and inst.id not in kept and inst.id in in_use:
                raise SettingsError(f"{inst.label} has open positions: switch it off instead of deleting it")
        self.store.set_setting(SETTINGS_KEY, clean)
        self.refresh_settings()
        self.store.event("settings", "strategy settings changed", data=clean)
        return clean

    # ---- scanning ------------------------------------------------------------

    def now(self):
        """New York time for intraday rules. Replays supply their own clock; None = the real one."""
        f = getattr(self.md, "clock_now", None)
        return f() if f else None

    def scan(self, symbols: list[str] | None = None) -> list[Candidate]:
        ctx = StrategyContext(self.cfg, ScanCache(self.md), self.store.positions("open"), self.md.today(), self.now())
        out: list[Candidate] = []
        same_day_block = sameday.blocked(ctx) if any(st.same_day for st in self.strategies) else None
        for sym in symbols or self.cfg.universe:
            for strat in self.strategies:
                if strat.same_day and same_day_block:
                    continue
                try:
                    out.extend(strat.generate(sym, ctx))
                except GatewayUnavailable:
                    raise  # gateway down or not answering: skip the cycle, don't time out per strategy
                except Exception as e:
                    log.exception("scan %s/%s failed", strat.name, sym)
                    self.store.event("error", f"scan {strat.name} {sym}: {e}")
        out.sort(key=lambda c: c.score, reverse=True)
        return out

    # ---- execution helpers ---------------------------------------------------

    def _assert_safe_after(self, changes: dict[str, int]) -> None:
        after = holdings_of(self.store.positions("open"))
        for s, q in changes.items():
            after[s] = after.get(s, 0) + q
        assert_never_naked(after)

    def open_candidate(self, c: Candidate, qty: int) -> Position | None:
        self._assert_safe_after({l.contract.symbol: l.ratio * qty for l in c.legs})
        intent = OrderIntent(c.legs, qty, c.net_credit, opening=True, tag=c.strategy)
        mid = self._mid_credit(c.legs)
        fill = self.broker.submit(intent)
        if fill is None:
            self.store.event("no_fill", f"open not filled: {c.describe()} @ {c.net_credit:.2f}")
            return None
        new_legs = [PositionLeg(l.contract.symbol, l.ratio * fill.qty, fill.leg_prices[l.contract.symbol])
                    for l in c.legs]
        if c.attach_to:
            pos = self.store.get_position(c.attach_to)
            pos.legs.extend(new_legs)
            pos.commissions += fill.commission
        else:
            pos = Position(c.strategy, c.underlying, new_legs, max_loss=c.max_loss * fill.qty, opened_at=self.store.clock(),
                           max_profit=c.max_profit * fill.qty, commissions=fill.commission,
                           entry_notes=candidate_row(c) | {"fill_quality": self._fill_quality(mid, c.net_credit, fill, c.legs)})
        self.store.save_position(pos)
        self.store.event("open", f"{c.describe()} x{fill.qty} @ {fill.net_credit(c.legs):.2f}", pos.id,
                         {"fill": asdict(fill)})
        return pos

    def _mid_credit(self, legs: list[Leg]) -> float | None:
        try:
            q = self.md.quotes([l.contract.symbol for l in legs])
            return -sum(l.ratio * q[l.contract.symbol].mid for l in legs)
        except Exception:
            return None

    @staticmethod
    def _fill_quality(mid: float | None, limit: float, fill: Fill, legs: list[Leg]) -> dict:
        """Per share: what the market's mid offered, what we asked, what we got. Slippage > 0 = worse than mid."""
        got = fill.net_credit(legs)
        return {"mid": None if mid is None else round(mid, 3), "limit": round(limit, 3), "fill": round(got, 3),
                "slippage": None if mid is None else round(mid - got, 3)}

    def close_legs(self, pos: Position, action: ExitAction) -> bool:
        legs = [l for l in pos.open_legs if l.symbol in action.symbols]
        units = reduce(math.gcd, (abs(l.qty) for l in legs))
        order_legs = [Leg(OptionContract.from_occ(l.symbol), -l.qty // units) for l in legs]
        self._assert_safe_after({l.symbol: -l.qty for l in legs})
        quotes = self.md.quotes([l.symbol for l in legs])
        if any(l.symbol not in quotes for l in legs):
            self.store.event("error", f"no quotes to close {action.symbols}", pos.id)
            return False
        limit = analysis.expected_net_credit(order_legs, quotes, self.cfg.execution.slippage_frac,
                                             urgent=action.urgent)
        fill = self.broker.submit(OrderIntent(order_legs, units, round(limit, 2), opening=False,
                                              tag=action.reason, urgent=action.urgent))
        if fill is None:
            self.store.event("no_fill", f"close not filled ({action.reason})", pos.id)
            return False
        self._apply_close(pos, legs, fill, units)
        if not pos.open_legs:
            pos.status = "closed"
            pos.closed_at = self.store.clock()
            pos.exit_reason = action.reason
        self.store.save_position(pos)
        self.store.event("close", f"{pos.strategy} {pos.underlying}: {action.reason}", pos.id,
                         {"fill": asdict(fill), "realized": pos.realized_pnl()})
        return True

    @staticmethod
    def _apply_close(pos: Position, legs: list[PositionLeg], fill: Fill, units: int) -> None:
        for l in legs:
            if l.symbol not in fill.leg_prices:
                continue
            closed_qty = l.qty * fill.qty // units
            if closed_qty == l.qty:
                l.exit_price = fill.leg_prices[l.symbol]
            else:  # partial fill: split the leg
                l.qty -= closed_qty
                pos.legs.append(PositionLeg(l.symbol, closed_qty, l.entry_price, fill.leg_prices[l.symbol]))
        pos.commissions += fill.commission

    # ---- cycle -----------------------------------------------------------------

    def manage(self) -> list[ExitAction]:
        today = self.md.today()
        done: list[ExitAction] = []
        for pos in self.store.positions("open"):
            try:
                quotes = self.md.quotes([l.symbol for l in pos.open_legs])
                action = self.manager.evaluate(pos, quotes, self.md.spot(pos.underlying), today, self.now())
                if action and (self.try_roll(pos, action) or self.close_legs(pos, action)):
                    done.append(action)
            except GatewayUnavailable:
                raise
            except Exception as e:
                log.exception("manage %s failed", pos.id)
                self.store.event("error", f"manage {pos.id}: {e}", pos.id)
        return done

    def try_roll(self, pos: Position, action: ExitAction) -> bool:
        """Roll instead of closing when it's paid for and passes every check (see roller.py)."""
        rc = self.cfg.rolling
        inst = strategy_registry.find(self.cfg, pos.strategy)
        if (not rc.enabled or not self.cfg.allow_new_entries or inst is None or inst.same_day
                or inst.type not in rc.strategies
                or action.reason.split(":")[0] not in rc.on_reasons
                or set(action.symbols) != {l.symbol for l in pos.open_legs}):
            return False
        acct = self.broker.account()
        state = self.risk_state(acct.equity)
        positions = self.store.positions("open")
        ctx = StrategyContext(self.cfg, ScanCache(self.md), positions, self.md.today(), self.now())
        roll, why = find_roll(pos, action.reason, ctx, self.gate, positions, acct.buying_power, state)
        if roll is None:
            self.store.event("roll_skipped", f"not rolled ({why}); closing instead", pos.id)
            return False
        old_legs = pos.open_legs
        changes = {l.symbol: -l.qty for l in old_legs}
        for l in roll.new.legs:
            changes[l.contract.symbol] = changes.get(l.contract.symbol, 0) + l.ratio * roll.qty
        self._assert_safe_after(changes)
        mid = self._mid_credit(roll.legs)
        fill = self.broker.submit(OrderIntent(roll.legs, roll.qty, roll.net_credit, opening=True, tag="roll"))
        if fill is None:
            self.store.event("no_fill", f"roll not filled @ {roll.net_credit:+.2f}; closing instead", pos.id)
            return False
        half = Fill(fill.qty, fill.leg_prices, fill.commission / 2, fill.broker_order_id)
        self._apply_close(pos, old_legs, half, roll.qty)
        if not pos.open_legs:
            pos.status, pos.closed_at, pos.exit_reason = "closed", self.store.clock(), f"rolled: {action.reason}"
        self.store.save_position(pos)
        new_legs = [PositionLeg(l.contract.symbol, l.ratio * fill.qty, fill.leg_prices[l.contract.symbol])
                    for l in roll.new.legs]
        new = Position(roll.new.strategy, pos.underlying, new_legs, max_loss=roll.new.max_loss * fill.qty, opened_at=self.store.clock(),
                       max_profit=roll.new.max_profit * fill.qty, commissions=half.commission,
                       entry_notes=candidate_row(roll.new) | {
                           "rolled_from": pos.id, "roll_count": roll_count(pos) + 1,
                           "roll_net_credit": roll.net_credit,
                           "fill_quality": self._fill_quality(mid, roll.net_credit, fill, roll.legs)})
        self.store.save_position(new)
        self.store.event("roll", f"{pos.underlying} rolled ({action.reason.split(':')[0]}) to "
                                 f"{roll.new.describe()} for {fill.net_credit(roll.legs):+.2f} net x{fill.qty}",
                         new.id, {"from": pos.id, "fill": asdict(fill), "realized_on_old": pos.realized_pnl()})
        return True

    def reconcile(self) -> list[str]:
        if self.broker.name == "sim":
            return []
        ours = {s: q for s, q in holdings_of(self.store.positions("open")).items() if q}
        theirs = self.broker.broker_positions()
        diffs = [f"{s}: journal {ours.get(s, 0)} vs broker {theirs.get(s, 0)}"
                 for s in sorted(set(ours) | set(theirs)) if ours.get(s, 0) != theirs.get(s, 0)]
        if diffs:
            self.store.event("reconcile", "journal and broker disagree", data={"diffs": diffs})
        return diffs

    def snapshot(self) -> None:
        acct = self.broker.account()
        open_pos = self.store.positions("open")
        realized = sum(p.realized_pnl() for p in self.store.positions(None))
        syms = [l.symbol for p in open_pos for l in p.open_legs]
        marks = {s: q.mid for s, q in self.md.quotes(syms).items()} if syms else {}
        unreal = sum(p.unrealized_pnl(marks) for p in open_pos if all(l.symbol in marks for l in p.open_legs))
        self.store.snapshot(acct.equity, sum(p.max_loss for p in open_pos), realized, unreal)

    def sizing_equity(self, account_equity: float, marks: dict[str, float] | None = None) -> float:
        """What the system sizes against: the account, or (with `risk.capital`) the trading
        capital plus the system's own P&L, never more than the account holds. `marks` (option
        symbol -> mid) lets the dashboard pass prices it already has instead of asking IBKR."""
        cap = self.cfg.risk.capital
        if cap is None:
            return account_equity
        positions = self.store.positions(None)
        realized = sum(p.realized_pnl() for p in positions)
        open_pos = [p for p in positions if p.status == "open"]
        unrealized = 0.0
        if open_pos:
            try:
                if marks is None:
                    marks = {s: q.mid for s, q in self.md.quotes([l.symbol for p in open_pos for l in p.open_legs]).items()}
                unrealized = sum(p.unrealized_pnl(marks) for p in open_pos if all(l.symbol in marks for l in p.open_legs))
            except Exception:
                snaps = self.store.snapshots(1)
                unrealized = snaps[-1]["unrealized"] if snaps else 0.0
        return min(account_equity, cap + realized + unrealized)

    def _equity_series(self) -> list[dict]:
        snaps = self.store.snapshots()
        cap = self.cfg.risk.capital
        if cap is None:
            return snaps
        return [{"ts": s["ts"], "equity": cap + (s["realized"] or 0) + (s["unrealized"] or 0)} for s in snaps]

    def opened_today(self) -> int:
        """New positions opened today (New York date); rolls don't count."""
        from .portfolio import _ny_date
        from .broker.base import NY
        from datetime import datetime
        today = (self.now() or datetime.now(NY)).astimezone(NY).date().isoformat()  # historical in a backtest
        return sum(1 for p in self.store.positions(None)
                   if _ny_date(p.opened_at) == today and not (p.entry_notes or {}).get("rolled_from"))

    def risk_state(self, account_equity: float, marks: dict[str, float] | None = None) -> RiskState:
        reset, _ = self.store.get_setting(RISK_RESET_KEY)
        return assess(self.cfg.risk, self.sizing_equity(account_equity, marks), self._equity_series(),
                      self.store.positions("closed"), reset.get("ts"), now=self.now(), opened_today=self.opened_today())

    def reset_drawdown(self) -> None:
        """Start measuring drawdown from now (lifts a drawdown halt). Logged in the journal."""
        from .store import now
        self.store.set_setting(RISK_RESET_KEY, {"ts": now()})
        self.store.event("risk", "drawdown peak reset from the dashboard")

    def open_new(self) -> list[Position]:
        opened: list[Position] = []
        rows: list[dict] = []
        touched: set[str] = set()
        unfilled = 0
        acct = self.broker.account()
        positions = self.store.positions("open")
        state = self.risk_state(acct.equity)
        sd_today = sameday.today_stats(self.store.positions(None), self.md.today())
        self._step("scan")
        candidates = self.scan()
        self._step("risk")
        for c in candidates:
            decision = self.gate.check(c, positions, state.equity, acct.buying_power, self.md.today(), state, sd_today)
            if decision.approved and (c.underlying in touched or len(opened) >= self.cfg.max_new_trades_per_cycle):
                decision.approved, decision.reasons = False, ["cycle limit / already traded this underlying"]
            elif decision.approved and unfilled >= self.cfg.execution.max_unfilled_entries_per_cycle:
                decision.approved, decision.reasons = False, [f"cycle limit: {unfilled} unfilled orders this cycle"]
            elif decision.approved and self.stop_requested:
                decision.approved, decision.reasons = False, ["stopping: no new orders"]
            elif decision.approved and (wait := self.opening_wait()):
                decision.approved, decision.reasons = False, [wait]
            rows.append(candidate_row(c, decision))
            if decision.approved and self.cfg.allow_new_entries:
                self._step("orders")
                try:
                    pos = self.open_candidate(c, decision.qty)
                except Exception as e:
                    log.exception("open failed: %s", c.describe())
                    self.store.event("error", f"open {c.describe()}: {e}")
                    continue
                if pos is None:
                    unfilled += 1
                if pos:
                    opened.append(pos)
                    touched.add(c.underlying)
                    acct = self.broker.account()
                    positions = self.store.positions("open")
                    state = self.risk_state(acct.equity)
                    sd_today = sameday.today_stats(self.store.positions(None), self.md.today())
        self.store.save_scan(trim_scan(rows))
        return opened

    def _log(self, team: str | None, message: str) -> None:
        try:
            st = self._cycle_state
            cyc = f"{st.get('day', '')} #{st.get('count', '?')}" if st else None
            self.store.log_activity(cyc, team, message)
        except Exception:
            log.debug("couldn't write the activity log", exc_info=True)

    def _received(self, notices_before: dict) -> dict | None:
        """What this cycle got back from IBKR: prices, option chains, quotes, account/positions, notices."""
        rec = getattr(self.md, "received", None)
        if rec is None:
            return None
        out = {k: v for k, v in rec.items()}
        out["positions"] = len(self.store.positions("open"))
        if self.session is not None:
            out["notices"] = [{"code": c, "count": n - notices_before.get(c, 0), "text": self.session.notice_text.get(c, "")}
                              for c, n in sorted(self.session.notices.items()) if n - notices_before.get(c, 0) > 0]
        return out

    def _ibc_heal(self, received: dict | None) -> None:
        """After a burst of market-data refusals (10197, another session using the live data), ask IBC
        to reconnect IB Gateway's market data. Logged in the journal."""
        ic = self.cfg.ibc
        if not (ic.enabled and received):
            return
        refused = sum(n["count"] for n in received.get("notices", []) if n["code"] == 10197)
        if refused < ic.reconnect_after_refusals:
            return
        from . import ibc
        try:
            reply = ibc.send("RECONNECTDATA", ic.host, ic.port)
            self.store.event("ibc", f"{refused} quotes refused (competing live session): asked IBC to reconnect market data ({reply or 'no reply'})")
        except OSError as e:
            self.store.event("error", f"{refused} quotes refused; couldn't reach IBC to reconnect market data: {e}")

    def opening_wait(self) -> str | None:
        """Why new trades must wait (the first minutes after the open), or None."""
        mins = self.cfg.risk.no_entries_after_open_min
        if not mins:
            return None
        from datetime import datetime, time, timedelta
        from . import clock
        now = self.now() or clock.now_ny()
        start = (datetime.combine(now.date(), time(9, 30)) + timedelta(minutes=mins)).time()
        if time(9, 30) <= now.time() < start:
            return f"no new trades in the first {mins} minutes after the open (until {start:%H:%M} New York)"
        return None

    def collect(self) -> None:
        """Record option prices the strategies didn't ask for, for backtests (record.* in config):
        today's expiry every cycle, and once a day a wider chain. Fetched chains are saved by the
        recording wrapper. Only on live IBKR data; a failure here never stops trading."""
        rc = self.cfg.record
        if not (rc.enabled and hasattr(self.md, "rec")):  # the recorder wraps live IBKR data only
            return
        from . import clock
        now = self.now() or clock.now_ny()
        for sym in dict.fromkeys(list(self.cfg.universe) + list(rc.extra_symbols)):
            try:
                if sym not in self.cfg.universe:
                    self.md.spot(sym)  # recorded each cycle (the chain records it too, but only when it has quotes)
                if rc.collect_same_day:
                    self.md.chain(sym, 0, 0, None, rc.same_day_strike_pct)
                if now.time() >= clock.parse_hhmm(rc.daily_chain_time) and self._daily_collected.get(sym) != now.date():
                    session = getattr(self.md, "s", None) or getattr(getattr(self.md, "md", None), "s", None)
                    old = session.cfg.max_expiries if session else None
                    if session:
                        session.cfg.max_expiries = rc.daily_chain_max_expiries
                    try:
                        self.md.chain(sym, 1, rc.daily_chain_max_dte, None, rc.daily_chain_strike_pct)
                    finally:
                        if session:
                            session.cfg.max_expiries = old
                    self._daily_collected[sym] = now.date()
                    log.info("recorded the daily option chain for %s", sym)
            except GatewayUnavailable as e:  # gateway slow or gone: stop collecting this cycle, keep the cycle going
                log.warning("collecting %s prices stopped: %s", sym, e)
                break
            except Exception as e:
                log.warning("collecting %s prices failed: %s", sym, e)

    def preview(self) -> list[dict]:
        """Scan and risk-check without trading."""
        self.refresh_settings()
        acct = self.broker.account()
        positions = self.store.positions("open")
        state = self.risk_state(acct.equity)
        sd_today = sameday.today_stats(self.store.positions(None), self.md.today())
        rows = [candidate_row(c, self.gate.check(c, positions, state.equity, acct.buying_power, self.md.today(), state, sd_today))
                for c in self.scan()]
        self.store.save_scan(trim_scan(rows))
        return rows

    def run_once(self, force: bool = False) -> dict:
        """One cycle. A file lock stops the scheduler and the UI from trading at the same time."""
        if self.cfg.db_path == ":memory:":  # in-memory journal: one process, nothing to lock against
            return self._cycle(force)
        lock_path = Path(self.cfg.db_path).with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"skipped": "another cycle is running"}
            return self._cycle(force)

    def _cycle(self, force: bool) -> dict:
        try:
            return self._run_cycle(force)
        except GatewayUnavailable as e:
            log.warning("cycle skipped: %s", e)
            return {"skipped": str(e)}

    def _step(self, name: str | None, **extra) -> None:
        """What the cycle is doing now, for the dashboard (the 3D office animates that team)."""
        from .store import now
        st = self._cycle_state
        if name:
            st["steps"] = st.get("steps", []) + [name]
            team, what = STEP_ACTIVITY.get(name, (None, name))
            self._log(team, what)
        st.update(step=name, at=now(), **extra)
        try:
            self.store.set_setting(CYCLE_KEY, st)
        except Exception:  # display only: never let it disturb trading
            log.debug("couldn't save cycle progress", exc_info=True)

    def _run_cycle(self, force: bool) -> dict:
        from .store import now
        self.refresh_settings()
        acct = self.broker.account()
        if not acct.is_market_open and not force:
            return {"skipped": "market closed"}
        from . import clock
        day = (self.now() or clock.now_ny()).date().isoformat()
        prev = self._cycle_state or self.store.get_setting(CYCLE_KEY)[0] or {}
        count = (prev.get("count", 0) if prev.get("day") == day else 0) + 1
        self._cycle_state = {"started": now(), "finished": None, "steps": [], "day": day, "count": count}
        if hasattr(self.md, "reset_received"):
            self.md.reset_received()
        notices_before = dict(self.session.notices) if self.session is not None else {}
        try:
            self._step("reconcile")
            diffs = self.reconcile()
            self._step("manage")
            exits = self.manage()
            opened = self.open_new() if not diffs else []
            if diffs:
                log.warning("skipping new entries until journal and broker agree: %s", diffs)
            self._step("collect")
            self.collect()
            self._step("snapshot")
            self.snapshot()
            result = {"exits": [a.reason for a in exits], "opened": [p.id for p in opened], "reconcile": diffs}
        except Exception as e:
            self._step(None, finished=now(), error=f"{type(e).__name__}: {e}")
            raise
        from datetime import datetime, timedelta
        nxt = (datetime.fromisoformat(now()) + timedelta(seconds=self.loop_interval_s)).isoformat() if self.loop_interval_s else None
        received = self._received(notices_before)
        if received:
            chains = [c for c in received["chains"] if not c["cached"]]
            self._log("data", f"received {len(received['spots'])} prices and {sum(c['quotes'] for c in chains)} option quotes "
                              f"in {len(chains)} chains from IBKR")
        scan = self.store.latest_scan()
        if scan.get("ts"):
            ok = sum(1 for c in scan["candidates"] if c.get("approved"))
            self._log("risk", f"{len(scan['candidates'])} ideas checked, {ok} approved")
        for p in result["opened"]:
            self._log("trading", f"opened position {p}")
        for r in result["exits"]:
            self._log("exits", f"closed a position: {r}")
        self._log(None, f"cycle done: {len(result['opened'])} opened, {len(result['exits'])} closed")
        self._step(None, finished=now(), next_at=nxt, result={"exits": len(result["exits"]), "opened": len(result["opened"])},
                   received=received)
        self._ibc_heal(received)
        return result
