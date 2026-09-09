"""Tests for commodity_calculator_library.Commodity against synthetic, fixed data (no network)."""

import pytest

from Portfolio_calculator_library import Commodity


def build_commodity(make_source_dir, csv_files, currency_to='usd', **kwargs):
    commodity_dir=make_source_dir('commodities', csv_files)
    return Commodity(commodity_dir, currency_to, **kwargs)


def test_buy_only_accumulates_money_invested_and_units(make_source_dir):
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,premium\n"
                   "2024-01-15,gold,2,1900.0,0.02\n",
    })
    data=commodity.data
    expected=round(1.02*2*1900.0, 2)
    assert data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)
    assert commodity.distribution_by_ticker['gold']==pytest.approx(100.0)


def test_partial_sell_preserves_average_cost_basis(make_source_dir):
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,premium\n"
                   "2024-01-15,gold,10,100.0,0.0\n",
        'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                    "2024-03-01,gold,4,120.0\n",
    })
    data=commodity.data
    assert data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(600.0)
    assert data[Commodity.REALIZED_PROFIT_COLUMN].iloc[-1]==pytest.approx(80.0)


def test_oversell_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="Cannot sell"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,price_of_unit,premium\n"
                       "2024-01-15,silver,5,25.0,0.0\n",
            'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                        "2024-02-01,silver,999,25.0\n",
        })


def test_no_dividend_column_profit_is_unrealized_plus_realized(make_source_dir):
    # Sell part (not all) of the position — selling to zero would leave total_current_value at
    # 0, an unrelated pre-existing division-by-zero edge case this test isn't about.
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,premium\n"
                   "2024-01-15,gold,10,100.0,0.0\n",
        'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                    "2024-02-01,gold,4,110.0\n",
    })
    assert not hasattr(Commodity, 'DIVIDEND_COLUMN')
    data=commodity.data
    # No dividends -> Profit is exactly unrealized (Profit_without_dividends) plus realized gain.
    expected=data[Commodity.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]+data[Commodity.REALIZED_PROFIT_COLUMN].iloc[-1]
    assert data[Commodity.PROFIT_COLUMN].iloc[-1]==pytest.approx(round(expected, 2))


def test_include_native_currency_isolates_fx_movement(make_source_dir):
    commodity=build_commodity(
        make_source_dir,
        {'buy.csv': "date,symbol,amount_of_units,price_of_unit,premium\n"
                    "2024-01-15,gold,2,1900.0,0.0\n"},
        currency_to='eur', include_native_currency=True,
    )
    assert 'gold' in commodity.native_data
    assert commodity.native_currency['gold']=='usd'
    assert commodity.native_data['gold'][Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(2*1900.0)
