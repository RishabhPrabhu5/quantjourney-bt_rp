# Top-5 Strategies Explained

A walkthrough of the five example strategies picked in [`README.md`](README.md), written
for someone learning quant research. Every statement below was checked against the
source in this repo. Citations use `file:line`. **Explicit** means the strategy file
sets the value. **Engine default** means the strategy leaves it unset and the engine
fills it in.

- [0. How the engine runs a strategy (read this first)](#0-how-the-engine-runs-a-strategy-read-this-first)
- [1. W25 Continuous Futures Trend](#1-w25-continuous-futures-trend)
- [2. W18 Vol-Targeted Momentum](#2-w18-vol-targeted-momentum)
- [3. O17 Monthly Rotation (orders)](#3-o17-monthly-rotation-orders)
- [4. W01 Daily SMA Trend](#4-w01-daily-sma-trend)
- [5. W05 Monthly Inverse Volatility](#5-w05-monthly-inverse-volatility)
- [6. Comparison table](#6-comparison-table)

---

## 0. How the engine runs a strategy (read this first)

All five strategies subclass `Backtester` (`backtester/core.py`). They share the
mechanics below, so this section covers them once.

### Pipeline

`run_strategy()` runs these steps in order (`backtester/core.py:2319-2325`):

```python
self._generate_signals()     # strategy's _compute_signals()
self._generate_weights()     # strategy's _compute_weights()
self._apply_risk_model()     # optional RiskModel.adjust(weights, returns)
self._generate_positions()
self._compute_strategy_performance()   # weights path or orders path
```

`_compute_strategy_performance` sends `execution_mode="orders"` to the event-driven
order simulator. Otherwise it uses the fast weight simulator
(`backtester/core.py:1533-1537`). The default is `weight_execution="fast"`
(`backtester/core.py:299`), and none of the four weight strategies changes it.

### Weight mode ("fast"): timing

1. **Signal observed.** Row *t* of the weights DataFrame is built from data up to and
   including the close of bar *t*. All four weight strategies compute from
   `adj_close` (dividend/split-adjusted close).
2. **One-bar lag.** The engine shifts the target weights forward one bar to avoid
   look-ahead bias (`backtester/core.py:2083-2084`):
   ```python
   # Shift weights forward by 1 day to prevent look-ahead bias
   target_weights = target_weights.shift(1).fillna(0.0)
   ```
3. **Trade on a rebalance day.** On a day *d* flagged by the `RebalancePolicy`, the
   book snaps to the shifted target, which is the weights computed at the close of
   *d−1*. The trade happens at the close of *d*. Day *d*'s P&L still accrues on the old
   weights, and the new weights earn from *d+1* onward. The code comment says "trades
   execute at the close, so the rebalance-day PnL accrues on the previous segment's
   final drifted weights" (`backtester/portfolio/rebalance.py:403-416`). Between
   rebalance days, weights drift with prices (`rebalance.py:425-445`).
4. **First bar.** The first bar is always a rebalance (`rebalance.py:386-387`).
5. **Calendar flags.** `"D"` flags every bar (`rebalance.py:965-966`). `"BME"`
   (business month-end) builds a `pd.date_range(..., freq="BME")` and snaps each date
   back to the last available trading bar on or before it (`rebalance.py:1000-1022`).
6. **Fill price ignored.** `RebalancePolicy.rebalance_at` defaults to `CLOSE`
   (`rebalance.py:111`). Fast weight mode always accounts close-to-close; it warns if
   you set anything else (`rebalance.py:304-314`).

### Weight mode: engine defaults that apply to every weight strategy

| Default | Value | Where |
|---|---|---|
| Cash buffer | **5%**: every target weight is multiplied by `0.95` before rebalancing | `backtester/portfolio/portf_data.py:88`, applied at `backtester/core.py:2088-2121` |
| Transaction cost model | `FixedBpsWeightCostModel(total_bps=1.0)`, i.e. **1 bp** of traded notional, charged on buys and sells | `backtester/core.py:478-486`, `backtester/portfolio/weight_cost.py:62` |
| Cost materiality filter | `min_trade_value=0.0` (every implied trade is costed) | `weight_cost.py:63` |
| Slippage / commission models | **Not used.** Fast weight mode logs them as ignored knobs if they are set | `backtester/core.py:567-571` |
| Drift / signal / circuit-breaker / turnover triggers | All off: `drift_threshold=None`, `tracking_error_threshold=None`, `rebalance_on_signal_change=False`, `max_drawdown_trigger=None`, `max_annual_turnover=None` | `rebalance.py:129-156` |
| `min_trade_size` | `0.0`. It is not implemented in fast mode anyway (warning at `rebalance.py:315-323`) | `rebalance.py:159` |
| Cash return | The unallocated (or negative, if levered or short) cash sleeve is `1 − Σw` and earns **0%** | `rebalance.py:473-482` |

With only a calendar frequency set, the engine takes the vectorized calendar-only path
(`rebalance.py:327-338`, `654-655`).

**How weight-mode costs are computed.** `build_weight_ledger` turns weights into
implied quantities: `weight × NAV / unit_value`, where `unit_value = |price| ×
multiplier × lot_size` (`backtester/portfolio/accounting/ledger.py:565-575`). The cost
model then:

- keeps quantity changes only on rebalance-flag days (`weight_cost.py:99-100`);
- charges `|Δqty| × unit_value × total_bps / 10_000` (`weight_cost.py:105-118`);
- subtracts that dollar cost from NAV on the same bar:
  `nav_after_cost = previous_nav*(1+r) - cost` (`weight_cost.py:283-284`).

Because target quantities depend on post-cost NAV, the engine solves NAV and costs
together as a fixed point (`weight_cost.py:135-236`). Implied quantities are
fractional, not whole shares or contracts.

**Position caps.** If a strategy passes `max_position_size` in weights mode and does
**not** pass a `risk_model`, the engine wraps the cap in a
`PositionLimitModel(max_weight=..., min_weight=0, max_total_leverage=inf)`. If a custom
`risk_model` is supplied, or the strategy runs in orders mode, the cap is "accepted
for report metadata only — not enforced" (`backtester/core.py:539-558`). If
`max_position_size` is omitted, `0.10` is stored only as report metadata
(`backtester/core.py:418-421`).

### Orders mode: defaults

| Default | Value | Where |
|---|---|---|
| Fill price for MARKET orders | `fill_at="open"` of the **next** bar | `backtester/core.py:362-366`, `backtester/execution/fill_engine.py:480-481` |
| Slippage if not set | `NoSlippage()` | `fill_engine.py:105` |
| Commission if not set | `ZeroCommission()` | `fill_engine.py:106` |
| Volume participation cap | `None` (no cap) | `backtester/core.py:305`, `fill_engine.py:97` |
| Pre-trade risk | `PreTradeRisk(max_margin_utilization=None)` with `max_gross_leverage=None` and `allow_short=True`. That is pass-through: no leverage or cash limit rejects an order | `backtester/core.py:530-537`, `backtester/risk/pre_trade.py:52-78` |
| `rebalance_policy`, `risk_model`, `weight_cost_model` | Not read by the order path (logged as ignored if set) | `backtester/core.py:586-596` |

The per-bar loop runs in this order: "build bars → process pending fills → mark NAV →
strategy callback → record state" (`backtester/execution/simulator.py:81`, `149-163`).
So an order submitted inside `_compute_orders` on bar *t* can fill on bar *t+1* at the
earliest (`backtester/core.py:915-920`).

---

## 1. W25 Continuous Futures Trend

File: `strategies/example_weights_25_continuous_futures_trend.py`

### What it does
This is a diversified **long/short trend-following** strategy. Each market is held
long when its medium-term moving average is above its long-term one, and short when it
is below. Positions are sized so that calmer markets get more weight
(inverse-volatility). The file calls it a "price-return research proxy" (line 9).

- **Universe:** `["MES=F", "MNQ=F", "ZN=F", "CL=F", "GC=F", "ZC=F"]` (line 80). These are
  Yahoo provider-managed continuous futures: Micro E-mini S&P 500, Micro E-mini Nasdaq,
  10-year T-Note, WTI crude, gold and corn.
- **Period:** `2019-05-06` to `2026-01-01` (line 81). Capital $1,000,000 (line 79).
  Benchmark SPY.

### Entry / exit signals

```python
fast = close.rolling(fast_window, min_periods=fast_window).mean()      # 63
slow = close.rolling(slow_window, min_periods=slow_window).mean()      # 252
valid = fast.notna() & slow.notna()
signals = np.sign(fast - slow).where(valid, 0.0).astype(float)
```
(lines 45-53)

- **Indicator:** 63-day SMA vs. 252-day SMA of `adj_close` (lines 66, 70).
- **Signal:** `+1` (long) if SMA63 > SMA252, `−1` (short) if below, `0` if equal or
  while either SMA is still warming up. Before the 252-bar warm-up ends there are no
  positions.
- **Entry/exit:** there is no separate exit rule. A position flips when the SMA
  crossover flips. It goes flat only on an exact tie or during warm-up.
- **Timing:** signals are computed every day. The strategy only rebalances at
  month-end (`BME`), so on the last trading day of each month it moves to the weights
  computed at the previous day's close ([§0](#weight-mode-fast-timing)). A crossover
  in the middle of the month waits until month-end.

### Portfolio construction & execution
- **Mode:** `execution_mode="weights"` (line 85), fast weight accounting.
- **Sizing (inverse volatility, unit gross):**
  ```python
  volatility = close.pct_change(fill_method=None).rolling(63, min_periods=63).std() * np.sqrt(252.0)
  raw = signals / volatility.replace(0.0, np.nan)
  weights = raw.div(raw.abs().sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
  ```
  (lines 54-58). Each market's weight is ∝ `signal / annualized 63-day vol`, scaled so
  `Σ|w| = 1` (100% gross). Net exposure can be anywhere from −100% to +100%.
- The engine's 5% cash buffer then scales gross exposure to **95%** ([§0](#weight-mode-engine-defaults-that-apply-to-every-weight-strategy)).
- **Caps:** none. `max_position_size` is not passed, so `0.10` is only report metadata
  (`backtester/core.py:421`). No `risk_model` is set, so no `PositionLimitModel` is
  created and a single market can carry most of the gross.
- **RebalancePolicy:** `RebalancePolicy(frequency="BME")` (line 86), calendar-only.
  Drift, signal, circuit-breaker and turnover triggers are off (engine defaults).
- **Contract specs:** `=F` symbols resolve to built-in futures specs, e.g. `MES`
  multiplier 5 and `ZC` multiplier 50 (`backtester/execution/contract_spec.py:319-331`,
  `536`). In fast weight mode the multiplier only affects the implied contract count
  (`ledger.py:574`). P&L is `weight × adj_close return`. Margin is computed for
  reporting (`ledger.py:636`) but does not constrain anything.

### Costs
| Item | Value | Explicit / default |
|---|---|---|
| Transaction cost | 1 bp of implied traded notional per rebalance trade | Engine default (`core.py:478-486`) |
| Slippage | none modelled separately | Fast weight mode has no slippage model (`core.py:567-571`) |
| Commission | none modelled separately | Same |
| Turnover / cost gate | none (`max_annual_turnover=None`) | Engine default (`rebalance.py:156`) |
| Roll cost | **not modelled** | File docstring, lines 14-17 |

### Known limitations
- **Continuous-futures proxy:** the file says it "does not select dated contracts,
  control the roll rule, book roll slippage, apply exchange margin, or calculate
  multiplier-based PnL. Use it for signal research, not for an executable futures
  performance claim" (lines 14-17). Roll gaps in the provider's series flow straight
  into returns.
- **Price return only.** There is no collateral yield on the cash backing a futures
  book, because the cash sleeve earns 0% (`rebalance.py:473-482`). Short proceeds sit
  in that same 0% sleeve.
- No financing or borrow modelling, and no margin enforcement.
- Signals react to crossovers only at month-end. SMA63 vs. SMA252 is slow, so
  whipsaws around regime changes are expected.
- The short history (from 2019-05, when the micro contracts launched) minus a 252-bar
  warm-up leaves about 5.6 years of live signals.

---

## 2. W18 Vol-Targeted Momentum

File: `strategies/example_weights_18_vol_target_momentum.py`

### What it does
This is a **long/cash cross-sectional momentum** strategy on multi-asset ETFs. It
holds up to the 3 ETFs with the best 6-month return, but only those whose 6-month
return is positive. A **volatility-targeting overlay** then scales the whole basket
toward 15% annualized volatility, with up to 2× leverage.

- **Universe:** `SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ` (line 69): US, developed and
  emerging equities; long and intermediate Treasuries; gold; commodities; REITs.
- **Period:** `2007-01-03` to `2026-01-01` (line 70). Capital $100,000.

### Entry / exit signals

```python
LOOKBACK = 126
TOP_N = 3
...
for i in range(self.LOOKBACK, len(close)):
    momentum = (close.iloc[i] / close.iloc[i - self.LOOKBACK] - 1.0).dropna()
    picks = momentum[momentum > 0.0].nlargest(self.TOP_N).index
    signals.iloc[i, signals.columns.isin(picks)] = 1.0
```
(lines 45-54)

- **Indicator:** 126-day (~6-month) total return of `adj_close`.
- **Entry:** an ETF gets signal `1` when it is among the top 3 by momentum **and**
  its momentum is > 0 (an absolute-momentum filter).
- **Exit:** the signal goes to `0` when the ETF leaves the top 3 or its momentum turns
  ≤ 0. If fewer than 3 ETFs have positive momentum, fewer are held. If none do, the
  book is all cash.
- **Timing:** computed daily and traded at month-end (`BME`), using the previous day's
  close ([§0](#weight-mode-fast-timing)).

### Portfolio construction & execution
- **Mode:** weights (line 74).
- **Base weights:** equal weight across active names, capped at 40%:
  ```python
  active = self.signals == 1.0
  counts = active.sum(axis=1)
  return active.div(counts, axis=0).fillna(0.0).clip(upper=0.40)
  ```
  (lines 58-60). With 3 picks each gets 33.3% (100% gross). With 2 picks each gets 40%
  (80% gross). With 1 pick it gets 40%. The `clip` does not redistribute, so the
  remainder is cash.
- **Risk model:** `VolTargetModel(target_vol=0.15, lookback=42, max_leverage=2.0)`
  (line 77). In `backtester/risk/vol_target.py`:
  - The scale factor is recomputed only on the **first bar of each calendar month**,
    because `rebalance_freq` defaults to `"BMS"`, which maps to a monthly period change
    (`vol_target.py:54`, `76-86`). This default is not overridden here.
  - On a rescore day *i*, the current basket is normalized to unit gross. Its realized
    volatility is estimated on the **previous 42 daily returns** (`returns.iloc[i-42:i]`,
    so day *i* is excluded) and annualized with √252 (`vol_target.py:98-104`).
  - `scale = min(0.15 / realised_vol, 2.0)`. If realized vol ≤ 1%, the scale is 1.0
    (`vol_target.py:106-112`).
  - Every row is multiplied by the most recent scale: `out.iloc[i] = row_w * prev_scale`
    (`vol_target.py:114`). The scale is applied to the **raw** weights, so a 2-name
    basket at 80% gross can reach 160% gross.
  - Timing subtlety: the month-end (`BME`) rebalance uses the scale set at the start of
    that same month. The volatility estimate behind each trade is therefore up to a
    month old.
- **Caps:** `max_position_size=0.40` (line 75) is **not enforced by the engine**,
  because a custom `risk_model` is supplied (`backtester/core.py:539-558`). The only
  cap is the strategy's own `.clip(upper=0.40)`, which runs *before* the vol scaling.
  After scaling, one name can reach 0.40 × 2.0 = 80%, and gross exposure can reach
  200%. The engine's 5% cash buffer then scales that to at most 190%.
- **RebalancePolicy:** `RebalancePolicy(frequency="BME")` (line 76).

### Costs
| Item | Value | Explicit / default |
|---|---|---|
| Transaction cost | 1 bp of implied traded notional | Engine default (`core.py:478-486`) |
| Slippage / commission | not modelled separately | Fast weight mode (`core.py:567-571`) |
| Turnover / cost gate | none | Engine default |
| Leverage financing | **none**. Negative cash earns or costs 0% | `rebalance.py:473-482` |

Costs scale with traded notional, so a levered month (scale near 2×) costs about
twice as much in dollars as an unlevered one.

### Known limitations
- **Free leverage:** up to 1.9× gross is held with no borrowing cost. In low-vol
  regimes the backtest effectively borrows at 0%.
- The vol estimate can be up to a month stale and uses a short 42-day window, so the
  overlay is slow to cut exposure in a sudden volatility spike. The published max
  drawdown is −41% ([README](README.md)).
- The 40% "cap" is pre-overlay, and `max_position_size` is metadata only.
- A small universe of 8 ETFs means picks are concentrated.
- Returns come from `adj_close`, so dividends enter only through the provider's price
  adjustment. Nothing books dividends explicitly.

---

## 3. O17 Monthly Rotation (orders)

File: `strategies/example_orders_17_monthly_rotation_orders.py`

### What it does
This is the same idea as W18's selection step (6-month momentum, top 3 ETFs), but
written as an **event-driven strategy that submits explicit orders**. Once a month it
sells holdings that dropped out of the top 3 and buys the new entrants with market
orders. There is no volatility overlay and no positive-momentum filter.

- **Universe:** `SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ` (line 91).
- **Period:** `2007-01-03` to `2026-01-01` (line 92). Capital $100,000.
- **Order type:** `OrderType.MARKET` only (lines 73, 82).

### Entry / exit signals

```python
month_key = (date.year, date.month)
if month_key == self._last_month:
    return
self._last_month = month_key
...
momentum = close.iloc[pos_idx] / close.iloc[pos_idx - self.LOOKBACK] - 1.0
target = set(momentum.dropna().nlargest(self.TOP_N).index)
```
(lines 54-67, `LOOKBACK = 126`, `TOP_N = 3`)

- **Event:** the first bar of each new calendar month. `_compute_orders` runs once per
  bar, and the month key makes it act only once per month.
- **Indicator:** 126-day return of `adj_close` at the current bar's close.
- **Target set:** the top 3 by momentum, **including negative momentum** (unlike W18).
  The strategy is always fully rotated into 3 names.
- **Exit:** any held name (`pos > 0`) that is not in the target gets a market SELL for
  the full position (lines 70-73).
- **Entry:** any target name with no current position gets a market BUY (lines 76-82).
- **Warm-up quirk:** `_last_month` is set *before* the `pos_idx < LOOKBACK` check
  (lines 57-64). The month in which bar 126 falls is therefore skipped unless bar 126
  is its first bar, and the first rotation happens on the first bar of the following
  month.
- **Timing:** the signal is observed at the **close of the month's first trading
  day**. Orders fill at the **open of the second trading day**: the default
  `fill_at="open"` for next-bar market fills (`backtester/core.py:362-366`,
  `fill_engine.py:480-481`, `core.py:915-920`).

### Portfolio construction & execution
- **Mode:** `execution_mode="orders"` (line 96). Fills go through `FillEngine` and
  `PortfolioLedger` (`backtester/core.py:1540-1661`).
- **Sizing:** each **new entrant** gets an equal share of current NAV in whole shares:
  ```python
  per_name_nav = nav / max(1, len(target))
  shares = int(per_name_nav / bars[inst].close)
  ```
  (lines 76-82). About 33% of NAV each, sized from the signal bar's close, then
  filled at the next open.
- **No rebalancing of continuing names.** A name that stays in the top 3 is never
  resized, so weights drift between rotations and can grow beyond 33%.
- **Caps:** `max_position_size=0.40` (line 97) is **metadata only** in orders mode
  (`backtester/core.py:539-558`). No order is ever capped by it.
- **Pre-trade risk:** the default `PreTradeRisk` is pass-through for orders mode
  ([§0](#orders-mode-defaults)). Buys are sized from NAV at the signal close and fill
  at the next open, after slippage and commission. Nothing in this configuration stops
  cash from dipping slightly below zero when prices gap up.
- **RebalancePolicy:** none. The calendar logic lives inside `_compute_orders`, and the
  order path never reads a rebalance policy.
- **Prices used:** momentum uses `adj_close`, but execution and marking use the `close`
  feature (and `open` for fills), falling back to `adj_close` only if `close` is
  missing (`backtester/core.py:1560`, `2037-2041`). Whether the provider's `close` is
  adjusted depends on the data payload and is not decided in this repo.

### Costs (explicitly configured)
| Item | Value | Explicit / default | How the engine applies it |
|---|---|---|---|
| Slippage | `FixedBpsSlippage(bps=3.0)` | **Explicit** (line 99) | Buys fill at `price × (1 + 3/10 000)`, sells at `price × (1 − 3/10 000)` (`backtester/execution/slippage.py:82-83`). The theoretical price is the next bar's open (`fill_engine.py:374-391`). |
| Commission | `PerShareCommission(cost_per_share=0.005, min_per_order=1.0)` | **Explicit** (line 100) | `max(min(|qty|×0.005, |notional|×max_pct), 1.0)` per order (`backtester/execution/commission.py:64-67`). Charged incrementally across partial fills of one order (`fill_engine.py:560-588`). Deducted from cash (`backtester/portfolio/accounting/ledger.py:233-235`). |
| `max_pct` (commission cap) | `0.005` (0.5% of notional) | **Engine default** of `PerShareCommission` (`commission.py:62`) | Rarely binds for liquid ETFs |
| Volume participation cap | none | Engine default (`core.py:305`) | Orders fill in full |
| Reported transaction cost | commission + `|qty × (fill − theoretical)|` per fill | Engine (`backtester/core.py:1888-1909`) | Reporting only. The slippage is already in the fill price. |

The weight-mode 1 bp cost model and the 5% cash buffer **do not apply** here. They
belong to the weight paths only.

### Known limitations
- **Price return only if `close` is unadjusted.** The ledger books no dividends (there
  is no dividend handling in `ledger.py` or `simulator.py`). If the provider's `close`
  is unadjusted, ETF distributions are missed.
- No volatility management and no absolute-momentum filter, so it stays 100% invested
  in risky assets through drawdowns (published max DD −28%).
- Continuing names drift unmanaged, and the 40% cap is not enforced.
- Uninvested cash earns no interest.
- Market orders at the open with a flat 3 bp is a simple model. Open auctions can have
  wider effective spreads.

---

## 4. W01 Daily SMA Trend

File: `strategies/example_weights_01_sma_daily.py`

### What it does
This is the simplest template: a **long/cash trend filter** on each US sector ETF. A
sector is held when its 50-day SMA is above its 200-day SMA (a "golden cross"
regime), and the strategy is in cash otherwise. Active sectors are equal-weighted,
with each capped at 25%.

- **Universe (normal mode):** `XLB, XLE, XLF, XLI, XLK, XLP, XLU, XLV, XLY`, the nine
  SPDR sector ETFs (line 75).
- **Period:** `2000-01-03` to `2026-01-01` (line 80). Capital $100,000. Benchmark SPY.
- **Sample mode:** if `QJ_SAMPLE_DATA` is truthy, the strategy instead uses
  `AAPL, MSFT, NVDA, GOOGL, AMZN`, 2015-2025, with bundled sample data (lines 40-46,
  72-84). The published results are the normal-mode run.

### Entry / exit signals

```python
sma_fast = self.instruments_data.get_feature("SMA_50_close")
sma_slow = self.instruments_data.get_feature("SMA_200_close")
valid = sma_fast.notna() & sma_slow.notna()
return (sma_fast > sma_slow).astype(float).where(valid, 0.0)
```
(lines 53-56)

- **Indicators:** configured with `{"function": "SMA", "price_cols": ["close"],
  "params": {"periods": [50, 200]}}` (line 89). The engine maps `"close"` to the
  **`adj_close`** series before computing the SMA (`backtester/core.py:833-834`). The
  SMA itself comes from the external `quantjourney_ti` package
  (`backtester/core.py:805-808`), which is not installed in this environment, so its
  warm-up behavior was not checked here. The `notna()` guard sets the signal to 0
  wherever either SMA is missing.
- **Entry:** signal `1` while SMA50 > SMA200. **Exit:** signal `0` once SMA50 ≤ SMA200.
- **Timing:** **daily** rebalancing. Weights computed at the close of day *t−1* are
  traded at the close of day *t* ([§0](#weight-mode-fast-timing)).

### Portfolio construction & execution
- **Mode:** weights (line 85).
- **Weights:**
  ```python
  active = signals == 1.0
  counts = active.sum(axis=1)
  return active.div(counts, axis=0).fillna(0.0).clip(upper=0.25)
  ```
  (lines 60-62). With 4 or more active sectors, each gets `1/n` (≤ 25%, fully
  invested). With 1-3 active sectors, each is clipped to 25% and the rest is cash,
  e.g. 2 active means 50% invested.
- **Caps:** `max_position_size=0.25` (line 86) is passed with no `risk_model`, so the
  engine adds `PositionLimitModel(max_weight=0.25, min_weight=0, max_total_leverage=inf)`
  (`backtester/core.py:539-553`). After the strategy's own clip no weight is *above*
  0.25, so the model's pre-screen finds nothing to change
  (`backtester/risk/position_limit.py:70-74`). It is a safety net here.
- **Cash buffer:** the 5% engine default makes the maximum investment 95%.
- **RebalancePolicy:** `RebalancePolicy(frequency="D")` (line 87). Every bar is a
  rebalance (`rebalance.py:965-966`), so the book snaps back to target every day.

### Costs
| Item | Value | Explicit / default |
|---|---|---|
| Transaction cost | 1 bp of implied traded notional | Engine default (`core.py:478-486`) |
| Slippage / commission | not modelled separately | Fast weight mode |
| Turnover / cost gate | none | Engine default |

Because the book rebalances **every day**, each day's small price drift away from the
targets is traded back and charged 1 bp. On days when a sector flips, the full
position change is charged too. This is the highest-turnover rebalance cadence of the
five strategies.

### Known limitations
- A daily snap-back is more trading than most practitioners would do. A drift band
  (`drift_threshold`) or monthly calendar would cut turnover, and only 1 bp is charged
  for all of it.
- 1-3 active sectors leave large idle cash at 0%.
- SMA crossovers lag. Choppy markets cause whipsaws.
- Returns come from `adj_close`. Dividends enter only through the provider's price
  adjustment.

---

## 5. W05 Monthly Inverse Volatility

File: `strategies/example_weights_05_monthly_inverse_vol.py`

### What it does
This is a **long-only risk-budgeting** basket. It holds every ETF all the time (after
warm-up) and gives each one weight proportional to `1 / its recent volatility`, so
bonds get more capital than equities. It rebalances monthly. There is no timing
signal.

- **Universe:** `SPY, EFA, EEM, TLT, IEF, GLD, DBC, VNQ` (line 64).
- **Period:** `2007-01-03` to `2026-01-01` (line 65). Capital $100,000.

### Entry / exit signals

```python
lookback = 63
...
signals = pd.DataFrame(0.0, index=close.index, columns=close.columns)
signals.iloc[self.lookback :] = 1.0
```
(lines 42-48)

- **Entry:** every ETF is "on" from bar 63 onward. There is **no exit signal**, so the
  strategy is always long everything.
- `_compute_weights` does **not** read `self.signals` (lines 50-55). Weights become
  non-zero once the 63-day volatility exists, and the signals are informational.
- **Timing:** rebalance at month-end (`BME`) to weights from the previous day's close
  ([§0](#weight-mode-fast-timing)).

### Portfolio construction & execution
- **Mode:** weights (line 69).
- **Weights (inverse volatility, computed in the strategy):**
  ```python
  realized_vol = close.pct_change().rolling(self.lookback).std() * np.sqrt(252)
  inverse_vol = 1.0 / realized_vol.replace(0.0, np.nan)
  weights = inverse_vol.div(inverse_vol.sum(axis=1), axis=0).fillna(0.0)
  return weights.clip(upper=0.35)
  ```
  (lines 52-55). Each weight is `(1/σᵢ) / Σⱼ(1/σⱼ)` with a 63-day annualized σ, so the
  weights sum to 100%. Then each is clipped at 35%. Any clipped excess (typically from
  the lowest-vol asset, e.g. IEF) is **not redistributed** and stays in cash.
  - The engine ships a separate `backtester/risk/inverse_vol.py` risk model. This
    strategy does **not** use it and does the math inline.
- **Caps:** `max_position_size=0.35` (line 70) with no `risk_model` makes the engine add
  `PositionLimitModel(max_weight=0.35, ...)` (`backtester/core.py:539-553`). It finds
  nothing above 0.35 after the clip, so it is a no-op
  (`position_limit.py:70-74`).
- **Cash buffer:** 5% engine default.
- **RebalancePolicy:** `RebalancePolicy(frequency="BME")` (line 71). Weights drift
  between month-ends.

### Costs
| Item | Value | Explicit / default |
|---|---|---|
| Transaction cost | 1 bp of implied traded notional | Engine default (`core.py:478-486`) |
| Slippage / commission | not modelled separately | Fast weight mode |
| Turnover / cost gate | none | Engine default |

Turnover is low because the strategy is always invested and trades only small monthly
re-weightings.

### Known limitations
- Inverse volatility **ignores correlations**. It is not true risk parity (see
  `backtester/risk/risk_parity.py` for that). Correlated assets such as SPY, EFA and VNQ
  together can still dominate the risk.
- A heavy bond tilt makes results sensitive to the rate regime (2022 hurt).
- The clip leaves unredistributed cash at 0%. With the 5% buffer, the book can be
  noticeably under-invested.
- There is no leverage, so the low-vol basket also has low return (published CAGR
  5.45%). Real risk-parity funds lever this up and pay financing, which is not modelled.
- Returns come from `adj_close`. Dividends enter only through the provider's price
  adjustment.

---

## 6. Comparison table

| | W25 Continuous Futures Trend | W18 Vol-Targeted Momentum | O17 Monthly Rotation | W01 Daily SMA Trend | W05 Monthly Inverse Vol |
|---|---|---|---|---|---|
| **Mode** | weights (fast) | weights (fast) + `VolTargetModel` | **orders** (event-driven) | weights (fast) | weights (fast) |
| **Universe** | 6 Yahoo continuous futures (MES, MNQ, ZN, CL, GC, ZC) | 8 multi-asset ETFs | 8 multi-asset ETFs | 9 SPDR sector ETFs | 8 multi-asset ETFs |
| **Period** | 2019-05 → 2026-01 | 2007-01 → 2026-01 | 2007-01 → 2026-01 | 2000-01 → 2026-01 | 2007-01 → 2026-01 |
| **Direction** | long/short | long/cash (levered up to ~1.9×) | long only, always 3 names | long/cash | long only, always invested |
| **Signal type** | time-series trend: sign(SMA63 − SMA252) | cross-sectional momentum: top 3 by 126d return, only if > 0 | cross-sectional momentum: top 3 by 126d return (no > 0 filter) | time-series trend: SMA50 > SMA200 | none (risk-based allocation) |
| **Signal → trade timing** | close *d−1* → close *d* (month-end) | close *d−1* → close *d* (month-end) | close of 1st bar of month → **open** of 2nd bar | close *t−1* → close *t* (daily) | close *d−1* → close *d* (month-end) |
| **Rebalance cadence** | `BME` | `BME` (vol scale recomputed at month start) | 1st bar of each month (custom logic; no policy) | `D` (daily) | `BME` |
| **Sizing** | inverse 63d vol, unit gross | equal weight ≤ 40%, × vol-target scale (15% target, 42d, ≤ 2×) | equal NAV/3 per new entrant, whole shares; holders not resized | equal weight, ≤ 25% each | inverse 63d vol, ≤ 35% each |
| **Caps enforced by engine** | none | none (`max_position_size` metadata-only because of custom risk model) | none (orders mode) | `PositionLimitModel(0.25)` (no-op after clip) | `PositionLimitModel(0.35)` (no-op after clip) |
| **Cash buffer** | 5% (default) | 5% (default) | n/a | 5% (default) | 5% (default) |
| **Cost model** | 1 bp of implied notional (**default**) | 1 bp (**default**) | **explicit** 3 bp fixed slippage + $0.005/share commission, $1 min, 0.5% max (max is default) | 1 bp (**default**) | 1 bp (**default**) |
| **Turnover gate / drift band** | none | none | none | none | none |
| **Biggest caveat** | continuous-futures roll proxy, no collateral yield | free leverage (0% financing) | dividends depend on `close` field, unmanaged drift | daily churn at a token 1 bp | ignores correlations, under-invested by clip + buffer |

### Takeaways for a learner
1. **Four of the five use the engine's default 1 bp cost.** That is optimistic for
   futures rolls (W25) and very optimistic for daily rebalancing (W01). Re-run with an
   explicit `weight_cost_model=FixedBpsWeightCostModel(total_bps=...)` to stress-test.
2. **Cash and leverage are free.** The cash sleeve earns 0%, and negative cash costs
   0% (`rebalance.py:473-482`). Long/cash strategies give up T-bill yield, and W18's
   leverage gets free borrowing.
3. **`max_position_size` does not always do what it says.** It is enforced only in
   weights mode **without** a custom risk model (`core.py:539-558`).
4. **The one-bar lag is built in** for weight mode (`core.py:2084`). Order mode gets
   the same protection from next-bar fills. Neither strategy type needs to shift its
   own signals.
