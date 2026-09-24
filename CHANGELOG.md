# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project is
pre-1.0: any release before 1.0.0 may include breaking changes to the public API, data formats,
or file layout.

## [Unreleased]

### Added
- `Portfolio.total_value` and `distribution_by_ticker_total_value`/
  `distribution_by_directory_total_value`/`distribution_by_currency_total_value`: allocation by
  `Money_invested + Profit` (currently held cost basis plus every gain ever made, realized
  included).

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
