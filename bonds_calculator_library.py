"""
PolishRetailBonds models Polish retail treasury bonds (obligacje detaliczne) - bought directly
from the Treasury, no secondary market or observable price, only redeemable early at a fixed
penalty rather than sold. That's why this class looks nothing like Stock/Commodity/Crypto: no
yfinance fetch, just each bond type's own accrual formula keyed off the bond code's three-letter
prefix. Every bond is issued in PLN (NOMINAL_VALUE); currency_to converts via Currency, the same
way Stock/Commodity/Crypto convert their own native-currency prices.

Each day's own accrued interest is converted at THAT day's own FX rate before accumulating (see
_bond_dataframe) - same convention Stock uses for dividends/realized profit. That's what keeps a
matured bond's frozen Profit frozen in currency_to terms too, instead of drifting with FX after
redemption.

The accrual rules below (period length/count, compounding vs. flat payout, rate source, and the
'cena zamiany' exchange discount) are transcribed from the Ministry of Finance's own listy
emisyjne (emission letters) for one real issuance of each of the eight bond types currently
sold, supplied for review as bonds_lists/*.pdf (not bundled with this repo - see README). Every
one of those eight follows one of two accrual shapes:

- "flat": each period's interest is paid out at the period's end, off the bond's fixed
  NOMINAL_VALUE (not compounded) - O = N*r/100*a/(D*F), where r is that period's annual rate, a
  is days elapsed in the period so far, D is days in the period, F is periods/year. ROR, DOR,
  COI use this shape.
- "compounding": interest is only ever paid at final redemption, each period's base is the
  previous period's base plus its own interest - W = N*(1+r_1)*(1+r_2)*...*(1+r_k). TOS, ROS,
  EDO, ROD use this shape; day-to-day, the currently-accruing period still adds a flat per-day
  amount, same as "flat" - only the base each period starts from differs.

OTS (a single 3-month period, always at its CSV_INITIAL_COUPON_COLUMN rate) is a degenerate case
of "flat" with only one period.

Every bond type's first-period rate comes from CSV_INITIAL_COUPON_COLUMN - a promotional rate
fixed at issuance. TOS reuses it for every later period too (fixed-for-life); every other
multi-period type looks up interest_rate_data (ROR/DOR, NBP reference rate) or
inflation_rate_data (COI/ROS/EDO/ROD, CPI) for each later period's base rate plus
CSV_ADDITIONAL_COUPON_COLUMN as margin - both quoted per-issuance, so both belong in the CSV row.

Withholding tax isn't an issuance term, so it's asserted here as one flat rate applied uniformly,
defaulting to TAX_RATE (19%, 'podatek Belki') but overridable via tax_rate - e.g. 0.0 for a
tax-exempt account (IKE/IKZE).

cancel.csv (optional) records that some or all of a holding was ACTUALLY redeemed early - a
holding is identified by its own (date, isin) pair, and each row also carries amount_of_units
(not necessarily the whole holding - see _build_tranches). Every unit accrues identically
regardless of amount (the formulas below are linear in amount_of_bonds), so a partial
cancellation is modeled by splitting a holding into tranches - one per cancellation plus a final
tranche for whatever's never cancelled - summed back together. A holding can appear more than
once in cancel.csv; tranches are built in cancel_date order regardless of file order.

A cancelled tranche's frozen Profit isn't just the plain held-to-maturity accrual: real early
redemption (przedterminowy wykup) pays gross accrued interest minus a per-bond redemption fee
(BOND_TYPES' early_redemption_fee), applied once on cancel_date, floored at 0 (never eats into
principal). OTS forfeits ALL interest accrued that period instead of a flat zł fee - modeled as
early_redemption_fee=inf, reduced to 0 by the same floor formula. Every other type's fee is a
typical zł/bond figure, not transcribed from a specific issuance - treat it like swap_discount:
worth double-checking against an authoritative source, overridable via bond_types_json.
"""

import os
import json
from typing import Callable
import numpy as np
import pandas as pd
from datetime import datetime
from .currency_calculator_library import Currency, get_cached_currency
from .cache_library import DiskCache
from .calculator_mixins import ReprMixin, MergeMixin


