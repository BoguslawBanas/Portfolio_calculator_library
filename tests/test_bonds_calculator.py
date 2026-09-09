"""
Tests for bonds_calculator_library.PolishRetailBonds — no network involved (bonds have no
yfinance-fetchable price, see the module's own docstring), just the accrual formulas keyed
off each bond-type code (R/D/T/E) plus the interest_rate.csv/inflation_rate.csv rate history.

Dates are chosen relative to date.today() (a handful of days ago) so every day in a bond's
window falls in "year 0" (days_from_beginning < 365) unless a test specifically wants to cross
a year boundary — that keeps the expected-value arithmetic below (transcribed independently
from the accrual formulas, not just re-calling the implementation) simple and exact.
"""

from datetime import date, timedelta

import pytest

from Portfolio_calculator_library import PolishRetailBonds


def make_bonds_dir(make_source_dir, buy_csv: str, rate_row: str="01-2020,5.0\n", inflation_row: str="01-2020,4.0\n"):
    return make_source_dir('bonds', {
        'buy.csv': buy_csv,
        'interest_rate.csv': "date,rate\n"+rate_row,
        'inflation_rate.csv': "date,inflation\n"+inflation_row,
    })


def test_fixed_rate_t_bond_accrues_at_documented_daily_rate(make_source_dir):
    start=date.today()-timedelta(days=3)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0327,1,0.0,6.0,False\n"
    ))
    bonds=PolishRetailBonds(bonds_dir)
    data=bonds.data

    n_days=(date.today()-start).days+1
    coupon=6.0
    daily_profit=100.0*((1+coupon/100)**1-(1+coupon/100)**0)/365.0  # tax=0.0% for T bonds
    expected_profit=round(daily_profit*n_days, 2)

    assert data[PolishRetailBonds.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(100.0)
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected_profit)
    assert bonds.total_revenue==pytest.approx(expected_profit)


def test_variable_rate_r_bond_uses_interest_rate_csv_plus_additional_coupon(make_source_dir):
    start=date.today()-timedelta(days=3)
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},ROR0125,1,0.5,0.0,False\n"
        ),
        rate_row="01-2020,6.0\n",
    )
    bonds=PolishRetailBonds(bonds_dir)
    data=bonds.data

    n_days=(date.today()-start).days+1
    rate, additional_coupon, tax=6.0, 0.5, 19.0
    daily_profit=(100.0*(1+(rate+additional_coupon)/100)-100.0)/365.0*(1-tax/100)
    expected_profit=round(daily_profit*n_days, 2)

    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected_profit)


def test_is_swapped_adds_a_flat_bonus_on_the_last_computed_day(make_source_dir):
    start=date.today()-timedelta(days=3)

    def build(is_swapped: str):
        bonds_dir=make_bonds_dir(
            make_source_dir,
            buy_csv=(
                "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
                f"{start.isoformat()},ROR0225,1,0.0,0.0,{is_swapped}\n"
            ),
            rate_row="01-2020,6.0\n",
        )
        return PolishRetailBonds(bonds_dir).data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]

    profit_not_swapped=build('False')
    profit_swapped=build('True')
    # amount_of_units=1 -> bonus is 1*0.1.
    assert profit_swapped-profit_not_swapped==pytest.approx(0.1)


def test_edo_bond_year_2_accrual_bug_is_locked_by_regression_test(make_source_dir):
    """
    Documents CURRENT (buggy) behavior, not correct behavior — see the README Roadmap entry on
    _inflationary_rate_bond's year-2-onward accrual bug. Its `for i in range(9)` loop breaks on
    its very first iteration for every real (10-year) EDO bond, and because the DataFrame is
    pre-initialized with Profit=0.0 and only the first-year slice ever gets overwritten with the
    cumulative daily-interest series, Profit for any day past the first year doesn't merely stop
    growing — it reverts to 0.0 (masked by whatever bonus is_swapped adds, if any).

    This test exists so that whoever fixes the accrual bug gets a loud, expected failure here
    telling them to update this test's expectation, rather than an unnoticed behavior change.
    """
    start=date.today()-timedelta(days=400)  # more than a year ago
    bonds_dir=make_bonds_dir(
        make_source_dir,
        buy_csv=(
            "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
            f"{start.isoformat()},EDO0321,1,1.0,3.0,False\n"
        ),
        inflation_row="01-2020,2.5\n",
    )
    bonds=PolishRetailBonds(bonds_dir)
    data=bonds.data

    # Some day within the first year does show growing profit...
    one_year_from_start=data.loc[start.isoformat():(start+timedelta(days=200)).isoformat()]
    assert one_year_from_start[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]>0.0

    # ...but today (400 days in, past the first-year cutoff) has reverted to exactly 0.0.
    assert data[PolishRetailBonds.PROFIT_COLUMN].iloc[-1]==pytest.approx(0.0)
    assert data[PolishRetailBonds.PROFIT_WITHOUT_DIVIDEND_COLUMN].iloc[-1]==pytest.approx(0.0)


def test_count_bonds_reads_row_count_without_computing(make_source_dir):
    start=date.today()-timedelta(days=1)
    bonds_dir=make_bonds_dir(make_source_dir, buy_csv=(
        "date,isin,amount_of_units,additional_coupon,initial_coupon,is_swapped\n"
        f"{start.isoformat()},TOS0327,1,0.0,6.0,False\n"
        f"{start.isoformat()},ROR0325,2,0.5,0.0,False\n"
    ))
    assert PolishRetailBonds.count_bonds(bonds_dir)==2
