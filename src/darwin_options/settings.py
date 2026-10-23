"""Settings people can change from the dashboard: strategies (add, switch on/off, entry
settings, exit rules), tickers to trade, and the new-entries switch.

They're stored in the journal database and laid over config.yaml at the start of
every cycle, so the trading service picks them up without a restart. Safety limits
(never naked, max loss, sizing) are deliberately not editable here.

Overlay format:
    {"allow_new_entries": bool,
     "tickers": {"SPY": true, "XLF": true},
     "strategies": {"<id>": {"enabled": bool, "entry": {...}, "exit": {...},
                             "type": "...", "label": "..."}}}   # type + label: added strategies
Built-in strategies (ids = type names) and ones from config.yaml send only what changed.
Strategies added from the dashboard are always sent in full; leaving one out deletes it.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, replace

from .config import Config
from .strategies import INFO, SAME_DAY_ENTRY, SAME_DAY_TYPES, TYPES, instances, params_for, sameday, sync_exit_rules


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str
    kind: str  # pct | mult | days | price | bool | delta | widths | time
    min: float = 0.0
    max: float = 0.0


FIELDS: dict[str, Field] = {f.key: f for f in [
    # exit rules
    Field("take_profit_pct", "Take profit", "Close when profit reaches this share of the credit received "
          "(or of the debit paid, for debit trades).", "pct", 0.05, 1.0),
    Field("stop_loss_mult", "Stop loss", "Close when the loss reaches this multiple of the credit received.",
          "mult", 0.5, 10.0),
    Field("debit_stop_loss_pct", "Stop loss", "Close when the loss reaches this share of the debit paid.",
          "pct", 0.1, 1.0),
    Field("close_at_dte", "Close at DTE", "Close short legs at or below this many days to expiry. "
          "Shorts are always closed before the last day regardless.", "days", 1, 120),
    Field("close_by_time", "Close by", "Same-day trades: close everything at this New York time. "
          "Options stop trading at 16:00.", "time", 9 * 60 + 45, 15 * 60 + 50),
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
    # entry settings
    Field("dte_min", "Days to expiry from", "Only open trades expiring at least this many days out. "
          "0 to 0 = options expiring today (0DTE).", "days", 0, 400),
    Field("dte_max", "Days to expiry to", "Only open trades expiring at most this many days out.", "days", 0, 400),
    Field("short_delta_min", "Short delta from", "Delta of the sold option: roughly its chance of finishing "
          "in the money. Lower = further out, smaller credit, wins more often.", "delta", 0.01, 0.60),
    Field("short_delta_max", "Short delta to", "Upper end of the short option's delta.", "delta", 0.01, 0.60),
    Field("widths", "Widths", "Distance between the sold and bought strikes, in dollars. Max loss per "
          "contract ≈ width × 100 − credit.", "widths", 0.5, 50),
    Field("wing_widths", "Wing widths", "Distance from the sold strike to each bought wing, in dollars.",
          "widths", 0.5, 50),
    Field("min_credit_pct_of_width", "Min credit", "Skip trades collecting less than this share of the width.",
          "pct", 0.01, 0.90),
    Field("min_otm_pct", "Min distance out of the money", "The sold strike must be at least this far from "
          "the price.", "pct", 0.0, 0.20),
    Field("front_dte_min", "Front month from", "Days to expiry of the sold option.", "days", 1, 120),
    Field("front_dte_max", "Front month to", "Days to expiry of the sold option.", "days", 1, 120),
    Field("back_gap_min", "Back month gap from", "Days between the sold and bought expiries.", "days", 7, 200),
    Field("back_gap_max", "Back month gap to", "Days between the sold and bought expiries.", "days", 7, 200),
    Field("leaps_dte_min", "LEAPS days to expiry", "Minimum days to expiry of the long LEAPS call.", "days", 180, 800),
    Field("leaps_delta_min", "LEAPS min delta", "How deep in the money the LEAPS must be.", "delta", 0.5, 0.95),
    Field("short_dte_min", "Short call days from", "Days to expiry of the calls sold against the LEAPS.",
          "days", 1, 120),
    Field("short_dte_max", "Short call days to", "Days to expiry of the calls sold against the LEAPS.",
          "days", 1, 120),
]}

_SHORT_GUARDS = ["close_at_dte", "min_short_extrinsic", "check_threatened", "itm_buffer_pct"]
EXIT_FIELDS: dict[str, list[str]] = {
    "credit_spread": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "iron_condor": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "iron_butterfly": ["take_profit_pct", "stop_loss_mult", *_SHORT_GUARDS],
    "calendar": ["take_profit_pct", "debit_stop_loss_pct", *_SHORT_GUARDS],
    "leaps_call": ["take_profit_pct", *_SHORT_GUARDS, "leaps_close_at_dte", "leaps_stop_loss_pct"],
}
_SPREAD_ENTRY = ["dte_min", "dte_max", "short_delta_min", "short_delta_max", "widths",
                 "min_credit_pct_of_width", "min_otm_pct"]
ENTRY_FIELDS: dict[str, list[str]] = {
    "credit_spread": _SPREAD_ENTRY,
    "iron_condor": _SPREAD_ENTRY,
    "iron_butterfly": ["dte_min", "dte_max", "wing_widths", "min_credit_pct_of_width"],
    "calendar": ["front_dte_min", "front_dte_max", "back_gap_min", "back_gap_max"],
    "leaps_call": ["leaps_dte_min", "leaps_delta_min", "short_dte_min", "short_dte_max",
                   "short_delta_min", "short_delta_max"],
}
STRATEGY_FIELDS = EXIT_FIELDS  # older name


def exit_fields(type_: str, same_day: bool) -> list[str]:
    keys = EXIT_FIELDS[type_]
    return [("close_by_time" if k == "close_at_dte" else k) for k in keys] if same_day else keys


SETTINGS_KEY = "ui_settings"
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_TICKERS = 30
MAX_STRATEGIES = 20


class SettingsError(ValueError):
    pass


def _fmt(f: Field, v) -> str:
    if f.kind == "time":
        return f"{int(v) // 60:02d}:{int(v) % 60:02d}" if isinstance(v, (int, float)) else str(v)
    return {"pct": f"{v * 100:g}%", "mult": f"{v:g}×", "days": f"{int(v)} day{'' if int(v) == 1 else 's'}",
            "price": f"${v:g}", "widths": f"${v:g}"}.get(f.kind, f"{v:g}" if isinstance(v, float) else str(v))


def _value(f: Field, value, who: str):
    """Check one field value; returns it normalised."""
    if f.kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"{who}: {f.label} must be on or off")
        return value
    if f.kind == "time":
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(value))
        mins = int(m.group(1)) * 60 + int(m.group(2)) if m else -1
        if not m or not (f.min <= mins <= f.max):
            raise SettingsError(f"{who}: {f.label} must be a New York time from {_fmt(f, f.min)} to {_fmt(f, f.max)}")
        return f"{mins // 60:02d}:{mins % 60:02d}"
    if f.kind == "widths":
        if not isinstance(value, list) or not value or len(value) > 6:
            raise SettingsError(f"{who}: {f.label} needs 1 to 6 values")
        out = []
        for v in value:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (f.min <= v <= f.max):
                raise SettingsError(f"{who}: each of {f.label} must be between {_fmt(f, f.min)} and {_fmt(f, f.max)}")
            out.append(float(v))
        return sorted(set(out))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{who}: {f.label} must be a number")
    if not (f.min <= value <= f.max):
        raise SettingsError(f"{who}: {f.label} must be between {_fmt(f, f.min)} and {_fmt(f, f.max)}")
    return int(value) if f.kind == "days" else float(value)


def _check_params(p, type_: str, who: str) -> None:
    """Cross-field checks on a strategy's effective entry settings."""
    def pair(lo: str, hi: str):
        if hasattr(p, lo) and getattr(p, lo) > getattr(p, hi):
            raise SettingsError(f"{who}: {FIELDS[lo].label} can't be above {FIELDS[hi].label.split(' from')[0]} to")
    for lo, hi in [("dte_min", "dte_max"), ("short_delta_min", "short_delta_max"), ("front_dte_min", "front_dte_max"),
                   ("back_gap_min", "back_gap_max"), ("short_dte_min", "short_dte_max")]:
        pair(lo, hi)
    if hasattr(p, "dte_max"):
        if p.dte_max == 0 and type_ not in SAME_DAY_TYPES:
            raise SettingsError(f"{who}: {INFO[type_][0]} can't trade options expiring today")
        if p.dte_min == 0 and p.dte_max > 0:
            raise SettingsError(f"{who}: days to expiry: use 0 to 0 for same-day (0DTE) trades, or start at 1")


