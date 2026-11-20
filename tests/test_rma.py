"""tools/rma.py against textbook cases (no board needed)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import rma  # noqa: E402


def T(name, prio, t, c):
    return dict(name=name, prio=prio, T=t, C=c, wcet=c, overhead=0.0)


def test_rta_textbook():
    # Burns & Wellings style example: T=(7,12,20), C=(3,3,5) -> R=(3,6,20)
    r = rma.rta([T("a", 3, 7, 3), T("b", 2, 12, 3), T("c", 1, 20, 5)])
    assert [x["R"] for x in r] == [3, 6, 20]
    assert all(x["ok"] for x in r)


def test_rta_detects_miss_and_blocking():
    r = rma.rta([T("a", 2, 4, 2), T("b", 1, 6, 3)])  # U = 1.0: R_b = 3+2*2 = 7 > 6
    assert r[1]["R"] == 7 and not r[1]["ok"]
    r = rma.rta([T("a", 2, 10, 2), T("b", 1, 20, 3)], blocking=1)
    assert r[0]["R"] == 3 and r[1]["R"] == 6


def wcet(feed_ns=200_000, budget=128):
    return {"mode": "sw", "budget": budget,
            "tick_isr": {"max_ns": 3000, "mean_ns": 1500, "n": 2000},
            "tick_plus_task": {"max_ns": 8000, "mean_ns": 4000, "n": 2000, "probe_jobs": 2000},
            "feed": {"jobs": 100, "cmax_ns": feed_ns, "cmean_ns": feed_ns // 2},
            "monitor": {"jobs": 10, "cmax_ns": 20_000, "cmean_ns": 10_000},
            "telemetry": {"jobs": 30, "cmax_ns": 15_000_000, "cmean_ns": 14_000_000}}


def test_analyse_bounds_and_budget():
    a = rma.analyse(wcet())
    rows = {r["name"]: r for r in a["tasks"]}
    # C = WCET + measured overhead (8 us - 3 us)
    assert math.isclose(rows["feed"]["C_us"], 205.0)
    assert math.isclose(rows["tick ISR"]["R_us"], 3.0)
    assert math.isclose(rows["feed"]["R_us"], 208.0)
    assert a["rta_ok"] and a["U"] < 1
    assert math.isclose(a["ll_bound"], 4 * (2 ** 0.25 - 1))
    # the budget extrapolation must itself be schedulable, one more message must not be
    per_msg = 200e-6 / 128
    assert a["max_budget_rta"] > 128
    w = wcet(feed_ns=int(per_msg * 1e9 * (a["max_budget_rta"] + 1)), budget=a["max_budget_rta"] + 1)
    assert not rma.analyse(w)["rta_ok"]


def test_unschedulable_feed():
    # feed C = 990 + 5 us: it still fits its own 1 ms (998 us with the tick), but it leaves no
    # time for the monitor, whose response time grows past its 10 ms deadline
    rows = {r["name"]: r for r in rma.analyse(wcet(feed_ns=990_000))["tasks"]}
    assert rows["feed"]["schedulable"] and math.isclose(rows["feed"]["R_us"], 998.0)
    assert not rows["monitor"]["schedulable"]
    # a feed job longer than its period cannot be schedulable at all
    a = rma.analyse(wcet(feed_ns=996_000))
    assert not {r["name"]: r for r in a["tasks"]}["feed"]["schedulable"] and not a["rta_ok"]


def test_release_jitter():
    # Tindell example style: with jitter J the response grows by J plus extra interference
    hi = dict(name="a", prio=2, T=10, C=3, wcet=3, overhead=0.0, J=0)
    lo = dict(name="b", prio=1, T=20, C=4, wcet=4, overhead=0.0, J=0)
    assert [x["R"] for x in rma.rta([hi, lo])] == [3, 7]
    hi_j = dict(hi, J=8)  # a released up to 8 late: two of its jobs can hit b's window
    r = rma.rta([hi_j, lo])
    assert r[0]["R"] == 11 and r[1]["R"] == 10  # w_b = 4 + ceil((w_b + 8)/10)*3 = 10
    a = rma.analyse(wcet(), jitter=130e-6)
    rows = {x["name"]: x for x in a["tasks"]}
    assert math.isclose(rows["feed"]["R_us"], 130 + 205 + 3)
