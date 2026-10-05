# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project is
pre-1.0: any release before 1.0.0 may include breaking changes to the public API, data formats,
or file layout.

## [0.4.2] - 2026-10-05

### Added
- `total_capital_invested`/`distribution_by_ticker_capital_invested` on every calculator
  (`Stock`, `Commodity`, `Crypto`, `PolishRetailBonds`, `BankAccount`, via the new
  `calculator_mixins.CapitalInvestedMixin`), plus `distribution_by_directory`/`_currency_capital_invested`
  on `Portfolio`: the most of the user's own money ever tied up in each position at once - unlike
  `total_money_invested`, money sold and bought back into the same position isn't counted twice.
- `Portfolio.total_capital_value` and `distribution_by_ticker`/`_directory`/
  `_currency_capital_value`: allocation by capital invested + `Profit` - everything a position has
  paid back plus the market value of what's still held.

### Changed
- `Plot.allocation_comparison_plot` now compares capital invested against capital value
  (`_capital_value`) instead of lifetime amount invested against `_total_value` (currently
  invested + revenue). The two bars used to start from different invested figures, so a closed
  position looked like it had shrunk even when it made money, and money recycled within a
  position was counted as invested each time; now the gap between them is that position's revenue.
- `Plot.allocation_comparison_plot` now scales both bars as a % of the same total (total capital
  invested) instead of each as a share of its own total, so a capital value bar taller than its
  invested bar always means that position gained. The y-axis is now "% of amount invested". Keys
  present on only one side are shown (as 0 on the other) instead of being dropped, and a zero
  line is drawn when any bar is negative.

### Fixed
- `PolishRetailBonds.distribution_by_ticker_currently_invested` (and `distribution_by_ticker`)
  now keep every bond type at 0.0 when nothing is held, instead of becoming an empty dict, which
  made fully matured bond types disappear from `Portfolio`'s per-ticker allocations.

## [0.4.1] - 2026-09-29

### Added
- `concurrency_library.Concurrency` — `Stock`/`Commodity`/`Crypto` now fetch their own tickers'/
  symbols' price history on a bounded thread pool (`max_workers=4` by default) instead of one at
  a time, cutting wall-clock load time for a source with many holdings. Bounded rather than one
  thread per ticker, since `PriceSource`'s own retry/backoff would otherwise compound under many
  parallel retries after a rate-limit response.
- `Portfolio`'s new `max_workers` argument is passed through to every `Stock`/`Commodity`/
  `Crypto` source it builds; `max_workers=1` restores the previous, fully sequential behavior.

### Changed
- `get_cached_currency`'s shared `currency_cache` access is now guarded by a lock, so several
  tickers racing to fetch the same currency pair concurrently serialize into one fetch instead of
  each fetching it independently.

## [0.4.0] - 2026-09-29

### Added
- `price_source_library.PriceSource` — the single seam every `yfinance` price-history fetch now
  goes through (`Stock`/`Commodity`/`Crypto`/`Currency`/`Benchmark`, previously five independent
  `yf.Ticker(...).history(...)` call sites). Retries a failed fetch with exponential backoff (up
  to 3 attempts by default) instead of one transient network/rate-limit failure aborting the
  whole `Portfolio` construction.

## [0.3.3] - 2026-09-28

### Added
- `Plot.allocation_over_time_plot` — portfolio allocation by source directory, evolving over
  time, as overlaid lines or a stacked area, by amount invested or current market value.
- `Portfolio.sources_by_directory` — each constructed source instance, kept around for
  `allocation_over_time_plot` to read its daily DataFrame from.

### Changed
- CI now also runs the test suite against Python 3.14.

## [0.3.2] - 2026-09-28

### Added
- `Plot.money_plot`/`performance_plot`/`revenue_plot` accept an optional `benchmark` (a
  `Benchmark`, same as `benchmark_comparison_plot`) to overlay a same-shaped line from it -
  `money_plot`/`revenue_plot` overlay its revenue/profit (a money-value comparison),
  `performance_plot` (`kind='plot'` only) its IRR.

### Changed
- `Plot.benchmark_comparison_plot` is now equivalent to
  `performance_plot(kind='plot', benchmark=...)`, which supersedes it; kept unchanged for
  backwards compatibility.

## [0.3.1] - 2026-09-28

### Added
- `Plot.allocation_plot` accepts `by='currency'`, charting allocation by each position's own
  native currency (FX exposure) alongside the existing `'ticker'`/`'directory'`.
- `Plot.dividend_income_plot` — dividends actually received per resample period, as bars, unlike
  `revenue_plot`'s cumulative dividend line.
- `Plot.rolling_return_plot` — rolling annualized return (%) over a trailing window, as a line.

## [0.3.0] - 2026-09-28

### Added
- `Plot.drawdown_plot` — total value's running peak-to-trough decline over time, as a percentage
  off its own running all-time high.
- `Plot.cashflow_plot` — net contributions/withdrawals per resample period, as bars colored by
  sign.
- `Plot.realized_vs_unrealized_profit_plot` — `Profit` split into its realized and unrealized
  (including dividends) components, as a stacked area.

## [0.2.3] - 2026-09-24

### Added
- `Portfolio.total_value` and `distribution_by_ticker_total_value`/
  `distribution_by_directory_total_value`/`distribution_by_currency_total_value`: allocation by
  `Money_invested + Profit` (currently held cost basis plus every gain ever made, realized
  included). Documented in the README's new "Total value" section.

### Changed
- `Plot.allocation_comparison_plot` now compares allocation by amount invested against
  allocation by total value (`_total_value`) instead of current market value (`_current_value`).
  The second bar is renamed from "Current value" to "Total value". A sold/matured position now
  keeps its realized gain there instead of dropping to 0%.

## [0.2.2] - 2026-09-23

### Changed
- Moved the Roadmap out of the README into its own `ROADMAP.md`.

## [0.2.1] - 2026-09-23

### Added
- `CHANGELOG.md`.
- A note in the README that the library is pre-1.0 and may change shape drastically before a
  1.0.0 release.

## [0.2.0] - 2026-09-23

### Changed
- Monetary values (`Money_invested`, `Profit` and its variants, `Dividend`, `Realized_profit`,
  every `total_money_invested`/`total_money_currently_invested`/`total_current_value`/
  `total_revenue` attribute, and the matching DataFrame columns) are now `decimal.Decimal`
  instead of `float`, so summing many of them stays exact instead of drifting.
- `Portfolio.simulate_benchmark`/`Benchmark` now accepts a weighted basket of tickers
  (e.g. `{'SPY': 60, 'GLD': 40}`), not just a single ticker.

### Added
- Automated tests for `plot_library.Plot`.
- A GitHub Actions workflow running the test suite on push/PR to `main`.

## [0.1.0] - 2026-09-09

### Added
- Initial versioned release: `pyproject.toml`/`requirements.txt`, package layout, and the first
  automated pytest test suite.
