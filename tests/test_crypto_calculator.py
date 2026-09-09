"""Tests for crypto_calculator_library.Crypto against synthetic, fixed data (no network)."""

import pytest

from Portfolio_calculator_library import Crypto


def build_crypto(make_source_dir, csv_files, currency_to='usd', **kwargs):
    crypto_dir=make_source_dir('crypto', csv_files)
    return Crypto(crypto_dir, currency_to, **kwargs)


def test_buy_only_accumulates_money_invested_and_units(make_source_dir):
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,bitcoin,0.5,40000.0,0.01\n",
    })
    data=crypto.data
    expected=round(1.01*0.5*40000.0, 2)
    assert data[Crypto.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)
    assert crypto.distribution_by_ticker['bitcoin']==pytest.approx(100.0)


def test_units_rounded_to_eight_decimals_not_stocks_four(make_source_dir):
    # amount_of_units itself (0.123456789) isn't what's charged — money invested is
    # amount*price*(1+fee), so the 8-decimal rounding only shows up if it happened before
    # the multiply. Compare against Stock/Commodity's 4-decimal rounding to pin down which one
    # ran: round(x, 4) and round(x, 8) disagree for this input, so the two expectations differ.
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,bitcoin,0.123456789,40000.0,0.0\n",
    })
    rounded_to_8=round(0.123456789, 8)*40000.0
    rounded_to_4=round(0.123456789, 4)*40000.0
    assert rounded_to_8!=pytest.approx(rounded_to_4)
    assert crypto.data[Crypto.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(rounded_to_8)


def test_partial_sell_preserves_average_cost_basis(make_source_dir):
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,ethereum,10,2000.0,0.0\n",
        'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                    "2024-03-01,ethereum,4,2200.0\n",
    })
    data=crypto.data
    assert data[Crypto.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(20000.0*0.6)
    assert data[Crypto.REALIZED_PROFIT_COLUMN].iloc[-1]==pytest.approx(4*2200.0-20000.0*0.4)


def test_oversell_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="Cannot sell"):
        build_crypto(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                       "2024-01-15,dogecoin,100,0.1,0.0\n",
            'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                        "2024-02-01,dogecoin,999,0.1\n",
        })


def test_no_dividend_column(make_source_dir):
    assert not hasattr(Crypto, 'DIVIDEND_COLUMN')
