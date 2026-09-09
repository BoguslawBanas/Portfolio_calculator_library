"""
PolishRetailBonds models Polish retail treasury bonds (obligacje detaliczne) — bought directly
from the Treasury, no secondary market or observable price, only redeemable early at a fixed
penalty rather than sold. That's why this class looks nothing like Stock/Commodity/Crypto: no
yfinance fetch, no Currency conversion (see README Roadmap), just each bond type's own accrual
formula keyed off the bond code's three-letter prefix.

The accrual rules below (period length/count, compounding vs. flat payout, rate source, and the
'cena zamiany' exchange discount) are transcribed from the Ministry of Finance's own listy
emisyjne (emission letters) for one real issuance of each of the eight bond types currently
sold, supplied for review as bonds_lists/*.pdf (not bundled with this repo — see README). Every
one of those eight follows one of two accrual shapes:

- "flat": each period's interest is paid out at the period's end, computed off the bond's fixed
  NOMINAL_VALUE (not compounded into a growing base) — O = N*r/100*a/(D*F), where r is that
  period's annual rate, a is the actual number of days elapsed in the period so far, D is the
  actual number of days in the period, and F is the number of periods per year (so D*F
  approximates a 365-day year). ROR, DOR, and COI use this shape.
- "compounding": interest is only ever paid at final redemption, and each period's base is the
  previous period's base plus that period's own interest — W = N*(1+r_1)*(1+r_2)*...*(1+r_k).
  TOS, ROS, EDO, and ROD use this shape. Day-to-day, the currently-accruing period still adds a
  flat per-day amount (base*r/100/(D*F)), same as the "flat" shape — only the base each period
  starts from differs.

OTS (a single 3-month period, always at its CSV_INITIAL_COUPON_COLUMN rate) is a degenerate case
of "flat" with only one period.

Every bond type's rate for its first period comes straight from CSV_INITIAL_COUPON_COLUMN — a
promotional rate fixed at issuance, not derived from any external rate history. TOS reuses that
same rate for every later period too (truly fixed-for-life); every other multi-period type looks
up interest_rate_data (ROR/DOR, NBP reference rate) or inflation_rate_data (COI/ROS/EDO/ROD, CPI)
for each later period's base rate and adds CSV_ADDITIONAL_COUPON_COLUMN as that period's margin —
both quoted per-issuance in each type's own list emisyjny, so both belong in the per-holding CSV
row rather than as a class constant.

None of the eight letters mention withholding tax at all — that's tax law, not an issuance term,
so it's asserted here as one TAX_RATE constant applied uniformly, rather than inferred per type
(the prior version of this class applied 19% to variable-rate bonds and 0% to fixed-rate ones,
a split that had no documented basis and is not carried forward — see README Roadmap on this).
"""

import os
from typing import Callable
import numpy as np
import pandas as pd
from datetime import datetime
from .cache_library import DiskCache


