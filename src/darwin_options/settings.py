"""Settings people can change from the dashboard: strategies on/off, exit rules,
tickers to trade, and the new-entries switch.

They're stored in the journal database and laid over config.yaml at the start of
every cycle, so the trading service picks them up without a restart. Safety limits
(never naked, max loss, sizing) are deliberately not editable here.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from .config import Config
from .strategies import STRATEGIES


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str
    kind: str  # pct | mult | days | price | bool
    min: float = 0.0
    max: float = 0.0


FIELDS: dict[str, Field] = {f.key: f for f in [
    Field("take_profit_pct", "Take profit", "Close when profit reaches this share of the credit received "
          "(or of the debit paid, for debit trades).", "pct", 0.05, 1.0),
    Field("stop_loss_mult", "Stop loss", "Close when the loss reaches this multiple of the credit received.",
          "mult", 0.5, 10.0),
    Field("debit_stop_loss_pct", "Stop loss", "Close when the loss reaches this share of the debit paid.",
          "pct", 0.1, 1.0),
    Field("close_at_dte", "Close at DTE", "Close short legs at or below this many days to expiry. "
          "Shorts are always closed before the last day regardless.", "days", 1, 120),
    Field("min_short_extrinsic", "Min short time value", "Close an in-the-money short when its time value "
          "falls below this (per share), before early assignment becomes likely.", "price", 0.0, 5.0),
    Field("check_threatened", "Close when strike threatened", "Close when a short strike is at or near the "
          "money. Off for strategies whose shorts start at the money.", "bool"),
    Field("itm_buffer_pct", "Threatened buffer", "How close to the short strike the price can get before "
          "the position is closed.", "pct", 0.0, 0.10),
    Field("leaps_close_at_dte", "Sell LEAPS at DTE", "Close the whole position when the LEAPS gets this "
          "close to expiry.", "days", 30, 365),
    Field("leaps_stop_loss_pct", "LEAPS stop loss", "Close the whole position when it's down this share of "
          "the net debit paid.", "pct", 0.05, 1.0),
]}

_SHORT_GUARDS = ["close_at_dte", "min_short_extrinsic", "check_threatened", "itm_buffer_pct"]
STRATEGY_FIELDS: dict[str, list[str]] = {
    "credit_spread": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "iron_condor": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "iron_butterfly": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "calendar": ["take_profit_pct", "debit_stop_loss_pct", *_SHORT_GUARDS],
    "leaps_call": ["take_profit_pct", *_SHORT_GUARDS, "leaps_close_at_dte", "leaps_stop_loss_pct"],
    "zero_dte": ["take_profit_pct", "stop_loss_mult", "min_short_extrinsic", "check_threatened", "itm_buffer_pct"],
}

INFO = {
    "credit_spread": ("Credit spread", "Bull put or bear call vertical. Max loss = width − credit."),
    "iron_condor": ("Iron condor", "Put and call credit spreads on one expiry. Max loss = width − credit."),
    "iron_butterfly": ("Iron butterfly", "Short at-the-money straddle with wings. Max loss = wing width − credit."),
    "calendar": ("Calendar", "Short front month, long back month at one strike. Max loss = debit paid."),
    "leaps_call": ("LEAPS calls", "Short calls written against a long LEAPS call. Max loss = net debit."),
    "zero_dte": ("0DTE", "Same-day credit spreads / iron condors, closed by 15:30 New York. "
                 "Needs live quotes. Max loss = width − credit."),
}

SETTINGS_KEY = "ui_settings"
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
MAX_TICKERS = 30


class SettingsError(ValueError):
    pass


def validate(overlay: dict) -> dict:
    """Check and normalise an overlay. Raises SettingsError with a readable message."""
    out: dict = {"strategies": {}}
    if "allow_new_entries" in overlay:
        if not isinstance(overlay["allow_new_entries"], bool):
            raise SettingsError("allow_new_entries must be true or false")
        out["allow_new_entries"] = overlay["allow_new_entries"]
    tickers = overlay.get("tickers") or {}
    if not isinstance(tickers, dict):
        raise SettingsError("tickers must be a map of symbol to on/off")
    if len(tickers) > MAX_TICKERS:
        raise SettingsError(f"at most {MAX_TICKERS} tickers")
    clean_tickers: dict[str, bool] = {}
    for sym, on in tickers.items():
        sym = str(sym).strip().upper()
        if not TICKER_RE.match(sym):
            raise SettingsError(f"{sym!r} isn't a ticker (1-5 letters, like SPY)")
        if not isinstance(on, bool):
            raise SettingsError(f"{sym}: must be on or off")
        clean_tickers[sym] = on
    if clean_tickers:
        out["tickers"] = clean_tickers
    for name, s in (overlay.get("strategies") or {}).items():
        if name not in STRATEGIES:
            raise SettingsError(f"unknown strategy {name!r}")
        entry: dict = {}
        if "enabled" in s:
            if not isinstance(s["enabled"], bool):
                raise SettingsError(f"{name}: enabled must be true or false")
            entry["enabled"] = s["enabled"]
        exits: dict = {}
        for key, value in (s.get("exit") or {}).items():
            if key not in STRATEGY_FIELDS[name]:
                raise SettingsError(f"{name}: {key} isn't an exit rule for this strategy")
            f = FIELDS[key]
            if value is None:
                continue  # reset to default
            if f.kind == "bool":
                if not isinstance(value, bool):
                    raise SettingsError(f"{INFO[name][0]}: {f.label} must be on or off")
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise SettingsError(f"{INFO[name][0]}: {f.label} must be a number")
                if not (f.min <= value <= f.max):
                    raise SettingsError(f"{INFO[name][0]}: {f.label} must be between {_fmt(f, f.min)} and {_fmt(f, f.max)}")
                value = int(value) if f.kind == "days" else float(value)
            exits[key] = value
        if exits:
            entry["exit"] = exits
        if entry:
            out["strategies"][name] = entry
    return out


def _fmt(f: Field, v: float) -> str:
    return {"pct": f"{v * 100:g}%", "mult": f"{v:g}×", "days": f"{int(v)} day{'' if int(v) == 1 else 's'}", "price": f"${v:g}"}.get(f.kind, str(v))


def apply(cfg: Config, base: Config, overlay: dict) -> None:
    """Reset `cfg`'s editable settings to `base` (config.yaml), then lay the overlay on top."""
    cfg.allow_new_entries = overlay.get("allow_new_entries", base.allow_new_entries)
    cfg.universe = active_tickers(base, overlay)
    overrides = copy.deepcopy(base.management.overrides)
    for name in STRATEGIES:
        s = overlay.get("strategies", {}).get(name, {})
        getattr(cfg, name).enabled = s.get("enabled", getattr(base, name).enabled)
        if s.get("exit"):
            overrides[name] = {**overrides.get(name, {}), **s["exit"]}
    cfg.management.overrides = overrides


