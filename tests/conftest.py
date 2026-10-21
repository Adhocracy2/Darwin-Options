from datetime import date

import pytest

from flick.broker.sim import SimBroker
from flick.config import Config
from flick.data.synthetic import SyntheticMarketData
from flick.engine import Engine
from flick.store import Store

TODAY = date(2026, 10, 21)


@pytest.fixture
def cfg(tmp_path):
    c = Config(mode="sim", data="synthetic", db_path=str(tmp_path / "t.db"), log_path=str(tmp_path / "t.log"))
    return c


@pytest.fixture
def md():
    return SyntheticMarketData(today=TODAY)


@pytest.fixture
def engine(cfg, md):
    store = Store(cfg.db_path)
    broker = SimBroker(md, store, cfg.sim_starting_equity, cfg.execution.slippage_frac,
                       cfg.execution.commission_per_contract, always_open=True)
    return Engine(cfg, md, broker, store)