class PolishRetailBonds(MergeMixin, ReprMixin):
    # --- Output: self.data columns. First three: shared contract (see CLAUDE.md).
    # PROFIT_WITHOUT_DIVIDEND_COLUMN and PROFIT_COLUMN track together while held, but diverge at
    # maturity: PROFIT_WITHOUT_DIVIDEND_COLUMN (unrealized) drops to 0, PROFIT_COLUMN (realized)
    # freezes at its final value - see _bond_dataframe. REALIZED_PROFIT_COLUMN is derived from
    # those two, not accrued independently. No DIVIDEND_COLUMN - bonds pay no real per-payment
    # dividend, matching Commodity/Crypto's precedent of not faking one. ---
    MONEY_INVESTED_COLUMN='Money_invested'
    PROFIT_WITHOUT_DIVIDEND_COLUMN='Profit_without_dividends'
    PROFIT_COLUMN='Profit'
    # Bond interest/redemption proceeds, matching Stock/Commodity/Crypto's Realized_profit: 0
    # while held, then jumps once - at maturity/cancellation - to everything ever accrued
    # (interest plus, if is_swapped, the swap discount) and freezes, mirroring real cash flow:
    # nothing paid until redemption, then it all is, recognized once per event. See
    # _bond_dataframe.
    REALIZED_PROFIT_COLUMN='Realized_profit'
    # Excludes gain already locked in (REALIZED_PROFIT_COLUMN) - equals PROFIT_WITHOUT_DIVIDEND_
    # COLUMN, no real dividend to add back - the "no dividends" pattern Commodity/Crypto use.
    PROFIT_WITHOUT_REALIZED_COLUMN='Profit_without_realized'
    # Excludes only dividends, keeping realized profit - equals PROFIT_COLUMN, no real dividend
    # to exclude.
    PROFIT_EXCLUDING_DIVIDEND_COLUMN='Profit_excluding_dividends'

    # --- Input: columns read from buy.csv / interest_rate.csv / inflation_rate.csv / cancel.csv. ---
    CSV_TICKER_COLUMN='isin'
    CSV_DATE_COLUMN='date'
    CSV_AMOUNT_OF_UNITS_COLUMN='amount_of_units'
    CSV_ADDITIONAL_COUPON_COLUMN='additional_coupon'
    CSV_INITIAL_COUPON_COLUMN='initial_coupon'
    CSV_IS_SWAPPED_COLUMN='is_swapped'
    CSV_INTEREST_RATE_COLUMN='rate'
    CSV_INFLATION_COLUMN='inflation'
    CSV_CANCEL_DATE_COLUMN='cancel_date'

    NOMINAL_VALUE=100.0  # zł per bond, every type (every list emisyjny's ust. 2)
    NATIVE_CURRENCY='PLN'  # every bond is issued in PLN — see module docstring
    TAX_RATE=19.0  # % 'podatek Belki', asserted per module docstring. Default for the tax_rate
                   # constructor arg - self.tax_rate is what accrual actually uses.

    # One entry per bond-type code (isin's first three letters, e.g. 'ROR' out of 'ROR0927').
    # period_months/num_periods: one period's length and the bond's term in periods (e.g. ROR =
    # 1*12 = 12 months). compounding: see module docstring's two accrual shapes. rate_source:
    # None - every period reuses CSV_INITIAL_COUPON_COLUMN (only TOS); 'interest'/'inflation' -
    # only period 1 uses it, later periods look up interest_rate_data/inflation_rate_data plus
    # CSV_ADDITIONAL_COUPON_COLUMN as margin.
    # swap_discount: zł/bond subtracted from NOMINAL_VALUE when CSV_IS_SWAPPED_COLUMN is set -
    # the 'cena zamiany' discount for buying by exchange instead of cash. 0.0 where a type prices
    # exchange at par (OTS) or doesn't offer one (ROS/ROD - family bonds, not exchangeable).
    # early_redemption_fee: zł/bond subtracted from a cancelled tranche's gross accrued interest
    # on cancel_date - float('inf') for OTS encodes "forfeit all interest this period" via the
    # same floor-at-0 formula every other type uses.
    BOND_TYPES={
        'OTS': dict(period_months=3,  num_periods=1,  compounding=False, rate_source=None,       swap_discount=0.0,  early_redemption_fee=float('inf')),
        'ROR': dict(period_months=1,  num_periods=12, compounding=False, rate_source='interest',  swap_discount=0.10, early_redemption_fee=0.50),
        'DOR': dict(period_months=1,  num_periods=24, compounding=False, rate_source='interest',  swap_discount=0.10, early_redemption_fee=0.70),
        'TOS': dict(period_months=12, num_periods=3,  compounding=True,  rate_source=None,        swap_discount=0.10, early_redemption_fee=1.00),
        'COI': dict(period_months=12, num_periods=4,  compounding=False, rate_source='inflation', swap_discount=0.10, early_redemption_fee=2.00),
        'ROS': dict(period_months=12, num_periods=6,  compounding=True,  rate_source='inflation', swap_discount=0.0,  early_redemption_fee=2.00),
        'EDO': dict(period_months=12, num_periods=10, compounding=True,  rate_source='inflation', swap_discount=0.10, early_redemption_fee=3.00),
        'ROD': dict(period_months=12, num_periods=12, compounding=True,  rate_source='inflation', swap_discount=0.0,  early_redemption_fee=3.00),
    }

    def __init__(self, directory_path: str, currency_to: str=NATIVE_CURRENCY, interest_rate_file: str='interest_rate.csv', inflation_rate_file: str='inflation_rate.csv', tax_rate: float=TAX_RATE, bond_types_json: str=None, progress_callback: Callable[[], None]=None, cache_dir: str=None, force_refresh: bool=False, currency_cache: dict=None):
        """directory_path: buy.csv, one row per bond holding - date, isin (e.g. 'ROR0927', first
        three letters select the type, see BOND_TYPES), amount_of_units, additional_coupon,
        initial_coupon, is_swapped.
        currency_to: target currency every bond's PLN values are converted to - defaults to
        NATIVE_CURRENCY ('PLN'), a no-op via Currency's own same-currency short-circuit.
        interest_rate_file/inflation_rate_file: CSVs for 'interest'/'inflation' rate_source
        types (see BOND_TYPES), resolved relative to directory_path.
        tax_rate: % withholding tax applied uniformly - defaults to TAX_RATE (19%, 'podatek
        Belki'). Override for a situation the flat default doesn't fit, e.g. tax_rate=0.0 for a
        tax-exempt account (IKE/IKZE).
        bond_types_json: optional {code: {"swap_discount": <float>, "early_redemption_fee":
        <float>}} merged on top of BOND_TYPES's own values per type, for a real issuance where
        the built-in default doesn't hold. Either field may be omitted; an unknown code or field
        raises ValueError. Doesn't touch the taxonomy itself (period_months/num_periods/
        compounding/rate_source are issuance facts, not assertions). None: self.bond_types is
        exactly BOND_TYPES.
        progress_callback: optional zero-arg callback, once, after all bond rows are computed.
        cancel.csv (optional): records that some or all of a holding was actually redeemed early
        - see the module docstring for its shape and how a partial cancellation is modeled.
        cache_dir: caches the fully computed bonds data, keyed by currency_to/tax_rate/
        bond_types_json plus buy.csv/interest_rate_file/inflation_rate_file/cancel.csv content,
        valid for the day written.
        force_refresh: ignores any cached entry, recomputes, overwrites the cache.
        currency_cache: optional dict shared across sources (Portfolio passes one automatically)
        so this instance's NATIVE_CURRENCY -> currency_to history is reused instead of re-fetched
        - see get_cached_currency. None: fetched fresh."""
        # Validated up front, before any file I/O, so a malformed override raises a clear error
        # at construction time instead of a cryptic failure deep inside _bond_dataframe.
        if isinstance(tax_rate, bool) or not isinstance(tax_rate, (int, float)):
            raise ValueError(f"tax_rate must be a number (percent), got {tax_rate!r}.")
        if not (0.0<=tax_rate<=100.0):
            raise ValueError(f"tax_rate must be between 0 and 100 (percent), got {tax_rate!r}.")

        self.tax_rate=tax_rate
        self.bond_types=self._load_bond_types(bond_types_json)
        today=datetime.today()
        buy_path=self._resolve_buy_path(directory_path)
        interest_rate_path=os.path.join(directory_path, interest_rate_file)
        inflation_rate_path=os.path.join(directory_path, inflation_rate_file)
        cancel_path=os.path.join(directory_path, "cancel.csv")

        self.dataframe=pd.read_csv(buy_path)
        self.dataframe.index=pd.to_datetime(self.dataframe[self.CSV_DATE_COLUMN], format='%Y-%m-%d')
        self.dataframe.drop([self.CSV_DATE_COLUMN], axis=1, inplace=True)
        self.interest_rate_data=self._load_rate_file(interest_rate_path, '%m-%Y', today)
        self.inflation_rate_data=self._load_rate_file(inflation_rate_path, '%m-%Y', today)
        self.cancellations=self._load_cancellations(cancel_path) if os.path.exists(cancel_path) else dict()

        cache=DiskCache(cache_dir) if cache_dir else None
        cache_key=None
        cached=None
        if cache is not None:
            # Version tag - the isinstance check below is the actual guard against an old-shaped
            # entry. currency_to/tax_rate are folded into the key itself (not just this tag)
            # since two different values are both otherwise-valid, simultaneously-live entries
            # for the same buy.csv - not a stale-vs-fresh case. cancel.csv/bond_types_json are
            # folded in the same way, sentinel string when absent.
            cancel_component=DiskCache.hash_file(cancel_path) if os.path.exists(cancel_path) else 'no-cancellations'
            bond_types_component=DiskCache.hash_file(bond_types_json) if bond_types_json is not None else 'default-bond-types'
            cache_key=DiskCache.make_key('bonds-v7', currency_to.upper(), tax_rate, bond_types_component, DiskCache.hash_file(buy_path), DiskCache.hash_file(interest_rate_path), DiskCache.hash_file(inflation_rate_path), cancel_component)
            if not force_refresh:
                cached=cache.get(cache_key)
                if cached is not None:
                    # Explicit shape check, not bare unpacking in try/except: a stale entry that's
                    # some other 4-shaped value would unpack without raising, silently assigning
                    # garbage - safer to recompute than to crash or misinterpret it.
                    if isinstance(cached, tuple) and len(cached)==4:
                        self.data, type_dataframes, invested_by_type, lifetime_invested_by_type=cached
                    else:
                        cached=None

        if cached is None:
            # One shared FX history for every holding, spanning the earliest start date through
            # today - a flat 1.0-rate no-op when currency_to is already NATIVE_CURRENCY, so
            # _compute_data/_bond_dataframe apply conversion unconditionally. Routed through
            # get_cached_currency so this pair is reused when shared with another source.
            currency=get_cached_currency(currency_cache, self.NATIVE_CURRENCY, currency_to, self.dataframe.index.min(), cache_dir=cache_dir, force_refresh=force_refresh)
            self.data, type_dataframes, invested_by_type, lifetime_invested_by_type=self._compute_data(today, currency, progress_callback)
            if cache is not None:
                cache.set(cache_key, (self.data, type_dataframes, invested_by_type, lifetime_invested_by_type))
        elif progress_callback is not None:
            progress_callback()

        # total_money_invested/distribution_by_ticker: lifetime-gross figure - every holding/
        # tranche's own cost basis at purchase, summed by type, never reduced by maturity/
        # cancellation (lifetime_invested_by_type) - matches Stock's own meaning.
        # total_money_currently_invested/distribution_by_ticker_currently_invested: the separate
        # figure of what's CURRENTLY held per type (invested_by_type, each type's own last
        # MONEY_INVESTED_COLUMN row) - 0 for a fully matured/cancelled type.
        # total_current_value/total_revenue read self.data's last row directly: Money_invested/
        # PROFIT_WITHOUT_DIVIDEND_COLUMN both 0 past maturity, PROFIT_COLUMN persists.
        self.total_money_invested=sum(lifetime_invested_by_type.values())
        self.total_money_currently_invested=sum(invested_by_type.values())
        self.total_current_value=self.data[self.MONEY_INVESTED_COLUMN].iloc[-1]+self.data[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
        self.total_revenue=self.data[self.PROFIT_COLUMN].iloc[-1]

        # Broken down by bond type (e.g. 'ROR'), not one flat bucket, same as Stock breaks down
        # by ticker - merging per type (type_dataframes) so two holdings of the same type land in
        # one slice. distribution_by_ticker keeps a fully-matured type's lifetime share;
        # _currently_invested drops it to 0% instead; _revenue keeps whatever was realized.
        self.distribution_by_ticker={code: (invested/self.total_money_invested)*100.0 for code, invested in lifetime_invested_by_type.items()} if self.total_money_invested else dict()
        self.distribution_by_ticker_currently_invested={code: (invested/self.total_money_currently_invested)*100.0 for code, invested in invested_by_type.items()} if self.total_money_currently_invested else dict()
        self.distribution_by_ticker_current_value=dict()
        self.distribution_by_ticker_revenue=dict()
        # Trivial (every type is issued in NATIVE_CURRENCY, unlike Stock's per-ticker currency) -
        # kept as a dict anyway so Portfolio's distribution_by_currency has one uniform shape.
        self.currency_by_ticker={code: self.NATIVE_CURRENCY for code in invested_by_type}
        for code, type_dataframe in type_dataframes.items():
            current_value=type_dataframe[self.MONEY_INVESTED_COLUMN].iloc[-1]+type_dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]
            revenue=type_dataframe[self.PROFIT_COLUMN].iloc[-1]
            self.distribution_by_ticker_current_value[code]=(current_value/self.total_current_value)*100.0 if self.total_current_value else 0.0
            self.distribution_by_ticker_revenue[code]=(revenue/self.total_revenue)*100.0 if self.total_revenue else 0.0

    @classmethod
    def count_tickers(cls, directory_path: str) -> int:
        """Number of bond rows in buy.csv - lets a caller size a progress bar before
        construction. Named count_tickers, matching Stock/Commodity/Crypto's equivalent."""
        return len(pd.read_csv(cls._resolve_buy_path(directory_path)))

    @classmethod
    def _resolve_buy_path(cls, directory_path: str) -> str:
        """Validates directory_path/buy.csv exist, raising this library's own clear-error
        ValueError instead of a raw FileNotFoundError - shared by __init__ and count_tickers."""
        if not os.path.isdir(directory_path):
            raise ValueError(f"No such directory: {directory_path!r}")
        buy_path=os.path.join(directory_path, "buy.csv")
        if not os.path.exists(buy_path):
            raise ValueError(f"No buy.csv found in {directory_path!r}")
        return buy_path

    @classmethod
    def _load_bond_types(cls, bond_types_json: str=None) -> dict:
        """{code: config-dict}, a fresh per-type copy of BOND_TYPES (never mutates the class-
        level dict) with bond_types_json's overrides merged on top - see __init__'s docstring."""
        bond_types={code: dict(config) for code, config in cls.BOND_TYPES.items()}
        if bond_types_json is not None:
            with open(bond_types_json, 'r') as f:
                overrides=json.load(f)
            if not isinstance(overrides, dict):
                raise ValueError(f"bond_types_json must contain a JSON object of {{code: {{field: value}}}}, got {type(overrides).__name__}.")
            overridable_fields={'swap_discount', 'early_redemption_fee'}
            for code, override in overrides.items():
                if code not in bond_types:
                    raise ValueError(f"bond_types_json overrides unknown bond type {code!r} (expected one of {sorted(bond_types)}).")
                if not isinstance(override, dict):
                    raise ValueError(f"bond_types_json for {code!r} must be a JSON object of {{field: value}}, got {type(override).__name__}.")
                unknown_fields=set(override)-overridable_fields
                if unknown_fields:
                    raise ValueError(f"bond_types_json for {code!r} sets unknown field(s) {sorted(unknown_fields)} (expected one of {sorted(overridable_fields)}).")
                # A non-numeric or out-of-range value would otherwise only surface as a cryptic
                # failure (or a silently nonsensical negative price/fee) deep in _bond_dataframe.
                for field, value in override.items():
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError(f"bond_types_json for {code!r} field {field!r} must be a number, got {value!r}.")
                    if field=='swap_discount' and not (0.0<=value<cls.NOMINAL_VALUE):
                        raise ValueError(f"bond_types_json for {code!r} swap_discount must be within [0, {cls.NOMINAL_VALUE}) zl/bond, got {value!r}.")
                    if field=='early_redemption_fee' and not (value>=0.0):
                        raise ValueError(f"bond_types_json for {code!r} early_redemption_fee must be >= 0 zl/bond, got {value!r}.")
                bond_types[code].update(override)
        return bond_types

    @classmethod
    def _load_rate_file(cls, path: str, date_format: str, today: datetime) -> pd.DataFrame:
        rate_df=pd.read_csv(path)
        rate_df[cls.CSV_DATE_COLUMN]=pd.to_datetime(rate_df[cls.CSV_DATE_COLUMN], format=date_format)
        rate_df=rate_df.set_index(cls.CSV_DATE_COLUMN)
        all_days=pd.DataFrame({}, index=pd.date_range(start=rate_df.index.min(), end=today, freq='D'))
        return all_days.join(rate_df).ffill()

    @classmethod
    def _load_cancellations(cls, path: str) -> dict:
        """{(purchase_date, isin): [(cancel_date, amount_of_units), ...]}, sorted by cancel_date -
        every cancel.csv row grouped by the holding it applies to (its own (date, isin) pair). A
        holding can have more than one row (several partial cancellations) - see _build_tranches
        for how these turn into per-tranche accrual."""
        # Vectorized groupby, not a per-row .iterrows() loop. Sorting by cancel_date before
        # grouping means each group's rows already come out in cancel_date order.
        cancel_df=pd.read_csv(path)
        cancel_df[cls.CSV_DATE_COLUMN]=pd.to_datetime(cancel_df[cls.CSV_DATE_COLUMN], format='%Y-%m-%d')
        cancel_df[cls.CSV_CANCEL_DATE_COLUMN]=pd.to_datetime(cancel_df[cls.CSV_CANCEL_DATE_COLUMN], format='%Y-%m-%d')
        cancel_df=cancel_df.sort_values(cls.CSV_CANCEL_DATE_COLUMN, kind='stable')
        cancellations=dict()
        for key, group in cancel_df.groupby([cls.CSV_DATE_COLUMN, cls.CSV_TICKER_COLUMN], sort=False):
            cancellations[key]=list(zip(group[cls.CSV_CANCEL_DATE_COLUMN], group[cls.CSV_AMOUNT_OF_UNITS_COLUMN].astype(float)))
        return cancellations

    @classmethod
    def _build_tranches(cls, total_amount: float, cancellations: list, start_date: pd.Timestamp, isin: str) -> list:
        """Splits total_amount into (tranche_amount, tranche_cancel_date) pairs: one per
        cancellation (in cancel_date order), each stopping accrual at its own cancel_date, plus a
        final (remaining_amount, None) tranche for whatever was never cancelled (omitted if
        cancelled in full)."""
        remaining=total_amount
        cumulative_cancelled=0.0
        tranches=list()
        for cancel_date, amount in cancellations:
            if cancel_date<start_date:
                raise ValueError(f"Cancellation date {cancel_date.date()} for {isin!r} is before its own purchase date {start_date.date()}.")
            cumulative_cancelled+=amount
            if cumulative_cancelled>total_amount+1e-9:
                raise ValueError(f"cancel.csv cancels {cumulative_cancelled} units of {isin!r} (purchased {start_date.date()}), more than the {total_amount} actually held.")
            tranches.append((amount, cancel_date))
            remaining-=amount
        if remaining>1e-9:
            tranches.append((remaining, None))
        return tranches

    def _compute_data(self, today: datetime, currency: Currency, progress_callback: Callable[[], None]=None) -> tuple:
        """Returns (merged_dataframe, type_dataframes, invested_by_type, lifetime_invested_by_type):
        - merged_dataframe: the whole-portfolio DataFrame (self.data).
        - type_dataframes: {bond-type code: merged DataFrame} for that type alone, used for
          distribution_by_ticker_current_value/_revenue.
        - invested_by_type: {code: that type's CURRENT Money_invested}, its last
          MONEY_INVESTED_COLUMN row - 0 for a matured/fully-cancelled type. Feeds
          total_money_currently_invested/distribution_by_ticker_currently_invested.
        - lifetime_invested_by_type: {code: cost basis summed across every holding/tranche ever
          bought}, never reduced by maturity/cancellation - the "lifetime gross" meaning Stock's
          own total_money_invested carries. Accumulated per-holding (see _bond_dataframe's own
          lifetime_invested), not read off a column, since a matured MONEY_INVESTED_COLUMN is
          already 0. Feeds total_money_invested/distribution_by_ticker.
        All four are cached together (see __init__)."""
        # Numpy arrays pulled out up front matters less here than in Stock - this loop runs once
        # per HOLDING (a handful, not hundreds); the real cost is _bond_dataframe's own accrual math.
        codes=self.dataframe[self.CSV_TICKER_COLUMN].str[:3].to_numpy()
        raw_codes=self.dataframe[self.CSV_TICKER_COLUMN].to_numpy()
        amounts=self.dataframe[self.CSV_AMOUNT_OF_UNITS_COLUMN].to_numpy()
        is_swapped_values=self.dataframe[self.CSV_IS_SWAPPED_COLUMN].to_numpy()
        additional_coupons=self.dataframe[self.CSV_ADDITIONAL_COUPON_COLUMN].to_numpy()
        initial_coupons=self.dataframe[self.CSV_INITIAL_COUPON_COLUMN].to_numpy()
        dates=self.dataframe.index

        bonds_by_type=dict()
        invested_by_type=dict()
        lifetime_invested_by_type=dict()
        for i in range(len(self.dataframe)):
            code=codes[i]
            if code not in self.bond_types:
                raise ValueError(f"Unknown Polish retail bond code {raw_codes[i]!r}: its type prefix {code!r} isn't one of {sorted(self.bond_types)}.")
            is_swapped=bool(is_swapped_values[i])

            cancellations=self.cancellations.get((dates[i], raw_codes[i]), [])
            tranches=self._build_tranches(amounts[i], cancellations, dates[i], raw_codes[i])
            tranche_results=[
                self._bond_dataframe(code, tranche_amount, initial_coupons[i], additional_coupons[i], dates[i], today, is_swapped, currency, tranche_cancel_date)
                for tranche_amount, tranche_cancel_date in tranches
            ]
            tranche_dataframes=[df for df, _ in tranche_results]
            # Summing each tranche's own lifetime_invested reconstructs the whole holding's cost
            # basis exactly - price_per_bond/fx_at_purchase are identical across tranches, and
            # tranche amounts sum back to amounts[i].
            lifetime_invested_by_type[code]=lifetime_invested_by_type.get(code, 0.0)+sum(lifetime_invested for _, lifetime_invested in tranche_results)
            bond=tranche_dataframes[0] if len(tranche_dataframes)==1 else self.merge(tranche_dataframes)
            bonds_by_type.setdefault(code, list()).append(bond)

        if progress_callback is not None:
            progress_callback()

        type_dataframes={code: self.merge(holdings) for code, holdings in bonds_by_type.items()}
        for code, value in type_dataframes.items():
            invested_by_type[code]=value[self.MONEY_INVESTED_COLUMN].iloc[-1]
        return self.merge(list(type_dataframes.values())), type_dataframes, invested_by_type, lifetime_invested_by_type

    def _external_rate(self, source: str, period_start: pd.Timestamp) -> float:
        """The published rate feeding a period-2-onward rate, before that period's own margin -
        interest_rate_data (NBP reference rate) for 'interest', inflation_rate_data (CPI) for
        'inflation'. Floored at 0 per every list emisyjny's 'i<0 -> i=0' clause. CPI is looked up
        a month before period_start (published in arrears); the NBP rate at period_start itself -
        both files are monthly-resolution, forward-filled, coarser than each list emisyjny's
        precise lookup rule, so this takes the rate already in effect rather than the exact day."""
        if source=='interest':
            raw=self.interest_rate_data.loc[period_start, self.CSV_INTEREST_RATE_COLUMN]
        else:
            lookup_date=period_start-pd.DateOffset(months=1)
            raw=self.inflation_rate_data.loc[lookup_date, self.CSV_INFLATION_COLUMN]
        return max(raw, 0.0)

    def _bond_dataframe(self, code: str, amount_of_bonds: float, initial_coupon: float, additional_coupon: float, start_date: pd.Timestamp, today: datetime, is_swapped: bool, currency: Currency, cancel_date: pd.Timestamp=None) -> tuple:
        config=self.bond_types[code]
        period_months=config['period_months']
        num_periods=config['num_periods']
        compounding=config['compounding']
        rate_source=config['rate_source']
        payments_per_year=12//period_months

        # accrual_cutoff, not today, bounds how far this holding actually accrued - differs from
        # today only when cancelled before today, in which case accrual stops at cancel_date.
        # full_index below still spans to the real today, so post-cancellation days zero
        # out/freeze via the same mechanics that handle natural maturity - cancellation is just
        # an earlier "effective maturity".
        accrual_cutoff=min(today, cancel_date) if cancel_date is not None else today

        # end_date/last_accrual_date/n_days work on plain numpy arrays addressed by integer
        # day-offset from start_date (every date in play is a whole number of days from it) -
        # avoids a fresh pd.date_range and label-based .loc alignment per period, the hot part of
        # this method.
        end_date=start_date+pd.DateOffset(months=period_months*num_periods)-pd.DateOffset(days=1)
        last_accrual_date=min(end_date, accrual_cutoff)
        n_days=(last_accrual_date-start_date).days+1

        # Interest always accrues on the bond's full NOMINAL_VALUE, whether bought for cash or
        # at a discount by exchange - only the cost basis below differs.
        base=self.NOMINAL_VALUE*amount_of_bonds
        daily_interest=np.zeros(n_days)

        for period in range(num_periods):
            period_start=start_date+pd.DateOffset(months=period_months*period)
            if period_start>accrual_cutoff:
                break
            period_end=start_date+pd.DateOffset(months=period_months*(period+1))
            period_days=(period_end-period_start).days

            rate=initial_coupon if period==0 or rate_source is None else self._external_rate(rate_source, period_start)+additional_coupon

            start_offset=(period_start-start_date).days
            end_offset=min((period_end-start_date).days, n_days)
            if end_offset<=start_offset:
                break
            daily_interest[start_offset:end_offset]=base*rate/100.0/(period_days*payments_per_year)

            if compounding:
                base=base*(1+rate/100.0)

        discount=config['swap_discount'] if is_swapped else 0.0
        price_per_bond=self.NOMINAL_VALUE-discount
        # Frozen at the historical rate on this holding's own purchase date, same as Stock's own
        # Money_invested - converted once, at acquisition, not re-marked to today's rate (a bond
        # has no observable mark-to-market price to begin with).
        fx_at_purchase=currency.data.loc[start_date, Currency.CLOSE_COLUMN]

        # Convert each day's own PLN interest to currency_to at THAT day's own FX rate before
        # accumulating - same convention Stock uses for dividends/realized profit. This is what
        # keeps a matured bond's frozen accrued_profit frozen in currency_to terms too, instead
        # of drifting with FX after redemption. .to_numpy() pulls this holding's FX slice out
        # once so the multiply/cumsum below are numpy end to end.
        maturity_index=pd.date_range(start=start_date, periods=n_days)
        fx_rates=currency.data.loc[maturity_index, Currency.CLOSE_COLUMN].to_numpy()
        daily_interest=daily_interest*fx_rates

        # Redemption value is always based on NOMINAL_VALUE regardless of what was actually paid,
        # so a lower cost basis (money_invested, below) needs a matching credit here to keep
        # Money_invested + Profit = current total value true up to maturity. Untaxed: it's a
        # purchase-price discount, not interest income. Converted at fx_at_purchase, same "locked
        # in when it happened" reason as Money_invested below.
        accrued_profit=np.round(np.cumsum(daily_interest)*(1-self.tax_rate/100.0), 2)+amount_of_bonds*discount*fx_at_purchase

        # A genuine early redemption pays gross accrued interest minus early_redemption_fee, not
        # the plain held-to-maturity accrual - applied once, to the frozen value every day from
        # cancel_date onward inherits. Floored at 0, so this only reduces interest, never
        # principal. last_accrual_date==cancel_date (not just "is not None") confirms the
        # cancellation actually governed this tranche's cutoff, not natural maturity/today.
        if cancel_date is not None and last_accrual_date==cancel_date:
            fee=config['early_redemption_fee']*amount_of_bonds*currency.data.loc[cancel_date, Currency.CLOSE_COLUMN]
            accrued_profit[-1]=round(max(0.0, accrued_profit[-1]-fee), 2)

        # Extend to today - a no-op if not yet matured/cancelled. Past accrual_cutoff, the bond
        # has been redeemed: Money_invested and PROFIT_WITHOUT_DIVIDEND_COLUMN drop to 0 (nothing
        # left held, proceeds became cash, not separately tracked), while Profit freezes at its
        # final value forever. Mirrors a fully-sold Stock position. n_days is always <= full_days,
        # so every slice below is safe.
        full_index=pd.date_range(start=start_date, end=today)
        full_days=len(full_index)
        money_invested=np.zeros(full_days)
        money_invested[:n_days]=amount_of_bonds*price_per_bond*fx_at_purchase
        profit_without_dividend=np.zeros(full_days)
        profit_without_dividend[:n_days]=accrued_profit
        profit=np.empty(full_days)
        profit[:n_days]=accrued_profit
        profit[n_days:]=accrued_profit[-1]

        dataframe=pd.DataFrame({
            self.MONEY_INVESTED_COLUMN: money_invested,
            self.PROFIT_WITHOUT_DIVIDEND_COLUMN: profit_without_dividend,
            self.PROFIT_COLUMN: profit,
        }, index=full_index)
        # Derived, not accrued independently: 0 while PROFIT_WITHOUT_DIVIDEND_COLUMN still
        # carries the fluctuating accrued value, then whatever it just dropped the moment it
        # hits 0 - always consistent with PROFIT_COLUMN=PROFIT_WITHOUT_DIVIDEND_COLUMN+
        # REALIZED_PROFIT_COLUMN. PROFIT_WITHOUT_REALIZED_COLUMN/PROFIT_EXCLUDING_DIVIDEND_COLUMN
        # follow Commodity/Crypto's "no dividends" pattern, not Stock's three-term one.
        dataframe[self.REALIZED_PROFIT_COLUMN]=dataframe[self.PROFIT_COLUMN]-dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]
        dataframe[self.PROFIT_WITHOUT_REALIZED_COLUMN]=dataframe[self.PROFIT_WITHOUT_DIVIDEND_COLUMN]
        dataframe[self.PROFIT_EXCLUDING_DIVIDEND_COLUMN]=dataframe[self.PROFIT_COLUMN]

        # This tranche's own cost basis at purchase, untouched by maturity/cancellation zeroing
        # money_invested above - the "lifetime gross" meaning Stock's total_money_invested
        # carries. Returned alongside the DataFrame since summing a partially-cancelled holding's
        # tranches needs this even once MONEY_INVESTED_COLUMN has dropped to 0.
        lifetime_invested=round(amount_of_bonds*price_per_bond*fx_at_purchase, 2)
        return dataframe, lifetime_invested