def validate(overlay: dict, base: Config | None = None) -> dict:
    """Check and normalise an overlay. Raises SettingsError with a readable message."""
    base = base or Config()
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

    yaml_customs = {d.get("id"): d for d in base.custom_strategies or []}
    strategies = overlay.get("strategies") or {}
    if len(strategies) > MAX_STRATEGIES + len(TYPES):
        raise SettingsError(f"at most {MAX_STRATEGIES} added strategies")
    labels: set[str] = {INFO[t][0].lower() for t in TYPES}
    for sid, s in strategies.items():
        if not isinstance(s, dict):
            raise SettingsError(f"{sid}: settings must be a map")
        entry: dict = {}
        if sid in TYPES:
            type_, who, prior = sid, INFO[sid][0], {}
        elif sid in yaml_customs:
            type_, who, prior = yaml_customs[sid]["type"], yaml_customs[sid].get("label") or sid, yaml_customs[sid].get("entry") or {}
        else:  # added from the dashboard
            if not ID_RE.match(str(sid)):
                raise SettingsError(f"{sid!r}: strategy ids are lower-case letters, digits and _")
            type_ = s.get("type")
            if type_ not in TYPES:
                raise SettingsError(f"{sid}: pick a strategy type")
            who = str(s.get("label") or "").strip()
            if not who or len(who) > 40:
                raise SettingsError(f"{sid}: give the strategy a name (up to 40 characters)")
            if who.lower() in labels:
                raise SettingsError(f"{who}: another strategy already has this name")
            labels.add(who.lower())
            entry.update(type=type_, label=who)
            prior = {}
        if "enabled" in s:
            if not isinstance(s["enabled"], bool):
                raise SettingsError(f"{who}: enabled must be true or false")
            entry["enabled"] = s["enabled"]
        entries: dict = {}
        for key, value in (s.get("entry") or {}).items():
            if key not in ENTRY_FIELDS[type_]:
                raise SettingsError(f"{who}: {key} isn't an entry setting for this strategy")
            if value is not None:
                entries[key] = _value(FIELDS[key], value, who)
        params = params_for(base, type_, {**prior, **entries})
        _check_params(params, type_, who)
        same_day = getattr(params, "dte_max", None) == 0
        exits: dict = {}
        for key, value in (s.get("exit") or {}).items():
            if key not in exit_fields(type_, same_day):
                if key in ("close_at_dte", "close_by_time"):
                    continue  # left over from switching between same-day and multi-day
                raise SettingsError(f"{who}: {key} isn't an exit rule for this strategy")
            if value is not None:
                exits[key] = _value(FIELDS[key], value, who)
        if entries:
            entry["entry"] = entries
        if exits:
            entry["exit"] = exits
        if entry:
            out["strategies"][sid] = entry
    return out


