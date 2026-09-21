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
    # The whole point of Benchmark: Money_invested's diffs must match contributions exactly,
    # regardless of how the ticker's own price moves.
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
    # Recompute over the same range Benchmark itself fetched (start..today) so indexing lines up.
    full=fake_close(idx.min(), pd.Timestamp.today())
    buy_price=full.loc['2024-01-02']
    last_price=full.loc['2024-01-05']
    units=1000.0/buy_price
    expected_current_value=units*last_price
    assert float(benchmark.total_current_value)==pytest.approx(expected_current_value, abs=0.01)
    assert float(benchmark.total_revenue)==pytest.approx(expected_current_value-1000.0, abs=0.01)


def test_withdrawal_sells_units_worth_the_withdrawn_amount():
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, -300.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, 'FAKEUSD', currency='usd', currency_to='usd')
    data=benchmark.data
    # Check the withdrawal actually reduced the held position's value, not just the bookkeeping.
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
    """Like conftest.FakeTicker, but also pays a per-share dividend partway through - needed
    since conftest's own fixture never returns a 'Dividends' column."""

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

    # Money_invested is untouched by the dividend - only investor cash counts as contribution.
    assert benchmark.data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1000.0)
    assert float(benchmark.total_current_value)==pytest.approx(expected_current_value, abs=0.01)


class _BasketFakeTicker:
    """Per-symbol price level (so a wrong split shows up), and only 'PAYS' pays a dividend."""

    BASES={'AAA': 100.0, 'BBB': 50.0, 'PAYS': 100.0}

    def __init__(self, symbol):
        self.symbol=symbol

    def history(self, start, end, repair=True, actions=False):
        idx=pd.date_range(start=start, end=end, freq='D')
        base=self.BASES[self.symbol]
        close=[base+0.37*((i*7) % 11)-0.5 for i in range(len(idx))]
        frame=pd.DataFrame({'Close': close}, index=idx)
        if self.symbol=='PAYS':
            frame['Dividends']=[2.0 if i==2 else 0.0 for i in range(len(idx))]
        return frame


def basket_close(symbol, day):
    idx=pd.date_range('2024-01-01', pd.Timestamp.today(), freq='D')
    return _BasketFakeTicker.BASES[symbol]+0.37*((list(idx).index(pd.Timestamp(day))*7) % 11)-0.5


def test_buys_are_split_across_tickers_by_weight(monkeypatch):
    monkeypatch.setattr(benchmark_mod.yf, 'Ticker', _BasketFakeTicker)
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, {'AAA': 60, 'BBB': 40}, currency='usd', currency_to='usd')

    expected=(600.0/basket_close('AAA', '2024-01-02'))*basket_close('AAA', '2024-01-05')
    expected+=(400.0/basket_close('BBB', '2024-01-02'))*basket_close('BBB', '2024-01-05')
    assert benchmark.tickers=={'AAA': 60.0, 'BBB': 40.0}
    assert float(benchmark.data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(1000.0)
    assert float(benchmark.total_current_value)==pytest.approx(expected, abs=0.01)


def test_withdrawal_sells_the_same_split_by_value(monkeypatch):
    monkeypatch.setattr(benchmark_mod.yf, 'Ticker', _BasketFakeTicker)
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, -500.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, {'AAA': 60, 'BBB': 40}, currency='usd', currency_to='usd')

    units_a=(600.0/basket_close('AAA', '2024-01-02'))-(300.0/basket_close('AAA', '2024-01-04'))
    units_b=(400.0/basket_close('BBB', '2024-01-02'))-(200.0/basket_close('BBB', '2024-01-04'))
    expected=units_a*basket_close('AAA', '2024-01-05')+units_b*basket_close('BBB', '2024-01-05')
    assert float(benchmark.data[Benchmark.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(500.0)
    assert float(benchmark.total_current_value)==pytest.approx(expected, abs=0.01)


def test_dividend_is_reinvested_only_into_the_ticker_that_paid_it(monkeypatch):
    monkeypatch.setattr(benchmark_mod.yf, 'Ticker', _BasketFakeTicker)
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, {'PAYS': 50, 'AAA': 50}, currency='usd', currency_to='usd')

    units_pays=500.0/basket_close('PAYS', '2024-01-02')
    units_pays+=(units_pays*2.0)/basket_close('PAYS', '2024-01-03')
    units_aaa=500.0/basket_close('AAA', '2024-01-02')
    expected=units_pays*basket_close('PAYS', '2024-01-05')+units_aaa*basket_close('AAA', '2024-01-05')
    assert float(benchmark.total_current_value)==pytest.approx(expected, abs=0.01)


def test_per_symbol_currency_dict_is_accepted(monkeypatch):
    monkeypatch.setattr(benchmark_mod.yf, 'Ticker', _BasketFakeTicker)
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    benchmark=Benchmark(contributions, {'AAA': 50, 'BBB': 50}, currency={'AAA': 'usd', 'BBB': 'usd'}, currency_to='usd')
    assert list(benchmark.tickers)==['AAA', 'BBB']

    with pytest.raises(ValueError, match="missing an entry"):
        Benchmark(contributions, {'AAA': 50, 'BBB': 50}, currency={'AAA': 'usd'}, currency_to='usd')


@pytest.mark.parametrize('weights,message', [
    ({}, "non-empty"),
    ({'AAA': 60, 'BBB': 30}, "sum to 100"),
    ({'AAA': 110, 'BBB': -10}, "positive"),
    ({'AAA': 0, 'BBB': 100}, "positive"),
    ({'AAA': True, 'BBB': 99}, "positive"),
])
def test_invalid_weights_raise(weights, message):
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    with pytest.raises(ValueError, match=message):
        Benchmark(contributions, weights, currency='usd', currency_to='usd')


def test_one_invalid_ticker_in_a_basket_raises_naming_it():
    idx=pd.date_range('2024-01-01', '2024-01-05')
    contributions=pd.Series([0.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    with pytest.raises(ValueError, match="INVALIDTICKER"):
        Benchmark(contributions, {'FAKEUSD': 50, 'INVALIDTICKER': 50}, currency='usd', currency_to='usd')
