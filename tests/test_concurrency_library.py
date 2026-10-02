"""Tests for concurrency_library.Concurrency — pure unit tests, no network involved."""

import time
import threading

from Portfolio_calculator_library.concurrency_library import Concurrency


def test_sequential_fallback_for_a_single_item():
    calls=[]
    result=Concurrency.run([1], lambda x: calls.append(x) or x*2, max_workers=4)
    assert result==[2]
    assert calls==[1]


def test_max_workers_of_one_runs_strictly_sequentially():
    concurrent_count=[0]
    max_concurrent=[0]
    lock=threading.Lock()

    def fn(item):
        with lock:
            concurrent_count[0]+=1
            max_concurrent[0]=max(max_concurrent[0], concurrent_count[0])
        time.sleep(0.05)
        with lock:
            concurrent_count[0]-=1
        return item

    result=Concurrency.run([1, 2, 3], fn, max_workers=1)
    assert result==[1, 2, 3]
    assert max_concurrent[0]==1


def test_items_actually_run_concurrently_not_one_at_a_time():
    # 4 items each sleeping 0.2s: sequentially that's ~0.8s, concurrently (max_workers=4) it
    # should be close to one sleep's worth - a generous 0.6s ceiling comfortably separates the
    # two without being flaky on a loaded CI machine.
    def fn(item):
        time.sleep(0.2)
        return item

    start=time.monotonic()
    result=Concurrency.run(list(range(4)), fn, max_workers=4)
    elapsed=time.monotonic()-start

    assert result==[0, 1, 2, 3]
    assert elapsed<0.6


def test_results_preserve_input_order_regardless_of_completion_order():
    # Item 0 sleeps longest, so it would finish LAST if anything raced on completion order -
    # Concurrency.run must still return results in input order, not completion order.
    sleeps={0: 0.15, 1: 0.05, 2: 0.10, 3: 0.0}

    def fn(item):
        time.sleep(sleeps[item])
        return item

    result=Concurrency.run([0, 1, 2, 3], fn, max_workers=4)
    assert result==[0, 1, 2, 3]


def test_progress_callback_is_invoked_exactly_once_per_item_thread_safely():
    # A plain (unlocked) read-sleep-write increment would lose updates under real concurrency -
    # this only reliably totals len(items) if Concurrency.run's own internal lock is doing its
    # job serializing calls to progress_callback.
    counter=[0]

    def progress_callback():
        current=counter[0]
        time.sleep(0.001)
        counter[0]=current+1

    items=list(range(20))
    Concurrency.run(items, lambda x: x, max_workers=8, progress_callback=progress_callback)

    assert counter[0]==len(items)


def test_exception_in_one_item_propagates():
    def fn(item):
        if item==2:
            raise ValueError("boom")
        return item

    try:
        Concurrency.run([1, 2, 3], fn, max_workers=4)
        assert False, "expected ValueError to propagate"
    except ValueError as e:
        assert "boom" in str(e)
