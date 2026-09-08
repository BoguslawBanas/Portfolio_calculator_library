"""
Disk cache for the DataFrames Stock/Bonds/Commodity/Crypto/Currency fetch from yfinance and
compute from the source transaction CSVs (see the README's Roadmap). Every one of those
calculators fetches price/rate history up to datetime.today(), so a cache entry is only ever
valid for the day it was written — a cache key doesn't need to encode "as of when", just
"computed_on" needs to still be today. Anything that should invalidate a cache entry sooner
(a new/edited transaction, a different ticker/currency) is instead folded into the key itself,
via hash_dataframe/hash_file below.

Caching is opt-in: every class that can use a DiskCache takes it through a cache_dir
constructor argument that defaults to None (disabled), so existing callers are unaffected.
"""

import os
import pickle
import hashlib
from datetime import datetime
import pandas as pd


class DiskCache:
    def __init__(self, cache_dir: str):
        self.cache_dir=cache_dir

    def get(self, key: str) -> pd.DataFrame:
        """Returns the cached DataFrame for key, or None on a cache miss or a stale
        (not computed today) entry."""
        path=self._path(key)
        if not os.path.exists(path):
            return None

        try:
            with open(path, 'rb') as f:
                entry=pickle.load(f)
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
            return None

        if entry.get('computed_on')!=datetime.today().date():
            return None

        return entry['data']

    def set(self, key: str, dataframe: pd.DataFrame):
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self._path(key), 'wb') as f:
            pickle.dump({'computed_on': datetime.today().date(), 'data': dataframe}, f)

    def clear(self):
        """Deletes every cached entry in cache_dir (the directory itself is left in place).
        Use this to reclaim space from orphaned entries — one whose key (ticker/currency/
        transactions hash) is no longer recomputed by anything, so it would otherwise never
        get overwritten or removed on its own — or simply to force a clean slate."""
        if not os.path.isdir(self.cache_dir):
            return
        for filename in os.listdir(self.cache_dir):
            if filename.endswith('.pkl'):
                os.remove(os.path.join(self.cache_dir, filename))

    def evict_stale(self) -> int:
        """Deletes every entry not computed today, reclaiming disk space from orphaned
        entries (see clear()'s docstring) without needing to track which keys are still
        'live'. Safe to call at any point, including mid-run: an entry not computed today is
        already worthless to get() (a cache miss), so removing it changes no caller's
        behavior — either nothing will ever recompute that key again (truly orphaned, so
        deleting it is pure cleanup), or something will recompute it later today, at which
        point set() writes a fresh file to the same path regardless of whether the old one
        was still there. A corrupted/unreadable entry (see get()'s docstring) is removed the
        same way — it's equally worthless. Returns the number of files removed."""
        if not os.path.isdir(self.cache_dir):
            return 0

        today=datetime.today().date()
        removed=0
        for filename in os.listdir(self.cache_dir):
            if not filename.endswith('.pkl'):
                continue
            path=os.path.join(self.cache_dir, filename)
            try:
                with open(path, 'rb') as f:
                    entry=pickle.load(f)
                is_stale=entry.get('computed_on')!=today
            except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
                is_stale=True
            if is_stale:
                os.remove(path)
                removed+=1
        return removed

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, hashlib.sha256(key.encode()).hexdigest()+'.pkl')

    @staticmethod
    def make_key(*parts) -> str:
        return '|'.join(str(part) for part in parts)

    @staticmethod
    def hash_dataframe(dataframe: pd.DataFrame) -> str:
        """Stable hash of a DataFrame's content (index + values) — fold a source transactions
        DataFrame into a cache key so a new/edited/removed transaction invalidates it."""
        return hashlib.sha256(pd.util.hash_pandas_object(dataframe, index=True).values.tobytes()).hexdigest()

    @staticmethod
    def hash_file(path: str) -> str:
        """Stable hash of a file's content — fold a rate CSV (interest_rate.csv,
        inflation_rate.csv) into a cache key so an edited/appended rate invalidates it."""
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
