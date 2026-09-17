"""
Benchmark calculator: simulates buying a single stock/ETF ticker with the same day-by-day cash
contributions a Portfolio actually made ("what if this money had gone into SPY instead"), so its
IRR is directly comparable to Portfolio.calculate_irr(). Dividends are reinvested.

Not meant to be constructed directly - see Portfolio.simulate_benchmark.
"""

from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf
from .currency_calculator_library import get_cached_currency
from .calculator_mixins import ReprMixin, IrrMixin


class Benchmark(IrrMixin, ReprMixin):
    # Same contract as Portfolio.data/.portfolio - see calculator_mixins.IrrMixin.
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_COLUMN='Profit'
    IRR_COLUMN='Irr'
    PREV_MONEY_INVESTED_COLUMN='Prev_money_inv'
    TOTAL_MONEY_COLUMN='Total_money'
    CASHFLOW_COLUMN='Cashflow'

    def __init__(self, contributions: pd.Series, ticker: str, currency: str='USD', currency_to: str='USD', cache_dir: str=None, force_refresh: bool=False, currency_cache: dict=None):
        """contributions: daily net investor cash - positive on a buy day, negative on a
        withdrawal, 0 elsewhere - indexed by a gapless daily DatetimeIndex (Portfolio.data's own
        shape). Money_invested's cumsum exactly reproduces this series; a withdrawal sells
        whatever units of `ticker` are worth that same dollar amount that day (can go
        net-negative if it exceeds the simulated position's value - a known simplification).
        ticker: yfinance symbol to buy with that schedule instead (e.g. 'SPY').
        currency: ticker's native currency. currency_to: currency `contributions` is already in -
        converted via get_cached_currency, same as Stock/Commodity/Crypto."""
        contributions=contributions.round(2)
        start_date=contributions.index.min()
        end_date=datetime.today()

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

        all_days=pd.date_range(start=start_date, end=end_date, freq='D').tz_localize(None).normalize()
        close=ticker_data['Close'].reindex(all_days).ffill().bfill()
        # 'Dividends' is absent (not just zero) for a ticker with no dividend history at all.
        dividends=ticker_data.get('Dividends', pd.Series(0.0, index=ticker_data.index)).reindex(all_days).fillna(0.0)

        currency_obj=get_cached_currency(currency_cache, currency, currency_to, start_date, cache_dir=cache_dir, force_refresh=force_refresh)
        fx=currency_obj.data['Close'].reindex(all_days).ffill().bfill()

        price=(close*fx).reindex(contributions.index).ffill().to_numpy(dtype=float)
        dividend_per_share=(dividends*fx).reindex(contributions.index).fillna(0.0).to_numpy(dtype=float)
        contribution_values=contributions.to_numpy(dtype=float)
        n=len(contribution_values)

        # Money_invested is just contributions' own cumsum. Only units need a sequential walk -
        # each day's dividend reinvestment and buy/sell depends on the running position so far.
        money_invested_cumulative=np.round(np.cumsum(contribution_values), 2)

        units_by_day=np.zeros(n)
        running_units=0.0

        for i in range(n):
            price_i=price[i]
            div_i=dividend_per_share[i]

            if div_i>0 and running_units>0:
                extra_units=(running_units*div_i)/price_i
                units_by_day[i]+=extra_units
                running_units+=extra_units

            contribution=contribution_values[i]
            if contribution>1e-9:
                units_bought=contribution/price_i
                units_by_day[i]+=units_bought
                running_units+=units_bought
            elif contribution<-1e-9:
                units_sold=(-contribution)/price_i
                units_by_day[i]-=units_sold
                running_units-=units_sold

        units_cumulative=np.cumsum(units_by_day)
        # Holding 0 units is worth 0 regardless of price - guards against 0*NaN on the leading
        # zero-contribution row.
        total_value=np.where(units_cumulative==0.0, 0.0, units_cumulative*price)
        profit=np.round(total_value-money_invested_cumulative, 2)

        self.data=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: money_invested_cumulative,
            self.PROFIT_COLUMN: profit,
        }, index=contributions.index)

        self.total_money_invested=float(contributions.clip(lower=0.0).sum())
        self.total_current_value=float(total_value[-1]) if n else 0.0
        self.total_revenue=float(profit[-1]) if n else 0.0
