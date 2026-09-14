"""
Shared behavior factored out of Stock/PolishRetailBonds/Commodity/Crypto/BankAccount/Portfolio
once it became byte-for-byte identical across most of them (README Roadmap item) - each mixin
below is a single, narrow piece of that overlap rather than one combined base class, so a class
only inherits what actually applies to it (e.g. PolishRetailBonds splits by bond type, not by
ticker, so it skips TickerSplitMixin and keeps its own count_tickers override).
"""

import pandas as pd


class ReprMixin:
    """__repr__ shared by every calculator class: a quick invested/current-value/revenue summary
    so printing one in a REPL/notebook shows something useful instead of the default
    <...object at 0x...>. Relies on self.total_money_invested/total_current_value/total_revenue,
    which every calculator class already sets during __init__."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(invested={self.total_money_invested:.2f}, current_value={self.total_current_value:.2f}, revenue={self.total_revenue:.2f})"


class MergeMixin:
    """merge() shared by every per-transaction calculator class (not Portfolio, whose merge()
    combines source objects rather than DataFrames directly)."""

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into a single aggregate DataFrame."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()


class TickerSplitMixin:
    """_split_by_ticker()/count_tickers() shared by the calculator classes that split their raw
    multi-ticker dataframe by a single CSV_TICKER_COLUMN (Stock/Commodity/Crypto/BankAccount) -
    not PolishRetailBonds, which splits by bond type instead and keeps its own count_tickers."""

    @classmethod
    def _split_by_ticker(cls, dataframe: pd.DataFrame) -> list:
        """Splits the raw multi-ticker dataframe into one per-ticker DataFrame each, indexed by
        parsed transaction date - a single groupby pass rather than iterrows()+pd.concat once
        per row, which is O(n^2) in transaction count (each concat copies the whole growing
        per-ticker frame so far) and, via the row-Series/transpose round trip, tends to coerce
        every column to dtype=object instead of keeping each column's own read_csv dtype.
        set_index/drop both return new frames rather than mutating dataframe in place, so the
        caller's own copy (self.dataframe) is left untouched."""
        dates=pd.to_datetime(dataframe[cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
        indexed=dataframe.set_index(dates).drop(columns=[cls.CSV_DATE_COLUMN])
        # sort=False preserves each ticker's first-appearance order, matching the old dict's
        # insertion order - callers don't rely on this, but it keeps behavior identical anyway.
        return [group for _, group in indexed.groupby(cls.CSV_TICKER_COLUMN, sort=False)]

    @classmethod
    def count_tickers(cls, directory_path: str) -> int:
        """Number of distinct tickers in a source directory, without fetching any price data —
        lets a caller (e.g. Portfolio) size a progress bar before construction."""
        return cls._load_sources(directory_path)[cls.CSV_TICKER_COLUMN].nunique()
