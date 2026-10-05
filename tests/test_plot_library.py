"""
Tests for plot_library.Plot - checks chart construction (trace types/names/values, argument
validation) against an already-computed Portfolio's data, rather than re-deriving Stock/Bonds/...
business values from scratch (those are covered by their own suites - Plot's own job is just to
read/cast/arrange them into a go.Figure).

Plot._render is mocked out everywhere via the captured_figures fixture, so no real plotly window
opens and kaleido isn't required - the mock just records the go.Figure (and path_to_save_fig) so
tests can assert on fig.data.
"""

from datetime import date, timedelta
from unittest import mock

import pytest
import pandas as pd
import plotly.graph_objects as go

from Portfolio_calculator_library import Portfolio, Plot
from Portfolio_calculator_library.plot_library import COLOR_GOOD, COLOR_CRITICAL


def build_portfolio(make_source_dir, make_tickers_json, currency_to='usd'):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
        'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                    "2024-03-01,US0000000001,4,120.0\n",
        'dividend.csv': "date,isin,dividend\n2024-06-01,US0000000001,25.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    return Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to=currency_to)


@pytest.fixture
def portfolio(make_source_dir, make_tickers_json):
    return build_portfolio(make_source_dir, make_tickers_json)


@pytest.fixture
def captured_figures():
    """Patches Plot._render to record the go.Figure (and path_to_save_fig) it was called with,
    instead of actually showing/saving it."""
    figures=list()

    def _capture(self, fig, path_to_save_fig=None):
        figures.append((fig, path_to_save_fig))

    with mock.patch.object(Plot, '_render', _capture):
        yield figures


def test_render_shows_by_default_and_saves_when_a_path_is_given():
    fig=go.Figure()
    with mock.patch.object(fig, 'show') as show, mock.patch.object(fig, 'write_image') as write_image:
        Plot._render(fig)
        show.assert_called_once()
        write_image.assert_not_called()

        show.reset_mock()
        Plot._render(fig, path_to_save_fig='out.png')
        write_image.assert_called_once_with('out.png')
        show.assert_not_called()


def test_money_plot_line_kind_matches_underlying_data(portfolio, captured_figures):
    portfolio.calculate_irr()  # populates portfolio.portfolio, which money_plot reads from
    plot=Plot(portfolio)
    plot.money_plot()
    fig, path=captured_figures[-1]

    assert path is None
    assert len(fig.data)==2
    assert fig.data[0].name=='Money_invested'
    assert fig.data[1].name=='Revenue'
    data=portfolio.portfolio
    expected_invested=data[Portfolio.MONEY_INVESTED_COLUMN].astype(float).tolist()
    expected_revenue=(data[Portfolio.MONEY_INVESTED_COLUMN].astype(float)+data[Portfolio.PROFIT_COLUMN].astype(float)).tolist()
    assert list(fig.data[0].y)==pytest.approx(expected_invested)
    assert list(fig.data[1].y)==pytest.approx(expected_revenue)


def test_money_plot_stacked_kind_uses_profit_not_revenue(portfolio, captured_figures):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    plot.money_plot(kind=Plot.MONEY_PLOT_KIND_STACKED_PLOT)
    fig, _=captured_figures[-1]

    assert fig.data[1].name=='Profit'
    expected_profit=portfolio.portfolio[Portfolio.PROFIT_COLUMN].astype(float).tolist()
    assert list(fig.data[1].y)==pytest.approx(expected_profit)


def test_money_plot_rejects_unknown_kind(portfolio):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown money_plot kind"):
        plot.money_plot(kind='nonsense')


def test_money_plot_with_benchmark_adds_revenue_overlay_line(portfolio, captured_figures):
    portfolio.calculate_irr()  # populates portfolio.portfolio, which money_plot reads from
    plot=Plot(portfolio)
    benchmark=portfolio.simulate_benchmark('FAKEUSD', currency='usd')
    plot.money_plot(benchmark=benchmark, benchmark_name='FAKEUSD')
    fig, _=captured_figures[-1]

    assert len(fig.data)==3
    assert fig.data[2].name=='FAKEUSD revenue'
    data=portfolio.portfolio
    expected=(benchmark.data[Portfolio.MONEY_INVESTED_COLUMN].astype(float)+benchmark.data[Portfolio.PROFIT_COLUMN].astype(float)).reindex(data.index).ffill()
    assert list(fig.data[2].y)==pytest.approx(expected.tolist())


