"""Configuration: YAML file + environment variables (for secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
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
    max_loss_per_trade: float = 500.0  # dollars, absolute cap
    max_loss_per_trade_pct: float = 0.02  # of equity
    max_portfolio_risk_pct: float = 0.25  # sum of max losses of open positions / equity
    max_positions: int = 10
    max_positions_per_underlying: int = 2
    min_score: float = 0.0  # minimum expected value per $ risked


@dataclass
class ManagementConfig:
    take_profit_pct: float = 0.50  # of max profit (credit received)
    stop_loss_mult: float = 2.0  # close if loss >= mult * credit received
    close_at_dte: int = 21  # close short legs at or below this DTE
    min_short_extrinsic: float = 0.10  # below this the short leg can get assigned early
    itm_buffer_pct: float = 0.005  # close when short strike is within this of spot (or ITM)
    leaps_close_at_dte: int = 120  # sell LEAPS before theta accelerates
    leaps_stop_loss_pct: float = 0.40  # close PMCC if down this much of the debit paid


@dataclass
class ExecutionConfig:
    slippage_frac: float = 0.25  # fraction of the half-spread we expect to give up vs mid
    commission_per_contract: float = 0.65
    fill_timeout_s: int = 45
    # Alpaca multi-leg orders: sign of limit_price for a net credit.
    # Alpaca treats negative limit_price as a credit. Check on paper before going live.
    mleg_credit_sign: int = -1


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
    mode: str = "paper"  # sim | paper | live
    broker: str = "alpaca"  # alpaca | ibkr
    data: str = ""  # blank = same as broker; or alpaca | ibkr | synthetic
    options_feed: str = "indicative"  # indicative (free) | opra (paid)
    universe: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    db_path: str = "var/flick.db"
    log_path: str = "var/flick.log"
    sim_starting_equity: float = 25_000.0
    risk_free_rate: float = 0.04
    vol_blend: float = 0.5  # forecast vol = blend*IV + (1-blend)*realized vol
    allow_new_entries: bool = True
    max_new_trades_per_cycle: int = 2
    credit_spread: CreditSpreadConfig = field(default_factory=CreditSpreadConfig)
    leaps_call: LeapsCallConfig = field(default_factory=LeapsCallConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    management: ManagementConfig = field(default_factory=ManagementConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)

    @property
    def data_source(self) -> str:
        return self.data or self.broker

    @property
    def alpaca_key(self) -> str:
        return os.environ.get("ALPACA_API_KEY", "")

    @property
    def alpaca_secret(self) -> str:
        return os.environ.get("ALPACA_SECRET_KEY", "")

    def validate(self) -> None:
        legacy = {"alpaca_paper": ("paper", "alpaca"), "alpaca_live": ("live", "alpaca")}
        if self.mode in legacy:
            self.mode, self.broker = legacy[self.mode]
        if self.mode not in {"sim", "paper", "live"}:
            raise ValueError(f"unknown mode {self.mode!r}")
        if self.broker not in {"alpaca", "ibkr"}:
            raise ValueError(f"unknown broker {self.broker!r}")
        if self.data_source not in {"alpaca", "ibkr", "synthetic"}:
            raise ValueError(f"unknown data source {self.data!r}")
        if self.data_source == "synthetic" and self.mode != "sim":
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
        setattr(obj, f.name, _build(type(current), value) if is_dataclass(current) else value)
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