def active_tickers(base: Config, overlay: dict) -> list[str]:
    """Tickers scanned for new trades. config.yaml ones are on unless switched off; added ones as saved."""
    t = overlay.get("tickers") or {}
    return [s for s in all_tickers(base, overlay) if t.get(s, s in base.universe)]


def all_tickers(base: Config, overlay: dict) -> list[str]:
    """config.yaml's universe first, then tickers added from the dashboard."""
    added = [t for t in (overlay.get("tickers") or {}) if t not in base.universe]
    return list(base.universe) + added


def apply(cfg: Config, base: Config, overlay: dict) -> None:
    """Reset `cfg`'s editable settings to `base` (config.yaml), then lay the overlay on top."""
    cfg.allow_new_entries = overlay.get("allow_new_entries", base.allow_new_entries)
    cfg.universe = active_tickers(base, overlay)
    overrides = copy.deepcopy(base.management.overrides)
    customs = copy.deepcopy(base.custom_strategies or [])
    by_id = {d.get("id"): d for d in customs}
    for t in TYPES:
        setattr(cfg, t, copy.deepcopy(getattr(base, t)))
    for sid, s in (overlay.get("strategies") or {}).items():
        if sid in TYPES:
            sec = getattr(cfg, sid)
            if "enabled" in s:
                sec.enabled = s["enabled"]
            if s.get("entry"):
                setattr(cfg, sid, replace(sec, **s["entry"]))
        else:
            d = by_id.get(sid)
            if d is None:
                if "type" not in s:
                    continue
                d = {"id": sid, "type": s["type"], "label": s.get("label", sid), "enabled": True, "entry": {}, "exit": {}}
                customs.append(d)
                by_id[sid] = d
            if "label" in s:
                d["label"] = s["label"]
            if "enabled" in s:
                d["enabled"] = s["enabled"]
            d["entry"] = {**(d.get("entry") or {}), **(s.get("entry") or {})}
        if s.get("exit"):
            overrides[sid] = {**overrides.get(sid, {}), **s["exit"]}
    cfg.custom_strategies = customs
    cfg.management.overrides = overrides
    sync_exit_rules(cfg)


def _defaults(cfg: Config, base: Config) -> Config:
    """`cfg`'s strategies with config.yaml's entry and exit rules, to show what 'default' means."""
    ref = copy.deepcopy(cfg)
    ref.management.overrides = copy.deepcopy(base.management.overrides)
    yaml_ids = {d.get("id") for d in base.custom_strategies or []}
    for d in ref.custom_strategies:
        if d["id"] in yaml_ids:
            d["entry"] = next(x for x in base.custom_strategies if x.get("id") == d["id"]).get("entry") or {}
        else:  # added from the dashboard: the type's settings (same-day ones if it trades today)
            same_day = (d.get("entry") or {}).get("dte_max") == 0 and d["type"] in SAME_DAY_TYPES
            d["entry"] = dict(SAME_DAY_ENTRY[d["type"]]) if same_day else {}
    for t in TYPES:
        setattr(ref, t, replace(copy.deepcopy(getattr(base, t)), enabled=getattr(cfg, t).enabled))
    sync_exit_rules(ref)
    return ref