def test_money_plot_without_benchmark_has_no_extra_trace(portfolio, captured_figures):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    plot.money_plot()
    fig, _=captured_figures[-1]
    assert len(fig.data)==2


def test_performance_plot_reruns_calculate_irr_when_irr_column_is_missing(portfolio, captured_figures):
    # Exercises the "IRR_COLUMN not in columns -> recompute" guard directly. In practice
    # self.portfolio only ever comes into existence via calculate_irr() itself (resample()/
    # calculate_money_earned_between_dates_column() both require it to already exist), so it
    # always already carries IRR_COLUMN - dropping it here is a synthetic setup, not a sequence
    # reachable through the public API, purely to test this guard's own recompute branch.
    portfolio.calculate_irr()
    del portfolio.portfolio[Portfolio.IRR_COLUMN]
    assert Portfolio.IRR_COLUMN not in portfolio.portfolio.columns
    plot=Plot(portfolio)
    plot.performance_plot()
    assert Portfolio.IRR_COLUMN in portfolio.portfolio.columns
    fig, _=captured_figures[-1]
    assert list(fig.data[0].y)==pytest.approx(portfolio.portfolio[Portfolio.IRR_COLUMN].tolist())


def test_performance_plot_on_a_never_computed_portfolio_raises_attributeerror(portfolio):
    # Known gap, not something this suite is fixing: performance_plot's own guard reads
    # self.portfolio.portfolio.columns before checking whether IRR_COLUMN is missing, so a
    # Portfolio that's never had calculate_irr()/resample()/calculate_money_earned_between_dates_
    # column() called at all (no .portfolio attribute yet) raises AttributeError instead of
    # transparently computing IRR the way the "auto-populate" comment implies. Same pattern in
    # benchmark_comparison_plot/period_return_bar_plot below.
    assert not hasattr(portfolio, 'portfolio')
    plot=Plot(portfolio)
    with pytest.raises(AttributeError):
        plot.performance_plot()


def test_performance_plot_candlestick_aggregates_by_resample_rule(portfolio, captured_figures):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    plot.performance_plot(kind=Plot.PERFORMANCE_PLOT_KIND_CANDLESTICK, resample_rule='W')
    fig, _=captured_figures[-1]
    assert isinstance(fig.data[0], go.Candlestick)


def test_performance_plot_rejects_unknown_kind(portfolio):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown performance_plot kind"):
        plot.performance_plot(kind='nonsense')


def test_performance_plot_with_benchmark_overlays_both_irr_series(portfolio, captured_figures):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    benchmark=portfolio.simulate_benchmark('FAKEUSD', currency='usd')
    plot.performance_plot(benchmark=benchmark, benchmark_name='FAKEUSD')
    fig, _=captured_figures[-1]

    assert len(fig.data)==2
    assert fig.data[0].name=='Portfolio'
    assert fig.data[1].name=='FAKEUSD'
    assert hasattr(benchmark, 'portfolio')  # calculate_irr() was called on it too, automatically


def test_performance_plot_rejects_benchmark_with_candlestick_kind(portfolio):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    benchmark=portfolio.simulate_benchmark('FAKEUSD', currency='usd')
    with pytest.raises(ValueError, match="benchmark overlay isn't supported for kind"):
        plot.performance_plot(kind=Plot.PERFORMANCE_PLOT_KIND_CANDLESTICK, benchmark=benchmark)


def test_benchmark_comparison_plot_overlays_both_irr_series(portfolio, captured_figures):
    portfolio.calculate_irr()
    plot=Plot(portfolio)
    benchmark=portfolio.simulate_benchmark('FAKEUSD', currency='usd')
    plot.benchmark_comparison_plot(benchmark, benchmark_name='FAKEUSD')
    fig, _=captured_figures[-1]

    assert len(fig.data)==2
    assert fig.data[0].name=='Portfolio'
    assert fig.data[1].name=='FAKEUSD'
    assert hasattr(benchmark, 'portfolio')  # calculate_irr() was called on it too, automatically


