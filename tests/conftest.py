from datetime import date

import pytest

from darwin_options.broker.sim import SimBroker
from darwin_options.config import Config
from darwin_options.data.synthetic import SyntheticMarketData
from darwin_options.engine import Engine
from darwin_options.store import Store

TODAY = date(2026, 10, 21)


@pytest.fixture
def cfg(tmp_path):
    c = Config(mode="sim", data="synthetic", db_path=str(tmp_path / "t.db"), log_path=str(tmp_path / "t.log"))
    c.record.path = str(tmp_path / "marketdata.db")  # never write into the repo's var/
    c.network.enabled = False  # no real pings from tests
    c.risk.no_entries_after_open_min = 0  # tests mustn't depend on the real time of day (their own test sets it)
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
