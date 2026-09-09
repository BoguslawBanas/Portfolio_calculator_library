"""Tests for cache_library.DiskCache — pure unit tests, no yfinance/pandas fetch involved."""

import os
import pickle
from datetime import date, timedelta

import pandas as pd
import pytest

from Portfolio_calculator_library import DiskCache


@pytest.fixture
def sample_df():
    return pd.DataFrame({'a': [1, 2, 3]})


def test_get_returns_none_on_miss(cache_dir):
    assert DiskCache(cache_dir).get('missing-key') is None


def test_set_then_get_round_trips(cache_dir, sample_df):
    cache=DiskCache(cache_dir)
    cache.set('k', sample_df)
    pd.testing.assert_frame_equal(cache.get('k'), sample_df)


def test_entry_from_a_previous_day_is_treated_as_a_miss(cache_dir, sample_df):
    cache=DiskCache(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache._path('k'), 'wb') as f:
        pickle.dump({'computed_on': date.today()-timedelta(days=1), 'data': sample_df}, f)
    assert cache.get('k') is None


def test_corrupted_entry_treated_as_miss(cache_dir):
    cache=DiskCache(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    with open(cache._path('k'), 'wb') as f:
        f.write(b'not a pickle')
    assert cache.get('k') is None


def test_clear_removes_entries_and_marker(cache_dir, sample_df):
    cache=DiskCache(cache_dir)
    cache.set('a', sample_df)
    cache.set('b', sample_df)
    cache.evict_stale_if_due()
    assert os.path.exists(os.path.join(cache_dir, DiskCache._EVICT_STALE_MARKER))
    cache.clear()
    assert cache.get('a') is None
    assert cache.get('b') is None
    assert not os.path.exists(os.path.join(cache_dir, DiskCache._EVICT_STALE_MARKER))
    assert os.path.isdir(cache_dir)  # directory itself is left in place


def test_clear_on_nonexistent_dir_is_a_no_op(tmp_path):
    DiskCache(str(tmp_path/'never-created')).clear()  # must not raise


def test_evict_stale_removes_only_stale_entries(cache_dir, sample_df):
    cache=DiskCache(cache_dir)
    cache.set('fresh', sample_df)
    os.makedirs(cache_dir, exist_ok=True)
    stale_path=os.path.join(cache_dir, 'stale.pkl')
    with open(stale_path, 'wb') as f:
        pickle.dump({'computed_on': date.today()-timedelta(days=1), 'data': sample_df}, f)

    removed=cache.evict_stale()
    assert removed==1
    assert not os.path.exists(stale_path)
    pd.testing.assert_frame_equal(cache.get('fresh'), sample_df)


def test_evict_stale_if_due_throttles_to_once_per_day(cache_dir, sample_df):
    cache=DiskCache(cache_dir)
    cache.set('fresh', sample_df)
    os.makedirs(cache_dir, exist_ok=True)
    stale_path=os.path.join(cache_dir, 'stale.pkl')
    with open(stale_path, 'wb') as f:
        pickle.dump({'computed_on': date.today()-timedelta(days=1), 'data': sample_df}, f)

    first=cache.evict_stale_if_due()
    assert first==1
    assert not os.path.exists(stale_path)

    # A second stale entry appears later the same day — evict_stale_if_due() should skip the
    # scan entirely (marker says it already ran today) and leave it alone.
    stale_path_2=os.path.join(cache_dir, 'stale2.pkl')
    with open(stale_path_2, 'wb') as f:
        pickle.dump({'computed_on': date.today()-timedelta(days=1), 'data': sample_df}, f)
    second=cache.evict_stale_if_due()
    assert second==0
    assert os.path.exists(stale_path_2)


def test_make_key_is_stable_and_order_sensitive():
    assert DiskCache.make_key('a', 'b', 1)==DiskCache.make_key('a', 'b', 1)
    assert DiskCache.make_key('a', 'b')!=DiskCache.make_key('b', 'a')


def test_hash_dataframe_changes_when_content_changes(sample_df):
    h1=DiskCache.hash_dataframe(sample_df)
    h2=DiskCache.hash_dataframe(sample_df)
    assert h1==h2
    changed=sample_df.copy()
    changed.iloc[0, 0]=999
    assert DiskCache.hash_dataframe(changed)!=h1


def test_hash_file_changes_when_content_changes(tmp_path):
    path=tmp_path/'rate.csv'
    path.write_text("date,rate\n01-2024,5.0\n")
    h1=DiskCache.hash_file(str(path))
    path.write_text("date,rate\n01-2024,6.0\n")
    h2=DiskCache.hash_file(str(path))
    assert h1!=h2
