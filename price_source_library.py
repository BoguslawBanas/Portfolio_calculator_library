"""
Single seam every price-history fetch goes through - stock/commodity/crypto/currency/benchmark
each called `yf.Ticker(...).history(...)` directly and independently before this. Centralizing it
here means retrying a transient failure, and eventually swapping providers, are each one change
instead of five. See ROADMAP.md's Extensibility section.
"""

import time
import yfinance as yf


class PriceSource:
    DEFAULT_MAX_RETRIES=3
    DEFAULT_BACKOFF_SECONDS=1.0

    @classmethod
    def fetch_history(cls, ticker: str, max_retries: int=DEFAULT_MAX_RETRIES, backoff_seconds: float=DEFAULT_BACKOFF_SECONDS, **history_kwargs):
        """yf.Ticker(ticker).history(**history_kwargs), retried with exponential backoff
        (backoff_seconds, then 2x, 4x, ...) on any exception yfinance or its HTTP layer raises -
        a transient network hiccup or rate-limit response no longer aborts the whole Portfolio
        construction outright. Retried up to max_retries times before the exception is
        re-raised.
        An invalid/delisted ticker isn't an exception - yfinance returns an empty DataFrame for
        that, a normal (not erroneous) result - so it isn't retried here; each caller already
        raises its own specific ValueError for that case."""
        attempt=0
        while True:
            try:
                return yf.Ticker(ticker).history(**history_kwargs)
            except Exception:
                attempt+=1
                if attempt>max_retries:
                    raise
                time.sleep(backoff_seconds*(2**(attempt-1)))
