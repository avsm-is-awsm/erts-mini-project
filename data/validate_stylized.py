"""Realism (stylized facts) plots for a generated dataset (SPEC 7.5).

    python data/validate_stylized.py data/out/day_default [--img docs/img]

Needs feed.bin, meta.json, events.parquet and the golden BBO trace (run ref/run_ref.py
with dbg_en=1 first).  Writes docs/img/data_<name>_*.png and data/out/<name>/stylized.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from proto import dk1proto as P  # noqa: E402
from tools.plots.style import apply_style, save  # noqa: E402

S = 1_000_000_000


def lifetimes_s(msgs: np.ndarray) -> np.ndarray:
    t = msgs["type"]
    add = t == P.T_ADD
    fin = np.isin(t, (P.T_DEL, P.T_EXEC))
    a_oid, a_ts = msgs["order_id"][add], msgs["ts_ns"][add].astype(np.int64)
    # last X/E per oid = removal time (approximation for partial executes)
    f_oid, f_ts = msgs["order_id"][fin], msgs["ts_ns"][fin].astype(np.int64)
    last = {}
    for o, ts in zip(f_oid.tolist(), f_ts.tolist()):
        last[o] = ts
    out = [last[o] - ts for o, ts in zip(a_oid.tolist(), a_ts.tolist()) if o in last]
    return np.array(out, dtype=np.float64) / S


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--img", type=Path, default=ROOT / "docs" / "img")
    a = ap.parse_args(argv)
    d: Path = a.dataset
    name = d.name
    apply_style()
    a.img.mkdir(parents=True, exist_ok=True)
    meta = json.loads((d / "meta.json").read_text())
    msgs = P.read_msgs(d / "feed.bin")
    bbo_raw = P.read_out(d / "golden_bbo.bin")
    bbo = P.as_bbo(bbo_raw[bbo_raw[:, 0] == P.O_BBO])
    counters = json.loads((d / "golden_counters.json").read_text())
    import pyarrow.parquet as pq

    ev = pq.read_table(d / "events.parquet").to_pandas()
    prod = {p["d"]: p for p in meta["products"]}
    summary = {}

    # 1. spread distribution
    both = (bbo["flags"] & 3) == 3
    spread = (bbo["ask_px"][both].astype(np.int64) - bbo["bid_px"][both]) / 100.0
    fig, ax = plt.subplots(figsize=(6, 3.4))
    ax.hist(spread, bins=np.arange(0, max(6, np.percentile(spread, 99.5)) + 0.1, 0.1))
    ax.set_xlabel("best ask - best bid [EUR/MWh]")
    ax.set_ylabel("messages")
    ax.set_title(f"{name}: spread after each book message")
    save(fig, a.img / f"data_{name}_spread.png")
    summary["spread_eur_p50"] = float(np.median(spread))
    summary["spread_eur_p90"] = float(np.percentile(spread, 90))
    summary["crossed_fraction"] = float((spread <= 0).mean())

    # 2. message rate vs time to gate closure
    dl = ev["delivery"].to_numpy()
    valid = dl >= 0
    gc = np.array([prod[x]["gc_ns"] if x >= 0 else 0 for x in dl], dtype=np.int64)
    ttg_min = (gc[valid] - ev["ts_ns"].to_numpy().astype(np.int64)[valid]) / S / 60
    fig, ax = plt.subplots(figsize=(6, 3.4))
    n_prod = len(meta["products"])
    bins = np.arange(0, meta["window_min"] + 1, max(1, meta["window_min"] // 60))
    h, e = np.histogram(ttg_min, bins=bins)
    ax.plot(e[:-1], h / n_prod / (np.diff(e) * 60), drawstyle="steps-post")
    ax.invert_xaxis()
    ax.set_xlabel("time to gate closure [min]")
    ax.set_ylabel("msgs / s / product")
    ax.set_title(f"{name}: activity rush towards gate closure")
    save(fig, a.img / f"data_{name}_rate_vs_gc.png")
    summary["rate_last5min_vs_mean"] = float(
        (ttg_min < 5).sum() / max(1, len(ttg_min)) / (5 / meta["window_min"]))

    # 3. depth at best
    fig, ax = plt.subplots(figsize=(6, 3.4))
    bq = bbo["bid_qty"][bbo["flags"] & 1 > 0] / 10.0
    aq = bbo["ask_qty"][bbo["flags"] & 2 > 0] / 10.0
    hi = np.percentile(np.concatenate([bq, aq]), 99)
    ax.hist([bq, aq], bins=40, range=(0, hi), label=["bid", "ask"])
    ax.set_xlabel("quantity at best [MW]")
    ax.set_ylabel("messages")
    ax.legend()
    ax.set_title(f"{name}: depth at best")
    save(fig, a.img / f"data_{name}_depth.png")
    summary["depth_mw_p50"] = float(np.median(np.concatenate([bq, aq])))

    # 4. trade price converging to the imbalance price
    ex = (ev["type"].to_numpy() == P.T_EXEC) & valid
    e_d = dl[ex]
    e_ttg = (np.array([prod[x]["gc_ns"] for x in e_d]) - ev["ts_ns"].to_numpy().astype(np.int64)[ex]) / S / 60
    e_px = msgs["price"][ex].astype(np.float64)
    imb = np.array([prod[x]["imb"] for x in e_d], dtype=np.float64)
    da = np.array([prod[x]["da"] for x in e_d], dtype=np.float64)
    dev_imb = np.abs(e_px - imb) / 100
    dev_da = np.abs(e_px - da) / 100
    fig, ax = plt.subplots(figsize=(6, 3.4))
    edges = np.linspace(0, meta["window_min"], 25)
    idx = np.digitize(e_ttg, edges)
    mid = (edges[:-1] + edges[1:]) / 2
    med_imb = [np.median(dev_imb[idx == i]) if (idx == i).any() else np.nan for i in range(1, len(edges))]
    med_da = [np.median(dev_da[idx == i]) if (idx == i).any() else np.nan for i in range(1, len(edges))]
    ax.plot(mid, med_imb, marker="o", ms=3, label="|trade - imbalance price|")
    ax.plot(mid, med_da, marker="s", ms=3, label="|trade - day-ahead price|")
    ax.invert_xaxis()
    ax.set_xlabel("time to gate closure [min]")
    ax.set_ylabel("median abs deviation [EUR/MWh]")
    ax.legend()
    ax.set_title(f"{name}: trades converge to the imbalance price")
    save(fig, a.img / f"data_{name}_convergence.png")
    late = e_ttg < meta["window_min"] * 0.1
    summary["trade_dev_imb_last10pct_eur"] = float(np.median(dev_imb[late])) if late.any() else None
    summary["trade_dev_imb_first10pct_eur"] = float(np.median(dev_imb[e_ttg > meta["window_min"] * 0.9])) \
        if (e_ttg > meta["window_min"] * 0.9).any() else None

    # 5. order lifetime distribution
    lt = lifetimes_s(msgs)
    fig, ax = plt.subplots(figsize=(6, 3.4))
    lt = lt[lt > 0]
    ax.hist(np.log10(lt), bins=60)
    ax.set_xlabel("log10(order lifetime [s])")
    ax.set_ylabel("orders")
    ax.set_title(f"{name}: resting-order lifetime")
    save(fig, a.img / f"data_{name}_lifetime.png")
    summary["lifetime_s_p50"] = float(np.median(lt))

    # 6. OOW / grid error fraction
    n_add = int((msgs["type"] == P.T_ADD).sum())
    summary["oow_fraction"] = counters["err_oow"] / max(1, n_add)
    summary["grid_fraction"] = counters["err_grid"] / max(1, n_add)
    summary["n_msgs"] = int(len(msgs))
    summary["counts"] = meta["counts"]
    (d / "stylized.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
