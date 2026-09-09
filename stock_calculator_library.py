"""
Class-based alternative to stock_calculator_library.py — a sketch, not wired into the rest
of the codebase, built the same way test.py wraps portfolio_calculator_library.py: the
module's free functions become methods on a Stock class, and the constructor does the work
that used to require calling get_data_from_isin by hand — it fetches the FX data and the
price history and computes the final Money_invested/Profit/Dividend DataFrame right away,
caching the result on self.data.

Uses a plain (non-relative) import of currency_calculator_library, same assumption test.py
makes: run as a standalone script from the repo root rather than as part of a package.
"""

import os
import json
from datetime import datetime
from typing import Callable
import numpy as np
import pandas as pd
import yfinance as yf
from .currency_calculator_library import Currency
from .cache_library import DiskCache


class Stock:
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    REALIZED_PROFIT_COLUMN='Realized_profit'
    UNITS_COLUMN='Units'
    CLOSE_COLUMN='Close'

    # --- Ingestion tag: synthesized while loading (from the CSV filename), not read from
    # inside a CSV cell — but consumed everywhere exactly like an input column. ---
    SOURCE_TYPE_COLUMN='state'

    # --- Input: columns read from the per-transaction CSVs (buy.csv, sell.csv, ...). ---
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_PRICE_OF_UNIT_COLUMN='price_of_unit'
    CSV_PENALTY_COLUMN='penalty'
    CSV_SELL_TAX_COLUMN='sell_tax'
    CSV_DIVIDEND_COLUMN='dividend'
    CSV_DIVIDEND_TAX_COLUMN='dividend_tax'

    def __init__(self, directory_path: str, stock_data: str, currency_to: str, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False):
        """progress_callback: optional zero-arg callback invoked once per ticker, right after that
        ticker's price history has been fetched and computed — the unit of work a caller (e.g.
        Portfolio) would want to track progress by, since that fetch is what actually takes time.
        cache_dir: optional directory to cache each ticker's computed DataFrame in, keyed by
        ticker/currency/transactions and valid for the day it was written — see
        cache_library.DiskCache. Also passed down to every Currency this Stock constructs.
        force_refresh: when True (and cache_dir is set), ignores any cached entry and
        recomputes/re-fetches everything, then overwrites the cache with the fresh result.
        include_native_currency: when True, also computes each ticker's DataFrame in its own
        native currency (self.native_data[ticker], self.native_currency[ticker]) alongside the
        currency_to-converted one in self.data — isolates that ticker's own performance from
        FX movement against currency_to. Off by default: for a foreign-currency ticker this is
        a second yfinance fetch/computation (the price history itself doesn't depend on
        currency_to, but _compute_data doesn't know that, so it's fetched again); for a ticker
        already in currency_to it's free (the already-computed DataFrame is reused as-is)."""
        self.total_money_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.dataframe=self._load_sources(directory_path)
        self.tickers=self._load_tickers_json(stock_data)
        self._cache_dir=cache_dir

        dataframes=self._split_by_isin(self.dataframe)

        # Money invested per buy row isn't a column on the source dataframe (see _compute_data,
        # which derives it the same way) — recompute it here instead of assuming one exists.
        money_invested_by_ticker=dict()
        for df in dataframes:
            currency=Currency(self.get_ticker_currency(df, stock_data, self.CSV_TICKER_COLUMN), currency_to, df.index.min(), cache_dir=cache_dir, force_refresh=force_refresh)

            money_invested=0.0
            for idx, row in df.iterrows():
                if row[self.SOURCE_TYPE_COLUMN]=='buy':
                    money_invested+=round((row[self.CSV_PENALTY_COLUMN]+1.0)*row[self.CSV_AMOUNT_OF_UNITS_COLUMN]*row[self.CSV_PRICE_OF_UNIT_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN], 2)

            money_invested_by_ticker[df[self.CSV_TICKER_COLUMN].iloc[0]]=money_invested
            self.total_money_invested+=money_invested

        dataframes_2=list()
        current_value_by_ticker=dict()
        revenue_by_ticker=dict()
        for df in dataframes:
            ticker=df[self.CSV_TICKER_COLUMN].iloc[0]
            self.distribution_by_ticker[ticker]=(money_invested_by_ticker[ticker]/self.total_money_invested)*100.0
            ticker_currency=self.get_ticker_currency(df, stock_data, self.CSV_TICKER_COLUMN)
            computed=self._compute_data(df, ticker_currency, currency_to, cache_dir, force_refresh)
            dataframes_2.append(computed)

            # Current market value of the position: cost basis still held plus its unrealized gain.
            current_value_by_ticker[ticker]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_ticker[ticker]

            # Revenue: this ticker's all-time gain (unrealized + dividends + realized), which
            # can be negative for a losing position.
            revenue_by_ticker[ticker]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_ticker[ticker]

            if include_native_currency:
                # Already the same currency -> computed is already this ticker's native-currency
                # DataFrame, no need to compute it again.
                if ticker_currency.upper()==currency_to.upper():
                    self.native_data[ticker]=computed
                else:
                    self.native_data[ticker]=self._compute_data(df, ticker_currency, ticker_currency, cache_dir, force_refresh)
                self.native_currency[ticker]=ticker_currency

            if progress_callback is not None:
                progress_callback()

        for ticker, value in current_value_by_ticker.items():
            self.distribution_by_ticker_current_value[ticker]=(value/self.total_current_value)*100.0

        for ticker, value in revenue_by_ticker.items():
            self.distribution_by_ticker_revenue[ticker]=(value/self.total_revenue)*100.0

        self.data=self.merge(dataframes_2)

    @staticmethod
    def transform_dataframe_to_dataframe_with_ticker(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> pd.DataFrame:
        """Equivalent of tranform_dataframe_to_dataframe_with_isin: replaces isin_column_name's
        values (ISIN codes) with the yfinance ticker looked up from the JSON file, in place.
        Run this on a per-instrument dataframe before constructing a Stock from it."""
        with open(path_to_json_file, 'r') as f:
            j=json.load(f)
            for _, row in dataframe.iterrows():
                row[isin_column_name]=j[row[isin_column_name]]["ticker"]
        return dataframe

    @staticmethod
    def get_ticker_currency(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> str:
        """Equivalent of get_ticker_currency. Only works if all rows share the same ticker/isin."""
        with open(path_to_json_file, "r") as f:
            j=json.load(f)
            currency=j[dataframe[isin_column_name].iloc[0]]['currency']
        return currency

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into a single portfolio DataFrame.
        Equivalent of merge_dataframes(dataframes)."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

    @staticmethod
    def _load_tickers_json(tickers_json: str) -> dict:
        with open(tickers_json, 'r') as f:
            return json.load(f)

    @classmethod
    def _split_by_isin(cls, dataframe: pd.DataFrame) -> list:
        dataframes=dict()
        for _, row in dataframe.iterrows():
            if dataframes.get(row[cls.CSV_TICKER_COLUMN]) is None:
                dataframes[row[cls.CSV_TICKER_COLUMN]]=pd.DataFrame()
            dataframes[row[cls.CSV_TICKER_COLUMN]]=pd.concat([dataframes[row[cls.CSV_TICKER_COLUMN]], row], axis=1)

        list_of_dataframes=list(dataframes.values())
        for i in range(len(list_of_dataframes)):
            list_of_dataframes[i]=list_of_dataframes[i].transpose()
            list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i][cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
            list_of_dataframes[i].drop(columns=[cls.CSV_DATE_COLUMN], inplace=True)

        return list_of_dataframes

    @classmethod
    def count_tickers(cls, directory_path: str) -> int:
        """Number of distinct tickers/ISINs in a source directory, without fetching any price
        data — lets a caller (e.g. Portfolio) size a progress bar before construction."""
        return cls._load_sources(directory_path)[cls.CSV_TICKER_COLUMN].nunique()

    @classmethod
    def _load_sources(cls, directory: str) -> pd.DataFrame:
        dataframes=list()
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.csv'):
                continue
            state_value=os.path.splitext(filename)[0]
            df=pd.read_csv(os.path.join(directory, filename))
            if df.empty:
                continue
            df[cls.SOURCE_TYPE_COLUMN]=state_value
            dataframes.append(df)

        if not dataframes:
            raise ValueError(f"No non-empty .csv files found in {directory}")

        return pd.concat(dataframes)

    def _replace_isin_with_ticker(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Swaps CSV_TICKER_COLUMN's values for the yfinance ticker symbol from self.tickers, in place
        on every per-instrument dataframe. Equivalent of stock_calculator_library.tranform_dataframe_to_dataframe_with_isin."""
        dataframe[self.CSV_TICKER_COLUMN]=dataframe[self.CSV_TICKER_COLUMN].map(lambda isin: self.tickers[isin]['ticker'])
        return dataframe

    def _compute_data(self, dataframe: pd.DataFrame, currency_from: str, currency_to: str, cache_dir: str=None, force_refresh: bool=False) -> pd.DataFrame:
        start_date=dataframe.index.min()
        ticker_name=self.tickers.get(dataframe[self.CSV_TICKER_COLUMN].iloc[0])['ticker']

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            cache_key=DiskCache.make_key('stock', ticker_name, currency_from, currency_to, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if cached is not None:
                    return cached

        currency=Currency(currency_from, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        ticker_data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        ticker_data.index=ticker_data.index.tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=datetime.today(), freq='D').tz_localize(None).normalize()
        )
        data=all_days.join(ticker_data).ffill()
        data[self.CLOSE_COLUMN]=data[self.CLOSE_COLUMN]*currency.data[self.CLOSE_COLUMN]

        # Transactions must be walked in date order (not CSV/file order) since a sell needs
        # the running average cost basis built up by every buy that precedes it in time -
        # inherently sequential, so unlike the rest of this method it can't be reduced to a
        # single vectorized expression. What's vectorized instead: pulling every column (and
        # each transaction's same-day FX rate) out as plain numpy arrays once up front, and
        # accumulating into numpy arrays by integer position instead of repeated
        # .iterrows()/.loc[label] calls inside the loop - .iterrows() rebuilds a pandas Series
        # per row and .loc[label] does a label lookup per call, both far more expensive than a
        # numpy array index. The arithmetic, rounding, and iteration order are unchanged.
        sorted_df=dataframe.sort_index(kind='stable')
        n=len(sorted_df)

        def column_or_nan(column: str) -> np.ndarray:
            # A source directory doesn't have to carry every CSV (e.g. buy.csv alone, no
            # sells/dividends yet) - a column only absent because its CSV never existed is
            # never actually read below (each is only used under its own state branch), same
            # as when the original per-row code simply never reached that branch.
            if column in sorted_df.columns:
                return sorted_df[column].to_numpy(dtype=float)
            return np.full(n, np.nan)

        state=sorted_df[self.SOURCE_TYPE_COLUMN].to_numpy()
        amount=column_or_nan(self.CSV_AMOUNT_OF_UNITS_COLUMN)
        price=column_or_nan(self.CSV_PRICE_OF_UNIT_COLUMN)
        penalty=column_or_nan(self.CSV_PENALTY_COLUMN)
        sell_tax=column_or_nan(self.CSV_SELL_TAX_COLUMN)
        dividend=column_or_nan(self.CSV_DIVIDEND_COLUMN)
        dividend_tax=column_or_nan(self.CSV_DIVIDEND_TAX_COLUMN)
        # One vectorized lookup for every transaction's same-day FX rate, instead of one
        # currency.data.loc[idx, ...] call per row.
        fx=currency.data.loc[sorted_df.index, self.CLOSE_COLUMN].to_numpy(dtype=float)

        # Each transaction's integer position in data's (continuous, daily) index, so the loop
        # below can write by position instead of by date label.
        position=data.index.get_indexer(sorted_df.index)
        if (position<0).any():
            missing=sorted_df.index[position<0]
            raise KeyError(f"Transaction date(s) {list(missing)} for {ticker_name} fall outside the computed daily range.")

        money_invested_by_day=np.zeros(len(data))
        units_by_day=np.zeros(len(data))
        dividend_by_day=np.zeros(len(data))
        realized_profit_by_day=np.zeros(len(data))

        running_units=0.0
        running_money_invested=0.0

        for i in range(n):
            pos=position[i]
            row_state=state[i]
            row_fx=fx[i]

            if row_state=='buy':
                units=round(amount[i], 4)
                raw_money_invested=amount[i]*price[i]*row_fx
                money_invested=round((penalty[i]+1.0)*raw_money_invested, 2)

                money_invested_by_day[pos]+=money_invested
                units_by_day[pos]+=units

                running_units+=units
                running_money_invested+=money_invested
            elif row_state=='sell':
                units_sold=round(amount[i], 4)
                if units_sold>running_units+1e-9:
                    raise ValueError(f"Cannot sell {units_sold} units of {ticker_name} on {sorted_df.index[i].date()}: only {running_units} units held.")

                # Remove cost basis in proportion to the units sold so the average price of the
                # remaining position (Money_invested/Units) is unchanged by a partial sell.
                fraction_sold=units_sold/running_units if running_units>1e-9 else 0.0
                money_invested_removed=round(running_money_invested*fraction_sold, 2)

                proceeds=amount[i]*price[i]*row_fx

                units_by_day[pos]-=units_sold
                money_invested_by_day[pos]-=money_invested_removed
                realized_profit_by_day[pos]+=round(proceeds-money_invested_removed, 2)

                running_units-=units_sold
                running_money_invested-=money_invested_removed
            elif row_state=='sell_tax':
                realized_profit_by_day[pos]-=round(sell_tax[i]*row_fx, 2)
            elif row_state=='dividend':
                dividend_by_day[pos]+=round(dividend[i]*row_fx, 2)
            elif row_state=='dividend_tax':
                dividend_by_day[pos]-=round(dividend_tax[i]*row_fx, 2)

        data[self.MONEY_INVESTED_COLUMN]=money_invested_by_day
        data[self.UNITS_COLUMN]=units_by_day
        data[self.DIVIDEND_COLUMN]=dividend_by_day
        data[self.REALIZED_PROFIT_COLUMN]=realized_profit_by_day

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        data[self.UNITS_COLUMN]=data[self.UNITS_COLUMN].cumsum()
        data[self.DIVIDEND_COLUMN]=data[self.DIVIDEND_COLUMN].cumsum()
        data[self.REALIZED_PROFIT_COLUMN]=data[self.REALIZED_PROFIT_COLUMN].cumsum()

        # Unrealized profit = current market value of the held units minus their cost basis.
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=round(data[self.CLOSE_COLUMN]*data[self.UNITS_COLUMN]-data[self.MONEY_INVESTED_COLUMN], 2)
        data[self.PROFIT_COLUMN]=round(data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN], 2)
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)

        if cache is not None:
            cache.set(cache_key, data)

        return data
