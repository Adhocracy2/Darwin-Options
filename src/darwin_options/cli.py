"""Command line: darwin check | scan | run | loop | serve | positions | report | close."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .factory import build_engine
from .manager import ExitAction
from .report import performance


def _setup_logging(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(path)])


def run_loop(eng, interval: int, log, max_cycles: int | None = None) -> None:
    """Run cycles until stopped. SIGTERM/SIGINT (systemctl stop, a deploy) never interrupt a
    cycle: an order may be waiting for a fill. The loop exits after the current cycle ends."""
    import signal
    import threading

    stop = threading.Event()

    def request_stop(signum, _frame):
        log.info("signal %s: stopping after the current cycle", signum)
        stop.set()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    cycles = 0
    while not stop.is_set():
        try:
            log.info("cycle: %s", eng.run_once())
        except Exception:
            log.exception("cycle failed")
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        stop.wait(interval)  # wakes immediately on a stop request
    log.info("trader stopped cleanly")


def print_review(r: dict) -> None:
    print(f"{r['trades']} closed trades reviewed")
    if r.get("note"):
        print(r["note"])
    for s in r["strategies"]:
        o = s["overall"]
        print(f"\n== {s['strategy']}: {o['trades']} trades, win rate {o['win_rate']:.0%}, "
              f"avg return on risk {o['avg_return_on_risk']:+.1%}, P&L {o['total_pnl']:+.0f}")
        if m := s.get("model"):
            print(f"   model predicted {m['predicted_win_rate']:.0%} winners, actual {m['actual_win_rate']:.0%}")
        for name, groups in s["groups"].items():
            print(f"   by {name}:")
            for g in groups:
                print(f"     {g['label']:>12}  n={g['trades']:<4} win {g['win_rate']:.0%}  "
                      f"return on risk {g['avg_return_on_risk']:+.1%}")
    print("\nSuggestions:" if r["suggestions"] else "\nNo changes suggested yet.")
    for x in r["suggestions"]:
        change = f"  -> set {x['setting']} = {x['value']}" if x["setting"] else ""
        print(f" * [{x['strategy']}] {x['title']}: {x['detail']}{change}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="darwin")
    ap.add_argument("-c", "--config", default=None, help="config YAML (default: config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="test broker and data connectivity")
    s = sub.add_parser("scan", help="scan and risk-check candidates, no trading")
    s.add_argument("--top", type=int, default=15)
    r = sub.add_parser("run", help="run one cycle: manage, scan, trade")
    r.add_argument("--force", action="store_true", help="run even if the market is closed")
    lp = sub.add_parser("loop", help="run cycles forever")
    lp.add_argument("--interval", type=int, default=300)
    sv = sub.add_parser("serve", help="start the API + dashboard")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sub.add_parser("positions", help="list open positions")
    sub.add_parser("report", help="performance summary")
    sub.add_parser("review", help="review closed trades and suggest strategy changes")
    sub.add_parser("ibkr-check", help="test the IBKR paper-trading path: connect, data, place + cancel an unfillable order")
    cl = sub.add_parser("close", help="close a position now")
    cl.add_argument("position_id")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    _setup_logging(cfg.log_path)
    log = logging.getLogger("darwin")

    if args.cmd == "serve":
        import uvicorn

        from .api.app import create_app

        cfg.ibkr.client_id += 1  # the trader service holds client_id; IBKR needs a distinct one
        uvicorn.run(create_app(cfg), host=args.host, port=args.port)
        return

    if args.cmd == "ibkr-check":
        from .ibkr_check import run_check

        cfg.ibkr.client_id += 2  # the trader holds client_id and the dashboard client_id + 1
        steps = run_check(cfg)
        for st in steps:
            print(f"[{'PASS' if st.ok else 'FAIL'}] {st.name}: {st.detail}")
        ok = all(st.ok for st in steps) and len(steps) >= 5
        print("\nIBKR paper trading path works." if ok else "\nIBKR check did not pass; see the failures above.")
        raise SystemExit(0 if ok else 1)

    if args.cmd == "review":  # reads the journal only; no broker connection needed
        from .review import review
        from .store import Store

        print_review(review(Store(cfg.db_path).positions(None)))
        return

    eng = build_engine(cfg)
    if args.cmd == "check":
        acct = eng.broker.account()
        print(f"broker={eng.broker.name} equity={acct.equity:.2f} bp={acct.buying_power:.2f} open={acct.is_market_open}")
        sym = cfg.universe[0]
        print(f"{sym} spot={eng.md.spot(sym):.2f}")
    elif args.cmd == "scan":
        rows = eng.preview()
        for row in rows[: args.top]:
            flag = "OK " if row["approved"] else "-- "
            print(f"{flag}{row['description']:<60} credit={row['net_credit']:>6.2f} maxloss={row['max_loss']:>7.0f} "
                  f"pop={row['pop']:.0%} ev={row['expected_value']:>7.2f} score={row['score']:.3f} qty={row['qty']}")
            if row["reasons"]:
                print("     " + "; ".join(row["reasons"]))
        print(f"{len(rows)} candidates")
    elif args.cmd == "run":
        print(json.dumps(eng.run_once(force=args.force), indent=2))
    elif args.cmd == "loop":
        run_loop(eng, args.interval, log)
    elif args.cmd == "positions":
        for p in eng.store.positions("open"):
            legs = ", ".join(f"{l.qty:+d} {l.symbol} @ {l.entry_price:.2f}" for l in p.open_legs)
            print(f"{p.id} {p.strategy} {p.underlying} max_loss={p.max_loss:.0f} | {legs}")
    elif args.cmd == "report":
        print(json.dumps(performance(eng.store.positions(None), eng.store.snapshots()), indent=2, default=str))
    elif args.cmd == "close":
        p = eng.store.get_position(args.position_id)
        if not p or p.status != "open":
            raise SystemExit("no such open position")
        print("closed" if eng.close_legs(p, ExitAction(p.id, [l.symbol for l in p.open_legs], "manual close"))
              else "not filled")


if __name__ == "__main__":
    main()
