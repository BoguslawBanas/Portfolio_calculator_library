"""
Tests for bank_account_calculator_library.BankAccount — no network involved (same as
PolishRetailBonds, a bank account isn't yfinance-fetchable), just the daily interest-accrual
loop with periodic capitalization.

Dates are chosen relative to date.today() so the window falls well inside one capitalization
period unless a test specifically wants to cross a capitalization boundary — that keeps the
expected-value arithmetic below (transcribed independently from the accrual formula, not just
re-calling the implementation) simple and exact.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from Portfolio_calculator_library import BankAccount


def test_fixed_rate_account_accrues_daily_interest_before_first_capitalization(make_source_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n",
    })
    account=BankAccount(account_dir)
    data=account.data

    assert data[BankAccount.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1000.0)

    n_days=(date.today()-start).days+1
    daily_rate=1000.0*(6.0/100.0)/365.0  # tax=0.0, balance constant (no capitalization reached yet)
    expected_profit=round(daily_rate*n_days, 2)
    assert data[BankAccount.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected_profit)
    assert account.total_revenue==pytest.approx(expected_profit)


def test_withdrawal_reduces_the_interest_bearing_balance(make_source_dir):
    start=date.today()-timedelta(days=5)
    withdrawal_date=date.today()-timedelta(days=2)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n",
        'withdrawal.csv': "date,account,amount\n"
                          f"{withdrawal_date.isoformat()},savings,400.0\n",
    })
    account=BankAccount(account_dir)
    data=account.data
    assert data[BankAccount.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(600.0)
    # Balance drops on withdrawal_date -> total profit is strictly less than if the full 1000
    # had earned interest the whole window.
    daily_rate_full=1000.0*(6.0/100.0)/365.0
    n_days=(date.today()-start).days+1
    assert data[BankAccount.PROFIT_COLUMN].iloc[-1]<round(daily_rate_full*n_days, 2)


def test_capitalization_folds_accrued_interest_into_the_balance(make_source_dir):
    # A 1-month capitalization period with a window long enough to cross exactly one boundary:
    # the day interest starts compounding on (principal + first month's interest) instead of
    # just principal is the one behavior this test is designed to catch.
    start=date.today()-timedelta(days=40)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,12.0,1,0.0\n",
    })
    account=BankAccount(account_dir)
    data=account.data

    n_days=(date.today()-start).days+1
    # If nothing ever capitalized, profit would be exactly this (flat daily rate on principal only).
    never_capitalized_profit=round(1000.0*(12.0/100.0)/365.0*n_days, 2)
    assert data[BankAccount.PROFIT_COLUMN].iloc[-1]>never_capitalized_profit


def test_variable_rate_account_reads_interest_rate_csv_plus_spread(make_source_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,variable,0.5,12,0.0\n",
        'interest_rate.csv': "date,rate\n01-2020,5.0\n",
    })
    account=BankAccount(account_dir)
    data=account.data

    n_days=(date.today()-start).days+1
    daily_rate=1000.0*((5.0+0.5)/100.0)/365.0
    expected_profit=round(daily_rate*n_days, 2)
    assert data[BankAccount.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected_profit)


def test_tax_defaults_to_19_percent_when_omitted(make_source_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12\n",
    })
    account=BankAccount(account_dir)
    data=account.data

    n_days=(date.today()-start).days+1
    daily_rate=1000.0*(6.0/100.0)/365.0*(1-BankAccount.DEFAULT_TAX/100.0)
    expected_profit=round(daily_rate*n_days, 2)
    assert data[BankAccount.PROFIT_COLUMN].iloc[-1]==pytest.approx(expected_profit)


def test_multiple_accounts_in_one_directory_are_split_and_summed(make_source_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n"
                       f"{start.isoformat()},emergency_fund,500.0,fixed,3.0,12,0.0\n",
    })
    account=BankAccount(account_dir)
    assert account.data[BankAccount.MONEY_INVESTED_COLUMN].iloc[-1]==pytest.approx(1500.0)
    assert set(account.distribution_by_ticker)=={'savings', 'emergency_fund'}
    assert sum(account.distribution_by_ticker.values())==pytest.approx(100.0)


def test_missing_deposit_csv_raises(make_source_dir):
    account_dir=make_source_dir('bank_account', {
        'withdrawal.csv': "date,account,amount\n2024-01-01,savings,100.0\n",
    })
    with pytest.raises(ValueError, match="deposit.csv"):
        BankAccount(account_dir)


def test_cache_dir_serves_an_identical_dataframe_on_the_second_construction(make_source_dir, cache_dir):
    start=date.today()-timedelta(days=5)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n",
    })
    first=BankAccount(account_dir, cache_dir=cache_dir)
    second=BankAccount(account_dir, cache_dir=cache_dir)
    pd.testing.assert_frame_equal(first.data, second.data)


def test_count_accounts_reads_without_computing(make_source_dir):
    start=date.today()-timedelta(days=1)
    account_dir=make_source_dir('bank_account', {
        'deposit.csv': "date,account,amount,rate_type,rate,capitalization_months,tax\n"
                       f"{start.isoformat()},savings,1000.0,fixed,6.0,12,0.0\n"
                       f"{start.isoformat()},emergency_fund,500.0,fixed,3.0,12,0.0\n",
    })
    assert BankAccount.count_accounts(account_dir)==2
