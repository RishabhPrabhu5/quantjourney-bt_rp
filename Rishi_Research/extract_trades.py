"""Extract when each top-5 strategy traded and why.

Re-runs each strategy in-process on the cached Yahoo data (reports off), then
reads the engine's own trade blotter, signals and target weights. For every
trade day it records:

* the decision bar (the bar whose close produced the target), using the engine's
  timing: weight mode trades at the close of day d toward the weights computed
  at the close of d-1; order mode fills at the open of the bar after the
  decision (see STRATEGIES_EXPLAINED.md, section 0);
* the indicator values the strategy's rule looked at on that bar;
* the target weights before and after, and what changed;
* the executed trades with prices and costs.

Output: Rishi_Research/trades/<CODE>.json, consumed by build_dashboard.py.

Usage (repo root, project venv):
    python Rishi_Research/extract_trades.py          # all five
    python Rishi_Research/extract_trades.py W25
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = HERE / "trades"

TOP5 = {
    "W25": "example_weights_25_continuous_futures_trend",
    "W18": "example_weights_18_vol_target_momentum",
    "O17": "example_orders_17_monthly_rotation_orders",
    "W01": "example_weights_01_sma_daily",
    "W05": "example_weights_05_monthly_inverse_vol",
}
CASH_BUFFER = 0.95  # engine default for weight mode (core.py, fast path)
EPS = 1e-4


def _r(x: float, nd: int = 4) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _run_strategy(module_name: str):
    sys.path[:0] = [str(HERE), str(REPO)]
    import yahoo_data

    from backtester import Backtester

    yahoo_data.install()
    captured = []
    original = Backtester.run_strategy

    async def run(self):  # type: ignore[no-untyped-def]
        await original(self)
        captured.append(self)

    Backtester.run_strategy = run
    spec = importlib.util.spec_from_file_location(
        module_name, REPO / "strategies" / f"{module_name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    asyncio.run(module.main())
    return captured[0], module


def _indicators(code: str, s, module, close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Per-bar indicator frames matching each strategy's own rule."""
    ind: dict[str, pd.DataFrame] = {}
    if code == "W01":
        ind["SMA50"] = s.instruments_data.get_feature("SMA_50_close")
        ind["SMA200"] = s.instruments_data.get_feature("SMA_200_close")
    elif code in ("W18", "O17"):
        lb = 126
        ind["Mom6m"] = close / close.shift(lb) - 1.0
    elif code == "W25":
        ind["SMA63"] = close.rolling(63, min_periods=63).mean()
        ind["SMA252"] = close.rolling(252, min_periods=252).mean()
        rets = close.pct_change(fill_method=None)
        ind["Vol63"] = rets.rolling(63, min_periods=63).std() * np.sqrt(252.0)
    elif code == "W05":
        ind["Vol63"] = close.pct_change().rolling(63).std() * np.sqrt(252)
    return ind


def _target_frame(code: str, s, close: pd.DataFrame) -> pd.DataFrame:
    """Target weights per decision bar (what the book trades toward)."""
    if code == "O17":
        mom = close / close.shift(126) - 1.0
        tgt = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        for i in range(126, len(close)):
            picks = mom.iloc[i].dropna().nlargest(3).index
            tgt.iloc[i, tgt.columns.isin(picks)] = 1.0 / max(1, len(picks))
        return tgt
    w = s.instruments_data.get_feature("strategies", s.strategy_name, "weights")
    return w.reindex(columns=close.columns).fillna(0.0) * CASH_BUFFER


