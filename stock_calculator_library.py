"""
Stock/ETF calculator: given buy/sell/dividend transactions plus a tickers.json mapping each ISIN
to its yfinance ticker and native currency, computes the Money_invested/Profit/Dividend
DataFrame at construction, caching the result on self.data. Every other calculator in this
package follows the same shape.
"""

import os
import json
from datetime import datetime
from decimal import Decimal
from typing import Callable
import numpy as np
import pandas as pd
import yfinance as yf
from .currency_calculator_library import get_cached_currency
from .cache_library import DiskCache
from .calculator_mixins import ReprMixin, MergeMixin, TickerSplitMixin, to_money, money_array


class Stock(TickerSplitMixin, MergeMixin, ReprMixin):
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    # Despite the name, *unrealized* only - market value of units held minus cost basis, so it
    # excludes REALIZED_PROFIT_COLUMN too, not just DIVIDEND_COLUMN. Load-bearing: total_current_
    # value/current_value_by_ticker add this to MONEY_INVESTED_COLUMN, correct only because
    # realized profit (cash off the table already) stays excluded.
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    DIVIDEND_COLUMN='Dividend'
    REALIZED_PROFIT_COLUMN='Realized_profit'
    # Unrealized gain on units held plus dividends collected, excluding REALIZED_PROFIT_COLUMN.
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    # Excludes only dividends, keeping the unrealized component and REALIZED_PROFIT_COLUMN.
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

    def __init__(self, directory_path: str, tickers_json: str, currency_to: str, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False, currency_cache: dict=None):
        """tickers_json: JSON file mapping each ISIN to {"ticker": <yfinance symbol>,
        "currency": <instrument currency>} - same shape as Commodity/Crypto's own, required here
        since Stock has no built-in ticker registry to fall back on.
        progress_callback: optional zero-arg callback, once per ticker, after its price history
        is fetched/computed.
        cache_dir: caches each ticker's computed DataFrame, keyed by ticker/currency/
        transactions, valid for the day written. Also passed to every Currency this constructs.
        force_refresh: ignores any cached entry, recomputes, overwrites the cache.
        include_native_currency: also computes each ticker's DataFrame in its own native
        currency (self.native_data/native_currency), isolating it from FX movement against
        currency_to. Off by default: a second fetch for a foreign-currency ticker, free for one
        already in currency_to (the computed DataFrame is reused).
        currency_cache: optional dict shared across sources (Portfolio passes one automatically)
        so a shared currency pair is fetched once instead of once per ticker - see
        get_cached_currency. None: every ticker fetches its own."""
        self.total_money_invested=Decimal('0')
        # Unlike total_money_invested (lifetime gross, never reduced by a sell), this is what's
        # still held today - a separate, independent computation.
        self.total_money_currently_invested=Decimal('0')
        self.total_current_value=Decimal('0')
        self.total_revenue=Decimal('0')
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_currently_invested=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Each ticker's native currency - always populated (unlike the opt-in native_data/
        # native_currency below), since Portfolio needs it for distribution_by_currency
        # regardless of include_native_currency.
        self.currency_by_ticker=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.dataframe=self._load_sources(directory_path)
        self.tickers=self._load_tickers_json(tickers_json)
        self._cache_dir=cache_dir

        dataframes=self._split_by_ticker(self.dataframe)

        # _compute_data returns the lifetime buy total alongside its DataFrame (same pattern as
        # Commodity) rather than recomputing it here via a second pass/FX fetch.
        dataframes_2=list()
        money_invested_by_ticker=dict()
        currently_invested_by_ticker=dict()
        current_value_by_ticker=dict()
        revenue_by_ticker=dict()
        for df in dataframes:
            ticker=df[self.CSV_TICKER_COLUMN].iloc[0]
            ticker_currency=self.ticker_currency(df, tickers_json, self.CSV_TICKER_COLUMN)
            self.currency_by_ticker[ticker]=ticker_currency
            computed, total_buy_invested=self._compute_data(df, ticker_currency, currency_to, cache_dir, force_refresh, currency_cache)
            dataframes_2.append(computed)

            money_invested_by_ticker[ticker]=total_buy_invested
            self.total_money_invested+=total_buy_invested

            # Cost basis still held today - unlike total_buy_invested, reduced by any sell. 0 for
            # a fully-sold ticker.
            currently_invested_by_ticker[ticker]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]
            self.total_money_currently_invested+=currently_invested_by_ticker[ticker]

            # Cost basis still held plus its unrealized gain.
            current_value_by_ticker[ticker]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_ticker[ticker]

            # All-time gain (unrealized + dividends + realized), can be negative.
            revenue_by_ticker[ticker]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_ticker[ticker]

            if include_native_currency:
                # Already the same currency -> computed already is the native-currency DataFrame.
                if ticker_currency.upper()==currency_to.upper():
                    self.native_data[ticker]=computed
                else:
                    self.native_data[ticker], _=self._compute_data(df, ticker_currency, ticker_currency, cache_dir, force_refresh, currency_cache)
                self.native_currency[ticker]=ticker_currency

            if progress_callback is not None:
                progress_callback()

        # Distribution percentages are ratios, not money - computed in float even though value/
        # total are Decimal.
        for ticker, value in money_invested_by_ticker.items():
            self.distribution_by_ticker[ticker]=(float(value)/float(self.total_money_invested))*100.0 if self.total_money_invested else 0.0

        for ticker, value in currently_invested_by_ticker.items():
            self.distribution_by_ticker_currently_invested[ticker]=(float(value)/float(self.total_money_currently_invested))*100.0 if self.total_money_currently_invested else 0.0

        for ticker, value in current_value_by_ticker.items():
            self.distribution_by_ticker_current_value[ticker]=(float(value)/float(self.total_current_value))*100.0 if self.total_current_value else 0.0

        for ticker, value in revenue_by_ticker.items():
            self.distribution_by_ticker_revenue[ticker]=(float(value)/float(self.total_revenue))*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    @staticmethod
    def ticker_currency(dataframe: pd.DataFrame, path_to_json_file: str, isin_column_name: str) -> str:
        """Looks up one ticker's declared currency from tickers_json. Requires every row in
        dataframe to share the same ticker/isin."""
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
        # A nonexistent directory would otherwise raise a raw FileNotFoundError from os.listdir
        # instead of this library's own clear-error convention.
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

    def _compute_data(self, dataframe: pd.DataFrame, currency_from: str, currency_to: str, cache_dir: str=None, force_refresh: bool=False, currency_cache: dict=None) -> tuple:
        """Returns (computed_dataframe, total_buy_invested): total_buy_invested is the lifetime
        amount ever bought ('buy' rows only, unreduced by sells) - what __init__ needs for
        distribution_by_ticker/total_money_invested, computed here (same pattern as Commodity)
        rather than in a second pass over __init__."""
        start_date=dataframe.index.min()
        ticker_name=self.tickers.get(dataframe[self.CSV_TICKER_COLUMN].iloc[0])['ticker']

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            # Version tag - the isinstance check below guards a cache entry from an older,
            # differently-shaped format.
            cache_key=DiskCache.make_key('stock-v2', ticker_name, currency_from, currency_to, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if isinstance(cached, tuple) and len(cached)==2:
                    return cached

        currency=get_cached_currency(currency_cache, currency_from, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        # yfinance returns an empty DataFrame, not an error, for an invalid/delisted ticker -
        # unchecked, all_days.join(ticker_data) below would silently produce an all-NaN column.
        if ticker_data.empty:
            raise ValueError(f"yfinance returned no price history for ticker {ticker_name!r} (requested {start_date.date()} to today) - check it's a valid, still-listed ticker.")
        ticker_data.drop(columns=['High', 'Low', 'Open', 'Volume', 'Repaired?'], inplace=True)
        ticker_data.index=ticker_data.index.tz_localize(None).normalize()

        all_days=pd.DataFrame(
            index=pd.date_range(start=start_date, end=datetime.today(), freq='D').tz_localize(None).normalize()
        )
        data=all_days.join(ticker_data).ffill()
        data[self.CLOSE_COLUMN]=data[self.CLOSE_COLUMN]*currency.data[self.CLOSE_COLUMN]

        # Walked in date order, not file order - a sell needs the running average cost basis
        # built by every preceding buy, so it's inherently sequential. Vectorized otherwise:
        # columns/FX pulled out as numpy arrays up front, accumulated by integer position
        # instead of .iterrows()/.loc[label] per row (both far more expensive than an array
        # index). Arithmetic, rounding, and iteration order unchanged.
        sorted_df=dataframe.sort_index(kind='stable')
        n=len(sorted_df)

        def column_or_nan(column: str) -> np.ndarray:
            # A source directory needn't carry every CSV (e.g. just buy.csv) - a missing
            # column is only ever read under its own state branch below, so it's never touched.
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
        # One vectorized FX lookup for every transaction, instead of one .loc[idx, ...] per row.
        fx=currency.data.loc[sorted_df.index, self.CLOSE_COLUMN].to_numpy(dtype=float)

        # Each transaction's integer position in data's daily index, so the loop below writes
        # by position instead of by date label.
        position=data.index.get_indexer(sorted_df.index)
        if (position<0).any():
            missing=sorted_df.index[position<0]
            raise KeyError(f"Transaction date(s) {list(missing)} for {ticker_name} fall outside the computed daily range.")

        # Money_invested/Dividend/Realized_profit accumulate as Decimal (object dtype) - each
        # transaction's own float computation is rounded to cents and cast via to_money() right
        # before landing in these arrays, so the cumsum() below (and every later sum across
        # sources) adds exact Decimals instead of compounding binary-float rounding error. Units
        # stays float - a quantity, not money.
        money_invested_by_day=np.full(len(data), Decimal('0'), dtype=object)
        units_by_day=np.zeros(len(data))
        dividend_by_day=np.full(len(data), Decimal('0'), dtype=object)
        realized_profit_by_day=np.full(len(data), Decimal('0'), dtype=object)

        running_units=0.0
        running_money_invested=0.0
        total_buy_invested=Decimal('0')

        for i in range(n):
            pos=position[i]
            row_state=state[i]
            row_fx=fx[i]

            if row_state=='buy':
                units=round(amount[i], 4)
                raw_money_invested=amount[i]*price[i]*row_fx
                money_invested=round((fee[i]+1.0)*raw_money_invested, 2)
                money_invested_decimal=to_money(money_invested)

                money_invested_by_day[pos]+=money_invested_decimal
                units_by_day[pos]+=units

                running_units+=units
                running_money_invested+=money_invested
                total_buy_invested+=money_invested_decimal
            elif row_state=='sell':
                units_sold=round(amount[i], 4)
                if units_sold>running_units+1e-9:
                    raise ValueError(f"Cannot sell {units_sold} units of {ticker_name} on {sorted_df.index[i].date()}: only {running_units} units held.")

                # Remove cost basis proportional to units sold, so the remaining position's
                # average price is unchanged by a partial sell.
                fraction_sold=units_sold/running_units if running_units>1e-9 else 0.0
                money_invested_removed=round(running_money_invested*fraction_sold, 2)

                proceeds=amount[i]*price[i]*row_fx

                units_by_day[pos]-=units_sold
                money_invested_by_day[pos]-=to_money(money_invested_removed)
                realized_profit_by_day[pos]+=to_money(round(proceeds-money_invested_removed, 2))

                running_units-=units_sold
                running_money_invested-=money_invested_removed
            elif row_state=='sell_tax':
                realized_profit_by_day[pos]-=to_money(round(sell_tax[i]*row_fx, 2))
            elif row_state=='dividend':
                dividend_by_day[pos]+=to_money(round(dividend[i]*row_fx, 2))
            elif row_state=='dividend_tax':
                dividend_by_day[pos]-=to_money(round(dividend_tax[i]*row_fx, 2))

        data[self.MONEY_INVESTED_COLUMN]=money_invested_by_day
        data[self.UNITS_COLUMN]=units_by_day
        data[self.DIVIDEND_COLUMN]=dividend_by_day
        data[self.REALIZED_PROFIT_COLUMN]=realized_profit_by_day

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        data[self.UNITS_COLUMN]=data[self.UNITS_COLUMN].cumsum()
        data[self.DIVIDEND_COLUMN]=data[self.DIVIDEND_COLUMN].cumsum()
        data[self.REALIZED_PROFIT_COLUMN]=data[self.REALIZED_PROFIT_COLUMN].cumsum()

        # Unrealized profit = current market value of the held units (still float - Close/Units
        # are price/quantity, not money) minus their cost basis (Decimal) - the market value is
        # cast to Decimal here, at the point it becomes a stored money figure. Every sum below is
        # then exact Decimal+Decimal, no further rounding needed.
        market_value=money_array(data[self.CLOSE_COLUMN]*data[self.UNITS_COLUMN])
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=market_value-data[self.MONEY_INVESTED_COLUMN]
        data[self.PROFIT_COLUMN]=data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN]
        data[self.PROFIT_WITHOUT_REALIZED_COLUMN]=data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.DIVIDEND_COLUMN]
        data[self.PROFIT_EXCLUDING_DIVIDEND_COLUMN]=data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN]
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)

        result=(data, total_buy_invested)
        if cache is not None:
            cache.set(cache_key, result)

        return result
