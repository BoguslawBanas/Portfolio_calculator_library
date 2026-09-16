"""
Behavior shared across Stock/PolishRetailBonds/Commodity/Crypto/BankAccount/Portfolio, split
into narrow mixins rather than one base class so each calculator only inherits what applies to
it (e.g. PolishRetailBonds splits by bond type, not ticker, so it skips TickerSplitMixin).
"""

import pandas as pd


class ReprMixin:
    """__repr__: invested/current_value/revenue summary, from attrs every calculator sets."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(invested={self.total_money_invested:.2f}, current_value={self.total_current_value:.2f}, revenue={self.total_revenue:.2f})"


class MergeMixin:
    """merge(): shared by per-transaction calculators (not Portfolio, which merges source
    objects, not DataFrames, directly)."""

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into one aggregate DataFrame."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()


class TickerSplitMixin:
    """_split_by_ticker()/count_tickers(): shared by calculators that split by a single
    CSV_TICKER_COLUMN (Stock/Commodity/Crypto/BankAccount) - not PolishRetailBonds."""

    @classmethod
    def _split_by_ticker(cls, dataframe: pd.DataFrame) -> list:
        """One groupby pass, not iterrows()+concat per row (O(n^2), and coerces every column to
        dtype=object). set_index/drop return new frames, leaving self.dataframe untouched."""
        dates=pd.to_datetime(dataframe[cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
        indexed=dataframe.set_index(dates).drop(columns=[cls.CSV_DATE_COLUMN])
        return [group for _, group in indexed.groupby(cls.CSV_TICKER_COLUMN, sort=False)]

    @classmethod
    def count_tickers(cls, directory_path: str) -> int:
        """Distinct ticker count, no price fetch - lets a caller size a progress bar upfront."""
        return cls._load_sources(directory_path)[cls.CSV_TICKER_COLUMN].nunique()
