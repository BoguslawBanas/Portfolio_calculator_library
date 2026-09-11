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

from Portfolio_calculator_library import PolishRetailBonds, Currency


def make_bonds_dir(make_source_dir, buy_csv: str, rate_row: str="01-2020,5.0\n", inflation_row: str="01-2020,4.0\n", cancel_csv: str=None):
    files={
        'buy.csv': buy_csv,
        'interest_rate.csv': "date,rate\n"+rate_row,
        'inflation_rate.csv': "date,inflation\n"+inflation_row,
    }
    if cancel_csv is not None:
        files['cancel.csv']=cancel_csv
    return make_source_dir('bonds', files)


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


def test_money_invested_zeroes_and_profit_freezes_after_maturity(make_source_dir):
    start=date.today()-timedelta(days=100)  # OTS's 3-month term has long since ended
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},OTS0826,2,0.0,2.0,False\n"
    ))
    data=PolishRetailBonds(bonds_dir).data

    maturity_date=(pd.Timestamp(start)+pd.DateOffset(months=3)-pd.DateOffset(days=1))
    at_maturity=data.loc[maturity_date, PolishRetailBonds.PROFIT_COLUMN]
    assert at_maturity>0.0

    # Index still reaches today (unlike before this was fixed), not just the maturity date.
    assert data.index[-1]==pd.Timestamp(date.today())
    last_row=data.iloc[-1]
    assert last_row[PolishRetailBonds.MONEY_INVESTED_COLUMN]==pytest.approx(0.0)
    assert last_row[PolishRetailBonds.PROFIT_WITHOUT_DIVIDEND_COLUMN]==pytest.approx(0.0)
    # Profit (realized at redemption) freezes at exactly its at-maturity value, forever after.
    assert last_row[PolishRetailBonds.PROFIT_COLUMN]==pytest.approx(at_maturity)
    day_after_maturity=data.loc[maturity_date+pd.DateOffset(days=1), PolishRetailBonds.PROFIT_COLUMN]
    assert day_after_maturity==pytest.approx(at_maturity)


def test_full_cancellation_freezes_profit_and_zeroes_money_invested_early(make_source_dir):
    """A holding fully cancelled in cancel.csv (amount_of_units matching everything held) stops
    accruing at cancel_date and behaves exactly like a naturally-matured holding from then on -
    see the module docstring."""
    start=date.today()-timedelta(days=200)
    cancel_date=start+timedelta(days=80)  # well before ROR's natural 12-month term ends
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},ROR0927,{cancel_date.isoformat()},1\n"
        ),
    )
    data=PolishRetailBonds(bonds_dir).data

    expected_at_cancellation=_expected_profit(start, cancel_date, 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0)
    assert expected_at_cancellation>0.0

    at_cancel_date=data.loc[pd.Timestamp(cancel_date), PolishRetailBonds.PROFIT_COLUMN]
    assert at_cancel_date==pytest.approx(expected_at_cancellation)

    last_row=data.iloc[-1]
    assert data.index[-1]==pd.Timestamp(date.today())  # index still reaches today, not just cancel_date
    assert last_row[PolishRetailBonds.MONEY_INVESTED_COLUMN]==pytest.approx(0.0)
    assert last_row[PolishRetailBonds.PROFIT_WITHOUT_DIVIDEND_COLUMN]==pytest.approx(0.0)
    assert last_row[PolishRetailBonds.PROFIT_COLUMN]==pytest.approx(expected_at_cancellation)

    # Never reached ROR's natural ~12-month maturity, so without the cancellation Profit today
    # would have kept growing well past what it was on cancel_date.
    uncancelled=PolishRetailBonds(make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
    )).data
    assert uncancelled[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]>expected_at_cancellation


