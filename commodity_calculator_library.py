"""
Class-based commodity calculator, built the same way as stock_calculator_library.Stock but
scoped to physical commodities (gold, silver, ...) instead of stocks/ETFs: a set of buy/sell
transactions turned into a daily investment/profit DataFrame. Commodities don't pay dividends,
so there's no Dividend column — Profit is simply unrealized plus realized profit, the same two
terms Stock uses minus the dividend one.

Unlike Stock, neither buy.csv nor sell.csv carries a price_of_unit column — every transaction's
market price is always the yfinance close price on its own transaction date (the same series
later used to mark the held position to market), not a price the user records by hand. Instead,
buy.csv carries amount_of_units alongside a unit column (CSV_UNIT_COLUMN) naming the physical
unit that amount is denominated in ('troy_ounce' or 'gram', see UNIT_TO_GRAMS) - the amount is
converted internally, via grams, to whatever unit that symbol's own yfinance ticker actually
quotes (troy ounce for gold/silver/platinum/palladium, pound for copper - see QUOTE_UNIT_GRAMS),
so a buy recorded in either unit lands on the same cost basis. sell.csv's amount_of_units is
already in that same native quote unit (it's selling off units tracked that way internally), so
it needs no unit column/conversion of its own.
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


class Commodity:
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    REALIZED_PROFIT_COLUMN='Realized_profit'
    UNITS_COLUMN='Units'
    CLOSE_COLUMN='Close'

    # --- Ingestion tag: synthesized while loading (from the CSV filename), not read from
    # inside a CSV cell — but consumed everywhere exactly like an input column. ---
    SOURCE_TYPE_COLUMN='state'

    # --- Input: columns read from the per-transaction CSVs (buy.csv, sell.csv, ...). Neither
    # buy nor sell carries a price - both are priced off yfinance's own close price on that
    # transaction's date (see module docstring). ---
    CSV_TICKER_COLUMN='symbol'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_UNIT_COLUMN='unit'
    CSV_PREMIUM_COLUMN='premium'
    CSV_SELL_TAX_COLUMN='sell_tax'

    # Every one of these yfinance futures tickers is USD-quoted, so unlike Stock/Bonds there's
    # no per-symbol currency to look up — QUOTE_CURRENCY below covers all of them. Built-in
    # defaults, always available with no configuration - tickers_json (below) can add to or
    # override these without editing this dict (README Roadmap item).
    TICKERS={
        'gold': 'GC=F',
        'silver': 'SI=F',
        'platinum': 'PL=F',
        'palladium': 'PA=F',
        'copper': 'HG=F',
    }
    QUOTE_CURRENCY='usd'

    # A buy.csv row's amount_of_units/unit is converted to grams, then to the symbol's own
    # quote_unit_grams entry, so a buy recorded in either supported unit lands on the same
    # physical quantity (and so the same cost basis) regardless of which one was used.
    GRAMS_PER_TROY_OUNCE=31.1034768
    GRAMS_PER_POUND=453.59237
    UNIT_TO_GRAMS={'troy_ounce': GRAMS_PER_TROY_OUNCE, 'gram': 1.0}
    # The physical unit each symbol's own yfinance futures ticker actually quotes a price per -
    # troy ounce for every metal here except copper, which yfinance quotes per pound. Built-in
    # defaults matching TICKERS above - a tickers_json entry for a new symbol must carry its own
    # quote_unit (one of QUOTE_UNIT_NAME_TO_GRAMS's keys) alongside its ticker, since there's no
    # way to infer what unit an arbitrary new yfinance ticker quotes a price per.
    QUOTE_UNIT_GRAMS={
        'gold': GRAMS_PER_TROY_OUNCE,
        'silver': GRAMS_PER_TROY_OUNCE,
        'platinum': GRAMS_PER_TROY_OUNCE,
        'palladium': GRAMS_PER_TROY_OUNCE,
        'copper': GRAMS_PER_POUND,
    }
    # Named units a tickers_json entry's own quote_unit can reference - a superset of
    # UNIT_TO_GRAMS (which is only the units a buy.csv row itself may specify), since a quoted
    # unit like 'pound' (copper) is never something a buy.csv row is recorded in directly.
    QUOTE_UNIT_NAME_TO_GRAMS={'troy_ounce': GRAMS_PER_TROY_OUNCE, 'pound': GRAMS_PER_POUND, 'gram': 1.0}

    def __init__(self, directory_path: str, currency_to: str, tickers_json: str=None, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False):
        """directory_path: a directory of per-transaction-state CSVs (buy.csv, sell.csv,
        sell_tax.csv), state inferred from filename, one row per transaction. Each row's
        CSV_TICKER_COLUMN value must be one of self.tickers's keys (e.g. 'gold', 'silver' from
        the built-in TICKERS, or a custom one added via tickers_json).
        Neither buy.csv nor sell.csv carries a price_of_unit: every transaction's market price is
        always that day's own yfinance close, not a manually recorded one. buy.csv rows carry
        amount_of_units alongside CSV_UNIT_COLUMN ('troy_ounce' or 'gram' - see UNIT_TO_GRAMS)
        and premium; sell.csv rows carry just amount_of_units (already in that symbol's own
        native quote unit, same as the units tracked internally - see module docstring).
        currency_to: target currency every instrument is converted to (from QUOTE_CURRENCY).
        tickers_json: optional path to a JSON file of {symbol: {"ticker": <yfinance futures
        ticker>, "quote_unit": <one of QUOTE_UNIT_NAME_TO_GRAMS's keys>}} - e.g. {"tin":
        {"ticker": "TIN=F", "quote_unit": "pound"}} - merged on top of the built-in
        TICKERS/QUOTE_UNIT_GRAMS (an entry here for an existing symbol overrides the built-in
        one), so a caller can track another commodity without editing this module's source.
        None (default): self.tickers/self.quote_unit_grams are exactly TICKERS/QUOTE_UNIT_GRAMS.
        progress_callback: optional zero-arg callback invoked once per symbol, right after that
        symbol's price history has been fetched and computed — the unit of work a caller (e.g.
        Portfolio) would want to track progress by, since that fetch is what actually takes
        time.
        cache_dir: optional directory to cache each symbol's computed DataFrame in, keyed by
        symbol/currency/transactions and valid for the day it was written — see
        cache_library.DiskCache. Also passed down to every Currency this Commodity constructs.
        force_refresh: when True (and cache_dir is set), ignores any cached entry and
        recomputes/re-fetches everything, then overwrites the cache with the fresh result.
        include_native_currency: when True, also computes each symbol's DataFrame in
        QUOTE_CURRENCY (self.native_data[symbol], self.native_currency[symbol]) alongside the
        currency_to-converted one in self.data — isolates that symbol's own performance from
        FX movement against currency_to. Free when currency_to is already QUOTE_CURRENCY (the
        already-computed DataFrame is reused); otherwise a second fetch/computation."""
        self.tickers, self.quote_unit_grams=self._load_tickers(tickers_json)
        self.total_money_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Every symbol here quotes in QUOTE_CURRENCY, so this is trivial (unlike Stock's, which
        # varies per ticker) — kept as a per-symbol dict anyway so Portfolio's
        # distribution_by_currency aggregation has one uniform shape to read across every source.
        self.currency_by_ticker=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.dataframe=self._load_sources(directory_path)

        dataframes=self._split_by_symbol(self.dataframe)

        # A buy row's money invested depends on _compute_data's own yfinance fetch (see its
        # docstring) rather than a price the CSV carries directly, so unlike Stock/Crypto there's
        # no cheap way to recompute distribution_by_ticker's lifetime-invested figure in a
        # separate, lightweight pass over the raw CSV rows first - _compute_data is called once
        # per symbol below and returns that lifetime total alongside its DataFrame instead.
        dataframes_2=list()
        money_invested_by_symbol=dict()
        current_value_by_symbol=dict()
        revenue_by_symbol=dict()
        for df in dataframes:
            symbol=df[self.CSV_TICKER_COLUMN].iloc[0]
            self.currency_by_ticker[symbol]=self.QUOTE_CURRENCY
            computed, total_buy_invested=self._compute_data(df, currency_to, cache_dir, force_refresh)
            dataframes_2.append(computed)

            money_invested_by_symbol[symbol]=total_buy_invested
            self.total_money_invested+=total_buy_invested

            # Current market value of the position: cost basis still held plus its unrealized gain.
            current_value_by_symbol[symbol]=computed[self.MONEY_INVESTED_COLUMN].iloc[-1]+computed[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            self.total_current_value+=current_value_by_symbol[symbol]

            # Revenue: this symbol's all-time gain (unrealized + realized), which can be negative.
            revenue_by_symbol[symbol]=computed[self.PROFIT_COLUMN].iloc[-1]
            self.total_revenue+=revenue_by_symbol[symbol]

            if include_native_currency:
                if self.QUOTE_CURRENCY.upper()==currency_to.upper():
                    self.native_data[symbol]=computed
                else:
                    self.native_data[symbol], _=self._compute_data(df, self.QUOTE_CURRENCY, cache_dir, force_refresh)
                self.native_currency[symbol]=self.QUOTE_CURRENCY

            if progress_callback is not None:
                progress_callback()

        for symbol, value in money_invested_by_symbol.items():
            self.distribution_by_ticker[symbol]=(value/self.total_money_invested)*100.0 if self.total_money_invested else 0.0

        for symbol, value in current_value_by_symbol.items():
            self.distribution_by_ticker_current_value[symbol]=(value/self.total_current_value)*100.0 if self.total_current_value else 0.0

        for symbol, value in revenue_by_symbol.items():
            self.distribution_by_ticker_revenue[symbol]=(value/self.total_revenue)*100.0 if self.total_revenue else 0.0

        self.data=self.merge(dataframes_2)

    def __repr__(self) -> str:
        """A quick invested/current-value/revenue summary (README Roadmap item) - so printing a
        Commodity in a REPL/notebook shows something useful instead of the default
        <...object at 0x...>."""
        return f"{self.__class__.__name__}(invested={self.total_money_invested:.2f}, current_value={self.total_current_value:.2f}, revenue={self.total_revenue:.2f})"

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into a single aggregate DataFrame.
        Equivalent of Stock.merge."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

    @classmethod
    def _split_by_symbol(cls, dataframe: pd.DataFrame) -> list:
        """Splits the raw multi-symbol dataframe into one per-symbol DataFrame each, indexed by
        parsed transaction date - a single groupby pass rather than iterrows()+pd.concat once
        per row, which is O(n^2) in transaction count (each concat copies the whole growing
        per-symbol frame so far) and, via the row-Series/transpose round trip, tends to coerce
        every column to dtype=object instead of keeping each column's own read_csv dtype.
        set_index/drop both return new frames rather than mutating dataframe in place, so the
        caller's own copy (self.dataframe) is left untouched, same as before this rewrite."""
        dates=pd.to_datetime(dataframe[cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
        indexed=dataframe.set_index(dates).drop(columns=[cls.CSV_DATE_COLUMN])
        # sort=False preserves each symbol's first-appearance order, matching the old dict's
        # insertion order - callers don't rely on this, but it keeps behavior identical anyway.
        return [group for _, group in indexed.groupby(cls.CSV_TICKER_COLUMN, sort=False)]

    @classmethod
    def count_tickers(cls, directory_path: str) -> int:
        """Number of distinct symbols in a source directory, without fetching any price data —
        lets a caller (e.g. Portfolio) size a progress bar before construction."""
        return cls._load_sources(directory_path)[cls.CSV_TICKER_COLUMN].nunique()

    @classmethod
    def _load_tickers(cls, tickers_json: str=None) -> tuple:
        """(tickers, quote_unit_grams): {symbol: yfinance ticker} and {symbol: grams per quoted
        unit}, each starting from the built-in TICKERS/QUOTE_UNIT_GRAMS and merging tickers_json
        (if given) on top - see __init__'s docstring."""
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

    def _compute_data(self, dataframe: pd.DataFrame, currency_to: str, cache_dir: str=None, force_refresh: bool=False) -> tuple:
        """Returns (computed_dataframe, total_buy_invested): total_buy_invested is the lifetime
        amount ever bought (only 'buy' rows, unreduced by later sells) - what __init__ needs for
        distribution_by_ticker/total_money_invested (see CLAUDE.md's Stock architecture notes on
        that lifetime-vs-currently-held split). Returned from here, rather than recomputed
        independently in __init__ the way Stock/Crypto still do straight from each buy row's own
        CSV price_of_unit, because a buy's price now comes from this method's own yfinance fetch
        below - recomputing it separately in __init__ would mean fetching the same price history
        a second time."""
        start_date=dataframe.index.min()
        symbol=dataframe[self.CSV_TICKER_COLUMN].iloc[0]
        ticker_name=self.tickers[symbol]

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        if cache is not None:
            # 'commodity-v3': bumped from 'commodity' when buy.csv dropped price_of_unit for
            # CSV_UNIT_COLUMN and this method started returning a (dataframe, total_buy_invested)
            # tuple instead of a bare DataFrame ('commodity-v2'), then again when sell.csv also
            # dropped its own price_of_unit in favor of that day's yfinance close. The isinstance
            # check below guards a cache entry from the older, bare-DataFrame format the same
            # way PolishRetailBonds guards its own tuple cache format.
            cache_key=DiskCache.make_key('commodity-v3', ticker_name, currency_to, DiskCache.hash_dataframe(dataframe))
            if not force_refresh:
                cached=cache.get(cache_key)
                if isinstance(cached, tuple) and len(cached)==2:
                    return cached

        currency=Currency(self.QUOTE_CURRENCY, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)

        ticker=yf.Ticker(ticker_name)
        ticker_data=ticker.history(start=start_date, end=datetime.today(), repair=True, actions=False)
        # yfinance returns an empty DataFrame (not an error) for an invalid futures ticker,
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
        # inherently sequential, so it can't be reduced to a single vectorized expression (see
        # Stock._compute_data, same pattern). What's vectorized instead: pulling every column
        # (and each transaction's same-day FX rate) out as plain numpy arrays once up front,
        # and accumulating into numpy arrays by integer position instead of repeated
        # .iterrows()/.loc[label] calls inside the loop.
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
        premium=column_or_nan(self.CSV_PREMIUM_COLUMN)
        sell_tax=column_or_nan(self.CSV_SELL_TAX_COLUMN)
        fx=currency.data.loc[sorted_df.index, self.CLOSE_COLUMN].to_numpy(dtype=float)

        position=data.index.get_indexer(sorted_df.index)
        if (position<0).any():
            missing=sorted_df.index[position<0]
            raise KeyError(f"Transaction date(s) {list(missing)} for {ticker_name} fall outside the computed daily range.")

        # Every buy's and sell's own market price - the same already currency_to-converted
        # Close series used for unrealized profit below - looked up by transaction row via
        # position, exactly like every other per-row column here (no separate FX step needed
        # for it, unlike sell_tax below, since data[CLOSE_COLUMN] above is already converted to
        # currency_to).
        market_price=data[self.CLOSE_COLUMN].to_numpy()[position]

        money_invested_by_day=np.zeros(len(data))
        units_by_day=np.zeros(len(data))
        realized_profit_by_day=np.zeros(len(data))

        running_units=0.0
        running_money_invested=0.0
        total_buy_invested=0.0

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
                raw_money_invested=units*market_price[i]
                money_invested=round((premium[i]+1.0)*raw_money_invested, 2)

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
        data.drop(columns=[self.CLOSE_COLUMN, self.UNITS_COLUMN], inplace=True)

        result=(data, total_buy_invested)
        if cache is not None:
            cache.set(cache_key, result)

        return result