def all_tickers(base: Config, overlay: dict) -> list[str]:
    """config.yaml's universe first, then tickers added from the dashboard."""
    added = [t for t in (overlay.get("tickers") or {}) if t not in base.universe]
    return list(base.universe) + added


def active_tickers(base: Config, overlay: dict) -> list[str]:
    """Tickers scanned for new trades. config.yaml ones are on unless switched off; added ones as saved."""
    t = overlay.get("tickers") or {}
    return [s for s in all_tickers(base, overlay) if t.get(s, s in base.universe)]


def describe(cfg: Config, base: Config, overlay: dict, open_counts: dict[str, int],
             open_by_ticker: dict[str, int] | None = None) -> dict:
    """Everything the Strategies screen needs: current values, defaults and field metadata."""
    strategies = []
    for name in STRATEGIES:
        eff = cfg.management.for_strategy(name)
        dflt = base.management.for_strategy(name)
        changed = overlay.get("strategies", {}).get(name, {}).get("exit", {})
        strategies.append({
            "name": name, "label": INFO[name][0], "description": INFO[name][1],
            "enabled": getattr(cfg, name).enabled,
            "enabled_default": getattr(base, name).enabled,
            "open_positions": open_counts.get(name, 0),
            "exit": [{"key": k, "value": getattr(eff, k), "default": getattr(dflt, k),
                      "changed": k in changed} for k in STRATEGY_FIELDS[name]],
        })
    return {
        "allow_new_entries": cfg.allow_new_entries,
        "allow_new_entries_default": base.allow_new_entries,
        "strategies": strategies,
        "tickers": [{"symbol": t, "enabled": t in cfg.universe, "in_config": t in base.universe,
                     "open_positions": (open_by_ticker or {}).get(t, 0)} for t in all_tickers(base, overlay)],
        "fields": {k: {"label": f.label, "help": f.help, "kind": f.kind, "min": f.min, "max": f.max}
                   for k, f in FIELDS.items()},
    }
