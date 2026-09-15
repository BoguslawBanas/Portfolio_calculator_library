"""Tests for crypto_calculator_library.Crypto against synthetic, fixed data (no network)."""

import pytest

from Portfolio_calculator_library import Crypto


def build_crypto(make_source_dir, csv_files, currency_to='usd', **kwargs):
    crypto_dir=make_source_dir('crypto', csv_files)
    return Crypto(crypto_dir, currency_to, **kwargs)


def test_invalid_ticker_raises_instead_of_returning_nan(make_source_dir, monkeypatch):
    # FakeTicker returns an empty DataFrame for any symbol starting with 'INVALID', mirroring
    # real yfinance's behavior for an invalid ticker - must raise here, not silently produce a
    # Close column of NaN (README Roadmap item). TICKERS maps a fixed set of known coins to
    # real yfinance symbols, so monkeypatch in a throwaway entry pointing at a symbol FakeTicker
    # treats as invalid, rather than one of the real (always-valid) ones.
    monkeypatch.setitem(Crypto.TICKERS, 'notarealcoin', 'INVALIDCOIN-USD')
    with pytest.raises(ValueError, match="INVALIDCOIN-USD"):
        build_crypto(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                       "2024-01-15,notarealcoin,0.5,40000.0,0.01\n",
        })


def test_nonexistent_directory_raises_clear_value_error(tmp_path):
    # A nonexistent directory_path used to surface as a raw FileNotFoundError straight from
    # os.listdir ([WinError 3]/[Errno 2]) instead of this library's own established clear-error
    # convention (README Roadmap item).
    missing_dir=str(tmp_path/'does_not_exist')
    with pytest.raises(ValueError, match="No such directory"):
        Crypto(missing_dir, 'usd')


def test_buy_only_accumulates_money_invested_and_units(make_source_dir):
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,bitcoin,0.5,40000.0,0.01\n",
    })
    data=crypto.data
    expected=round(1.01*0.5*40000.0, 2)
    assert data[Crypto.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)
    assert crypto.distribution_by_ticker['bitcoin']==pytest.approx(100.0)


def test_custom_symbol_via_tickers_json_is_usable(make_source_dir, make_tickers_json):
    # A new coin beyond the built-in TICKERS - tickers_json is a plain {symbol: yfinance
    # ticker} mapping, simpler than Commodity's (no per-symbol quote_unit needed, since every
    # crypto ticker here is directly USD-quoted per whole coin) (README Roadmap item).
    tickers_path=make_tickers_json({"notarealcoin": "NRC-USD"}, filename='crypto_tickers.json')
    crypto=build_crypto(
        make_source_dir,
        {'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                    "2024-01-15,notarealcoin,0.5,40000.0,0.01\n"},
        tickers_json=tickers_path,
    )
    assert crypto.tickers['notarealcoin']=='NRC-USD'
    expected=round(1.01*0.5*40000.0, 2)
    assert crypto.data[Crypto.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(expected)


def test_tickers_json_entry_overrides_a_built_in_symbol(make_source_dir, make_tickers_json, mock_yfinance):
    tickers_path=make_tickers_json({"bitcoin": "CUSTOM-BTC-USD"})
    build_crypto(
        make_source_dir,
        {'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                    "2024-01-15,bitcoin,0.5,40000.0,0.01\n"},
        tickers_json=tickers_path,
    )
    assert 'CUSTOM-BTC-USD' in mock_yfinance.call_log
    assert 'BTC-USD' not in mock_yfinance.call_log


def test_repr_shows_invested_current_value_and_revenue(make_source_dir):
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,bitcoin,0.5,40000.0,0.01\n",
    })
    representation=repr(crypto)
    assert representation.startswith("Crypto(")
    assert f"invested={crypto.total_money_invested:.2f}" in representation
    assert f"current_value={crypto.total_current_value:.2f}" in representation
    assert f"revenue={crypto.total_revenue:.2f}" in representation


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
    # No dividends -> Profit_without_realized collapses to just the unrealized component,
    # excluding the realized gain from the partial sell above.
    assert data[Crypto.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]==pytest.approx(data[Crypto.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1])
    assert data[Crypto.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]!=pytest.approx(data[Crypto.PROFIT_COLUMN].iloc[-1])


def test_full_sell_at_cost_zeroes_current_value_and_revenue_without_nan(make_source_dir):
    # Same division-by-zero edge case as Stock's own version of this test (README Roadmap item):
    # selling every unit at exactly the buy price drives total_current_value/total_revenue to
    # exactly 0.0, which distribution_by_ticker_current_value/_revenue must guard to 0.0 rather
    # than dividing by zero.
    crypto=build_crypto(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,price_of_unit,fee\n"
                   "2024-01-15,bitcoin,5,40000.0,0.0\n",
        'sell.csv': "date,symbol,amount_of_units,price_of_unit\n"
                    "2024-03-01,bitcoin,5,40000.0\n",
    })
    assert crypto.total_current_value==pytest.approx(0.0)
    assert crypto.total_revenue==pytest.approx(0.0)
    assert crypto.distribution_by_ticker_current_value==pytest.approx({'bitcoin': 0.0})
    assert crypto.distribution_by_ticker_revenue==pytest.approx({'bitcoin': 0.0})


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
