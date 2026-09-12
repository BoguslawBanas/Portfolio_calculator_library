"""
Class-based alternative to portfolio_calculator_library.py — a sketch, not wired
into the rest of the codebase. The original module stays a set of free functions
that each take a DataFrame; here the same logic is grouped behind a Portfolio
class whose constructor builds each source's Stock/PolishRetailBonds/Commodity/
Crypto/BankAccount instance once and merges their per-instrument DataFrames, and
the rest of the methods operate on the state the constructor built instead of
re-taking a dataframe argument every call.
"""

from datetime import datetime
import pandas as pd
import numpy as np
from tqdm import tqdm
from .stock_calculator_library import Stock
from .bonds_calculator_library import PolishRetailBonds
from .commodity_calculator_library import Commodity
from .crypto_calculator_library import Crypto
from .bank_account_calculator_library import BankAccount
from .cache_library import DiskCache


class Portfolio:
    # --- Output: self.data contract + Portfolio-specific extras. The first three form the
    # shared DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    # Only present when at least one Stock source contributed (the only asset type that
    # produces a Dividend column) — read via self.data.get(DIVIDEND_COLUMN, 0.0), not [].
    DIVIDEND_COLUMN='Dividend'
    DAILY_RETURN_COLUMN='Daily_return'
    IRR_COLUMN='Irr'
    # Working-only columns, created and dropped again within calculate_irr.
    PREV_MONEY_INVESTED_COLUMN='Prev_money_inv'
    TOTAL_MONEY_COLUMN='Total_money'
    CASHFLOW_COLUMN='Cashflow'

    # --- Leftover from a raw-CSV ingestion path (_load_sources/_read_directory/_split_by_isin/
    # _replace_isin_with_ticker/get_dataframe_currency/get_earliest_date) that has since been
    # removed as dead code: __init__ never builds a combined dataframe of its own to tag/split -
    # it hands each sources dict entry straight to the matching Stock/PolishRetailBonds/
    # Commodity/Crypto/BankAccount constructor, which does its own loading. Nothing in this file
    # reads these four constants anymore (each asset-type module defines its own
    # SOURCE_TYPE_COLUMN/CSV_TICKER_COLUMN instead - see e.g. Stock's) - kept only in case a
    # future raw-ingestion path resurrects them; safe to delete otherwise. ---
    SOURCE_TYPE_COLUMN='type'          # asset type ('stock'/'bonds'/...), from the sources dict
    TRANSACTION_STATE_COLUMN='state'   # per-row state ('buy'/'sell'/...), from the CSV filename
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'

    def __init__(self, sources: dict, tickers_json: str=None, currency: str='USD', cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False):
        """sources: a dict mapping each directory path to the asset type it holds ('stock',
        'bonds', 'commodities', 'crypto', or 'bank_account'). Each directory is handed straight
        to the matching Stock/PolishRetailBonds/Commodity/Crypto/BankAccount constructor below,
        which does its own loading/splitting of the CSVs inside it - Portfolio itself never
        builds a combined raw dataframe to tag/split.

        tickers_json: required when sources includes a 'stock' entry - path to the JSON file
        (see CLAUDE.md / stock_calculator_library) mapping each ISIN to {"ticker": <yfinance
        symbol>, "currency": <instrument currency>}, passed straight through to Stock.

        self.distribution_by_currency/_current_value/_revenue (always populated, no flag needed):
        allocation by each ticker/symbol/bond-type's own NATIVE currency (a US stock's 'usd',
        a Polish bond's 'PLN', ...) rather than by ticker/directory - answers "how much of my
        portfolio is actually USD-denominated vs. EUR vs. PLN", independent of currency below
        (the single currency self.data itself is already converted to and summed in).

        cache_dir: optional directory to cache every source's computed DataFrame in — see
        cache_library.DiskCache. Passed straight through to each Stock/Bonds/Commodity/Crypto
        constructed below; disabled (no caching) when left as None. Once every source is
        loaded, __init__ also sweeps cache_dir via DiskCache.evict_stale_if_due() — reclaiming
        orphaned entries automatically, at most once per calendar day regardless of how many
        times Portfolio is constructed that day, rather than requiring a manual
        DiskCache(cache_dir).clear().

        force_refresh: when True (and cache_dir is set), every source ignores its cached
        entry and recomputes/re-fetches from scratch, then overwrites the cache with the
        fresh result — a one-off "cold start" without deleting cache_dir yourself.

        include_native_currency: when True, Stock/Commodity/Crypto sources also compute each
        ticker/symbol's DataFrame in its own native currency, isolated from FX movement
        against currency — collected into self.native_data/self.native_currency (keyed by
        ticker/symbol), alongside the always-converted, summable self.data. Bonds are left out
        of this: PolishRetailBonds now does convert (via its own currency_to, passed through as
        currency above — see README Roadmap), but doesn't yet expose an include_native_currency
        of its own the way Stock/Commodity/Crypto do, so there's no per-holding native-currency
        DataFrame for Portfolio to collect here."""
        self.distribution_by_directory=dict()
        self.distribution_by_directory_current_value=dict()
        self.distribution_by_directory_revenue=dict()
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Allocation by each ticker/symbol/bond-type's own NATIVE currency (tickers.json's
        # currency field for Stock, each source's fixed QUOTE_CURRENCY/NATIVE_CURRENCY for
        # Commodity/Crypto/PolishRetailBonds) - not by currency (this constructor's target
        # currency, what self.data is already summed in), so this answers "how much of my
        # portfolio is actually USD-denominated vs. EUR vs. PLN" regardless of what everything
        # gets converted to for reporting. Keyed uppercase so e.g. 'usd' (Stock) and 'USD'
        # (a differently-cased source) land in the same bucket.
        self.distribution_by_currency=dict()
        self.distribution_by_currency_current_value=dict()
        self.distribution_by_currency_revenue=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.total_invested_money=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        portfolio_list=list()

        # Counting tickers/bonds up front (cheap — just reads/splits CSVs, no network calls) lets
        # one progress bar span the whole portfolio, tracking the unit of work that's actually
        # slow: one yfinance fetch per ticker. A whole bonds directory only counts as a single
        # unit — PolishRetailBonds computation is fast, local work with no per-row network
        # calls, and its progress_callback now fires once per instance rather than once per
        # bond row.
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
            elif type=='bank_account':
                total_units+=BankAccount.count_accounts(dir)

        with tqdm(total=total_units, desc='Loading portfolio') as progress_bar:
            for dir, type in sources.items():
                if type=='stock':
                    source=Stock(dir, tickers_json, currency, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                elif type=='bonds':
                    source=PolishRetailBonds(dir, currency, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh)
                    self._absorb_source(dir, source)
                elif type=='commodities':
                    source=Commodity(dir, currency, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                elif type=='crypto':
                    source=Crypto(dir, currency, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                elif type=='bank_account':
                    source=BankAccount(dir, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh)
                    self._absorb_source(dir, source, supports_currency=False)
                else:
                    continue
                portfolio_list.append(source)

        # A portfolio where every holding across every source has fully matured/been fully sold
        # has a 0 total for one or more of these metrics while its distribution dict is still
        # non-empty - guard each division so that lands on a correct 0.0 instead of a
        # ZeroDivisionError (these are plain Python floats, not numpy - an unguarded division
        # raises rather than silently producing NaN).
        for key, value in self.distribution_by_directory.items():
            self.distribution_by_directory[key]=100.0*value/self.total_invested_money if self.total_invested_money else 0.0

        for key, value in self.distribution_by_directory_current_value.items():
            self.distribution_by_directory_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_directory_revenue.items():
            self.distribution_by_directory_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        for key, value in self.distribution_by_ticker.items():
            self.distribution_by_ticker[key]=100.0*value/self.total_invested_money if self.total_invested_money else 0.0

        for key, value in self.distribution_by_ticker_current_value.items():
            self.distribution_by_ticker_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_ticker_revenue.items():
            self.distribution_by_ticker_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        for key, value in self.distribution_by_currency.items():
            self.distribution_by_currency[key]=100.0*value/self.total_invested_money if self.total_invested_money else 0.0

        for key, value in self.distribution_by_currency_current_value.items():
            self.distribution_by_currency_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_currency_revenue.items():
            self.distribution_by_currency_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        self.data=self.merge(portfolio_list)

        if cache_dir is not None:
            DiskCache(cache_dir).evict_stale_if_due()

    @classmethod
    def from_csv(cls, dataframe_file: str, source_type: str, tickers_json: str=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False) -> 'Portfolio':
        """Convenience alias — the constructor already accepts a single prepared CSV."""
        return cls({dataframe_file: source_type}, tickers_json, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency)

    @staticmethod
    def _accumulate_by_currency(target: dict, currency_by_ticker: dict, key, amount: float):
        """Adds amount into target's bucket for currency_by_ticker[key]'s currency (uppercased,
        so differently-cased sources land in the same bucket), used by __init__ to build
        distribution_by_currency/_current_value/_revenue from each source's per-ticker figures
        (still in absolute terms at that point - see __init__'s own distribution_by_ticker loops,
        which normalize to percentages only after every source has been folded in)."""
        currency_code=currency_by_ticker[key].upper()
        target[currency_code]=target.get(currency_code, 0.0)+amount

    def _absorb_source(self, dir: str, source, supports_currency: bool=True, supports_native_currency: bool=False):
        """Folds one already-constructed source instance (Stock/PolishRetailBonds/Commodity/
        Crypto/BankAccount) into the matching portfolio-level totals/distributions - the logic
        every branch of __init__'s loop above used to repeat almost verbatim per asset type.
        supports_currency: False only for BankAccount, the one source type with no
        currency_by_ticker to accumulate distribution_by_currency/_current_value/_revenue from.
        supports_native_currency: True only when include_native_currency was requested for a
        source type that supports it (Stock/Commodity/Crypto) - PolishRetailBonds/BankAccount
        never pass True here since neither exposes native_data/native_currency."""
        self.distribution_by_directory[dir]=source.total_money_invested
        self.distribution_by_directory_current_value[dir]=source.total_current_value
        self.distribution_by_directory_revenue[dir]=source.total_revenue
        self.total_invested_money+=source.total_money_invested
        self.total_current_value+=source.total_current_value
        self.total_revenue+=source.total_revenue

        per_metric=(
            (self.distribution_by_ticker, self.distribution_by_currency, source.distribution_by_ticker, source.total_money_invested),
            (self.distribution_by_ticker_current_value, self.distribution_by_currency_current_value, source.distribution_by_ticker_current_value, source.total_current_value),
            (self.distribution_by_ticker_revenue, self.distribution_by_currency_revenue, source.distribution_by_ticker_revenue, source.total_revenue),
        )
        for target_ticker, target_currency, source_ticker, total in per_metric:
            for key, value in source_ticker.items():
                amount=round(value/100.0*total, 2)
                target_ticker[key]=amount
                if supports_currency:
                    self._accumulate_by_currency(target_currency, source.currency_by_ticker, key, amount)

        if supports_native_currency:
            self.native_data.update(source.native_data)
            self.native_currency.update(source.native_currency)

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

        n=len(dataframe[self.CASHFLOW_COLUMN])
        cashflow_values=dataframe[self.CASHFLOW_COLUMN].to_numpy()
        total_money_values=dataframe[self.TOTAL_MONEY_COLUMN].to_numpy()

        irr=np.full(n, np.nan)
        guess=0.1
        irr[0]=0.0

        # Day i's IRR input is every cashflow through day i, plus day i's total money as a
        # closing/terminal value - dataframe[CASHFLOW_COLUMN].iloc[:i+1].to_list()+[...] used to
        # rebuild that (i+2)-element list from scratch on every iteration (an O(n) copy each
        # time, so O(n^2) total over the full loop). Since only the last two slots actually
        # change between iterations - the newly-added cashflow term and the terminal value - a
        # single preallocated buffer can be extended by two O(1) writes per iteration instead:
        # position i gets this day's cashflow (permanently, matching what the list-rebuild
        # would have had there), position i+1 gets this day's terminal value (overwriting the
        # previous iteration's terminal value, which was never anything but scratch space).
        buffer=np.empty(n+1, dtype=np.float64)
        for i in range(n):
            buffer[i]=cashflow_values[i]
            buffer[i+1]=total_money_values[i]
            guess=self._irr_newton(buffer[:i+2], guess=guess)
            irr[i]=round(((guess+1.0)**i-1)*100.0, 2)
            if np.isnan(guess):
                guess=0.1

        dataframe[self.IRR_COLUMN]=irr
        dataframe.drop(columns=[self.PREV_MONEY_INVESTED_COLUMN, self.CASHFLOW_COLUMN], inplace=True)
        self.portfolio=dataframe

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

        if days_between==0:
            days_between=(dataframe.index[-1]-dataframe.index[0]).days

        # Same formula as calculate_money_earned_between_dates (Profit `offset` days ago minus
        # Profit `days_between+offset` days ago, 0.0 wherever that date falls outside the
        # index), but for every row at once via .shift() instead of calling it in a per-row
        # Python loop. .shift(N) moving N *rows* is calendar-day-offset-equivalent to
        # idx-pd.DateOffset(days=N) only when the index is a continuous daily range - true here
        # before resample() (see its own use in the README/example), not after, since resample()
        # produces a weekly/monthly/etc. index where shifting by rows and by days diverge.
        recent_profit=dataframe[self.PROFIT_COLUMN].shift(offset).fillna(0.0)
        older_profit=dataframe[self.PROFIT_COLUMN].shift(days_between+offset).fillna(0.0)
        dataframe[self.DAILY_RETURN_COLUMN]=round((recent_profit-older_profit)/days_between, 2)

        self.portfolio=dataframe
        return self.portfolio