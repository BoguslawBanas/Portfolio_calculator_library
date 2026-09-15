"""
Tests for portfolio_calculator_library.Portfolio, combining multiple asset-type sources —
the integration layer on top of the individual Stock/Bonds/Commodity/Crypto suites.
"""

from datetime import date, timedelta
import warnings

import pytest

from Portfolio_calculator_library import Portfolio


def build_single_stock_portfolio(make_source_dir, make_tickers_json, currency_to='usd'):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
        'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                    "2024-03-01,US0000000001,4,120.0\n",
        'dividend.csv': "date,isin,dividend\n2024-06-01,US0000000001,25.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    return Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to=currency_to)


def test_single_stock_source_totals_match_the_underlying_stock(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    data=portfolio.data
    # Money_invested (the data column): current cost basis still held, reduced by the 4-unit
    # sell -> 1000*0.6 = 600. total_money_invested: gross amount ever bought (1000), unreduced
    # by later sells — the two track different things (position size vs. lifetime capital in).
    assert data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(600.0)
    assert portfolio.total_money_invested==pytest.approx(1000.0)
    # total_money_currently_invested tracks the same "still held" figure data[Money_invested]
    # already does - unlike total_money_invested, reduced by the 4-unit sell.
    assert portfolio.total_money_currently_invested==pytest.approx(600.0)
    # Portfolio.distribution_by_ticker is keyed the same way Stock.distribution_by_ticker is —
    # by the CSV's isin column, not the yfinance ticker symbol.
    assert 'US0000000001' in portfolio.distribution_by_ticker
    assert portfolio.distribution_by_directory
    assert sum(portfolio.distribution_by_directory.values())==pytest.approx(100.0)
    assert portfolio.distribution_by_ticker_currently_invested['US0000000001']==pytest.approx(100.0)
    assert sum(portfolio.distribution_by_directory_currently_invested.values())==pytest.approx(100.0)


def test_profit_without_realized_excludes_the_sells_locked_in_gain(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    data=portfolio.data
    from Portfolio_calculator_library import Stock
    # Same source data, computed standalone, to read off exactly how much of Profit is the
    # 4-unit sell's realized gain (proceeds 4*120 minus 40% of the 1000 cost basis = 80.0).
    stock_dir=list(portfolio.distribution_by_directory)[0]
    stock=Stock(stock_dir, make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}}), 'usd')
    realized=stock.data[Stock.REALIZED_PROFIT_COLUMN].iloc[-1]
    assert realized==pytest.approx(80.0)

    assert data[Portfolio.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]==pytest.approx(data[Portfolio.PROFIT_COLUMN].iloc[-1]-realized)
    # The net dividend (25.0-0.0, no dividend_tax.csv here) is still included, unlike realized.
    assert data[Portfolio.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]==pytest.approx(data[Portfolio.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]+data[Portfolio.DIVIDEND_COLUMN].iloc[-1])

    # Profit_excluding_dividends is the mirror image: keeps the realized 80.0, drops the dividend.
    assert data[Portfolio.PROFIT_EXCLUDING_DIVIDEND_COLUMN].iloc[-1]==pytest.approx(data[Portfolio.PROFIT_COLUMN].iloc[-1]-data[Portfolio.DIVIDEND_COLUMN].iloc[-1])
    assert data[Portfolio.PROFIT_EXCLUDING_DIVIDEND_COLUMN].iloc[-1]==pytest.approx(data[Portfolio.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]+realized)


