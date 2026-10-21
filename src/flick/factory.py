from __future__ import annotations

from .config import Config
from .engine import Engine
from .store import Store


def build_engine(cfg: Config, store: Store | None = None) -> Engine:
    store = store or Store(cfg.db_path)
    live = cfg.mode == "live"
    session = None

    def ib_session():
        nonlocal session
        if session is None:
            from .ibkr import IBSession

            session = IBSession(cfg.ibkr, live=live)
        return session

    src = cfg.data_source
    if src == "synthetic":
        from .data.synthetic import SyntheticMarketData

        md = SyntheticMarketData(r=cfg.risk_free_rate)
    elif src == "ibkr":
        from .data.ibkr import IBKRMarketData

        md = IBKRMarketData(ib_session(), cfg.risk_free_rate)
    else:
        from .data.alpaca import AlpacaMarketData

        md = AlpacaMarketData(cfg.alpaca_key, cfg.alpaca_secret, cfg.options_feed,
                              paper=not live, risk_free_rate=cfg.risk_free_rate)

    if cfg.mode == "sim":
        from .broker.sim import SimBroker

        broker = SimBroker(md, store, cfg.sim_starting_equity, cfg.execution.slippage_frac,
                           cfg.execution.commission_per_contract, always_open=src == "synthetic")
    elif cfg.broker == "ibkr":
        from .broker.ibkr import IBKRBroker

        broker = IBKRBroker(cfg, ib_session(), md)
    else:
        from .broker.alpaca import AlpacaBroker

        broker = AlpacaBroker(cfg)
    return Engine(cfg, md, broker, store)
