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

Computes the value over time of Polish retail treasury bonds (*obligacje detaliczne*), given a directory of holdings plus two rate CSVs (`interest_rate.csv`, `inflation_rate.csv`). Named for what it actually models — unlike a market-traded bond (a Treasury ETF, a corporate bond, anything with a `yfinance`-fetchable price, which fits `Stock`'s existing model as-is), these aren't traded on any exchange: bought directly from the Treasury, no secondary market or observable price, only redeemable early at a fixed penalty.

- all eight bond types currently sold — `OTS` (3-month, fixed), `ROR`/`DOR` (1-/2-year, variable, monthly periods), `TOS` (3-year, fixed, compounding), `COI` (4-year, first year fixed then inflation-indexed), `ROS`/`ROD` (6-/12-year family bonds, inflation-indexed, compounding, not exchangeable), `EDO` (10-year retirement, inflation-indexed, compounding) — dispatched off each holding's bond code's three-letter prefix (e.g. `ROR` out of `ROR0927`), not a single letter
- each type's period length/count, flat-payout-vs-compounding accrual, and rate source (fixed for life, external interest-rate history, or external inflation history) is one entry in `PolishRetailBonds.BOND_TYPES`, transcribed from the Ministry of Finance's own *listy emisyjne* (emission letters) for a real issuance of each type — supplied for review as `bonds_lists/*.pdf`, not bundled with the repo (see `.gitignore`)
- `is_swapped` (per holding) discounts the cost basis by that type's *cena zamiany* — the price of buying the bond by exchanging a maturing predecessor's redemption proceeds instead of paying cash (0.10 zł/bond for every exchange-eligible type except `OTS`, priced at par; no effect at all for `ROS`/`ROD`, which aren't exchangeable) — reflected in `Profit` from day one, not as a lump sum tacked onto the final day
- once a bond matures, its cost basis and unrealized value both drop to 0 — nothing is left held, the redemption proceeds became cash, which this library doesn't separately track — while its accrued interest freezes at the final value and persists in `Profit`/`total_revenue`/`distribution_by_ticker_revenue` forever after, since it was realized at redemption rather than lost; `total_money_invested`/`distribution_by_ticker` track the lifetime amount ever put into each bond/type, unreduced by since-matured holdings — the same split `Stock`'s own `total_money_invested` (lifetime) vs. `data[Money_invested]` (currently held) draws for a fully-sold ticker
- interest always accrues on each bond's full nominal value (100 zł) regardless of `is_swapped`'s discount, and a flat 19% tax (`PolishRetailBonds.TAX_RATE`) is applied uniformly — neither the exact tax treatment nor the discount amount is stated in the *listy emisyjne* themselves (tax law and bank-quoted exchange pricing aren't issuance terms), so both are asserted as named constants rather than sourced per type
- optional `currency_to` — every bond is issued in PLN (`PolishRetailBonds.NATIVE_CURRENCY`), converted via `Currency` the same way `Stock`/`Commodity`/`Crypto` convert their own native-currency prices; defaults to `'PLN'`, a no-op. Each day's own accrued interest is converted at *that day's own* FX rate before accumulating (the same convention `Stock` uses for dividends/realized profit — that event's own rate baked in once, not re-marked later), so a matured bond's frozen `Profit` stays frozen in `currency_to` terms too, instead of drifting with FX after redemption despite nothing further actually happening to it. `Portfolio` passes its own `currency` through automatically
- optional `cancel.csv` — records that some or all of a holding (identified by its own `date`/`isin` pair, matching its `buy.csv` row) was *actually* redeemed early in real life, and how many units (`amount_of_units`) were redeemed on `cancel_date`. A partial cancellation splits the holding into independent tranches — the cancelled units stop accruing at `cancel_date` (paying out gross accrued interest minus that type's `early_redemption_fee`, floored at 0 so redeeming early never returns less than what was originally paid in) while the rest keep accruing normally; a holding can appear more than once in `cancel.csv` for several partial cancellations over time. `OTS`'s fee forfeits *all* interest accrued that period rather than a flat zł amount (`early_redemption_fee=float('inf')`, reduced to 0 by the same floor every other type uses); the other seven types' fees (0.50 zł/bond for `ROR` up to 3.00 zł/bond for `EDO`/`ROD`) are typical values, not transcribed from one specific real issuance the way the rest of `BOND_TYPES` is — treat them the same as `TAX_RATE`/`swap_discount`: asserted, worth double-checking (see Roadmap)
- `Dividend` column, matching `Stock`'s own — 0 while a holding is still open (its accrued value lives entirely in the unrealized column below until then), then jumps once, at redemption (natural maturity or an earlier `cancel.csv` cancellation), to everything ever accrued for that holding and stays there — mirroring a bond's real cash flow (nothing paid until redemption, then it all is), unlike `Stock`'s per-payment `dividend.csv` rows

### 💱 `currency_calculator_library.Currency`

Daily FX rates via `yfinance`, used internally by `Stock` (and usable standalone) to convert foreign-currency instruments to a base currency. Same-currency conversions short-circuit to a flat 1.0 rate — no network call needed.

### 📊 `portfolio_calculator_library.Portfolio`

Combines one or more `Stock`/`PolishRetailBonds`/`Commodity`/`Crypto`/`BankAccount` sources into a single portfolio-level DataFrame.

- builds and sums per-instrument DataFrames (`Money_invested`, `Profit_without_dividends`, `Profit`) across every source, regardless of asset type
- allocation by ticker/directory/currency, by amount invested (cost basis), by current market value (cost basis still held plus unrealized gain), or by revenue (each position's share of total portfolio gains — can be negative for a losing position)
- `distribution_by_currency`/`_current_value`/`_revenue` — allocation by each position's own *native* currency (a US stock's `usd`, a Polish bond's `PLN`, ...), always populated (no flag needed, unlike `include_native_currency` below) — answers "how much of my portfolio is actually USD- vs. EUR- vs. PLN-denominated", independent of `currency` (the single currency `self.data`/totals are already converted to and summed in)
- shows a `tqdm` progress bar while fetching, sized to the actual number of tickers/bond directories up front
- `calculate_irr()` — incremental Newton's-method internal rate of return
- `calculate_money_earned_between_dates()` / `calculate_money_earned_between_dates_column()` — profit over a rolling date window
- `resample()` — downsample to daily/weekly/monthly/quarterly/yearly buckets
- optional `cache_dir` — caches each source's computed DataFrame to disk instead of re-fetching/recomputing on every run (see `cache_library.DiskCache` below)
- optional `force_refresh` — with `cache_dir` set, forces a one-off cold start (ignores any cached entry, then overwrites it with the fresh result) without having to clear `cache_dir` yourself
- optional `include_native_currency` — collects each `Stock`/`Commodity`/`Crypto` ticker/symbol's native-currency DataFrame into `self.native_data`/`self.native_currency`, alongside the always-converted `self.data` every other feature above works from. `PolishRetailBonds`/`BankAccount` are left out — both are already single-currency with no conversion step to opt out of

### 💾 `cache_library.DiskCache`

Disk cache backing the optional `cache_dir`/`force_refresh` arguments on `Stock`/`PolishRetailBonds`/`Commodity`/`Crypto`/`Currency`/`BankAccount`/`Portfolio` — opt-in, off by default.

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

### 🏛️ `bank_account_calculator_library.BankAccount`

Turns a set of deposit/withdrawal transactions into a daily balance/interest DataFrame. Like `PolishRetailBonds`, there's no `yfinance` fetch and no `Currency` conversion — a bank balance isn't traded or quoted, and (same known limitation as `PolishRetailBonds`, see Roadmap) everything is assumed to already be in one currency.

- fixed-rate accounts (a flat annual %) or variable-rate accounts (a spread added to a rate history CSV, `interest_rate.csv` — daily `date,rate` rows, forward-filled for any gaps, a finer-grained format than `PolishRetailBonds`' monthly one)
- interest compounds via periodic capitalization (`capitalization_months`) — accrued-but-not-yet-capitalized interest earns no further interest until it's folded into the balance, so this is a genuinely sequential day-by-day accrual, unlike every other calculator's mostly-vectorized computation
- multiple accounts can share one source directory (`account` column), same as `Stock`'s per-ticker/`Commodity`'s per-symbol split
- tax on interest defaults to 19% (`BankAccount.DEFAULT_TAX`), overridable per account via an optional `tax` column
- optional progress-bar hook, driven by `Portfolio` (see below)

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
├── bank_account_calculator_library.py   # BankAccount
├── __init__.py                          # re-exports the classes above at the package root
├── tests/                               # pytest suite — see Testing below
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
# read from the bonds directory itself, not the working directory; an optional cancel.csv
# there too records any holding actually redeemed early - see PolishRetailBonds' Features entry),
# or deposit.csv plus an optional withdrawal.csv (bank_account — deposit.csv also carries each
# account's rate_type/rate/capitalization_months/tax; interest_rate.csv is only read for a
# "variable" rate_type account, daily date,rate rows rather than bonds' monthly ones).
sources = {
    "data/stocks": "stock",
    "data/bonds": "bonds",
    "data/commodities": "commodities",
    "data/crypto": "crypto",
    "data/bank_accounts": "bank_account",
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

# distribution_by_currency/_current_value/_revenue: same three allocations, but grouped by each
# position's own NATIVE currency (e.g. {"usd": 60.0, "eur": 25.0, "PLN": 15.0}) instead of by
# ticker - always populated, no flag needed. Independent of currency="usd" above, which is only
# what everything gets CONVERTED to for self.data/totals, not what it natively IS.
print(portfolio.distribution_by_currency)

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

## Testing

`tests/` holds an automated `pytest` suite — one module per calculator (`test_stock_calculator.py`, `test_bonds_calculator.py`, `test_commodity_calculator.py`, `test_crypto_calculator.py`, `test_currency_calculator.py`, `test_cache_library.py`) plus `test_portfolio_calculator.py` for the multi-source integration layer. Every test runs against synthetic, fixed CSV data written to a temp directory — `yfinance.Ticker` is monkeypatched suite-wide (see `tests/conftest.py`) to a deterministic fake price series, so the suite needs no network access and never depends on real market data.

```bash
pip install -e ".[test]"   # or: pip install pytest
pytest
```

`test_bonds_calculator.py` pins `PolishRetailBonds.BOND_TYPES`' whole taxonomy (period length/count, flat-vs-compounding accrual, rate source) in one test, independent of the accrual math tests, as a single place that fails loudly if the registry ever drifts from what `bonds_lists/*.pdf` (the *listy emisyjne* it was transcribed from) actually says.

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

- provide to a portfolio condtructor a json file with tax types and values
- `PolishRetailBonds.TAX_RATE` (19%, applied uniformly across all eight types), the `is_swapped` exchange-price discount (`BOND_TYPES`' `swap_discount`, sourced from each type's *cena zamiany*), and the `cancel.csv` early-redemption fee (`BOND_TYPES`' `early_redemption_fee`) are all asserted, not derived from the *listy emisyjne* — withholding tax, bank-quoted exchange pricing, and early-redemption fees are none of them issuance terms, so none appear in them; double-check all three against a current, authoritative source before relying on this for real tax reporting or an actual redemption
- add a CI workflow (e.g. GitHub Actions) running the test suite (see Testing below) on push

## License

MIT License.
