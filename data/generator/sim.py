"""Event-driven simulation of one quarter-hour product over its active window.

Products never interact, so each one is simulated on its own (seeded per delivery period)
and the per-product message streams are merged by timestamp afterwards (generate.py).

Time is integer ns; prices are integer 0.01 EUR/MWh snapped to the 0.10 EUR grid;
quantities are 0.1 MW.  The true fair value is piecewise constant:

    FV(t) = DA + w(t) * (IMB - DA) + (1 - w(t)) * sum(shocks up to t),   w = logistic ramp 0 -> 1

(the (1 - w) damping of the forecast-revision shocks makes FV converge to the imbalance
price at gate closure; DECISIONS D-24)

updated every fv_step_s and at every forecast-revision shock (dFV = -beta * dWind).
"""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

from .book import ASK, BID, Book

S_NS = 1_000_000_000
MS_NS = 1_000_000
US_NS = 1_000

# agent kinds (events.parquet 'agent' column)
AG_SYSTEM, AG_MM, AG_NOISE, AG_COMP, AG_SLOW = 0, 1, 2, 3, 4
AGENT_NAMES = {AG_SYSTEM: "exchange", AG_MM: "market_maker", AG_NOISE: "noise",
               AG_COMP: "competitor", AG_SLOW: "slow_path"}

# event kinds (heap priority within one timestamp: lower first)
EV_OPEN, EV_FV, EV_SLOW, EV_MM, EV_NOISE, EV_NCANCEL, EV_COMP, EV_CLOSE = range(8)


@dataclass
class ProductSpec:
    d: int              # delivery index (global)
    slot: int
    open_ns: int
    gc_ns: int
    da: int             # 0.01 EUR
    imb: int
    anchor: int
    shocks: list        # [(ts_ns, dFV in 0.01 EUR)]


def grid_floor(x: float, tick: int) -> int:
    return int(math.floor(x / tick)) * tick


def grid_ceil(x: float, tick: int) -> int:
    return int(math.ceil(x / tick)) * tick


