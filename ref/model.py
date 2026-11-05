"""Golden reference model of the dk1-t2t fast path (SPEC section 6, docs/DECISIONS.md).

This is the semantic source of truth that the C++ core, the HLS IP and the ARM
SwEngine must match bit-for-bit (timestamp fields excluded).

It is written independently of the C++ core on purpose:
  * the order table stores the order *price* (C++ stores a ladder index),
  * best bid/ask are tracked with an incremental best-index + rescan (C++ uses a
    2-level bitmap and priority encoders),
  * all arithmetic is on exact Python/numba int64 with explicit masking where the
    spec defines 32-bit wrap.

The per-message loop is a plain function over numpy arrays so that numba can JIT it
(>= 200k msgs/s target); without numba it runs as ordinary (slow) Python.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402

try:  # optional JIT
    if os.environ.get("DK1_NO_NUMBA"):
        raise ImportError
    from numba import njit as _njit

    def jit(f):
        return _njit(cache=True)(f)

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover
    def jit(f):
        return f

    HAVE_NUMBA = False

# ------------------------------------------------------------------ compile-time params (SPEC 6.1)
PARAMS = dict(P_MAX=8, SETS=1024, WAYS=4, W=1024, LADDER_TICK=10)

CTRL_STRAT_EN, CTRL_KILL, CTRL_DBG_EN, CTRL_SOFT_RESET = 1, 2, 4, 8

COUNTER_NAMES = (
    "msgs_in", "orders_out", "rejects", "err_unknown_type", "err_bad_product",
    "err_not_found", "err_table_full", "err_dup_id", "err_oow", "err_grid",
    "err_seq_gap", "err_qty_over", "err_anchor", "err_bad_state", "last_seq",
)
(C_MSGS_IN, C_ORDERS_OUT, C_REJECTS, C_UNKNOWN, C_BAD_PRODUCT, C_NOT_FOUND, C_TABLE_FULL,
 C_DUP_ID, C_OOW, C_GRID, C_SEQ_GAP, C_QTY_OVER, C_ANCHOR, C_BAD_STATE, C_LAST_SEQ) = range(15)
N_COUNTERS = len(COUNTER_NAMES)


@dataclass
class Config:
    """Runtime configuration (SPEC 6.7).  Stored as config.json next to each dataset."""
    ctrl: int = CTRL_STRAT_EN | CTRL_DBG_EN
    clip_qty: int = 50
    max_order_qty: int = 100
    max_pos: int = 200
    collar: int = 2000
    min_gap_ns: int = 0
    max_orders: int = 1_000_000
    lockout_ms: int = 0

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        d = json.loads(Path(path).read_text())
        return cls(**{k: int(v) for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")
        # tiny key=value companion for the bare-metal DatasetLoader (sw/app)
        kv = "".join(f"{k}={v}\n" for k, v in asdict(self).items())
        Path(path).with_suffix(".txt").write_text(kv)

    def as_array(self) -> np.ndarray:
        return np.array([self.ctrl, self.clip_qty, self.max_order_qty, self.max_pos, self.collar,
                         self.min_gap_ns, self.max_orders, self.lockout_ms], dtype=np.int64)


# ------------------------------------------------------------------ the kernel
U = np.uint64
M32 = 0xFFFFFFFF


@jit
def _fold_set(oid, sets_mask):
    f = oid ^ (oid >> U(10)) ^ (oid >> U(20)) ^ (oid >> U(30)) ^ (oid >> U(40)) \
        ^ (oid >> U(50)) ^ (oid >> U(60))
    return np.int64(f & sets_mask)


@jit
def _kernel(mtype, mflags, mprod, mseq, mts, moid, mprice, mqty, cfg, prm, st_i, st_u, tab_i, tab_u,
            lq, best, gl_u, gl_i, ord_o, bbo_o, cnt):
    """Process all messages.  Returns (n_ord, n_bbo).

    st_i  int64 [P_MAX, 11]: state, anchor, live, fv, edge, fv_valid, lb_ok, lb_px, ls_ok, ls_px, pos
    st_u  uint64[P_MAX, 1] : gc_ts
    tab_i int64 [WAYS, SETS, 5]: valid, product, side, price, qty   (oow derived from price)
    tab_u uint64[WAYS, SETS]   : order_id
    lq    int64 [P_MAX, 2, W]  : aggregated qty (mod 2^32)
    best  int64 [P_MAX, 2]     : best bid index (-1 none) / best ask index (W none)
    gl_u  uint64[1]            : last_order_ts
    gl_i  int64 [2]            : has_ordered, next_coid
    """
    P_MAX, SETS, WAYS, W, TICK = prm[0], prm[1], prm[2], prm[3], prm[4]
    HALF = W // 2
    sets_mask = U(SETS - 1)
    ctrl, clip_qty, max_order_qty, max_pos = cfg[0], cfg[1], cfg[2], cfg[3]
    collar, min_gap, max_orders, lockout_ms = cfg[4], cfg[5], cfg[6], cfg[7]
    dbg_en = (ctrl & 4) != 0
    lock_ns = U(lockout_ms) * U(1000000)

    # risk globals
    last_order_ts = gl_u[0]
    has_ordered = gl_i[0] != 0
    next_coid = gl_i[1]
    n_ord = 0
    n_bbo = 0

    for k in range(mtype.shape[0]):
        t = mtype[k]
        seq = mseq[k]
        ts = mts[k]
        oid = moid[k]
        price = mprice[k]
        qty = mqty[k]
        side = mflags[k] & 1

        # ---- 1. counters & sequence check
        cnt[C_MSGS_IN] = (cnt[C_MSGS_IN] + 1) & M32
        if cnt[C_LAST_SEQ] != 0 and seq != ((cnt[C_LAST_SEQ] + 1) & M32):
            cnt[C_SEQ_GAP] += 1
        cnt[C_LAST_SEQ] = seq

        # ---- 2. type check
        if not (t == 65 or t == 88 or t == 69 or t == 77 or t == 83 or t == 86 or t == 72):
            cnt[C_UNKNOWN] += 1
            continue
        # ---- 3. product check (not for H)
        if t != 72 and mprod[k] >= P_MAX:
            cnt[C_BAD_PRODUCT] += 1
            continue
        if t == 72:  # 'H'
            if mflags[k] & 2:  # END_OF_SESSION -> 'Z'
                ord_o[n_ord, 0] = 90
                ord_o[n_ord, 1] = 0
                ord_o[n_ord, 2] = 0
                ord_o[n_ord, 3] = seq
                ord_o[n_ord, 4] = cnt[C_MSGS_IN]
                ord_o[n_ord, 5] = cnt[C_ORDERS_OUT]
                ord_o[n_ord, 6] = 0
                ord_o[n_ord, 7] = 0
                n_ord += 1
                if dbg_en:
                    bbo_o[n_bbo, 0] = 90
                    bbo_o[n_bbo, 1] = 0
                    bbo_o[n_bbo, 2] = 0
                    bbo_o[n_bbo, 3] = seq
                    bbo_o[n_bbo, 4] = cnt[C_MSGS_IN]
                    bbo_o[n_bbo, 5] = cnt[C_ORDERS_OUT]
                    bbo_o[n_bbo, 6] = 0
                    bbo_o[n_bbo, 7] = 0
                    n_bbo += 1
            continue

        p = mprod[k]
        ok = True
        upd_p = -1  # ladder level touched (product, side, index) -> best-index maintenance
        upd_s = 0
        upd_i = 0

        # ---- 4. dispatch
        if t == 65:  # 'A' add
            s = _fold_set(oid, sets_mask)
            found = -1
            free = -1
            for w in range(WAYS):  # key = (order_id, product)  (DECISIONS D-10)
                if tab_i[w, s, 0] == 1 and tab_u[w, s] == oid and tab_i[w, s, 1] == p:
                    found = w
            for w in range(WAYS - 1, -1, -1):
                if tab_i[w, s, 0] == 0:
                    free = w
            d = price - st_i[p, 1]
            if found >= 0:
                cnt[C_DUP_ID] += 1
                ok = False
            elif d % TICK != 0:
                cnt[C_GRID] += 1
                ok = False
            elif free < 0:
                cnt[C_TABLE_FULL] += 1
                ok = False
            else:
                tab_i[free, s, 0] = 1
                tab_i[free, s, 1] = p
                tab_i[free, s, 2] = side
                tab_i[free, s, 3] = price
                tab_i[free, s, 4] = qty
                tab_u[free, s] = oid
                i = d // TICK + HALF
                if 0 <= i < W:
                    lq[p, side, i] = (lq[p, side, i] + qty) & M32
                    upd_p, upd_s, upd_i = p, side, i
                else:
                    cnt[C_OOW] += 1
                st_i[p, 2] += 1
        elif t == 88 or t == 69 or t == 77:  # 'X' delete, 'E' execute, 'M' reduce
            s = _fold_set(oid, sets_mask)
            found = -1
            for w in range(WAYS):
                if tab_i[w, s, 0] == 1 and tab_u[w, s] == oid and tab_i[w, s, 1] == p:
                    found = w
            if found < 0:
                cnt[C_NOT_FOUND] += 1
                ok = False
            else:
                ep = tab_i[found, s, 1]
                es = tab_i[found, s, 2]
                eq = tab_i[found, s, 4]
                ei = (tab_i[found, s, 3] - st_i[ep, 1]) // TICK + HALF
                in_win = 0 <= ei < W
                if t == 88:
                    r = eq
                else:
                    r = qty if qty < eq else eq
                    if qty > eq:
                        cnt[C_QTY_OVER] += 1
                tab_i[found, s, 4] = eq - r
                if in_win:
                    lq[ep, es, ei] = (lq[ep, es, ei] - r) & M32
                    upd_p, upd_s, upd_i = ep, es, ei
                if eq - r == 0:
                    tab_i[found, s, 0] = 0
                    st_i[ep, 2] -= 1
        elif t == 83:  # 'S' product state
            ns = qty
            if ns > 3:
                cnt[C_BAD_STATE] += 1
                ok = False
            else:
                if ns == 1 and st_i[p, 0] == 0:
                    if st_i[p, 2] == 0:
                        st_i[p, 1] = price
                        st_u[p, 0] = oid
                    else:
                        cnt[C_ANCHOR] += 1
                if ns == 0:
                    if st_i[p, 2] != 0:
                        cnt[C_ANCHOR] += 1
                    st_i[p, 5] = 0
                    st_i[p, 10] = 0
                    st_i[p, 6] = 0
                    st_i[p, 8] = 0
                st_i[p, 0] = ns
        else:  # 'V' fair value
            st_i[p, 3] = price
            st_i[p, 4] = qty
            st_i[p, 5] = 1
            st_i[p, 6] = 0
            st_i[p, 8] = 0

        # maintain best indices (bid = max non-zero level, ask = min non-zero level)
        if upd_p >= 0:
            if lq[upd_p, upd_s, upd_i] != 0:
                if upd_s == 0:
                    if upd_i > best[upd_p, 0]:
                        best[upd_p, 0] = upd_i
                else:
                    if upd_i < best[upd_p, 1]:
                        best[upd_p, 1] = upd_i
            else:
                if upd_s == 0 and upd_i == best[upd_p, 0]:
                    j = upd_i - 1
                    while j >= 0 and lq[upd_p, 0, j] == 0:
                        j -= 1
                    best[upd_p, 0] = j
                elif upd_s == 1 and upd_i == best[upd_p, 1]:
                    j = upd_i + 1
                    while j < W and lq[upd_p, 1, j] == 0:
                        j += 1
                    best[upd_p, 1] = j

        # ---- 5. BBO of product p
        anchor = st_i[p, 1]
        bi = best[p, 0]
        ai = best[p, 1]
        bid_ok = bi >= 0
        ask_ok = ai < W
        bid_px = anchor + (bi - HALF) * TICK if bid_ok else 0
        bid_q = lq[p, 0, bi] if bid_ok else 0
        ask_px = anchor + (ai - HALF) * TICK if ask_ok else 0
        ask_q = lq[p, 1, ai] if ask_ok else 0
        if dbg_en:
            bbo_o[n_bbo, 0] = 66
            bbo_o[n_bbo, 1] = (1 if bid_ok else 0) | (2 if ask_ok else 0)
            bbo_o[n_bbo, 2] = p
            bbo_o[n_bbo, 3] = seq
            bbo_o[n_bbo, 4] = bid_px
            bbo_o[n_bbo, 5] = bid_q
            bbo_o[n_bbo, 6] = ask_px
            bbo_o[n_bbo, 7] = ask_q
            n_bbo += 1

        # ---- 6. strategy -> risk
        if not ok:
            continue
        gc = st_u[p, 0]
        armed = ((ctrl & 1) != 0 and (ctrl & 2) == 0 and st_i[p, 0] == 1 and st_i[p, 5] == 1
                 and gc > ts and (gc - ts) > lock_ns)
        if not armed:
            continue
        fv = st_i[p, 3]
        edge = st_i[p, 4]
        cand = -1  # 0 buy, 1 sell
        c_px = 0
        c_q = 0
        if ask_ok and ask_px <= fv - edge and not (st_i[p, 6] == 1 and st_i[p, 7] == ask_px):
            cand = 0
            c_px = ask_px
            c_q = ask_q if ask_q < clip_qty else clip_qty
            st_i[p, 6] = 1
            st_i[p, 7] = ask_px
        elif bid_ok and bid_px >= fv + edge and not (st_i[p, 8] == 1 and st_i[p, 9] == bid_px):
            cand = 1
            c_px = bid_px
            c_q = bid_q if bid_q < clip_qty else clip_qty
            st_i[p, 8] = 1
            st_i[p, 9] = bid_px
        if cand < 0:
            continue

        reason = 0
        q = c_q if c_q < max_order_qty else max_order_qty
        room = (max_pos - st_i[p, 10]) if cand == 0 else (max_pos + st_i[p, 10])
        if room < q:
            q = room
        if abs(c_px - fv) > collar:
            reason = 1
        elif q <= 0:
            reason = 2
        elif has_ordered and min_gap != 0 and (ts < last_order_ts or (ts - last_order_ts) < U(min_gap)):
            reason = 3
        elif cnt[C_ORDERS_OUT] >= max_orders:
            reason = 4

        if reason == 0:
            st_i[p, 10] += q if cand == 0 else -q
            cnt[C_ORDERS_OUT] += 1
            last_order_ts = ts
            has_ordered = True
            ord_o[n_ord, 0] = 79  # 'O'
            ord_o[n_ord, 1] = cand | 2
            ord_o[n_ord, 2] = p
            ord_o[n_ord, 3] = seq
            ord_o[n_ord, 4] = next_coid
            ord_o[n_ord, 5] = c_px
            ord_o[n_ord, 6] = q
            ord_o[n_ord, 7] = 0
            next_coid += 1
            n_ord += 1
        else:
            cnt[C_REJECTS] += 1
            if dbg_en:
                ord_o[n_ord, 0] = 82  # 'R'
                ord_o[n_ord, 1] = cand | 2
                ord_o[n_ord, 2] = p
                ord_o[n_ord, 3] = seq
                ord_o[n_ord, 4] = 0
                ord_o[n_ord, 5] = c_px
                ord_o[n_ord, 6] = c_q
                ord_o[n_ord, 7] = reason
                n_ord += 1
    gl_u[0] = last_order_ts
    gl_i[0] = 1 if has_ordered else 0
    gl_i[1] = next_coid
    return n_ord, n_bbo


# ------------------------------------------------------------------ public API
@dataclass
class RunResult:
    orders: np.ndarray     # (N, 32) uint8 records: 'O', 'R', 'Z'
    bbo: np.ndarray        # (M, 32) uint8 records: 'B', 'Z'
    counters: dict
    pos: list = field(default_factory=list)


def _pack(fields: np.ndarray) -> np.ndarray:
    """Pack (N, 8) int64 [type, flags, product, w4, w8, w12, w16, w20] into (N, 32) uint8."""
    n = fields.shape[0]
    rec = np.zeros(n, dtype=P.ORD_DTYPE)
    rec["type"] = fields[:, 0]
    rec["flags"] = fields[:, 1]
    rec["product"] = fields[:, 2]
    rec["trig_seq"] = fields[:, 3] & M32
    rec["coid"] = fields[:, 4] & M32
    rec["price"] = (fields[:, 5] & M32).astype(np.uint32).view(np.int32)
    rec["qty"] = fields[:, 6] & M32
    rec["reason"] = fields[:, 7] & M32
    return rec.view(np.uint8).reshape(n, 32)


class RefModel:
    """Stateful golden model.  Feed it message arrays; state persists across calls."""

    def __init__(self, cfg: Config | None = None, params: dict | None = None):
        self.cfg = cfg or Config()
        self.prm = dict(PARAMS if params is None else params)
        self.reset()

    def reset(self) -> None:
        pm = self.prm
        Pn, S, Wy, Wd = pm["P_MAX"], pm["SETS"], pm["WAYS"], pm["W"]
        self.st_i = np.zeros((Pn, 11), dtype=np.int64)
        self.st_u = np.zeros((Pn, 1), dtype=np.uint64)
        self.tab_i = np.zeros((Wy, S, 5), dtype=np.int64)
        self.tab_u = np.zeros((Wy, S), dtype=np.uint64)
        self.lq = np.zeros((Pn, 2, Wd), dtype=np.int64)
        self.best = np.empty((Pn, 2), dtype=np.int64)
        self.best[:, 0] = -1
        self.best[:, 1] = Wd
        self.cnt = np.zeros(N_COUNTERS, dtype=np.int64)
        self.gl_u = np.zeros(1, dtype=np.uint64)          # last_order_ts
        self.gl_i = np.array([0, 1], dtype=np.int64)      # has_ordered, next_coid

    def run(self, msgs: np.ndarray) -> RunResult:
        n = len(msgs)
        prm = np.array([self.prm[k] for k in ("P_MAX", "SETS", "WAYS", "W", "LADDER_TICK")],
                       dtype=np.int64)
        ord_o = np.zeros((n + 1, 8), dtype=np.int64)
        bbo_o = np.zeros((n + 1, 8), dtype=np.int64)
        n_ord, n_bbo = _kernel(
            msgs["type"].astype(np.int64), msgs["flags"].astype(np.int64),
            msgs["product"].astype(np.int64), msgs["seq"].astype(np.int64),
            msgs["ts_ns"].astype(np.uint64), msgs["order_id"].astype(np.uint64),
            msgs["price"].astype(np.int64), msgs["qty"].astype(np.int64),
            self.cfg.as_array(), prm, self.st_i, self.st_u, self.tab_i, self.tab_u,
            self.lq, self.best, self.gl_u, self.gl_i, ord_o, bbo_o, self.cnt)
        orders = _pack(ord_o[:n_ord])
        bb = bbo_o[:n_bbo]
        bbo = np.zeros(n_bbo, dtype=P.BBO_DTYPE)
        bbo["type"] = bb[:, 0]
        bbo["flags"] = bb[:, 1]
        bbo["product"] = bb[:, 2]
        bbo["seq"] = bb[:, 3] & M32
        bbo["bid_px"] = (bb[:, 4] & M32).astype(np.uint32).view(np.int32)
        bbo["bid_qty"] = bb[:, 5] & M32
        bbo["ask_px"] = (bb[:, 6] & M32).astype(np.uint32).view(np.int32)
        bbo["ask_qty"] = bb[:, 7] & M32
        counters = {name: int(self.cnt[i]) for i, name in enumerate(COUNTER_NAMES)}
        pos = [int(x) for x in self.st_i[:, 10]]
        return RunResult(orders, bbo.view(np.uint8).reshape(n_bbo, 32), counters, pos)


def run(msgs: np.ndarray, cfg: Config | None = None, params: dict | None = None) -> RunResult:
    return RefModel(cfg, params).run(msgs)
