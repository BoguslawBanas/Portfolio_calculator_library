"""
Tests for bonds_calculator_library.PolishRetailBonds — no network involved (bonds have no
yfinance-fetchable price, see the module's own docstring), just the accrual formulas keyed off
each bond-type code's three-letter prefix (OTS/ROR/DOR/TOS/COI/ROS/EDO/ROD) plus the
interest_rate.csv/inflation_rate.csv rate history.

Dates are chosen relative to date.today() so every test's expected values are computed
dynamically (via _expected_profit below, a hand-written parallel transcription of each accrual
shape's formula — not a call into PolishRetailBonds itself) rather than hardcoded, so the suite
stays correct regardless of what day it happens to run on. Every rate_row/inflation_row default
is dated far in the past (2020) specifically so ffill carries that single value forward through
whatever calendar months a test's dynamic dates actually touch.
"""

import glob
import os
import pickle
from datetime import date, timedelta

import pandas as pd
import pytest

from Portfolio_calculator_library import PolishRetailBonds


def make_bonds_dir(make_source_dir, buy_csv: str, rate_row: str="01-2020,5.0\n", inflation_row: str="01-2020,4.0\n"):
    return make_source_dir('bonds', {
        'buy.csv': buy_csv,
        'interest_rate.csv': "date,rate\n"+rate_row,
        'inflation_rate.csv': "date,inflation\n"+inflation_row,
    })


def _expected_profit(start, today, code, initial_coupon, additional_coupon, external_rate=0.0, amount=1.0, is_swapped=False):
    """Independent transcription of PolishRetailBonds._bond_dataframe's per-period accrual, used
    to compute an expected Profit without calling into the class under test. external_rate is the
    single ffilled interest_rate.csv/inflation_rate.csv value a rate_source type would see for
    every period-2-onward lookup (both this helper's tests and PolishRetailBonds._external_rate
    resolve it via ffill from one old CSV row, so it's constant regardless of which calendar
    months the test's dynamic dates actually land in)."""
    config=PolishRetailBonds.BOND_TYPES[code]
    start_ts, today_ts=pd.Timestamp(start), pd.Timestamp(today)
    base=PolishRetailBonds.NOMINAL_VALUE*amount
    payments_per_year=12//config['period_months']
    gross=0.0

    for period in range(config['num_periods']):
        period_start=start_ts+pd.DateOffset(months=config['period_months']*period)
        if period_start>today_ts:
            break
        period_end=start_ts+pd.DateOffset(months=config['period_months']*(period+1))
        period_days=(period_end-period_start).days
        rate=initial_coupon if period==0 or config['rate_source'] is None else max(external_rate, 0.0)+additional_coupon

        last_day=min(period_end-pd.DateOffset(days=1), today_ts)
        days_elapsed=(last_day-period_start).days+1
        if days_elapsed<=0:
            break
        gross+=base*rate/100.0*days_elapsed/(period_days*payments_per_year)

        if config['compounding']:
            base=base*(1+rate/100.0)

    return round(gross*(1-PolishRetailBonds.TAX_RATE/100.0), 2)


def test_bond_type_registry_matches_documented_taxonomy():
    """Pins the whole taxonomy transcribed from bonds_lists/*.pdf (the official listy emisyjne,
    supplied for review — see README) in one place, independent of any accrual math."""
    assert PolishRetailBonds.BOND_TYPES=={
        'OTS': dict(period_months=3,  num_periods=1,  compounding=False, rate_source=None,       swap_discount=0.0),
        'ROR': dict(period_months=1,  num_periods=12, compounding=False, rate_source='interest',  swap_discount=0.10),
        'DOR': dict(period_months=1,  num_periods=24, compounding=False, rate_source='interest',  swap_discount=0.10),
        'TOS': dict(period_months=12, num_periods=3,  compounding=True,  rate_source=None,        swap_discount=0.10),
        'COI': dict(period_months=12, num_periods=4,  compounding=False, rate_source='inflation', swap_discount=0.10),
        'ROS': dict(period_months=12, num_periods=6,  compounding=True,  rate_source='inflation', swap_discount=0.0),
        'EDO': dict(period_months=12, num_periods=10, compounding=True,  rate_source='inflation', swap_discount=0.10),
        'ROD': dict(period_months=12, num_periods=12, compounding=True,  rate_source='inflation', swap_discount=0.0),
    }
    # Every type's full term (period_months * num_periods) matches its tenor as named/described
    # in its own list emisyjny.
    terms_in_months={'OTS': 3, 'ROR': 12, 'DOR': 24, 'TOS': 36, 'COI': 48, 'ROS': 72, 'EDO': 120, 'ROD': 144}
    for code, months in terms_in_months.items():
        config=PolishRetailBonds.BOND_TYPES[code]
        assert config['period_months']*config['num_periods']==months, code