def simulate(spec: ProductSpec, prm: dict, seed: int) -> tuple[list, dict]:
    """Return (messages, stats).  A message is the tuple
    (ts, type, side, local_oid, price, qty, extra_u64, agent_kind, agent_id, true_fv)."""
    rng = random.Random(seed)
    gauss, expo, lnv, rnd = rng.gauss, rng.expovariate, rng.lognormvariate, rng.random
    tick = prm["tick"]
    t_open, t_gc = spec.open_ns, spec.gc_ns
    span = t_gc - t_open
    book = Book(prm["px_min"], prm["px_max"])
    msgs: list = []
    heap: list = []
    cnt = [0]

    def push(ts, kind, data=None):
        cnt[0] += 1
        heapq.heappush(heap, (ts, kind, cnt[0], data))

    # ---------------------------------------------------------------- true fair value
    shocks = sorted(spec.shocks)
    shock_sum = [0.0]
    fv_clip = prm["fv_clip"]
    ramp_k = prm["ramp_k"]
    w0 = 1.0 / (1.0 + math.exp(ramp_k * 0.5))
    w1 = 1.0 / (1.0 + math.exp(-ramp_k * 0.5))

    def ramp(t):
        u = (t - t_open) / span
        return (1.0 / (1.0 + math.exp(-ramp_k * (u - 0.5))) - w0) / (w1 - w0)

    fv = [float(spec.da)]

    def fv_at(t):
        w = ramp(t)
        v = spec.da + w * (spec.imb - spec.da) + (1.0 - w) * shock_sum[0]
        return max(spec.anchor - fv_clip, min(spec.anchor + fv_clip, v))

    # ---------------------------------------------------------------- emission helper
    def emit(ts, out, agent, aid):
        f = int(round(fv[0]))
        for (typ, side, oid, px, q, _owner) in out:
            msgs.append((ts, typ, side, oid, px, q, 0, agent, aid, f))
        out.clear()

    buf: list = []

    # ---------------------------------------------------------------- agents
    lam0, a_rush, tau = prm["noise_rate"], prm["rush_a"], prm["rush_tau_s"] * S_NS
    n_mm = prm["n_mm"]
    mm_hs = [rng.uniform(*prm["mm_half_spread"]) for _ in range(n_mm)]
    mm_levels = [rng.randint(1, prm["mm_levels_max"]) for _ in range(n_mm)]
    mm_size = [rng.uniform(*prm["mm_size"]) for _ in range(n_mm)]
    mm_orders = [dict() for _ in range(n_mm)]  # (side, px) -> oid
    mm_gen = [0] * n_mm                         # one live refresh chain per market maker

    def mm_refresh(now, i):
        mm_gen[i] += 1
        est = fv[0] + gauss(0.0, prm["mm_sigma"])
        hs, gap = mm_hs[i], prm["mm_gap"]
        bb, ba = book.best_bid(), book.best_ask()
        want = {}
        for j in range(mm_levels[i]):
            bp = grid_floor(est - hs - j * gap, tick)
            ap = grid_ceil(est + hs + j * gap, tick)
            if ba is not None and bp >= ba:   # post-only
                bp = ba - tick * (1 + j)
            if bb is not None and ap <= bb:
                ap = bb + tick * (1 + j)
            size = max(5, int(lnv(math.log(mm_size[i]), 0.4)))
            want[(BID, bp)] = size
            want[(ASK, ap)] = size
        cur = mm_orders[i]
        for key in list(cur):
            oid = cur[key]
            o = book.orders.get(oid)
            if o is None:          # filled meanwhile
                del cur[key]
                continue
            if key not in want:
                book.cancel(oid, buf)
                del cur[key]
            elif want[key] < o[2] and rnd() < prm["mm_reduce_p"]:
                book.reduce(oid, o[2] - want[key], buf)
                want.pop(key)
            else:
                want.pop(key)
        for (side, px), size in want.items():
            oid = book.submit(("mm", i), side, px, size, False, now, buf)
            if oid:
                cur[(side, px)] = oid
        emit(now, buf, AG_MM, i)
        rate = (1.0 + prm["mm_rush_a"] * math.exp(-(t_gc - now) / tau)) / prm["mm_refresh_s"]
        push(now + max(1, int(expo(rate) * S_NS)), EV_MM, (i, mm_gen[i]))

    def mm_react(now):
        for i in range(n_mm):
            lat = int(lnv(math.log(prm["mm_react_ms"] * MS_NS), prm["mm_react_sigma"]))
            push(now + max(1, lat), EV_MM, (i, mm_gen[i]))

    # noise traders
    lam_max = lam0 * (1.0 + a_rush)

    def lam(t):
        return lam0 * (1.0 + a_rush * math.exp(-(t_gc - t) / tau))

    def noise_arrival(now):
        side = BID if rnd() < 0.5 else ASK
        size = int(min(max(lnv(math.log(prm["noise_size_med"]), 0.8), 5), 100))
        if rnd() < prm["noise_aggr_p"]:
            opp = book.best_ask() if side == BID else book.best_bid()
            if opp is not None:
                px = opp + (tick * rng.randint(0, 2) if side == BID else -tick * rng.randint(0, 2))
                book.submit(("noise", 0), side, px, size, rnd() < 0.5, now, buf)
                emit(now, buf, AG_NOISE, 0)
                return
        # passive limit order around the true value with its own error
        if rnd() < prm["noise_far_p"]:
            off = rng.uniform(*prm["noise_far_eur"])
            px = grid_floor(fv[0] - off, tick) if side == BID else grid_ceil(fv[0] + off, tick)
        else:
            err = gauss(0.0, prm["noise_sigma"])
            hs = prm["noise_half_spread"]
            px = grid_floor(fv[0] - hs + err, tick) if side == BID else grid_ceil(fv[0] + hs + err, tick)
        oid = book.submit(("noise", 0), side, px, size, False, now, buf)
        emit(now, buf, AG_NOISE, 0)
        if oid:
            life = int(expo(1.0 / prm["noise_life_s"]) * S_NS)
            push(now + max(1, life), EV_NCANCEL, oid)

    def noise_cancel(now, oid):
        o = book.orders.get(oid)
        if o is None:
            return
        if rnd() < prm["noise_reduce_p"] and o[2] > 10:
            book.reduce(oid, o[2] // 2, buf)
            emit(now, buf, AG_NOISE, 0)
            life = int(expo(1.0 / prm["noise_life_s"]) * S_NS)
            push(now + max(1, life), EV_NCANCEL, oid)
        else:
            book.cancel(oid, buf)
            emit(now, buf, AG_NOISE, 0)

    # competitor takers: per-agent latency median (log-spaced), lognormal jitter
    n_ct = prm["n_ct"]
    lo, hi = math.log(prm["ct_lat_min_us"]), math.log(prm["ct_lat_max_us"])
    ct_med = [math.exp(lo + (hi - lo) * j / max(1, n_ct - 1)) * US_NS for j in range(n_ct)]
    ct_pending = [False] * n_ct
    edge_c = prm["ct_edge"]

    def mispriced():
        ba, bb = book.best_ask(), book.best_bid()
        return (ba is not None and ba <= fv[0] - edge_c), (bb is not None and bb >= fv[0] + edge_c)

    def comp_trigger(now):
        buy, sell = mispriced()
        if not (buy or sell):
            return
        for j in range(n_ct):
            if not ct_pending[j]:
                ct_pending[j] = True
                push(now + max(1, int(lnv(math.log(ct_med[j]), prm["ct_lat_sigma"]))), EV_COMP, j)

    def comp_take(now, j):
        ct_pending[j] = False
        buy, sell = mispriced()
        if buy:
            book.submit(("comp", j), BID, grid_floor(fv[0] - edge_c, tick), prm["ct_clip"], True, now, buf)
        elif sell:
            book.submit(("comp", j), ASK, grid_ceil(fv[0] + edge_c, tick), prm["ct_clip"], True, now, buf)
        if buf:
            emit(now, buf, AG_COMP, j)

    # our slow path: V = FV + model error, delayed
    def slow_emit(now, val):
        msgs.append((now, "V", 0, 0, val, prm["edge"], 0, AG_SLOW, 0, int(round(fv[0]))))

    # ---------------------------------------------------------------- schedule
    push(t_open, EV_OPEN)
    k = 1
    while t_open + k * prm["fv_step_s"] * S_NS < t_gc:
        push(t_open + k * prm["fv_step_s"] * S_NS, EV_FV, None)
        k += 1
    for ts, dv in shocks:
        if t_open < ts < t_gc:
            push(ts, EV_FV, dv)
    for i in range(n_mm):
        push(t_open + int(rng.uniform(0.001, 2.0) * S_NS), EV_MM, (i, 0))
    push(t_open + int(expo(lam_max) * S_NS), EV_NOISE)
    push(t_gc, EV_CLOSE)

    # ---------------------------------------------------------------- run
    while heap:
        now, kind, _, data = heapq.heappop(heap)
        if kind == EV_CLOSE:
            msgs.append((now, "S", 0, 0, 0, 2, 0, AG_SYSTEM, 0, int(round(fv[0]))))
            book.purge(buf)
            emit(now, buf, AG_SYSTEM, 0)
            break
        if kind == EV_OPEN:
            msgs.append((now, "S", 0, 0, 0, 0, 0, AG_SYSTEM, 0, int(round(fv[0]))))
            msgs.append((now, "S", 0, 0, spec.anchor, 1, t_gc, AG_SYSTEM, 0, int(round(fv[0]))))
            fv[0] = fv_at(now)
            push(now + prm["d_model_ms"] * MS_NS, EV_SLOW, fv[0])
        elif kind == EV_FV:
            if data is not None:
                shock_sum[0] += data
            old = fv[0]
            fv[0] = fv_at(now)
            push(now + prm["d_model_ms"] * MS_NS, EV_SLOW, fv[0])
            if data is not None and abs(fv[0] - old) >= tick:
                mm_react(now)
            comp_trigger(now)
        elif kind == EV_SLOW:
            # the slow path's estimate of the FV it observed d_model ago
            slow_emit(now, int(round(data + gauss(0.0, prm["sigma_model"]))))
        elif kind == EV_MM:
            i, gen = data
            if gen == mm_gen[i]:
                mm_refresh(now, i)
                comp_trigger(now)
        elif kind == EV_NOISE:
            if rnd() < lam(now) / lam_max:
                noise_arrival(now)
                comp_trigger(now)
            push(now + max(1, int(expo(lam_max) * S_NS)), EV_NOISE)
        elif kind == EV_NCANCEL:
            noise_cancel(now, data)
        elif kind == EV_COMP:
            comp_take(now, data)
            comp_trigger(now)
    stats = dict(final_fv=int(round(fv[0])))
    return msgs, stats
