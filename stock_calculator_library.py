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

import json
from datetime import datetime
import pandas as pd
import yfinance as yf
from .currency_calculator_library import Currency


class Stock:
    # The column that holds the yfinance-recognizable ticker symbol. Named 'isin' to match
    # Portfolio.ISIN_COLUMN in test.py: that class's _replace_isin_with_ticker overwrites the
    # isin column in place with the ticker, rather than adding a separate column.
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    TICKER_COLUMN='isin'
    SOURCE_TYPE_COLUMN='type'

    def __init__(self, dataframe: pd.DataFrame, stock_data: str, currency_to: str):
        """dataframe: one per-instrument transactions dataframe (e.g. one entry of
        Portfolio.dataframes from test.py) whose TICKER_COLUMN already holds a
        yfinance-recognizable ticker symbol — see transform_dataframe_to_dataframe_with_ticker.
        currency_to: the base currency to convert Money_invested/Profit into (the instrument's
        own currency is looked up from stock_data, and both feed a Currency instance)."""
        self.dataframe=dataframe
        print(self.dataframe)
        self.tickers=self._load_tickers_json(stock_data)
        currency_from=self.tickers.get(self.dataframe[self.TICKER_COLUMN].iloc[0])['currency']
        self.currency=Currency(currency_from, currency_to, self.dataframe.index[0])
        self._replace_isin_with_ticker()

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
    def _load_tickers_json(tickers_json: str) -> dict:
        with open(tickers_json, 'r') as f:
            return json.load(f)

    def _replace_isin_with_ticker(self):
        """Swaps ISIN_COLUMN's values for the yfinance ticker symbol from self.tickers, in place on
        every per-instrument dataframe. Equivalent of stock_calculator_library.tranform_dataframe_to_dataframe_with_isin."""
        self.dataframe[self.TICKER_COLUMN]=self.dataframe[self.TICKER_COLUMN].map(lambda isin: self.tickers[isin]['ticker'])

    def _compute_data(self) -> pd.DataFrame:
        start_date=self.dataframe.index[0]

        ticker=yf.Ticker(self.dataframe[self.TICKER_COLUMN].iloc[0])
        data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        data.index=data.index.tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=datetime.today(), freq='D').tz_localize(None).normalize()
        )
        data=all_days.join(data).ffill()
        data['Close']=data['Close']*self.currency.data['Close']

        print(data)

        data['Money_invested']=0.0
        data['Avg_price']=0.0
        data['Dividend']=0.0
        data['Units']=0.0

        for idx, rows in self.dataframe.iterrows():
            if rows['state']=='buy':
                data.loc[idx, 'Money_invested']=round(rows['Money_invested'], 2)
                data.loc[idx, 'Units']=round(rows['amount_of_units'], 4)
                data.loc[idx, 'Avg_price']=rows['Money_invested']*rows['price_of_unit']*self.currency.data.loc[idx, 'Close']*(1+rows['penalty'])
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
