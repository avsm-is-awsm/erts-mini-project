"""Random cross-check: ref/model.py (Python) vs hls/src/core/engine.hpp (C++) on >= 10k
independent adversarial sequences, for the default parameters and for a tiny geometry
(SETS=4, W=64) that makes set-full, collisions and ladder edges frequent.

Environment: DK1_XCHECK_N overrides the number of sequences per geometry (default 6000,
i.e. 12000 sequences in total).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402
from ref.model import COUNTER_NAMES, PARAMS, Config, RefModel  # noqa: E402
from tests import cxx  # noqa: E402
from tests.randseq import make_batch  # noqa: E402

N_SEQ = int(os.environ.get("DK1_XCHECK_N", "6000"))

GEOMETRIES = {
    "default": (dict(PARAMS), []),
    "tiny": (dict(P_MAX=8, SETS=4, WAYS=4, W=64, LADDER_TICK=10),
             ["T2T_SETS=4", "T2T_W=64"]),
}


def _cfg(row) -> Config:
    v = [int(x) for x in row[1:]]
    mp = v[3] - (1 << 32) if v[3] >= (1 << 31) else v[3]
    return Config(ctrl=v[0], clip_qty=v[1], max_order_qty=v[2], max_pos=mp, collar=v[4],
                  min_gap_ns=v[5], max_orders=v[6], lockout_ms=v[7])


@pytest.mark.skipif(cxx.find_gxx() is None, reason="g++ not available")
@pytest.mark.parametrize("geom", list(GEOMETRIES))
def test_xcheck(geom, tmp_path):
    prm, defines = GEOMETRIES[geom]
    seed = 1234 if geom == "default" else 98765
    seqs, idx = make_batch(seed, N_SEQ, prm)
    np.concatenate(seqs).tofile(tmp_path / "xseq.bin")
    idx.tofile(tmp_path / "xidx.bin")

    binary = cxx.build(f"xcheck_{geom}", ["hls/tb/xcheck.cpp"], defines)
    cxx.run(binary, tmp_path)
    c_ord = P.read_out(tmp_path / "xout_ord.bin")
    c_dbg = P.read_out(tmp_path / "xout_dbg.bin")
    c_cnt = np.fromfile(tmp_path / "xout_cnt.bin", dtype=np.int32).reshape(N_SEQ, -1)

    o_off = d_off = 0
    n_msgs = 0
    cover = {k: 0 for k in COUNTER_NAMES}
    reasons = set()
    sides = set()
    for s, (msgs, row) in enumerate(zip(seqs, idx)):
        res = RefModel(_cfg(row), prm).run(msgs)
        n_msgs += len(msgs)
        n_ord, n_dbg = int(c_cnt[s, 0]), int(c_cnt[s, 1])
        ctx = f"geometry={geom} seed={seed} sequence={s}"
        assert n_ord == len(res.orders), f"{ctx}: ord count C++={n_ord} py={len(res.orders)}"
        assert n_dbg == len(res.bbo), f"{ctx}: dbg count C++={n_dbg} py={len(res.bbo)}"
        got_o = P.golden_mask(c_ord[o_off:o_off + n_ord])
        got_d = P.golden_mask(c_dbg[d_off:d_off + n_dbg])
        if not np.array_equal(got_o, P.golden_mask(res.orders)):
            bad = int(np.nonzero((got_o != P.golden_mask(res.orders)).any(axis=1))[0][0])
            pytest.fail(f"{ctx}: ord record {bad}\n C++ {got_o[bad].tobytes().hex()}\n"
                        f" py  {P.golden_mask(res.orders)[bad].tobytes().hex()}")
        if not np.array_equal(got_d, P.golden_mask(res.bbo)):
            bad = int(np.nonzero((got_d != P.golden_mask(res.bbo)).any(axis=1))[0][0])
            pytest.fail(f"{ctx}: dbg record {bad}\n C++ {got_d[bad].tobytes().hex()}\n"
                        f" py  {P.golden_mask(res.bbo)[bad].tobytes().hex()}")
        py_cnt = [res.counters[k] & 0xFFFFFFFF for k in COUNTER_NAMES]
        c_c = [int(x) & 0xFFFFFFFF for x in c_cnt[s, 2:2 + len(COUNTER_NAMES)]]
        assert c_c == py_cnt, f"{ctx}: counters C++={c_c} py={py_cnt}"
        assert [int(x) for x in c_cnt[s, 2 + len(COUNTER_NAMES):]] == res.pos, f"{ctx}: positions"
        for k in COUNTER_NAMES:
            cover[k] += res.counters[k]
        ordv = P.as_ord(res.orders) if len(res.orders) else []
        for r in ordv:
            if r["type"] == P.O_REJECT:
                reasons.add(int(r["reason"]))
            if r["type"] == P.O_ORDER:
                sides.add(int(r["flags"]) & 1)
        o_off += n_ord
        d_off += n_dbg
    assert o_off == len(c_ord) and d_off == len(c_dbg)
    # coverage: every error counter, every reject reason and both order sides were hit
    missing = [k for k, v in cover.items() if v == 0]
    assert not missing, f"random sequences never hit: {missing}"
    assert reasons == {1, 2, 3, 4}, f"reject reasons hit: {sorted(reasons)}"
    assert sides == {0, 1}
    print(f"coverage[{geom}]: " + ", ".join(f"{k}={v}" for k, v in cover.items()))
    print(f"xcheck[{geom}]: {N_SEQ} sequences, {n_msgs} messages, {o_off} ord / {d_off} dbg records equal")
