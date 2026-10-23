"""History importers: OptionsDX files -> history database -> replay."""
from datetime import date, datetime

import pytest

from darwin_options import clock
from darwin_options.history import SOURCES, import_files, summary
from darwin_options.recorder import ReplayMarketData

HEADER = ("[QUOTE_UNIXTIME], [QUOTE_READTIME], [QUOTE_DATE], [QUOTE_TIME_HOURS], [UNDERLYING_LAST], [EXPIRE_DATE], "
          "[EXPIRE_UNIX], [DTE], [C_DELTA], [C_GAMMA], [C_VEGA], [C_THETA], [C_RHO], [C_IV], [C_VOLUME], [C_LAST], "
          "[C_SIZE], [C_BID], [C_ASK], [STRIKE], [P_BID], [P_ASK], [P_SIZE], [P_LAST], [P_DELTA], [P_GAMMA], [P_VEGA], "
          "[P_THETA], [P_RHO], [P_IV], [P_VOLUME], [STRIKE_DISTANCE], [STRIKE_DISTANCE_PCT]")


def _line(day: date, spot: float, expiry: date, strike: float) -> str:
    ts = int(datetime(day.year, day.month, day.day, 16, tzinfo=clock.NY).timestamp())
    call = max(spot - strike, 0) + 2.0
    put = max(strike - spot, 0) + 2.0
    return (f"{ts}, {day} 16:00, {day}, 16.000000, {spot:.2f}, {expiry}, 0, {(expiry - day).days}, 0.5, 0.01, 0.3, -0.05, 0.1, 0.18, 10, "
            f"{call:.2f}, 1 x 1, {call - 0.05:.2f}, {call + 0.05:.2f}, {strike:.2f}, {put - 0.05:.2f}, {put + 0.05:.2f}, 1 x 1, "
            f"{put:.2f}, -0.5, 0.01, 0.3, -0.05, -0.1, 0.19, 12, {abs(strike - spot):.1f}, 0.01")


def write_month(path, days, expiries, strikes):
    lines = [HEADER]
    for i, d in enumerate(days):
        for e in expiries:
            for k in strikes:
                lines.append(_line(d, 400 + i, e, k))
    path.write_text("\n".join(lines) + "\n")


def test_import_and_replay_reads_it(tmp_path):
    days = [date(2023, 10, 2), date(2023, 10, 3)]
    f = tmp_path / "spy_eod_202310.txt"
    write_month(f, days, [date(2023, 11, 17), date(2023, 12, 15), date(2026, 1, 16)], [300, 380, 400, 420, 700])
    db = str(tmp_path / "hist.db")
    out = import_files([str(tmp_path)], db, SOURCES["optionsdx"]())
    # strike 700 is > 40% from ~$400 (300 is 25%, kept); the 2026 expiry is > 730 days out
    assert out["files"] == 1 and out["days"] == 2 and out["symbols"] == ["SPY"]
    assert out["quotes"] == 2 * 2 * 4 * 2  # 2 days x 2 expiries x 4 strikes x call+put
    assert import_files([str(f)], db, SOURCES["optionsdx"]())["skipped_files"] == 1  # not imported twice
    s = summary(db)
    assert s["days"] == 2 and s["quotes"] == out["quotes"]

    md = ReplayMarketData(db)
    moments = md.cycles()
    assert len(moments) == 2 and moments[0].astimezone(clock.NY).hour == 16
    md.at = moments[1]
    ch = md.chain("SPY", 30, 80)
    assert ch.spot == 401 and ch.quotes and {q.contract.expiry for q in ch.quotes} == {date(2023, 11, 17), date(2023, 12, 15)}
    q = next(q for q in ch.quotes if q.contract.strike == 380 and q.contract.right.value == "P")
    assert q.bid == pytest.approx(1.95) and q.iv == pytest.approx(0.19) and q.delta == pytest.approx(-0.5)
    assert md.closes("SPY") == [400, 401]


def test_replay_runs_on_history(tmp_path, cfg):
    from datetime import timedelta
    from darwin_options.replay import replay
    days = [date(2023, 10, 2) + timedelta(days=i) for i in range(5) if (date(2023, 10, 2) + timedelta(days=i)).weekday() < 5]
    write_month(tmp_path / "spy_eod_202310.txt", days, [date(2023, 11, 17)], [360, 370, 380, 390, 400, 410, 420, 430, 440])
    db = str(tmp_path / "hist.db")
    import_files([str(tmp_path)], db, SOURCES["optionsdx"]())
    cfg.universe = ["SPY"]
    out = replay(cfg, journal=str(tmp_path / "replay.db"), data=db)
    assert out["cycles"] == len(days) and out["failed_cycles"] == 0


def test_rejects_other_files(tmp_path):
    f = tmp_path / "spy_eod_202310.txt"
    f.write_text("a,b,c\n1,2,3\n")
    with pytest.raises(ValueError, match="not an OptionsDX file"):
        import_files([str(f)], str(tmp_path / "h.db"), SOURCES["optionsdx"]())


def test_reads_7z_archives(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    write_month(tmp_path / "spy_eod_202310.txt", [date(2023, 10, 2)], [date(2023, 11, 17)], [390, 400, 410])
    arc = tmp_path / "spy_eod_2023q4.7z"
    with py7zr.SevenZipFile(arc, "w") as z:
        z.write(tmp_path / "spy_eod_202310.txt", "spy_eod_202310.txt")
    (tmp_path / "spy_eod_202310.txt").unlink()
    out = import_files([str(arc)], str(tmp_path / "h.db"), SOURCES["optionsdx"]())
    assert out["files"] == 1 and out["quotes"] == 6
