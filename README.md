# Portfolio Library

A Python library for tracking the performance of an investment portfolio, combining stocks/ETFs and Polish retail treasury bonds into a single aggregated view. Price data comes from `yfinance`; every chart is built with `plotly`.

## Features

The library is organized as one class per module.

### 📈 `stock_calculator_library.Stock`

Fetches stock/ETF price history and turns a set of buy/sell/dividend transactions into a daily investment/profit DataFrame.

- historical prices via `yfinance`, forward-filled to a continuous daily calendar
- foreign-currency instruments converted to a target currency via `Currency`
- full or partial sells, tracked against a running average cost basis
- dividends and dividend/sell tax tracked separately from price gains
- optional progress-bar hook, driven by `Portfolio` (see below)

### 🏦 `bonds_calculator_library.Bonds`

Computes the value over time of Polish retail treasury bonds, given a directory of holdings plus two rate CSVs (`interest_rate.csv`, `inflation_rate.csv`).

- fixed-rate (`T`, 3-year), variable-rate (`R`/`D`, 1-/2-year), and inflation-indexed (`E`, 10-year, EDO) bonds
- daily accrued interest, compounded per bond type's own rules

### 💱 `currency_calculator_library.Currency`

Daily FX rates via `yfinance`, used internally by `Stock` (and usable standalone) to convert foreign-currency instruments to a base currency. Same-currency conversions short-circuit to a flat 1.0 rate — no network call needed.

### 📊 `portfolio_calculator_library.Portfolio`

Combines one or more `Stock`/`Bonds` sources into a single portfolio-level DataFrame.

- builds and sums per-instrument DataFrames (`Money_invested`, `Profit_without_dividends`, `Profit`) across every source, regardless of asset type
- shows a `tqdm` progress bar while fetching, sized to the actual number of tickers/bond directories up front
- `calculate_irr()` — incremental Newton's-method internal rate of return
- `calculate_money_earned_between_dates()` / `calculate_money_earned_between_dates_column()` — profit over a rolling date window
- `resample()` — downsample to daily/weekly/monthly/quarterly/yearly buckets

### 📉 `plot_library.Plot`

Charts for a constructed `Portfolio`, built entirely on `plotly`:

- `money_plot` — money invested vs. total revenue, as overlaid lines or a stacked area
- `performance_plot` — IRR over time, as a line or a candlestick chart
- `period_return_bar_plot` — rolling daily return, colored by sign
- `allocation_plot` — portfolio allocation by ticker or by source directory, as a pie or bar chart

### 🪙 `commodity_calculator_library.Commodity`

Turns a set of buy/sell transactions in physical commodities (gold, silver, platinum, palladium, copper) into a daily investment/profit DataFrame, the same way `Stock` does for stocks/ETFs.

- prices via `yfinance` futures tickers (`GC=F`, `SI=F`, ...), all USD-quoted, converted to the target currency via `Currency`
- full or partial sells, tracked against a running average cost basis, same as `Stock`
- no dividends — `Profit` is unrealized plus realized gain
- optional progress-bar hook, driven by `Portfolio` (see below)

### ₿ `crypto_calculator_library.Crypto`

Turns a set of buy/sell transactions in crypto (bitcoin, ethereum, ...) into a daily investment/profit DataFrame — the same pattern as `Commodity`, minus the physical-delivery framing.

- prices via `yfinance` USD-quoted tickers (`BTC-USD`, `ETH-USD`, ...), converted to the target currency via `Currency`
- full or partial sells, tracked against a running average cost basis, same as `Stock`/`Commodity`
- no dividends — `Profit` is unrealized plus realized gain
- units rounded to 8 decimal places (vs. `Commodity`'s 4) for fractional holdings
- optional progress-bar hook, driven by `Portfolio` (see below)

### 🚧 In progress

`bank_account_calculator_library.py` is an early, function-based prototype that predates `Stock`/`Bonds`/`Commodity`/`Crypto`'s class-based design and isn't wired into `Portfolio` yet.

## Project structure

```
Portfolio_calculator_library/
│
├── stock_calculator_library.py          # Stock
├── bonds_calculator_library.py          # Bonds
├── commodity_calculator_library.py      # Commodity
├── crypto_calculator_library.py         # Crypto
├── currency_calculator_library.py       # Currency
├── portfolio_calculator_library.py      # Portfolio
├── plot_library.py                      # Plot
├── bank_account_calculator_library.py   # prototype, not yet integrated
└── LICENSE
```

## Installation

There's no `requirements.txt`/`pyproject.toml` yet — install the dependencies directly:

```bash
pip install pandas numpy yfinance plotly tqdm
```

`Plot`'s `path_to_save_fig` option (saving a chart to a file instead of displaying it) additionally needs `kaleido`:

```bash
pip install kaleido
```

The modules use relative imports (`from .module import ...`), so they're meant to be used as a package. There's currently no `__init__.py` in the repo — add an empty one at the project root before importing:

```
Portfolio_calculator_library/
├── __init__.py   # add this
├── stock_calculator_library.py
└── ...
```

## Usage example

```python
from Portfolio_calculator_library.portfolio_calculator_library import Portfolio
from Portfolio_calculator_library.plot_library import Plot

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
portfolio = Portfolio(sources, tickers_json="tickers.json", currency="usd")

print(f"Total invested: {portfolio.total_invested_money:.2f}")
print(portfolio.distribution_by_ticker)

portfolio.calculate_irr()

plot = Plot(portfolio)
plot.money_plot()
plot.performance_plot(kind="candlestick")
plot.allocation_plot(by="ticker", kind="pie")
```

## Requirements

- Python 3.10+
- pandas
- numpy
- yfinance
- plotly
- tqdm
- kaleido (optional — only needed to save charts to a file)

## Use cases

- tracking your own investment portfolio across stocks, ETFs, and treasury bonds
- comparing performance across brokers/accounts by treating each as a separate source directory
- computing IRR and rolling returns instead of relying on a broker's own reporting
- building custom charts or reports on top of one merged portfolio DataFrame

## Roadmap

- bank account support, following the `Stock`/`Bonds`/`Commodity`/`Crypto` pattern
- allocation by current market value, not just amount invested
- revenue allocation — how much each source/ticker's revenue contributes to total portfolio revenue
- a revenue-only chart in `Plot` (skipping IRR), with dividends optionally included or excluded
- option to compute revenue in each instrument's native currency, instead of always converting to the portfolio's target currency
- caching computed DataFrames to disk instead of re-fetching/recomputing on every run

## License

MIT License.
