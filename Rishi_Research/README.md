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
