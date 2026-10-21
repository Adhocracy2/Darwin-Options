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
from .data.base import MarketData, ScanCache
from .manager import ExitAction, PositionManager
from .models import Candidate, Fill, Leg, OptionContract, OrderIntent, Position, PositionLeg
from .portfolio import RiskState, assess
from .roller import find_roll, roll_count
from .risk import RiskGate, assert_never_naked, holdings_of
from .store import Store
from .strategies.base import StrategyContext
from .settings import SETTINGS_KEY, apply as apply_settings, validate as validate_settings
from .strategies import STRATEGIES
from . import analysis

RISK_RESET_KEY = "risk_reset"

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
    def __init__(self, cfg: Config, md: MarketData, broker: Broker, store: Store):
        self.cfg, self.md, self.broker, self.store = cfg, md, broker, store
        self.gate = RiskGate(cfg)
        self.manager = PositionManager(cfg)
        self.base_cfg = copy.deepcopy(cfg)  # config.yaml as loaded; dashboard settings go on top
        self._settings_version = 0
        self._build_strategies()
        self.refresh_settings()

    def _build_strategies(self) -> None:
        self.strategies = [cls() for name, cls in STRATEGIES.items() if getattr(self.cfg, name).enabled]

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
        clean = validate_settings(overlay)
        self.store.set_setting(SETTINGS_KEY, clean)
        self.refresh_settings()
        self.store.event("settings", "strategy settings changed", data=clean)
        return clean

    # ---- scanning ------------------------------------------------------------

    def scan(self, symbols: list[str] | None = None) -> list[Candidate]:
        ctx = StrategyContext(self.cfg, ScanCache(self.md), self.store.positions("open"), self.md.today())
        out: list[Candidate] = []
        for sym in symbols or self.cfg.universe:
            for strat in self.strategies:
                try:
                    out.extend(strat.generate(sym, ctx))
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
            pos = Position(c.strategy, c.underlying, new_legs, max_loss=c.max_loss * fill.qty,
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
            from .store import now
            pos.closed_at = now()
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
                action = self.manager.evaluate(pos, quotes, self.md.spot(pos.underlying), today)
                if action and (self.try_roll(pos, action) or self.close_legs(pos, action)):
                    done.append(action)
            except Exception as e:
                log.exception("manage %s failed", pos.id)
                self.store.event("error", f"manage {pos.id}: {e}", pos.id)
        return done

    def try_roll(self, pos: Position, action: ExitAction) -> bool:
        """Roll instead of closing when it's paid for and passes every check (see roller.py)."""
        rc = self.cfg.rolling
        if (not rc.enabled or not self.cfg.allow_new_entries or pos.strategy not in rc.strategies
                or action.reason.split(":")[0] not in rc.on_reasons
                or set(action.symbols) != {l.symbol for l in pos.open_legs}):
            return False
        acct = self.broker.account()
        state = self.risk_state(acct.equity)
        positions = self.store.positions("open")
        ctx = StrategyContext(self.cfg, ScanCache(self.md), positions, self.md.today())
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
        from .store import now
        if not pos.open_legs:
            pos.status, pos.closed_at, pos.exit_reason = "closed", now(), f"rolled: {action.reason}"
        self.store.save_position(pos)
        new_legs = [PositionLeg(l.contract.symbol, l.ratio * fill.qty, fill.leg_prices[l.contract.symbol])
                    for l in roll.new.legs]
        new = Position(roll.new.strategy, pos.underlying, new_legs, max_loss=roll.new.max_loss * fill.qty,
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

    def sizing_equity(self, account_equity: float) -> float:
        """What the system sizes against: the account, or (with `risk.capital`) the trading
        capital plus the system's own P&L, never more than the account holds."""
        cap = self.cfg.risk.capital
        if cap is None:
            return account_equity
        positions = self.store.positions(None)
        realized = sum(p.realized_pnl() for p in positions)
        open_pos = [p for p in positions if p.status == "open"]
        unrealized = 0.0
        if open_pos:
            try:
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
        today = datetime.now(NY).date().isoformat()
        return sum(1 for p in self.store.positions(None)
                   if _ny_date(p.opened_at) == today and not (p.entry_notes or {}).get("rolled_from"))

    def risk_state(self, account_equity: float) -> RiskState:
        reset, _ = self.store.get_setting(RISK_RESET_KEY)
        return assess(self.cfg.risk, self.sizing_equity(account_equity), self._equity_series(),
                      self.store.positions("closed"), reset.get("ts"), opened_today=self.opened_today())

    def reset_drawdown(self) -> None:
        """Start measuring drawdown from now (lifts a drawdown halt). Logged in the journal."""
        from .store import now
        self.store.set_setting(RISK_RESET_KEY, {"ts": now()})
        self.store.event("risk", "drawdown peak reset from the dashboard")

    def open_new(self) -> list[Position]:
        opened: list[Position] = []
        rows: list[dict] = []
        touched: set[str] = set()
        acct = self.broker.account()
        positions = self.store.positions("open")
        state = self.risk_state(acct.equity)
        for c in self.scan():
            decision = self.gate.check(c, positions, state.equity, acct.buying_power, self.md.today(), state)
            if decision.approved and (c.underlying in touched or len(opened) >= self.cfg.max_new_trades_per_cycle):
                decision.approved, decision.reasons = False, ["cycle limit / already traded this underlying"]
            rows.append(candidate_row(c, decision))
            if decision.approved and self.cfg.allow_new_entries:
                try:
                    pos = self.open_candidate(c, decision.qty)
                except Exception as e:
                    log.exception("open failed: %s", c.describe())
                    self.store.event("error", f"open {c.describe()}: {e}")
                    continue
                if pos:
                    opened.append(pos)
                    touched.add(c.underlying)
                    acct = self.broker.account()
                    positions = self.store.positions("open")
                    state = self.risk_state(acct.equity)
        self.store.save_scan(trim_scan(rows))
        return opened

    def preview(self) -> list[dict]:
        """Scan and risk-check without trading."""
        self.refresh_settings()
        acct = self.broker.account()
        positions = self.store.positions("open")
        state = self.risk_state(acct.equity)
        rows = [candidate_row(c, self.gate.check(c, positions, state.equity, acct.buying_power, self.md.today(), state))
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
        self.refresh_settings()
        acct = self.broker.account()
        if not acct.is_market_open and not force:
            return {"skipped": "market closed"}
        diffs = self.reconcile()
        exits = self.manage()
        opened = self.open_new() if not diffs else []
        if diffs:
            log.warning("skipping new entries until journal and broker agree: %s", diffs)
        self.snapshot()
        return {"exits": [a.reason for a in exits], "opened": [p.id for p in opened], "reconcile": diffs}
