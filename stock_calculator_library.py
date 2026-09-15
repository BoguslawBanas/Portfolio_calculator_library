"""
Class-based stock/ETF calculator: given a directory of buy/sell/dividend transactions plus a
tickers.json mapping each ISIN to its yfinance ticker and native currency, the constructor
fetches the FX data and price history and computes the final Money_invested/Profit/Dividend
DataFrame right away, caching the result on self.data. Every other asset-type calculator in
this package (PolishRetailBonds, Commodity, Crypto, BankAccount) follows the same shape.
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
from .calculator_mixins import ReprMixin, MergeMixin, TickerSplitMixin


class Stock(TickerSplitMixin, MergeMixin, ReprMixin):
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    # Despite the name, this is the *unrealized* component only - current market value of units
    # still held minus their cost basis - so it excludes REALIZED_PROFIT_COLUMN too, not just
    # DIVIDEND_COLUMN. Load-bearing as-is: total_current_value/current_value_by_ticker below both
    # add this to MONEY_INVESTED_COLUMN, which is only correct because realized profit (cash
    # already taken off the table, not part of what the position is worth today) stays excluded.
    # PROFIT_EXCLUDING_DIVIDEND_COLUMN below is the column that excludes only dividends.
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    REALIZED_PROFIT_COLUMN='Realized_profit'
    # Profit still attributable to the position as it stands today - unrealized gain on units
    # still held plus dividends collected along the way - excluding gain/loss already locked in
    # by a sell (REALIZED_PROFIT_COLUMN). Mirrors PROFIT_WITHOUT_DIVIDEND_COLUMN's naming (Profit
    # minus one component) for the complementary exclusion.
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    # The literal complement of PROFIT_WITHOUT_DIVIDEND_COLUMN's name: excludes only dividends,
    # keeping both the unrealized component and REALIZED_PROFIT_COLUMN (Profit - Dividend).
    PROFIT_EXCLUDING_DIVIDEND_COLUMN='Profit_excluding_dividends'
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
    CSV_FEE_COLUMN='fee'
    CSV_SELL_TAX_COLUMN='sell_tax'
    CSV_DIVIDEND_COLUMN='dividend'
    CSV_DIVIDEND_TAX_COLUMN='dividend_tax'

    def __init__(self, directory_path: str, tickers_json: str, currency_to: str, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False):
        """tickers_json: path to the JSON file mapping each ISIN to {"ticker": <yfinance
        symbol>, "currency": <instrument currency>} - same shape/role as Commodity/Crypto's own
        tickers_json parameter, just required here (rather than optional) since Stock has no
        built-in ticker registry of its own to fall back on.
        progress_callback: optional zero-arg callback invoked once per ticker, right after that
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
        # Unlike total_money_invested (lifetime gross ever bought, never reduced by a sell), this
        # is what's still held today - the same figure PolishRetailBonds/BankAccount already
        # expose under their own total_money_invested (README Roadmap item). Kept as a separate
        # attribute rather than changing total_money_invested itself so existing callers relying
        # on the lifetime figure see no behavior change.
        self.total_money_currently_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_currently_invested=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Each ticker's own native currency (tickers.json's currency field) — always populated,
        # unlike native_data/native_currency below which are opt-in, since Portfolio needs this
        # for distribution_by_currency regardless of whether include_native_currency is set.
        self.currency_by_ticker=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.dataframe=self._load_sources(directory_path)
        self.tickers=self._load_tickers_json(tickers_json)
        self._cache_dir=cache_dir

        dataframes=self._split_by_ticker(self.dataframe)

        # A buy row's money invested depends on that ticker's own FX rate (see _compute_data),
        # so - same pattern Commodity._compute_data already uses - _compute_data itself returns
        # the lifetime buy total alongside its DataFrame, computed in the very same pass, rather
        # than a second iterrows() loop plus a second Currency(...) construction/fetch here
        # recomputing the identical figure a second time.
        dataframes_2=list()
        money_invested_by_ticker=dict()
        currently_invested_by_ticker=dict()
        current_value_by_ticker=dict()
        revenue_by_ticker=dict()
        for df in dataframes:
            ticker=df[self.CSV_TICKER_COLUMN].iloc[0]
            ticker_currency=self.ticker_currency(df, tickers_json, self.CSV_TICKER_COLUMN)
            self.currency_by_ticker[ticker]=ticker_currency
            computed, total_buy_invested=self._compute_data(df, ticker_currency, currency_to, cache_dir, force_refresh)
            dataframes_2.append(computed)

            money_invested_by_ticker[ticker]=total_buy_invested
            self.total_money_invested+=total_buy_invested

            # Cost basis of what's still held today - unlike total_buy_invested above, reduced by
            # any sell (see MONEY_INVESTED_COLUMN itself). 0 for a ticker that's been fully sold.
            currently_invested_by_ticker[ticker]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]
            self.total_money_currently_invested+=currently_invested_by_ticker[ticker]

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
                    self.native_data[ticker], _=self._compute_data(df, ticker_currency, ticker_currency, cache_dir, force_refresh)
                self.native_currency[ticker]=ticker_currency

            if progress_callback is not None:
                progress_callback()

        for ticker, value in money_invested_by_ticker.items():
            self.distribution_by_ticker[ticker]=(value/self.total_money_invested)*100.0 if self.total_money_invested else 0.0

        for ticker, value in currently_invested_by_ticker.items():
            self.distribution_by_ticker_currently_invested[ticker]=(value/self.total_money_currently_invested)*100.0 if self.total_money_currently_invested else 0.0

        for ticker, value in current_value_by_ticker.items():
            self.distribution_by_ticker_current_value[ticker]=(value/self.total_current_value)*100.0 if self.total_current_value else 0.0

        for ticker, value in revenue_by_ticker.items():
            self.distribution_by_ticker_revenue[ticker]=(value/self.total_revenue)*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    @staticmethod
    def ticker_currency(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> str:
        """Looks up a single ticker's declared currency from tickers_json. Only works if every
        row in dataframe shares the same ticker/isin - named ticker_currency, not
        get_ticker_currency, matching the rest of this library's public methods, none of which
        use a get_ prefix (merge, count_tickers, ...)."""
        with open(path_to_json_file, "r") as f:
            j=json.load(f)
            currency=j[dataframe[isin_column_name].iloc[0]]['currency']
        return currency

    @staticmethod
    def _load_tickers_json(tickers_json: str) -> dict:
        with open(tickers_json, 'r') as f:
            return json.load(f)

    @classmethod
    def _load_sources(cls, directory: str) -> pd.DataFrame:
        # A nonexistent directory would otherwise surface as a raw FileNotFoundError straight
        # from os.listdir ([WinError 3]/[Errno 2]) instead of this library's own established
        # clear-error convention, like the yfinance-empty-history ValueErrors already in place
        # (README Roadmap item).
        if not os.path.isdir(directory):
            raise ValueError(f"No such directory: {directory!r}")

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

    def _compute_data(self, dataframe: pd.DataFrame, currency_from: str, currency_to: str, cache_dir: str=None, force_refresh: bool=False) -> tuple:
        """Returns (computed_dataframe, total_buy_invested): total_buy_invested is the lifetime
        amount ever bought (only 'buy' rows, unreduced by later sells) - what __init__ needs for
        distribution_by_ticker/total_money_invested. Returned from here (same pattern
        Commodity._compute_data already uses) instead of recomputed independently in __init__
        via a second iterrows() pass and a second Currency(...) fetch for the same ticker/date
        range - this loop already computes it below while building the full DataFrame."""
        start_date=dataframe.index.min()
        ticker_name=self.tickers.get(dataframe[self.CSV_TICKER_COLUMN].iloc[0])['ticker']

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            # 'stock-v2': bumped from 'stock' when this method started returning a (dataframe,
            # total_buy_invested) tuple instead of a bare DataFrame - the isinstance check below
            # guards a cache entry from the older, bare-DataFrame format the same way
            # Commodity/PolishRetailBonds guard their own tuple cache formats.
            cache_key=DiskCache.make_key('stock-v2', ticker_name, currency_from, currency_to, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if isinstance(cached, tuple) and len(cached)==2:
                    return cached

        currency=Currency(currency_from, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        # yfinance returns an empty DataFrame (not an error) for an invalid/delisted ticker,
        # rather than raising - left unchecked, all_days.join(ticker_data) below would silently
        # produce a Close column of all-NaN that ffill() can't fill from anything, rather than
        # failing loudly at construction time (README Roadmap item).
        if ticker_data.empty:
            raise ValueError(f"yfinance returned no price history for ticker {ticker_name!r} (requested {start_date.date()} to today) - check it's a valid, still-listed ticker.")
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
        fee=column_or_nan(self.CSV_FEE_COLUMN)
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
        total_buy_invested=0.0

        for i in range(n):
            pos=position[i]
            row_state=state[i]
            row_fx=fx[i]

            if row_state=='buy':
                units=round(amount[i], 4)
                raw_money_invested=amount[i]*price[i]*row_fx
                money_invested=round((fee[i]+1.0)*raw_money_invested, 2)

                money_invested_by_day[pos]+=money_invested
                units_by_day[pos]+=units

                running_units+=units
                running_money_invested+=money_invested
                total_buy_invested+=money_invested
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
        data[self.PROFIT_WITHOUT_REALIZED_COLUMN]=round(data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.DIVIDEND_COLUMN], 2)
        data[self.PROFIT_EXCLUDING_DIVIDEND_COLUMN]=round(data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN], 2)
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)

        result=(data, total_buy_invested)
        if cache is not None:
            cache.set(cache_key, result)

        return result
