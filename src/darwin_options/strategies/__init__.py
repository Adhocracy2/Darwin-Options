"""Strategy types and the configured strategies built from them.

A *type* is the trade shape (credit spread, iron condor, ...). A *strategy* is a type plus
its own settings, under its own id. The five built-in strategies use the type name as id
and the matching config section. More can be added (`custom_strategies`, or the dashboard),
e.g. an iron condor that trades options expiring today. Same-day (0DTE) is just a strategy
whose days to expiry are 0 to 0; see sameday.py for the rules that then apply.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, fields, replace

from .calendar import Calendar
from .credit_spread import CreditSpread
from .iron_butterfly import IronButterfly
from .iron_condor import IronCondor
from .leaps_call import LeapsCall
from . import sameday

TYPES = {c.type: c for c in (CreditSpread, IronCondor, IronButterfly, Calendar, LeapsCall)}
STRATEGIES = TYPES  # older name

INFO = {
    "credit_spread": ("Credit spread", "Bull put or bear call vertical. Max loss = width − credit."),
    "iron_condor": ("Iron condor", "Put and call credit spreads on one expiry. Max loss = width − credit."),
    "iron_butterfly": ("Iron butterfly", "Short at-the-money straddle with wings. Max loss = wing width − credit."),
    "calendar": ("Calendar", "Short front month, long back month at one strike. Max loss = debit paid."),
    "leaps_call": ("LEAPS calls", "Short calls written against a long LEAPS call. Max loss = net debit."),
}

# Types that can trade options expiring today. Calendars and LEAPS calls need a later expiry.
SAME_DAY_TYPES = ("credit_spread", "iron_condor", "iron_butterfly")
# Entry settings a new same-day strategy starts from (over the type's own settings).
SAME_DAY_ENTRY = {
    "credit_spread": {"dte_min": 0, "dte_max": 0, "short_delta_min": 0.05, "short_delta_max": 0.15,
                      "widths": [2.0, 3.0, 5.0], "min_credit_pct_of_width": 0.05, "min_otm_pct": 0.004},
    "iron_condor": {"dte_min": 0, "dte_max": 0, "short_delta_min": 0.05, "short_delta_max": 0.15,
                    "widths": [2.0, 3.0, 5.0], "min_credit_pct_of_width": 0.05, "min_otm_pct": 0.004},
    "iron_butterfly": {"dte_min": 0, "dte_max": 0, "wing_widths": [2.0, 3.0, 5.0]},
}


@dataclass
class Instance:
    id: str
    type: str
    label: str
    enabled: bool
    params: object  # the type's config dataclass, with this strategy's entry settings
    builtin: bool
    entry: dict  # entry settings that differ from the type's section (custom only)
    exit: dict  # exit rules from the definition (custom only; dashboard changes go via overrides)

    @property
    def same_day(self) -> bool:
        return getattr(self.params, "dte_max", None) == 0


def params_for(cfg, type_: str, entry: dict | None):
    base = getattr(cfg, type_)
    known = {f.name for f in fields(base)}
    return replace(copy.deepcopy(base), **{k: v for k, v in (entry or {}).items() if k in known})


def instances(cfg) -> list[Instance]:
    """Every configured strategy, on or off: the built-in five, then added ones."""
    out = [Instance(t, t, INFO[t][0], getattr(cfg, t).enabled, getattr(cfg, t), True, {}, {}) for t in TYPES]
    seen = set(TYPES)
    for d in cfg.custom_strategies or []:
        t = d.get("type")
        if t not in TYPES or not d.get("id") or d["id"] in seen:
            continue
        seen.add(d["id"])
        out.append(Instance(d["id"], t, d.get("label") or d["id"], bool(d.get("enabled", True)),
                            params_for(cfg, t, d.get("entry")), False, dict(d.get("entry") or {}),
                            dict(d.get("exit") or {})))
    return out


def find(cfg, sid: str) -> Instance | None:
    return next((i for i in instances(cfg) if i.id == sid), None)


def type_of(cfg, sid: str) -> str:
    """Strategy type for a strategy id (positions record the id). Unknown ids map to themselves."""
    i = find(cfg, sid)
    return i.type if i else sid


def build(cfg) -> list:
    return [TYPES[i.type](i.id, None if i.builtin else i.params, i.same_day) for i in instances(cfg) if i.enabled]


def sync_exit_rules(cfg) -> None:
    """Give every strategy its exit rules in management.overrides: the type's defaults, then
    same-day defaults if it trades today's expiry, then its own. Same-day strategies always
    get a close_by_time; others never do."""
    ov = cfg.management.overrides
    for i in instances(cfg):
        rules = {**ov.get(i.type, {})} if not i.builtin else {}
        if i.same_day:
            rules = {**sameday.EXIT_DEFAULTS, "close_by_time": cfg.same_day.close_by_time, **rules}
        rules.update(i.exit)
        rules.update(ov.get(i.id, {}))
        if i.same_day:
            rules["close_at_dte"] = 0
            rules.setdefault("close_by_time", cfg.same_day.close_by_time)
        else:
            rules.pop("close_by_time", None)
        ov[i.id] = rules
