from __future__ import annotations

from .config import Config
from .engine import Engine
from .store import Store


def build_engine(cfg: Config, store: Store | None = None) -> Engine:
    """Interactive Brokers for data and orders. `mode: sim` keeps orders local;
    `data: synthetic` (sim only) runs fully offline."""
    store = store or Store(cfg.db_path)
    session = None
    if cfg.data == "synthetic":
        from .data.synthetic import SyntheticMarketData

        md = SyntheticMarketData(r=cfg.risk_free_rate)
    else:
        from .data.ibkr import IBKRMarketData
        from .ibkr import IBSession

        session = IBSession(cfg.ibkr, live=cfg.mode == "live")
        md = IBKRMarketData(session, cfg.risk_free_rate)
        if cfg.record.enabled:
            from .recorder import Recorder, RecordingMarketData

            md = RecordingMarketData(md, Recorder(cfg.record.path))

    if cfg.mode == "sim":
        from .broker.sim import SimBroker

        broker = SimBroker(md, store, cfg.sim_starting_equity, cfg.execution.slippage_frac,
                           cfg.execution.commission_per_contract, always_open=cfg.data == "synthetic")
    else:
        from .broker.ibkr import IBKRBroker

        broker = IBKRBroker(cfg, session, md)
    eng = Engine(cfg, md, broker, store)
    eng.session = session
    return eng