def _reason(code: str, assets_rows: list[dict], changes: list[dict], extra: dict) -> str:
    def names(kind: str) -> list[str]:
        return [c["asset"] for c in changes if c["kind"] == kind]

    ent, ext, flip = names("entered"), names("exited"), names("flipped")
    if code == "W01":
        parts = []
        for c in changes:
            if c["kind"] == "entered":
                parts.append(f"{c['asset']} SMA50 crossed above SMA200 → buy")
            elif c["kind"] == "exited":
                parts.append(f"{c['asset']} SMA50 fell below SMA200 → sell")
        n = extra["n_active"]
        if parts:
            return "; ".join(parts) + f". The {n} active ETFs are re-split equally."
        return f"No signal change; re-equalise the {n} active ETFs (daily rebalance)."
    if code in ("W18", "O17"):
        picks = ", ".join(extra["picks"]) or "none"
        base = f"Top-3 by 6-month return: {picks}."
        if code == "W18":
            base = (
                f"Top-3 with positive 6-month return: {picks}. Vol target 15% sets gross "
                f"exposure to {extra['gross']:.0%} (scale ×{extra['scale']:.2f})."
            )
        moves = []
        if ent:
            moves.append("buy " + ", ".join(ent))
        if ext:
            moves.append("sell " + ", ".join(ext))
        if not moves:
            moves.append("holdings unchanged, resize only" if code == "W18" else "no change")
        text = "; ".join(moves)
        return base + " " + text[:1].upper() + text[1:] + "."
    if code == "W25":
        side = {r["asset"]: ("long" if (r["target"] or 0) > 0 else "short") for r in assets_rows}
        moves = [f"{a} trend flipped to {side[a]}" for a in flip]
        moves += [f"{a} opened {side[a]}" for a in ent]
        moves += [f"{a} closed" for a in ext]
        lead = "; ".join(moves) if moves else "No trend flips"
        return lead + (
            ". Month-end resize: each future gets sign(SMA63 − SMA252) / 63-day vol, "
            "scaled to 100% gross."
        )
    if code == "W05":
        lo = min(assets_rows, key=lambda r: r.get("Vol63") or 9)
        hi = max(assets_rows, key=lambda r: r.get("Vol63") or 0)
        return (
            f"Month-end resize to inverse 63-day volatility (cap 35%). Calmest: {lo['asset']} "
            f"({(lo.get('Vol63') or 0):.0%} vol, {lo['target']:.0%}); most volatile: {hi['asset']} "
            f"({(hi.get('Vol63') or 0):.0%} vol, {hi['target']:.0%})."
        )
    return ""