def test_partial_cancellation_splits_the_holding_into_tranches(make_source_dir):
    """Cancelling 5 of 10 held bonds: the cancelled 5 stop accruing at cancel_date (and their
    cost basis/unrealized profit zero out), while the remaining 5 keep accruing normally - so
    today's totals should sit strictly between 'nothing cancelled' and 'all cancelled'."""
    start=date.today()-timedelta(days=200)
    cancel_date=start+timedelta(days=80)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,10,0.5,4.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},ROR0927,{cancel_date.isoformat()},5\n"
        ),
    )
    partial=PolishRetailBonds(bonds_dir).data

    # Cost basis: half the original 10-unit cost basis is still held (the other half zeroed out).
    full_money_invested=PolishRetailBonds.NOMINAL_VALUE*10
    assert partial[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(full_money_invested/2)

    # Profit: 5 units' worth froze at cancel_date, the other 5 units' worth kept accruing to
    # today - so total Profit should be strictly between "all 10 froze at cancel_date" and
    # "all 10 kept accruing to today".
    all_frozen=_expected_profit(start, cancel_date, 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=10.0)
    all_kept=_expected_profit(start, date.today(), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=10.0)
    actual=partial[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]
    assert all_frozen<actual<all_kept

    # Exactly the sum of two independent 5-unit tranches: one frozen at cancel_date, one still
    # accruing to today.
    half_frozen=_expected_profit(start, cancel_date, 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=5.0)
    half_kept=_expected_profit(start, date.today(), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=5.0)
    assert actual==pytest.approx(half_frozen+half_kept)


def test_multiple_partial_cancellations_stack_into_separate_tranches(make_source_dir):
    """Two separate cancel.csv rows against the same holding (3 units, then 2 more units, out of
    10 total) - each cancellation freezes only its own tranche at its own cancel_date, and the
    remaining 5 units keep accruing."""
    start=date.today()-timedelta(days=200)
    first_cancel=start+timedelta(days=40)
    second_cancel=start+timedelta(days=90)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0927,10,0.5,4.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
        # Deliberately out of chronological order - _build_tranches must sort by cancel_date.
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},ROR0927,{second_cancel.isoformat()},2\n"
            f"{start.isoformat()},ROR0927,{first_cancel.isoformat()},3\n"
        ),
    )
    data=PolishRetailBonds(bonds_dir).data

    # 10-3-2=5 units still held -> half the original cost basis.
    assert data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(PolishRetailBonds.NOMINAL_VALUE*5)

    expected=(
        _expected_profit(start, first_cancel, 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=3.0)
        + _expected_profit(start, second_cancel, 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=2.0)
        + _expected_profit(start, date.today(), 'ROR', initial_coupon=4.0, additional_coupon=0.5, external_rate=6.0, amount=5.0)
    )
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)


def test_cancellation_after_natural_maturity_is_a_no_op(make_source_dir):
    start=date.today()-timedelta(days=200)  # OTS's 3-month term has long since ended
    cancel_date=date.today()-timedelta(days=1)  # recorded long after OTS actually matured
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},OTS0826,2,0.0,2.0,False\n"
        ),
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},OTS0826,{cancel_date.isoformat()},2\n"
        ),
    )
    cancelled=PolishRetailBonds(bonds_dir).data

    uncancelled=PolishRetailBonds(make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},OTS0826,2,0.0,2.0,False\n"
    ))).data

    assert cancelled[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(uncancelled[PolishRetailBonds.PROFIT_COLUMN].iloc[-1])


def test_cancellation_before_purchase_date_raises(make_source_dir):
    start=date.today()-timedelta(days=10)
    invalid_cancel_date=start-timedelta(days=1)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
        ),
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},TOS0929,{invalid_cancel_date.isoformat()},1\n"
        ),
    )
    with pytest.raises(ValueError, match="before its own purchase date"):
        PolishRetailBonds(bonds_dir)


