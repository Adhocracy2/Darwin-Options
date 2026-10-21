from __future__ import annotations

from .config import Config
from .engine import Engine
from .store import Store


def build_engine(cfg: Config, store: Store | None = None) -> Engine:
    store = store or Store(cfg.db_path)
    if cfg.data == "synthetic":
        if cfg.mode != "sim":
            raise ValueError("synthetic data can only be used with mode: sim")
        from .data.synthetic import SyntheticMarketData

        md = SyntheticMarketData(r=cfg.risk_free_rate)
    else:
        from .data.alpaca import AlpacaMarketData

        md = AlpacaMarketData(cfg.alpaca_key, cfg.alpaca_secret, cfg.options_feed,
                              paper=cfg.mode != "alpaca_live", risk_free_rate=cfg.risk_free_rate)
    if cfg.mode == "sim":
        from .broker.sim import SimBroker

        broker = SimBroker(md, store, cfg.sim_starting_equity, cfg.execution.slippage_frac,
                           cfg.execution.commission_per_contract, always_open=cfg.data == "synthetic")
    else:
        from .broker.alpaca import AlpacaBroker

        broker = AlpacaBroker(cfg)
    return Engine(cfg, md, broker, store)
