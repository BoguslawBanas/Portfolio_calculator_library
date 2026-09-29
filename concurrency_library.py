"""
Shared helper for fetching several tickers'/symbols' price history concurrently within one
source (Stock/Commodity/Crypto) - each fetch is I/O-bound (a yfinance HTTP call), so a bounded
thread pool cuts wall-clock load time for a portfolio with many holdings. See ROADMAP.md's
Performance & reliability section.
"""

from concurrent.futures import ThreadPoolExecutor
import threading


class Concurrency:
    # Bounded, not one thread per ticker: PriceSource.fetch_history already retries a failed
    # fetch with backoff - many tickers retrying in parallel after a rate-limit response would
    # look worse to yfinance's rate limiter than fewer, sequential retries. See ROADMAP.md.
    DEFAULT_MAX_WORKERS=4

    @classmethod
    def run(cls, items: list, fn, max_workers: int=DEFAULT_MAX_WORKERS, progress_callback=None) -> list:
        """Runs fn(item) for each item in items, on a bounded thread pool, returning results in
        the same order as items (ThreadPoolExecutor.map yields in submission order, not
        completion order) - so a caller building ticker-keyed structures from the result list can
        stay written exactly as if this were still a plain sequential loop.
        progress_callback: optional zero-arg callable, invoked once per completed item, and
        serialized through a lock - the callback itself (typically tqdm's own update()) isn't
        guaranteed thread-safe against concurrent calls from several worker threads.
        max_workers<=1 (or fewer than 2 items) runs fn in the calling thread instead, sequentially
        - no pool, no thread-safety concerns at all, identical to the pre-concurrency behavior."""
        if max_workers<=1 or len(items)<=1:
            results=list()
            for item in items:
                results.append(fn(item))
                if progress_callback is not None:
                    progress_callback()
            return results

        progress_lock=threading.Lock()

        def _run_one(item):
            result=fn(item)
            if progress_callback is not None:
                with progress_lock:
                    progress_callback()
            return result

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            return list(executor.map(_run_one, items))
