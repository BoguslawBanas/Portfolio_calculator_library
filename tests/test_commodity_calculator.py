"""Tests for commodity_calculator_library.Commodity against synthetic, fixed data (no network)."""

from datetime import date

import pytest

from Portfolio_calculator_library import Commodity


def fake_close(start_date: str, transaction_date: str) -> float:
    """Reproduces tests/conftest.py's FakeTicker.history formula for a given transaction date,
    relative to a source's earliest transaction (dataframe.index.min(), where _compute_data's
    own fetched price history starts) - every commodity futures symbol falls into the non-'=X'
    branch there (base=100.0)."""
    day_index=(date.fromisoformat(transaction_date)-date.fromisoformat(start_date)).days
    return 100.0+0.37*((day_index*7) % 11)-0.5


def fake_fx(start_date: str, transaction_date: str) -> float:
    """Same as fake_close, but for an FX pair (a symbol ending '=X', base=1.10 in FakeTicker) -
    what a buy row's own currency -> currency_to conversion resolves to on a given date, relative
    to the earliest date get_cached_currency fetched that pair from (dataframe.index.min())."""
    day_index=(date.fromisoformat(transaction_date)-date.fromisoformat(start_date)).days
    return 1.10+0.37*((day_index*7) % 11)-0.5


# FakeTicker's day-0 close (a source's earliest transaction date) is always fake_close(x, x)
# == 99.5, regardless of what that date actually is.
DAY_0_CLOSE=fake_close('2024-01-15', '2024-01-15')


def build_commodity(make_source_dir, csv_files, currency_to='usd', **kwargs):
    commodity_dir=make_source_dir('commodities', csv_files)
    return Commodity(commodity_dir, currency_to, **kwargs)


def test_invalid_ticker_raises_instead_of_returning_nan(make_source_dir, monkeypatch):
    # FakeTicker returns an empty DataFrame for any symbol starting with 'INVALID', mirroring
    # real yfinance's behavior for an invalid futures ticker - must raise here, not silently
    # produce a Close column of NaN (README Roadmap item). TICKERS maps a fixed set of known
    # commodities to real yfinance symbols, so monkeypatch in a throwaway entry pointing at a
    # symbol FakeTicker treats as invalid, rather than one of the real (always-valid) ones.
    monkeypatch.setitem(Commodity.TICKERS, 'unobtainium', 'INVALIDXYZ=F')
    with pytest.raises(ValueError, match="INVALIDXYZ=F"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                       "2024-01-15,unobtainium,2,troy_ounce,200.0,usd\n",
        })


def test_nonexistent_directory_raises_clear_value_error(tmp_path):
    # A nonexistent directory_path used to surface as a raw FileNotFoundError straight from
    # os.listdir ([WinError 3]/[Errno 2]) instead of this library's own established clear-error
    # convention (README Roadmap item).
    missing_dir=str(tmp_path/'does_not_exist')
    with pytest.raises(ValueError, match="No such directory"):
        Commodity(missing_dir, 'usd')


def test_buy_money_invested_is_recorded_directly_not_derived_from_market_price(make_source_dir):
    # money_invested is the actual amount paid, taken as-is (FX-converted, but otherwise
    # untouched) - not derived from yfinance's close price the way the old fee-percentage model
    # was, so it need not bear any particular relationship to DAY_0_CLOSE at all.
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                   "2024-01-15,gold,2,troy_ounce,500.0,usd\n",
    })
    data=commodity.data
    assert float(data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(500.0)
    assert commodity.distribution_by_ticker['gold']==pytest.approx(100.0)


def test_repr_shows_invested_current_value_and_revenue(make_source_dir):
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                   "2024-01-15,gold,2,troy_ounce,500.0,usd\n",
    })
    representation=repr(commodity)
    assert representation.startswith("Commodity(")
    assert f"invested={commodity.total_money_invested:.2f}" in representation
    assert f"current_value={commodity.total_current_value:.2f}" in representation
    assert f"revenue={commodity.total_revenue:.2f}" in representation


