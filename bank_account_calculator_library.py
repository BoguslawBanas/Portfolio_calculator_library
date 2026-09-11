"""
Class-based bank account calculator, following the same pattern as
bonds_calculator_library.PolishRetailBonds: no yfinance fetch and no Currency conversion (a
bank account balance isn't traded/quoted, and — same known limitation as PolishRetailBonds,
see the README Roadmap — everything here is assumed to already be in one currency). What makes
this different from every other calculator is that daily interest compounds onto a balance
that only grows in discrete jumps (deposits/withdrawals, and periodic interest
capitalization) rather than continuously — so unlike Stock/Commodity/Crypto's per-transaction
walk (which is sequential only because of the running average cost basis) or Bonds' per-bond
closed-form formula, interest here has to be accrued one calendar day at a time: each day's
interest depends on the running (possibly still-uncapitalized) balance left by every prior day.
"""

import os
from datetime import datetime
from typing import Callable
import numpy as np
import pandas as pd
from .cache_library import DiskCache


class BankAccount:
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). No
    # Dividend/Realized_profit columns — same as PolishRetailBonds, interest accrues into
    # Profit directly rather than being split into a separate realized/dividend-like stream. ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'

    # --- Ingestion tag: synthesized while loading (from the CSV filename), not read from
    # inside a CSV cell — but consumed everywhere exactly like an input column. ---
    SOURCE_TYPE_COLUMN='state'

    # --- Input: columns read from deposit.csv / withdrawal.csv. ---
    CSV_ACCOUNT_COLUMN='account'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_COLUMN='amount'
    CSV_RATE_TYPE_COLUMN='rate_type'
    CSV_RATE_COLUMN='rate'
    CSV_CAPITALIZATION_MONTHS_COLUMN='capitalization_months'
    CSV_TAX_COLUMN='tax'
    CSV_INTEREST_RATE_COLUMN='rate'

    DEFAULT_TAX=19.0

    def __init__(self, directory_path: str, interest_rate_file: str='interest_rate.csv', progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False):
        """directory_path: a directory holding deposit.csv (required) and withdrawal.csv
        (optional), one row per transaction. Multiple accounts can share a directory,
        distinguished by CSV_ACCOUNT_COLUMN. Every deposit.csv row also carries that account's
        rate_type ('fixed'/'variable'), rate (the fixed annual %, or the spread added to
        interest_rate_file's base rate when variable), capitalization_months (how often accrued
        interest is folded into the interest-bearing balance), and optional tax (%, defaults to
        DEFAULT_TAX) — read once per account from its first deposit row, the same convention
        Stock uses for a ticker's per-row penalty column.
        interest_rate_file: CSV of a variable base rate over time (date,rate — %m-%Y monthly
        rows, forward-filled to daily, same format as PolishRetailBonds' interest_rate.csv),
        resolved relative to directory_path. Only ever read if at least one account in this
        directory uses rate_type='variable'.
        progress_callback: optional zero-arg callback invoked once per account, right after
        that account's interest has been computed — the unit of work a caller (e.g. Portfolio)
        would want to track progress by.
        cache_dir: optional directory to cache each account's computed DataFrame in, keyed by
        account/transactions and valid for the day it was written — see cache_library.DiskCache.
        force_refresh: when True (and cache_dir is set), ignores any cached entry and
        recomputes everything, then overwrites the cache with the fresh result."""
        self.dataframe=self._load_sources(directory_path)
        self._interest_rate_path=os.path.join(directory_path, interest_rate_file)
        self._interest_rate_data=None  # lazily loaded — only accounts with rate_type='variable' need it

        self.total_money_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()

        dataframes=self._split_by_account(self.dataframe)

        dataframes_2=list()
        money_invested_by_account=dict()
        current_value_by_account=dict()
        revenue_by_account=dict()
        for df in dataframes:
            account=df[self.CSV_ACCOUNT_COLUMN].iloc[0]
            computed=self._compute_data(df, cache_dir, force_refresh)
            dataframes_2.append(computed)

            money_invested_by_account[account]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]
            self.total_money_invested+=money_invested_by_account[account]

            # Current value: balance still held plus its unrealized (accrued, uncapitalized-or-not) interest.
            current_value_by_account[account]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_account[account]

            revenue_by_account[account]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_account[account]

            if progress_callback is not None:
                progress_callback()

        for account, value in money_invested_by_account.items():
            self.distribution_by_ticker[account]=(value/self.total_money_invested)*100.0 if self.total_money_invested else 0.0
        for account, value in current_value_by_account.items():
            self.distribution_by_ticker_current_value[account]=(value/self.total_current_value)*100.0 if self.total_current_value else 0.0
        for account, value in revenue_by_account.items():
            self.distribution_by_ticker_revenue[account]=(value/self.total_revenue)*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-account DataFrames by date into a single aggregate DataFrame.
        Equivalent of Stock.merge/Commodity.merge."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

    @classmethod
    def count_accounts(cls, directory_path: str) -> int:
        """Number of distinct accounts in a source directory's deposit.csv, without computing
        any interest — lets a caller (e.g. Portfolio) size a progress bar before construction."""
        return cls._load_sources(directory_path)[cls.CSV_ACCOUNT_COLUMN].nunique()

    @classmethod
    def _load_sources(cls, directory: str) -> pd.DataFrame:
        # Read deposit.csv/withdrawal.csv by their fixed names rather than scanning every CSV
        # in the directory (like Stock/Commodity/Crypto do) — this directory can also hold
        # interest_rate_file, which isn't a transaction CSV, same reason PolishRetailBonds reads
        # buy.csv by name instead of scanning.
        deposit_path=os.path.join(directory, 'deposit.csv')
        if not os.path.exists(deposit_path):
            raise ValueError(f"No deposit.csv found in {directory}")

        deposit_df=pd.read_csv(deposit_path)
        if deposit_df.empty:
            raise ValueError(f"deposit.csv in {directory} has no rows")
        deposit_df[cls.SOURCE_TYPE_COLUMN]='deposit'
        dataframes=[deposit_df]

        withdrawal_path=os.path.join(directory, 'withdrawal.csv')
        if os.path.exists(withdrawal_path):
            withdrawal_df=pd.read_csv(withdrawal_path)
            if not withdrawal_df.empty:
                withdrawal_df[cls.SOURCE_TYPE_COLUMN]='withdrawal'
                dataframes.append(withdrawal_df)

        return pd.concat(dataframes)

    @classmethod
    def _split_by_account(cls, dataframe: pd.DataFrame) -> list:
        dataframes=dict()
        for _, row in dataframe.iterrows():
            if dataframes.get(row[cls.CSV_ACCOUNT_COLUMN]) is None:
                dataframes[row[cls.CSV_ACCOUNT_COLUMN]]=pd.DataFrame()
            dataframes[row[cls.CSV_ACCOUNT_COLUMN]]=pd.concat([dataframes[row[cls.CSV_ACCOUNT_COLUMN]], row], axis=1)

        list_of_dataframes=list(dataframes.values())
        for i in range(len(list_of_dataframes)):
            list_of_dataframes[i]=list_of_dataframes[i].transpose()
            list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i][cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
            list_of_dataframes[i].drop(columns=[cls.CSV_DATE_COLUMN], inplace=True)

        return list_of_dataframes

    def _load_interest_rate_data(self, end_date: datetime) -> pd.DataFrame:
        if self._interest_rate_data is None:
            rate_df=pd.read_csv(self._interest_rate_path)
            rate_df[self.CSV_DATE_COLUMN]=pd.to_datetime(rate_df[self.CSV_DATE_COLUMN], format='%m-%Y')
            rate_df=rate_df.set_index(self.CSV_DATE_COLUMN)
            all_days=pd.DataFrame({}, index=pd.date_range(start=rate_df.index.min(), end=end_date, freq='D'))
            self._interest_rate_data=all_days.join(rate_df).ffill()
        return self._interest_rate_data

    def _compute_data(self, dataframe: pd.DataFrame, cache_dir: str=None, force_refresh: bool=False) -> pd.DataFrame:
        account=dataframe[self.CSV_ACCOUNT_COLUMN].iloc[0]
        start_date=dataframe.index.min()
        end_date=datetime.today()

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            cache_key=DiskCache.make_key('bank_account', account, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if cached is not None:
                    return cached

        deposit_rows=dataframe[dataframe[self.SOURCE_TYPE_COLUMN]=='deposit']
        rate_type=deposit_rows[self.CSV_RATE_TYPE_COLUMN].iloc[0]
        account_rate=float(deposit_rows[self.CSV_RATE_COLUMN].iloc[0])
        capitalization_months=int(deposit_rows[self.CSV_CAPITALIZATION_MONTHS_COLUMN].iloc[0])
        tax=float(deposit_rows[self.CSV_TAX_COLUMN].iloc[0]) if self.CSV_TAX_COLUMN in deposit_rows.columns else self.DEFAULT_TAX

        interest_rate_data=self._load_interest_rate_data(end_date) if rate_type=='variable' else None

        data=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: 0.0,
            self.PROFIT_WITHOUT_DIVIDEND_COLUMN: 0.0,
            self.PROFIT_COLUMN: 0.0,
        }, index=pd.date_range(start=start_date, end=end_date, freq='D'))

        sorted_df=dataframe.sort_index(kind='stable')
        for idx, row in sorted_df.iterrows():
            if row[self.SOURCE_TYPE_COLUMN]=='deposit':
                data.loc[idx, self.MONEY_INVESTED_COLUMN]+=round(row[self.CSV_AMOUNT_COLUMN], 2)
            elif row[self.SOURCE_TYPE_COLUMN]=='withdrawal':
                data.loc[idx, self.MONEY_INVESTED_COLUMN]-=round(row[self.CSV_AMOUNT_COLUMN], 2)

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        money_invested=data[self.MONEY_INVESTED_COLUMN].to_numpy()

        # Interest must be accrued one calendar day at a time (not vectorized) — capitalization
        # (folding accrued-but-uncapitalized interest into the interest-bearing balance every
        # capitalization_months) is a path-dependent step function: which days trigger it, and
        # what balance the following days' interest is computed against, both depend on every
        # prior day's outcome. See this module's docstring for how that differs from why
        # Stock/Commodity/Crypto's transaction loop and Bonds' per-bond loop are sequential.
        profit=np.empty(len(data))
        capitalized_interest=0.0
        uncapitalized_interest=0.0
        cumulative_profit=0.0
        last_capitalization_date=start_date

        for i, idx in enumerate(data.index):
            interest_bearing_balance=money_invested[i]+capitalized_interest

            annual_rate=account_rate if rate_type=='fixed' else interest_rate_data.loc[idx, self.CSV_INTEREST_RATE_COLUMN]+account_rate
            daily_interest=interest_bearing_balance*(annual_rate/100.0)/365.0*(1-tax/100.0)
            uncapitalized_interest+=daily_interest
            cumulative_profit+=daily_interest
            profit[i]=round(cumulative_profit, 2)

            months_elapsed=(idx.year-last_capitalization_date.year)*12+(idx.month-last_capitalization_date.month)
            if idx!=start_date and months_elapsed>=capitalization_months:
                capitalized_interest+=uncapitalized_interest
                uncapitalized_interest=0.0
                last_capitalization_date=idx

        data[self.PROFIT_COLUMN]=profit
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=data[self.PROFIT_COLUMN]

        if cache is not None:
            cache.set(cache_key, data)

        return data
