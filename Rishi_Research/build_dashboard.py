"""Build Rishi_Research/dashboard/index.html from the top-5 backtest results.

Reads the engine outputs written by run_top5.py (results/*/*/equity_curve.csv,
weights.csv, metrics.csv, run.log) plus the cached Yahoo SPY series, and writes a
single self-contained HTML page (no network, no external JS).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT = HERE / "dashboard" / "index.html"

# code, folder, name, idea, mode, universe,
# published (Sharpe, CAGR %, Max DD %, total return %)
STRATEGIES = [
    ("W25", "example_weights_25_continuous_futures_trend", "Continuous Futures Trend",
     "Long/short 6 futures when SMA(63) vs SMA(252) trend says so; inverse-vol sized; month-end",
     "weights", "MES MNQ ZN CL GC ZC (continuous)", (0.62, 5.88, -9.69, 46.28)),
    ("W18", "example_weights_18_vol_target_momentum", "Vol-Targeted Momentum",
     "Top-3 ETFs with positive 6-month momentum, scaled to 15% vol (max 2x); month-end",
     "weights", "SPY EFA EEM TLT IEF GLD DBC VNQ", (0.57, 10.96, -41.18, 621.23)),
    ("O17", "example_orders_17_monthly_rotation_orders", "Monthly Rotation (orders)",
     "Top-3 ETFs by 6-month momentum, rotated monthly with market orders; per-share fees + 3 bps slippage",
     "orders", "SPY EFA EEM TLT IEF GLD DBC VNQ", (0.53, 8.62, -28.23, 380.52)),
    ("W01", "example_weights_01_sma_daily", "Daily SMA Trend",
     "Hold each sector ETF while SMA(50) > SMA(200); daily",
     "weights", "9 SPDR sector ETFs", (0.45, 7.38, -34.68, 536.96)),
    ("W05", "example_weights_05_monthly_inverse_vol", "Monthly Inverse Volatility",
     "Always invested; each ETF sized by 1 / 63-day vol, capped at 35%; month-end",
     "weights", "SPY EFA EEM TLT IEF GLD DBC VNQ", (0.45, 5.45, -20.42, 174.15)),
]


def _run_dir(folder: str) -> Path:
    subdirs = [p for p in (RESULTS / folder).iterdir() if p.is_dir()]
    if len(subdirs) != 1:
        raise RuntimeError(f"expected one run directory under {folder}, found {subdirs}")
    return subdirs[0]


def _log_stats(folder: str) -> dict:
    text = (RESULTS / folder / "run.log").read_text()

    def grab(label: str) -> float | None:
        m = re.search(rf"{label}:\s+\$?\s*([-\d,.]+)", text)
        return float(m.group(1).replace(",", "")) if m else None

    period = re.search(r"Period:\s+(\S+) → (\S+)", text)
    return {
        "total": grab("Total Return"),
        "vol": grab("Ann. Volatility"),
        "maxdd": grab("Max Drawdown"),
        "sharpe": grab("Sharpe Ratio"),
        "rebalances": grab("Rebalances"),
        "start": period.group(1) if period else None,
        "end": period.group(2) if period else None,
    }


def _metric(folder: str, name: str) -> str | None:
    metrics = pd.read_csv(_run_dir(folder) / "metrics.csv")
    row = metrics.loc[metrics["metric"] == name, "value"]
    return None if row.empty else str(row.iloc[0])


def main() -> None:
    series: dict[str, pd.Series] = {}
    meta = []
    for code, folder, name, idea, mode, universe, published in STRATEGIES:
        run = _run_dir(folder)
        eq = pd.read_csv(run / "equity_curve.csv", index_col=0)
        eq.index = pd.to_datetime(eq.index, utc=True).tz_convert(None).normalize()
        series[code] = eq["return"].astype(float)

        weights = pd.read_csv(run / "weights.csv", index_col=0)
        last = weights.iloc[-1]
        holdings = [
            {"asset": str(k), "w": round(float(v), 4)}
            for k, v in last.sort_values(key=np.abs, ascending=False).items()
            if abs(v) > 1e-4
        ]
        gross = weights.abs().sum(axis=1)
        stats = _log_stats(folder)
        meta.append(
            {
                "code": code,
                "name": name,
                "idea": idea,
                "mode": mode,
                "universe": universe,
                "file": f"strategies/{folder}.py",
                "engine": stats,
                "published": dict(zip(("sharpe", "cagr", "maxdd", "total"), published, strict=True)),
                "turnover": _metric(folder, "Annualized turnover"),
                "holdings": holdings,
                "holdings_date": str(pd.to_datetime(weights.index[-1]).date()),
                "avg_gross": round(float(gross[gross > 0].mean()), 3),
            }
        )

    spy = pd.read_csv(HERE / "data" / "SPY.csv", index_col=0, parse_dates=True)["adj_close"]
    spy.index = pd.to_datetime(spy.index).normalize()
    spy_ret = spy.pct_change(fill_method=None)
    first = min(s.index[0] for s in series.values())
    last = max(s.index[-1] for s in series.values())
    series["SPY"] = spy_ret[(spy_ret.index >= first) & (spy_ret.index <= last)].fillna(0.0)

    index = sorted(set().union(*(s.index for s in series.values())))
    dates = [d.strftime("%Y-%m-%d") for d in index]
    columns = {}
    for code, s in series.items():
        s = s[~s.index.duplicated()].reindex(index)
        start, end = series[code].index[0], series[code].index[-1]
        vals = []
        for d, v in zip(index, s.values, strict=True):
            if d < start or d > end:
                vals.append(None)
            else:
                vals.append(0.0 if pd.isna(v) else round(float(v), 7))
        columns[code] = vals

    payload = {
        "dates": dates,
        "returns": columns,
        "strategies": meta,
        "benchmark": {"code": "SPY", "name": "SPY (S&P 500 ETF, total return)"},
        "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d"),
        "rf": 0.02,
    }
    template = (HERE / "dashboard_template.html").read_text()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(template.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":"))))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