def test_revenue_plot_include_dividends_true_is_a_single_profit_line(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.revenue_plot(include_dividends=True)
    fig, _=captured_figures[-1]

    assert len(fig.data)==1
    assert fig.data[0].name=='Revenue'
    expected=portfolio.data[Portfolio.PROFIT_COLUMN].astype(float).tolist()
    assert list(fig.data[0].y)==pytest.approx(expected)


def test_revenue_plot_include_dividends_false_splits_out_dividends(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.revenue_plot(include_dividends=False)
    fig, _=captured_figures[-1]

    assert len(fig.data)==2
    assert fig.data[0].name=='Revenue'
    assert fig.data[1].name=='Dividends'
    data=portfolio.data
    dividends=data[Portfolio.DIVIDEND_COLUMN].astype(float)
    profit=data[Portfolio.PROFIT_COLUMN].astype(float)
    assert list(fig.data[0].y)==pytest.approx((profit-dividends).tolist())
    assert list(fig.data[1].y)==pytest.approx(dividends.tolist())


def test_revenue_plot_defaults_dividends_to_zero_when_source_has_none(make_source_dir, captured_figures):
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       "2024-01-15,savings,1000.0,fixed,6.0,12,0.0\n",
    })
    portfolio=Portfolio({account_dir: 'bank_account'})
    assert Portfolio.DIVIDEND_COLUMN not in portfolio.data.columns
    plot=Plot(portfolio)
    plot.revenue_plot(include_dividends=False)
    fig, _=captured_figures[-1]
    assert list(fig.data[1].y)==pytest.approx([0.0]*len(portfolio.data))


def test_revenue_plot_with_benchmark_adds_revenue_overlay_line(portfolio, captured_figures):
    plot=Plot(portfolio)
    benchmark=portfolio.simulate_benchmark('FAKEUSD', currency='usd')
    plot.revenue_plot(benchmark=benchmark, benchmark_name='FAKEUSD')
    fig, _=captured_figures[-1]

    assert len(fig.data)==2
    assert fig.data[1].name=='FAKEUSD revenue'
    data=portfolio.data
    expected=benchmark.data[Portfolio.PROFIT_COLUMN].astype(float).reindex(data.index).ffill()
    assert list(fig.data[1].y)==pytest.approx(expected.tolist())


