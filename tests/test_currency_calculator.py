"""Tests for currency_calculator_library.Currency against synthetic, fixed data (no network)."""

from datetime import datetime

import pytest

from Portfolio_calculator_library import Currency, get_cached_currency


def test_same_currency_short_circuits_to_flat_rate_no_network_call(mock_yfinance):
    currency=Currency('usd', 'USD', datetime(2024, 1, 1))
    assert (currency.data[Currency.CLOSE_COLUMN]==1.0).all()
    assert mock_yfinance.call_log==[]


def test_foreign_currency_fetches_and_fills_continuous_daily_range(mock_yfinance):
    currency=Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 10))
    assert mock_yfinance.call_log==['EURUSD=X']
    assert not currency.data[Currency.CLOSE_COLUMN].isna().any()
    # One row per calendar day in [start, end].
    assert len(currency.data)==10


def test_unquoted_pair_raises_instead_of_returning_nan(mock_yfinance):
    # FakeTicker returns an empty DataFrame for any symbol starting with 'INVALID', mirroring
    # real yfinance's behavior for a pair it doesn't quote - must raise here, not silently
    # produce a Close column of NaN (README Roadmap item).
    with pytest.raises(ValueError, match="INVALIDUSD=X"):
        Currency('invalid', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 10))


def test_cache_hit_avoids_second_fetch(cache_dir, mock_yfinance):
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    # Second construction should have hit the cache, not called yfinance again.
    assert mock_yfinance.call_log.count('EURUSD=X')==1


def test_force_refresh_bypasses_cache(cache_dir, mock_yfinance):
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir, force_refresh=True)
    assert mock_yfinance.call_log.count('EURUSD=X')==2


def test_get_cached_currency_with_no_cache_dict_fetches_every_call(mock_yfinance):
    # currency_cache=None (the default for every caller) must behave exactly like calling
    # Currency(...) directly - no reuse at all.
    get_cached_currency(None, 'eur', 'usd', datetime(2024, 1, 1))
    get_cached_currency(None, 'eur', 'usd', datetime(2024, 1, 1))
    assert mock_yfinance.call_log.count('EURUSD=X')==2


def test_get_cached_currency_reuses_a_shared_entry_for_the_same_pair(mock_yfinance):
    currency_cache=dict()
    first=get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 1))
    second=get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 3))
    assert mock_yfinance.call_log.count('EURUSD=X')==1
    assert first is second


def test_get_cached_currency_is_keyed_by_pair_case_insensitively(mock_yfinance):
    currency_cache=dict()
    get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 1))
    get_cached_currency(currency_cache, 'EUR', 'USD', datetime(2024, 1, 1))
    assert mock_yfinance.call_log.count('EURUSD=X')==1
    # A different pair is unaffected - its own separate fetch.
    get_cached_currency(currency_cache, 'gbp', 'usd', datetime(2024, 1, 1))
    assert mock_yfinance.call_log.count('GBPUSD=X')==1


def test_get_cached_currency_refetches_with_a_wider_range_for_an_earlier_start_date(mock_yfinance):
    currency_cache=dict()
    get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 10))
    # An earlier start_date than what's cached isn't covered - must re-fetch (once), not just
    # reuse the too-narrow cached entry.
    earlier=get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 1))
    assert mock_yfinance.call_log.count('EURUSD=X')==2
    assert earlier.start_date==datetime(2024, 1, 1)
    assert currency_cache[('EUR', 'USD')] is earlier

    # A third call whose start_date falls inside the now-widened cached range reuses it with no
    # further fetch.
    get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 5))
    assert mock_yfinance.call_log.count('EURUSD=X')==2


def test_get_cached_currency_force_refresh_still_refetches_a_cached_pair(mock_yfinance):
    currency_cache=dict()
    get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 1))
    get_cached_currency(currency_cache, 'eur', 'usd', datetime(2024, 1, 1), force_refresh=True)
    assert mock_yfinance.call_log.count('EURUSD=X')==2
