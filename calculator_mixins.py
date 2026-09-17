"""
Behavior shared across Stock/PolishRetailBonds/Commodity/Crypto/BankAccount/Portfolio, split
into narrow mixins rather than one base class so each calculator only inherits what applies to
it (e.g. PolishRetailBonds splits by bond type, not ticker, so it skips TickerSplitMixin).
"""

import numpy as np
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


class IrrMixin:
    """_irr_newton()/calculate_irr(): shared by Portfolio and Benchmark - both build a self.data
    with Money_invested/Profit columns and want the same money-weighted IRR off it. Requires
    MONEY_INVESTED_COLUMN/PROFIT_COLUMN/IRR_COLUMN/PREV_MONEY_INVESTED_COLUMN/
    TOTAL_MONEY_COLUMN/CASHFLOW_COLUMN as class attributes on whatever inherits this."""

    @staticmethod
    def _irr_newton(cashflows: list, guess: float, tol: float=1e-12, max_iter: int=10):
        cashflows=np.asarray(cashflows, dtype=np.float64)
        r=guess

        for _ in range(max_iter):
            t=np.arange(len(cashflows))
            denom=(1+r)**t
            f=np.sum(cashflows/denom)
            fp=np.sum(-t*cashflows/((1+r)**(t+1)))

            if abs(fp)<1e-15:
                return np.nan
            r_new=r-f/fp

            if abs(r_new-r)<tol:
                return r_new
            r=r_new

        return r

    def calculate_irr(self):
        dataframe=self.data

        dataframe[self.PREV_MONEY_INVESTED_COLUMN]=dataframe[self.MONEY_INVESTED_COLUMN].shift(1).fillna(0.0)
        dataframe[self.TOTAL_MONEY_COLUMN]=round(dataframe[self.MONEY_INVESTED_COLUMN]+dataframe[self.PROFIT_COLUMN], 2)
        dataframe[self.CASHFLOW_COLUMN]=round(dataframe[self.PREV_MONEY_INVESTED_COLUMN]-dataframe[self.MONEY_INVESTED_COLUMN], 2)

        n=len(dataframe[self.CASHFLOW_COLUMN])
        cashflow_values=dataframe[self.CASHFLOW_COLUMN].to_numpy()
        total_money_values=dataframe[self.TOTAL_MONEY_COLUMN].to_numpy()

        irr=np.full(n, np.nan)
        guess=0.1
        irr[0]=0.0

        # Day i's IRR input is every cashflow through day i, plus day i's total money as a
        # terminal value. Rebuilding that (i+2)-element list from scratch each iteration is
        # O(n^2); a preallocated buffer instead gets two O(1) writes per iteration - position i
        # gets this day's cashflow permanently, position i+1 overwrites the prior terminal value.
        buffer=np.empty(n+1, dtype=np.float64)
        for i in range(n):
            buffer[i]=cashflow_values[i]
            buffer[i+1]=total_money_values[i]
            guess=self._irr_newton(buffer[:i+2], guess=guess)
            irr[i]=round(((guess+1.0)**i-1)*100.0, 2)
            if np.isnan(guess):
                guess=0.1

        dataframe[self.IRR_COLUMN]=irr
        dataframe.drop(columns=[self.PREV_MONEY_INVESTED_COLUMN, self.CASHFLOW_COLUMN], inplace=True)
        self.portfolio=dataframe


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
