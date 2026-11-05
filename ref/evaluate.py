"""Simulated exchange: fills and P&L of our orders as a function of tick-to-trade latency
(SPEC 8.2).  Produces the headline chart (fill rate & P&L vs latency).

    python ref/evaluate.py data/out/day_default [--orders golden_orders.bin]
                           [--hw-ns 150 --hw-label "HW (cosim)"] [--sw-ns 9000 --sw-label "SW (ARM)"]

For every 'O' record with trigger timestamp T and an assumed latency L, the IOC order is
matched against the book reconstructed from feed.bin after all messages with ts <= T + L:
it fills at the resting prices that are at least as good as its limit, up to its quantity,
with no market impact (our orders never change the book).  P&L per fill is measured against
the delivery period's imbalance price:

    pnl_EUR = (ref - px) / 100 * qty / 10 * 0.25      (buy; negated for a sell)

Simplified exchange, synthetic data: the numbers illustrate latency sensitivity only.
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

LATENCIES_NS = [0, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000]


def _label(ns: int) -> str:
    if ns == 0:
        return "0"
    for unit, div in (("s", 10**9), ("ms", 10**6), ("µs", 10**3), ("ns", 1)):
        if ns >= div:
            v = ns / div
            return f"{v:g} {unit}"
    return f"{ns} ns"


def evaluate(ds: Path, orders_file: Path | None = None, latencies=LATENCIES_NS) -> dict:
    msgs = P.read_msgs(ds / "feed.bin")
    meta = json.loads((ds / "meta.json").read_text())
    ords = P.read_out(orders_file or ds / "golden_orders.bin")
    o = P.as_ord(ords[ords[:, 0] == P.O_ORDER])
    if len(o) == 0:
        return dict(n_orders=0, rows=[])

    # map trigger seq -> feed index -> timestamp and delivery period
    seq = msgs["seq"].astype(np.int64)
    if not (np.diff(seq) > 0).all():
        raise ValueError("evaluate.py needs strictly increasing feed sequence numbers")
    trig_idx = np.searchsorted(seq, o["trig_seq"].astype(np.int64))
    T = msgs["ts_ns"][trig_idx].astype(np.int64)
    import pyarrow.parquet as pq

    delivery = pq.read_table(ds / "events.parquet", columns=["delivery"]).column(0).to_numpy()
    d_of = delivery[trig_idx]
    imb = {p["d"]: p["imb"] for p in meta["products"]}
    ref = np.array([imb.get(int(x), 0) for x in d_of], dtype=np.int64)

    nq = len(o) * len(latencies)
    q_t = np.repeat(T, len(latencies)) + np.tile(np.array(latencies, dtype=np.int64), len(o))
    q_ord = np.repeat(np.arange(len(o)), len(latencies))
    q_lat = np.tile(np.arange(len(latencies)), len(o))
    order = np.argsort(q_t, kind="stable")

    fill_q = np.zeros(nq, dtype=np.int64)
    fill_notional = np.zeros(nq, dtype=np.int64)  # sum(px * q)

    # ---- sweep the feed once, answering queries in time order
    ts_l = msgs["ts_ns"].astype(np.int64).tolist()
    ty_l = msgs["type"].tolist()
    pr_l = msgs["product"].tolist()
    fl_l = msgs["flags"].tolist()
    oid_l = msgs["order_id"].tolist()
    px_l = msgs["price"].tolist()
    qt_l = msgs["qty"].tolist()
    live: dict[int, list] = {}
    book: dict[tuple, dict] = {}
    o_prod = o["product"].tolist()
    o_side = (o["flags"] & 1).tolist()
    o_px = o["price"].tolist()
    o_q = o["qty"].tolist()
    A, X, E, M = P.T_ADD, P.T_DEL, P.T_EXEC, P.T_RED
    i, n = 0, len(ts_l)
    for qi in order.tolist():
        tq = int(q_t[qi])
        while i < n and ts_l[i] <= tq:
            t = ty_l[i]
            if t == A:
                k = (pr_l[i], fl_l[i] & 1)
                lv = book.setdefault(k, {})
                lv[px_l[i]] = lv.get(px_l[i], 0) + qt_l[i]
                live[oid_l[i]] = [k, px_l[i], qt_l[i]]
            elif t == X or t == E or t == M:
                e = live.get(oid_l[i])
                if e is not None:
                    r = e[2] if t == X else min(qt_l[i], e[2])
                    lv = book[e[0]]
                    lv[e[1]] -= r
                    if lv[e[1]] == 0:
                        del lv[e[1]]
                    e[2] -= r
                    if e[2] == 0:
                        del live[oid_l[i]]
            i += 1
        j = int(q_ord[qi])
        p, side, lim, want = o_prod[j], o_side[j], o_px[j], o_q[j]
        opp = book.get((p, 1 - side), {})
        if side == 0:   # buy: take asks <= limit, cheapest first
            levels = sorted(x for x in opp if x <= lim)
        else:           # sell: take bids >= limit, highest first
            levels = sorted((x for x in opp if x >= lim), reverse=True)
        got = notional = 0
        for x in levels:
            if got >= want:
                break
            q = min(opp[x], want - got)
            got += q
            notional += q * x
        fill_q[qi] = got
        fill_notional[qi] = notional

    sign = np.where(np.array(o_side)[q_ord] == 0, 1, -1)
    # pnl = sign * (ref*q - notional) / 100 / 10 * 0.25
    pnl = sign * (ref[q_ord] * fill_q - fill_notional) / 100.0 / 10.0 * 0.25
    rows = []
    want_total = int(np.sum(o["qty"]))
    for li, L in enumerate(latencies):
        sel = q_lat == li
        fq = int(fill_q[sel].sum())
        rows.append(dict(latency_ns=int(L), label=_label(int(L)), orders=int(len(o)),
                         orders_filled=int((fill_q[sel] > 0).sum()),
                         qty_ordered=want_total, qty_filled=fq,
                         fill_rate=fq / want_total if want_total else 0.0,
                         pnl_eur=float(pnl[sel].sum()),
                         pnl_per_mwh=float(pnl[sel].sum() / (fq / 10 * 0.25)) if fq else 0.0))
    return dict(dataset=ds.name, n_orders=int(len(o)), rows=rows)


def plot(res: dict, out: Path, hw_ns=None, hw_label="HW", sw_ns=None, sw_label="SW") -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from tools.plots.style import HW, PALETTE, SW, apply_style, save

    apply_style()
    rows = res["rows"]
    x = np.array([max(r["latency_ns"], 10) for r in rows], dtype=float)  # 0 plotted at 10 ns
    fr = np.array([r["fill_rate"] for r in rows]) * 100
    pnl = np.array([r["pnl_eur"] for r in rows])
    fig, ax1 = plt.subplots(figsize=(6.4, 3.6))
    ax1.plot(x, fr, marker="o", color=PALETTE[0], label="fill rate")
    ax1.set_xscale("log")
    ax1.set_xlabel("tick-to-trade latency (0 plotted at 10 ns)")
    ax1.set_ylabel("fill rate [% of ordered MW]", color=PALETTE[0])
    ax1.set_ylim(0, 105)
    ax2 = ax1.twinx()
    ax2.plot(x, pnl, marker="s", color=PALETTE[3], label="P&L")
    ax2.set_ylabel("P&L vs imbalance price [EUR]", color=PALETTE[3])
    ax2.axhline(0, color="#999", lw=0.6)
    ax2.spines["right"].set_visible(True)
    ax2.grid(False)
    for v, lab, c, y in ((hw_ns, hw_label, HW, 8), (sw_ns, sw_label, SW, 8)):
        if v:
            ax1.axvline(v, color=c, ls="--", lw=1.2)
            ax1.text(v * 1.12, y, lab, color=c, fontsize=7.5, rotation=90, va="bottom", ha="left")
    ax1.set_xticks(x)
    ax1.set_xticklabels([r["label"] for r in rows], rotation=30, fontsize=7)
    ax1.set_title(f"{res['dataset']}: fill rate and P&L vs latency ({res['n_orders']} orders)")
    save(fig, out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--orders", type=Path, default=None)
    ap.add_argument("--hw-ns", type=float, default=None)
    ap.add_argument("--hw-label", default="HW fabric")
    ap.add_argument("--sw-ns", type=float, default=None)
    ap.add_argument("--sw-label", default="SW (ARM)")
    ap.add_argument("--img", type=Path, default=ROOT / "docs" / "img")
    a = ap.parse_args(argv)
    res = evaluate(a.dataset, a.orders)
    (a.dataset / "eval.json").write_text(json.dumps(res, indent=2) + "\n")
    plot(res, a.img / f"pnl_vs_latency_{a.dataset.name}.png", a.hw_ns, a.hw_label, a.sw_ns, a.sw_label)
    print(f"{'latency':>10} {'fill%':>7} {'filled':>8} {'P&L EUR':>10}")
    for r in res["rows"]:
        print(f"{r['label']:>10} {100*r['fill_rate']:7.1f} {r['orders_filled']:8d} {r['pnl_eur']:10.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