def test_repr_shows_invested_current_value_and_revenue(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    representation=repr(portfolio)
    assert representation.startswith("Portfolio(")
    assert f"invested={portfolio.total_money_invested:.2f}" in representation
    assert f"current_value={portfolio.total_current_value:.2f}" in representation
    assert f"revenue={portfolio.total_revenue:.2f}" in representation


def test_zero_day_row_is_prepended_before_the_first_transaction(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    data=portfolio.data
    assert data.index[0]<data.index[1]
    first_row=data.iloc[0]
    assert (first_row==0.0).all()


def test_distribution_by_currency_single_stock_source(make_source_dir, make_tickers_json):
    portfolio=build_single_stock_portfolio(make_source_dir, make_tickers_json)
    # The single ticker's own native currency ('usd'), uppercased - not the ticker/isin, and not
    # currency_to='usd' (the target everything's converted to) by coincidence, but by definition.
    assert portfolio.distribution_by_currency==pytest.approx({'USD': 100.0})
    assert portfolio.distribution_by_currency_current_value==pytest.approx({'USD': 100.0})
    assert portfolio.distribution_by_currency_revenue==pytest.approx({'USD': 100.0})


def test_distribution_by_currency_splits_across_two_stock_native_currencies(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n"   # 500, native usd
                   "2024-01-20,DE0000000002,2,150.0,0.0\n",  # 300, native eur
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "DE0000000002": {"ticker": "FAKEEUR", "currency": "eur"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')

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
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n"
                   "2024-01-20,US0000000003,2,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "US0000000003": {"ticker": "FAKEUSD2", "currency": "usd"},
    })
    portfolio=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')

    # Two distinct tickers, both native-usd - distribution_by_ticker has two entries,
    # distribution_by_currency collapses them into one 'USD': 100.0 bucket.
    assert len(portfolio.distribution_by_ticker)==2
    assert portfolio.distribution_by_currency==pytest.approx({'USD': 100.0})


def test_distribution_by_ticker_accumulates_same_isin_across_two_sources(make_source_dir, make_tickers_json):
    # Same ISIN split across two separate source directories (e.g. two different brokers) -
    # _absorb_source used to overwrite distribution_by_ticker[isin] with the later source's own
    # amount instead of adding to it, silently dropping the first source's contribution even
    # though total_money_invested itself stayed correct (README Roadmap item).
    stock_dir1=make_source_dir('broker_a', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n",
    })
    stock_dir2=make_source_dir('broker_b', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-20,US0000000001,5,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    portfolio=Portfolio(
        {stock_dir1: 'stock', stock_dir2: 'stock'},
        tickers_json=tickers_json, currency_to='usd',
    )

    assert portfolio.total_money_invested==pytest.approx(1000.0)
    # A single ISIN held across both sources -> its distribution should reflect the combined
    # 1000 invested (100% of the portfolio), not just one source's 500 (50%).
    assert portfolio.distribution_by_ticker==pytest.approx({'US0000000001': 100.0})
    assert portfolio.distribution_by_currency==pytest.approx({'USD': 100.0})


def test_distribution_by_currency_multi_source_stock_and_bonds(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
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

    portfolio=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency_to='usd')

    # Every Polish retail bond is natively PLN (PolishRetailBonds.NATIVE_CURRENCY) regardless of
    # currency_to='usd' above - self.data/totals are converted to usd, but distribution_by_currency
    # tracks what each position actually IS denominated in, so PLN still shows up here.
    assert set(portfolio.distribution_by_currency)=={'USD', 'PLN'}
    assert sum(portfolio.distribution_by_currency.values())==pytest.approx(100.0)
    assert sum(portfolio.distribution_by_currency_current_value.values())==pytest.approx(100.0)
    assert sum(portfolio.distribution_by_currency_revenue.values())==pytest.approx(100.0)


def test_multi_source_portfolio_sums_stock_and_bonds(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
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

    portfolio=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency_to='usd')

    # Portfolio now passes its own currency down to PolishRetailBonds (Roadmap: apply currency
    # conversion to PolishRetailBonds) - read the bond's own USD-converted total directly instead
    # of hardcoding 100.0 PLN, so this test doesn't depend on the fake FX rate's exact value.
    from Portfolio_calculator_library import PolishRetailBonds
    bonds_alone=PolishRetailBonds(bonds_dir, 'usd')
    assert portfolio.total_money_invested==pytest.approx(1000.0+bonds_alone.total_money_invested)
    assert set(portfolio.distribution_by_directory)=={stock_dir, bonds_dir}
    assert sum(portfolio.distribution_by_directory.values())==pytest.approx(100.0)
    # No sell anywhere in this portfolio, so total_money_currently_invested merges correctly
    # across both a Stock source (its own genuinely-computed figure) and a PolishRetailBonds
    # source (a plain alias of its total_money_invested) to land on the same total.
    assert portfolio.total_money_currently_invested==pytest.approx(portfolio.total_money_invested)
    assert sum(portfolio.distribution_by_directory_currently_invested.values())==pytest.approx(100.0)
    # Both sources contribute a Dividend column now (Stock's per dividend.csv row, bonds' own
    # derived one - see PolishRetailBonds.DIVIDEND_COLUMN), so it's present regardless.
    assert Portfolio.DIVIDEND_COLUMN in portfolio.data.columns
    # Unlike Dividend, every source type contributes Profit_without_realized (see each source's
    # own PROFIT_WITHOUT_REALIZED_COLUMN), so it's always present - and, with no sell anywhere in
    # this portfolio, equals total Profit (nothing realized to exclude).
    assert Portfolio.PROFIT_WITHOUT_REALIZED_COLUMN in portfolio.data.columns
    assert portfolio.data[Portfolio.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]==pytest.approx(portfolio.data[Portfolio.PROFIT_COLUMN].iloc[-1])
    # Same for Profit_excluding_dividends - every source contributes it too.
    assert Portfolio.PROFIT_EXCLUDING_DIVIDEND_COLUMN in portfolio.data.columns
    assert portfolio.data[Portfolio.PROFIT_EXCLUDING_DIVIDEND_COLUMN].iloc[-1]==pytest.approx(portfolio.data[Portfolio.PROFIT_COLUMN].iloc[-1])


def test_profit_column_keeps_a_matured_bonds_realized_gain_but_drops_its_cost_basis(make_source_dir, make_tickers_json):
    """Portfolio-level check of PROFIT_COLUMN/MONEY_INVESTED_COLUMN once a bond has matured: its
    interest was realized at redemption and must persist in the merged Profit column forever
    after, while its cost basis (Money_invested) must drop out entirely, since nothing is still
    held from it — see bonds_calculator_library.PolishRetailBonds._bond_dataframe. Compares
    against a stock-only portfolio built from the exact same stock data, so the bond's isolated
    contribution can be read off as a plain difference rather than hardcoded expected totals."""
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
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

    stock_only=Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd')
    mixed=Portfolio({stock_dir: 'stock', bonds_dir: 'bonds'}, tickers_json=tickers_json, currency_to='usd')

    from Portfolio_calculator_library import PolishRetailBonds
    # 'usd', matching currency_to='usd' above - Portfolio now converts bonds through its own
    # currency (Roadmap: apply currency conversion to PolishRetailBonds), so the standalone
    # comparison value must be converted the same way to still be comparable.
    bonds_only=PolishRetailBonds(bonds_dir, 'usd')
    matured_bond_revenue=bonds_only.data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]
    assert matured_bond_revenue>0.0  # sanity check: the bond actually earned something before maturing

    # Cost basis: the matured bond contributes nothing - identical to the stock-only portfolio.
    assert mixed.data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(stock_only.data[Portfolio.MONEY_INVESTED_COLUMN].iloc[-1])

    # Profit: the bond's realized gain is still added on top, even though it matured before today.
    assert mixed.data[Portfolio.PROFIT_COLUMN].iloc[-1]==pytest.approx(stock_only.data[Portfolio.PROFIT_COLUMN].iloc[-1]+matured_bond_revenue)

    # total_money_invested is lifetime (like Stock's own), so it still counts the matured bond.
    assert mixed.total_money_invested==pytest.approx(stock_only.total_money_invested+bonds_only.total_money_invested)


def test_bank_account_source_is_wired_into_portfolio(make_source_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n",
    })
    portfolio=Portfolio({account_dir: 'bank_account'})
    assert portfolio.total_money_invested==pytest.approx(1000.0)
    assert 'savings' in portfolio.distribution_by_ticker
    assert portfolio.distribution_by_directory[account_dir]==pytest.approx(100.0)


