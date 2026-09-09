"""
Shared fixtures for the automated test suite (README Roadmap item).

Every calculator that talks to yfinance (Stock, Commodity, Crypto, Currency) does so through
`yf.Ticker(...).history(...)` — see each module's `_compute_data`/`_fetch_data`. Patching
`yfinance.Ticker` once here (it's the same module object every calculator imports, so one
patch covers all of them) is enough to run the whole suite offline, deterministically, and
without depending on real market data.
"""

import os
import sys
import json
import shutil
import tempfile
from datetime import datetime
from unittest import mock

import pytest
import pandas as pd

REPO_ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_PARENT=os.path.dirname(REPO_ROOT)
if PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, PACKAGE_PARENT)

import Portfolio_calculator_library as pcl  # noqa: E402
import Portfolio_calculator_library.stock_calculator_library as stock_mod  # noqa: E402
import Portfolio_calculator_library.currency_calculator_library as currency_mod  # noqa: E402


class FakeTicker:
    """Deterministic, non-flat price series keyed by symbol — non-flat so any indexing/
    alignment bug would actually show up as a mismatch instead of being masked by a constant
    price. call_log records every symbol fetched, for tests that assert on caching/call counts."""

    call_log=list()

    def __init__(self, symbol):
        self.symbol=symbol

    def history(self, start, end, repair=True, actions=False):
        FakeTicker.call_log.append(self.symbol)
        idx=pd.date_range(start=start, end=end, freq='D')
        base=1.10 if self.symbol.endswith('=X') else 100.0
        close=[base + 0.37*((i*7) % 11) - 0.5 for i in range(len(idx))]
        return pd.DataFrame({
            'Close': close, 'High': close, 'Low': close, 'Open': close,
            'Volume': 0, 'Repaired?': False,
        }, index=idx)


@pytest.fixture(autouse=True)
def mock_yfinance():
    """Applied to every test automatically — nothing in this suite should ever hit the network."""
    FakeTicker.call_log=list()
    with mock.patch.object(stock_mod.yf, 'Ticker', FakeTicker):
        yield FakeTicker


@pytest.fixture
def make_source_dir(tmp_path):
    """Factory fixture: make_source_dir(name, {"buy.csv": "...", "sell.csv": "..."}) writes the
    given per-state CSVs into a fresh subdirectory of tmp_path and returns its path. Callable
    more than once per test (e.g. to build several scenarios) — each call gets its own
    directory even when given the same name, via an internal counter, so later scenarios never
    inherit stale CSVs left over from an earlier one."""
    counter=[0]

    def _make(name: str, csv_files: dict) -> str:
        counter[0]+=1
        directory=tmp_path/f'{name}_{counter[0]}'
        directory.mkdir()
        for filename, content in csv_files.items():
            (directory/filename).write_text(content)
        return str(directory)
    return _make


@pytest.fixture
def make_tickers_json(tmp_path):
    """Factory fixture: writes an ISIN -> {ticker, currency} mapping to tickers.json and
    returns its path."""
    def _make(mapping: dict, filename: str='tickers.json') -> str:
        path=tmp_path/filename
        path.write_text(json.dumps(mapping))
        return str(path)
    return _make


@pytest.fixture
def cache_dir(tmp_path):
    return str(tmp_path/'.cache')
