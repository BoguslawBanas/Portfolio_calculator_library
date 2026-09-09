"""Tests for stock_calculator_library.Stock against synthetic, fixed data (no network)."""

import pytest

from Portfolio_calculator_library import Stock


def build_stock(make_source_dir, make_tickers_json, csv_files, tickers, currency_to='usd', **kwargs):
    stock_dir=make_source_dir('stocks', csv_files)
    tickers_json=make_tickers_json(tickers)
    return Stock(stock_dir, tickers_json, currency_to, **kwargs)


def test_buy_only_accumulates_money_invested_and_units(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                    "2024-01-15,US0000000001,5,100.0,0.0\n"
                    "2024-02-01,US0000000001,3,110.0,0.01\n"},
        {"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}},
    )
    data=stock.data
    # The computed range starts on the first buy's own date (no day-before-zero row here,
    # unlike Portfolio's merge), so day 0 already reflects that first buy, not zero.
    assert data[Stock.MONEY_INVESTED_COLUMN].iloc[0]==pytest.approx(500.0)
    # After both buys: 5*100 + (1.01)*3*110 = 500 + 333.3 = 833.3, up to FX (flat 1.0, same currency).
    expected=round(5*100.0, 2)+round(1.01*3*110.0, 2)
    assert data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)
    assert stock.total_money_invested==pytest.approx(expected)
    # distribution_by_ticker/native_data are keyed by the CSV's isin column (CSV_TICKER_COLUMN),
    # not the yfinance ticker symbol looked up from tickers.json — despite the name.
    assert stock.distribution_by_ticker['US0000000001']==pytest.approx(100.0)


def test_partial_sell_preserves_average_cost_basis(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {
            'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                       "2024-01-15,US0000000001,10,100.0,0.0\n",
            'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                        "2024-03-01,US0000000001,4,120.0\n",
        },
        {"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}},
    )
    data=stock.data
    # Selling 4 of 10 units removes 40% of the cost basis (1000 -> 600), average price unchanged.
    assert data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(600.0)
    # Realized profit = proceeds (4*120) - cost basis removed (400) = 80.
    assert data[Stock.REALIZED_PROFIT_COLUMN].iloc[-1]==pytest.approx(80.0)


def test_oversell_raises_value_error(make_source_dir, make_tickers_json):
    with pytest.raises(ValueError, match="Cannot sell"):
        build_stock(
            make_source_dir, make_tickers_json,
            {
                'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                           "2024-01-15,US0000000001,5,100.0,0.0\n",
                'sell.csv': "date,isin,amount_of_units,price_of_unit\n"
                            "2024-02-01,US0000000001,999,100.0\n",
            },
            {"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}},
        )


def test_dividends_and_dividend_tax_tracked_separately_from_price_gain(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {
            'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                       "2024-01-15,US0000000001,10,100.0,0.0\n",
            'dividend.csv': "date,isin,dividend\n2024-06-01,US0000000001,25.0\n",
            'dividend_tax.csv': "date,isin,dividend_tax\n2024-06-01,US0000000001,4.75\n",
        },
        {"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}},
    )
    data=stock.data
    assert data[Stock.DIVIDEND_COLUMN].iloc[-1]==pytest.approx(25.0-4.75)
    # Money invested/units are untouched by a dividend.
    assert data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1000.0)


def test_missing_csvs_do_not_error_only_buy_present(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                    "2024-01-15,US0000000001,5,100.0,0.0\n"},
        {"US0000000001": {"ticker": "FAKEUSD", "currency": "usd"}},
    )
    assert stock.data[Stock.DIVIDEND_COLUMN].iloc[-1]==0.0
    assert stock.data[Stock.REALIZED_PROFIT_COLUMN].iloc[-1]==0.0


def test_foreign_currency_ticker_is_converted(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                    "2024-01-15,DE0000000002,5,50.0,0.0\n"},
        {"DE0000000002": {"ticker": "FAKEEUR", "currency": "eur"}},
        currency_to='usd',
    )
    # FakeTicker's EUR/USD rate isn't flat 1.0, so a straight EUR cost basis wouldn't match.
    assert stock.data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]!=pytest.approx(250.0)
    assert stock.data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]>0.0


def test_include_native_currency_isolates_fx_movement(make_source_dir, make_tickers_json):
    stock=build_stock(
        make_source_dir, make_tickers_json,
        {'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                    "2024-01-15,DE0000000002,5,50.0,0.0\n"},
        {"DE0000000002": {"ticker": "FAKEEUR", "currency": "eur"}},
        currency_to='usd', include_native_currency=True,
    )
    assert 'DE0000000002' in stock.native_data
    assert stock.native_currency['DE0000000002']=='eur'
    # Native (EUR) cost basis is the flat EUR calculation, no FX applied.
    assert stock.native_data['DE0000000002'][Stock.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(250.0)


def test_merge_sums_multiple_tickers_by_date(make_source_dir, make_tickers_json):
    stock_dir=make_source_dir('stocks', {
        'buy.csv': "date,isin,amount_of_units,price_of_unit,penalty\n"
                   "2024-01-15,US0000000001,5,100.0,0.0\n"
                   "2024-01-20,US0000000003,2,200.0,0.0\n",
    })
    tickers_json=make_tickers_json({
        "US0000000001": {"ticker": "FAKEUSD", "currency": "usd"},
        "US0000000003": {"ticker": "FAKEUSD2", "currency": "usd"},
    })
    stock=Stock(stock_dir, tickers_json, 'usd')
    expected_total=round(5*100.0, 2)+round(2*200.0, 2)
    assert stock.data[Stock.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected_total)
    assert set(stock.distribution_by_ticker)=={'US0000000001', 'US0000000003'}
    assert sum(stock.distribution_by_ticker.values())==pytest.approx(100.0)
