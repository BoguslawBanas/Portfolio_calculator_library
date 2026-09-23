"""
Benchmark calculator: simulates buying one stock/ETF ticker - or a weighted basket of several -
with the same day-by-day cash contributions a Portfolio actually made ("what if this money had
gone into SPY instead", or "60% SPY / 40% gold"), so its IRR is directly comparable to
Portfolio.calculate_irr(). Dividends are reinvested into the ticker that paid them.

Not meant to be constructed directly - see Portfolio.simulate_benchmark.
"""

from datetime import datetime
from decimal import Decimal
import numpy as np
import pandas as pd
import yfinance as yf
from .currency_calculator_library import get_cached_currency
from .calculator_mixins import ReprMixin, IrrMixin, to_money, money_array


class Benchmark(IrrMixin, ReprMixin):
    # Same contract as Portfolio.data/.portfolio - see calculator_mixins.IrrMixin.
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_COLUMN='Profit'
    IRR_COLUMN='Irr'
    PREV_MONEY_INVESTED_COLUMN='Prev_money_inv'
    TOTAL_MONEY_COLUMN='Total_money'
    CASHFLOW_COLUMN='Cashflow'

    # Tolerance (in percentage points) on the weights summing to 100, so e.g. 33.33/33.33/33.34
    # or a rounded 1/3 split isn't rejected over float noise.
    WEIGHT_SUM_TOLERANCE=0.01

    def __init__(self, contributions: pd.Series, tickers, currency='USD', currency_to: str='USD', cache_dir: str=None, force_refresh: bool=False, currency_cache: dict=None):
        """contributions: daily net investor cash - positive on a buy day, negative on a
        withdrawal, 0 elsewhere - indexed by a gapless daily DatetimeIndex (Portfolio.data's own
        shape). Money_invested's cumsum exactly reproduces this series.
        tickers: yfinance symbol to buy with that schedule instead (e.g. 'SPY'), or a dict
        {symbol: percent} splitting every buy across several (e.g. {'SPY': 60, 'GLD': 40}) - the
        percents must be positive and sum to 100. A withdrawal sells the same per-symbol split, by
        value, so a position can go net-negative if it withdraws more than it holds (a known
        simplification, same as with a single ticker). self.tickers holds the {symbol: percent}
        dict either way.
        currency: each symbol's native currency - one string for all of them, or a dict
        {symbol: currency} covering every symbol. currency_to: currency `contributions` is
        already in - converted via get_cached_currency, same as Stock/Commodity/Crypto."""
        self.tickers=self._normalize_weights(tickers)
        currencies=self._normalize_currencies(currency, self.tickers)

        contributions=contributions.round(2)
        start_date=contributions.index.min()
        end_date=datetime.today()
        all_days=pd.date_range(start=start_date, end=end_date, freq='D').tz_localize(None).normalize()

        prices=list()
        dividends_per_share=list()
        for ticker in self.tickers:
            price, dividend_per_share=self._load_ticker_series(ticker, currencies[ticker], currency_to, contributions.index, all_days, start_date, end_date, cache_dir, force_refresh, currency_cache)
            prices.append(price)
            dividends_per_share.append(dividend_per_share)

        # One column per ticker: (n days, k tickers).
        price=np.column_stack(prices)
        dividend_per_share=np.column_stack(dividends_per_share)
        weights=np.array(list(self.tickers.values()))/100.0
        contribution_values=contributions.to_numpy(dtype=float)
        n=len(contribution_values)
        k=len(weights)

        # Money_invested is just contributions' own cumsum. Only units need a sequential walk -
        # each day's dividend reinvestment and buy/sell depends on the running position so far.
        money_invested_cumulative=np.round(np.cumsum(contribution_values), 2)

        units_by_day=np.zeros((n, k))
        running_units=np.zeros(k)

        for i in range(n):
            price_i=price[i]
            div_i=dividend_per_share[i]
            delta=np.zeros(k)

            paying=(div_i>0) & (running_units>0)
            delta[paying]=(running_units[paying]*div_i[paying])/price_i[paying]

            # Signed: a buy adds units, a withdrawal removes the same per-ticker split's worth.
            contribution=contribution_values[i]
            if abs(contribution)>1e-9:
                delta+=contribution*weights/price_i

            units_by_day[i]=delta
            running_units+=delta

        units_cumulative=np.cumsum(units_by_day, axis=0)
        # Holding 0 units is worth 0 regardless of price - guards against 0*NaN on the leading
        # zero-contribution row.
        total_value=np.where(units_cumulative==0.0, 0.0, units_cumulative*price).sum(axis=1)
        profit=np.round(total_value-money_invested_cumulative, 2)

        # Units/price/contributions stay float throughout (quantities/prices, not money) -
        # Decimal only enters here, where the cumulative money_invested/profit arrays become the
        # actually-stored columns.
        self.data=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: money_array(money_invested_cumulative),
            self.PROFIT_COLUMN: money_array(profit),
        }, index=contributions.index)

        self.total_money_invested=to_money(contributions.clip(lower=0.0).sum())
        self.total_current_value=to_money(total_value[-1]) if n else Decimal('0')
        self.total_revenue=to_money(profit[-1]) if n else Decimal('0')

    @classmethod
    def _normalize_weights(cls, tickers) -> dict:
        """{symbol: percent} from a single symbol (100%) or an already-weighted dict, validated."""
        if isinstance(tickers, str):
            return {tickers: 100.0}
        if not isinstance(tickers, dict) or not tickers:
            raise ValueError(f"tickers must be a symbol or a non-empty {{symbol: percent}} dict, got {tickers!r}.")
        for symbol, percent in tickers.items():
            if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not percent>0:
                raise ValueError(f"Weight for {symbol!r} must be a positive number (percent), got {percent!r}.")
        total=float(sum(tickers.values()))
        if abs(total-100.0)>cls.WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"Ticker weights must sum to 100 (percent), got {total}.")
        return {symbol: float(percent) for symbol, percent in tickers.items()}

    @staticmethod
    def _normalize_currencies(currency, tickers: dict) -> dict:
        """{symbol: currency} from one currency string for every symbol, or a per-symbol dict."""
        if isinstance(currency, str):
            return {symbol: currency for symbol in tickers}
        missing=[symbol for symbol in tickers if symbol not in currency]
        if missing:
            raise ValueError(f"currency dict is missing an entry for {missing}.")
        return dict(currency)

    @staticmethod
    def _load_ticker_series(ticker: str, currency: str, currency_to: str, index: pd.DatetimeIndex, all_days: pd.DatetimeIndex, start_date, end_date, cache_dir, force_refresh, currency_cache) -> tuple:
        """(price, dividend_per_share) float arrays aligned to `index`, both already converted
        to currency_to."""
        ticker_data=yf.Ticker(ticker).history(start=start_date, end=end_date, repair=True, actions=True)
        if ticker_data.empty:
            raise ValueError(f"yfinance returned no price history for ticker {ticker!r} (requested {start_date.date()} to today) - check it's a valid, still-listed ticker.")
        ticker_data.index=ticker_data.index.tz_localize(None).normalize()

        # start_date is usually the day before the first real contribution (Portfolio's own
        # prepended zero row), often a weekend with no trading day - bridged by bfill below. A
        # multi-day gap instead means the ticker genuinely didn't exist yet.
        first_trading_day=ticker_data.index.min()
        if (first_trading_day-start_date).days>7:
            raise ValueError(f"No {ticker!r} price history before {first_trading_day.date()} - it doesn't reach back to this portfolio's start date {start_date.date()}.")

        close=ticker_data['Close'].reindex(all_days).ffill().bfill()
        # 'Dividends' is absent (not just zero) for a ticker with no dividend history at all.
        dividends=ticker_data.get('Dividends', pd.Series(0.0, index=ticker_data.index)).reindex(all_days).fillna(0.0)

        currency_obj=get_cached_currency(currency_cache, currency, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)
        fx=currency_obj.data['Close'].reindex(all_days).ffill().bfill()

        price=(close*fx).reindex(index).ffill().to_numpy(dtype=float)
        dividend_per_share=(dividends*fx).reindex(index).fillna(0.0).to_numpy(dtype=float)
        return price, dividend_per_share
