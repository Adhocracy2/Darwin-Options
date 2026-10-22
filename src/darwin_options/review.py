"""Trade review: what's working, what isn't, and which settings to change.

Works on closed positions in the journal. Every trade carries the scanner's view at
entry (delta, DTE, width, credit, IV, realized vol, model POP/EV), so results can be
grouped by those and compared. Suggestions are made only when a group has enough
trades and the difference is larger than noise; otherwise the review says so.

Return on risk (P&L / max loss) is used throughout so trades of different sizes compare fairly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import Position

MIN_TRADES = 20  # per group before drawing conclusions
ATM_STRATEGIES = {"iron_butterfly", "calendar"}  # short strikes start at the money
MIN_T = 2.0  # difference in mean return on risk must be ~2 standard errors


@dataclass
class Group:
    label: str
    n: int
    win_rate: float
    avg_ror: float  # mean return on risk
    total_pnl: float
    se: float  # standard error of avg_ror

    def as_dict(self) -> dict:
        return {"label": self.label, "trades": self.n, "win_rate": round(self.win_rate, 3),
                "avg_return_on_risk": round(self.avg_ror, 4), "total_pnl": round(self.total_pnl, 2)}


@dataclass
class Suggestion:
    strategy: str
    title: str
    detail: str
    setting: str | None = None  # config path to change, if any
    value: object = None
    evidence: dict = field(default_factory=dict)


def _ror(p: Position) -> float:
    return p.realized_pnl() / p.max_loss if p.max_loss else 0.0


def _group(label: str, ps: list[Position]) -> Group | None:
    if not ps:
        return None
    r = [_ror(p) for p in ps]
    mean = sum(r) / len(r)
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1)) if len(r) > 1 else float("inf")
    return Group(label, len(r), sum(p.realized_pnl() > 0 for p in ps) / len(ps), mean,
                 sum(p.realized_pnl() for p in ps), sd / math.sqrt(len(r)) if len(r) > 1 else float("inf"))


def _bucket(ps: list[Position], key, edges: list[float], fmt) -> list[tuple[tuple[float, float], Group]]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        members = [p for p in ps if (v := key(p)) is not None and lo <= v < hi]
        if g := _group(fmt(lo, hi), members):
            out.append(((lo, hi), g))
    return out


def _significant(a: Group, b: Group) -> bool:
    if a.n < MIN_TRADES or b.n < MIN_TRADES:
        return False
    se = math.sqrt(a.se ** 2 + b.se ** 2)
    return se > 0 and abs(a.avg_ror - b.avg_ror) / se >= MIN_T


def _note(p: Position, key: str):
    v = (p.entry_notes or {}).get("notes", {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _entry(p: Position, key: str):
    v = (p.entry_notes or {}).get(key)
    return float(v) if isinstance(v, (int, float)) else None


def review(positions: list[Position]) -> dict:
    closed = [p for p in positions if p.status == "closed" and p.max_loss > 0]
    report: dict = {"trades": len(closed), "min_trades_per_group": MIN_TRADES, "strategies": [], "suggestions": []}
    suggestions: list[Suggestion] = []

    for strat in sorted({p.strategy for p in closed}):
        ps = [p for p in closed if p.strategy == strat]
        overall = _group("all", ps)
        section: dict = {"strategy": strat, "overall": overall.as_dict(), "groups": {}}

        # Model calibration: predicted probability of profit vs what happened.
        pops = [v for p in ps if (v := _entry(p, "pop")) is not None]
        if pops:
            predicted = sum(pops) / len(pops)
            section["model"] = {"predicted_win_rate": round(predicted, 3), "actual_win_rate": round(overall.win_rate, 3)}
            if overall.n >= MIN_TRADES * 2 and overall.win_rate < predicted - 0.10:
                suggestions.append(Suggestion(
                    strat, "The model is too optimistic",
                    f"It predicted {predicted:.0%} winners but {overall.win_rate:.0%} won. Weight implied "
                    f"volatility more heavily so fewer marginal trades pass.", "vol_blend", 0.75,
                    section["model"]))

        dims = {
            "short delta": (lambda p: abs(_note(p, "short_delta") or _note(p, "put_delta") or 0) or None,
                            [0.0, 0.15, 0.20, 0.25, 0.35], lambda a, b: f"{a:.2f}–{b:.2f}"),
            "days to expiry at entry": (lambda p: _note(p, "dte"), [0, 30, 38, 46, 400], lambda a, b: f"{a:.0f}+" if b >= 400 else f"{a:.0f}–{b:.0f}"),
            "credit as % of width": (lambda p: (_entry(p, "net_credit") or 0) / _note(p, "width")
                                     if _note(p, "width") else None,
                                     [0, 0.2, 0.25, 0.33, 1.0], lambda a, b: f"{a:.0%}–{b:.0%}"),
            "implied ÷ realized vol": (lambda p: (_note(p, "iv") / _note(p, "rv"))
                                       if _note(p, "iv") and _note(p, "rv") else None,
                                       [0, 1.0, 1.2, 1.5, 10], lambda a, b: f"{a:.1f}+" if b >= 10 else f"{a:.1f}–{b:.1f}"),
        }
        for name, (key, edges, fmt) in dims.items():
            buckets = _bucket(ps, key, edges, fmt)
            if not buckets:
                continue
            section["groups"][name] = [g.as_dict() for _, g in buckets]
            best = max(buckets, key=lambda b: b[1].avg_ror)
            worst = min(buckets, key=lambda b: b[1].avg_ror)
            if best is worst or not _significant(best[1], worst[1]) or worst[1].avg_ror >= 0:
                continue
            ev = {"better": best[1].as_dict(), "worse": worst[1].as_dict()}
            (lo, hi) = worst[0]
            if name == "short delta":
                if lo >= best[0][1]:  # higher deltas are the losers
                    suggestions.append(Suggestion(strat, "Sell further out of the money",
                                                  f"Shorts at delta {worst[1].label} lost money; {best[1].label} did best.",
                                                  f"{strat}.short_delta_max", round(lo, 2), ev))
                else:
                    suggestions.append(Suggestion(strat, "Sell a little closer to the money",
                                                  f"Shorts at delta {worst[1].label} lost money; {best[1].label} did best.",
                                                  f"{strat}.short_delta_min", round(hi, 2), ev))
            elif name == "credit as % of width":
                if hi <= best[0][0]:
                    suggestions.append(Suggestion(strat, "Demand more credit",
                                                  f"Trades collecting {worst[1].label} of the width lost money; "
                                                  f"{best[1].label} did best.", f"{strat}.min_credit_pct_of_width",
                                                  round(hi, 2), ev))
            elif name == "implied ÷ realized vol":
                if hi <= best[0][0]:
                    suggestions.append(Suggestion(strat, "Only sell when options are expensive",
                                                  f"Entries with implied/realized vol of {worst[1].label} lost money; "
                                                  f"{best[1].label} did best. Raise the volatility weighting or add an "
                                                  f"IV/RV floor.", "vol_blend", 0.3, ev))
            elif name == "days to expiry at entry":
                rng = (best[0][0], best[0][1])
                suggestions.append(Suggestion(strat, "Change the expiry window",
                                              f"Entries at {worst[1].label} DTE lost money; {best[1].label} did best.",
                                              f"{strat}.dte_min/dte_max", [int(rng[0]), int(min(rng[1], 60))], ev))

        # Exits: are stop losses doing the damage?
        by_reason: dict[str, list[Position]] = {}
        for p in ps:
            by_reason.setdefault((p.exit_reason or "").split(":")[0], []).append(p)
        section["exits"] = {k: _group(k, v).as_dict() for k, v in by_reason.items()}
        losses = -sum(min(0.0, p.realized_pnl()) for p in ps)
        # Which exit is doing the damage? Early exits (stop, threatened strike) that cause most
        # losses usually mean entries sit too close to the money.
        for reason in ("stop loss", "short strike threatened"):
            hit = by_reason.get(reason, [])
            hit_loss = -sum(min(0.0, p.realized_pnl()) for p in hit)
            if overall.n < MIN_TRADES or losses <= 0 or hit_loss / losses <= 0.6 or overall.avg_ror >= 0:
                continue
            share = f"'{reason}' exits caused {hit_loss / losses:.0%} of all losses and the strategy is losing overall."
            if strat in ATM_STRATEGIES:
                advice = (f"{share} This strategy starts at the money, so it needs the price to stay put; "
                          f"consider pausing it while the market is trending.")
                suggestions.append(Suggestion(strat, f"Most losses come from '{reason}' exits", advice,
                                              f"{strat}.enabled", False, {"share": round(hit_loss / losses, 3)}))
            else:
                advice = (f"{share} The price reaches the short strike too often: sell further out of the "
                          f"money (lower delta) rather than loosening the exit. As a rule of thumb the chance "
                          f"of touching a strike is about twice its delta.")
                suggestions.append(Suggestion(strat, f"Most losses come from '{reason}' exits", advice,
                                              f"{strat}.short_delta_max", 0.16, {"share": round(hit_loss / losses, 3)}))
        report["strategies"].append(section)

    # Execution quality: how far fills landed from the mid price at order time.
    fq = [p.entry_notes["fill_quality"] for p in positions
          if (p.entry_notes or {}).get("fill_quality", {}).get("slippage") is not None]
    if fq:
        slip = [f["slippage"] for f in fq]
        mids = [abs(f["mid"]) for f in fq if f.get("mid")]
        report["execution"] = {
            "orders": len(fq),
            "avg_slippage_per_share": round(sum(slip) / len(slip), 4),
            "avg_slippage_pct_of_mid": round(sum(slip) / sum(mids), 4) if mids and sum(mids) else None,
        }
        avg_pct = report["execution"]["avg_slippage_pct_of_mid"]
        if len(fq) >= 10 and avg_pct is not None and avg_pct > 0.10:
            suggestions.append(Suggestion(
                "all", "Fills are costing too much",
                f"Fills average {avg_pct:.0%} worse than the mid price. Trade only the most liquid "
                f"underlyings and widths, or tighten the bid/ask filter.",
                "filters.max_leg_spread_pct", 0.10, report["execution"]))

    if len(closed) < MIN_TRADES:
        report["note"] = (f"Only {len(closed)} closed trades. Groups need {MIN_TRADES}+ trades before the review "
                          f"suggests changes; until then, treat the tables as anecdotes.")
    report["suggestions"] = [s.__dict__ for s in suggestions]
    return report