def test_ots_single_period_uses_initial_coupon(make_source_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},OTS0826,1,0.0,2.0,False\n"
    ))
    data=PolishRetailBonds(bonds_dir).data

    expected=_expected_profit(start, date.today(), 'OTS', initial_coupon=2.0, additional_coupon=0.0)
    assert data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(100.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)
    assert expected>0.0  # sanity check the helper itself isn't vacuously computing zero


def test_ror_first_period_uses_initial_coupon_ignoring_interest_rate_csv(make_source_dir):
    start=date.today()-timedelta(days=3)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
        ),
        rate_row="01-2020,99.0\n",  # deliberately absurd - proving period 1 never reads this
    )
    data=PolishRetailBonds(bonds_dir).data

    expected=_expected_profit(start, date.today(), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=99.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)
    # If period 1 had used interest_rate.csv (99.0) instead of initial_coupon (4.0), profit would
    # be wildly higher - this pins that it didn't.
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]<1.0


def test_ror_second_period_uses_interest_rate_csv_plus_additional_coupon(make_source_dir):
    start=date.today()-timedelta(days=45)  # safely into period index 1 (the second month) regardless of month lengths
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
    )
    data=PolishRetailBonds(bonds_dir).data

    expected=_expected_profit(start, date.today(), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)
    # Confirms this test actually exercises period 2, not just period 1 again.
    period_1_only=_expected_profit(start, start+timedelta(days=20), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]>period_1_only


def test_coi_first_period_fixed_then_inflation_plus_margin(make_source_dir):
    start=date.today()-timedelta(days=400)  # more than a year ago - into period index 1
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},COI0930,1,1.5,4.75,False\n"
        ),
        inflation_row="01-2020,3.0\n",
    )
    data=PolishRetailBonds(bonds_dir).data

    expected=_expected_profit(start, date.today(), 'COI', initial_coupon=4.75, additional_coupon=1.5, external_rate=3.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)


def test_tos_compounds_across_periods(make_source_dir):
    start=date.today()-timedelta(days=400)  # into period index 1 (year 2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
    ))
    data=PolishRetailBonds(bonds_dir).data

    expected=_expected_profit(start, date.today(), 'TOS', initial_coupon=4.4, additional_coupon=0.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)
    # Compounding means year 2's contribution alone (computed on the grown base) exceeds what a
    # flat, non-compounding year 2 at the same rate would have contributed.
    non_compounding_equivalent=PolishRetailBonds.NOMINAL_VALUE*4.4/100.0*(1-PolishRetailBonds.TAX_RATE/100.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]>non_compounding_equivalent


def test_edo_continues_accruing_past_year_one(make_source_dir):
    """EDO's real term is 10 annual periods; this used to be locked in as a regression test for a
    bug where Profit reverted to 0.0 past year one (see git history / README Roadmap - fixed as
    part of the taxonomy rework, so this now asserts the fixed behavior instead)."""
    start=date.today()-timedelta(days=400)  # more than a year ago
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},EDO0936,1,2.0,5.35,False\n"
        ),
        inflation_row="01-2020,2.5\n",
    )
    data=PolishRetailBonds(bonds_dir).data

    profit_within_year_one=data.loc[start.isoformat():(start+timedelta(days=200)).isoformat(), PolishRetailBonds.PROFIT_COLUMN].iloc[-1]
    profit_today=data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]

    assert profit_within_year_one>0.0
    assert profit_today>profit_within_year_one  # keeps growing past the year-1 boundary, unlike the old bug

    expected=_expected_profit(start, date.today(), 'EDO', initial_coupon=5.35, additional_coupon=2.0, external_rate=2.5)
    assert profit_today==pytest.approx(expected)


