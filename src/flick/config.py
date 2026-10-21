"""Configuration: YAML file + environment variables (for secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

import yaml

LIVE_CONFIRMATION = "yes-i-understand-real-money"


@dataclass
class CreditSpreadConfig:
    enabled: bool = True
    sides: list[str] = field(default_factory=lambda: ["put", "call"])  # bull put / bear call
    dte_min: int = 30
    dte_max: int = 50
    short_delta_min: float = 0.15  # absolute delta of the short leg
    short_delta_max: float = 0.30
    widths: list[float] = field(default_factory=lambda: [1.0, 2.0, 5.0])
    min_credit_pct_of_width: float = 0.20
    min_otm_pct: float = 0.02  # short strike must be at least this far out of the money


@dataclass
class IronCondorConfig:
    """Short put spread + short call spread, same expiry and width. Max loss = width - credit."""
    enabled: bool = True
    dte_min: int = 30
    dte_max: int = 50
    short_delta_min: float = 0.10
    short_delta_max: float = 0.20
    widths: list[float] = field(default_factory=lambda: [2.0, 5.0])
    min_credit_pct_of_width: float = 0.25
    min_otm_pct: float = 0.02


@dataclass
class IronButterflyConfig:
    """Short at-the-money put + call at one strike, long wings either side.
    Max loss = wing width - credit."""
    enabled: bool = True
    dte_min: int = 30
    dte_max: int = 50
    wing_widths: list[float] = field(default_factory=lambda: [5.0, 10.0])
    min_credit_pct_of_width: float = 0.50


@dataclass
class CalendarConfig:
    """Sell a near-term option, buy a later one at the same strike. Max loss = debit paid."""
    enabled: bool = True
    rights: list[str] = field(default_factory=lambda: ["put", "call"])
    front_dte_min: int = 25
    front_dte_max: int = 40
    back_gap_min: int = 21  # back expiry is this many days or more after the front
    back_gap_max: int = 63
    strikes_from_atm: int = 1  # also try this many strikes either side of the money


@dataclass
class LeapsCallConfig:
    """Poor man's covered call: long deep-ITM LEAPS call, short near-term OTM calls against it."""
    enabled: bool = True
    leaps_dte_min: int = 270
    leaps_delta_min: float = 0.75
    short_dte_min: int = 21
    short_dte_max: int = 45
    short_delta_min: float = 0.15
    short_delta_max: float = 0.30
    min_otm_pct: float = 0.03


@dataclass
class FilterConfig:
    min_open_interest: int = 100
    max_leg_spread_pct: float = 0.15  # (ask-bid)/mid per leg
    min_bid: float = 0.05


@dataclass
class RiskConfig:
    max_loss_per_trade: float = 2500.0  # dollars, absolute cap on any one trade
    # Used only when `stages` is empty:
    max_loss_per_trade_pct: float = 0.02  # of equity
    max_portfolio_risk_pct: float = 0.25  # sum of max losses of open positions / equity
    max_positions: int = 10
    max_positions_per_underlying: int = 2
    min_score: float = 0.0  # minimum expected value per $ risked

    # Growth stages: limits by account size. A small account can't diversify (the smallest
    # spread is a big share of it), so the ceilings start high and tighten as equity grows.
    # These are ceilings; measured-edge sizing below can only make trades smaller.
    stages: list = field(default_factory=lambda: [
        {"min_equity": 0, "risk_per_trade_pct": 0.20, "max_portfolio_risk_pct": 0.40, "max_positions": 2},
        {"min_equity": 2_000, "risk_per_trade_pct": 0.10, "max_portfolio_risk_pct": 0.35, "max_positions": 4},
        {"min_equity": 10_000, "risk_per_trade_pct": 0.05, "max_portfolio_risk_pct": 0.30, "max_positions": 8},
        {"min_equity": 50_000, "risk_per_trade_pct": 0.02, "max_portfolio_risk_pct": 0.25, "max_positions": 12},
    ])
    # Measured-edge sizing: once there are enough closed trades, risk per trade is a fraction
    # of the Kelly bet computed from your own results. No measured edge -> no new trades.
    kelly_multiplier: float = 0.5  # half-Kelly: ~75% of the growth for far less drawdown
    min_trades_for_kelly: int = 30
    probation_risk_pct: float = 0.05  # per-trade ceiling until there are enough trades
    pause_if_no_edge: bool = True
    # Drawdown brakes, measured from the equity peak (or the last manual reset).
    drawdown_reduce_at: float = 0.15  # halve trade size
    drawdown_halt_at: float = 0.30  # stop new entries until reset from the dashboard
    daily_loss_halt_pct: float = 0.06  # no new entries for the rest of the day
    # Underlyings that move together count as one bet.
    groups: dict = field(default_factory=lambda: {"US equity index": ["SPY", "QQQ", "IWM", "DIA"]})
    max_group_risk_pct: float = 0.30
    goals: list = field(default_factory=lambda: [1_000, 10_000])

    # Trading capital: how much of the account the system may use. Sizing, drawdown and goals
    # are measured on this plus the system's own P&L, never the whole account. None = whole account.
    capital: float | None = None

    # Pilot mode for the first live days: hard dollar limits that replace the % limits above.
    pilot: bool = False
    # Dollars at risk per trade. $500 fits one $5-wide spread; narrower spreads mostly lose to
    # commissions (~$2.60 a round trip on 2 legs) and bid/ask, so the model rejects them anyway.
    pilot_max_loss_per_trade: float = 500.0
    pilot_max_positions: int = 1
    pilot_max_new_trades_per_day: int = 1
    pilot_strategies: list = field(default_factory=lambda: ["credit_spread"])
    pilot_max_leg_spread_pct: float = 0.10  # each leg's bid/ask must be within 10% of its mid