def extract(code: str) -> dict:
    module_name = TOP5[code]
    s, module = _run_strategy(module_name)
    close = s.instruments_data.get_feature("adj_close")
    idx = close.index
    trades = s.blotter.get_trades_dataframe()
    trades["Timestamp"] = pd.to_datetime(trades["Timestamp"], utc=True)
    ind = _indicators(code, s, module, close)
    target = _target_frame(code, s, close)
    pre = None
    if code == "W18":
        sig = s.signals.reindex(columns=close.columns).fillna(0.0)
        active = sig == 1.0
        pre = active.div(active.sum(axis=1), axis=0).fillna(0.0).clip(upper=0.40)

    events = []
    prev_target = pd.Series(0.0, index=close.columns)
    n_maint = 0
    maint_cost = 0.0
    maint_value = 0.0
    for day, group in trades.groupby("Timestamp", sort=True):
        pos = idx.get_indexer([day])[0]
        if pos <= 0:
            continue
        dpos = pos - 1  # decision bar
        tgt = target.iloc[dpos]
        changes = []
        for a in close.columns:
            before, after = float(prev_target.get(a, 0.0)), float(tgt.get(a, 0.0))
            if abs(before) < EPS and abs(after) >= EPS:
                changes.append({"asset": a, "kind": "entered"})
            elif abs(before) >= EPS and abs(after) < EPS:
                changes.append({"asset": a, "kind": "exited"})
            elif before * after < 0:
                changes.append({"asset": a, "kind": "flipped"})
        rows = []
        for a in close.columns:
            row = {"asset": a, "before": _r(prev_target.get(a, 0.0)), "target": _r(tgt.get(a, 0.0)),
                   "close": _r(close.iloc[dpos][a], 4)}
            for k, frame in ind.items():
                row[k] = _r(frame.iloc[dpos][a], 4)
            rows.append(row)
        extra: dict = {"n_active": int((tgt.abs() > EPS).sum())}
        if code in ("W18", "O17"):
            mom = ind["Mom6m"].iloc[dpos].dropna().sort_values(ascending=False)
            if code == "W18":
                mom = mom[mom > 0]
            extra["picks"] = list(mom.index[:3])
        if code == "W18" and pre is not None:
            g_pre = float(pre.iloc[dpos].abs().sum())
            g_fin = float(tgt.abs().sum()) / CASH_BUFFER
            extra["gross"] = g_fin * CASH_BUFFER
            extra["scale"] = g_fin / g_pre if g_pre > 0 else 0.0
        fills = []
        for _, t in group.iterrows():
            slip = 0.0
            if "TheoreticalPrice" in t and pd.notna(t["TheoreticalPrice"]):
                slip = abs(float(t["Price"]) - float(t["TheoreticalPrice"])) * float(t["Quantity"])
            # Blotter TradeValue omits futures multipliers; use the engine's own
            # contract-aware notional so value and cost (charged on it) agree.
            qty, px = float(t["Quantity"]), float(t["Price"])
            notional = abs(s._trade_notional(t["Instrument"], qty, px))
            fills.append({"asset": t["Instrument"], "side": t["Side"], "qty": _r(t["Quantity"], 2),
                          "price": _r(t["Price"], 4), "value": _r(notional, 2),
                          "cost": _r(t["TransactionCost"], 2), "slip": _r(slip, 2)})
        value = float(sum(f["value"] or 0 for f in fills))
        cost = float(group["TransactionCost"].fillna(0).sum()) + sum(f["slip"] or 0 for f in fills)
        kind = "signal" if changes else "resize"
        if code == "W01" and not changes:
            # Daily re-equalisation trades: summarise instead of listing 6,000 rows.
            n_maint += 1
            maint_cost += cost
            maint_value += value
            prev_target = tgt.copy()
            continue
        events.append({
            "date": day.strftime("%Y-%m-%d"),
            "decision": idx[dpos].strftime("%Y-%m-%d"),
            "kind": kind,
            "changes": changes,
            "reason": _reason(code, rows, changes, extra),
            "value": _r(value, 2),
            "cost": _r(cost, 2),
            "n_trades": int(len(group)),
            "assets": rows,
            "fills": sorted(fills, key=lambda f: -(f["value"] or 0)),
        })
        prev_target = tgt.copy()

    # Daily actual weights sampled weekly for the holdings heatmap.
    run_dir = next(p for p in (HERE / "results" / module_name).iterdir() if p.is_dir())
    weights = pd.read_csv(run_dir / "weights.csv", index_col=0)
    weights.index = pd.to_datetime(weights.index, utc=True)
    weights = weights.reindex(columns=list(close.columns))
    weekly = weights.resample("W-FRI").last().dropna(how="all")
    total_cost = float(trades["TransactionCost"].fillna(0).sum()) + sum(
        f["slip"] or 0 for e in events for f in e["fills"]
    )
    return {
        "code": code,
        "assets": list(close.columns),
        "timing": "orders" if code == "O17" else "weights",
        "heat": {
            "dates": [d.strftime("%Y-%m-%d") for d in weekly.index],
            "w": {a: [_r(v, 3) for v in weekly[a].fillna(0.0)] for a in close.columns},
        },
        "events": events,
        "summary": {
            "trade_days": int(trades["Timestamp"].nunique()),
            "n_trades": int(len(trades)),
            "traded_value": _r(sum(e["value"] or 0 for e in events) + maint_value, 0),
            "costs": _r(total_cost, 0),
            "initial_capital": float(s.initial_capital),
            "maintenance_days": n_maint,
            "maintenance_cost": _r(maint_cost, 0),
            "maintenance_value": _r(maint_value, 0),
        },
    }


def main(selected: list[str]) -> int:
    OUT.mkdir(exist_ok=True)
    for code in selected or list(TOP5):
        env = os.environ.copy()
        env.update({"QJ_NO_REPORTS": "1", "QJ_LOG_LEVEL": "ERROR", "QJ_QUIET": "1",
                    "MPLBACKEND": "Agg", "NUMBA_DISABLE_JIT": "1"})
        env.pop("QJ_SAMPLE_DATA", None)
        proc = subprocess.run([sys.executable, __file__, "--child", code], cwd=REPO, env=env,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stdout[-2000:], proc.stderr[-4000:])
            return 1
        print(proc.stdout.strip().splitlines()[-1])
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        data = extract(sys.argv[2])
        path = OUT / f"{sys.argv[2]}.json"
        path.write_text(json.dumps(data, separators=(",", ":")))
        print(f"{sys.argv[2]}: {len(data['events'])} events, {data['summary']['trade_days']} trade "
              f"days, costs ${data['summary']['costs']:,.0f} → {path.name} "
              f"({path.stat().st_size / 1024:.0f} KB)")
    else:
        sys.exit(main(sys.argv[1:]))
