"""Adversarial random message sequences for the Python <-> C++ cross-check.

Each sequence is short (tens to hundreds of messages) and deliberately hits every
branch of SPEC 6.4-6.6: unknown types, bad products, seq gaps, dup/missing ids,
hash-set collisions and full sets, off-grid / out-of-window prices, quantity
wrap-around, state-code abuse, crossed books, every risk reject and the kill switch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402

TYPES = np.array([ord(c) for c in "AXEMSVH"] + [ord("Q"), 0, 255], dtype=np.uint8)
TYPE_W = np.array([34, 9, 10, 8, 9, 10, 3, 1, 0.5, 0.5])
TYPE_W = TYPE_W / TYPE_W.sum()


def colliding_ids(rng: np.random.Generator, sets: int, target_set: int, n: int) -> list[int]:
    """n order ids that all hash into target_set (brute force over random 64-bit ids)."""
    out: list[int] = []
    while len(out) < n:
        cand = rng.integers(0, 2**63, size=4096, dtype=np.uint64) * np.uint64(2) + np.uint64(rng.integers(0, 2))
        f = cand.copy()
        for s in (10, 20, 30, 40, 50, 60):
            f ^= cand >> np.uint64(s)
        hit = cand[(f & np.uint64(sets - 1)) == np.uint64(target_set)]
        out.extend(int(x) for x in hit[: n - len(out)])
    return out


def random_config(rng: np.random.Generator) -> list[int]:
    ctrl = 1 | 4  # strat_en | dbg_en
    if rng.random() < 0.1:
        ctrl &= ~1          # strategy off
    if rng.random() < 0.1:
        ctrl |= 2           # kill
    if rng.random() < 0.15:
        ctrl &= ~4          # no debug output
    return [
        ctrl,
        int(rng.choice([1, 5, 50, 1000, 2**32 - 1])),            # clip_qty
        int(rng.choice([0, 3, 100, 2**32 - 1])),                 # max_order_qty
        int(rng.choice([-5, 0, 7, 200, 2**31 - 1])) & 0xFFFFFFFF,  # max_pos (int32)
        int(rng.choice([0, 30, 2000, 2**32 - 1])),               # collar
        int(rng.choice([0, 0, 50, 1000, 2**32 - 1])),            # min_gap_ns
        int(rng.choice([0, 2, 5, 1_000_000])),                   # max_orders
        int(rng.choice([0, 0, 1, 1000])),                        # lockout_ms
    ]


def random_sequence(rng: np.random.Generator, prm: dict, length: int) -> np.ndarray:
    p_max, sets, tick, w = prm["P_MAX"], prm["SETS"], prm["LADDER_TICK"], prm["W"]
    m = np.zeros(length, dtype=P.MSG_DTYPE)
    types = rng.choice(TYPES, size=length, p=TYPE_W)
    # id pool: small ints + ids colliding in one set (forces table_full) + random 64-bit
    pool = list(range(1, 40))
    pool += colliding_ids(rng, sets, int(rng.integers(0, sets)), prm["WAYS"] + 3)
    pool += [int(x) for x in rng.integers(0, 2**63, size=10, dtype=np.uint64)]
    pool += [2**64 - 1, 2**63]
    anchors = [int(rng.choice([0, 5000, -3000, 999_900, -999_900, 2**31 - 1000, -(2**31) + 1000]))
               for _ in range(p_max)]
    ts = int(rng.integers(0, 2**40))
    seq = int(rng.choice([1, 1, 1, 0, 2**32 - 3]))
    for k in range(length):
        t = int(types[k])
        prod = int(rng.integers(0, p_max)) if rng.random() > 0.04 else int(rng.integers(p_max, 2**16))
        side = int(rng.integers(0, 2))
        flags = side | (2 if (t == ord("H") and rng.random() < 0.5) else 0) | (
            int(rng.integers(0, 256)) & 0xFC if rng.random() < 0.05 else 0)
        oid = int(pool[int(rng.integers(0, len(pool)))])
        anc = anchors[prod % p_max]
        r = rng.random()
        if r < 0.75:
            lvl = int(rng.integers(-w // 2 - 3, w // 2 + 3)) if rng.random() < 0.3 else int(rng.integers(-8, 8))
            price = anc + lvl * tick
        elif r < 0.85:
            price = anc + int(rng.integers(-50, 50))          # often off grid
        elif r < 0.92:
            price = anc + int(rng.integers(-3, 3)) * w * tick  # out of window
        else:
            price = int(rng.integers(-(2**31), 2**31))
        price = max(-(2**31), min(2**31 - 1, price))
        qty = int(rng.choice([1, 2, 5, 10, 50, 100, 300])) if rng.random() < 0.95 else int(
            rng.choice([0, 2**31, 2**32 - 1, 2**32 - 2]))
        if t == ord("S"):
            qty = int(rng.choice([0, 1, 1, 2, 3, 0, 1, 4, 255])) if rng.random() < 0.97 else 2**32 - 1
            if qty == 1 and rng.random() < 0.8:
                price = anchors[prod % p_max]
            oid = ts + int(rng.choice([10**6, 10**9, 10**12, 0, 2**64 - 1 - ts]))  # gc_ts
            oid &= 2**64 - 1
        if t == ord("V"):
            price = anc + int(rng.integers(-20, 20)) * tick + (int(rng.integers(-5, 5)) if rng.random() < 0.2 else 0)
            qty = int(rng.choice([0, 5, 10, 50, 200])) if rng.random() < 0.97 else 2**32 - 1
        # time and sequence
        dt = int(rng.choice([0, 1, 10, 1000, 10**6]))
        if rng.random() < 0.02:
            ts = max(0, ts - int(rng.integers(0, 10**6)))
        else:
            ts += dt
        if rng.random() < 0.01:
            ts = 2**64 - 1 - int(rng.integers(0, 1000))
        ts &= 2**64 - 1
        if rng.random() < 0.03:
            seq = int(rng.integers(0, 2**32))
        else:
            seq = (seq + 1) & 0xFFFFFFFF
        m[k] = (t, flags & 0xFF, prod, seq, ts, oid & (2**64 - 1), price, qty & 0xFFFFFFFF)
    return m


def make_batch(seed: int, n_seq: int, prm: dict, min_len: int = 20, max_len: int = 400):
    rng = np.random.default_rng(seed)
    seqs, idx = [], []
    for _ in range(n_seq):
        L = int(rng.integers(min_len, max_len))
        s = random_sequence(rng, prm, L)
        seqs.append(s)
        idx.append([L] + random_config(rng))
    return seqs, np.array(idx, dtype=np.uint32)
