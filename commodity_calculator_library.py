"""
Commodity calculator, built the same way as Stock but scoped to physical commodities (gold,
silver, ...): buy/sell transactions turned into a daily investment/profit DataFrame. No
dividends, so no Dividend column - Profit is unrealized plus realized, the same two terms Stock
uses minus dividends.

Unlike Stock, buy.csv carries no price_of_unit - cost basis is the actual amount paid, recorded
directly via money_invested/currency (e.g. a coin dealer's price, which can diverge from the
futures price by more than a flat percentage), converted via that currency's own FX rate on the
row's own date - not derived from yfinance at all. buy.csv also carries amount_of_units plus a
unit column (CSV_UNIT_COLUMN, 'troy_ounce' or 'gram') converted via grams to whatever unit that
symbol's yfinance ticker actually quotes (see QUOTE_UNIT_GRAMS) - purely to size the held
position for marking to market, with no bearing on cost basis.

sell.csv is unchanged: no price of its own - proceeds are still that day's yfinance close, not a
recorded amount. Its amount_of_units is already in the native quote unit, no unit column needed.
"""

import os
import json
from datetime import datetime
from typing import Callable
import numpy as np
import pandas as pd
import yfinance as yf
from .currency_calculator_library import get_cached_currency
from .cache_library import DiskCache
from .calculator_mixins import ReprMixin, MergeMixin, TickerSplitMixin


