"""
Class-based alternative to currency_calculator_library.py — a sketch, not wired into the
rest of the codebase, built the same way test.py/test2.py wrap their modules: the module's
one function becomes a method, and the constructor fetches the exchange-rate data right
away, caching the result on self.data.
"""

from datetime import datetime
import pandas as pd
import yfinance as yf


class Currency:
    CLOSE_COLUMN='Close'

    def __init__(self, currency_from: str, currency_to: str, start_date: datetime, end_date: datetime=None):
        """currency_from/currency_to: the two currency codes (e.g. 'usd', 'pln'); identical
        codes (case-insensitive) short-circuit to a flat 1.0 exchange rate instead of an
        API call. end_date defaults to today — computed here rather than as a literal default
        value, so it reflects the actual construction time instead of import time."""
        self.currency_from=currency_from
        self.currency_to=currency_to
        self.start_date=start_date
        self.end_date=end_date if end_date is not None else datetime.today()
        self.data=self._fetch_data()

    def _fetch_data(self) -> pd.DataFrame:
        if self.currency_from.upper()==self.currency_to.upper():
            return pd.DataFrame({
                self.CLOSE_COLUMN: 1.0
            }, index=pd.date_range(start=self.start_date, end=self.end_date, freq='D'))

        ticker=yf.Ticker(self.currency_from.upper()+self.currency_to.upper()+"=X")
        data=ticker.history(start=self.start_date, end=self.end_date, repair=True, actions=False)
        data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        data.index=pd.to_datetime(data.index).tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=self.start_date, end=self.end_date, freq='D').tz_localize(None).normalize()
        )
        return all_days.join(data).ffill().bfill()
