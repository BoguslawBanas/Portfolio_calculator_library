# Portfolio Library

A Python library for tracking the performance of an investment portfolio, combining stocks/ETFs, Polish retail treasury bonds, physical commodities, crypto, and bank accounts into a single aggregated view. Price data comes from `yfinance`; every chart is built with `plotly`.

## Features

The library is organized as one class per module. Every one of them (`Portfolio`, `Stock`, `PolishRetailBonds`, `Commodity`, `Crypto`, `BankAccount`) implements `__repr__`, showing a quick `invested`/`current_value`/`revenue` summary instead of the default `<...object at 0x...>` — handy in a REPL/notebook.

### 📈 `stock_calculator_library.Stock`

Fetches stock/ETF price history and turns a set of buy/sell/dividend transactions into a daily investment/profit DataFrame.

- historical prices via `yfinance`, forward-filled to a continuous daily calendar
- foreign-currency instruments converted to a target currency via `Currency`
- full or partial sells, tracked against a running average cost basis
- dividends and dividend/sell tax tracked separately from price gains — the `dividend`/`dividend_tax` figures in the CSV are assumed to already be in that ticker's own declared currency (`tickers.json`'s `currency` field, the same one its buy/sell rows use), not necessarily the currency your broker actually paid the dividend in; convert it yourself first if the two differ
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `include_native_currency` — also computes each ticker's DataFrame in its own native currency (`self.native_data[ticker]`/`self.native_currency[ticker]`), isolating its own performance from FX movement against the target currency
- an invalid/delisted ticker raises `ValueError` at construction time instead of silently continuing — `yfinance` returns an empty (not an error) result for one, which would otherwise `ffill()` into a `Close` column of all-`NaN`
- splitting the raw multi-ticker transactions dataframe by ticker is a single `groupby` pass, not `iterrows()`/`pd.concat` once per row — the latter is O(n²) in transaction count (each `concat` copies the whole growing per-ticker frame), the biggest single cost for a portfolio with many transactions once price history is cached
- each ticker's lifetime invested total is computed once, inside the same pass that builds its DataFrame — not a second `Currency`/FX fetch and a second summation pass over the same rows beforehand

### 🏦 `bonds_calculator_library.PolishRetailBonds`