def test_partial_sell_preserves_average_cost_basis(make_source_dir):
    start_date, sell_date='2024-01-15', '2024-03-01'
    commodity=build_commodity(make_source_dir, {
        'buy.csv': f"date,symbol,amount_of_units,unit,money_invested,currency\n"
                   f"{start_date},gold,10,troy_ounce,995.0,usd\n",
        'sell.csv': f"date,symbol,amount_of_units\n"
                    f"{sell_date},gold,4\n",
    })
    data=commodity.data
    # Cost basis is the recorded 995.0; selling 4/10 removes 4/10 of that cost basis (398.0),
    # leaving 597.0. sell.csv still carries no price of its own - proceeds come from that sell
    # date's own fake close.
    money_invested=995.0
    money_invested_removed=round(money_invested*0.4, 2)
    proceeds=4*fake_close(start_date, sell_date)
    assert float(data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(round(money_invested-money_invested_removed, 2))
    assert float(data[Commodity.REALIZED_PROFIT_COLUMN].iloc[-1])==pytest.approx(round(proceeds-money_invested_removed, 2))
    # total_money_invested stays at the lifetime-gross buy total, while
    # total_money_currently_invested drops to what's still held (post-sell cost basis) - the two
    # diverge exactly once a sell happens, same as Stock.
    assert float(commodity.total_money_invested)==pytest.approx(round(money_invested, 2))
    assert float(commodity.total_money_currently_invested)==pytest.approx(round(money_invested-money_invested_removed, 2))
    assert commodity.distribution_by_ticker_currently_invested['gold']==pytest.approx(100.0)


def test_oversell_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="Cannot sell"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                       "2024-01-15,silver,5,troy_ounce,500.0,usd\n",
            'sell.csv': "date,symbol,amount_of_units\n"
                        "2024-02-01,silver,999\n",
        })


def test_no_dividend_column_profit_is_unrealized_plus_realized(make_source_dir):
    # Sell part (not all) of the position — selling to zero would leave total_current_value at
    # 0, an unrelated pre-existing division-by-zero edge case this test isn't about.
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                   "2024-01-15,gold,10,troy_ounce,1000.0,usd\n",
        'sell.csv': "date,symbol,amount_of_units\n"
                    "2024-02-01,gold,4\n",
    })
    assert not hasattr(Commodity, 'DIVIDEND_COLUMN')
    data=commodity.data
    # No dividends -> Profit is exactly unrealized (Profit_without_dividends) plus realized gain.
    expected=data[Commodity.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]+data[Commodity.REALIZED_PROFIT_COLUMN].iloc[-1]
    assert data[Commodity.PROFIT_COLUMN].iloc[-1]==pytest.approx(round(expected, 2))
    # No dividends -> Profit_without_realized (unrealized+dividends) collapses to just the
    # unrealized component, and excludes the realized gain that's already in Profit.
    assert data[Commodity.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]==pytest.approx(data[Commodity.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1])
    assert data[Commodity.PROFIT_WITHOUT_REALIZED_COLUMN].iloc[-1]!=pytest.approx(data[Commodity.PROFIT_COLUMN].iloc[-1])
    # No dividends -> Profit_excluding_dividends is a no-op, equal to total Profit.
    assert data[Commodity.PROFIT_EXCLUDING_DIVIDEND_COLUMN].iloc[-1]==pytest.approx(data[Commodity.PROFIT_COLUMN].iloc[-1])


def test_buy_currency_is_converted_via_its_own_fx_rate(make_source_dir):
    # A buy row's own currency need not match QUOTE_CURRENCY (or currency_to) - money_invested is
    # recorded in that row's own currency and converted to currency_to via that pair's own FX
    # rate on the row's own transaction date, the same convention Stock uses for a foreign ticker.
    commodity=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                   "2024-01-15,gold,2,troy_ounce,500.0,eur\n",
    }, currency_to='usd')
    expected=round(500.0*fake_fx('2024-01-15', '2024-01-15'), 2)
    assert float(commodity.data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(expected)


def test_missing_currency_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="currency"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,money_invested\n"
                       "2024-01-15,gold,2,troy_ounce,500.0\n",
        })


