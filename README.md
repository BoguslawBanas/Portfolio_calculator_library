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
- once a bond matures, its cost basis and unrealized value both drop to 0 — nothing is left held, the redemption proceeds became cash, which this library doesn't separately track — while its accrued interest freezes at the final value and persists in `Profit`/`total_revenue`/`distribution_by_ticker_revenue` forever after, since it was realized at redemption rather than lost. Matching `Stock`'s own convention, `total_money_invested`/`distribution_by_ticker` stay at their lifetime, gross-ever-bought value even for a fully-matured/cancelled type; `total_money_currently_invested`/`distribution_by_ticker_currently_invested` are the separate figures that track only what's currently held per type — a fully-matured type drops to 0% in both of those instead, exactly mirroring its 0% in `distribution_by_ticker_current_value`
- interest always accrues on each bond's full nominal value (100 zł) regardless of `is_swapped`'s discount, and a flat tax is applied uniformly — neither the exact tax treatment nor the discount amount is stated in the *listy emisyjne* themselves (tax law and bank-quoted exchange pricing aren't issuance terms), so both are asserted as named constants/defaults rather than sourced per type
- optional `tax_rate` — % withholding tax applied uniformly to every bond's interest, defaulting to `PolishRetailBonds.TAX_RATE` (19%, the standard *podatek Belki* rate) — override it for a situation the flat default doesn't fit, e.g. `tax_rate=0.0` for a tax-exempt account (IKE/IKZE)
- optional `bond_types_json` — a JSON file of `{code: {"swap_discount": <float>, "early_redemption_fee": <float>}}`, merged on top of `BOND_TYPES`' built-in `swap_discount`/`early_redemption_fee` per type (either field may be omitted to leave that one at its built-in value) — for a real issuance where those two asserted defaults don't hold; doesn't touch the taxonomy itself (`period_months`/`num_periods`/`compounding`/`rate_source`), which are issuance facts, not assertions
- optional `currency_to` — every bond is issued in PLN (`PolishRetailBonds.NATIVE_CURRENCY`), converted via `Currency` the same way `Stock`/`Commodity`/`Crypto` convert their own native-currency prices; defaults to `'PLN'`, a no-op. Each day's own accrued interest is converted at *that day's own* FX rate before accumulating (the same convention `Stock` uses for dividends/realized profit — that event's own rate baked in once, not re-marked later), so a matured bond's frozen `Profit` stays frozen in `currency_to` terms too, instead of drifting with FX after redemption despite nothing further actually happening to it. `Portfolio` passes its own `currency_to` through automatically
- optional `cancel.csv` — records that some or all of a holding (identified by its own `date`/`isin` pair, matching its `buy.csv` row) was *actually* redeemed early in real life, and how many units (`amount_of_units`) were redeemed on `cancel_date`. A partial cancellation splits the holding into independent tranches — the cancelled units stop accruing at `cancel_date` (paying out gross accrued interest minus that type's `early_redemption_fee`, floored at 0 so redeeming early never returns less than what was originally paid in) while the rest keep accruing normally; a holding can appear more than once in `cancel.csv` for several partial cancellations over time. `OTS`'s fee forfeits *all* interest accrued that period rather than a flat zł amount (`early_redemption_fee=float('inf')`, reduced to 0 by the same floor every other type uses); the other seven types' fees (0.50 zł/bond for `ROR` up to 3.00 zł/bond for `EDO`/`ROD`) are typical values, not transcribed from one specific real issuance the way the rest of `BOND_TYPES` is — treat them the same as `swap_discount`: asserted, worth double-checking against a current, authoritative source, and overridable via `bond_types_json` above for an issuance where the default doesn't hold
- `Dividend` column, matching `Stock`'s own — 0 while a holding is still open (its accrued value lives entirely in the unrealized column below until then), then jumps once, at redemption (natural maturity or an earlier `cancel.csv` cancellation), to everything ever accrued for that holding and stays there — mirroring a bond's real cash flow (nothing paid until redemption, then it all is), unlike `Stock`'s per-payment `dividend.csv` rows
- `_bond_dataframe`'s per-holding accrual is numpy end to end — plain arrays addressed by integer day-offset from the holding's own purchase date, not a `pd.Series` walked via label-based `.loc`/`reindex`/`ffill` — since every date in play (period boundaries, `cancel_date`/maturity, today) is always a whole number of days apart. Several times faster per holding, more so the longer its span (~3x for a 1-year `ROR`, ~6x for a 10-year `EDO` in an internal benchmark)

### 💱 `currency_calculator_library.Currency`

Daily FX rates via `yfinance`, used internally by `Stock`/`Commodity`/`Crypto`/`PolishRetailBonds` (and usable standalone) to convert foreign-currency instruments to a base currency. Same-currency conversions short-circuit to a flat 1.0 rate — no network call needed. A currency pair `yfinance` doesn't quote raises `ValueError` at construction time instead of silently `ffill()`/`bfill()`ing into a column of all-`NaN`.

- `get_cached_currency(currency_cache, ...)` — the module-level function `Stock`/`Commodity`/`Crypto`/`PolishRetailBonds` all call instead of constructing `Currency` directly, so holdings that share a currency pair (several same-currency tickers within one source, or two different sources converting the same pair, e.g. `Commodity` and `Crypto` both quoting in `usd`) reuse one fetch instead of each fetching their own. `Portfolio` builds one `currency_cache` dict per construction and passes it down to every source automatically (see `Portfolio` below); a `currency_cache=None` (the default for every class used standalone) skips this and always fetches fresh, unchanged from before this existed. A cached entry is reused whenever its own `start_date` already covers what's being asked for; otherwise it's re-fetched with the wider of the two ranges and the cache entry is replaced, so a later, earlier-starting call still only re-fetches once more rather than missing the cache forever

### 📊 `portfolio_calculator_library.Portfolio`

Combines one or more `Stock`/`PolishRetailBonds`/`Commodity`/`Crypto`/`BankAccount` sources into a single portfolio-level DataFrame.

- builds and sums per-instrument DataFrames (`Money_invested`, `Profit_without_dividends`, `Profit`, `Profit_without_realized`, `Profit_excluding_dividends`) across every source, regardless of asset type
- `Profit_without_dividends`, despite the name, is the *unrealized* component only — current market value of what's still held minus its cost basis (`total_current_value`/`distribution_by_ticker_current_value` are built on it) — so it excludes realized profit too, not just dividends. The two columns below spell out each of the other three combinations
- `Profit_without_realized` — profit still attributable to positions as they stand today: unrealized gain on whatever's still held plus dividends collected along the way, excluding gain/loss already locked in by a sell
- `Profit_excluding_dividends` — the literal complement of `Profit_without_dividends`'s name: unrealized gain plus realized gain/loss, excluding only dividends
- Both are always present (unlike `Dividend` below), since every source contributes them — for `PolishRetailBonds`/`BankAccount`, which don't track a separate realized-profit stream, both simply equal `Profit`/`Profit_without_dividends` respectively; for `Commodity`/`Crypto`, which pay no dividends, `Profit_excluding_dividends` is a no-op equal to `Profit`
- allocation by ticker/directory/currency, by amount invested (cost basis), by amount *currently* invested, by current market value (cost basis still held plus unrealized gain), or by revenue (each position's share of total portfolio gains — can be negative for a losing position)
- `distribution_by_currency`/`_current_value`/`_revenue` — allocation by each position's own *native* currency (a US stock's `usd`, a Polish bond's `PLN`, ...), always populated (no flag needed, unlike `include_native_currency` below) — answers "how much of my portfolio is actually USD- vs. EUR- vs. PLN-denominated", independent of `currency_to` (the single currency `self.data`/totals are already converted to and summed in)
- `total_money_currently_invested`/`distribution_by_directory`/`_ticker`/`_currency_currently_invested` — the same allocation-by-amount-invested figures as `total_money_invested`/`distribution_by_directory`/`_ticker`/`_currency`, but by cost basis of what's actually still held today rather than lifetime-gross-ever-bought/deposited/issued. Every source (`Stock`/`Commodity`/`Crypto` sells, `PolishRetailBonds` maturity/cancellation, `BankAccount` withdrawals) tracks both figures independently, and they diverge once anything's actually been sold/matured/cancelled/withdrawn (a fully-closed-out position drops to 0% here, while the lifetime figure keeps its historical share) — see the Distribution metrics section below
- builds one shared `currency_cache` per construction and passes it to every `Stock`/`Commodity`/`Crypto`/`PolishRetailBonds` source it builds, so a currency pair needed by more than one source/ticker/holding is only fetched once (see `get_cached_currency` under `Currency` above)
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

- prices via `yfinance` futures tickers (`GC=F`, `SI=F`, ...), all USD-quoted, converted to the target currency via `Currency` for marking a still-held position to market
- unlike `Stock`, a `buy.csv` row carries no `price_of_unit`/`fee` — its cost basis is `money_invested`/`currency`, the actual amount paid and the currency it was paid in (e.g. a coin dealer's price, which can diverge from the futures market price by more than a flat percentage), converted to the target currency via that currency's own `yfinance`-fetched FX rate on the row's own transaction date, independent of the futures price entirely. A `buy.csv` row also carries `amount_of_units` alongside a `unit` column (`'troy_ounce'` or `'gram'`), converted internally (via grams) to whichever unit that symbol's own `yfinance` ticker actually quotes a price per — troy ounce for gold/silver/platinum/palladium, pound for copper — purely to size the held position for marking it to market later, with no bearing on cost basis. `sell.csv` is unchanged from before: no price of its own — every sale's proceeds are still that day's own fetched `yfinance` close, not a manually recorded amount — just `amount_of_units` (already in that symbol's own native quote unit, no `unit` column of its own)
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

## Distribution metrics

Every calculator (`Stock`/`Commodity`/`Crypto`/`PolishRetailBonds`/`BankAccount`, and `Portfolio` itself) exposes the same four `{key: percentage}`-shaped dicts, keyed by ticker/symbol/bond-type/account. All four are computed the same way — each key's own "value" divided by the sum of every key's value, ×100 — the only thing that differs is what "value" measures, and that measurement differs by asset class:

| | `distribution_by_ticker` (amount ever invested) | `distribution_by_ticker_currently_invested` (amount currently invested) | `distribution_by_ticker_current_value` (current market value) | `distribution_by_ticker_revenue` (share of total gain) |
|---|---|---|---|---|
| `Stock` | Sum of every `buy` row's cost basis, ever — **never reduced by a sell** | `Money_invested`'s last value — cost basis of units still held today | `Money_invested + Profit_without_dividends` (last value) — cost basis still held plus its unrealized gain | `Profit`'s last value — all-time gain: unrealized + dividends + realized, can be negative |
| `Commodity` | Same as `Stock` (cost basis is `money_invested`, recorded directly, like `Stock`'s `price_of_unit` — not derived from `yfinance`) | Same as `Stock` | Same as `Stock` | Same as `Stock`, minus dividends (`Commodity` pays none) |
| `Crypto` | Same as `Stock` (buy price is user-recorded, like `Stock`) | Same as `Stock` | Same as `Stock` | Same as `Stock`, minus dividends (`Crypto` pays none) |
| `PolishRetailBonds` | Sum of every holding/tranche's own cost basis at purchase, per type — **never reduced by maturity or cancellation** | Each type's current `Money_invested` (its merged type-level DataFrame's last value) — drops to 0 once every holding of that type has matured or been fully cancelled | `Money_invested + Profit_without_dividends` (both 0 once matured) — 0% past maturity | `Profit`'s last value — **persists past maturity**, since interest realized at redemption isn't lost from history |
| `BankAccount` | Sum of every `deposit` row's own amount, per account — **never reduced by a withdrawal** | Each account's current `Money_invested` (its own last value) — current balance, net of withdrawals | `Money_invested + Profit_without_dividends` (last value) — balance plus all interest accrued so far, capitalized or not | `Profit`'s last value — all interest ever accrued for that account |

For every asset class, the first two columns coincide until something's actually been sold/matured/cancelled/withdrawn — at that point only the "amount currently invested" column drops, while "amount ever invested" keeps that position's full historical share (a fully-sold `Stock` ticker, a matured `PolishRetailBonds` type, or a fully-withdrawn `BankAccount` account all behave the same way here).

`Portfolio` builds its own three dict families on top of the tables above, not by re-deriving them from scratch:
- `distribution_by_directory*` — one entry per source directory, value = that source's own `total_money_invested`/`total_money_currently_invested`/`total_current_value`/`total_revenue` (the class-level totals the table above rolls up into), not broken down further by ticker.
- `distribution_by_ticker*` — each source's own already-computed per-ticker dict (the table above) is re-expanded back to an absolute amount and summed across every source that happens to share the same ticker/type/account key, then the combined total is renormalized to a percentage of the whole portfolio.
- `distribution_by_currency*` — the same merge as `distribution_by_ticker*` above, but grouped by each holding's own *native* currency instead of its ticker key. `BankAccount` never contributes here — it has no per-ticker native currency to look up (everything is assumed to already be in one currency), so a `BankAccount`-only portfolio's `distribution_by_currency*` dicts stay empty.

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

print(f"Total invested (lifetime): {portfolio.total_money_invested:.2f}")
print(f"Total invested (currently held): {portfolio.total_money_currently_invested:.2f}")
print(f"Current value: {portfolio.total_current_value:.2f}")
print(f"Total revenue: {portfolio.total_revenue:.2f}")
print(portfolio.distribution_by_ticker)                     # allocation by amount ever invested (lifetime)
print(portfolio.distribution_by_ticker_currently_invested)  # allocation by amount currently invested
print(portfolio.distribution_by_ticker_current_value)       # allocation by current market value
print(portfolio.distribution_by_ticker_revenue)              # allocation by share of total gains

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

- `PolishRetailBonds`' `Dividend` column reuses Stock's name for something that behaves quite differently: it stays at 0 the entire time a bond is held, then jumps once, at redemption/cancellation, to the whole accrued amount — closer in spirit to Stock/Commodity/Crypto's `Realized_profit` (a locked-in gain, recognized once, per event) than to a real per-payment dividend. Renaming it to `Realized_profit` instead would fix the mislabeling and let bonds merge into Portfolio's own `Realized_profit`/`Profit_without_realized`/`Profit_excluding_dividends` the same way Stock/Commodity/Crypto already do, rather than being the one source that fakes a `Dividend` column to fit in — a relabeling only, no change to the numbers themselves
- separately, and a bigger change: `PolishRetailBonds` currently treats every bond type identically — interest accrues as unrealized (`Profit_without_dividends`) continuously and is only recognized as realized once, at final redemption. That matches the four "compounding" types (`TOS`/`ROS`/`EDO`/`ROD`, which really do pay nothing until maturity), but not the four "flat" types (`OTS`/`ROR`/`DOR`/`COI`), which in real life pay interest out at the end of every period — cash that arguably should exit the position and register as realized at each period boundary instead of sitting modeled as still-unrealized for years. Worth a closer look at whether/how to model per-period realization for the flat types; unlike the renaming above, this would change actual reported numbers for those four types, not just labels
- `Plot.allocation_plot`'s `kind='histogram'` path — the one its own docstring recommends in place of `kind='pie'` whenever `metric='revenue'` can go negative (a pie chart has no honest way to draw a negative-share wedge) — doesn't actually make a losing position's bar stand out: it colors every bar from the fixed `CATEGORICAL_COLORS` palette regardless of sign, so a negative share just quietly dips below zero on the axis. `period_return_bar_plot` a few methods up in the same file already has this solved (`COLOR_GOOD`/`COLOR_CRITICAL` based on sign) - reusing that convention in `allocation_plot`'s histogram branch would make losing positions jump out at a glance

## License

MIT License.
