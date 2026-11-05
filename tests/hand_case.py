"""Hand-checked scenario (24 messages, product 0) with expected outputs written out by hand.

Every expected value below was derived manually from SPEC section 6 and docs/DECISIONS.md;
the derivation is in the comments (it is *not* computed by the reference model).
Config: strat_en | dbg_en, CLIP_QTY 50, MAX_ORDER_QTY 100, MAX_POS 200, COLLAR 2000,
MIN_GAP_NS 0, MAX_ORDERS 1e6, LOCKOUT_MS 0.  Anchor 5000 => level i = (px-5000)/10 + 512.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402

A, X, E, M, S, V, H = (ord(c) for c in "AXEMSVH")
BIDF, ASKF = 0, 1

# (type, flags, product, seq, ts, order_id, price, qty)
FEED = [
    (S, 0, 0, 1, 100, 10_000, 5000, 1),      # 1  OPEN, anchor 5000, gc 10 us
    (A, BIDF, 0, 2, 200, 1, 4990, 20),       # 2  bid L511
    (A, ASKF, 0, 3, 300, 2, 5020, 30),       # 3  ask L514
    (V, 0, 0, 4, 400, 0, 5100, 50),          # 4  fv 5100 edge 50 -> BUY 30 @5020 (coid 1)
    (A, ASKF, 0, 5, 500, 3, 5010, 100),      # 5  ask L513 -> BUY 50 @5010 (coid 2), pos 80
    (E, ASKF, 0, 6, 600, 3, 5010, 60),       # 6  oid3 100->40; ask 5010==last_buy -> none
    (X, 0, 0, 7, 700, 3, 0, 0),              # 7  oid3 gone; ask 5020/30 -> BUY 30 (coid 3), pos 110
    (M, 0, 0, 8, 800, 2, 0, 10),             # 8  oid2 30->20; dedup -> none
    (A, BIDF, 0, 9, 900, 4, 5160, 10),       # 9  bid 5160 >= 5150 -> SELL 10 @5160 (coid 4), pos 100
    (A, BIDF, 0, 10, 1000, 5, 5165, 5),      # 10 off grid -> err_grid
    (A, BIDF, 0, 11, 1010, 1, 4990, 5),      # 11 dup oid 1 -> err_dup_id
    (X, 0, 0, 12, 1020, 99, 0, 0),           # 12 unknown oid -> err_not_found
    (A, BIDF, 9, 13, 1030, 50, 5000, 1),     # 13 product 9 -> err_bad_product (no B)
    (ord("Q"), 0, 0, 14, 1040, 0, 0, 0),     # 14 unknown type (no B)
    (V, 0, 0, 15, 1100, 0, 9000, 0),         # 15 fv 9000 -> BUY 20 @5020 rejected COLLAR
    (V, 0, 0, 16, 1200, 0, 5030, 0),         # 16 fv 5030 -> BUY 20 @5020 (coid 5), pos 120
    (A, ASKF, 0, 17, 1300, 6, 5000, 200),    # 17 ask 5000/200 -> BUY 50 (coid 6), pos 170
    (A, ASKF, 0, 18, 1400, 7, 4980, 200),    # 18 ask 4980/200 -> BUY 30 (room 30, coid 7), pos 200
    (A, ASKF, 0, 19, 1500, 8, 4970, 5),      # 19 ask 4970/5 -> BUY 5 rejected POSITION (room 0)
    (S, 0, 0, 20, 1600, 0, 0, 2),            # 20 CLOSED
    (S, 0, 0, 21, 1700, 0, 0, 7),            # 21 bad state code -> err_bad_state
    (A, ASKF, 0, 22, 1800, 9, 10120, 10),    # 22 L1024 -> out of window (err_oow)
    (X, 0, 0, 23, 1900, 9, 0, 0),            # 23 remove the oow order (no ladder change)
    (H, P.FLAG_END, 0, 30, 2000, 0, 0, 0),   # 24 END, seq 30 -> err_seq_gap
]

# ord_out: (type, flags, product, trig_seq, coid, price, qty, reason)
EXP_ORD = [
    ("O", 0 | 2, 0, 4, 1, 5020, 30, 0),
    ("O", 0 | 2, 0, 5, 2, 5010, 50, 0),
    ("O", 0 | 2, 0, 7, 3, 5020, 30, 0),
    ("O", 1 | 2, 0, 9, 4, 5160, 10, 0),
    ("R", 0 | 2, 0, 15, 0, 5020, 20, 1),
    ("O", 0 | 2, 0, 16, 5, 5020, 20, 0),
    ("O", 0 | 2, 0, 17, 6, 5000, 50, 0),
    ("O", 0 | 2, 0, 18, 7, 4980, 30, 0),
    ("R", 0 | 2, 0, 19, 0, 4970, 5, 2),
]
EXP_Z = (30, 24, 7)  # seq, msgs_in, orders_out

# dbg_out 'B': seq -> (flags, bid_px, bid_qty, ask_px, ask_qty)
_B9 = (3, 5160, 10, 5020, 20)
_B19 = (3, 5160, 10, 4970, 5)
EXP_BBO = [
    (1, (0, 0, 0, 0, 0)),
    (2, (1, 4990, 20, 0, 0)),
    (3, (3, 4990, 20, 5020, 30)),
    (4, (3, 4990, 20, 5020, 30)),
    (5, (3, 4990, 20, 5010, 100)),
    (6, (3, 4990, 20, 5010, 40)),
    (7, (3, 4990, 20, 5020, 30)),
    (8, (3, 4990, 20, 5020, 20)),
    (9, _B9), (10, _B9), (11, _B9), (12, _B9), (15, _B9), (16, _B9),
    (17, (3, 5160, 10, 5000, 200)),
    (18, (3, 5160, 10, 4980, 200)),
    (19, _B19), (20, _B19), (21, _B19), (22, _B19), (23, _B19),
]

EXP_COUNTERS = dict(
    msgs_in=24, orders_out=7, rejects=2, err_unknown_type=1, err_bad_product=1,
    err_not_found=1, err_table_full=0, err_dup_id=1, err_oow=1, err_grid=1, err_seq_gap=1,
    err_qty_over=0, err_anchor=0, err_bad_state=1, last_seq=30,
)
EXP_POS0 = 200


def feed_array() -> np.ndarray:
    return np.array(FEED, dtype=[(n, P.MSG_DTYPE[n]) for n in P.MSG_DTYPE.names]).astype(P.MSG_DTYPE)


def expected_orders_bytes() -> np.ndarray:
    recs = [P.pack_ord(ord(t), f, p, s, c, px, q, r) for (t, f, p, s, c, px, q, r) in EXP_ORD]
    recs.append(P.pack_end(*EXP_Z))
    return np.frombuffer(b"".join(recs), dtype=np.uint8).reshape(-1, 32)


def expected_bbo_bytes() -> np.ndarray:
    recs = [P.pack_bbo(f, 0, seq, bp, bq, ap, aq) for seq, (f, bp, bq, ap, aq) in EXP_BBO]
    recs.append(P.pack_end(*EXP_Z))
    # the dbg 'Z' record has the same layout as the ord 'Z' record
    return np.frombuffer(b"".join(recs), dtype=np.uint8).reshape(-1, 32)


def write_dataset(d: Path) -> None:
    """Write the hand case as a dataset folder (feed + config + hand-derived golden files)."""
    import json

    d.mkdir(parents=True, exist_ok=True)
    feed_array().tofile(d / "feed.bin")
    from ref.model import Config

    Config(ctrl=5, clip_qty=50, max_order_qty=100, max_pos=200, collar=2000, min_gap_ns=0,
           max_orders=1_000_000, lockout_ms=0).save(d / "config.json")  # also writes config.txt
    expected_orders_bytes().tofile(d / "golden_orders.bin")
    expected_bbo_bytes().tofile(d / "golden_bbo.bin")
    c = dict(EXP_COUNTERS)
    c["pos"] = [EXP_POS0, 0, 0, 0, 0, 0, 0, 0]
    (d / "golden_counters.json").write_text(json.dumps(c, indent=2) + "\n")


if __name__ == "__main__":
    write_dataset(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/hand_case"))
