"""Free Yahoo Finance data adapter for the QuantJourney Backtester.

The engine normally gets market data from QuantJourney Cloud (POST /bt/prepare).
This module builds the same payload shape from Yahoo Finance instead, so the
unchanged engine and the unchanged example strategies can run on real data
without a QuantJourney account.

Price convention
----------------
Yahoo returns raw OHLC plus a dividend/split-adjusted close. We scale open, high
and low by the same adjustment factor (adj_close / close) so the whole bar is
adjusted and ``close == adj_close``. Both engine paths then see total-return
prices: weight mode books P&L on ``adj_close`` and order mode fills on
``close``/``open``. Volume is left unadjusted.

Downloads are cached under ``Rishi_Research/data/`` (git-ignored) so reruns are
reproducible and do not hit Yahoo again.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtester.sample_data import _frame_payload, _series_payload

CACHE_DIR = Path(__file__).resolve().parent / "data"
FIELDS = ["open", "high", "low", "close", "adj_close", "volume"]


def _cache_path(symbol: str) -> Path:
    safe = symbol.replace("=", "_").replace("^", "_").replace("/", "_")
    return CACHE_DIR / f"{safe}.csv"


def load_symbol(symbol: str, start: str = "1999-01-01") -> pd.DataFrame:
    """Return a daily adjusted OHLCV frame for one symbol (cached)."""
    path = _cache_path(symbol)
    if path.exists():
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
    else:
        import yfinance as yf

        raw = yf.download(
            symbol,
            start=start,
            end=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"),
            auto_adjust=False,
            actions=False,
            progress=False,
            multi_level_index=False,
        )
        if raw is None or raw.empty:
            raise ValueError(f"Yahoo returned no data for {symbol}")
        raw = raw.rename(columns=str.lower).rename(columns={"adj close": "adj_close"})
        frame = raw[FIELDS].copy()
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "date"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path)

    frame = frame.dropna(subset=["close", "adj_close"])
    frame = frame[(frame["close"] > 0) & (frame["adj_close"] > 0)]
    factor = frame["adj_close"] / frame["close"]
    adjusted = pd.DataFrame(index=frame.index)
    for col in ("open", "high", "low", "close"):
        adjusted[col] = frame[col] * factor
    adjusted["adj_close"] = frame["adj_close"]
    adjusted["volume"] = frame["volume"].fillna(0.0)
    # Some futures bars have missing open/high/low on Yahoo; fall back to close.
    for col in ("open", "high", "low"):
        adjusted[col] = adjusted[col].fillna(adjusted["close"])
    adjusted["high"] = adjusted[["high", "open", "close"]].max(axis=1)
    adjusted["low"] = adjusted[["low", "open", "close"]].min(axis=1)
    return adjusted


def build_yahoo_bt_payload(
    *, instruments: list[str], start: str, end: str, initial_nav: float = 100.0
) -> dict[str, Any]:
    """Build a /bt/prepare-compatible payload from Yahoo Finance daily bars."""
    symbols = [str(s).strip().upper() for s in instruments if str(s).strip()]
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    per_symbol = {}
    for symbol in symbols:
        frame = load_symbol(symbol)
        frame = frame[(frame.index >= start_ts) & (frame.index < end_ts)]
        if frame.empty:
            raise ValueError(f"No Yahoo data for {symbol} in {start}..{end}")
        per_symbol[symbol] = frame

    # Union calendar: keep real gaps (NaN) rather than inventing prices.
    index = sorted(set().union(*(f.index for f in per_symbol.values())))
    index = pd.DatetimeIndex(index).tz_localize("UTC")

    price_cols, metric_cols, param_cols, returns = {}, {}, {}, {}
    for symbol, frame in per_symbol.items():
        frame = frame.copy()
        frame.index = frame.index.tz_localize("UTC")
        frame = frame.reindex(index)
        for field in FIELDS:
            price_cols[(symbol, field)] = frame[field]
        ret = frame["adj_close"].pct_change(fill_method=None)
        returns[symbol] = ret
        filled = ret.fillna(0.0)
        nav = (1.0 + filled).cumprod()
        metric_cols.update(
            {
                (symbol, "returns"): filled,
                (symbol, "volatility"): filled.rolling(20, min_periods=2).std().fillna(0.0),
                (symbol, "daily_pnl"): filled,
                (symbol, "transaction_costs"): pd.Series(0.0, index=index),
                (symbol, "net_asset_value"): nav,
                (symbol, "gross_asset_value"): nav,
                (symbol, "daily_net_return"): filled,
                (symbol, "drawdown"): nav / nav.cummax() - 1.0,
            }
        )
        trading = frame["adj_close"].notna().astype(float)
        param_cols.update(
            {
                (symbol, "exchange"): pd.Series(0.0, index=index),
                (symbol, "units"): pd.Series(0.0, index=index),
                (symbol, "eligibility"): pd.Series(1.0, index=index),
                (symbol, "active"): pd.Series(1.0, index=index),
                (symbol, "forecasts"): pd.Series(0.0, index=index),
                (symbol, "is_trading_day"): trading,
                (symbol, "day_type"): pd.Series(1.0, index=index),
            }
        )

    def _frame(cols: dict) -> pd.DataFrame:
        df = pd.DataFrame(cols, index=index)
        df.columns = pd.MultiIndex.from_tuples(df.columns, names=["instrument", "field"])
        return df

    prices_df = _frame(price_cols)
    returns_df = pd.DataFrame(returns, index=index).fillna(0.0)
    portfolio_nav = initial_nav * (1.0 + returns_df.mean(axis=1)).cumprod()

    return {
        "session_id": "yahoo-session",
        "dataset_id": "yahoo-" + "-".join(symbols).lower(),
        "instrument_names": symbols,
        "prices": _frame_payload(prices_df),
        "metrics": _frame_payload(_frame(metric_cols)),
        "parameters": _frame_payload(_frame(param_cols)),
        "nav": _series_payload(portfolio_nav),
        "summary": {
            "source": "yahoo",
            "instruments": len(symbols),
            "dates": len(index),
            "start": index[0].date().isoformat(),
            "end": index[-1].date().isoformat(),
        },
    }


def install() -> None:
    """Route the engine's market-data fetch through Yahoo Finance."""
    from backtester.mixins.sdk_client import SDKClientMixin

    async def _fetch_market_data(self) -> None:  # type: ignore[no-untyped-def]
        self._api_response = build_yahoo_bt_payload(
            instruments=self.instruments,
            start=self.backtest_period.start,
            end=self.backtest_period.end,
        )
        self.session_id = self._api_response["session_id"]
        self.dataset_id = self._api_response["dataset_id"]
        self._validate_data_completeness_response()

    SDKClientMixin._fetch_market_data = _fetch_market_data


if __name__ == "__main__":
    import sys

    for sym in sys.argv[1:]:
        f = load_symbol(sym)
        info = {
            "nan_close": int(f["close"].isna().sum()),
            "last": float(np.round(f["close"].iloc[-1], 2)),
        }
        print(sym, f.index[0].date(), f.index[-1].date(), len(f), json.dumps(info))
