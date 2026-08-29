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
import pandas as pd
import yfinance as yf
from .currency_calculator_library import Currency


class Stock:
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    TICKER_COLUMN='isin'
    SOURCE_TYPE_COLUMN='state'

    def __init__(self, directory_path: str, stock_data: str, currency_to: str):
        self.total_money_invested=0.0
        self.distribution_by_ticker=dict()
        self.dataframe=self._load_sources(directory_path)
        self.tickers=self._load_tickers_json(stock_data)

        for idx, row in self.dataframe.iterrows():
            if row['state']=='buy':
                self.total_money_invested+=row[self.MONEY_INVESTED_COLUMN]

        dataframes=self._split_by_isin(self.dataframe)
        dataframes_2=list()

        for df in dataframes:
            self.distribution_by_ticker[df[self.TICKER_COLUMN].iloc[0]]=0.0
            for idx, row in df.iterrows():
                if row['state']=='buy':
                    self.distribution_by_ticker[df[self.TICKER_COLUMN].iloc[0]]+=(row[self.MONEY_INVESTED_COLUMN]/self.total_money_invested)*100.0
            dataframes_2.append(self._compute_data(df, self.get_ticker_currency(df, stock_data, self.TICKER_COLUMN), currency_to))

        self.dataframe=self.merge(dataframes_2)

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

    def calculate_total_money_invested(self) -> float:
        money_inv=0.0
        for idx, row in self.dataframe.iterrows():
            if row['state']=='buy':
                money_inv+=row[self.MONEY_INVESTED_COLUMN]
        return money_inv

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
            if dataframes.get(row[cls.TICKER_COLUMN]) is None:
                dataframes[row[cls.TICKER_COLUMN]]=pd.DataFrame()
            dataframes[row[cls.TICKER_COLUMN]]=pd.concat([dataframes[row[cls.TICKER_COLUMN]], row], axis=1)

        list_of_dataframes=list(dataframes.values())
        for i in range(len(list_of_dataframes)):
            list_of_dataframes[i]=list_of_dataframes[i].transpose()
            list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i]['date'], format='%Y-%m-%d')
            list_of_dataframes[i].drop(columns=['date'], inplace=True)

        return list_of_dataframes

    def _load_sources(self, directory: str) -> pd.DataFrame:
        dataframes=list()
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.csv'):
                continue
            state_value=os.path.splitext(filename)[0]
            df=pd.read_csv(os.path.join(directory, filename))
            df['state']=state_value
            dataframes.append(df)
        return pd.concat(dataframes)

    def _replace_isin_with_ticker(self, dataframe: pd.DataFrame) -> pd.DataFrame:
        """Swaps ISIN_COLUMN's values for the yfinance ticker symbol from self.tickers, in place on
        every per-instrument dataframe. Equivalent of stock_calculator_library.tranform_dataframe_to_dataframe_with_isin."""
        dataframe[self.TICKER_COLUMN]=dataframe[self.TICKER_COLUMN].map(lambda isin: self.tickers[isin]['ticker'])
        return dataframe

    def _compute_data(self, dataframe: pd.DataFrame, currency_from: str, currency_to: str) -> pd.DataFrame:
        start_date=dataframe.index[0]
        ticker_name=self.tickers.get(dataframe[self.TICKER_COLUMN].iloc[0])['ticker']

        currency=Currency(currency_from, currency_to, start_date)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        ticker_data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        ticker_data.index=ticker_data.index.tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=datetime.today(), freq='D').tz_localize(None).normalize()
        )
        data=all_days.join(ticker_data).ffill()
        data['Close']=data['Close']*currency.data['Close']

        data['Money_invested']=0.0
        data['Avg_price']=0.0
        data['Dividend']=0.0
        data['Units']=0.0

        for idx, rows in dataframe.iterrows():
            if rows['state']=='buy':
                data.loc[idx, 'Money_invested']=round(rows['Money_invested'], 2)
                data.loc[idx, 'Units']=round(rows['amount_of_units'], 4)
                data.loc[idx, 'Avg_price']=rows['Money_invested']*rows['price_of_unit']*currency.data.loc[idx, 'Close']*(1+rows['penalty'])
            elif rows['state'] in ('sell', 'sell_tax', 'swap', 'swap_tax'):
                pass
            elif rows['state']=='dividend':
                data.loc[idx, 'Dividend']+=round(rows['dividend'], 2)
            elif rows['state']=='dividend_tax':
                data.loc[idx, 'Dividend']-=round(rows['dividend_tax'], 2)

        data['Money_invested']=data['Money_invested'].cumsum()
        data['Units']=data['Units'].cumsum()
        data['Avg_price']=data['Avg_price'].cumsum()/data['Money_invested']
        data['Dividend']=data['Dividend'].cumsum()
        data['Money_invested_after_penalty']=data['Avg_price']*data['Units']

        data['Profit_without_dividends']=round(
            (data['Close']-data['Avg_price'])/data['Avg_price']*data['Money_invested']-data['Money_invested']+data['Money_invested_after_penalty'], 2
        )
        data['Profit']=round(data['Profit_without_dividends']+data['Dividend'], 2)
        data.drop(columns=['Close', 'Money_invested_after_penalty', 'Avg_price', 'Units'], inplace=True)
        return data
