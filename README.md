# Portfolio Library

A Python library that helps manage an investment portfolio. It enables the analysis and processing of data related to treasury bonds, stocks, and ETFs using the `pandas`, `plotly`, and `yfinance` libraries.

## Features

The library consists of four main modules:

### 📈 stock_calculator_library

Module responsible for handling stocks and ETFs.

Key features:

- fetching historical price data via the `yfinance` library,
- fetching financial instrument information,
- calculating rates of return,
- preparing data for further portfolio analysis,
- aggregating data across multiple instruments.

The module uses:

- pandas
- numpy
- yfinance

---

### 🏦 bonds_calculator_library

Module for handling Polish treasury bonds.

Among other things, it allows:

- calculating bond value over time,
- determining accrued interest,
- accounting for interest capitalization,
- analyzing redemption schedules,
- tracking investment value.

---

### 📊 portfolio_calculator_library

Module containing a set of functions operating on `pandas.DataFrame` objects.

Among other things, it allows:

- transforming portfolio data,
- combining data from different sources,
- filling in missing time-series data,
- calculating portfolio value over time,
- aggregating data by asset,
- preparing data for visualization.

This module is not responsible for fetching data — its job is to process it.

---

### plot_library

Module containing a set of functions used to generate charts.

## Example project structure

```
portfolio_library/
│
├── bonds_library.py
├── stock_library.py
├── portfolio_library.py
```

## Installation

```bash
pip install -r requirements.txt
```

or

```bash
pip install pandas numpy yfinance
```

## Usage example

```python
from stock_library import download_prices
from portfolio_library import calculate_portfolio_value

prices = download_prices(
    tickers=["VWCE.DE", "CSPX.L"],
    start="2020-01-01",
    end="2025-01-01"
)

portfolio = calculate_portfolio_value(prices)
```

## Requirements

- Python 3.10+
- pandas
- numpy
- yfinance

## Use cases

The library was designed with the following use cases in mind:

- managing your own investment portfolio,
- analyzing historical performance,
- monitoring asset value,
- analyzing treasury bonds,
- building your own investment reporting tools.

## Development

The project is developed modularly, so new classes and functions can be added to each module independently.

Planned extensions include, among others:

- dividend handling,
- tax analysis,
- exporting reports to Excel/PDF,
- support for additional data sources,
- extended portfolio statistics.

## License

MIT License.