def test_commodity_and_crypto_tickers_json_are_threaded_through(make_source_dir, make_tickers_json):
    # commodity_tickers_json/crypto_tickers_json let a Portfolio-level caller track a symbol
    # beyond each class's built-in TICKERS without touching the library source, same as
    # tickers_json already does for 'stock' sources (README Roadmap item).
    commodity_tickers_path=make_tickers_json(
        {"tin": {"ticker": "TIN=F", "quote_unit": "pound"}}, filename='commodity_tickers.json',
    )
    crypto_tickers_path=make_tickers_json({"notarealcoin": "NRC-USD"}, filename='crypto_tickers.json')

    commodities_dir=make_source_dir('commodities', {
        'buy.csv': "date,symbol,amount_of_units,unit,fee\n"
                   "2024-01-15,tin,100.0,gram,0.0\n",
    })
    crypto_dir=make_source_dir('crypto', {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,notarealcoin,0.5,40000.0,0.01\n",
    })

    portfolio=Portfolio(
        {commodities_dir: 'commodities', crypto_dir: 'crypto'},
        commodity_tickers_json=commodity_tickers_path,
        crypto_tickers_json=crypto_tickers_path,
    )
    assert set(portfolio.distribution_by_ticker)=={'tin', 'notarealcoin'}
    assert portfolio.total_money_invested>0.0


