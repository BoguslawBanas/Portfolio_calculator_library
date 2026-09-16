"""
Disk cache for the DataFrames Stock/Bonds/Commodity/Crypto/Currency/BankAccount compute. Every
calculator computes up to datetime.today(), so an entry is only ever valid for the day it was
written - the key doesn't encode "as of when", just needs "computed_on" to still be today.
Anything that should invalidate sooner (an edited transaction, a different ticker/currency/rate
file) is folded into the key itself via hash_dataframe/hash_file below.

Opt-in: every class takes a cache_dir argument defaulting to None (disabled).
"""

import os
import pickle
import hashlib
from datetime import datetime
import pandas as pd


class DiskCache:
    # Marker recording the last date evict_stale_if_due() ran - not '.pkl', so the other methods
    # (which filter on that extension) never touch it.
    _EVICT_STALE_MARKER='.evict_stale_last_run'

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
        """Deletes every cached entry (plus the evict_stale_if_due marker), directory left in
        place. Reclaims orphaned entries - ones no key ever recomputes anymore - or forces a
        clean slate."""
        if not os.path.isdir(self.cache_dir):
            return
        for filename in os.listdir(self.cache_dir):
            if filename.endswith('.pkl') or filename==self._EVICT_STALE_MARKER:
                os.remove(os.path.join(self.cache_dir, filename))

    def evict_stale(self) -> int:
        """Deletes every entry not computed today - reclaims orphaned entries with no need to
        track which keys are still live. Safe mid-run: a not-today entry is already a cache miss
        to get(), so removing it changes no behavior - set() just writes a fresh file later if
        something recomputes that key. Corrupted/unreadable entries are removed the same way.
        Returns the number removed."""
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

    def evict_stale_if_due(self) -> int:
        """Like evict_stale(), but skips the scan if a marker file shows it already ran today -
        throttles the O(cache size) sweep to once per day. Use for an automatic/repeated sweep
        (e.g. per Portfolio construction); call evict_stale() directly for an unconditional one.
        Returns files removed (0 if skipped)."""
        if not os.path.isdir(self.cache_dir):
            return 0

        today=datetime.today().date()
        marker_path=os.path.join(self.cache_dir, self._EVICT_STALE_MARKER)

        if os.path.exists(marker_path):
            try:
                with open(marker_path, 'r') as f:
                    last_run=datetime.strptime(f.read().strip(), '%Y-%m-%d').date()
                if last_run==today:
                    return 0
            except (ValueError, OSError):
                pass  # corrupted/unreadable marker — fall through, sweep, and rewrite it

        removed=self.evict_stale()

        with open(marker_path, 'w') as f:
            f.write(today.isoformat())

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
