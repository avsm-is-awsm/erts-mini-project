"""Plot / tabulate the SystemC architecture predictions (systemc/out/*.json).

    python systemc/plot_arch.py
Writes docs/img/sysc_arch_<dataset>.png and systemc/out/summary.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tools.plots.style import HW, MODEL, SW, apply_style, save  # noqa: E402

COL = {"A": SW, "B": MODEL, "C": HW}
NAME = {"A": "A all-SW (ARM)", "B": "B split (PL book + ARM strategy)", "C": "C all-HW (PL)"}


def main() -> int:
    apply_style()
    out = ROOT / "systemc" / "out"
    res: dict = {}
    for f in sorted(out.glob("*.json")):
        if f.name == "summary.json":
            continue
        d = json.loads(f.read_text())
        ds = f.name.rsplit("_", 2)[0]
        res.setdefault(ds, {})[f"{d['arch']}_{d['mode']}"] = d["latency_ns"]
    (out / "summary.json").write_text(json.dumps(res, indent=2) + "\n")
    for ds, runs in res.items():
        fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.2), sharey=False)
        for ax, mode in zip(axes, ("replay", "live")):
            archs = [a for a in "ABC" if f"{a}_{mode}" in runs and runs[f"{a}_{mode}"].get("n")]
            for k, a in enumerate(archs):
                s = runs[f"{a}_{mode}"]
                ax.errorbar([k], [s["p50"]], yerr=[[s["p50"] - s["min"]], [s["p99"] - s["p50"]]],
                            fmt="o", color=COL[a], capsize=4, label=NAME[a])
            ax.set_yscale("log")
            ax.set_xticks(range(len(archs)))
            ax.set_xticklabels(archs)
            ax.set_title(f"{mode}: p50 (min..p99)")
            ax.set_ylabel("predicted tick-to-trade [ns]")
        axes[1].legend(fontsize=7, loc="upper right")
        fig.suptitle(f"SystemC prediction, {ds}", fontsize=10, fontweight="bold")
        save(fig, ROOT / "docs" / "img" / f"sysc_arch_{ds}.png")
    print(json.dumps(res, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
