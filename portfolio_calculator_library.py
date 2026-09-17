"""
Portfolio calculator: combines one or more Stock/PolishRetailBonds/Commodity/Crypto/BankAccount
sources into a single portfolio-level DataFrame. The constructor builds each source once and
merges their DataFrames; other methods (calculate_irr, resample, ...) operate on that state
instead of retaking a dataframe argument each call.
"""

from datetime import datetime
import pandas as pd
from tqdm import tqdm
from .stock_calculator_library import Stock
from .bonds_calculator_library import PolishRetailBonds
from .commodity_calculator_library import Commodity
from .crypto_calculator_library import Crypto
from .bank_account_calculator_library import BankAccount
from .benchmark_calculator_library import Benchmark
from .cache_library import DiskCache
from .calculator_mixins import ReprMixin, IrrMixin


class Portfolio(IrrMixin, ReprMixin):
    # --- Output: self.data contract + Portfolio-specific extras (see CLAUDE.md). ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    # Only present when a Stock source contributed - read via self.data.get(DIVIDEND_COLUMN,
    # 0.0), not [].
    DIVIDEND_COLUMN='Dividend'
    # Unrealized gain plus dividends, excluding gain already locked in by a sell (each source's
    # own Realized_profit; only BankAccount has none to exclude). Every source contributes it,
    # so unlike DIVIDEND_COLUMN this is always present - safe to read via self.data[...] directly.
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    # Excludes only dividends, keeping realized profit (Profit - Dividend). Also always present.
    PROFIT_EXCLUDING_DIVIDEND_COLUMN='Profit_excluding_dividends'
    DAILY_RETURN_COLUMN='Daily_return'
    IRR_COLUMN='Irr'
    # Working-only columns, created and dropped again within calculate_irr.
    PREV_MONEY_INVESTED_COLUMN='Prev_money_inv'
    TOTAL_MONEY_COLUMN='Total_money'
    CASHFLOW_COLUMN='Cashflow'

    VALID_SOURCE_TYPES={'stock', 'bonds', 'commodities', 'crypto', 'bank_account'}
    # Subset of VALID_SOURCE_TYPES simulate_benchmark accepts - 'bonds'/'bank_account' have no
    # market price to simulate a position in.
    BENCHMARK_ASSET_TYPES={'stock', 'commodities', 'crypto'}

    def __init__(self, sources: dict, tickers_json: str=None, currency_to: str='USD', cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False, commodity_tickers_json: str=None, crypto_tickers_json: str=None):
        """sources: dict mapping each directory to its asset type, one of VALID_SOURCE_TYPES;
        any other value raises ValueError immediately, before any source is constructed. Each
        directory goes straight to the matching source class, which does its own CSV loading.

        tickers_json: required for a 'stock' entry - JSON mapping each ISIN to {"ticker":
        <yfinance symbol>, "currency": <instrument currency>}, passed through to Stock.

        commodity_tickers_json/crypto_tickers_json: optional, passed through to every
        Commodity/Crypto source's own tickers_json - lets a caller track a symbol beyond each
        class's built-in TICKERS without editing the source. Never required, unlike tickers_json.

        currency_to: target currency every source is converted to and summed in - defaults to
        'USD'. Passed through as each source's own currency_to (BankAccount has none - single-
        currency only, see its own README entry).

        self.distribution_by_currency/_current_value/_revenue (always populated): allocation by
        each position's own NATIVE currency (a US stock's 'usd', a Polish bond's 'PLN', ...)
        rather than by ticker/directory - independent of currency_to, the single currency
        self.data is converted to and summed in.

        self.total_money_currently_invested/distribution_by_directory/_ticker/_currency_currently_
        invested: the same allocation figures as their lifetime-gross counterparts above, but by
        cost basis of what's actually still held today. Every source tracks both independently;
        they diverge once anything's actually been sold/matured/cancelled/withdrawn.

        Every Stock/Commodity/Crypto/PolishRetailBonds source below shares one currency_cache
        dict, built fresh here and passed down to each, so a currency pair shared across
        holdings/sources is fetched once per Portfolio construction - see get_cached_currency.

        cache_dir: caches every source's computed DataFrame - see cache_library.DiskCache.
        Disabled when None. Once every source is loaded, also sweeps cache_dir via
        DiskCache.evict_stale_if_due(), at most once per calendar day.

        force_refresh: every source ignores its cached entry, recomputes, overwrites the cache -
        a one-off cold start without deleting cache_dir yourself.

        include_native_currency: Stock/Commodity/Crypto sources also compute each ticker/
        symbol's DataFrame in its own native currency (self.native_data/native_currency),
        alongside the always-converted self.data. PolishRetailBonds is left out - it converts via
        its own currency_to but doesn't yet expose an include_native_currency of its own."""
        # Kept so simulate_benchmark can default to it - self.data is already in this currency.
        self.currency_to=currency_to
        self.distribution_by_directory=dict()
        self.distribution_by_directory_currently_invested=dict()
        self.distribution_by_directory_current_value=dict()
        self.distribution_by_directory_revenue=dict()
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_currently_invested=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Allocation by each position's own NATIVE currency, not currency_to - answers "how much
        # of my portfolio is actually USD/EUR/PLN" regardless of reporting currency. Keyed
        # uppercase so differently-cased sources land in the same bucket.
        self.distribution_by_currency=dict()
        self.distribution_by_currency_currently_invested=dict()
        self.distribution_by_currency_current_value=dict()
        self.distribution_by_currency_revenue=dict()
        self.native_data=dict()
        self.native_currency=dict()
        self.total_money_invested=0.0
        # Unlike total_money_invested (lifetime gross, never reduced by a sell/withdrawal/
        # maturity), this is what's actually still held today across every source.
        self.total_money_currently_invested=0.0
        self.total_current_value=0.0
        self.total_revenue=0.0
        portfolio_list=list()
        # Shared across every source below so a currency pair fetched by one is reused by
        # another instead of each fetching its own.
        currency_cache=dict()

        # Validated up front, before any slow, network-bound source construction starts, so a
        # typo in sources fails loudly instead of silently dropping that source.
        for dir, type in sources.items():
            if type not in self.VALID_SOURCE_TYPES:
                raise ValueError(f"Unknown source type {type!r} for {dir!r} (expected one of {sorted(self.VALID_SOURCE_TYPES)}).")

        # Counting tickers/bonds up front (cheap, no network calls) lets one progress bar span
        # the whole portfolio, tracking one yfinance fetch per ticker. A bonds directory counts
        # as a single unit - PolishRetailBonds is fast, local, no per-row network calls.
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
                total_units+=BankAccount.count_tickers(dir)

        with tqdm(total=total_units, desc='Loading portfolio') as progress_bar:
            for dir, type in sources.items():
                if type=='stock':
                    source=Stock(dir, tickers_json, currency_to, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency, currency_cache=currency_cache)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                elif type=='bonds':
                    source=PolishRetailBonds(dir, currency_to, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, currency_cache=currency_cache)
                    self._absorb_source(dir, source)
                elif type=='commodities':
                    source=Commodity(dir, currency_to, commodity_tickers_json, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency, currency_cache=currency_cache)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                elif type=='crypto':
                    source=Crypto(dir, currency_to, crypto_tickers_json, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency, currency_cache=currency_cache)
                    self._absorb_source(dir, source, supports_native_currency=include_native_currency)
                else:  # type=='bank_account' - the only remaining member, already validated above
                    source=BankAccount(dir, progress_callback=progress_bar.update, cache_dir=cache_dir, force_refresh=force_refresh)
                    self._absorb_source(dir, source, supports_currency=False)
                portfolio_list.append(source)

        # A portfolio fully matured/sold out has a 0 total for one or more of these metrics while
        # its distribution dict is still non-empty - guard each division for a correct 0.0
        # instead of a ZeroDivisionError (plain Python floats, not numpy).
        for key, value in self.distribution_by_directory.items():
            self.distribution_by_directory[key]=100.0*value/self.total_money_invested if self.total_money_invested else 0.0

        for key, value in self.distribution_by_directory_currently_invested.items():
            self.distribution_by_directory_currently_invested[key]=100.0*value/self.total_money_currently_invested if self.total_money_currently_invested else 0.0

        for key, value in self.distribution_by_directory_current_value.items():
            self.distribution_by_directory_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_directory_revenue.items():
            self.distribution_by_directory_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        for key, value in self.distribution_by_ticker.items():
            self.distribution_by_ticker[key]=100.0*value/self.total_money_invested if self.total_money_invested else 0.0

        for key, value in self.distribution_by_ticker_currently_invested.items():
            self.distribution_by_ticker_currently_invested[key]=100.0*value/self.total_money_currently_invested if self.total_money_currently_invested else 0.0

        for key, value in self.distribution_by_ticker_current_value.items():
            self.distribution_by_ticker_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_ticker_revenue.items():
            self.distribution_by_ticker_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        for key, value in self.distribution_by_currency.items():
            self.distribution_by_currency[key]=100.0*value/self.total_money_invested if self.total_money_invested else 0.0

        for key, value in self.distribution_by_currency_currently_invested.items():
            self.distribution_by_currency_currently_invested[key]=100.0*value/self.total_money_currently_invested if self.total_money_currently_invested else 0.0

        for key, value in self.distribution_by_currency_current_value.items():
            self.distribution_by_currency_current_value[key]=100.0*value/self.total_current_value if self.total_current_value else 0.0

        for key, value in self.distribution_by_currency_revenue.items():
            self.distribution_by_currency_revenue[key]=100.0*value/self.total_revenue if self.total_revenue else 0.0

        self.data=self.merge(portfolio_list)

        if cache_dir is not None:
            DiskCache(cache_dir).evict_stale_if_due()

    @classmethod
    def from_csv(cls, dataframe_file: str, source_type: str, tickers_json: str=None, cache_dir: str=None, force_refresh: bool=False, include_native_currency: bool=False) -> 'Portfolio':
        """Convenience alias - the constructor already accepts a single prepared CSV."""
        return cls({dataframe_file: source_type}, tickers_json, cache_dir=cache_dir, force_refresh=force_refresh, include_native_currency=include_native_currency)

    @staticmethod
    def _accumulate_by_currency(target: dict, currency_by_ticker: dict, key, amount: float):
        """Adds amount into target's bucket for currency_by_ticker[key]'s currency (uppercased).
        Used by __init__ to build distribution_by_currency/_current_value/_revenue from each
        source's per-ticker figures, still absolute at that point (normalized to % later)."""
        currency_code=currency_by_ticker[key].upper()
        target[currency_code]=target.get(currency_code, 0.0)+amount

    def _absorb_source(self, dir: str, source, supports_currency: bool=True, supports_native_currency: bool=False):
        """Folds one already-constructed source instance into the matching portfolio-level
        totals/distributions.
        supports_currency: False only for BankAccount, which has no currency_by_ticker.
        supports_native_currency: True only when include_native_currency was requested for a
        source that supports it (Stock/Commodity/Crypto)."""
        self.distribution_by_directory[dir]=source.total_money_invested
        self.distribution_by_directory_currently_invested[dir]=source.total_money_currently_invested
        self.distribution_by_directory_current_value[dir]=source.total_current_value
        self.distribution_by_directory_revenue[dir]=source.total_revenue
        self.total_money_invested+=source.total_money_invested
        self.total_money_currently_invested+=source.total_money_currently_invested
        self.total_current_value+=source.total_current_value
        self.total_revenue+=source.total_revenue

        per_metric=(
            (self.distribution_by_ticker, self.distribution_by_currency, source.distribution_by_ticker, source.total_money_invested),
            (self.distribution_by_ticker_currently_invested, self.distribution_by_currency_currently_invested, source.distribution_by_ticker_currently_invested, source.total_money_currently_invested),
            (self.distribution_by_ticker_current_value, self.distribution_by_currency_current_value, source.distribution_by_ticker_current_value, source.total_current_value),
            (self.distribution_by_ticker_revenue, self.distribution_by_currency_revenue, source.distribution_by_ticker_revenue, source.total_revenue),
        )
        for target_ticker, target_currency, source_ticker, total in per_metric:
            for key, value in source_ticker.items():
                amount=round(value/100.0*total, 2)
                target_ticker[key]=target_ticker.get(key, 0.0)+amount
                if supports_currency:
                    self._accumulate_by_currency(target_currency, source.currency_by_ticker, key, amount)

        if supports_native_currency:
            self.native_data.update(source.native_data)
            self.native_currency.update(source.native_currency)

    def simulate_benchmark(self, symbol: str, asset_type: str='stock', currency: str=None, currency_to: str=None, tickers_json: str=None, cache_dir: str=None, force_refresh: bool=False) -> Benchmark:
        """Simulates buying a single stock/ETF/commodity/crypto with this portfolio's own
        day-by-day cash contributions, so the result's calculate_irr() is directly comparable to
        this portfolio's own (see Benchmark, and Plot.benchmark_comparison_plot to overlay both).

        symbol: for asset_type='stock' (the default), a yfinance ticker directly (e.g. 'SPY').
        For 'commodities'/'crypto', a friendly name (e.g. 'gold', 'bitcoin') resolved through
        Commodity.TICKERS/Crypto.TICKERS - an unknown name raises the same KeyError constructing
        one of those would.
        asset_type: one of BENCHMARK_ASSET_TYPES ('stock', 'commodities', 'crypto').
        currency: symbol's native currency - defaults to 'USD' for a stock, or to Commodity/
        Crypto's own QUOTE_CURRENCY (both 'usd') otherwise.
        tickers_json: for 'commodities'/'crypto' only - merged on top of that class's built-in
        TICKERS, same as a real source. Ignored for 'stock'.
        currency_to: reporting currency - defaults to this portfolio's own currency_to."""
        if asset_type=='stock':
            ticker=symbol
            currency=currency or 'USD'
        elif asset_type=='commodities':
            ticker=Commodity._load_tickers(tickers_json)[0][symbol]
            currency=currency or Commodity.QUOTE_CURRENCY
        elif asset_type=='crypto':
            ticker=Crypto._load_tickers(tickers_json)[symbol]
            currency=currency or Crypto.QUOTE_CURRENCY
        else:
            raise ValueError(f"Unknown simulate_benchmark asset_type: {asset_type!r} (expected one of {sorted(self.BENCHMARK_ASSET_TYPES)})")

        contributions=self.data[self.MONEY_INVESTED_COLUMN].diff().fillna(0.0)
        return Benchmark(contributions, ticker, currency=currency, currency_to=currency_to or self.currency_to, cache_dir=cache_dir, force_refresh=force_refresh)

    @staticmethod
    def merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-instrument DataFrames by date into a single portfolio DataFrame."""
        list_of_df=[df.data for df in dataframes]
        merged=pd.concat(list_of_df).groupby(level=0, sort=True).sum().ffill()
        return Portfolio._prepend_zero_day(merged)

    @staticmethod
    def _prepend_zero_day(dataframe: pd.DataFrame) -> pd.DataFrame:
        """Adds a zero-valued row one day before the first date, so IRR/return calculations have
        a clean starting point."""
        zero_row=pd.DataFrame(
            [{col: 0.0 for col in dataframe.columns}],
            index=[dataframe.index[0]-pd.DateOffset(days=1)]
        )
        return pd.concat([zero_row, dataframe]).sort_index()

    def resample(self, resample_rule: str) -> pd.DataFrame:
        resample_rule=resample_rule.upper()

        timedelta_to_subtract: pd.DateOffset
        if resample_rule=='D':
            timedelta_to_subtract=pd.DateOffset(days=1)
        elif resample_rule=='W':
            timedelta_to_subtract=pd.DateOffset(weeks=1)
        elif resample_rule=='ME':
            timedelta_to_subtract=pd.DateOffset(months=1)
        elif resample_rule=='QE':
            timedelta_to_subtract=pd.DateOffset(months=3)
        elif resample_rule=='YE':
            timedelta_to_subtract=pd.DateOffset(years=1)

        new_row=pd.DataFrame(
            [{col: 0.0 for col in self.portfolio.columns}],
            index=[pd.to_datetime(self.portfolio.index[0]-timedelta_to_subtract, format='%Y-%m-%d')]
        )
        dataframe=pd.concat([self.portfolio, new_row]).sort_index()
        self.portfolio=dataframe.resample(resample_rule).ffill()
        return self.portfolio

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

        # Same formula as calculate_money_earned_between_dates, vectorized via .shift() instead
        # of a per-row loop. Shifting N rows only matches N calendar days on a continuous daily
        # index - true before resample(), not after (weekly/monthly rows diverge from row shifts).
        recent_profit=dataframe[self.PROFIT_COLUMN].shift(offset).fillna(0.0)
        older_profit=dataframe[self.PROFIT_COLUMN].shift(days_between+offset).fillna(0.0)
        dataframe[self.DAILY_RETURN_COLUMN]=round((recent_profit-older_profit)/days_between, 2)

        self.portfolio=dataframe
        return self.portfolio