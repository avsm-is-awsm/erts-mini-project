"""Latency / throughput analysis of run outputs (SPEC 9.3, 10.5, 12.4).

    python tools/analyze.py <run_dir> [--dataset data/out/<name>] [--img docs/img]

<run_dir> contains any of:
  orders_hw.bin + run_info_hw.json   board run, t_in/t_out in PL cycles (axis_ts_in / axis_ts_out)
  orders_sw.bin + run_info_sw.json   board SW baseline, t_in/t_out in global-timer ticks
  rtl_orders.bin                     RTL system simulation (rtl/tests/test_system.py), cycles
Writes <run_dir>/latency_summary.json and docs/img/latency_hist_<name>.png (HW vs SW, log x).
The source of every number is stated in the JSON ("board", "rtl-sim") -- nothing is assumed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from proto import dk1proto as P  # noqa: E402

PCTS = (50, 90, 99, 99.9)


def latencies(orders: np.ndarray, kinds=(P.O_ORDER,)) -> np.ndarray:
    r = P.as_ord(orders)
    sel = np.isin(r["type"], kinds)
    return ((r["t_out"][sel].astype(np.int64) - r["t_in"][sel].astype(np.int64)) & 0xFFFFFFFF).astype(np.int64)


def stats(x: np.ndarray, unit_ns: float) -> dict:
    if len(x) == 0:
        return {}
    d = {"n": int(len(x)), "min": float(x.min()), "max": float(x.max()), "mean": float(x.mean())}
    for p in PCTS:
        d[f"p{p:g}"] = float(np.percentile(x, p))
    d["jitter_max_minus_min"] = float(x.max() - x.min())
    d["ns"] = {k: (v * unit_ns if isinstance(v, float) else v) for k, v in d.items() if k != "n"}
    return d


def throughput_from_z(orders: np.ndarray, f_clk: float) -> dict:
    z = P.as_end(orders[orders[:, 0] == P.O_END])
    if not len(z):
        return {}
    n = int(z["msgs_in"][-1])
    span = (int(z["last_t_in"][-1]) - int(z["first_t_in"][-1])) & 0xFFFFFFFF
    if span == 0:
        return {"msgs_in": n}
    return {"msgs_in": n, "input_span_cycles": span, "cycles_per_msg": span / max(1, n - 1),
            "fabric_msgs_per_s": (n - 1) / (span / f_clk)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--dataset", type=Path, default=None)
    ap.add_argument("--img", type=Path, default=ROOT / "docs" / "img")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    rd: Path = a.run_dir
    name = a.name or rd.name
    out: dict = {"run_dir": str(rd)}
    hw = sw = None
    f_clk = 100e6

    if (rd / "orders_hw.bin").exists():
        info = json.loads((rd / "run_info_hw.json").read_text()) if (rd / "run_info_hw.json").exists() else {}
        f_clk = float(info.get("pl_clk_hz", 100e6))
        o = P.read_out(rd / "orders_hw.bin")
        hw = latencies(o)
        out["hw"] = dict(source="board (axis_ts_in/axis_ts_out probes)", unit="PL cycles",
                         clk_hz=f_clk, latency=stats(hw, 1e9 / f_clk), throughput=throughput_from_z(o, f_clk))
        if info.get("wall_ticks"):
            wall = info["wall_ticks"] / info["timer_hz"]
            out["hw"]["wall_msgs_per_s_incl_dma"] = info["n_msgs"] / wall
            out["hw"]["wall_s"] = wall
    elif (rd / "rtl_orders.bin").exists():
        o = P.read_out(rd / "rtl_orders.bin")
        hw = latencies(o)
        out["hw"] = dict(source="RTL simulation (exported HLS RTL + probes, Icarus)", unit="PL cycles",
                         clk_hz=f_clk, latency=stats(hw, 10.0), throughput=throughput_from_z(o, f_clk))
    if (rd / "orders_sw.bin").exists():
        info = json.loads((rd / "run_info_sw.json").read_text()) if (rd / "run_info_sw.json").exists() else {}
        hz = float(info.get("timer_hz", 333_333_333))
        sw = latencies(P.read_out(rd / "orders_sw.bin"))
        out["sw"] = dict(source="board (ARM Cortex-A9 SwEngine, global timer)", unit="timer ticks",
                         timer_hz=hz, latency=stats(sw, 1e9 / hz))
        if info.get("wall_ticks"):
            out["sw"]["wall_msgs_per_s"] = info["n_msgs"] / (info["wall_ticks"] / hz)
    if "hw" in out and "sw" in out and out["hw"]["latency"] and out["sw"]["latency"]:
        out["speedup_p50"] = out["sw"]["latency"]["ns"]["p50"] / out["hw"]["latency"]["ns"]["p50"]
        out["speedup_p99"] = out["sw"]["latency"]["ns"]["p99"] / out["hw"]["latency"]["ns"]["p99"]
    if a.dataset is not None:
        from tools.compare import compare_dataset

        for eng, fo, fb, fc in (("hw", "orders_hw.bin", "dbg_hw.bin", "counters_hw.json"),
                                ("sw", "orders_sw.bin", "dbg_sw.bin", "counters_sw.json")):
            if (rd / fo).exists():
                import shutil

                for f in (fo, fb, fc):
                    if (rd / f).exists() and (rd / f).resolve() != (a.dataset / f).resolve():
                        shutil.copy2(rd / f, a.dataset / f)
                gold = P.as_ord(P.read_out(a.dataset / "golden_orders.bin"))
                got = P.as_ord(P.read_out(rd / fo))
                no_dbg = (gold["type"] == P.O_REJECT).any() and not (got["type"] == P.O_REJECT).any()
                out[f"compare_{eng}"] = compare_dataset(a.dataset, fo, fb if (rd / fb).exists() else None,
                                                        fc if (rd / fc).exists() else None, no_dbg)
    (rd / "latency_summary.json").write_text(json.dumps(out, indent=2) + "\n")

    if hw is not None or sw is not None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from tools.plots.style import HW, SW, apply_style, save

        apply_style()
        fig, ax = plt.subplots(figsize=(6.4, 3.4))
        allns = []
        if hw is not None and len(hw):
            allns.append(hw * 1e9 / f_clk)
        if sw is not None and len(sw):
            allns.append(sw * 1e9 / out["sw"]["timer_hz"])
        # fixed context range 10 ns .. 100 us (extended if data falls outside), so HW and SW
        # histograms from different runs are directly comparable
        lo = min(10.0, max(1.0, min(x.min() for x in allns) / 2))
        hi = max(1e5, max(x.max() for x in allns) * 2)
        bins = np.logspace(np.log10(lo), np.log10(hi), 60)
        if hw is not None and len(hw):
            ax.hist(hw * 1e9 / f_clk, bins=bins, color=HW, alpha=0.85,
                    label=f"HW fabric ({out['hw']['source'].split(' (')[0]}), p50 {np.median(hw)*1e9/f_clk:.0f} ns")
        if sw is not None and len(sw):
            ns = sw * 1e9 / out["sw"]["timer_hz"]
            ax.hist(ns, bins=bins, color=SW, alpha=0.85, label=f"SW ARM, p50 {np.median(ns):.0f} ns")
        ax.set_xscale("log")
        ax.set_xlabel("tick-to-trade latency [ns] (log)")
        ax.set_ylabel("orders")
        ax.legend()
        ax.set_title(f"{name}: tick-to-trade latency")
        save(fig, a.img / f"latency_hist_{name}.png")
    print(json.dumps(out, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