def test_swap_discount_reduces_cost_basis_and_raises_profit_by_the_same_amount_every_day(make_source_dir):
    start=date.today()-timedelta(days=3)

    def build(is_swapped: str):
        bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,1,0.0,4.0,{is_swapped}\n"
        ))
        return PolishRetailBonds(bonds_dir).data

    not_swapped, swapped=build('False'), build('True')

    # amount_of_units=1, ROR's swap_discount=0.10 zł/bond -> cost basis 0.10 lower.
    assert not_swapped[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(100.0)
    assert swapped[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(99.90)

    # Accrual is always computed off the full nominal value regardless of what was paid, so a
    # lower cost basis means Profit is uniformly higher by the same 0.10 - every day, not just at
    # the end (unlike the pre-rework flat maturity-day bonus).
    delta=swapped[PolishRetailBonds.PROFIT_COLUMN]-not_swapped[PolishRetailBonds.PROFIT_COLUMN]
    assert delta.sub(0.10).abs().max()<1e-9


@pytest.mark.parametrize('code,row', [
    ('OTS0826', "OTS0826,1,0.0,2.0"),
    ('ROS0932', "ROS0932,1,2.0,5.0"),
    ('ROD0938', "ROD0938,1,2.5,5.6"),
])
def test_swap_has_no_effect_for_par_priced_or_non_exchangeable_types(make_source_dir, code, row):
    start=date.today()-timedelta(days=2)

    def build(is_swapped: str):
        bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},{row},{is_swapped}\n"
        ))
        return PolishRetailBonds(bonds_dir).data

    not_swapped, swapped=build('False'), build('True')
    assert swapped[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(not_swapped[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1])
    assert swapped[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(not_swapped[PolishRetailBonds.PROFIT_COLUMN].iloc[-1])


def test_distribution_by_ticker_splits_by_bond_type(make_source_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,3,0.0,4.4,False\n"
        f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir)

    # No swap discount on either holding, so money invested is purely amount_of_units*NOMINAL_VALUE
    # -> a clean 3:1 split between the two types, regardless of their different accrued interest.
    assert set(bonds.distribution_by_ticker)=={'TOS', 'ROR'}
    assert bonds.distribution_by_ticker['TOS']==pytest.approx(75.0)
    assert bonds.distribution_by_ticker['ROR']==pytest.approx(25.0)
    assert sum(bonds.distribution_by_ticker.values())==pytest.approx(100.0)
    assert sum(bonds.distribution_by_ticker_current_value.values())==pytest.approx(100.0)
    assert sum(bonds.distribution_by_ticker_revenue.values())==pytest.approx(100.0)


def test_distribution_by_ticker_merges_multiple_holdings_of_the_same_type(make_source_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
        f"{start.isoformat()},ROR0125,2,0.0,3.5,False\n"  # a second, distinct ROR holding
        f"{start.isoformat()},TOS0929,3,0.0,4.4,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir)

    # The two ROR holdings (amount 1 and 2) land in one 'ROR' slice, not two separate ones.
    assert set(bonds.distribution_by_ticker)=={'TOS', 'ROR'}
    assert bonds.distribution_by_ticker['ROR']==pytest.approx(50.0)  # (1+2)*100 vs 3*100
    assert bonds.distribution_by_ticker['TOS']==pytest.approx(50.0)


def test_distribution_by_ticker_survives_a_cache_hit(make_source_dir, cache_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,3,0.0,4.4,False\n"
        f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
    ))

    cold=PolishRetailBonds(bonds_dir, cache_dir=cache_dir)
    warm=PolishRetailBonds(bonds_dir, cache_dir=cache_dir)  # second construction - cache hit

    assert warm.distribution_by_ticker==cold.distribution_by_ticker
    assert set(warm.distribution_by_ticker)=={'TOS', 'ROR'}


def test_stale_incompatible_cache_entry_is_recomputed_not_crashed(make_source_dir, cache_dir):
    """Regression test: a same-day cache entry whose payload predates the (dataframe,
    type_dataframes) tuple __init__ now expects — e.g. left over from before
    distribution_by_ticker started breaking down by bond type — must not crash construction,
    even though it's otherwise still 'fresh' (computed today, so DiskCache.get() wouldn't reject
    it on age alone)."""
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
    ))

    PolishRetailBonds(bonds_dir, cache_dir=cache_dir)  # primes a real cache entry under the real key
    [cache_file]=glob.glob(os.path.join(cache_dir, '*.pkl'))
    with open(cache_file, 'rb') as f:
        entry=pickle.load(f)
    entry['data']=pd.DataFrame({'Money_invested': [1.0], 'Profit_without_dividends': [0.0], 'Profit': [0.0]})  # old, pre-tuple shape
    with open(cache_file, 'wb') as f:
        pickle.dump(entry, f)

    bonds=PolishRetailBonds(bonds_dir, cache_dir=cache_dir)  # must not raise - recomputes instead
    assert bonds.distribution_by_ticker=={'TOS': 100.0}


def test_unknown_bond_code_raises_value_error(make_source_dir):
    start=date.today()-timedelta(days=1)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},XYZ0125,1,0.0,5.0,False\n"
    ))
    with pytest.raises(ValueError, match='XYZ'):
        PolishRetailBonds(bonds_dir)


def test_count_bonds_reads_row_count_without_computing(make_source_dir):
    start=date.today()-timedelta(days=1)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
        f"{start.isoformat()},ROR0927,2,0.5,4.0,False\n"
    ))
    assert PolishRetailBonds.count_bonds(bonds_dir)==2
