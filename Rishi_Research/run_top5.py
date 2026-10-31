"""Run the top-5 QuantJourney example strategies on free Yahoo Finance data.

Each strategy file in ``strategies/`` is imported and its own ``main()`` is run
unchanged; the only substitution is the market-data fetch (see yahoo_data.py).
Reports land in ``Rishi_Research/results/<strategy>/``.

Usage (from the repository root, inside the project venv):
    python Rishi_Research/run_top5.py            # all five
    python Rishi_Research/run_top5.py W18 O17    # a subset
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

TOP5 = {
    "W25": "example_weights_25_continuous_futures_trend",
    "W18": "example_weights_18_vol_target_momentum",
    "O17": "example_orders_17_monthly_rotation_orders",
    "W01": "example_weights_01_sma_daily",
    "W05": "example_weights_05_monthly_inverse_vol",
}


def _run_one(module_name: str) -> None:
    """Child-process entry point: patch data source, then run the strategy."""
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(REPO))
    import yahoo_data

    yahoo_data.install()
    spec = importlib.util.spec_from_file_location(
        module_name, REPO / "strategies" / f"{module_name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    asyncio.run(module.main())


def main(selected: list[str]) -> int:
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    failures = 0
    for code in selected or list(TOP5):
        module_name = TOP5[code]
        out_dir = results / module_name
        out_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.pop("QJ_SAMPLE_DATA", None)
        env.update(
            {
                "QJ_OUTPUT_DIR": str(out_dir),
                "MPLBACKEND": "Agg",
                "NUMBA_DISABLE_JIT": env.get("NUMBA_DISABLE_JIT", "1"),
                "PYTHONPATH": os.pathsep.join([str(REPO), str(HERE)]),
            }
        )
        started = time.perf_counter()
        with open(out_dir / "run.log", "w") as log:
            proc = subprocess.run(
                [sys.executable, __file__, "--child", module_name],
                cwd=REPO,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        status = "ok" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
        failures += proc.returncode != 0
        print(f"{code} {module_name}: {status} in {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        _run_one(sys.argv[2])
    else:
        sys.exit(main(sys.argv[1:]))
