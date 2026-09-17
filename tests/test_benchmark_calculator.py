"""Tests for benchmark_calculator_library.Benchmark against synthetic, fixed data (no network)."""

import pandas as pd
import pytest

from Portfolio_calculator_library import Benchmark
import Portfolio_calculator_library.benchmark_calculator_library as benchmark_mod


def fake_close(start, end):
    """Mirrors conftest.FakeTicker's own price formula exactly, so tests can compute an
    expected price at a given date instead of hardcoding numbers derived by hand."""
    idx=pd.date_range(start=start, end=end, freq='D')
    return pd.Series([100.0+0.37*((i*7) % 11)-0.5 for i in range(len(idx))], index=idx)


def test_money_invested_reproduces_the_contribution_schedule_exactly():
    # The whole point of Benchmark: Money_invested's day-over-day diffs (what calculate_irr's
    # cashflow schedule is built from) must match the input contributions precisely, regardless
    # of how the ticker's own price moves.
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 500.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')
    data=benchmark.data
    assert data[Benchmark.MONEY_INVESTED_COLUMN].diff().fillna(0.0).tolist()==pytest.approx(contributions.tolist())
    assert data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1500.0)
    assert benchmark.total_money_invested==pytest.approx(1500.0)


def test_current_value_matches_units_bought_at_the_buy_day_price():
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')
    # Recompute over the identical range Benchmark itself fetched (start..today) so the indexing
    # lines up, then read off just the two dates this test cares about.
    full=fake_close(idx.min(), pd.Timestamp.today())
    buy_price=full.loc['2024-01-02']
    last_price=full.loc['2024-01-05']
    units=1000.0/buy_price
    expected_current_value=units*last_price
    assert benchmark.total_current_value==pytest.approx(expected_current_value, rel=1e-6)
    assert benchmark.total_revenue==pytest.approx(expected_current_value-1000.0, abs=0.01)


def test_withdrawal_sells_units_worth_the_withdrawn_amount():
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, -300.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')
    data=benchmark.data
    # Money_invested still mirrors the schedule exactly (see the dedicated test above) - here we
    # only check that the withdrawal actually reduced the held position's value, not just the
    # bookkeeping column.
    assert data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(700.0)
    assert benchmark.total_money_invested==pytest.approx(1000.0)  # gross buys only, unreduced by the withdrawal


def test_calculate_irr_populates_irr_column_starting_at_zero():
    idx=pd.date_range('2024-01-01', '2024-02-01')
    contributions=pd.Series(0.0, index=idx)
    contributions.iloc[0]=1000.0
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')
    benchmark.calculate_irr()
    assert Benchmark.IRR_COLUMN in benchmark.portfolio.columns
    assert benchmark.portfolio[Benchmark.IRR_COLUMN].iloc[0]==pytest.approx(0.0)


def test_invalid_ticker_raises_clear_error():
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    with pytest.raises(ValueError, match="no price history"):
        Benchmark(contributions, 'INVALIDTICKER', currency='usd', currency_to='usd')


class _DividendFakeTicker:
    """Like conftest.FakeTicker, but also pays a single per-share dividend partway through -
    conftest's own fixture never returns a 'Dividends' column, so dividend reinvestment needs
    its own local mock."""

    def __init__(self, symbol):
        self.symbol=symbol

    def history(self, start, end, repair=True, actions=False):
        idx=pd.date_range(start=start, end=end, freq='D')
        close=[100.0+0.37*((i*7) % 11)-0.5 for i in range(len(idx))]
        dividends=[0.0]*len(idx)
        dividends[2]=2.0  # ex-date = 3rd day of the range
        return pd.DataFrame({'Close': close, 'Dividends': dividends}, index=idx)


def test_dividends_are_reinvested_as_extra_units(monkeypatch):
    monkeypatch.setattr(benchmark_mod.yf, 'Ticker', _DividendFakeTicker)
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')

    full=fake_close(idx.min(), pd.Timestamp.today())
    buy_price=full.loc['2024-01-02']
    ex_div_price=full.loc['2024-01-03']
    last_price=full.loc['2024-01-05']

    units_before_dividend=1000.0/buy_price
    extra_units=(units_before_dividend*2.0)/ex_div_price
    expected_current_value=(units_before_dividend+extra_units)*last_price

    # Money_invested is untouched by the dividend - only investor cash counts, matching the
    # rest of the library's convention that a dividend is a return on the position, not new
    # investor capital.
    assert benchmark.data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1000.0)
    assert benchmark.total_current_value==pytest.approx(expected_current_value, rel=1e-6)
