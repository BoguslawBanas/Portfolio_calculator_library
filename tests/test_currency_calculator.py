"""Tests for currency_calculator_library.Currency against synthetic, fixed data (no network)."""

from datetime import datetime

import pytest

from Portfolio_calculator_library import Currency


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


def test_cache_hit_avoids_second_fetch(cache_dir, mock_yfinance):
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    # Second construction should have hit the cache, not called yfinance again.
    assert mock_yfinance.call_log.count('EURUSD=X')==1


def test_force_refresh_bypasses_cache(cache_dir, mock_yfinance):
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir)
    Currency('eur', 'usd', datetime(2024, 1, 1), datetime(2024, 1, 5), cache_dir=cache_dir, force_refresh=True)
    assert mock_yfinance.call_log.count('EURUSD=X')==2
