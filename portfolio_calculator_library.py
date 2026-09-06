"""
Class-based alternative to portfolio_calculator_library.py — a sketch, not wired
into the rest of the codebase. The original module stays a set of free functions
that each take a DataFrame; here the same logic is grouped behind a Portfolio
class whose constructor plays the role of create_dataframe_and_get_data_from_directory:
it loads and splits the per-instrument DataFrames once, and the rest of the
methods operate on the state the constructor built instead of re-taking a
dataframe argument every call.
"""

import os
from datetime import datetime
import pandas as pd
import numpy as np
from tqdm import tqdm
from .stock_calculator_library import Stock
from .bonds_calculator_library import Bonds


class Portfolio:
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    ISIN_COLUMN='isin'
    SOURCE_TYPE_COLUMN='type'

    def __init__(self, sources: dict, tickers_json: str=None, currency: str='USD'):
        """sources: a dict mapping each path to the asset type it holds (e.g. 'stock', 'bonds',
        'bank_account', 'crypto'). Each path is either
        - a directory of per-state CSVs (state inferred from filename, as in
          create_dataframe_and_get_data_from_directory), or
        - an already-prepared single transactions CSV that already has a 'state'
          column (as in create_dataframe_and_get_data).
        Directories and prepared CSVs can be mixed freely in the same dict; every row loaded
        from a given path is tagged with that path's type (SOURCE_TYPE_COLUMN) before all rows
        are combined and split by ISIN_COLUMN, so each per-instrument dataframe carries along
        which asset-type module should process it (dataframe[Portfolio.SOURCE_TYPE_COLUMN].iloc[0]).

        tickers_json: optional path to the JSON file (see CLAUDE.md / stock_calculator_library)
        mapping each ISIN to {"ticker": <yfinance symbol>, "currency": <instrument currency>}.
        When given, get_currency()/get_dataframe_currency() become available."""
        self.distribution_by_directory=dict()
        self.distribution_by_ticker=dict()
        self.total_invested_money=0.0
        portfolio_list=list()

        # Counting tickers/bonds up front (cheap — just reads/splits CSVs, no network calls) lets
        # one progress bar span the whole portfolio, tracking the unit of work that's actually
        # slow: one yfinance fetch per ticker (Bonds rows are local computation, but are counted
        # in too so the bar reaches 100% and moves smoothly through that fast section as well).
        total_units=0
        for dir, type in sources.items():
            if type=='stock':
                total_units+=Stock.count_tickers(dir)
            elif type=='bonds':
                total_units+=Bonds.count_bonds(dir)

        with tqdm(total=total_units, desc='Loading portfolio') as progress_bar:
            for dir, type in sources.items():
                if type=='stock':
                    stock=Stock(dir, tickers_json, currency, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=stock.total_money_invested
                    self.total_invested_money+=stock.total_money_invested
                    for key, value in stock.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*stock.total_money_invested, 2)
                    portfolio_list.append(stock)
                elif type=='bonds':
                    bonds=Bonds(dir, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=bonds.total_money_invested
                    self.total_invested_money+=bonds.total_money_invested
                    for key, value in bonds.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*bonds.total_money_invested, 2)
                    portfolio_list.append(bonds)
                elif type=='crypto':
                    pass
                elif type=='commodities':
                    pass

        for key, value in self.distribution_by_directory.items():
            self.distribution_by_directory[key]=100.0*value/self.total_invested_money

        for key, value in self.distribution_by_ticker.items():
            self.distribution_by_ticker[key]=100.0*value/self.total_invested_money

        self.data=self.merge(portfolio_list)

    @classmethod
    def from_csv(cls, dataframe_file: str, source_type: str, tickers_json: str=None) -> 'Portfolio':
        """Convenience alias — the constructor already accepts a single prepared CSV."""
        return cls({dataframe_file: source_type}, tickers_json)

    def _replace_isin_with_ticker(self):
        """Swaps ISIN_COLUMN's values for the yfinance ticker symbol from self.tickers, in place on
        every per-instrument dataframe. Equivalent of stock_calculator_library.tranform_dataframe_to_dataframe_with_isin."""
        for dataframe in self.dataframes:
            dataframe[self.ISIN_COLUMN]=dataframe[self.ISIN_COLUMN].map(lambda isin: self.tickers[isin]['ticker'])

    def get_currency(self, isin: str) -> str:
        return self.tickers[isin]['currency']

    def get_dataframe_currency(self, dataframe: pd.DataFrame) -> str:
        """Currency of the instrument a per-instrument dataframe (one of self.dataframes) belongs to.
        Once tickers_json is supplied, ISIN_COLUMN holds the yfinance ticker (see _replace_isin_with_ticker),
        so this looks the currency up by ticker rather than by the original ISIN."""
        return self._currency_by_ticker[dataframe[self.ISIN_COLUMN].iloc[0]]

    @classmethod
    def _split_by_isin(cls, dataframe: pd.DataFrame) -> list:
        dataframes=dict()
        for _, row in dataframe.iterrows():
            if dataframes.get(row[cls.ISIN_COLUMN]) is None:
                dataframes[row[cls.ISIN_COLUMN]]=pd.DataFrame()
            dataframes[row[cls.ISIN_COLUMN]]=pd.concat([dataframes[row[cls.ISIN_COLUMN]], row], axis=1)

        list_of_dataframes=list(dataframes.values())
        for i in range(len(list_of_dataframes)):
            list_of_dataframes[i]=list_of_dataframes[i].transpose()
            list_of_dataframes[i].index=pd.to_datetime(list_of_dataframes[i]['date'], format='%Y-%m-%d')
            list_of_dataframes[i].drop(columns=['date'], inplace=True)

        return list_of_dataframes

    def _load_sources(self, sources: dict) -> list:
        dataframes=list()
        for source, source_type in sources.items():
            if os.path.isdir(source):
                source_dataframes=self._read_directory(source)
            else:
                source_dataframes=[pd.read_csv(source)]

            for dataframe in source_dataframes:
                dataframe[self.SOURCE_TYPE_COLUMN]=source_type
                dataframes.append(dataframe)

        combined_dataframe=pd.concat(dataframes, ignore_index=True)
        return self._split_by_isin(combined_dataframe)

    def _read_directory(self, directory: str) -> list:
        dataframes=list()
        money_invested=0.0
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith('.csv'):
                continue
            state_value=os.path.splitext(filename)[0]
            df=pd.read_csv(os.path.join(directory, filename))
            df['state']=state_value
            # print(df) #remove
            # if state_value=='buy':
            #     money_invested+=df[self.MONEY_INVESTED_COLUMN].cumsum().ffill().iloc[-1]
            #     self.distribution_by_directory[directory]=money_invested
            dataframes.append(df)
        return dataframes

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

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into a single portfolio DataFrame.
        Equivalent of merge_dataframes(dataframes)."""
        list_of_df=[df.data for df in dataframes]
        return pd.concat(list_of_df).groupby(level=0, sort=True).sum().ffill()

    def resample(self, resample_rule: str) -> pd.DataFrame:
        timedelta_to_subtract: pd.DateOffset
        if resample_rule[0]=='D':
            timedelta_to_subtract=pd.DateOffset(days=1)
        elif resample_rule[0]=='W':
            timedelta_to_subtract=pd.DateOffset(weeks=1)
        elif resample_rule[0]=='M':
            timedelta_to_subtract=pd.DateOffset(months=1)
        elif resample_rule[0]=='Q':
            timedelta_to_subtract=pd.DateOffset(months=3)
        elif resample_rule[0]=='Y':
            timedelta_to_subtract=pd.DateOffset(years=1)

        new_row=pd.DataFrame(
            [{col: 0.0 for col in self.portfolio.columns}],
            index=[pd.to_datetime(self.portfolio.index[0]-timedelta_to_subtract, format='%Y-%m-%d')]
        )
        dataframe=pd.concat([self.portfolio, new_row]).sort_index()
        self.portfolio=dataframe.resample(resample_rule).ffill()
        return self.portfolio

    def calculate_irr(self):
        dataframe=self.data

        dataframe['Prev_money_inv']=dataframe[self.MONEY_INVESTED_COLUMN].shift(1).fillna(0.0)
        dataframe['Total_money']=round(dataframe[self.MONEY_INVESTED_COLUMN]+dataframe[self.PROFIT_COLUMN], 2)
        dataframe['Cashflow']=round(dataframe['Prev_money_inv']-dataframe[self.MONEY_INVESTED_COLUMN], 2)

        irr=np.full(len(dataframe['Cashflow']), np.nan)
        guess=0.1
        irr[0]=0.0

        for i in range(len(dataframe['Cashflow'])):
            guess=self._irr_newton(dataframe['Cashflow'].iloc[:i+1].to_list()+[dataframe['Total_money'].iloc[i]], guess=guess)
            irr[i]=round(((guess+1.0)**i-1)*100.0, 2)
            if np.isnan(guess):
                guess=0.1

        dataframe['Irr']=irr
        self.portfolio=dataframe.drop(columns=['Prev_money_inv', 'Cashflow'], inplace=True)

    def get_earliest_date(self) -> datetime:
        earliest_date=datetime.today()
        for df in self.dataframes:
            if df.index.min()<earliest_date:
                earliest_date=df.index.min()
        return earliest_date

    def calculate_money_earned_between_dates(self, start_date: datetime, end_date: datetime) -> float:
        dataframe=self.portfolio

        start_date_profit=0.0
        end_date_profit=0.0

        if start_date.strftime('%Y-%m-%d') in dataframe.index:
            start_date_profit=dataframe.loc[start_date.strftime('%Y-%m-%d'), self.PROFIT_COLUMN]

        if end_date.strftime('%Y-%m-%d') in dataframe.index:
            end_date_profit=dataframe.loc[end_date.strftime('%Y-%m-%d'), self.PROFIT_COLUMN]

        return end_date_profit-start_date_profit

    def calculate_money_earned_between_dates_column(self, days_between: int=0, offset: int=0) -> pd.DataFrame:
        dataframe=self.portfolio

        dataframe['Daily_return']=0.0
        if days_between==0:
            days_between=(dataframe.index[-1]-dataframe.index[0]).days
        for idx, _ in dataframe.iterrows():
            dataframe.loc[idx, 'Daily_return']=round(
                self.calculate_money_earned_between_dates(idx-pd.DateOffset(days=days_between+offset), idx-pd.DateOffset(days=offset))/days_between, 2
            )

        self.portfolio=dataframe
        return self.portfolio