class Commodity(TickerSplitMixin, MergeMixin, ReprMixin):
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    REALIZED_PROFIT_COLUMN='Realized_profit'
    # Unrealized gain on units still held, excluding REALIZED_PROFIT_COLUMN. Mirrors Stock's own
    # column (no Dividend term here, see module docstring).
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    # No dividends to exclude - always equals PROFIT_COLUMN.
    PROFIT_EXCLUDING_DIVIDEND_COLUMN='Profit_excluding_dividends'
    UNITS_COLUMN='Units'
    CLOSE_COLUMN='Close'

    # --- Ingestion tag, synthesized from the CSV filename, consumed like an input column. ---
    SOURCE_TYPE_COLUMN='state'

    # --- Input: columns read from the per-transaction CSVs. buy's cost basis is money_invested/
    # currency, recorded directly; sell still has no price of its own (yfinance close). ---
    CSV_TICKER_COLUMN='symbol'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_UNIT_COLUMN='unit'
    CSV_MONEY_INVESTED_COLUMN='money_invested'
    CSV_CURRENCY_COLUMN='currency'
    CSV_SELL_TAX_COLUMN='sell_tax'

    # Every yfinance futures ticker here is USD-quoted, so unlike Stock/Bonds there's no
    # per-symbol currency to look up - QUOTE_CURRENCY covers all of them. tickers_json can
    # add/override entries without editing this dict.
    TICKERS={
        'gold': 'GC=F',
        'silver': 'SI=F',
        'platinum': 'PL=F',
        'palladium': 'PA=F',
        'copper': 'HG=F',
    }
    QUOTE_CURRENCY='usd'

    # amount_of_units/unit is converted to grams, then to the symbol's own quote_unit_grams
    # entry, so a buy recorded in either supported unit lands on the same physical quantity.
    GRAMS_PER_TROY_OUNCE=31.1034768
    GRAMS_PER_POUND=453.59237
    UNIT_TO_GRAMS={'troy_ounce': GRAMS_PER_TROY_OUNCE, 'gram': 1.0}
    # The physical unit each symbol's yfinance futures ticker quotes a price per - troy ounce for
    # every metal here except copper (pound). A tickers_json entry for a new symbol must carry
    # its own quote_unit alongside its ticker - there's no way to infer it otherwise.
    QUOTE_UNIT_GRAMS={
        'gold': GRAMS_PER_TROY_OUNCE,
        'silver': GRAMS_PER_TROY_OUNCE,
        'platinum': GRAMS_PER_TROY_OUNCE,
        'palladium': GRAMS_PER_TROY_OUNCE,
        'copper': GRAMS_PER_POUND,
    }
    # Named units a tickers_json quote_unit can reference - a superset of UNIT_TO_GRAMS (only the
    # units a buy.csv row itself may specify; 'pound' is never recorded in a row directly).
    QUOTE_UNIT_NAME_TO_GRAMS={'troy_ounce': GRAMS_PER_TROY_OUNCE, 'pound': GRAMS_PER_POUND, 'gram': 1.0}

    def __init__(self, directory_path: str, currency_to: str, tickers_json: str=None, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False, currency_cache: dict=None):
        """directory_path: per-transaction-state CSVs (buy.csv, sell.csv, sell_tax.csv), state
        from filename. CSV_TICKER_COLUMN must be one of self.tickers's keys.
        buy.csv rows carry amount_of_units plus CSV_UNIT_COLUMN ('troy_ounce' or 'gram' - see
        UNIT_TO_GRAMS, sizes the held position only) and CSV_MONEY_INVESTED_COLUMN/
        CSV_CURRENCY_COLUMN - the amount paid and its currency, converted to currency_to via
        that currency's own FX rate on the row's date. sell.csv carries no price of its own:
        proceeds are always that day's yfinance close, just amount_of_units (native quote unit).
        currency_to: target currency every instrument is converted to (from QUOTE_CURRENCY).
        tickers_json: optional {symbol: {"ticker": <yfinance futures ticker>, "quote_unit":
        <QUOTE_UNIT_NAME_TO_GRAMS key>}} merged on top of the built-in TICKERS/QUOTE_UNIT_GRAMS,
        so a caller can track another commodity without editing this module. None: self.tickers/
        self.quote_unit_grams are exactly TICKERS/QUOTE_UNIT_GRAMS.
        progress_callback: optional zero-arg callback, once per symbol, after its price history
        is fetched/computed.
        cache_dir: caches each symbol's computed DataFrame, keyed by symbol/currency/
        transactions, valid for the day written. Also passed to every Currency this constructs.
        force_refresh: ignores any cached entry, recomputes, overwrites the cache.
        include_native_currency: also computes each symbol's DataFrame in QUOTE_CURRENCY
        (self.native_data/native_currency), isolating it from FX movement against currency_to.
        Free when currency_to is already QUOTE_CURRENCY; otherwise a second fetch.
        currency_cache: optional dict shared across sources (Portfolio passes one automatically)
        so a shared currency pair is fetched once instead of once per symbol - see
        get_cached_currency. None: every symbol fetches its own."""
        self.tickers, self.quote_unit_grams=self._load_tickers(tickers_json)
        self.total_money_invested=0.0
        # Unlike total_money_invested (lifetime gross, never reduced by a sell), this is what's
        # still held today - a separate, independent computation.
        self.total_money_currently_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_currently_invested=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Trivial (every symbol quotes in QUOTE_CURRENCY, unlike Stock's per-ticker currency) -
        # kept as a dict anyway so Portfolio's distribution_by_currency has one uniform shape.
        self.currency_by_ticker=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.dataframe=self._load_sources(directory_path)

        dataframes=self._split_by_ticker(self.dataframe)

        # A buy row's own FX conversion (see _compute_data) means there's no cheap way to
        # recompute distribution_by_ticker's lifetime-invested figure separately, so
        # _compute_data returns that lifetime total alongside its DataFrame instead.
        dataframes_2=list()
        money_invested_by_symbol=dict()
        currently_invested_by_symbol=dict()
        current_value_by_symbol=dict()
        revenue_by_symbol=dict()
        for df in dataframes:
            symbol=df[self.CSV_TICKER_COLUMN].iloc[0]
            self.currency_by_ticker[symbol]=self.QUOTE_CURRENCY
            computed, total_buy_invested=self._compute_data(df, currency_to, cache_dir, force_refresh, currency_cache)
            dataframes_2.append(computed)

            money_invested_by_symbol[symbol]=total_buy_invested
            self.total_money_invested+=total_buy_invested

            # Cost basis still held today - unlike total_buy_invested, reduced by any sell. 0 for
            # a fully-sold symbol.
            currently_invested_by_symbol[symbol]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]
            self.total_money_currently_invested+=currently_invested_by_symbol[symbol]

            # Cost basis still held plus its unrealized gain.
            current_value_by_symbol[symbol]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_symbol[symbol]

            # All-time gain (unrealized + realized), can be negative.
            revenue_by_symbol[symbol]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_symbol[symbol]

            if include_native_currency:
                if self.QUOTE_CURRENCY.upper()==currency_to.upper():
                    self.native_data[symbol]=computed
                else:
                    self.native_data[symbol], _=self._compute_data(df, self.QUOTE_CURRENCY, cache_dir, force_refresh, currency_cache)
                self.native_currency[symbol]=self.QUOTE_CURRENCY

            if progress_callback is not None:
                progress_callback()

        for symbol, value in money_invested_by_symbol.items():
            self.distribution_by_ticker[symbol]=(value/self.total_money_invested)*100.0 if self.total_money_invested else 0.0

        for symbol, value in currently_invested_by_symbol.items():
            self.distribution_by_ticker_currently_invested[symbol]=(value/self.total_money_currently_invested)*100.0 if self.total_money_currently_invested else 0.0

        for symbol, value in current_value_by_symbol.items():
            self.distribution_by_ticker_current_value[symbol]=(value/self.total_current_value)*100.0 if self.total_current_value else 0.0

        for symbol, value in revenue_by_symbol.items():
            self.distribution_by_ticker_revenue[symbol]=(value/self.total_revenue)*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    @classmethod
    def _load_tickers(cls, tickers_json: str=None) -> tuple:
        """(tickers, quote_unit_grams): TICKERS/QUOTE_UNIT_GRAMS merged with tickers_json - see
        __init__'s docstring."""
        tickers=dict(cls.TICKERS)
        quote_unit_grams=dict(cls.QUOTE_UNIT_GRAMS)
        if tickers_json is not None:
            with open(tickers_json, 'r') as f:
                custom=json.load(f)
            for symbol, config in custom.items():
                tickers[symbol]=config['ticker']
                quote_unit_grams[symbol]=cls.QUOTE_UNIT_NAME_TO_GRAMS[config['quote_unit']]
        return tickers, quote_unit_grams

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

    def _compute_data(self, dataframe: pd.DataFrame, currency_to: str, cache_dir: str=None, force_refresh: bool=False, currency_cache: dict=None) -> tuple:
        """Returns (computed_dataframe, total_buy_invested): total_buy_invested is the lifetime
        amount ever bought ('buy' rows only, unreduced by sells) - what __init__ needs for
        distribution_by_ticker/total_money_invested. Returned here rather than recomputed in
        __init__, since converting each row's currency needs this method's own Currency lookups
        below."""
        start_date=dataframe.index.min()
        symbol=dataframe[self.CSV_TICKER_COLUMN].iloc[0]
        ticker_name=self.tickers[symbol]

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            # Version tag - the isinstance check below guards a cache entry from an older,
            # differently-shaped format.
            cache_key=DiskCache.make_key('commodity-v4', ticker_name, currency_to, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if isinstance(cached, tuple) and len(cached)==2:
                    return cached

        currency=get_cached_currency(currency_cache, self.QUOTE_CURRENCY, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        # yfinance returns an empty DataFrame, not an error, for an invalid futures ticker -
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
        # built by every preceding buy, so it's inherently sequential (see Stock, same pattern).
        # Vectorized otherwise: columns/FX pulled out as numpy arrays up front, accumulated by
        # integer position instead of .iterrows()/.loc[label] per row.
        sorted_df=dataframe.sort_index(kind='stable')
        n=len(sorted_df)

        def column_or_nan(column: str) -> np.ndarray:
            if column in sorted_df.columns:
                return sorted_df[column].to_numpy(dtype=float)
            return np.full(n, np.nan)

        def column_or_none(column: str) -> np.ndarray:
            if column in sorted_df.columns:
                return sorted_df[column].to_numpy(dtype=object)
            return np.full(n, None, dtype=object)

        state=sorted_df[self.SOURCE_TYPE_COLUMN].to_numpy()
        amount=column_or_nan(self.CSV_AMOUNT_OF_UNITS_COLUMN)
        unit=column_or_none(self.CSV_UNIT_COLUMN)
        money_invested_native=column_or_nan(self.CSV_MONEY_INVESTED_COLUMN)
        row_currency=column_or_none(self.CSV_CURRENCY_COLUMN)
        sell_tax=column_or_nan(self.CSV_SELL_TAX_COLUMN)
        fx=currency.data.loc[sorted_df.index, self.CLOSE_COLUMN].to_numpy(dtype=float)

        position=data.index.get_indexer(sorted_df.index)
        if (position<0).any():
            missing=sorted_df.index[position<0]
            raise KeyError(f"Transaction date(s) {list(missing)} for {ticker_name} fall outside the computed daily range.")

        # Every sell's own market price - the already currency_to-converted Close series, looked
        # up by position (no separate FX step needed, unlike sell_tax below). A buy's cost basis
        # doesn't come from this at all - see money_invested_native/row_currency below.
        market_price=data[self.CLOSE_COLUMN].to_numpy()[position]

        money_invested_by_day=np.zeros(len(data))
        units_by_day=np.zeros(len(data))
        realized_profit_by_day=np.zeros(len(data))

        running_units=0.0
        running_money_invested=0.0
        total_buy_invested=0.0
        # Memoizes each currency code seen in a buy row to its Currency object (routed through
        # get_cached_currency, so a pair used elsewhere is still fetched once) - a buy row's own
        # currency can differ from QUOTE_CURRENCY or from another row's (e.g. eur vs usd).
        buy_currency_objects=dict()

        for i in range(n):
            pos=position[i]
            row_state=state[i]
            row_fx=fx[i]

            if row_state=='buy':
                row_unit=unit[i]
                if row_unit not in self.UNIT_TO_GRAMS:
                    raise ValueError(f"Unknown unit {row_unit!r} for {ticker_name} buy on {sorted_df.index[i].date()} (expected one of {sorted(self.UNIT_TO_GRAMS)}).")
                grams=amount[i]*self.UNIT_TO_GRAMS[row_unit]
                units=round(grams/self.quote_unit_grams[symbol], 4)

                buy_currency=row_currency[i]
                if buy_currency is None:
                    raise ValueError(f"Missing {self.CSV_CURRENCY_COLUMN!r} for {ticker_name} buy on {sorted_df.index[i].date()}.")
                if buy_currency not in buy_currency_objects:
                    buy_currency_objects[buy_currency]=get_cached_currency(currency_cache, buy_currency, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)
                buy_fx=buy_currency_objects[buy_currency].data.loc[sorted_df.index[i], self.CLOSE_COLUMN]
                money_invested=round(money_invested_native[i]*buy_fx, 2)

                money_invested_by_day[pos]+=money_invested
                units_by_day[pos]+=units

                running_units+=units
                running_money_invested+=money_invested
                total_buy_invested+=money_invested
            elif row_state=='sell':
                units_sold=round(amount[i], 4)
                if units_sold>running_units+1e-9:
                    raise ValueError(f"Cannot sell {units_sold} units of {ticker_name} on {sorted_df.index[i].date()}: only {running_units} units held.")

                # Remove cost basis proportional to units sold, so the remaining position's
                # average price is unchanged by a partial sell.
                fraction_sold=units_sold/running_units if running_units>1e-9 else 0.0
                money_invested_removed=round(running_money_invested*fraction_sold, 2)

                proceeds=amount[i]*market_price[i]

                units_by_day[pos]-=units_sold
                money_invested_by_day[pos]-=money_invested_removed
                realized_profit_by_day[pos]+=round(proceeds-money_invested_removed, 2)

                running_units-=units_sold
                running_money_invested-=money_invested_removed
            elif row_state=='sell_tax':
                realized_profit_by_day[pos]-=round(sell_tax[i]*row_fx, 2)

        data[self.MONEY_INVESTED_COLUMN]=money_invested_by_day
        data[self.UNITS_COLUMN]=units_by_day
        data[self.REALIZED_PROFIT_COLUMN]=realized_profit_by_day

        data[self.MONEY_INVESTED_COLUMN]=data[self.MONEY_INVESTED_COLUMN].cumsum()
        data[self.UNITS_COLUMN]=data[self.UNITS_COLUMN].cumsum()
        data[self.REALIZED_PROFIT_COLUMN]=data[self.REALIZED_PROFIT_COLUMN].cumsum()

        # Unrealized profit = current market value of the held units minus their cost basis.
        data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=round(data[self.CLOSE_COLUMN]*data[self.UNITS_COLUMN]-data[self.MONEY_INVESTED_COLUMN], 2)
        data[self.PROFIT_COLUMN]=round(data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]+data[self.REALIZED_PROFIT_COLUMN], 2)
        # No Dividend column to add back - equal to the unrealized component alone.
        data[self.PROFIT_WITHOUT_REALIZED_COLUMN]=data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]
        # No dividends to exclude either - equal to total Profit.
        data[self.PROFIT_EXCLUDING_DIVIDEND_COLUMN]=data[self.PROFIT_COLUMN]
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)

        result=(data, total_buy_invested)
        if cache is not None:
            cache.set(cache_key, result)

        return result