def test_drawdown_plot_matches_running_peak_to_trough_decline(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.drawdown_plot()
    fig, path=captured_figures[-1]

    assert path is None
    data=portfolio.data
    total_value=data[Portfolio.MONEY_INVESTED_COLUMN].astype(float)+data[Portfolio.PROFIT_COLUMN].astype(float)
    running_max=total_value.cummax()
    expected=[(0.0 if peak<=0 else (value-peak)/peak*100.0) for value, peak in zip(total_value, running_max)]
    assert list(fig.data[0].y)==pytest.approx(expected)
    assert all(value<=1e-9 for value in fig.data[0].y)  # never above its own running peak


def test_cashflow_plot_matches_periodic_money_invested_diff(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.cashflow_plot(resample_rule='D')
    fig, path=captured_figures[-1]

    assert path is None
    data=portfolio.data
    money_invested=data[Portfolio.MONEY_INVESTED_COLUMN].astype(float)
    expected=money_invested.diff().fillna(0.0)
    assert list(fig.data[0].x)==list(data.index)
    assert list(fig.data[0].y)==pytest.approx(expected.tolist())


def test_cashflow_plot_colors_bars_by_sign(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.cashflow_plot()
    fig, _=captured_figures[-1]

    values=list(fig.data[0].y)
    colors=list(fig.data[0].marker.color)
    assert colors==[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in values]


def test_realized_vs_unrealized_profit_plot_splits_and_sums_to_profit(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.realized_vs_unrealized_profit_plot()
    fig, path=captured_figures[-1]

    assert path is None
    assert len(fig.data)==2
    assert fig.data[0].name=='Realized profit'
    assert fig.data[1].name=='Unrealized profit (incl. dividends)'
    data=portfolio.data
    profit=data[Portfolio.PROFIT_COLUMN].astype(float)
    unrealized=data[Portfolio.PROFIT_WITHOUT_REALIZED_COLUMN].astype(float)
    realized=profit-unrealized
    assert list(fig.data[0].y)==pytest.approx(realized.tolist())
    assert list(fig.data[1].y)==pytest.approx(unrealized.tolist())
    summed=[a+b for a, b in zip(fig.data[0].y, fig.data[1].y)]
    assert summed==pytest.approx(profit.tolist())
    # the fixture's partial sell (4 units bought at 100, sold at 120) locks in a realized gain
    assert realized.iloc[-1]>0


def test_dividend_income_plot_matches_periodic_dividend_diff(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.dividend_income_plot(resample_rule='D')
    fig, path=captured_figures[-1]

    assert path is None
    data=portfolio.data
    dividends=data[Portfolio.DIVIDEND_COLUMN].astype(float)
    expected=dividends.diff().fillna(0.0)
    assert list(fig.data[0].x)==list(data.index)
    assert list(fig.data[0].y)==pytest.approx(expected.tolist())
    # the fixture's single dividend.csv row (25.0 on 2024-06-01) shows up as one day's income
    assert expected[pd.Timestamp('2024-06-01')]==pytest.approx(25.0)


def test_dividend_income_plot_defaults_dividends_to_zero_when_source_has_none(make_source_dir, captured_figures):
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       "2024-01-15,savings,1000.0,fixed,6.0,12,0.0\n",
    })
    portfolio=Portfolio({account_dir: 'bank_account'})
    assert Portfolio.DIVIDEND_COLUMN not in portfolio.data.columns
    plot=Plot(portfolio)
    plot.dividend_income_plot()
    fig, _=captured_figures[-1]
    assert list(fig.data[0].y)==pytest.approx([0.0]*len(fig.data[0].y))


def test_period_return_bar_plot_colors_bars_by_sign(portfolio, captured_figures):
    portfolio.calculate_irr()  # gives period_return_bar_plot's own guard a self.portfolio.portfolio to work with
    plot=Plot(portfolio)
    plot.period_return_bar_plot(days_between=30)
    fig, _=captured_figures[-1]

    assert Portfolio.DAILY_RETURN_COLUMN in portfolio.portfolio.columns
    values=portfolio.portfolio[Portfolio.DAILY_RETURN_COLUMN].astype(float).tolist()
    assert list(fig.data[0].y)==pytest.approx(values)
    assert list(fig.data[0].marker.color)==[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in values]


def test_rolling_return_plot_matches_annualized_window_formula(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.rolling_return_plot(days_between=30)
    fig, path=captured_figures[-1]

    assert path is None
    data=portfolio.data
    money_invested=data[Portfolio.MONEY_INVESTED_COLUMN].astype(float)
    profit=data[Portfolio.PROFIT_COLUMN].astype(float)
    older_profit=profit.shift(30).fillna(0.0)
    older_money_invested=money_invested.shift(30).fillna(0.0)
    expected=[
        0.0 if older<=0 else (recent-old)/older*(365.0/30)*100.0
        for recent, old, older in zip(profit, older_profit, older_money_invested)
    ]
    assert list(fig.data[0].x)==list(data.index)
    assert list(fig.data[0].y)==pytest.approx(expected)


def test_rolling_return_plot_rejects_non_positive_days_between(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="days_between must be a positive number of days"):
        plot.rolling_return_plot(days_between=0)


def test_allocation_plot_pie_matches_distribution_by_ticker(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.allocation_plot(by=Plot.ALLOCATION_PLOT_BY_TICKER, kind=Plot.ALLOCATION_PLOT_KIND_PIE, metric=Plot.ALLOCATION_PLOT_METRIC_INVESTED)
    fig, _=captured_figures[-1]

    assert isinstance(fig.data[0], go.Pie)
    assert set(fig.data[0].labels)==set(portfolio.distribution_by_ticker)
    label_to_value=dict(zip(fig.data[0].labels, fig.data[0].values))
    for ticker, pct in portfolio.distribution_by_ticker.items():
        assert label_to_value[ticker]==pytest.approx(pct)


def test_allocation_plot_histogram_revenue_colors_by_sign(make_source_dir, make_tickers_json, captured_figures):
    # A full sell locks in realized profit from the CSV's own price_of_unit - independent of
    # FakeTicker's own (oscillating, not trending) Close series - so the two tickers' revenue
    # signs (and the portfolio total's) are deterministic regardless of what "today" happens to be.
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n"
                   "2024-01-15,US0000000002,10,100.0,0.0\n",
        'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                    "2024-03-01,US0000000001,10,90.0\n"    # a loss: realized -100
                    "2024-03-01,US0000000002,10,200.0\n",  # a big gain: realized +1000
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "US0000000002": {"ticker": "FAKEUSD2", "currency": "usd"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')
    assert portfolio.distribution_by_ticker_revenue['US0000000001']<0
    assert portfolio.distribution_by_ticker_revenue['US0000000002']>0
    plot=Plot(portfolio)
    plot.allocation_plot(by=Plot.ALLOCATION_PLOT_BY_TICKER, kind=Plot.ALLOCATION_PLOT_KIND_HISTOGRAM, metric=Plot.ALLOCATION_PLOT_METRIC_REVENUE)
    fig, _=captured_figures[-1]

    values=list(fig.data[0].y)
    colors=list(fig.data[0].marker.color)
    assert colors==[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in values]


def test_allocation_plot_buckets_extra_slices_into_other(make_source_dir, make_tickers_json, captured_figures):
    n=9  # > max_slices default (7)
    buy_rows="".join(f"2024-01-15,ISIN{i},{i+1},100.0,0.0\n" for i in range(n))
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"+buy_rows,
    })
    tickers_json=make_tickers_json({f"ISIN{i}": {"ticker": f"FAKE{i}", "currency": "usd"} for i in range(n)})
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')
    plot=Plot(portfolio)
    plot.allocation_plot(by=Plot.ALLOCATION_PLOT_BY_TICKER, kind=Plot.ALLOCATION_PLOT_KIND_PIE, max_slices=7)
    fig, _=captured_figures[-1]

    assert len(fig.data[0].labels)==8  # 7 kept + one 'Other' bucket
    assert fig.data[0].labels[-1]=='Other'


def test_allocation_plot_by_currency_matches_distribution_by_currency(make_source_dir, make_tickers_json, captured_figures):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n"
                   "2024-01-15,US0000000002,5,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "US0000000002": {"ticker": "FAKEEUR", "currency": "eur"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')
    assert set(portfolio.distribution_by_currency)=={'USD', 'EUR'}
    plot=Plot(portfolio)
    plot.allocation_plot(by=Plot.ALLOCATION_PLOT_BY_CURRENCY, kind=Plot.ALLOCATION_PLOT_KIND_PIE)
    fig, _=captured_figures[-1]

    assert isinstance(fig.data[0], go.Pie)
    assert set(fig.data[0].labels)==set(portfolio.distribution_by_currency)
    label_to_value=dict(zip(fig.data[0].labels, fig.data[0].values))
    for currency, pct in portfolio.distribution_by_currency.items():
        assert label_to_value[currency]==pytest.approx(pct)


def test_allocation_plot_rejects_unknown_metric(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_plot metric"):
        plot.allocation_plot(metric='nonsense')


def test_allocation_plot_rejects_unknown_by(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_plot by"):
        plot.allocation_plot(by='nonsense')


def test_allocation_plot_rejects_unknown_kind(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_plot kind"):
        plot.allocation_plot(kind='nonsense')


def test_allocation_comparison_plot_groups_invested_and_total_value_by_ticker(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.allocation_comparison_plot(by=Plot.ALLOCATION_COMPARISON_PLOT_BY_TICKER)
    fig, _=captured_figures[-1]

    assert len(fig.data)==2
    assert fig.data[0].name=='Invested'
    assert fig.data[1].name=='Total value'
    assert list(fig.data[0].x)==list(fig.data[1].x)
    assert list(fig.data[0].x)==list(portfolio.distribution_by_ticker)
    # Both bars are a % of the same total (lifetime invested) - total value is rescaled from its
    # own total onto it, so the bars sum to 100% and 100% plus the portfolio's return.
    scale=float(portfolio.total_lifetime_value)/float(portfolio.total_money_invested)
    assert list(fig.data[0].y)==pytest.approx([portfolio.distribution_by_ticker[key] for key in fig.data[0].x])
    assert list(fig.data[1].y)==pytest.approx([portfolio.distribution_by_ticker_lifetime_value[key]*scale for key in fig.data[1].x])
    assert sum(fig.data[0].y)==pytest.approx(100.0)
    assert sum(fig.data[1].y)==pytest.approx(100.0*scale)


def test_allocation_comparison_plot_by_directory(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.allocation_comparison_plot(by=Plot.ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY)
    fig, _=captured_figures[-1]
    scale=float(portfolio.total_lifetime_value)/float(portfolio.total_money_invested)
    assert list(fig.data[0].x)==list(portfolio.distribution_by_directory)
    assert list(fig.data[0].y)==pytest.approx([portfolio.distribution_by_directory[key] for key in fig.data[0].x])
    assert list(fig.data[1].y)==pytest.approx([portfolio.distribution_by_directory_lifetime_value[key]*scale for key in fig.data[1].x])


def test_allocation_comparison_plot_closed_position_is_shown_against_what_was_invested(make_source_dir, make_tickers_json, captured_figures):
    # SOLD is bought for 1000 and fully sold for 1200 - nothing left held, 200 realized.
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-02,HELD,10,100,0\n"
                   "2024-01-02,SOLD,10,100,0\n",
        'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                    "2024-01-10,SOLD,10,120\n",
    })
    tickers_json=make_tickers_json({
        'HELD': {'ticker': 'HELD', 'currency': 'usd'},
        'SOLD': {'ticker': 'SOLD', 'currency': 'usd'},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')
    plot=Plot(portfolio)
    plot.allocation_comparison_plot(by=Plot.ALLOCATION_COMPARISON_PLOT_BY_TICKER)
    fig, _=captured_figures[-1]

    # 1000 of 2000 lifetime invested (50%), turned into 1200 (60% of that same 2000) - the
    # total value bar is taller, as the position gained, rather than dropping to its 200 profit.
    invested=dict(zip(fig.data[0].x, fig.data[0].y))
    total_value=dict(zip(fig.data[1].x, fig.data[1].y))
    assert invested['SOLD']==pytest.approx(50.0)
    assert total_value['SOLD']==pytest.approx(60.0, abs=0.01)


def test_allocation_comparison_plot_keeps_fully_matured_bond_types(make_source_dir, captured_figures):
    # Every bond has matured - nothing currently invested, but the type must still be plotted
    # against its lifetime cost basis instead of disappearing.
    matured_start=date.today()-timedelta(days=100)  # OTS's 3-month term has long since ended
    bonds_dir=make_source_dir('bonds', {
        'buy.csv': "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
                   f"{matured_start.isoformat()},OTS0826,5,0.0,2.0,False\n",
        'interest_rate.csv': "date,rate\n01-2020,5.0\n",
        'inflation_rate.csv': "date,inflation\n01-2020,4.0\n",
    })
    portfolio=Portfolio({bonds_dir: 'bonds'}, currency_to='PLN')
    plot=Plot(portfolio)
    plot.allocation_comparison_plot(by=Plot.ALLOCATION_COMPARISON_PLOT_BY_TICKER)
    fig, _=captured_figures[-1]

    assert list(fig.data[0].x)==['OTS']
    assert list(fig.data[0].y)==pytest.approx([100.0])
    assert fig.data[1].y[0]>100.0


def test_allocation_comparison_plot_rejects_unknown_by(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_comparison_plot by"):
        plot.allocation_comparison_plot(by='nonsense')


def test_allocation_over_time_plot_invested_reflects_when_each_directory_starts_contributing(make_source_dir, make_tickers_json, captured_figures):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       "2024-02-01,savings,500.0,fixed,0.0,12,0.0\n",
    })
    portfolio=Portfolio({stock_dir: 'stock', account_dir: 'bank_account'}, tickers_json=tickers_json, currency_to='usd')
    plot=Plot(portfolio)
    plot.allocation_over_time_plot(metric=Plot.ALLOCATION_PLOT_METRIC_INVESTED)
    fig, path=captured_figures[-1]

    assert path is None
    assert all(trace.stackgroup is None for trace in fig.data)  # kind='plot' (default): not stacked
    by_directory={trace.name: pd.Series(list(trace.y), index=pd.to_datetime(list(trace.x))) for trace in fig.data}
    # Before the bank account's first deposit, the stock directory is the entire portfolio.
    assert by_directory[stock_dir][pd.Timestamp('2024-01-20')]==pytest.approx(100.0)
    assert by_directory[account_dir][pd.Timestamp('2024-01-20')]==pytest.approx(0.0)
    # After: 1000 (stock) vs. 500 (bank), cost basis only - unaffected by FakeTicker's price moves.
    assert by_directory[stock_dir][pd.Timestamp('2024-02-05')]==pytest.approx(1000.0/1500.0*100.0)
    assert by_directory[account_dir][pd.Timestamp('2024-02-05')]==pytest.approx(500.0/1500.0*100.0)
    # The final day matches Portfolio's own snapshot distribution exactly.
    for dir, pct in portfolio.distribution_by_directory.items():
        assert by_directory[dir].iloc[-1]==pytest.approx(pct)


def test_allocation_over_time_plot_stacked_kind_sums_to_100_at_final_day(make_source_dir, make_tickers_json, captured_figures):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       "2024-02-01,savings,500.0,fixed,0.0,12,0.0\n",
    })
    portfolio=Portfolio({stock_dir: 'stock', account_dir: 'bank_account'}, tickers_json=tickers_json, currency_to='usd')
    plot=Plot(portfolio)
    plot.allocation_over_time_plot(kind=Plot.ALLOCATION_OVER_TIME_PLOT_KIND_STACKED_PLOT)
    fig, _=captured_figures[-1]

    assert all(trace.stackgroup=='one' for trace in fig.data)
    total_at_final_day=sum(trace.y[-1] for trace in fig.data)
    assert total_at_final_day==pytest.approx(100.0)


def test_allocation_over_time_plot_current_value_matches_distribution_by_directory_current_value_at_final_day(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.allocation_over_time_plot(metric=Plot.ALLOCATION_PLOT_METRIC_CURRENT_VALUE)
    fig, _=captured_figures[-1]

    final_by_name={trace.name: trace.y[-1] for trace in fig.data}
    for dir, pct in portfolio.distribution_by_directory_current_value.items():
        assert final_by_name[dir]==pytest.approx(pct)


def test_allocation_over_time_plot_buckets_extra_directories_into_other(make_source_dir, captured_figures):
    n=9  # > max_slices default (7)
    sources=dict()
    for i in range(n):
        account_dir=make_source_dir(f'account{i}', {
            'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                           f"2024-01-15,acct,{100.0*(i+1)},fixed,0.0,12,0.0\n",
        })
        sources[account_dir]='bank_account'
    portfolio=Portfolio(sources)
    plot=Plot(portfolio)
    plot.allocation_over_time_plot(max_slices=7)
    fig, _=captured_figures[-1]

    assert len(fig.data)==8  # 7 kept + one 'Other' bucket
    assert fig.data[-1].name=='Other'


def test_allocation_over_time_plot_rejects_unknown_metric(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_over_time_plot metric"):
        plot.allocation_over_time_plot(metric='revenue')


def test_allocation_over_time_plot_rejects_unknown_kind(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_over_time_plot kind"):
        plot.allocation_over_time_plot(kind='nonsense')
