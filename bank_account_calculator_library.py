"""
Bank account calculator, following the same pattern as PolishRetailBonds: no yfinance fetch, no
Currency conversion (unlike PolishRetailBonds, everything here is assumed to already be in one
currency). Different from every other calculator: daily interest compounds onto a balance that
only grows in discrete jumps (deposits/withdrawals, periodic capitalization), so interest must
be accrued one calendar day at a time - each day depends on the running, possibly-uncapitalized
balance left by every prior day.
"""

import os
from datetime import datetime
from decimal import Decimal
from typing import Callable
import numpy as np
import pandas as pd
from .cache_library import DiskCache
from .calculator_mixins import ReprMixin, MergeMixin, TickerSplitMixin, to_money, money_array


class BankAccount(TickerSplitMixin, MergeMixin, ReprMixin):
    # --- Output: self.data columns. First three: shared contract (see CLAUDE.md). No
    # Dividend/Realized_profit - interest accrues straight into Profit, no separate stream. ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    # Always equals PROFIT_COLUMN - present so Portfolio's merge has something to sum for every
    # source type.
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    PROFIT_EXCLUDING_DIVIDEND_COLUMN='Profit_excluding_dividends'

    # --- Ingestion tag, synthesized from the CSV filename, consumed like an input column. ---
    SOURCE_TYPE_COLUMN='state'

    # --- Input: columns read from deposit.csv / withdrawal.csv. ---
    # CSV_TICKER_COLUMN (value 'account'): matches every other class's own name, so
    # distribution_by_ticker holds account names using the same generic term.
    CSV_TICKER_COLUMN='account'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_COLUMN='amount'
    CSV_RATE_TYPE_COLUMN='rate_type'
    CSV_RATE_COLUMN='rate'
    CSV_CAPITALIZATION_MONTHS_COLUMN='capitalization_months'
    CSV_TAX_COLUMN='tax'
    CSV_INTEREST_RATE_COLUMN='rate'

    DEFAULT_TAX=19.0

    def __init__(self, directory_path: str, interest_rate_file: str='interest_rate.csv', progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False):
        """directory_path: deposit.csv (required) and withdrawal.csv (optional), one row per
        transaction; multiple accounts can share a directory via CSV_TICKER_COLUMN. Each
        deposit.csv row also carries that account's rate_type ('fixed'/'variable'), rate (fixed
        annual %, or the spread over interest_rate_file's base rate when variable),
        capitalization_months, and optional tax (%, defaults to DEFAULT_TAX) - read once per
        account, from its first deposit row.
        interest_rate_file: variable base rate over time (date,rate, daily YYYY-MM-DD rows,
        forward-filled) - finer-grained than PolishRetailBonds' monthly rows. Only read if some
        account uses rate_type='variable'.
        progress_callback: optional zero-arg callback, once per account, after its interest is
        computed.
        cache_dir: caches each account's computed DataFrame, keyed by account/transactions,
        valid for the day written.
        force_refresh: ignores any cached entry, recomputes, overwrites the cache."""
        self.dataframe=self._load_sources(directory_path)
        self._interest_rate_path=os.path.join(directory_path, interest_rate_file)
        self._interest_rate_data=None  # lazily loaded — only accounts with rate_type='variable' need it

        self.total_money_invested=Decimal('0')
        self.total_current_value=Decimal('0')
        self.total_revenue=Decimal('0')
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_currently_invested=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()

        dataframes=self._split_by_ticker(self.dataframe)

        dataframes_2=list()
        money_invested_by_account=dict()
        lifetime_invested_by_account=dict()
        current_value_by_account=dict()
        revenue_by_account=dict()
        for df in dataframes:
            account=df[self.CSV_TICKER_COLUMN].iloc[0]
            computed, lifetime_deposited=self._compute_data(df, cache_dir, force_refresh)
            dataframes_2.append(computed)

            money_invested_by_account[account]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]
            # Lifetime gross ever deposited, never reduced by a withdrawal - matches Stock's own
            # total_money_invested meaning.
            lifetime_invested_by_account[account]=lifetime_deposited
            self.total_money_invested+=lifetime_deposited

            # Balance still held plus its unrealized (accrued, uncapitalized-or-not) interest.
            current_value_by_account[account]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_account[account]

            revenue_by_account[account]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_account[account]

            if progress_callback is not None:
                progress_callback()

        # distribution_by_ticker: lifetime-gross (every deposit, ignoring withdrawals), matching
        # Stock. distribution_by_ticker_currently_invested: current balance net of withdrawals -
        # an account withdrawn to 0 drops to 0% there while keeping its lifetime share above.
        # Distribution percentages are ratios, not money - computed in float even though value/
        # total are Decimal.
        for account, value in lifetime_invested_by_account.items():
            self.distribution_by_ticker[account]=(float(value)/float(self.total_money_invested))*100.0 if self.total_money_invested else 0.0
        self.total_money_currently_invested=sum(money_invested_by_account.values())
        for account, value in money_invested_by_account.items():
            self.distribution_by_ticker_currently_invested[account]=(float(value)/float(self.total_money_currently_invested))*100.0 if self.total_money_currently_invested else 0.0
        for account, value in current_value_by_account.items():
            self.distribution_by_ticker_current_value[account]=(float(value)/float(self.total_current_value))*100.0 if self.total_current_value else 0.0
        for account, value in revenue_by_account.items():
            self.distribution_by_ticker_revenue[account]=(float(value)/float(self.total_revenue))*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    @classmethod
    def _load_sources(cls, directory: str) -> pd.DataFrame:
        # Fixed names, not a directory scan (like Stock/Commodity/Crypto) - this directory can
        # also hold interest_rate_file, not a transaction CSV.
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

    def _load_interest_rate_data(self, end_date: datetime) -> pd.DataFrame:
        if self._interest_rate_data is None:
            rate_df=pd.read_csv(self._interest_rate_path)
            rate_df[self.CSV_DATE_COLUMN]=pd.to_datetime(rate_df[self.CSV_DATE_COLUMN], format='%Y-%m-%d')
            rate_df=rate_df.set_index(self.CSV_DATE_COLUMN)
            all_days=pd.DataFrame({}, index=pd.date_range(start=rate_df.index.min(), end=end_date, freq='D'))
            self._interest_rate_data=all_days.join(rate_df).ffill()
        return self._interest_rate_data

    def _compute_data(self, dataframe: pd.DataFrame, cache_dir: str=None, force_refresh: bool=False) -> tuple:
        """Returns (data, lifetime_deposited): lifetime_deposited sums every deposit row's own
        amount, ignoring withdrawals - the lifetime-gross figure total_money_invested/
        distribution_by_ticker need. Returned alongside data since data[MONEY_INVESTED_COLUMN]
        is the net, withdrawal-reduced balance instead."""
        account=dataframe[self.CSV_TICKER_COLUMN].iloc[0]
        start_date=dataframe.index.min()
        end_date=datetime.today()

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            # Version tag - the isinstance check below guards a cache entry from an older,
            # differently-shaped format.
            cache_key=DiskCache.make_key('bank_account-v2', account, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if isinstance(cached, tuple) and len(cached)==2:
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

        # Plain numpy arrays, scattered by position instead of .iterrows()/.loc[label] per row -
        # same pattern as Stock/Commodity/Crypto, but with no running-average dependency between
        # rows, so np.add.at does the whole accumulation in one call, no Python loop needed.
        # signed_amount/money_invested_by_day are Decimal (object dtype) - each deposit/
        # withdrawal is already meaningful at cent precision, cast via to_money()/money_array()
        # right before landing here, so the cumsum() below adds exact Decimals.
        sorted_df=dataframe.sort_index(kind='stable')
        state=sorted_df[self.SOURCE_TYPE_COLUMN].to_numpy()
        raw_amount=sorted_df[self.CSV_AMOUNT_COLUMN].to_numpy(dtype=float)
        signed_amount=money_array(np.where(state=='deposit', 1.0, -1.0)*np.round(raw_amount, 2))
        # signed_amount is already +amount for deposits/-amount for withdrawals, so summing just
        # the deposit entries gives lifetime_deposited directly.
        lifetime_deposited=signed_amount[state=='deposit'].sum() if (state=='deposit').any() else Decimal('0')

        position=data.index.get_indexer(sorted_df.index)
        if (position<0).any():
            missing=sorted_df.index[position<0]
            raise KeyError(f"Transaction date(s) {list(missing)} for {account} fall outside the computed daily range.")

        money_invested_by_day=np.full(len(data), Decimal('0'), dtype=object)
        np.add.at(money_invested_by_day, position, signed_amount)
        data[self.MONEY_INVESTED_COLUMN]=money_invested_by_day

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        money_invested=data[self.MONEY_INVESTED_COLUMN].to_numpy()

        # Must accrue one calendar day at a time (not vectorized) - capitalization (folding
        # accrued interest into the balance every capitalization_months) is path-dependent: which
        # days trigger it, and what balance later days accrue against, depend on every prior day.
        # Stays float end to end (annual_rate is a rate, not money) - money_invested[i] (Decimal)
        # is cast to float here for this internal computation, and the resulting profit array is
        # cast back to Decimal in one shot below, at the point it becomes the stored Profit column.
        profit=np.empty(len(data))
        capitalized_interest=0.0
        uncapitalized_interest=0.0
        cumulative_profit=0.0
        last_capitalization_date=start_date

        for i, idx in enumerate(data.index):
            interest_bearing_balance=float(money_invested[i])+capitalized_interest

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

        data[self.PROFIT_COLUMN]=money_array(profit)
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=data[self.PROFIT_COLUMN]
        data[self.PROFIT_WITHOUT_REALIZED_COLUMN]=data[self.PROFIT_COLUMN]
        data[self.PROFIT_EXCLUDING_DIVIDEND_COLUMN]=data[self.PROFIT_COLUMN]

        result=(data, lifetime_deposited)
        if cache is not None:
            cache.set(cache_key, result)

        return result