@dataclass
class ManagementConfig:
    take_profit_pct: float = 0.50  # of max profit (credit received)
    stop_loss_mult: float = 2.0  # close if loss >= mult * credit received
    close_at_dte: int = 21  # close short legs at or below this DTE
    min_short_extrinsic: float = 0.10  # below this the short leg can get assigned early
    itm_buffer_pct: float = 0.005  # close when short strike is within this of spot (or ITM)
    leaps_close_at_dte: int = 120  # sell LEAPS before theta accelerates
    leaps_stop_loss_pct: float = 0.40  # close PMCC if down this much of the debit paid
    debit_stop_loss_pct: float = 0.50  # debit trades (calendars): close if down this much of the debit
    check_threatened: bool = True  # close when a short strike is at/near the money
    # Per-strategy changes to any of the settings above. YAML entries are merged into these.
    overrides: dict = field(default_factory=lambda: {
        # Short strikes start at the money, so "threatened" doesn't apply; assignment is
        # still guarded by the extrinsic-value and DTE checks.
        "iron_butterfly": {"take_profit_pct": 0.25, "check_threatened": False},
        "calendar": {"take_profit_pct": 0.25, "close_at_dte": 5, "check_threatened": False},
    })

    def for_strategy(self, name: str) -> "ManagementConfig":
        return replace(self, **self.overrides.get(name, {}))


@dataclass
class RollingConfig:
    """Roll a credit spread out in time (and same or further from the money) instead of closing it,
    but only when the roll is paid for, passes the risk gate, and the model expects it to make money.
    Never on a stop loss: rolling losers is how small losses become big ones."""
    enabled: bool = True
    strategies: list = field(default_factory=lambda: ["credit_spread"])
    on_reasons: list = field(default_factory=lambda: ["dte exit", "short strike threatened"])
    min_net_credit: float = 0.05  # per share: the roll must collect at least this
    max_rolls: int = 2  # per position chain


@dataclass
class ExecutionConfig:
    slippage_frac: float = 0.25  # fraction of the half-spread we expect to give up vs mid
    commission_per_contract: float = 0.65
    fill_timeout_s: int = 45


@dataclass
class IBKRConfig:
    """Interactive Brokers via TWS or IB Gateway (must be running and logged in)."""
    host: str = "127.0.0.1"
    port: int = 4002  # IB Gateway: 4002 paper, 4001 live. TWS: 7497 paper, 7496 live.
    client_id: int = 17  # the dashboard uses client_id + 1
    account: str = ""  # blank = the only account on the login
    market_data_type: int = 1  # 1 = live (needs OPRA subscription), 3 = delayed
    max_expiries: int = 2  # expiries fetched per chain request (keeps requests within IB pacing)
    timeout_s: int = 60


@dataclass
class Config:
    mode: str = "paper"  # sim | paper | live  (broker is always Interactive Brokers)
    data: str = "ibkr"  # ibkr | synthetic (offline testing, sim mode only)
    universe: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    db_path: str = "var/flick.db"
    log_path: str = "var/flick.log"
    sim_starting_equity: float = 25_000.0
    risk_free_rate: float = 0.04
    vol_blend: float = 0.5  # forecast vol = blend*IV + (1-blend)*realized vol
    allow_new_entries: bool = True
    max_new_trades_per_cycle: int = 2
    credit_spread: CreditSpreadConfig = field(default_factory=CreditSpreadConfig)
    iron_condor: IronCondorConfig = field(default_factory=IronCondorConfig)
    iron_butterfly: IronButterflyConfig = field(default_factory=IronButterflyConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    leaps_call: LeapsCallConfig = field(default_factory=LeapsCallConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    rolling: RollingConfig = field(default_factory=RollingConfig)

    def validate(self) -> None:
        if self.mode not in {"sim", "paper", "live"}:
            raise ValueError(f"unknown mode {self.mode!r}")
        if self.data not in {"ibkr", "synthetic"}:
            raise ValueError(f"unknown data source {self.data!r}")
        if self.data == "synthetic" and self.mode != "sim":
            raise ValueError("synthetic data can only be used with mode: sim")
        if self.mode == "live" and os.environ.get("FLICK_ALLOW_LIVE") != LIVE_CONFIRMATION:
            raise RuntimeError(
                "Live trading refused: set FLICK_ALLOW_LIVE=" + LIVE_CONFIRMATION + " to enable."
            )


def _build(cls: type, data: dict[str, Any] | None):
    obj = cls()
    for f in fields(cls):
        if not data or f.name not in data:
            continue
        current = getattr(obj, f.name)
        value = data[f.name]
        if is_dataclass(current):
            value = _build(type(current), value)
        elif isinstance(current, dict) and isinstance(value, dict):  # merge, e.g. management.overrides
            value = {**current, **{k: ({**current.get(k, {}), **v} if isinstance(v, dict) else v)
                                   for k, v in value.items()}}
        setattr(obj, f.name, value)
    return obj


def load_config(path: str | Path | None = None) -> Config:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    path = Path(path or os.environ.get("FLICK_CONFIG", "config.yaml"))
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    cfg = _build(Config, data or {})
    cfg.validate()
    return cfg
