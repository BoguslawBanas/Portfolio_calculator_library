"""
Class-based alternative to currency_calculator_library.py — a sketch, not wired into the
rest of the codebase, built the same way test.py/test2.py wrap their modules: the module's
one function becomes a method, and the constructor fetches the exchange-rate data right
away, caching the result on self.data.
"""

from datetime import datetime
import pandas as pd
import yfinance as yf
from .cache_library import DiskCache


class Currency:
    CLOSE_COLUMN='Close'

    def __init__(self, currency_from: str, currency_to: str, start_date: datetime, end_date: datetime=None, cache_dir: str=None, force_refresh: bool=False):
        """currency_from/currency_to: the two currency codes (e.g. 'usd', 'pln'); identical
        codes (case-insensitive) short-circuit to a flat 1.0 exchange rate instead of an
        API call. end_date defaults to today — computed here rather than as a literal default
        value, so it reflects the actual construction time instead of import time.
        cache_dir: optional directory to cache the fetched exchange-rate DataFrame in, keyed
        by currency pair and start/end date and valid for the day it was written — see
        cache_library.DiskCache. Skipped for a same-currency pair, which never calls out.
        force_refresh: when True (and cache_dir is set), skips reading the cache — always
        re-fetches — but still writes the fresh result back, refreshing the entry for callers
        that don't pass force_refresh."""
        self.currency_from=currency_from
        self.currency_to=currency_to
        self.start_date=start_date
        self.end_date=end_date if end_date is not None else datetime.today()
        self._cache=DiskCache(cache_dir) if cache_dir else None
        self._force_refresh=force_refresh
        self.data=self._fetch_data()

    def _fetch_data(self) -> pd.DataFrame:
        if self.currency_from.upper()==self.currency_to.upper():
            return pd.DataFrame({
                self.CLOSE_COLUMN: 1.0
            }, index=pd.date_range(start=self.start_date, end=self.end_date, freq='D'))

        cache_key=None
        if self._cache is not None:
            cache_key=DiskCache.make_key('currency', self.currency_from.upper(), self.currency_to.upper(), self.start_date.date(), self.end_date.date())
            if not self._force_refresh:
                cached=self._cache.get(cache_key)
                if cached is not None:
                    return cached

        ticker=yf.Ticker(self.currency_from.upper()+self.currency_to.upper()+"=X")
        data=ticker.history(start=self.start_date, end=self.end_date, repair=True, actions=False)
        data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        data.index=pd.to_datetime(data.index).tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=self.start_date, end=self.end_date, freq='D').tz_localize(None).normalize()
        )
        result=all_days.join(data).ffill().bfill()

        if self._cache is not None:
            self._cache.set(cache_key, result)

        return result