def test_include_native_currency_isolates_fx_movement(make_source_dir):
    commodity=build_commodity(
        make_source_dir,
        {'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                    "2024-01-15,gold,2,troy_ounce,500.0,usd\n"},
        currency_to='eur', include_native_currency=True,
    )
    assert 'gold' in commodity.native_data
    assert commodity.native_currency['gold']=='usd'
    # Recomputed in QUOTE_CURRENCY ('usd', matching this buy row's own currency), so it's exactly
    # the recorded amount, no FX involved - unlike self.data, which is currency_to='eur'-converted
    # and so diverges from it.
    assert float(commodity.native_data['gold'][Commodity.MONEY_INVESTED_COLUMN].iloc[-1])==pytest.approx(500.0)
    assert float(commodity.data[Commodity.MONEY_INVESTED_COLUMN].iloc[-1])!=pytest.approx(500.0)


def test_gram_and_troy_ounce_units_convert_to_the_same_physical_quantity(make_source_dir):
    """A buy recorded in grams should land on the exact same physical quantity (and so the same
    total_current_value, Units * Close - independent of money_invested, which is now recorded
    directly rather than derived) as the equivalent quantity recorded in troy ounces - the whole
    point of CSV_UNIT_COLUMN/UNIT_TO_GRAMS."""
    two_troy_ounces_in_grams=2*Commodity.GRAMS_PER_TROY_OUNCE
    commodity_oz=build_commodity(make_source_dir, {
        'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                   "2024-01-15,gold,2,troy_ounce,500.0,usd\n",
    })
    commodity_g=build_commodity(make_source_dir, {
        'buy.csv': f"date,symbol,amount_of_units,unit,money_invested,currency\n"
                   f"2024-01-15,gold,{two_troy_ounces_in_grams},gram,500.0,usd\n",
    })
    assert commodity_g.total_current_value==pytest.approx(commodity_oz.total_current_value)


def test_copper_quote_unit_is_pounds_not_troy_ounce(make_source_dir):
    """Copper's own yfinance futures ticker (HG=F) quotes USD per pound, unlike the other four
    metals here (all quoted per troy ounce) - QUOTE_UNIT_GRAMS must convert against the right
    one per symbol, not a single hardcoded unit for every commodity."""
    one_pound_in_grams=Commodity.GRAMS_PER_POUND
    commodity=build_commodity(make_source_dir, {
        'buy.csv': f"date,symbol,amount_of_units,unit,money_invested,currency\n"
                   f"2024-01-15,copper,{one_pound_in_grams},gram,500.0,usd\n",
    })
    # 1 pound of copper converts to exactly 1.0 unit in copper's own quoted unit, so
    # total_current_value (Units * today's Close, independent of money_invested) is exactly
    # today's own fake close (not DAY_0_CLOSE - total_current_value marks to market as of today,
    # not as of the buy date).
    expected=fake_close('2024-01-15', date.today().isoformat())
    assert float(commodity.total_current_value)==pytest.approx(expected)


def test_custom_symbol_via_tickers_json_is_usable(make_source_dir, make_tickers_json):
    # A new commodity beyond the built-in TICKERS - tickers_json entries need both the yfinance
    # ticker and the physical unit that ticker quotes a price per (quote_unit), since neither
    # can be inferred for an arbitrary new symbol (README Roadmap item).
    tickers_path=make_tickers_json(
        {"tin": {"ticker": "TIN=F", "quote_unit": "pound"}}, filename='commodity_tickers.json',
    )
    commodity=build_commodity(
        make_source_dir,
        {'buy.csv': f"date,symbol,amount_of_units,unit,money_invested,currency\n"
                    f"2024-01-15,tin,{Commodity.GRAMS_PER_POUND},gram,500.0,usd\n"},
        tickers_json=tickers_path,
    )
    assert commodity.tickers['tin']=='TIN=F'
    # 1 pound of tin (recorded in grams) converts to exactly 1.0 unit in tin's own custom
    # quote_unit ('pound'), same conversion logic UNIT_TO_GRAMS/QUOTE_UNIT_GRAMS use for the
    # built-in symbols - so total_current_value (Units * today's Close) is exactly today's own
    # fake close (not DAY_0_CLOSE - total_current_value marks to market as of today).
    expected=fake_close('2024-01-15', date.today().isoformat())
    assert float(commodity.total_current_value)==pytest.approx(expected)


def test_tickers_json_entry_overrides_a_built_in_symbol(make_source_dir, make_tickers_json, mock_yfinance):
    tickers_path=make_tickers_json({"gold": {"ticker": "CUSTOM-GOLD=F", "quote_unit": "troy_ounce"}})
    build_commodity(
        make_source_dir,
        {'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                    "2024-01-15,gold,2,troy_ounce,500.0,usd\n"},
        tickers_json=tickers_path,
    )
    assert 'CUSTOM-GOLD=F' in mock_yfinance.call_log
    assert 'GC=F' not in mock_yfinance.call_log


def test_unknown_unit_raises_value_error(make_source_dir):
    with pytest.raises(ValueError, match="[Uu]nit"):
        build_commodity(make_source_dir, {
            'buy.csv': "date,symbol,amount_of_units,unit,money_invested,currency\n"
                       "2024-01-15,gold,2,kilogram,500.0,usd\n",
        })
