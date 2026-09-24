"""
Tests for plot_library.Plot - checks chart construction (trace types/names/values, argument
validation) against an already-computed Portfolio's data, rather than re-deriving Stock/Bonds/...
business values from scratch (those are covered by their own suites - Plot's own job is just to
read/cast/arrange them into a go.Figure).

Plot._render is mocked out everywhere via the captured_figures fixture, so no real plotly window
opens and kaleido isn't required - the mock just records the go.Figure (and path_to_save_fig) so
tests can assert on fig.data.
"""

from unittest import mock

import pytest
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


def test_period_return_bar_plot_colors_bars_by_sign(portfolio, captured_figures):
    portfolio.calculate_irr()  # gives period_return_bar_plot's own guard a self.portfolio.portfolio to work with
    plot=Plot(portfolio)
    plot.period_return_bar_plot(days_between=30)
    fig, _=captured_figures[-1]

    assert Portfolio.DAILY_RETURN_COLUMN in portfolio.portfolio.columns
    values=portfolio.portfolio[Portfolio.DAILY_RETURN_COLUMN].astype(float).tolist()
    assert list(fig.data[0].y)==pytest.approx(values)
    assert list(fig.data[0].marker.color)==[COLOR_GOOD if value>=0 else COLOR_CRITICAL for value in values]


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
    assert list(fig.data[1].y)==pytest.approx([portfolio.distribution_by_ticker_total_value[key] for key in fig.data[1].x])


def test_allocation_comparison_plot_by_directory(portfolio, captured_figures):
    plot=Plot(portfolio)
    plot.allocation_comparison_plot(by=Plot.ALLOCATION_COMPARISON_PLOT_BY_DIRECTORY)
    fig, _=captured_figures[-1]
    assert list(fig.data[0].x)==list(portfolio.distribution_by_directory)


def test_allocation_comparison_plot_rejects_unknown_by(portfolio):
    plot=Plot(portfolio)
    with pytest.raises(ValueError, match="Unknown allocation_comparison_plot by"):
        plot.allocation_comparison_plot(by='nonsense')
