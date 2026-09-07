"""
Class-based version of bonds_calculator_library.py, following the same pattern as
portfolio_calculator_library.py's Portfolio class: the free functions
(create_dataframe_and_get_data, get_data_from_dataframe, fixed_rate_bond,
variable_rate_bond, inflationary_rate_bond) become a Bonds class whose constructor
plays the role of create_dataframe_and_get_data — it loads the two rate CSVs,
computes one DataFrame per bond row, and merges them into self.data right away.
"""

import os
from typing import Callable
import numpy as np
import pandas as pd
from datetime import datetime


class Bonds:
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    RATE_COLUMN='Rate'
    DAILY_INTEREST_COLUMN='Daily_interest'
    DAYS_FROM_BEGINNING_COLUMN='Days_from_beginning'
    YEARS_FROM_BEGINNING_COLUMN='Years_from_beginning'

    # --- Input: columns read from buy.csv / interest_rate.csv / inflation_rate.csv. ---
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_ADDITIONAL_COUPON_COLUMN='additional_coupon'
    CSV_INITIAL_COUPON_COLUMN='initial_coupon'
    CSV_IS_SWAPPED_COLUMN='is_swapped'
    CSV_INTEREST_RATE_COLUMN='rate'
    CSV_INFLATION_COLUMN='inflation'

    def __init__(self, dataframe: str, interest_rate_file: str='interest_rate.csv', inflation_rate_file: str='inflation_rate.csv', progress_callback: Callable[[], None]=None):
        """dataframe: raw bonds transactions dataframe, one row per bond holding, with columns
        date, code, amount_of_units, additional_coupon, initial_coupon, is_swapped. The first
        letter of code selects the bond type: R (1-year) / D (2-year) -> variable-rate, T
        (3-year) -> fixed-rate, E (10-year) -> inflation-indexed. interest_rate_file/
        inflation_rate_file: CSVs expected in the working directory, used respectively by
        variable-rate and inflation-indexed bonds. progress_callback: optional zero-arg callback
        invoked once, after all bond rows have been computed, for a caller (e.g. Portfolio)
        tracking overall progress."""
        today=datetime.today()
        self.dataframe=pd.read_csv(os.path.join(dataframe, "buy.csv"))
        self.dataframe.index=pd.to_datetime(self.dataframe[self.CSV_DATE_COLUMN], format='%Y-%m-%d')
        self.dataframe.drop([self.CSV_DATE_COLUMN], axis=1, inplace=True)
        self.interest_rate_data=self._load_rate_file(interest_rate_file, '%m-%Y', today)
        self.inflation_rate_data=self._load_rate_file(inflation_rate_file, '%m-%Y', today)
        self.data=self._compute_data(today, progress_callback)
        self.total_money_invested=self.data[self.MONEY_INVESTED_COLUMN].iloc[-1]
        self.total_current_value=self.total_money_invested+self.data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
        self.distribution_by_ticker={'Polish bonds': 100.0}
        self.distribution_by_ticker_current_value={'Polish bonds': 100.0}

    @staticmethod
    def count_bonds(directory_path: str) -> int:
        """Number of bond rows in a source directory's buy.csv — lets a caller (e.g. Portfolio)
        size a progress bar before construction."""
        return len(pd.read_csv(os.path.join(directory_path, "buy.csv")))

    @classmethod
    def _load_rate_file(cls, path: str, date_format: str, today: datetime) -> pd.DataFrame:
        rate_df=pd.read_csv(path)
        rate_df[cls.CSV_DATE_COLUMN]=pd.to_datetime(rate_df[cls.CSV_DATE_COLUMN], format=date_format)
        rate_df=rate_df.set_index(cls.CSV_DATE_COLUMN)
        all_days=pd.DataFrame({}, index=pd.date_range(start=rate_df.index.min(), end=today, freq='D'))
        return all_days.join(rate_df).ffill()

    def _compute_data(self, today: datetime, progress_callback: Callable[[], None]=None) -> pd.DataFrame:
        bonds=list()
        for row in self.dataframe.itertuples():
            idx=row.Index
            code=getattr(row, self.CSV_TICKER_COLUMN)[0]
            amount_of_units=getattr(row, self.CSV_AMOUNT_OF_UNITS_COLUMN)
            is_swapped=getattr(row, self.CSV_IS_SWAPPED_COLUMN)
            if code=='R':
                bonds.append(self._variable_rate_bond(amount_of_units, 100.0, getattr(row, self.CSV_ADDITIONAL_COUPON_COLUMN), idx, idx+pd.DateOffset(years=1)-pd.DateOffset(days=1), 19.0, today, is_swapped))
            elif code=='D':
                bonds.append(self._variable_rate_bond(amount_of_units, 100.0, getattr(row, self.CSV_ADDITIONAL_COUPON_COLUMN), idx, idx+pd.DateOffset(years=2)-pd.DateOffset(days=1), 19.0, today, is_swapped))
            elif code=='T':
                bonds.append(self._fixed_rate_bond(amount_of_units, 100.0, getattr(row, self.CSV_INITIAL_COUPON_COLUMN), idx, idx+pd.DateOffset(years=3)-pd.DateOffset(days=1), 0.0, today, is_swapped))
            elif code=='E':
                bonds.append(self._inflationary_rate_bond(amount_of_units, 100.0, getattr(row, self.CSV_INITIAL_COUPON_COLUMN), getattr(row, self.CSV_ADDITIONAL_COUPON_COLUMN), idx, idx+pd.DateOffset(years=10)-pd.DateOffset(days=1), 0.0, today, is_swapped))

        if progress_callback is not None:
            progress_callback()

        return self._merge(bonds)

    @staticmethod
    def _merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-bond DataFrames by date into a single aggregate DataFrame.
        Equivalent of the old portfolio_calculator_library.merge_dataframes(dataframes)."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

    def _fixed_rate_bond(self, amount_of_bonds: int, price_of_unit: float, coupon: float, start_date: datetime, end_date: datetime, tax: float, today: datetime, is_swapped: bool=False) -> pd.DataFrame:
        dataframe=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: amount_of_bonds*price_of_unit,
            self.PROFIT_WITHOUT_DIVIDEND_COLUMN: 0.0,
            self.PROFIT_COLUMN: 0.0,
        }, index=pd.date_range(start=start_date, end=min(end_date, today)))

        dataframe[self.DAYS_FROM_BEGINNING_COLUMN]=(dataframe.index-start_date).days
        dataframe[self.YEARS_FROM_BEGINNING_COLUMN]=np.floor(dataframe[self.DAYS_FROM_BEGINNING_COLUMN]/365)
        dataframe[self.PROFIT_COLUMN]=(amount_of_bonds*price_of_unit*(1+coupon/100)**(1+dataframe[self.YEARS_FROM_BEGINNING_COLUMN])-amount_of_bonds*100*(1+coupon/100)**(dataframe[self.YEARS_FROM_BEGINNING_COLUMN]))/365.0*(1-tax/100)
        dataframe[self.PROFIT_COLUMN]=round(dataframe[self.PROFIT_COLUMN].cumsum(), 2)
        dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=dataframe[self.PROFIT_COLUMN]

        dataframe.drop(columns=[self.DAYS_FROM_BEGINNING_COLUMN, self.YEARS_FROM_BEGINNING_COLUMN], inplace=True)
        return dataframe

    def _variable_rate_bond(self, amount_of_bonds: int, price_of_unit: float, additional_coupon: float, start_date: datetime, end_date: datetime, tax: float, today: datetime, is_swapped: bool=False) -> pd.DataFrame:
        dataframe=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: amount_of_bonds*price_of_unit,
            self.PROFIT_WITHOUT_DIVIDEND_COLUMN: 0.0,
            self.PROFIT_COLUMN: 0.0
        }, index=pd.date_range(start=start_date, end=min(end_date, today)))

        dataframe=dataframe.join(self.interest_rate_data)

        dataframe[self.PROFIT_COLUMN]=(amount_of_bonds*price_of_unit*(1+(dataframe[self.CSV_INTEREST_RATE_COLUMN]+additional_coupon)/100)-(amount_of_bonds*price_of_unit))/365.0*(1-tax/100)
        dataframe[self.PROFIT_COLUMN]=round(dataframe[self.PROFIT_COLUMN].cumsum(), 2)
        dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=dataframe[self.PROFIT_COLUMN]

        dataframe.drop(columns=[self.CSV_INTEREST_RATE_COLUMN], inplace=True)

        if is_swapped:
            dataframe.loc[dataframe.index.max(), self.PROFIT_COLUMN]+=amount_of_bonds*0.1
        return dataframe

    def _inflationary_rate_bond(self, amount_of_bonds: int, price_of_unit: float, initial_coupon: float, additional_coupon: float, start_date: datetime, end_date: datetime, tax: float, today: datetime, is_swapped: bool=False) -> pd.DataFrame:
        dataframe=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: amount_of_bonds*price_of_unit,
            self.PROFIT_WITHOUT_DIVIDEND_COLUMN: 0.0,
            self.PROFIT_COLUMN: 0.0,
            self.DAILY_INTEREST_COLUMN: 0.0
        }, index=pd.date_range(start=start_date, end=min(end_date, today)))

        dataframe.loc[start_date:start_date+pd.DateOffset(years=1), self.RATE_COLUMN]=initial_coupon
        dataframe.loc[start_date:start_date+pd.DateOffset(years=1), self.DAILY_INTEREST_COLUMN]=(amount_of_bonds*price_of_unit*initial_coupon/100)/365.0
        dataframe.loc[start_date:start_date+pd.DateOffset(years=1), self.PROFIT_COLUMN]=dataframe[self.DAILY_INTEREST_COLUMN].cumsum()

        for i in range(9):
            tmp_end_date=min(start_date+pd.DateOffset(years=i+2), end_date)
            if tmp_end_date<end_date:
                break
            dataframe.loc[start_date+pd.DateOffset(years=i+1, days=1):tmp_end_date, self.RATE_COLUMN]=self.inflation_rate_data.loc[pd.to_datetime(start_date+pd.DateOffset(years=i+1)-pd.DateOffset(months=1)), self.CSV_INFLATION_COLUMN]+additional_coupon
            dataframe.loc[start_date+pd.DateOffset(years=i+1):tmp_end_date, self.DAILY_INTEREST_COLUMN]=((amount_of_bonds*price_of_unit+dataframe.loc[start_date+pd.DateOffset(years=i+1), 'Close'])*dataframe[self.RATE_COLUMN]/100)/365.0
            dataframe.loc[start_date+pd.DateOffset(years=i+1):tmp_end_date, self.PROFIT_COLUMN]=dataframe[self.DAILY_INTEREST_COLUMN].cumsum()

        dataframe.drop(columns=[self.RATE_COLUMN, self.DAILY_INTEREST_COLUMN], inplace=True)
        dataframe[self.PROFIT_COLUMN]=round(dataframe[self.PROFIT_COLUMN]*(1-tax/100), 2)
        dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=dataframe[self.PROFIT_COLUMN]

        if is_swapped:
            dataframe.loc[dataframe.index.max(), self.PROFIT_COLUMN]+=amount_of_bonds*0.1
        return dataframe
