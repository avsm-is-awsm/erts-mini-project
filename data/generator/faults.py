"""Fault / stress injection applied to a merged feed (edge scenarios only).

Operates on the internal record array (generate.REC_DTYPE) before sequence numbers are
assigned, except 'seq_gaps' which is applied by generate.py when numbering.
All injected rows carry agent = AG_FAULT so evaluation and plots can exclude them.
"""
from __future__ import annotations

import numpy as np

AG_FAULT = 5


def _insert(recs: np.ndarray, rows: np.ndarray, after: np.ndarray) -> np.ndarray:
    """Insert rows (same dtype) so that row k lands right after recs[after[k]]."""
    order = np.argsort(after, kind="stable")
    rows, after = rows[order], after[order]
    return np.insert(recs, after + 1, rows)


def apply(recs: np.ndarray, faults: dict, rng: np.random.Generator, p_max: int, sets: int) -> np.ndarray:
    n = len(recs)
    if n == 0 or not faults:
        return recs
    t = recs["type"]

    # replace some cancels by over-sized executes (same book effect, counts err_qty_over)
    k = int(faults.get("over_executes", 0))
    if k:
        xs = np.nonzero(t == ord("X"))[0]
        pick = rng.choice(xs, size=min(k, len(xs)), replace=False)
        recs["type"][pick] = ord("E")
        recs["qty"][pick] = 0x7FFFFFFF
        recs["agent"][pick] = AG_FAULT

    new_rows, after = [], []

    def add(row, pos):
        new_rows.append(row)
        after.append(pos)

    adds = np.nonzero(t == ord("A"))[0]
    # duplicate ids: re-send an Add right after the original (order still live)
    for pos in rng.choice(adds, size=min(int(faults.get("dup_ids", 0)), len(adds)), replace=False):
        r = recs[pos].copy()
        r["agent"] = AG_FAULT
        add(r, pos)
    # unknown message types
    for pos in rng.integers(0, n, size=int(faults.get("unknown_types", 0))):
        r = recs[pos].copy()
        r["type"] = rng.choice(np.frombuffer(b"QZBzx\x00\xff", dtype=np.uint8))
        r["agent"] = AG_FAULT
        add(r, pos)
    # bad products: copies of real Adds sent to a non-existent slot
    for pos in rng.choice(adds, size=min(int(faults.get("bad_products", 0)), len(adds)), replace=False):
        r = recs[pos].copy()
        r["slot"] = int(rng.integers(p_max, 65536))
        r["oid"] = int(rng.integers(2**40, 2**41))
        r["agent"] = AG_FAULT
        add(r, pos)
    # deletes of ids that never existed
    for pos in rng.integers(0, n, size=int(faults.get("not_found", 0))):
        r = recs[pos].copy()
        r["type"] = ord("X")
        r["oid"] = int(rng.integers(2**50, 2**51))
        r["price"] = 0
        r["qty"] = 0
        r["agent"] = AG_FAULT
        add(r, pos)
    # off-grid adds (rejected by the grid check)
    for pos in rng.choice(adds, size=min(int(faults.get("off_grid", 0)), len(adds)), replace=False):
        r = recs[pos].copy()
        r["price"] = int(r["price"]) + int(rng.integers(1, 10))
        r["oid"] = int(rng.integers(2**42, 2**43))
        r["agent"] = AG_FAULT
        add(r, pos)

    if new_rows:
        recs = _insert(recs, np.array(new_rows, dtype=recs.dtype), np.array(after))

    # table flood: many long-lived far-from-touch orders, ids crafted to collide in a few sets
    fl = faults.get("table_flood")
    if fl:
        recs = _table_flood(recs, fl, rng, sets)
    return recs


def colliding_ids(rng: np.random.Generator, sets: int, target: int, n: int, lo: int) -> list[int]:
    out: list[int] = []
    while len(out) < n:
        cand = rng.integers(lo, lo + 2**44, size=65536, dtype=np.uint64)
        f = cand.copy()
        for s in (10, 20, 30, 40, 50, 60):
            f ^= cand >> np.uint64(s)
        out.extend(int(x) for x in cand[(f & np.uint64(sets - 1)) == np.uint64(target)][: n - len(out)])
    return out


def _table_flood(recs: np.ndarray, fl: dict, rng: np.random.Generator, sets: int) -> np.ndarray:
    """For each product window listed, add `per_product` resting orders 30-50 EUR away from the
    anchor right after S(OPEN) and delete them at gate closure (before the exchange purge)."""
    n_per = int(fl.get("per_product", 700))
    n_coll_sets = int(fl.get("collide_sets", 16))
    coll_per_set = int(fl.get("collide_per_set", 7))
    opens = np.nonzero((recs["type"] == ord("S")) & (recs["qty"] == 1))[0]
    max_products = int(fl.get("products", len(opens)))
    rows, after = [], []
    base = 2**45
    for j, pos in enumerate(opens[:max_products]):
        o = recs[pos]
        slot, d, anchor = int(o["slot"]), int(o["d"]), int(o["price"])
        close = np.nonzero((recs["type"] == ord("S")) & (recs["qty"] == 2) & (recs["d"] == d))[0]
        if len(close) == 0:
            continue
        cpos = int(close[0])
        ids = []
        for _ in range(n_coll_sets):  # coll_per_set > WAYS ids per set -> err_table_full
            ids += colliding_ids(rng, sets, int(rng.integers(0, sets)), coll_per_set, base)
        ids += [base + 10**9 * (j + 1) + i for i in range(n_per)]
        base += 2**40
        for i, oid in enumerate(ids[: n_per]):
            side = i & 1
            off = int(rng.integers(300, 500)) * 10
            px = anchor - off if side == 0 else anchor + off
            a = o.copy()
            a["type"], a["side"], a["oid"], a["price"], a["qty"] = ord("A"), side, oid, px, 10
            a["agent"] = AG_FAULT
            rows.append(a)
            after.append(pos)
            x = recs[cpos].copy()
            x["type"], x["side"], x["oid"], x["price"], x["qty"] = ord("X"), side, oid, 0, 0
            x["agent"] = AG_FAULT
            rows.append(x)
            after.append(cpos - 1)
    if not rows:
        return recs
    return _insert(recs, np.array(rows, dtype=recs.dtype), np.array(after))
