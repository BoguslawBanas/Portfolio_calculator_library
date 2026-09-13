"""Tests for commodity_calculator_library.Commodity against synthetic, fixed data (no network)."""

import pytest

from Portfolio_calculator_library import Commodity

# FakeTicker's day-0 close (the earliest transaction's own date, since _compute_data's price
# history starts at dataframe.index.min()) is always 100.0 + 0.37*((0*7)%11) - 0.5 == 99.5,
# for every commodity futures symbol (none of them end in '=X') - see tests/conftest.py.
DAY_0_CLOSE=99.5


def build_commodity(make_source_dir, csv_files, currency_to='usd', **kwargs):
    commodity_dir=make_source_dir('commodities', csv_files)
    return Commodity(commodity_dir, currency_to, **kwargs)


def test_buy_only_accumulates_money_invested_and_units(make_source_dir):
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                   "2024-01-15,gold,2,troy_ounce,0.02\n",
    })
    data=commodity.data
    # gold's own yfinance quote unit IS troy_ounce, so no unit conversion changes the quantity.
    expected=round(1.02*2*DAY_0_CLOSE, 2)
    assert data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)
    assert commodity.distribution_by_ticker['gold']==pytest.approx(100.0)


def test_partial_sell_preserves_average_cost_basis(make_source_dir):
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                   "2024-01-15,gold,10,troy_ounce,0.0\n",
        'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                    "2024-03-01,gold,4,120.0\n",
    })
    data=commodity.data
    # 10 troy ounces bought at day-0's fake close (99.5, no premium) -> cost basis 995.0;
    # selling 4/10 removes 4/10 of that cost basis (398.0), leaving 597.0. sell.csv still
    # carries its own real price_of_unit, unaffected by this buy-side change.
    money_invested=10*DAY_0_CLOSE
    money_invested_removed=round(money_invested*0.4, 2)
    assert data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(round(money_invested-money_invested_removed, 2))
    assert data[Commodity.REALIZED_PROFIT_COLUMN].iloc[-1]==pytest.approx(round(4*120.0-money_invested_removed, 2))


def test_oversell_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="Cannot sell"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                       "2024-01-15,silver,5,troy_ounce,0.0\n",
            'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                        "2024-02-01,silver,999,25.0\n",
        })


def test_no_dividend_column_profit_is_unrealized_plus_realized(make_source_dir):
    # Sell part (not all) of the position — selling to zero would leave total_current_value at
    # 0, an unrelated pre-existing division-by-zero edge case this test isn't about.
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                   "2024-01-15,gold,10,troy_ounce,0.0\n",
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
        {'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                    "2024-01-15,gold,2,troy_ounce,0.0\n"},
        currency_to='eur', include_native_currency=True,
    )
    assert 'gold' in commodity.native_data
    assert commodity.native_currency['gold']=='usd'
    assert commodity.native_data['gold'][Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(2*DAY_0_CLOSE)


def test_gram_and_troy_ounce_units_convert_to_the_same_money_invested(make_source_dir):
    """A buy recorded in grams should land on the exact same cost basis as the equivalent
    quantity recorded in troy ounces - the whole point of CSV_UNIT_COLUMN/UNIT_TO_GRAMS."""
    two_troy_ounces_in_grams=2*Commodity.GRAMS_PER_TROY_OUNCE
    commodity_oz=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                   "2024-01-15,gold,2,troy_ounce,0.0\n",
    })
    commodity_g=build_commodity(make_source_dir, {
        'buy.csv': f"date,symbol,amount_of_units,unit,premium\n"
                   f"2024-01-15,gold,{two_troy_ounces_in_grams},gram,0.0\n",
    })
    assert commodity_g.data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(
        commodity_oz.data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]
    )


def test_copper_quote_unit_is_pounds_not_troy_ounce(make_source_dir):
    """Copper's own yfinance futures ticker (HG=F) quotes USD per pound, unlike the other four
    metals here (all quoted per troy ounce) - QUOTE_UNIT_GRAMS must convert against the right
    one per symbol, not a single hardcoded unit for every commodity."""
    one_pound_in_grams=Commodity.GRAMS_PER_POUND
    commodity=build_commodity(make_source_dir, {
        'buy.csv': f"date,symbol,amount_of_units,unit,premium\n"
                   f"2024-01-15,copper,{one_pound_in_grams},gram,0.0\n",
    })
    # 1 pound of copper converts to exactly 1.0 unit in copper's own quoted unit.
    expected=round(1.0*DAY_0_CLOSE, 2)
    assert commodity.data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)


def test_unknown_unit_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="[Uu]nit"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,premium\n"
                       "2024-01-15,gold,2,kilogram,0.0\n",
        })