def test_cancelling_more_than_held_raises(make_source_dir):
    start=date.today()-timedelta(days=10)
    cancel_date=start+timedelta(days=1)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},TOS0929,5,0.0,4.4,False\n"
        ),
        cancel_csv=(
            "date,isin,cancel_date,amount_of_units\n"
            f"{start.isoformat()},TOS0929,{cancel_date.isoformat()},6\n"
        ),
    )
    with pytest.raises(ValueError, match="more than the"):
        PolishRetailBonds(bonds_dir)


def test_cache_key_is_scoped_by_cancel_csv_presence(make_source_dir, cache_dir):
    start=date.today()-timedelta(days=200)
    cancel_date=start+timedelta(days=80)
    buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},ROR0927,1,0.5,4.0,False\n"
    )

    no_cancel_dir=make_bonds_dir(make_source_dir, buy_csv=buy_csv, rate_row="01-2020,6.0\n")
    without_cancellation=PolishRetailBonds(no_cancel_dir, cache_dir=cache_dir).data

    # Same directory, now with cancel.csv added - a same-day cache hit for the old (no-cancel.csv)
    # key must not be silently reused for this different input.
    with open(os.path.join(no_cancel_dir, 'cancel.csv'), 'w') as f:
        f.write("date,isin,cancel_date,amount_of_units\n"+f"{start.isoformat()},ROR0927,{cancel_date.isoformat()},1\n")
    with_cancellation=PolishRetailBonds(no_cancel_dir, cache_dir=cache_dir).data

    assert with_cancellation[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]!=pytest.approx(without_cancellation[PolishRetailBonds.PROFIT_COLUMN].iloc[-1])


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


def test_matured_bond_type_stays_in_lifetime_invested_but_drops_from_current_value(make_source_dir):
    """A matured holding's Money_invested/unrealized profit both go to 0 (nothing is left held -
    see _bond_dataframe), but the amount that was, historically, put into it doesn't disappear -
    same as a fully-sold Stock ticker still counting toward distribution_by_ticker (lifetime
    invested) while showing 0% in distribution_by_ticker_current_value (nothing currently held)."""
    matured_start=date.today()-timedelta(days=100)  # OTS's 3-month term has long since ended
    active_start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{matured_start.isoformat()},OTS0826,1,0.0,2.0,False\n"
        f"{active_start.isoformat()},TOS0929,3,0.0,4.4,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir)

    assert bonds.total_money_invested==pytest.approx(100.0+300.0)
    assert set(bonds.distribution_by_ticker)=={'OTS', 'TOS'}
    assert bonds.distribution_by_ticker['OTS']==pytest.approx(25.0)
    assert bonds.distribution_by_ticker['TOS']==pytest.approx(75.0)

    # Both types still appear (matches Stock: 0%, not omitted) - OTS's matured holding no longer
    # holds anything, TOS's is still fully held.
    assert bonds.distribution_by_ticker_current_value==pytest.approx({'OTS': 0.0, 'TOS': 100.0})

    # OTS's interest was realized at maturity and persists in revenue forever after.
    expected_ots_revenue=_expected_profit(matured_start, date.today(), 'OTS', initial_coupon=2.0, additional_coupon=0.0)
    assert expected_ots_revenue>0.0
    assert set(bonds.distribution_by_ticker_revenue)=={'OTS', 'TOS'}
    assert bonds.distribution_by_ticker_revenue['OTS']>0.0


def test_matured_holding_still_counts_toward_lifetime_invested_but_not_currently_held(make_source_dir):
    matured_start=date.today()-timedelta(days=100)
    active_start=date.today()-timedelta(days=10)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{matured_start.isoformat()},OTS0826,1,0.0,2.0,False\n"  # matured
        f"{active_start.isoformat()},OTS0125,2,0.0,2.5,False\n"   # still active
    ))
    bonds=PolishRetailBonds(bonds_dir)

    # Lifetime invested counts both holdings (1+2 units) even though only one is still held.
    assert bonds.total_money_invested==pytest.approx(300.0)
    assert set(bonds.distribution_by_ticker)=={'OTS'}
    assert bonds.distribution_by_ticker['OTS']==pytest.approx(100.0)

    # Only the still-active holding (2 units) contributes to what's currently held.
    assert bonds.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(200.0)
    assert bonds.total_current_value==pytest.approx(200.0+bonds.data[PolishRetailBonds.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1])


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


