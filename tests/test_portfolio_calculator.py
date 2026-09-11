"""
Tests for portfolio_calculator_library.Portfolio, combining multiple asset-type sources —
the integration layer on top of the individual Stock/Bonds/Commodity/Crypto suites.
"""

from datetime import date, timedelta

import pytest

from Portfolio_calculator_library import Portfolio


def build_single_stock_portfolio(make_source_dir, make_tickers_json, currency='usd'):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
        'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                    "2024-03-01,US0000000001,4,120.0\n",
        'dividend.csv': "date,isin,dividend\n2024-06-01,US0000000001,25.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    return Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency=currency)


def test_single_stock_source_totals_match_the_underlying_stock(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    data=portfolio.data
    # Money_invested (the data column): current cost basis still held, reduced by the 4-unit
    # sell -> 1000*0.6 = 600. total_invested_money: gross amount ever bought (1000), unreduced
    # by later sells — the two track different things (position size vs. lifetime capital in).
    assert data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(600.0)
    assert portfolio.total_invested_money==pytest.approx(1000.0)
    # Portfolio.distribution_by_ticker is keyed the same way Stock.distribution_by_ticker is —
    # by the CSV's isin column, not the yfinance ticker symbol.
    assert 'US0000000001' in portfolio.distribution_by_ticker
    assert portfolio.distribution_by_directory
    assert sum(portfolio.distribution_by_directory.values())==pytest.approx(100.0)


def test_zero_day_row_is_prepended_before_the_first_transaction(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    data=portfolio.data
    assert data.index[0]<data.index[1]
    first_row=data.iloc[0]
    assert (first_row==0.0).all()


def test_distribution_by_currency_single_stock_source(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    # The single ticker's own native currency ('usd'), uppercased - not the ticker/isin, and not
    # currency='usd' (the target everything's converted to) by coincidence, but by definition.
    assert portfolio.distribution_by_currency==pytest.approx({'USD': 100.0})
    assert portfolio.distribution_by_currency_current_value==pytest.approx({'USD': 100.0})
    assert portfolio.distribution_by_currency_revenue==pytest.approx({'USD': 100.0})


def test_distribution_by_currency_splits_across_two_stock_native_currencies(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n"   # 500, native usd
                   "2024-01-20,DE0000000002,2,150.0,0.0\n",  # 300, native eur
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "DE0000000002": {"ticker": "FAKEEUR", "currency": "eur"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd')

    # distribution_by_ticker is keyed by isin (two entries); distribution_by_currency instead
    # groups those same two positions into just 'USD'/'EUR' - fewer keys than distribution_by_ticker
    # whenever two tickers share a native currency, though not exercised by this particular case.
    assert set(portfolio.distribution_by_currency)=={'USD', 'EUR'}
    assert sum(portfolio.distribution_by_currency.values())==pytest.approx(100.0)
    # Matches distribution_by_ticker's own split for each single-currency ticker exactly, since
    # each currency bucket here holds only one position.
    assert portfolio.distribution_by_currency['USD']==pytest.approx(portfolio.distribution_by_ticker['US0000000001'])
    assert portfolio.distribution_by_currency['EUR']==pytest.approx(portfolio.distribution_by_ticker['DE0000000002'])


def test_distribution_by_currency_groups_same_currency_tickers_together(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n"
                   "2024-01-20,US0000000003,2,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "US0000000003": {"ticker": "FAKEUSD2", "currency": "usd"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd')

    # Two distinct tickers, both native-usd - distribution_by_ticker has two entries,
    # distribution_by_currency collapses them into one 'USD': 100.0 bucket.
    assert len(portfolio.distribution_by_ticker)==2
    assert portfolio.distribution_by_currency==pytest.approx({'USD': 100.0})


def test_distribution_by_currency_multi_source_stock_and_bonds(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    start=date.today()-timedelta(days=3)
    bonds_dir=make_source_dir('bonds', {
        'buy.csv': "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
                   f"{start.isoformat()},TOS0327,1,0.0,6.0,False\n",
        'interest_rate.csv': "date,rate\n01-2020,5.0\n",
        'inflation_rate.csv': "date,inflation\n01-2020,4.0\n",
    })

    portfolio=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency='usd')

    # Every Polish retail bond is natively PLN (PolishRetailBonds.NATIVE_CURRENCY) regardless of
    # currency='usd' above - self.data/totals are converted to usd, but distribution_by_currency
    # tracks what each position actually IS denominated in, so PLN still shows up here.
    assert set(portfolio.distribution_by_currency)=={'USD', 'PLN'}
    assert sum(portfolio.distribution_by_currency.values())==pytest.approx(100.0)
    assert sum(portfolio.distribution_by_currency_current_value.values())==pytest.approx(100.0)
    assert sum(portfolio.distribution_by_currency_revenue.values())==pytest.approx(100.0)


def test_multi_source_portfolio_sums_stock_and_bonds(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    start=date.today()-timedelta(days=3)
    bonds_dir=make_source_dir('bonds', {
        'buy.csv': "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
                   f"{start.isoformat()},TOS0327,1,0.0,6.0,False\n",
        'interest_rate.csv': "date,rate\n01-2020,5.0\n",
        'inflation_rate.csv': "date,inflation\n01-2020,4.0\n",
    })

    portfolio=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency='usd')

    # Portfolio now passes its own currency down to PolishRetailBonds (Roadmap: apply currency
    # conversion to PolishRetailBonds) - read the bond's own USD-converted total directly instead
    # of hardcoding 100.0 PLN, so this test doesn't depend on the fake FX rate's exact value.
    from Portfolio_calculator_library import PolishRetailBonds
    bonds_alone=PolishRetailBonds(bonds_dir, 'usd')
    assert portfolio.total_invested_money==pytest.approx(1000.0+bonds_alone.total_money_invested)
    assert set(portfolio.distribution_by_directory)=={stock_dir, bonds_dir}
    assert sum(portfolio.distribution_by_directory.values())==pytest.approx(100.0)
    # Dividend column only exists because the stock source contributed one.
    assert Portfolio.DIVIDEND_COLUMN in portfolio.data.columns


def test_profit_column_keeps_a_matured_bonds_realized_gain_but_drops_its_cost_basis(make_source_dir, make_tickers_json):
    """Portfolio-level check of PROFIT_COLUMN/MONEY_INVESTED_COLUMN once a bond has matured: its
    interest was realized at redemption and must persist in the merged Profit column forever
    after, while its cost basis (Money_invested) must drop out entirely, since nothing is still
    held from it — see bonds_calculator_library.PolishRetailBonds._bond_dataframe. Compares
    against a stock-only portfolio built from the exact same stock data, so the bond's isolated
    contribution can be read off as a plain difference rather than hardcoded expected totals."""
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    matured_start=date.today()-timedelta(days=100)  # OTS's 3-month term has long since ended
    bonds_dir=make_source_dir('bonds', {
        'buy.csv': "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
                   f"{matured_start.isoformat()},OTS0826,5,0.0,2.0,False\n",
        'interest_rate.csv': "date,rate\n01-2020,5.0\n",
        'inflation_rate.csv': "date,inflation\n01-2020,4.0\n",
    })

    stock_only=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd')
    mixed=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency='usd')

    from Portfolio_calculator_library import PolishRetailBonds
    # 'usd', matching currency='usd' above - Portfolio now converts bonds through its own
    # currency (Roadmap: apply currency conversion to PolishRetailBonds), so the standalone
    # comparison value must be converted the same way to still be comparable.
    bonds_only=PolishRetailBonds(bonds_dir, 'usd')
    matured_bond_revenue=bonds_only.data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]
    assert matured_bond_revenue>0.0  # sanity check: the bond actually earned something before maturing

    # Cost basis: the matured bond contributes nothing - identical to the stock-only portfolio.
    assert mixed.data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(stock_only.data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1])

    # Profit: the bond's realized gain is still added on top, even though it matured before today.
    assert mixed.data[Portfolio.PROFIT_COLUMN].iloc[-1]==pytest.approx(stock_only.data[Portfolio.PROFIT_COLUMN].iloc[-1]+matured_bond_revenue)

    # total_invested_money is lifetime (like Stock's own), so it still counts the matured bond.
    assert mixed.total_invested_money==pytest.approx(stock_only.total_invested_money+bonds_only.total_money_invested)


def test_cache_dir_is_reused_across_portfolio_constructions(make_source_dir, make_tickers_json, cache_dir, mock_yfinance):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd', cache_dir=cache_dir)
    calls_after_first=len(mock_yfinance.call_log)
    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd', cache_dir=cache_dir)
    # The second construction should have been served entirely from cache_dir.
    assert len(mock_yfinance.call_log)==calls_after_first


def test_force_refresh_ignores_the_cache(make_source_dir, make_tickers_json, cache_dir, mock_yfinance):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd', cache_dir=cache_dir)
    calls_after_first=len(mock_yfinance.call_log)
    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency='usd', cache_dir=cache_dir, force_refresh=True)
    assert len(mock_yfinance.call_log)>calls_after_first


def test_calculate_irr_starts_at_zero_and_covers_every_row(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    portfolio.calculate_irr()
    irr=portfolio.portfolio[Portfolio.IRR_COLUMN]
    # Day 0's IRR is always exactly 0%: guess**0==1 regardless of whether Newton's method
    # converges on that first (degenerate, all-zero-cashflow) iteration.
    assert irr.iloc[0]==pytest.approx(0.0)
    assert len(irr)==len(portfolio.portfolio)


def test_calculate_money_earned_between_dates_matches_column_version(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    portfolio.calculate_irr()  # populates self.portfolio, which the two methods below read from
    from datetime import datetime as dt
    single_value=portfolio.calculate_money_earned_between_dates(dt(2024, 1, 15), dt(2024, 6, 1))
    days_between=(dt(2024, 6, 1)-dt(2024, 1, 15)).days
    column=portfolio.calculate_money_earned_between_dates_column(days_between=days_between, offset=0)
    row_at_end_date=column.loc['2024-06-01', Portfolio.DAILY_RETURN_COLUMN]
    assert row_at_end_date==pytest.approx(round(single_value/days_between, 2))
