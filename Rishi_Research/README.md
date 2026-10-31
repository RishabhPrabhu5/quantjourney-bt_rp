# Rishi_Research

Personal research folder on top of the QuantJourney Backtester fork.

## Top-5 strategy selection

Ranked by Sharpe ratio from the real-data results QuantJourney publishes at
https://backtester.quantjourney.cloud/strategies (scraped 2026-10-31). Intraday
(W07-W09, O15-O16) and walk-forward/optimization examples (WF01-WF05) were excluded
because their published packets do not report comparable full-history metrics.
Ties are broken by smaller maximum drawdown.

| Rank | Strategy | Source file | Published Sharpe | CAGR | Max DD | Period |
|---|---|---|---|---|---|---|
| 1 | W25 Continuous Futures Trend | `strategies/example_weights_25_continuous_futures_trend.py` | 0.62 | 5.88% | -9.69% | 2019-05 → 2026-01 |
| 2 | W18 Vol-Targeted Momentum | `strategies/example_weights_18_vol_target_momentum.py` | 0.57 | 10.96% | -41.18% | 2007-01 → 2026-01 |
| 3 | O17 Monthly Rotation (orders) | `strategies/example_orders_17_monthly_rotation_orders.py` | 0.53 | 8.62% | -28.23% | 2007-01 → 2026-01 |
| 4 | W01 Daily SMA Trend | `strategies/example_weights_01_sma_daily.py` | 0.45 | 7.38% | -34.68% | 2000-01 → 2026-01 |
| 5 | W05 Monthly Inverse Volatility | `strategies/example_weights_05_monthly_inverse_vol.py` | 0.45 | 5.45% | -20.42% | 2007-01 → 2026-01 |

Published numbers come from QuantJourney's own runs (engine 0.10.0); they are
illustrative historical backtests, not investable performance.

## What's in this folder

| Path | What it is |
|---|---|
| `dashboard/index.html` | **The dashboard.** Self-contained page (no internet needed): performance table, growth of $100, drawdowns, calendar-year returns, correlations, latest weights, and a reproduction check. Switch the window between the common period, full history, and the last 5 / 3 / 1 years. |
| `STRATEGIES_EXPLAINED.md` | Plain-English walkthrough of each strategy: signals, portfolio construction, execution and costs (written in a separate session). |
| `yahoo_data.py` | Free-data adapter: builds the engine's market-data payload from Yahoo Finance instead of QuantJourney Cloud. |
| `run_top5.py` | Runs the five strategy files from `strategies/` **unchanged**, with only the data source swapped. |
| `build_dashboard.py`, `dashboard_template.html` | Turn the run outputs into `dashboard/index.html`. |
| `results/<strategy>/` | Engine outputs per strategy: equity curve, weights, metrics, summary, run metadata and log. The PNG plot packs (~9 MB each) are git-ignored; rerun to regenerate them. |

## Reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[data]"
python Rishi_Research/run_top5.py          # downloads Yahoo data into Rishi_Research/data/ (git-ignored) and runs all five
python Rishi_Research/build_dashboard.py   # rebuilds dashboard/index.html
```

Needs network access to `query1.finance.yahoo.com`, `query2.finance.yahoo.com` and `fc.yahoo.com`.
No QuantJourney account or API key is required.

## Data notes

- Yahoo daily bars, with open/high/low/close all scaled by Yahoo's dividend/split
  adjustment factor, so both weight mode (`adj_close`) and order mode (`close` fills)
  are total-return.
- Four of five runs match QuantJourney's published results to within 0.01%. W25
  differs slightly (total 48.9% vs 46.3%) because Yahoo's continuous futures history
  has been revised since and the negative WTI crude print on 2020-04-20 is dropped.
- The engine's console Sharpe uses a 2% risk-free rate; `metrics.csv` reports a Sharpe
  with 0% risk-free, so the two differ (e.g. W05: 0.45 vs 0.70). The dashboard uses 2%.