Computes the value over time of Polish retail treasury bonds (*obligacje detaliczne*), given a directory of holdings plus two rate CSVs (`interest_rate.csv`, `inflation_rate.csv`). Named for what it actually models — unlike a market-traded bond (a Treasury ETF, a corporate bond, anything with a `yfinance`-fetchable price, which fits `Stock`'s existing model as-is), these aren't traded on any exchange: bought directly from the Treasury, no secondary market or observable price, only redeemable early at a fixed penalty.

- all eight bond types currently sold — `OTS` (3-month, fixed), `ROR`/`DOR` (1-/2-year, variable, monthly periods), `TOS` (3-year, fixed, compounding), `COI` (4-year, first year fixed then inflation-indexed), `ROS`/`ROD` (6-/12-year family bonds, inflation-indexed, compounding, not exchangeable), `EDO` (10-year retirement, inflation-indexed, compounding) — dispatched off each holding's bond code's three-letter prefix (e.g. `ROR` out of `ROR0927`), not a single letter
- each type's period length/count, flat-payout-vs-compounding accrual, and rate source (fixed for life, external interest-rate history, or external inflation history) is one entry in `PolishRetailBonds.BOND_TYPES`, transcribed from the Ministry of Finance's own *listy emisyjne* (emission letters) for a real issuance of each type — supplied for review as `bonds_lists/*.pdf`, not bundled with the repo (see `.gitignore`)
- `is_swapped` (per holding) discounts the cost basis by that type's *cena zamiany* — the price of buying the bond by exchanging a maturing predecessor's redemption proceeds instead of paying cash (0.10 zł/bond for every exchange-eligible type except `OTS`, priced at par; no effect at all for `ROS`/`ROD`, which aren't exchangeable) — reflected in `Profit` from day one, not as a lump sum tacked onto the final day
- once a bond matures, its cost basis and unrealized value both drop to 0 — nothing is left held, the redemption proceeds became cash, which this library doesn't separately track — while its accrued interest freezes at the final value and persists in `Profit`/`total_revenue`/`distribution_by_ticker_revenue` forever after, since it was realized at redemption rather than lost. Unlike `Stock`'s `total_money_invested`/`distribution_by_ticker` (which stay at their lifetime, gross-ever-bought value even for a fully-sold ticker), `PolishRetailBonds`' own `total_money_invested`/`distribution_by_ticker` track only what's currently held per type — a fully-matured type drops to 0% in both, exactly mirroring its 0% in `distribution_by_ticker_current_value`
- interest always accrues on each bond's full nominal value (100 zł) regardless of `is_swapped`'s discount, and a flat tax is applied uniformly — neither the exact tax treatment nor the discount amount is stated in the *listy emisyjne* themselves (tax law and bank-quoted exchange pricing aren't issuance terms), so both are asserted as named constants/defaults rather than sourced per type
- optional `tax_rate` — % withholding tax applied uniformly to every bond's interest, defaulting to `PolishRetailBonds.TAX_RATE` (19%, the standard *podatek Belki* rate) — override it for a situation the flat default doesn't fit, e.g. `tax_rate=0.0` for a tax-exempt account (IKE/IKZE)
- optional `bond_types_json` — a JSON file of `{code: {"swap_discount": <float>, "early_redemption_fee": <float>}}`, merged on top of `BOND_TYPES`' built-in `swap_discount`/`early_redemption_fee` per type (either field may be omitted to leave that one at its built-in value) — for a real issuance where those two asserted defaults don't hold; doesn't touch the taxonomy itself (`period_months`/`num_periods`/`compounding`/`rate_source`), which are issuance facts, not assertions
- optional `currency_to` — every bond is issued in PLN (`PolishRetailBonds.NATIVE_CURRENCY`), converted via `Currency` the same way `Stock`/`Commodity`/`Crypto` convert their own native-currency prices; defaults to `'PLN'`, a no-op. Each day's own accrued interest is converted at *that day's own* FX rate before accumulating (the same convention `Stock` uses for dividends/realized profit — that event's own rate baked in once, not re-marked later), so a matured bond's frozen `Profit` stays frozen in `currency_to` terms too, instead of drifting with FX after redemption despite nothing further actually happening to it. `Portfolio` passes its own `currency_to` through automatically
- optional `cancel.csv` — records that some or all of a holding (identified by its own `date`/`isin` pair, matching its `buy.csv` row) was *actually* redeemed early in real life, and how many units (`amount_of_units`) were redeemed on `cancel_date`. A partial cancellation splits the holding into independent tranches — the cancelled units stop accruing at `cancel_date` (paying out gross accrued interest minus that type's `early_redemption_fee`, floored at 0 so redeeming early never returns less than what was originally paid in) while the rest keep accruing normally; a holding can appear more than once in `cancel.csv` for several partial cancellations over time. `OTS`'s fee forfeits *all* interest accrued that period rather than a flat zł amount (`early_redemption_fee=float('inf')`, reduced to 0 by the same floor every other type uses); the other seven types' fees (0.50 zł/bond for `ROR` up to 3.00 zł/bond for `EDO`/`ROD`) are typical values, not transcribed from one specific real issuance the way the rest of `BOND_TYPES` is — treat them the same as `swap_discount`: asserted, worth double-checking against a current, authoritative source, and overridable via `bond_types_json` above for an issuance where the default doesn't hold
- `Dividend` column, matching `Stock`'s own — 0 while a holding is still open (its accrued value lives entirely in the unrealized column below until then), then jumps once, at redemption (natural maturity or an earlier `cancel.csv` cancellation), to everything ever accrued for that holding and stays there — mirroring a bond's real cash flow (nothing paid until redemption, then it all is), unlike `Stock`'s per-payment `dividend.csv` rows
- `_bond_dataframe`'s per-holding accrual is numpy end to end — plain arrays addressed by integer day-offset from the holding's own purchase date, not a `pd.Series` walked via label-based `.loc`/`reindex`/`ffill` — since every date in play (period boundaries, `cancel_date`/maturity, today) is always a whole number of days apart. Several times faster per holding, more so the longer its span (~3x for a 1-year `ROR`, ~6x for a 10-year `EDO` in an internal benchmark)

### 💱 `currency_calculator_library.Currency`

Daily FX rates via `yfinance`, used internally by `Stock` (and usable standalone) to convert foreign-currency instruments to a base currency. Same-currency conversions short-circuit to a flat 1.0 rate — no network call needed. A currency pair `yfinance` doesn't quote raises `ValueError` at construction time instead of silently `ffill()`/`bfill()`ing into a column of all-`NaN`.

### 📊 `portfolio_calculator_library.Portfolio`

Combines one or more `Stock`/`PolishRetailBonds`/`Commodity`/`Crypto`/`BankAccount` sources into a single portfolio-level DataFrame.

- builds and sums per-instrument DataFrames (`Money_invested`, `Profit_without_dividends`, `Profit`, `Profit_without_realized`, `Profit_excluding_dividends`) across every source, regardless of asset type
- `Profit_without_dividends`, despite the name, is the *unrealized* component only — current market value of what's still held minus its cost basis (`total_current_value`/`distribution_by_ticker_current_value` are built on it) — so it excludes realized profit too, not just dividends. The two columns below spell out each of the other three combinations
- `Profit_without_realized` — profit still attributable to positions as they stand today: unrealized gain on whatever's still held plus dividends collected along the way, excluding gain/loss already locked in by a sell
- `Profit_excluding_dividends` — the literal complement of `Profit_without_dividends`'s name: unrealized gain plus realized gain/loss, excluding only dividends
- Both are always present (unlike `Dividend` below), since every source contributes them — for `PolishRetailBonds`/`BankAccount`, which don't track a separate realized-profit stream, both simply equal `Profit`/`Profit_without_dividends` respectively; for `Commodity`/`Crypto`, which pay no dividends, `Profit_excluding_dividends` is a no-op equal to `Profit`
- allocation by ticker/directory/currency, by amount invested (cost basis), by current market value (cost basis still held plus unrealized gain), or by revenue (each position's share of total portfolio gains — can be negative for a losing position)
- `distribution_by_currency`/`_current_value`/`_revenue` — allocation by each position's own *native* currency (a US stock's `usd`, a Polish bond's `PLN`, ...), always populated (no flag needed, unlike `include_native_currency` below) — answers "how much of my portfolio is actually USD- vs. EUR- vs. PLN-denominated", independent of `currency_to` (the single currency `self.data`/totals are already converted to and summed in)
- shows a `tqdm` progress bar while fetching, sized to the actual number of tickers/bond directories up front
- `calculate_irr()` — incremental Newton's-method internal rate of return
- `calculate_money_earned_between_dates()` / `calculate_money_earned_between_dates_column()` — profit over a rolling date window
- `resample()` — downsample to daily/weekly/monthly/quarterly/yearly buckets
- optional `cache_dir` — caches each source's computed DataFrame to disk instead of re-fetching/recomputing on every run (see `cache_library.DiskCache` below)
- optional `force_refresh` — with `cache_dir` set, forces a one-off cold start (ignores any cached entry, then overwrites it with the fresh result) without having to clear `cache_dir` yourself
- optional `include_native_currency` — collects each `Stock`/`Commodity`/`Crypto` ticker/symbol's native-currency DataFrame into `self.native_data`/`self.native_currency`, alongside the always-converted `self.data` every other feature above works from. `PolishRetailBonds`/`BankAccount` are left out — both are already single-currency with no conversion step to opt out of
- optional `commodity_tickers_json`/`crypto_tickers_json` — passed straight through to every `'commodities'`/`'crypto'` source's own `tickers_json` (see `Commodity`/`Crypto` above), never required unlike `tickers_json` above

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
- unlike `Stock`, neither `buy.csv` nor `sell.csv` carries a `price_of_unit` — every transaction's market price is always that day's own fetched `yfinance` close, not a manually recorded price. Instead a `buy.csv` row carries `amount_of_units` alongside a `unit` column (`'troy_ounce'` or `'gram'`), converted internally (via grams) to whichever unit that symbol's own `yfinance` ticker actually quotes a price per — troy ounce for gold/silver/platinum/palladium, pound for copper — so a buy recorded in either supported unit lands on the same cost basis; `sell.csv` needs only `amount_of_units` (already in that symbol's own native quote unit, no `unit` column of its own)
- full or partial sells, tracked against a running average cost basis, same as `Stock`
- no dividends — `Profit` is unrealized plus realized gain
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `include_native_currency` — also computes each symbol's DataFrame in USD (its native quote currency), same as `Stock`
- same construction-time `ValueError` as `Stock` if `yfinance` returns no price history for a symbol's futures ticker
- optional `tickers_json` — a JSON file of `{symbol: {"ticker": <yfinance futures ticker>, "quote_unit": <"troy_ounce"/"pound"/"gram">}}`, merged on top of the small built-in `TICKERS`/`QUOTE_UNIT_GRAMS` (an entry for an existing symbol overrides the built-in one) — lets a caller track another commodity without editing the library source
- same `groupby`-based (not `iterrows()`/`pd.concat`-per-row) symbol split as `Stock`

### ₿ `crypto_calculator_library.Crypto`

Turns a set of buy/sell transactions in crypto (bitcoin, ethereum, ...) into a daily investment/profit DataFrame — the same pattern as `Commodity`, minus the physical-delivery framing.

- prices via `yfinance` USD-quoted tickers (`BTC-USD`, `ETH-USD`, ...), converted to the target currency via `Currency`
- full or partial sells, tracked against a running average cost basis, same as `Stock`/`Commodity`
- no dividends — `Profit` is unrealized plus realized gain
- same construction-time `ValueError` as `Stock`/`Commodity` if `yfinance` returns no price history for a symbol's ticker
- units rounded to 8 decimal places (vs. `Commodity`'s 4) for fractional holdings
- optional progress-bar hook, driven by `Portfolio` (see below)
- optional `tickers_json` — a JSON file of `{symbol: <yfinance ticker>}`, merged on top of the small built-in `TICKERS` (an entry for an existing symbol overrides the built-in one) — lets a caller track another coin without editing the library source
- optional `include_native_currency` — also computes each symbol's DataFrame in USD (its native quote currency), same as `Stock`/`Commodity`
- same `groupby`-based (not `iterrows()`/`pd.concat`-per-row) symbol split as `Stock`/`Commodity`
- same "computed once, inside the same pass" lifetime-invested total as `Stock` — no second `Currency`/FX fetch or summation pass

### 🏛️ `bank_account_calculator_library.BankAccount`

Turns a set of deposit/withdrawal transactions into a daily balance/interest DataFrame. Like `PolishRetailBonds` used to be, there's no `yfinance` fetch and no `Currency` conversion — a bank balance isn't traded or quoted, and (unlike `PolishRetailBonds`, which now converts via its own `currency_to`) everything here is still assumed to already be in one currency.

- fixed-rate accounts (a flat annual %) or variable-rate accounts (a spread added to a rate history CSV, `interest_rate.csv` — daily `date,rate` rows, forward-filled for any gaps, a finer-grained format than `PolishRetailBonds`' monthly one)
- interest compounds via periodic capitalization (`capitalization_months`) — accrued-but-not-yet-capitalized interest earns no further interest until it's folded into the balance, so this is a genuinely sequential day-by-day accrual, unlike every other calculator's mostly-vectorized computation
- multiple accounts can share one source directory (`account` column), same as `Stock`'s per-ticker/`Commodity`'s per-symbol split
- tax on interest defaults to 19% (`BankAccount.DEFAULT_TAX`), overridable per account via an optional `tax` column
- optional progress-bar hook, driven by `Portfolio` (see below)
- same `groupby`-based (not `iterrows()`/`pd.concat`-per-row) account split as `Stock`/`Commodity`/`Crypto`

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
├── calculator_mixins.py                 # shared merge()/__repr__/_split_by_ticker()/count_tickers() behavior
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
portfolio = Portfolio(sources, tickers_json="tickers.json", currency_to="usd", cache_dir=".portfolio_cache")

# To wipe the cache entirely (e.g. to reclaim space from stale/orphaned entries) instead of
# forcing a single refresh:
# from Portfolio_calculator_library.cache_library import DiskCache
# DiskCache(".portfolio_cache").clear()

print(f"Total invested: {portfolio.total_money_invested:.2f}")
print(f"Current value: {portfolio.total_current_value:.2f}")
print(f"Total revenue: {portfolio.total_revenue:.2f}")
print(portfolio.distribution_by_ticker)                # allocation by amount invested
print(portfolio.distribution_by_ticker_current_value)  # allocation by current market value
print(portfolio.distribution_by_ticker_revenue)         # allocation by share of total gains

# distribution_by_currency/_current_value/_revenue: same three allocations, but grouped by each
# position's own NATIVE currency (e.g. {"usd": 60.0, "eur": 25.0, "PLN": 15.0}) instead of by
# ticker - always populated, no flag needed. Independent of currency_to="usd" above, which is only
# what everything gets CONVERTED to for self.data/totals, not what it natively IS.
print(portfolio.distribution_by_currency)

# include_native_currency=True (pass it to Portfolio(...) above) additionally populates
# portfolio.native_data/native_currency per Stock/Commodity/Crypto ticker or symbol, isolating
# that instrument's own performance from FX movement against currency_to="usd" above:
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

`tests/` holds an automated `pytest` suite — one module per calculator (`test_stock_calculator.py`, `test_bonds_calculator.py`, `test_commodity_calculator.py`, `test_crypto_calculator.py`, `test_bank_account_calculator.py`, `test_currency_calculator.py`, `test_cache_library.py`) plus `test_portfolio_calculator.py` for the multi-source integration layer. Every test runs against synthetic, fixed CSV data written to a temp directory — `yfinance.Ticker` is monkeypatched suite-wide (see `tests/conftest.py`) to a deterministic fake price series, so the suite needs no network access and never depends on real market data.

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

- tracking your own investment portfolio across stocks, ETFs, treasury bonds, commodities, crypto, and bank accounts
- comparing performance across brokers/accounts by treating each as a separate source directory
- computing IRR and rolling returns instead of relying on a broker's own reporting
- building custom charts or reports on top of one merged portfolio DataFrame

## Roadmap

- no de-duplication of `Currency` fetches across tickers that share a currency pair within one `Stock`/`Commodity`/`Crypto` construction — several holdings denominated in the same foreign currency each build their own `Currency(...)` and, without `cache_dir`, each hits yfinance separately for the same FX pair; fixing this well is nontrivial since each ticker's own start date can differ, so only worth doing if it's actually a bottleneck in practice
- `PolishRetailBonds`' `Dividend` column reuses Stock's name for something that behaves quite differently: it stays at 0 the entire time a bond is held, then jumps once, at redemption/cancellation, to the whole accrued amount — closer in spirit to Stock/Commodity/Crypto's `Realized_profit` (a locked-in gain, recognized once, per event) than to a real per-payment dividend. Renaming it to `Realized_profit` instead would fix the mislabeling and let bonds merge into Portfolio's own `Realized_profit`/`Profit_without_realized`/`Profit_excluding_dividends` the same way Stock/Commodity/Crypto already do, rather than being the one source that fakes a `Dividend` column to fit in — a relabeling only, no change to the numbers themselves
- separately, and a bigger change: `PolishRetailBonds` currently treats every bond type identically — interest accrues as unrealized (`Profit_without_dividends`) continuously and is only recognized as realized once, at final redemption. That matches the four "compounding" types (`TOS`/`ROS`/`EDO`/`ROD`, which really do pay nothing until maturity), but not the four "flat" types (`OTS`/`ROR`/`DOR`/`COI`), which in real life pay interest out at the end of every period — cash that arguably should exit the position and register as realized at each period boundary instead of sitting modeled as still-unrealized for years. Worth a closer look at whether/how to model per-period realization for the flat types; unlike the renaming above, this would change actual reported numbers for those four types, not just labels
- `total_money_invested`/`distribution_by_ticker` mean two different things depending on the source type, with no flag or docs at the `Portfolio` level to tell them apart: for `Stock`/`Commodity`/`Crypto` it's the lifetime gross amount ever bought, never reduced by a later sell; for `PolishRetailBonds` and `BankAccount` it's only what's currently held (a matured bond type or a withdrawn-down account drops toward 0%) — `PolishRetailBonds` documents this divergence from `Stock` in its own comments, but `BankAccount` doesn't call out that it follows the same "currently held" convention. A mixed `Portfolio`'s `total_money_invested` ends up silently summing "everything ever put in" and "what's in there right now" across its sources

## License

MIT License.
