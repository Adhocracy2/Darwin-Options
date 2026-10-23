"""Recording market data and replaying it through the engine."""
from datetime import date, datetime, timedelta, timezone

from darwin_options.broker.sim import SimBroker
from darwin_options.data.synthetic import SyntheticMarketData
from darwin_options.engine import Engine
from darwin_options.recorder import Recorder, RecordingMarketData, ReplayMarketData, summary
from darwin_options.replay import replay
from darwin_options.store import Store

TODAY = date(2026, 10, 21)
T0 = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)  # 11:00 New York


def record_cycles(cfg, n=3, step_min=5):
    """Run n scans against the synthetic market while recording; returns the recording times."""
    md = SyntheticMarketData(today=TODAY)
    rec = Recorder(cfg.record.path)
    rmd = RecordingMarketData(md, rec)
    store = Store(cfg.db_path)
    eng = Engine(cfg, rmd, SimBroker(rmd, store, 25_000, always_open=True), store)
    times = []
    for i in range(n):
        t = T0 + timedelta(minutes=step_min * i)
        rec.now = lambda t=t: t.isoformat(timespec="seconds")
        md.spots["SPY"] = 500 + i  # price moves a little each cycle
        eng.scan()
        times.append(t)
    return times, md


def test_records_and_replays_the_same_prices(cfg):
    times, md = record_cycles(cfg)
    s = summary(cfg.record.path)
    assert s["quotes"] > 100 and s["spots"] >= 3 and set(s["by_underlying"]) >= {"SPY", "QQQ", "IWM"}

    rp = ReplayMarketData(cfg.record.path, at=times[1] + timedelta(seconds=30))
    assert rp.today() == TODAY
    assert rp.spot("SPY") == 501
    ch = rp.chain("SPY", 30, 50)
    assert ch.quotes and ch.realized_vol
    q = ch.quotes[0]
    md.spots["SPY"] = 501
    live = md.quotes([q.contract.symbol])[q.contract.symbol]
    assert (q.bid, q.ask) == (live.bid, live.ask) and q.delta == live.delta
    assert set(rp.quotes([q.contract.symbol])) == {q.contract.symbol}


def test_replay_ignores_stale_data(cfg):
    times, _ = record_cycles(cfg, n=1)
    rp = ReplayMarketData(cfg.record.path, at=times[0] + timedelta(hours=2))
    assert rp.quotes(["SPY   261204P00480000"]) == {}


def test_cycles_groups_writes(cfg):
    times, _ = record_cycles(cfg, n=3)
    rp = ReplayMarketData(cfg.record.path)
    assert rp.cycles() == times
    assert rp.cycles(start=date(2026, 10, 22)) == []


def test_replay_runs_the_trader_without_touching_the_journal(cfg, tmp_path):
    record_cycles(cfg, n=4)
    before = Store(cfg.db_path).positions(None)
    out = replay(cfg, journal=str(tmp_path / "replay.db"))
    assert out["cycles"] == 4 and out["failed_cycles"] == 0
    replayed = Store(str(tmp_path / "replay.db")).positions(None)
    assert replayed  # the synthetic market always has something to trade
    assert Store(cfg.db_path).positions(None) == before


def test_recording_failure_never_breaks_trading(cfg):
    md = SyntheticMarketData(today=TODAY)
    rec = Recorder(cfg.record.path)
    rec._db.close()  # every write now fails
    assert RecordingMarketData(md, rec).spot("SPY") == 500


def test_last_quotes_returns_newest_priced_quote(tmp_path):
    import pytest
    from darwin_options.recorder import Recorder, last_quotes
    from darwin_options.models import OptionContract, OptionQuote
    rec = Recorder(str(tmp_path / "md.db"))
    c = OptionContract.from_occ("SPY261127C00787000")
    rec.now = lambda: "2026-10-22T19:00:00+00:00"
    rec.quotes([OptionQuote(c, bid=4.6, ask=4.8, iv=0.12)])
    rec.now = lambda: "2026-10-22T21:00:00+00:00"
    rec.quotes([OptionQuote(c, bid=0.0, ask=0.0)])  # after the close: no price, skipped
    got = last_quotes(str(tmp_path / "md.db"), [c.symbol, "SPY261127C00999000"])
    assert list(got) == [c.symbol]
    ts, q = got[c.symbol]
    assert ts.startswith("2026-10-22T19") and q.mid == pytest.approx(4.7) and q.iv == 0.12
    assert last_quotes(str(tmp_path / "none.db"), [c.symbol]) == {}
