# Portfolio Library

A Python library for tracking the performance of an investment portfolio, combining stocks/ETFs and Polish retail treasury bonds into a single aggregated view. Price data comes from `yfinance`; every chart is built with `plotly`.

## Features

The library is organized as one class per module.

### 📈 `stock_calculator_library.Stock`

Fetches stock/ETF price history and turns a set of buy/sell/dividend transactions into a daily investment/profit DataFrame.

- historical prices via `yfinance`, forward-filled to a continuous daily calendar
- foreign-currency instruments converted to a target currency via `Currency`
- full or partial sells, tracked against a running average cost basis
- dividends and dividend/sell tax tracked separately from price gains — the `dividend`/`dividend_tax` figures in the CSV are assumed to already be in that ticker's own declared currency (`tickers.json`'s `currency` field, the same one its buy/sell rows use), not necessarily the currency your broker actually paid the dividend in; convert it yourself first if the two differ
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `include_native_currency` — also computes each ticker's DataFrame in its own native currency (`self.native_data[ticker]`/`self.native_currency[ticker]`), isolating its own performance from FX movement against the target currency

### 🏦 `bonds_calculator_library.PolishRetailBonds`

Computes the value over time of Polish retail treasury bonds (*obligacje detaliczne*), given a directory of holdings plus two rate CSVs (`interest_rate.csv`, `inflation_rate.csv`). Named for what it actually models — unlike a market-traded bond (a Treasury ETF, a corporate bond, anything with a `yfinance`-fetchable price, which fits `Stock`'s existing model as-is), these aren't traded on any exchange: bought directly from the Treasury, no secondary market or observable price, only redeemable early at a fixed penalty (`is_swapped` below).

- fixed-rate (`T`, 3-year), variable-rate (`R`/`D`, 1-/2-year), and inflation-indexed (`E`, 10-year, EDO) bonds
- daily accrued interest, compounded per bond type's own rules

### 💱 `currency_calculator_library.Currency`

Daily FX rates via `yfinance`, used internally by `Stock` (and usable standalone) to convert foreign-currency instruments to a base currency. Same-currency conversions short-circuit to a flat 1.0 rate — no network call needed.

### 📊 `portfolio_calculator_library.Portfolio`

Combines one or more `Stock`/`Bonds` sources into a single portfolio-level DataFrame.

- builds and sums per-instrument DataFrames (`Money_invested`, `Profit_without_dividends`, `Profit`) across every source, regardless of asset type
- allocation by ticker/directory, by amount invested (cost basis), by current market value (cost basis still held plus unrealized gain), or by revenue (each position's share of total portfolio gains — can be negative for a losing position)
- shows a `tqdm` progress bar while fetching, sized to the actual number of tickers/bond directories up front
- `calculate_irr()` — incremental Newton's-method internal rate of return
- `calculate_money_earned_between_dates()` / `calculate_money_earned_between_dates_column()` — profit over a rolling date window
- `resample()` — downsample to daily/weekly/monthly/quarterly/yearly buckets
- optional `cache_dir` — caches each source's computed DataFrame to disk instead of re-fetching/recomputing on every run (see `cache_library.DiskCache` below)
- optional `force_refresh` — with `cache_dir` set, forces a one-off cold start (ignores any cached entry, then overwrites it with the fresh result) without having to clear `cache_dir` yourself
- optional `include_native_currency` — collects each `Stock`/`Commodity`/`Crypto` ticker/symbol's native-currency DataFrame into `self.native_data`/`self.native_currency`, alongside the always-converted `self.data` every other feature above works from. `Bonds` are left out — they're already single-currency (PLN) with no conversion step to opt out of

### 💾 `cache_library.DiskCache`

Disk cache backing the optional `cache_dir`/`force_refresh` arguments on `Stock`/`Bonds`/`Commodity`/`Crypto`/`Currency`/`Portfolio` — opt-in, off by default.

- caches each source's fully computed DataFrame (price history already fetched, transactions already walked), so a same-day re-run skips both the `yfinance` calls and the recomputation entirely
- a cache entry is valid only for the day it was written — every calculator fetches price/rate history up to "today", so entries auto-invalidate the next calendar day
- also invalidates on any change to the underlying inputs (an edited/added/removed transaction, a different ticker/currency pair) by folding a hash of them into the cache key, independent of the day-based expiry
- `force_refresh=True` bypasses a cache read for one run without touching disk — a per-call cold start
- `DiskCache(cache_dir).clear()` deletes every cached entry, plus `evict_stale_if_due()`'s marker file if present (the directory itself is left in place) — use it to force a clean slate by hand
- `DiskCache(cache_dir).evict_stale()` deletes only entries not computed today (already worthless to `get()` — an entry either gets recomputed today, in which case `set()` overwrites it anyway, or nothing ever recomputes that key again, in which case it was orphaned and this reclaims its space). An unconditional, full `cache_dir` scan (open + unpickle every entry) every time it's called
- `DiskCache(cache_dir).evict_stale_if_due()` — the throttled version of `evict_stale()`: skips the scan entirely if it already ran today, remembered via a small marker file in `cache_dir`, so repeated calls the same day cost an O(1) marker check instead of an O(cache size) scan. `Portfolio` calls this automatically once (at most) per calendar day at the end of construction whenever `cache_dir` is set, so orphaned entries (a removed ticker, an edited transaction) get swept up without any manual cleanup step or per-construction scan overhead

### 📉 `plot_library.Plot`

Charts for a constructed `Portfolio`, built entirely on `plotly`:

- `money_plot` — money invested vs. total revenue, as overlaid lines or a stacked area
- `performance_plot` — IRR over time, as a line or a candlestick chart
- `revenue_plot` — total gain over time, skipping IRR; dividends either summed into revenue or shown as a separate line
- `period_return_bar_plot` — rolling daily return, colored by sign
- `allocation_plot` — portfolio allocation by ticker or by source directory, as a pie or bar chart, by amount invested, current market value, or revenue
- `allocation_comparison_plot` — grouped bar chart comparing allocation by amount invested against allocation by current market value, side by side per ticker/directory

### 🪙 `commodity_calculator_library.Commodity`

Turns a set of buy/sell transactions in physical commodities (gold, silver, platinum, palladium, copper) into a daily investment/profit DataFrame, the same way `Stock` does for stocks/ETFs.

- prices via `yfinance` futures tickers (`GC=F`, `SI=F`, ...), all USD-quoted, converted to the target currency via `Currency`
- full or partial sells, tracked against a running average cost basis, same as `Stock`
- no dividends — `Profit` is unrealized plus realized gain
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `include_native_currency` — also computes each symbol's DataFrame in USD (its native quote currency), same as `Stock`

### ₿ `crypto_calculator_library.Crypto`

Turns a set of buy/sell transactions in crypto (bitcoin, ethereum, ...) into a daily investment/profit DataFrame — the same pattern as `Commodity`, minus the physical-delivery framing.

- prices via `yfinance` USD-quoted tickers (`BTC-USD`, `ETH-USD`, ...), converted to the target currency via `Currency`
- full or partial sells, tracked against a running average cost basis, same as `Stock`/`Commodity`
- no dividends — `Profit` is unrealized plus realized gain
- units rounded to 8 decimal places (vs. `Commodity`'s 4) for fractional holdings
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `include_native_currency` — also computes each symbol's DataFrame in USD (its native quote currency), same as `Stock`/`Commodity`

### 🚧 In progress

`bank_account_calculator_library.py` is an early, function-based prototype that predates `Stock`/`Bonds`/`Commodity`/`Crypto`'s class-based design and isn't wired into `Portfolio` yet.

## Project structure

```
Portfolio_calculator_library/
│
├── stock_calculator_library.py          # Stock
├── bonds_calculator_library.py          # PolishRetailBonds
├── commodity_calculator_library.py      # Commodity
├── crypto_calculator_library.py         # Crypto
├── currency_calculator_library.py       # Currency
├── portfolio_calculator_library.py      # Portfolio
├── plot_library.py                      # Plot
├── cache_library.py                     # DiskCache
├── bank_account_calculator_library.py   # prototype, not yet integrated
├── __init__.py                          # re-exports the classes above at the package root
└── LICENSE
```

## Installation

The modules use relative imports (`from .module import ...`), so they're meant to be used as a package — `__init__.py` (tracked in the repo, re-exporting each class above) is what makes that work. Either:

- **Just the dependencies** — install from `requirements.txt` and put the repo's *parent* directory on `sys.path` yourself (see `example_data/example.py` for a working example of that setup) so `Portfolio_calculator_library` resolves as a package:
  ```bash
  pip install -r requirements.txt
  ```
- **Install the package itself** — from one directory above the repo, so it installs as the `Portfolio_calculator_library` package (matches `pyproject.toml`'s packaging config) and is importable like any other installed package, no `sys.path` setup needed:
  ```bash
  pip install ./Portfolio_calculator_library
  ```

`Plot`'s `path_to_save_fig` option (saving a chart to a file instead of displaying it) additionally needs `kaleido` — `pip install kaleido`, or `pip install ".[charts-export]"` if you installed the package itself.

## Usage example

```python
from Portfolio_calculator_library import Portfolio, Plot

# Each source is a directory of per-transaction-state CSVs: buy.csv, sell.csv,
# sell_tax.csv, dividend.csv, dividend_tax.csv (stocks), buy.csv, sell.csv, sell_tax.csv
# (commodities — symbol column must be one of Commodity.TICKERS's keys, e.g. "gold";
# crypto — same shape, symbol column must be one of Crypto.TICKERS's keys, e.g. "bitcoin"),
# or buy.csv plus interest_rate.csv/inflation_rate.csv (bonds — the two rate CSVs are
# read from the bonds directory itself, not the working directory).
sources = {
    "data/stocks": "stock",
    "data/bonds": "bonds",
    "data/commodities": "commodities",
    "data/crypto": "crypto",
}

# tickers.json maps each ISIN to its yfinance ticker and native currency, e.g.
# {"US78462F1030": {"ticker": "SPY", "currency": "usd"}}
# Every buy/sell/dividend/dividend_tax/sell_tax row for that ISIN is assumed to already be in
# this same declared currency — e.g. a US stock's dividend.csv entries are assumed to be in USD,
# regardless of what currency your broker actually deposited the dividend in; convert it
# yourself first if the two differ, there's no separate per-row currency field.
# cache_dir is optional: when given, every source's computed DataFrame is cached to disk for
# the day, so re-running later today skips both the yfinance calls and the recomputation.
# force_refresh=True ignores the cache for this one run and refreshes it with fresh data —
# a one-off cold start, e.g. Portfolio(sources, ..., cache_dir=".portfolio_cache", force_refresh=True).
portfolio = Portfolio(sources, tickers_json="tickers.json", currency="usd", cache_dir=".portfolio_cache")

# To wipe the cache entirely (e.g. to reclaim space from stale/orphaned entries) instead of
# forcing a single refresh:
# from Portfolio_calculator_library.cache_library import DiskCache
# DiskCache(".portfolio_cache").clear()

print(f"Total invested: {portfolio.total_invested_money:.2f}")
print(f"Current value: {portfolio.total_current_value:.2f}")
print(f"Total revenue: {portfolio.total_revenue:.2f}")
print(portfolio.distribution_by_ticker)                # allocation by amount invested
print(portfolio.distribution_by_ticker_current_value)  # allocation by current market value
print(portfolio.distribution_by_ticker_revenue)         # allocation by share of total gains

# include_native_currency=True (pass it to Portfolio(...) above) additionally populates
# portfolio.native_data/native_currency per Stock/Commodity/Crypto ticker or symbol, isolating
# that instrument's own performance from FX movement against currency="usd" above:
# print(portfolio.native_currency["SPY"])                  # e.g. "usd"
# print(portfolio.native_data["SPY"][Portfolio.PROFIT_COLUMN].iloc[-1])

portfolio.calculate_irr()

plot = Plot(portfolio)
plot.money_plot()
plot.performance_plot(kind="candlestick")
plot.revenue_plot(include_dividends=False)
plot.allocation_plot(by="ticker", kind="pie", metric="current_value")
plot.allocation_plot(by="ticker", kind="histogram", metric="revenue")
plot.allocation_comparison_plot(by="ticker")
```

## Requirements

- Python 3.10+
- pandas
- numpy
- yfinance
- plotly
- tqdm
- kaleido (optional — only needed to save charts to a file)

See `requirements.txt`/`pyproject.toml` for exact version bounds.

## Use cases

- tracking your own investment portfolio across stocks, ETFs, and treasury bonds
- comparing performance across brokers/accounts by treating each as a separate source directory
- computing IRR and rolling returns instead of relying on a broker's own reporting
- building custom charts or reports on top of one merged portfolio DataFrame

## Roadmap

- bank account support, following the `Stock`/`Bonds`/`Commodity`/`Crypto` pattern
- validate `PolishRetailBonds` against real historical Polish retail bond rate data (supplied for review, not bundled with the library) to catch further correctness bugs like the EDO accrual issue below, and refactor `bonds_calculator_library.py`'s `_fixed_rate_bond`/`_variable_rate_bond`/`_inflationary_rate_bond` — which duplicate the same DataFrame-skeleton/accrual/tax/`is_swapped`-bonus pattern three times over — to share that logic instead
- fix `_inflationary_rate_bond`'s year-2-onward interest accrual: its `for i in range(9)` loop breaks on its very first iteration for every real (10-year) EDO bond, so `Profit` only ever reflects the first year's `initial_coupon` and silently stops growing for the rest of the holding period
- add an automated test suite (e.g. `pytest`, one module per calculator plus integration tests against fixed synthetic data) — there are currently no committed tests, so regressions like the EDO accrual bug above can ship unnoticed
- apply currency conversion to `PolishRetailBonds` — unlike `Stock`/`Commodity`/`Crypto`, it never imports `Currency`, so a bond's PLN values get summed straight into `Portfolio`'s totals with no FX applied whenever `Portfolio`'s target currency isn't PLN
- pull the bond formulas' hardcoded magic numbers (19% tax on `R`/`D` bonds vs. 0% on `T`/`E`, the `amount_of_bonds*0.1` `is_swapped` bonus) into documented, named constants, and double-check the `T`-bond 0% tax rate is actually correct
- add a CI workflow (e.g. GitHub Actions) running the test suite above on push, once it exists
- vectorize `Commodity._compute_data`'s per-row `.iterrows()` transaction-walking loop the same way
- vectorize `Crypto._compute_data`'s per-row `.iterrows()` transaction-walking loop the same way
- vectorize `PolishRetailBonds._compute_data`'s per-row `.itertuples()` bond-walking loop the same way
- fix `Portfolio.calculate_irr()`'s per-day loop: it rebuilds a growing Python list (`.iloc[:i+1].to_list()`) on every iteration, which is effectively O(n²) for a long date range — replace with a vectorized or incremental (non-list-rebuilding) approach
- vectorize `Portfolio.calculate_money_earned_between_dates_column()`'s per-day `.iterrows()` loop

## License

MIT License.