def test_currency_to_defaults_to_native_pln_with_no_fx_call(make_source_dir, mock_yfinance):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
    ))
    PolishRetailBonds(bonds_dir)
    # PLN -> PLN is Currency's own same-currency short-circuit - no yfinance call at all, so
    # existing callers that never pass currency_to get exactly the old PLN-only behavior for free.
    assert mock_yfinance.call_log==[]


def test_currency_to_converts_money_invested_at_the_purchase_date_rate(make_source_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,3,0.0,4.4,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir, currency_to='usd')

    # Independent Currency instance, same start_date as the single holding above -> its FX
    # series lines up position-for-position with the one PolishRetailBonds builds internally.
    fx=Currency('PLN', 'usd', pd.Timestamp(start))
    fx_at_purchase=fx.data.loc[pd.Timestamp(start), Currency.CLOSE_COLUMN]

    assert bonds.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(300.0*fx_at_purchase)
    # Frozen at the purchase-date rate specifically, not today's - the two only coincide by
    # accident, so assert against the deliberately non-flat fake rate rather than 1.0.
    assert fx_at_purchase!=pytest.approx(1.0)


def test_currency_to_bakes_in_each_days_own_fx_rate_before_accumulating(make_source_dir):
    # OTS: a single flat (non-compounding) period, so its daily PLN interest is one constant
    # value every day - isolates the day-by-day FX conversion from any compounding interaction.
    start=date.today()-timedelta(days=5)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},OTS0826,1,0.0,3.0,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir, currency_to='usd')

    fx=Currency('PLN', 'usd', pd.Timestamp(start))
    n_days=(date.today()-start).days+1
    period_days=(pd.Timestamp(start)+pd.DateOffset(months=3)-pd.Timestamp(start)).days
    payments_per_year=12//3
    daily_interest_pln=PolishRetailBonds.NOMINAL_VALUE*1*3.0/100.0/(period_days*payments_per_year)

    gross_converted=sum(daily_interest_pln*fx.data[Currency.CLOSE_COLUMN].iloc[j] for j in range(n_days))
    expected=round(gross_converted*(1-PolishRetailBonds.TAX_RATE/100.0), 2)
    assert bonds.data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected)

    # If every day had instead been converted at a single flat (e.g. today's) rate, the result
    # would differ from the day-by-day sum above, since the fake FX rate genuinely isn't flat.
    flat_at_today=round(daily_interest_pln*n_days*fx.data[Currency.CLOSE_COLUMN].iloc[-1]*(1-PolishRetailBonds.TAX_RATE/100.0), 2)
    assert bonds.data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]!=pytest.approx(flat_at_today)


def test_currency_to_is_folded_into_the_cache_key(make_source_dir, cache_dir):
    start=date.today()-timedelta(days=2)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
    ))

    pln=PolishRetailBonds(bonds_dir, cache_dir=cache_dir)
    usd=PolishRetailBonds(bonds_dir, currency_to='usd', cache_dir=cache_dir)
    pln_again=PolishRetailBonds(bonds_dir, cache_dir=cache_dir)  # cache hit - must not read usd's entry

    assert pln.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]!=pytest.approx(usd.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1])
    assert pln_again.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(pln.data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1])


def test_count_bonds_reads_row_count_without_computing(make_source_dir):
    start=date.today()-timedelta(days=1)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0929,1,0.0,4.4,False\n"
        f"{start.isoformat()},ROR0927,2,0.5,4.0,False\n"
    ))
    assert PolishRetailBonds.count_bonds(bonds_dir)==2
