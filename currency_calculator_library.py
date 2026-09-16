"""
FX rate fetcher: fetches daily exchange-rate history for a currency pair via yfinance at
construction, caching the result on self.data. Used internally by Stock/Commodity/Crypto/
PolishRetailBonds to convert native-currency values to a portfolio's target currency, and usable
standalone.
"""

from datetime import datetime
import pandas as pd
import yfinance as yf
from .cache_library import DiskCache


class Currency:
    CLOSE_COLUMN='Close'

    def __init__(self, currency_from: str, currency_to: str, start_date: datetime, end_date: datetime=None, cache_dir: str=None, force_refresh: bool=False):
        """currency_from/currency_to: e.g. 'usd', 'pln' - identical (case-insensitive) codes
        short-circuit to a flat 1.0 rate, no API call. end_date defaults to today, computed here
        (not as a literal default) so it reflects construction time.
        cache_dir: caches the fetched DataFrame, keyed by pair/start/end, valid for the day it
        was written - see cache_library.DiskCache. Skipped for a same-currency pair.
        force_refresh: skips the cache read but still writes the fresh result back."""
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

        symbol=self.currency_from.upper()+self.currency_to.upper()+"=X"
        ticker=yf.Ticker(symbol)
        data=ticker.history(start=self.start_date, end=self.end_date, repair=True, actions=False)
        # yfinance returns an empty DataFrame, not an error, for an unquoted pair - unchecked,
        # all_days.join(data) below would silently produce an all-NaN column instead.
        if data.empty:
            raise ValueError(f"yfinance returned no FX history for {symbol!r} (requested {self.start_date.date()} to {self.end_date.date()}) - check {self.currency_from!r}/{self.currency_to!r} is a currency pair yfinance actually quotes.")
        data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        data.index=pd.to_datetime(data.index).tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=self.start_date, end=self.end_date, freq='D').tz_localize(None).normalize()
        )
        result=all_days.join(data).ffill().bfill()

        if self._cache is not None:
            self._cache.set(cache_key, result)

        return result


def get_cached_currency(currency_cache: dict, currency_from: str, currency_to: str, start_date, cache_dir: str=None, force_refresh: bool=False) -> Currency:
    """Looks up/fetches a Currency for (currency_from, currency_to) through a shared
    currency_cache dict (keyed by upper-cased pair) instead of each caller constructing its own -
    Stock/Commodity/Crypto/PolishRetailBonds all call this instead of Currency(...) directly, so
    a currency pair shared across tickers/sources is fetched once per Portfolio construction.

    currency_cache=None skips this and always constructs fresh, identical to calling Currency(...)
    directly.

    A cached entry is reused as-is when its own start_date already covers what's needed (dates
    are always looked up by label, so a wider range is harmless). Otherwise it's refetched over
    the union of both ranges and the cache entry is replaced, so a later, earlier-starting call
    only re-fetches once more."""
    if currency_cache is None:
        return Currency(currency_from, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

    key=(currency_from.upper(), currency_to.upper())
    cached=currency_cache.get(key)
    if cached is not None and not force_refresh and cached.start_date<=start_date:
        return cached

    fetch_start=start_date if cached is None else min(cached.start_date, start_date)
    currency=Currency(currency_from, currency_to, fetch_start, cache_dir=cache_dir, force_refresh=force_refresh)
    currency_cache[key]=currency
    return currency
