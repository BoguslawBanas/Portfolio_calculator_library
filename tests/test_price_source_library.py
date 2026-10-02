"""Tests for price_source_library.PriceSource — pure unit tests, no real network involved."""

import pandas as pd
import pytest

from Portfolio_calculator_library import PriceSource
import Portfolio_calculator_library.price_source_library as price_source_mod


class _FlakyTicker:
    """Raises on its first `fail_count` history() calls (across all instances - class-level
    counter, mirroring how yf.Ticker(...) is re-constructed on every PriceSource.fetch_history
    call), then returns a fixed DataFrame."""

    fail_count=0
    calls=0

    def __init__(self, symbol):
        self.symbol=symbol

    def history(self, **kwargs):
        type(self).calls+=1
        if type(self).calls<=type(self).fail_count:
            raise ConnectionError("simulated transient failure")
        return pd.DataFrame({'Close': [1.0]})


@pytest.fixture(autouse=True)
def reset_flaky_ticker():
    _FlakyTicker.fail_count=0
    _FlakyTicker.calls=0
    yield


def test_succeeds_on_first_try_without_sleeping(monkeypatch):
    monkeypatch.setattr(price_source_mod.yf, 'Ticker', _FlakyTicker)
    sleeps=[]
    monkeypatch.setattr(price_source_mod.time, 'sleep', lambda seconds: sleeps.append(seconds))

    result=PriceSource.fetch_history('AAA')

    assert list(result['Close'])==[1.0]
    assert _FlakyTicker.calls==1
    assert sleeps==[]


def test_retries_transient_failures_then_succeeds(monkeypatch):
    _FlakyTicker.fail_count=2
    monkeypatch.setattr(price_source_mod.yf, 'Ticker', _FlakyTicker)
    sleeps=[]
    monkeypatch.setattr(price_source_mod.time, 'sleep', lambda seconds: sleeps.append(seconds))

    result=PriceSource.fetch_history('AAA', backoff_seconds=1.0)

    assert list(result['Close'])==[1.0]
    assert _FlakyTicker.calls==3
    # Exponential backoff: backoff_seconds, then 2x, ... - one sleep per failed attempt.
    assert sleeps==[1.0, 2.0]


def test_gives_up_after_max_retries(monkeypatch):
    _FlakyTicker.fail_count=100
    monkeypatch.setattr(price_source_mod.yf, 'Ticker', _FlakyTicker)
    monkeypatch.setattr(price_source_mod.time, 'sleep', lambda seconds: None)

    with pytest.raises(ConnectionError):
        PriceSource.fetch_history('AAA', max_retries=2)

    # The first attempt plus 2 retries - no further attempts after the last retry fails.
    assert _FlakyTicker.calls==3


def test_history_kwargs_are_forwarded(monkeypatch):
    seen_kwargs={}

    class _RecordingTicker:
        def __init__(self, symbol):
            self.symbol=symbol

        def history(self, **kwargs):
            seen_kwargs.update(kwargs)
            return pd.DataFrame({'Close': [1.0]})

    monkeypatch.setattr(price_source_mod.yf, 'Ticker', _RecordingTicker)

    PriceSource.fetch_history('AAA', start='2024-01-01', end='2024-01-02', repair=True, actions=False)

    assert seen_kwargs=={'start': '2024-01-01', 'end': '2024-01-02', 'repair': True, 'actions': False}
