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
import pandas as pd
import yfinance as yf
from .currency_calculator_library import Currency


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

    def __init__(self, directory_path: str, stock_data: str, currency_to: str, progress_callback: Callable[[], None]=None):
        """progress_callback: optional zero-arg callback invoked once per ticker, right after that
        ticker's price history has been fetched and computed — the unit of work a caller (e.g.
        Portfolio) would want to track progress by, since that fetch is what actually takes time."""
        self.total_money_invested=0.0
        self.total_current_value=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.dataframe=self._load_sources(directory_path)
        self.tickers=self._load_tickers_json(stock_data)

        dataframes=self._split_by_isin(self.dataframe)

        # Money invested per buy row isn't a column on the source dataframe (see _compute_data,
        # which derives it the same way) — recompute it here instead of assuming one exists.
        money_invested_by_ticker=dict()
        for df in dataframes:
            currency=Currency(self.get_ticker_currency(df, stock_data, self.CSV_TICKER_COLUMN), currency_to, df.index.min())

            money_invested=0.0
            for idx, row in df.iterrows():
                if row[self.SOURCE_TYPE_COLUMN]=='buy':
                    money_invested+=round((row[self.CSV_PENALTY_COLUMN]+1.0)*row[self.CSV_AMOUNT_OF_UNITS_COLUMN]*row[self.CSV_PRICE_OF_UNIT_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN], 2)

            money_invested_by_ticker[df[self.CSV_TICKER_COLUMN].iloc[0]]=money_invested
            self.total_money_invested+=money_invested

        dataframes_2=list()
        current_value_by_ticker=dict()
        for df in dataframes:
            ticker=df[self.CSV_TICKER_COLUMN].iloc[0]
            self.distribution_by_ticker[ticker]=(money_invested_by_ticker[ticker]/self.total_money_invested)*100.0
            computed=self._compute_data(df, self.get_ticker_currency(df, stock_data, self.CSV_TICKER_COLUMN), currency_to)
            dataframes_2.append(computed)

            # Current market value of the position: cost basis still held plus its unrealized gain.
            current_value_by_ticker[ticker]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_ticker[ticker]

            if progress_callback is not None:
                progress_callback()

        for ticker, value in current_value_by_ticker.items():
            self.distribution_by_ticker_current_value[ticker]=(value/self.total_current_value)*100.0

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

    def _compute_data(self, dataframe: pd.DataFrame, currency_from: str, currency_to: str) -> pd.DataFrame:
        start_date=dataframe.index.min()
        ticker_name=self.tickers.get(dataframe[self.CSV_TICKER_COLUMN].iloc[0])['ticker']

        currency=Currency(currency_from, currency_to, start_date)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        ticker_data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        ticker_data.index=ticker_data.index.tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=datetime.today(), freq='D').tz_localize(None).normalize()
        )
        data=all_days.join(ticker_data).ffill()
        data[self.CLOSE_COLUMN]=data[self.CLOSE_COLUMN]*currency.data[self.CLOSE_COLUMN]

        data[self.MONEY_INVESTED_COLUMN]=0.0
        data[self.DIVIDEND_COLUMN]=0.0
        data[self.UNITS_COLUMN]=0.0
        data[self.REALIZED_PROFIT_COLUMN]=0.0

        # Transactions must be walked in date order (not CSV/file order) since a sell needs
        # the running average cost basis built up by every buy that precedes it in time.
        running_units=0.0
        running_money_invested=0.0

        for idx, rows in dataframe.sort_index(kind='stable').iterrows():
            if rows[self.SOURCE_TYPE_COLUMN]=='buy':
                units=round(rows[self.CSV_AMOUNT_OF_UNITS_COLUMN], 4)
                raw_money_invested=rows[self.CSV_AMOUNT_OF_UNITS_COLUMN]*rows[self.CSV_PRICE_OF_UNIT_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN]
                money_invested=round((rows[self.CSV_PENALTY_COLUMN]+1.0)*raw_money_invested, 2)

                data.loc[idx, self.MONEY_INVESTED_COLUMN]+=money_invested
                data.loc[idx, self.UNITS_COLUMN]+=units

                running_units+=units
                running_money_invested+=money_invested
            elif rows[self.SOURCE_TYPE_COLUMN]=='sell':
                units_sold=round(rows[self.CSV_AMOUNT_OF_UNITS_COLUMN], 4)
                if units_sold>running_units+1e-9:
                    raise ValueError(f"Cannot sell {units_sold} units of {ticker_name} on {idx.date()}: only {running_units} units held.")

                # Remove cost basis in proportion to the units sold so the average price of the
                # remaining position (Money_invested/Units) is unchanged by a partial sell.
                fraction_sold=units_sold/running_units if running_units>1e-9 else 0.0
                money_invested_removed=round(running_money_invested*fraction_sold, 2)

                proceeds=rows[self.CSV_AMOUNT_OF_UNITS_COLUMN]*rows[self.CSV_PRICE_OF_UNIT_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN]

                data.loc[idx, self.UNITS_COLUMN]-=units_sold
                data.loc[idx, self.MONEY_INVESTED_COLUMN]-=money_invested_removed
                data.loc[idx, self.REALIZED_PROFIT_COLUMN]+=round(proceeds-money_invested_removed, 2)

                running_units-=units_sold
                running_money_invested-=money_invested_removed
            elif rows[self.SOURCE_TYPE_COLUMN]=='sell_tax':
                data.loc[idx, self.REALIZED_PROFIT_COLUMN]-=round(rows[self.CSV_SELL_TAX_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN], 2)
            elif rows[self.SOURCE_TYPE_COLUMN]=='dividend':
                data.loc[idx, self.DIVIDEND_COLUMN]+=round(rows[self.CSV_DIVIDEND_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN], 2)
            elif rows[self.SOURCE_TYPE_COLUMN]=='dividend_tax':
                data.loc[idx, self.DIVIDEND_COLUMN]-=round(rows[self.CSV_DIVIDEND_TAX_COLUMN]*currency.data.loc[idx, self.CLOSE_COLUMN], 2)

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        data[self.UNITS_COLUMN]=data[self.UNITS_COLUMN].cumsum()
        data[self.DIVIDEND_COLUMN]=data[self.DIVIDEND_COLUMN].cumsum()
        data[self.REALIZED_PROFIT_COLUMN]=data[self.REALIZED_PROFIT_COLUMN].cumsum()

        # Unrealized profit = current market value of the held units minus their cost basis.
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=round(data[self.CLOSE_COLUMN]*data[self.UNITS_COLUMN]-data[self.MONEY_INVESTED_COLUMN], 2)
        data[self.PROFIT_COLUMN]=round(data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN], 2)
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)
        return data