def _jsonable(v):
    return list(v) if isinstance(v, (list, tuple)) else v


def _type_info(base: Config, t: str) -> dict:
    """What a newly added strategy of type `t` starts with (multi-day and same-day versions)."""
    m = base.management
    regular = replace(m, **base.management.overrides.get(t, {}))
    out = {"type": t, "label": INFO[t][0], "description": INFO[t][1], "same_day_ok": t in SAME_DAY_TYPES,
           "entry": {k: _jsonable(getattr(getattr(base, t), k)) for k in ENTRY_FIELDS[t]},
           "exit": {k: getattr(regular, k) for k in exit_fields(t, False)},
           "same_day_entry": None, "same_day_exit": None}
    if t in SAME_DAY_TYPES:
        sd = replace(m, **{**sameday.EXIT_DEFAULTS, "close_by_time": base.same_day.close_by_time,
                           **base.management.overrides.get(t, {})})
        out["same_day_entry"] = {**out["entry"], **SAME_DAY_ENTRY[t]}
        out["same_day_exit"] = {k: getattr(sd, k) for k in exit_fields(t, True)}
    return out


def describe(cfg: Config, base: Config, overlay: dict, open_counts: dict[str, int],
             open_by_ticker: dict[str, int] | None = None) -> dict:
    """Everything the Strategies screen needs: current values, defaults and field metadata."""
    ref = _defaults(cfg, base)
    ref_by_id = {i.id: i for i in instances(ref)}
    over = overlay.get("strategies") or {}
    yaml_ids = {d.get("id") for d in base.custom_strategies or []}
    strategies = []
    for inst in instances(cfg):
        dflt = ref_by_id[inst.id]
        eff_m = cfg.management.for_strategy(inst.id)
        dflt_m = ref.management.for_strategy(inst.id)
        changed_exit = (over.get(inst.id) or {}).get("exit", {})
        enabled_default = getattr(base, inst.type).enabled if inst.builtin else (
            next((bool(d.get("enabled", True)) for d in base.custom_strategies or [] if d.get("id") == inst.id), True))
        strategies.append({
            "name": inst.id, "type": inst.type, "label": inst.label, "description": INFO[inst.type][1],
            "builtin": inst.builtin, "removable": not inst.builtin and inst.id not in yaml_ids,
            "same_day": inst.same_day, "same_day_ok": inst.type in SAME_DAY_TYPES,
            "enabled": inst.enabled, "enabled_default": enabled_default,
            "open_positions": open_counts.get(inst.id, 0),
            "entry": [{"key": k, "value": _jsonable(getattr(inst.params, k)), "default": _jsonable(getattr(dflt.params, k)),
                       "changed": getattr(inst.params, k) != getattr(dflt.params, k)} for k in ENTRY_FIELDS[inst.type]],
            "exit": [{"key": k, "value": getattr(eff_m, k), "default": getattr(dflt_m, k),
                      "changed": k in changed_exit} for k in exit_fields(inst.type, inst.same_day)],
        })
    return {
        "allow_new_entries": cfg.allow_new_entries,
        "allow_new_entries_default": base.allow_new_entries,
        "strategies": strategies,
        "types": [_type_info(base, t) for t in TYPES],
        "same_day": {"entry_start": cfg.same_day.entry_start, "entry_end": cfg.same_day.entry_end,
                     "close_by_time": cfg.same_day.close_by_time,
                     "live_data": cfg.data != "ibkr" or cfg.ibkr.market_data_type == 1,
                     "max_risk_per_trade_pct": cfg.same_day.max_risk_per_trade_pct,
                     "max_trades_per_day": cfg.same_day.max_trades_per_day, "max_open": cfg.same_day.max_open,
                     "daily_loss_limit_pct": cfg.same_day.daily_loss_limit_pct,
                     "profit_targets": cfg.same_day.profit_targets, "lock_profit_at": cfg.same_day.lock_profit_at,
                     "lock_profit_floor": cfg.same_day.lock_profit_floor, "losing_time_stop": cfg.same_day.losing_time_stop,
                     "skip_dates": [str(d) for d in cfg.same_day.skip_dates]},
        "tickers": [{"symbol": t, "enabled": t in cfg.universe, "in_config": t in base.universe,
                     "open_positions": (open_by_ticker or {}).get(t, 0)} for t in all_tickers(base, overlay)],
        "fields": {k: {"label": f.label, "help": f.help, "kind": f.kind, "min": f.min, "max": f.max}
                   for k, f in FIELDS.items()},
    }