class PolishRetailBonds:
    # --- Output: self.data / working DataFrame columns. The first three form the shared
    # DataFrame contract every asset-type calculator normalizes to (see CLAUDE.md). Bonds don't
    # separately track a realized/dividend component, so PROFIT_WITHOUT_DIVIDEND_COLUMN and
    # PROFIT_COLUMN are always identical. ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'

    # --- Input: columns read from buy.csv / interest_rate.csv / inflation_rate.csv. ---
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_ADDITIONAL_COUPON_COLUMN='additional_coupon'
    CSV_INITIAL_COUPON_COLUMN='initial_coupon'
    CSV_IS_SWAPPED_COLUMN='is_swapped'
    CSV_INTEREST_RATE_COLUMN='rate'
    CSV_INFLATION_COLUMN='inflation'

    NOMINAL_VALUE=100.0  # zł per bond, every type (every list emisyjny's ust. 2)
    TAX_RATE=19.0  # % 'podatek Belki' on interest income — see module docstring: not sourced from
                   # the listy emisyjne (they don't set tax law), asserted as one uniform rate

    # One entry per bond-type code (the CSV isin/code's first three letters — e.g. 'ROR' out of
    # 'ROR0927'). period_months/num_periods: length of one interest period and the bond's full
    # term in periods (period_months*num_periods = the term listed in each type's own list
    # emisyjny — e.g. ROR = 1*12 = 12 months). compounding: see module docstring's two accrual
    # shapes. rate_source: None — every period reuses CSV_INITIAL_COUPON_COLUMN unchanged (only
    # TOS: truly fixed-for-life); 'interest'/'inflation' — only period 1 uses
    # CSV_INITIAL_COUPON_COLUMN, every later period looks up interest_rate_data/
    # inflation_rate_data and adds CSV_ADDITIONAL_COUPON_COLUMN as that period's margin.
    # swap_discount: zł/bond subtracted from NOMINAL_VALUE when CSV_IS_SWAPPED_COLUMN is set —
    # the 'cena zamiany' discount for a bond bought by exchanging a maturing predecessor's
    # redemption proceeds instead of paying cash. 0.0 where a type's list emisyjny either prices
    # that exchange at par (OTS) or doesn't offer one at all (ROS/ROD — family bonds restricted
    # to child-benefit recipients, not tradable or exchangeable; see their own list emisyjny's
    # absence of a 'zamiana' section, unlike every other type here).
    BOND_TYPES={
        'OTS': dict(period_months=3,  num_periods=1,  compounding=False, rate_source=None,       swap_discount=0.0),
        'ROR': dict(period_months=1,  num_periods=12, compounding=False, rate_source='interest',  swap_discount=0.10),
        'DOR': dict(period_months=1,  num_periods=24, compounding=False, rate_source='interest',  swap_discount=0.10),
        'TOS': dict(period_months=12, num_periods=3,  compounding=True,  rate_source=None,        swap_discount=0.10),
        'COI': dict(period_months=12, num_periods=4,  compounding=False, rate_source='inflation', swap_discount=0.10),
        'ROS': dict(period_months=12, num_periods=6,  compounding=True,  rate_source='inflation', swap_discount=0.0),
        'EDO': dict(period_months=12, num_periods=10, compounding=True,  rate_source='inflation', swap_discount=0.10),
        'ROD': dict(period_months=12, num_periods=12, compounding=True,  rate_source='inflation', swap_discount=0.0),
    }

    def __init__(self, dataframe: str, interest_rate_file: str='interest_rate.csv', inflation_rate_file: str='inflation_rate.csv', progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False):
        """dataframe: raw bonds transactions dataframe, one row per bond holding, with columns
        date, isin (the bond code, e.g. 'ROR0927' — its first three letters select the type, see
        BOND_TYPES), amount_of_units, additional_coupon, initial_coupon, is_swapped.
        interest_rate_file/inflation_rate_file: CSVs used respectively by 'interest'/'inflation'
        rate_source types (see BOND_TYPES), resolved relative to dataframe (the bonds source
        directory) — pass an absolute path instead to point elsewhere. progress_callback:
        optional zero-arg callback invoked once, after all bond rows have been computed, for a
        caller (e.g. Portfolio) tracking overall progress.
        cache_dir: optional directory to cache the fully computed bonds data in (both self.data
        and the per-bond-type breakdown behind distribution_by_ticker — see _compute_data),
        keyed by the content of buy.csv/interest_rate_file/inflation_rate_file and valid for the
        day it was written — see cache_library.DiskCache.
        force_refresh: when True (and cache_dir is set), ignores any cached entry and
        recomputes everything, then overwrites the cache with the fresh result."""
        today=datetime.today()
        interest_rate_path=os.path.join(dataframe, interest_rate_file)
        inflation_rate_path=os.path.join(dataframe, inflation_rate_file)
        buy_path=os.path.join(dataframe, "buy.csv")

        self.dataframe=pd.read_csv(buy_path)
        self.dataframe.index=pd.to_datetime(self.dataframe[self.CSV_DATE_COLUMN], format='%Y-%m-%d')
        self.dataframe.drop([self.CSV_DATE_COLUMN], axis=1, inplace=True)
        self.interest_rate_data=self._load_rate_file(interest_rate_path, '%m-%Y', today)
        self.inflation_rate_data=self._load_rate_file(inflation_rate_path, '%m-%Y', today)

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        cached=None
        if cache is not None:
            cache_key=DiskCache.make_key('bonds', DiskCache.hash_file(buy_path), DiskCache.hash_file(interest_rate_path), DiskCache.hash_file(inflation_rate_path))
            if not force_refresh:
                cached=cache.get(cache_key)

        if cached is not None:
            self.data, type_dataframes=cached
            if progress_callback is not None:
                progress_callback()
        else:
            self.data, type_dataframes=self._compute_data(today, progress_callback)
            if cache is not None:
                cache.set(cache_key, (self.data, type_dataframes))
        self.total_money_invested=self.data[self.MONEY_INVESTED_COLUMN].iloc[-1]
        self.total_current_value=self.total_money_invested+self.data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
        self.total_revenue=self.data[self.PROFIT_COLUMN].iloc[-1]

        # Broken down by bond type (its three-letter code, e.g. 'ROR') rather than one flat
        # 'Polish bonds' bucket, the same way Stock/Commodity/Crypto break distribution_by_ticker
        # down by ticker/symbol - merging per type first (type_dataframes below), not per holding,
        # so two separate holdings of the same type (e.g. two different ROR issues) land in one
        # slice instead of two.
        self.distribution_by_ticker=dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        for code, type_dataframe in type_dataframes.items():
            money_invested=type_dataframe[self.MONEY_INVESTED_COLUMN].iloc[-1]
            current_value=money_invested+type_dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            revenue=type_dataframe[self.PROFIT_COLUMN].iloc[-1]
            self.distribution_by_ticker[code]=(money_invested/self.total_money_invested)*100.0
            self.distribution_by_ticker_current_value[code]=(current_value/self.total_current_value)*100.0
            self.distribution_by_ticker_revenue[code]=(revenue/self.total_revenue)*100.0

    @staticmethod
    def count_bonds(directory_path: str) -> int:
        """Number of bond rows in a source directory's buy.csv — lets a caller (e.g. Portfolio)
        size a progress bar before construction."""
        return len(pd.read_csv(os.path.join(directory_path, "buy.csv")))

    @classmethod
    def _load_rate_file(cls, path: str, date_format: str, today: datetime) -> pd.DataFrame:
        rate_df=pd.read_csv(path)
        rate_df[cls.CSV_DATE_COLUMN]=pd.to_datetime(rate_df[cls.CSV_DATE_COLUMN], format=date_format)
        rate_df=rate_df.set_index(cls.CSV_DATE_COLUMN)
        all_days=pd.DataFrame({}, index=pd.date_range(start=rate_df.index.min(), end=today, freq='D'))
        return all_days.join(rate_df).ffill()

    def _compute_data(self, today: datetime, progress_callback: Callable[[], None]=None) -> tuple:
        """Returns (merged_dataframe, type_dataframes) — the whole-portfolio DataFrame (self.data)
        plus a dict of {bond-type code: merged DataFrame} for that type alone, one entry per
        distinct type actually held, used to compute distribution_by_ticker(_current_value/
        _revenue) in __init__. Both are cached together (see __init__) so a cache hit doesn't
        lose the per-type breakdown."""
        # See Stock._compute_data's equivalent comment on why plain numpy arrays are pulled out
        # up front. Here it matters less — this loop runs once per bond HOLDING (typically a
        # handful, not hundreds), and the real per-iteration cost is the vectorized-per-period
        # accrual math inside _bond_dataframe, not the row access itself.
        codes=self.dataframe[self.CSV_TICKER_COLUMN].str[:3].to_numpy()
        raw_codes=self.dataframe[self.CSV_TICKER_COLUMN].to_numpy()
        amounts=self.dataframe[self.CSV_AMOUNT_OF_UNITS_COLUMN].to_numpy()
        is_swapped_values=self.dataframe[self.CSV_IS_SWAPPED_COLUMN].to_numpy()
        additional_coupons=self.dataframe[self.CSV_ADDITIONAL_COUPON_COLUMN].to_numpy()
        initial_coupons=self.dataframe[self.CSV_INITIAL_COUPON_COLUMN].to_numpy()
        dates=self.dataframe.index

        bonds_by_type=dict()
        for i in range(len(self.dataframe)):
            code=codes[i]
            if code not in self.BOND_TYPES:
                raise ValueError(f"Unknown Polish retail bond code {raw_codes[i]!r}: its type prefix {code!r} isn't one of {sorted(self.BOND_TYPES)}.")
            bond=self._bond_dataframe(code, amounts[i], initial_coupons[i], additional_coupons[i], dates[i], today, bool(is_swapped_values[i]))
            bonds_by_type.setdefault(code, list()).append(bond)

        if progress_callback is not None:
            progress_callback()

        type_dataframes={code: self._merge(holdings) for code, holdings in bonds_by_type.items()}
        return self._merge(list(type_dataframes.values())), type_dataframes

    @staticmethod
    def _merge(dataframes: list) -> pd.DataFrame:
        """Sums a list of per-bond DataFrames by date into a single aggregate DataFrame."""
        return pd.concat(dataframes).groupby(level=0, sort=True).sum().ffill()

    def _external_rate(self, source: str, period_start: pd.Timestamp) -> float:
        """The published rate feeding a period-2-onward rate (before that period's own margin is
        added) — interest_rate_data (NBP reference rate) for 'interest', inflation_rate_data (CPI)
        for 'inflation'. Floored at 0 per every list emisyjny's 'w przypadku gdy i<0 przyjmuje się
        że i=0' clause. CPI is looked up a month before period_start (it's published in arrears
        for the prior 12 months); the NBP reference rate is looked up at period_start itself —
        both external rate files only carry monthly-resolution, forward-filled data, coarser than
        each list emisyjny's precise 'Nth business day before' lookup rule, so this takes the
        rate already in effect at the relevant date rather than reproducing that day-count."""
        if source=='interest':
            raw=self.interest_rate_data.loc[period_start, self.CSV_INTEREST_RATE_COLUMN]
        else:
            lookup_date=period_start-pd.DateOffset(months=1)
            raw=self.inflation_rate_data.loc[lookup_date, self.CSV_INFLATION_COLUMN]
        return max(raw, 0.0)

    def _bond_dataframe(self, code: str, amount_of_bonds: float, initial_coupon: float, additional_coupon: float, start_date: pd.Timestamp, today: datetime, is_swapped: bool) -> pd.DataFrame:
        config=self.BOND_TYPES[code]
        period_months=config['period_months']
        num_periods=config['num_periods']
        compounding=config['compounding']
        rate_source=config['rate_source']
        payments_per_year=12//period_months

        end_date=start_date+pd.DateOffset(months=period_months*num_periods)-pd.DateOffset(days=1)
        index=pd.date_range(start=start_date, end=min(end_date, today))

        # Interest always accrues on the bond's full NOMINAL_VALUE, whether bought for cash or
        # (at a discount) by exchange — only the cost basis below differs.
        base=self.NOMINAL_VALUE*amount_of_bonds
        daily_interest=pd.Series(0.0, index=index)

        for period in range(num_periods):
            period_start=start_date+pd.DateOffset(months=period_months*period)
            if period_start>today:
                break
            period_end=start_date+pd.DateOffset(months=period_months*(period+1))
            period_days=(period_end-period_start).days

            rate=initial_coupon if period==0 or rate_source is None else self._external_rate(rate_source, period_start)+additional_coupon

            period_index=pd.date_range(start=period_start, end=min(period_end-pd.DateOffset(days=1), today))
            if len(period_index)==0:
                break
            daily_interest.loc[period_index]=base*rate/100.0/(period_days*payments_per_year)

            if compounding:
                base=base*(1+rate/100.0)

        discount=config['swap_discount'] if is_swapped else 0.0
        price_per_bond=self.NOMINAL_VALUE-discount

        # Redemption value is always based on NOMINAL_VALUE regardless of what was actually paid
        # (see base above), so a lower cost basis (money_invested, below) needs a matching credit
        # here to keep this contract's Money_invested + Profit = current total value true from day
        # one - not just a lump sum tacked onto the final day, as the pre-rework version did it.
        # Untaxed: it's a purchase-price discount, not interest income.
        dataframe=pd.DataFrame(index=index)
        dataframe[self.MONEY_INVESTED_COLUMN]=amount_of_bonds*price_per_bond
        dataframe[self.PROFIT_COLUMN]=round(daily_interest.cumsum()*(1-self.TAX_RATE/100.0), 2)+amount_of_bonds*discount
        dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]=dataframe[self.PROFIT_COLUMN]
        return dataframe
