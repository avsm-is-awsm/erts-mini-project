"""Rate Monotonic Analysis + exact response-time analysis of the live-replay task set (docs/09).

    python tools/rma.py --wcet runs/rt/wcet_sw.log [--live runs/rt/itch_sample/live_sw.json]
                        [--json out.json] [--md out.md] [--plot docs/img/rta_sw.png]

--wcet: a UART log (or file) containing the board's `@W {...}` line from `wcet hw|sw [budget]`:
        execution times measured in isolation (CLI task raised above all application tasks).
--live: live_<mode>.json written by `live ...`: measured response times of the same tasks.

Task model (FreeRTOS, fixed priorities, rate monotonic: shorter period = higher priority,
implicit deadlines D = T, synchronous release at T0 = the critical instant):

    tick ISR    T = 1 ms    C = longest interruption of a busy loop (gap probe)
    feed        T = 1 ms    C = feed job WCET + scheduling overhead
    monitor     T = 10 ms   C = monitor job WCET + scheduling overhead
    telemetry   T = 100 ms  C = telemetry job WCET (formats + prints one line) + overhead
    (cli: background, lowest; silent while a live run is active, so it never holds the UART
     mutex then: blocking B = 0 for the analysed tasks)

The scheduling overhead per job (two context switches + vTaskDelayUntil) is measured with an
empty 1 kHz probe task: (longest gap with probe) - (longest gap with the tick alone).
The feed task and the tick ISR share T = 1 ms; the ISR always has priority.

Response time (Joseph & Pandya / Audsley):  R_i = C_i + B_i + sum_{j in hp(i)} ceil(R_i / T_j) C_j
With release jitter J (Audsley/Tindell):  w_i = C_i + B_i + sum_{j in hp(i)} ceil((w_i + J_j) / T_j) C_j,
R_i = J_i + w_i.  J = the longest measured delay between a task's release (its tick) and the start
of its job (--jitter-us, from `live` runs: R max - C max); the tick ISR has J = 0.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_wcet(path: Path) -> dict:
    text = path.read_text(errors="replace")
    m = re.findall(r"@W (\{.*\})", text)
    if not m:
        raise SystemExit(f"{path}: no '@W {{...}}' line (run `wcet hw|sw` on the board)")
    return json.loads(m[-1])


def task_set(w: dict, jitter: float = 0.0) -> list[dict]:
    ns = 1e-9
    tick = w["tick_isr"]["max_ns"] * ns
    overhead = max(0.0, (w["tick_plus_task"]["max_ns"] - w["tick_isr"]["max_ns"]) * ns)
    return [
        dict(name="tick ISR", prio=99, T=1e-3, C=tick, wcet=tick, overhead=0.0, J=0.0),
        dict(name="feed", prio=4, T=1e-3, C=w["feed"]["cmax_ns"] * ns + overhead, wcet=w["feed"]["cmax_ns"] * ns,
             overhead=overhead, J=jitter),
        dict(name="monitor", prio=3, T=10e-3, C=w["monitor"]["cmax_ns"] * ns + overhead,
             wcet=w["monitor"]["cmax_ns"] * ns, overhead=overhead, J=jitter),
        dict(name="telemetry", prio=2, T=100e-3, C=w["telemetry"]["cmax_ns"] * ns + overhead,
             wcet=w["telemetry"]["cmax_ns"] * ns, overhead=overhead, J=jitter),
    ]


def rta(tasks: list[dict], blocking: float = 0.0) -> list[dict]:
    """Exact response times; tasks sorted by priority (highest first)."""
    ts = sorted(tasks, key=lambda t: -t["prio"])
    out = []
    for i, t in enumerate(ts):
        hp = ts[:i]
        w = t["C"] + blocking
        for _ in range(1000):
            nxt = t["C"] + blocking + sum(math.ceil((w + h.get("J", 0.0)) / h["T"] - 1e-12) * h["C"] for h in hp)
            if abs(nxt - w) < 1e-12:
                break
            w = nxt
            if w > t["T"] * 10:
                break
        r = t.get("J", 0.0) + w
        out.append(dict(t, R=r, ok=r <= t["T"]))
    return out


def analyse(w: dict, live: dict | None = None, jitter: float = 0.0) -> dict:
    tasks = task_set(w, jitter)
    n = len(tasks)
    u = [t["C"] / t["T"] for t in tasks]
    U = sum(u)
    ll = n * (2 ** (1 / n) - 1)
    hyper = math.prod(x + 1 for x in u)
    res = rta(tasks)
    meas = {}
    if live:
        hz = live["timer_hz"]
        for t in live["tasks"]:
            meas[t["name"]] = dict(jobs=t["jobs"], misses=t["misses"], rmax=t["rmax_ticks"] / hz,
                                   cmax=t["cmax_ticks"] / hz,
                                   u=(t["csum_ticks"] / hz) / (t["jobs"] * t["period_ticks"] / hz) if t["jobs"] else 0)
    rows = []
    for t, ui in zip(res, [r["C"] / r["T"] for r in res]):
        m = meas.get(t["name"].split()[0], {})
        rows.append(dict(name=t["name"], prio=t["prio"], T_ms=t["T"] * 1e3, C_us=t["C"] * 1e6, wcet_us=t["wcet"] * 1e6,
                         overhead_us=t["overhead"] * 1e6, U=ui, R_us=t["R"] * 1e6, schedulable=t["ok"],
                         measured_R_us=m.get("rmax", float("nan")) * 1e6 if m else None,
                         measured_jobs=m.get("jobs"), measured_misses=m.get("misses"),
                         measured_U=m.get("u")))
    # largest feed budget that stays schedulable, assuming the feed WCET grows linearly with the
    # number of messages per job (fixed part = the empty job is not measured: conservative)
    per_msg = w["feed"]["cmax_ns"] * 1e-9 / w["budget"]
    best = 0
    for b in range(1, 1_000_000):
        tt = task_set(w, jitter)
        tt[1]["C"] = per_msg * b + tt[1]["overhead"]
        if not all(r["ok"] for r in rta(tt)):
            break
        best = b
    return dict(mode=w["mode"], budget=w["budget"], n_tasks=n, jitter_us=jitter * 1e6, U=U, ll_bound=ll, ll_ok=U <= ll, hyperbolic=hyper,
                hyperbolic_ok=hyper <= 2.0, rta_ok=all(r["schedulable"] for r in rows), tasks=rows,
                feed_per_msg_us=per_msg * 1e6, max_budget_rta=best,
                max_rate_msgs_per_s=w["budget"] / 1e-3,
                live=dict(speed=live["speed"], budget=live["budget"], released=live["released"],
                          n_msgs=live["n_msgs"], error=live["error"]) if live else None)


def markdown(a: dict) -> str:
    L = [f"Task set ({a['mode']} mode, feed budget {a['budget']} messages / 1 ms, release jitter "
         f"J = {a['jitter_us']:.0f} µs):", "",
         "| Task | Prio | T (ms) | C (µs) = WCET + overhead | U | R analytic (µs) | R measured max (µs) | jobs / misses |",
         "|---|---|---|---|---|---|---|---|"]
    for r in a["tasks"]:
        meas = f"{r['measured_R_us']:.1f}" if r["measured_R_us"] is not None else "—"
        jm = f"{r['measured_jobs']} / {r['measured_misses']}" if r["measured_jobs"] is not None else "—"
        L.append(f"| {r['name']} | {r['prio']} | {r['T_ms']:g} | {r['C_us']:.1f} = {r['wcet_us']:.1f} + "
                 f"{r['overhead_us']:.1f} | {r['U']:.3f} | {r['R_us']:.1f} {'✓' if r['schedulable'] else '✗'} | "
                 f"{meas} | {jm} |")
    L += ["", f"U = {a['U']:.3f}; Liu & Layland bound for n = {a['n_tasks']}: {a['ll_bound']:.3f} "
          f"({'passes' if a['ll_ok'] else 'not met'}); hyperbolic bound Π(Uᵢ+1) = {a['hyperbolic']:.3f} ≤ 2 "
          f"({'passes' if a['hyperbolic_ok'] else 'not met'}); exact RTA: "
          f"{'all deadlines met' if a['rta_ok'] else 'deadline miss possible'}.",
          f"Feed cost ≈ {a['feed_per_msg_us']:.3f} µs per message; the largest budget the RTA accepts is "
          f"{a['max_budget_rta']} messages per 1 ms job (linear extrapolation)."]
    return "\n".join(L) + "\n"


def plot(a: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = a["tasks"]
    names = [r["name"] for r in rows]
    x = range(len(rows))
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.bar([i - 0.27 for i in x], [r["R_us"] for r in rows], width=0.27, label="R analytic (RTA)", color="#3b6ea5")
    ax.bar([i for i in x], [r["measured_R_us"] or 0 for r in rows], width=0.27, label="R measured max",
           color="#e08a2b")
    ax.bar([i + 0.27 for i in x], [r["T_ms"] * 1e3 for r in rows], width=0.27, label="deadline D = T",
           color="#bbbbbb")
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names)
    ax.set_ylabel("µs (log)")
    ax.set_title(f"Response times, {a['mode']} live replay (budget {a['budget']})")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wcet", type=Path, required=True)
    ap.add_argument("--live", type=Path, default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--plot", type=Path, default=None)
    ap.add_argument("--jitter-us", type=float, default=0.0, help="release jitter J of the tasks (not the tick ISR)")
    a = ap.parse_args(argv)
    w = read_wcet(a.wcet)
    live = json.loads(a.live.read_text()) if a.live else None
    res = analyse(w, live, a.jitter_us * 1e-6)
    md = markdown(res)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    print(md)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(dict(wcet=w, analysis=res), indent=2) + "\n")
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    if a.plot:
        plot(res, a.plot)
    return 0 if res["rta_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