def test_cache_dir_is_reused_across_portfolio_constructions(make_source_dir, make_tickers_json, cache_dir, mock_yfinance):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd', cache_dir=cache_dir)
    calls_after_first=len(mock_yfinance.call_log)
    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd', cache_dir=cache_dir)
    # The second construction should have been served entirely from cache_dir.
    assert len(mock_yfinance.call_log)==calls_after_first


def test_force_refresh_ignores_the_cache(make_source_dir, make_tickers_json, cache_dir, mock_yfinance):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})

    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd', cache_dir=cache_dir)
    calls_after_first=len(mock_yfinance.call_log)
    Portfolio({stock_dir: 'stock'}, tickers_json=tickers_json, currency_to='usd', cache_dir=cache_dir, force_refresh=True)
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


def test_resample_accepts_deprecated_month_quarter_year_aliases(make_source_dir, make_tickers_json):
    """pandas deprecated the bare 'M'/'Q'/'Y' resample offset aliases (FutureWarning since
    pandas 2.2, in favor of 'ME'/'QE'/'YE') - Portfolio.resample() normalizes them internally
    so the old, still commonly documented single-letter spelling doesn't warn/eventually break."""
    for old, new in (('M', 'ME'), ('Q', 'QE'), ('Y', 'YE')):
        portfolio_old=build_single_stock_portfolio(make_source_dir, make_tickers_json)
        portfolio_old.calculate_irr()
        with warnings.catch_warnings():
            warnings.simplefilter('error', FutureWarning)
            resampled_old=portfolio_old.resample(old)

        portfolio_new=build_single_stock_portfolio(make_source_dir, make_tickers_json)
        portfolio_new.calculate_irr()
        resampled_new=portfolio_new.resample(new)

        assert resampled_old.index.equals(resampled_new.index)


def test_unknown_source_type_raises_instead_of_being_silently_dropped(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,US0000000001,10,100.0,0.0\n",
    })
    tickers_json=make_tickers_json({"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}})
    with pytest.raises(ValueError, match="stocks"):
        Portfolio({stock_dir: 'stocks'}, tickers_json=tickers_json)  # typo: 'stocks', not 'stock'
