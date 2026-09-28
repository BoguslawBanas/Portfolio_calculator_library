# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project is
pre-1.0: any release before 1.0.0 may include breaking changes to the public API, data formats,
or file layout.

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
