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
from .commodity_calculator_library import Commodity
from .crypto_calculator_library import Crypto


class Portfolio:
    # --- Output: self.data contract + Portfolio-specific extras. The first three form the
    # shared DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DAILY_RETURN_COLUMN='Daily_return'
    IRR_COLUMN='Irr'
    # Working-only columns, created and dropped again within calculate_irr.
    PREV_MONEY_INVESTED_COLUMN='Prev_money_inv'
    TOTAL_MONEY_COLUMN='Total_money'
    CASHFLOW_COLUMN='Cashflow'

    # --- Ingestion tags: synthesized while loading (from the sources dict / CSV filename),
    # not read from inside a CSV cell — but consumed everywhere exactly like input columns. ---
    SOURCE_TYPE_COLUMN='type'          # asset type ('stock'/'bonds'/...), from the sources dict
    TRANSACTION_STATE_COLUMN='state'   # per-row state ('buy'/'sell'/...), from the CSV filename

    # --- Input: columns read from a raw sources CSV (only reachable via the currently-dead
    # _load_sources/_read_directory/_split_by_isin path — see __init__'s docstring/comments). ---
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'

    def __init__(self, sources: dict, tickers_json: str=None, currency: str='USD'):
        """sources: a dict mapping each path to the asset type it holds (e.g. 'stock', 'bonds',
        'commodities', 'bank_account', 'crypto'). Each path is either
        - a directory of per-state CSVs (state inferred from filename, as in
          create_dataframe_and_get_data_from_directory), or
        - an already-prepared single transactions CSV that already has a 'state'
          column (as in create_dataframe_and_get_data).
        Directories and prepared CSVs can be mixed freely in the same dict; every row loaded
        from a given path is tagged with that path's type (SOURCE_TYPE_COLUMN) before all rows
        are combined and split by CSV_TICKER_COLUMN, so each per-instrument dataframe carries along
        which asset-type module should process it (dataframe[Portfolio.SOURCE_TYPE_COLUMN].iloc[0]).

        tickers_json: optional path to the JSON file (see CLAUDE.md / stock_calculator_library)
        mapping each ISIN to {"ticker": <yfinance symbol>, "currency": <instrument currency>}.
        When given, get_currency()/get_dataframe_currency() become available."""
        self.distribution_by_directory=dict()
        self.distribution_by_directory_current_value=dict()
        self.distribution_by_directory_revenue=dict()
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        self.total_invested_money=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        portfolio_list=list()

        # Counting tickers/bonds up front (cheap — just reads/splits CSVs, no network calls) lets
        # one progress bar span the whole portfolio, tracking the unit of work that's actually
        # slow: one yfinance fetch per ticker. A whole bonds directory only counts as a single
        # unit — Bonds computation is fast, local work with no per-row network calls, and its
        # progress_callback now fires once per Bonds instance rather than once per bond row.
        total_units=0
        for dir, type in sources.items():
            if type=='stock':
                total_units+=Stock.count_tickers(dir)
            elif type=='bonds':
                total_units+=1
            elif type=='commodities':
                total_units+=Commodity.count_tickers(dir)
            elif type=='crypto':
                total_units+=Crypto.count_tickers(dir)

        with tqdm(total=total_units, desc='Loading portfolio') as progress_bar:
            for dir, type in sources.items():
                if type=='stock':
                    stock=Stock(dir, tickers_json, currency, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=stock.total_money_invested
                    self.distribution_by_directory_current_value[dir]=stock.total_current_value
                    self.distribution_by_directory_revenue[dir]=stock.total_revenue
                    self.total_invested_money+=stock.total_money_invested
                    self.total_current_value+=stock.total_current_value
                    self.total_revenue+=stock.total_revenue
                    for key, value in stock.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*stock.total_money_invested, 2)
                    for key, value in stock.distribution_by_ticker_current_value.items():
                        self.distribution_by_ticker_current_value[key]=round(value/100.0*stock.total_current_value, 2)
                    for key, value in stock.distribution_by_ticker_revenue.items():
                        self.distribution_by_ticker_revenue[key]=round(value/100.0*stock.total_revenue, 2)
                    portfolio_list.append(stock)
                elif type=='bonds':
                    bonds=Bonds(dir, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=bonds.total_money_invested
                    self.distribution_by_directory_current_value[dir]=bonds.total_current_value
                    self.distribution_by_directory_revenue[dir]=bonds.total_revenue
                    self.total_invested_money+=bonds.total_money_invested
                    self.total_current_value+=bonds.total_current_value
                    self.total_revenue+=bonds.total_revenue
                    for key, value in bonds.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*bonds.total_money_invested, 2)
                    for key, value in bonds.distribution_by_ticker_current_value.items():
                        self.distribution_by_ticker_current_value[key]=round(value/100.0*bonds.total_current_value, 2)
                    for key, value in bonds.distribution_by_ticker_revenue.items():
                        self.distribution_by_ticker_revenue[key]=round(value/100.0*bonds.total_revenue, 2)
                    portfolio_list.append(bonds)
                elif type=='commodities':
                    commodity=Commodity(dir, currency, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=commodity.total_money_invested
                    self.distribution_by_directory_current_value[dir]=commodity.total_current_value
                    self.distribution_by_directory_revenue[dir]=commodity.total_revenue
                    self.total_invested_money+=commodity.total_money_invested
                    self.total_current_value+=commodity.total_current_value
                    self.total_revenue+=commodity.total_revenue
                    for key, value in commodity.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*commodity.total_money_invested, 2)
                    for key, value in commodity.distribution_by_ticker_current_value.items():
                        self.distribution_by_ticker_current_value[key]=round(value/100.0*commodity.total_current_value, 2)
                    for key, value in commodity.distribution_by_ticker_revenue.items():
                        self.distribution_by_ticker_revenue[key]=round(value/100.0*commodity.total_revenue, 2)
                    portfolio_list.append(commodity)
                elif type=='crypto':
                    crypto=Crypto(dir, currency, progress_callback=progress_bar.update)
                    self.distribution_by_directory[dir]=crypto.total_money_invested
                    self.distribution_by_directory_current_value[dir]=crypto.total_current_value
                    self.distribution_by_directory_revenue[dir]=crypto.total_revenue
                    self.total_invested_money+=crypto.total_money_invested
                    self.total_current_value+=crypto.total_current_value
                    self.total_revenue+=crypto.total_revenue
                    for key, value in crypto.distribution_by_ticker.items():
                        self.distribution_by_ticker[key]=round(value/100.0*crypto.total_money_invested, 2)
                    for key, value in crypto.distribution_by_ticker_current_value.items():
                        self.distribution_by_ticker_current_value[key]=round(value/100.0*crypto.total_current_value, 2)
                    for key, value in crypto.distribution_by_ticker_revenue.items():
                        self.distribution_by_ticker_revenue[key]=round(value/100.0*crypto.total_revenue, 2)
                    portfolio_list.append(crypto)

        for key, value in self.distribution_by_directory.items():
            self.distribution_by_directory[key]=100.0*value/self.total_invested_money

        for key, value in self.distribution_by_directory_current_value.items():
            self.distribution_by_directory_current_value[key]=100.0*value/self.total_current_value

        for key, value in self.distribution_by_directory_revenue.items():
            self.distribution_by_directory_revenue[key]=100.0*value/self.total_revenue

        for key, value in self.distribution_by_ticker.items():
            self.distribution_by_ticker[key]=100.0*value/self.total_invested_money

        for key, value in self.distribution_by_ticker_current_value.items():
            self.distribution_by_ticker_current_value[key]=100.0*value/self.total_current_value

        for key, value in self.distribution_by_ticker_revenue.items():
            self.distribution_by_ticker_revenue[key]=100.0*value/self.total_revenue

        self.data=self.merge(portfolio_list)

    @classmethod
    def from_csv(cls, dataframe_file: str, source_type: str, tickers_json: str=None) -> 'Portfolio':
        """Convenience alias — the constructor already accepts a single prepared CSV."""
        return cls({dataframe_file: source_type}, tickers_json)

    def _replace_isin_with_ticker(self):
        """Swaps CSV_TICKER_COLUMN's values for the yfinance ticker symbol from self.tickers, in place
        on every per-instrument dataframe. Equivalent of stock_calculator_library.tranform_dataframe_to_dataframe_with_isin."""
        for dataframe in self.dataframes:
            dataframe[self.CSV_TICKER_COLUMN]=dataframe[self.CSV_TICKER_COLUMN].map(lambda isin: self.tickers[isin]['ticker'])

    def get_currency(self, isin: str) -> str:
        return self.tickers[isin]['currency']

    def get_dataframe_currency(self, dataframe: pd.DataFrame) -> str:
        """Currency of the instrument a per-instrument dataframe (one of self.dataframes) belongs to.
        Once tickers_json is supplied, CSV_TICKER_COLUMN holds the yfinance ticker (see _replace_isin_with_ticker),
        so this looks the currency up by ticker rather than by the original ISIN."""
        return self._currency_by_ticker[dataframe[self.CSV_TICKER_COLUMN].iloc[0]]

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
            df[self.TRANSACTION_STATE_COLUMN]=state_value
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
        merged=pd.concat(list_of_df).groupby(level=0, sort=True).sum().ffill()
        return Portfolio._prepend_zero_day(merged)

    @staticmethod
    def _prepend_zero_day(dataframe: pd.DataFrame) -> pd.DataFrame:
        """Adds a zero-valued row one day before the first date, so IRR/return
        calculations have a clean starting point (see README roadmap)."""
        zero_row=pd.DataFrame(
            [{col: 0.0 for col in dataframe.columns}],
            index=[dataframe.index[0]-pd.DateOffset(days=1)]
        )
        return pd.concat([zero_row, dataframe]).sort_index()

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

        dataframe[self.PREV_MONEY_INVESTED_COLUMN]=dataframe[self.MONEY_INVESTED_COLUMN].shift(1).fillna(0.0)
        dataframe[self.TOTAL_MONEY_COLUMN]=round(dataframe[self.MONEY_INVESTED_COLUMN]+dataframe[self.PROFIT_COLUMN], 2)
        dataframe[self.CASHFLOW_COLUMN]=round(dataframe[self.PREV_MONEY_INVESTED_COLUMN]-dataframe[self.MONEY_INVESTED_COLUMN], 2)

        irr=np.full(len(dataframe[self.CASHFLOW_COLUMN]), np.nan)
        guess=0.1
        irr[0]=0.0

        for i in range(len(dataframe[self.CASHFLOW_COLUMN])):
            guess=self._irr_newton(dataframe[self.CASHFLOW_COLUMN].iloc[:i+1].to_list()+[dataframe[self.TOTAL_MONEY_COLUMN].iloc[i]], guess=guess)
            irr[i]=round(((guess+1.0)**i-1)*100.0, 2)
            if np.isnan(guess):
                guess=0.1

        dataframe[self.IRR_COLUMN]=irr
        dataframe.drop(columns=[self.PREV_MONEY_INVESTED_COLUMN, self.CASHFLOW_COLUMN], inplace=True)
        self.portfolio=dataframe

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

        dataframe[self.DAILY_RETURN_COLUMN]=0.0
        if days_between==0:
            days_between=(dataframe.index[-1]-dataframe.index[0]).days
        for idx, _ in dataframe.iterrows():
            dataframe.loc[idx, self.DAILY_RETURN_COLUMN]=round(
                self.calculate_money_earned_between_dates(idx-pd.DateOffset(days=days_between+offset), idx-pd.DateOffset(days=offset))/days_between, 2
            )

        self.portfolio=dataframe
        return self.portfolio