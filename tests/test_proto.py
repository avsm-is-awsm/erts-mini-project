"""Protocol tests (M1): Python codec self-consistency and Python <-> C++ round trip."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402
from tests import cxx  # noqa: E402


def random_msgs(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    raw = rng.integers(0, 256, size=n * 32, dtype=np.uint8)
    m = raw.view(P.MSG_DTYPE).copy()
    m["type"][: n // 2] = rng.choice(np.frombuffer(b"AXEMSVH", dtype=np.uint8), n // 2)
    return m


def test_sizes():
    for dt in (P.MSG_DTYPE, P.ORD_DTYPE, P.END_DTYPE, P.BBO_DTYPE):
        assert dt.itemsize == 32


def test_offsets_match_spec():
    o = P.MSG_DTYPE.fields
    assert [o[k][1] for k in ("type", "flags", "product", "seq", "ts_ns", "order_id", "price", "qty")] == \
        [0, 1, 2, 4, 8, 16, 24, 28]
    o = P.ORD_DTYPE.fields
    assert [o[k][1] for k in ("trig_seq", "coid", "price", "qty", "reason", "t_in", "t_out")] == \
        [4, 8, 12, 16, 20, 24, 28]
    o = P.BBO_DTYPE.fields
    assert [o[k][1] for k in ("seq", "bid_px", "bid_qty", "ask_px", "ask_qty", "t_in")] == \
        [4, 8, 12, 16, 20, 24]


def test_struct_vs_dtype():
    m = P.Msg(type=P.T_ADD, flags=1, product=3, seq=42, ts_ns=2**63 + 5, order_id=2**64 - 1,
              price=-123456, qty=4_000_000_000)
    b = P.pack_msg(m)
    arr = np.frombuffer(b, dtype=P.MSG_DTYPE)
    assert arr["price"][0] == -123456 and arr["order_id"][0] == 2**64 - 1
    assert P.unpack_msg(b) == m
    assert P.from_beats(P.to_beats(b)) == b
    # beat layout: byte i of beat k = bits [8i+7:8i]
    beats = P.to_beats(b)
    assert beats[0] & 0xFF == P.T_ADD and (beats[0] >> 32) == 42
    assert beats[3] & 0xFFFFFFFF == (-123456) & 0xFFFFFFFF and beats[3] >> 32 == 4_000_000_000


def test_out_records():
    r = P.pack_ord(P.O_ORDER, 3, 2, 99, 7, -5, 30, 0, 11, 22)
    assert P.unpack_ord(r) == (P.O_ORDER, 3, 2, 99, 7, -5, 30, 0, 11, 22)
    z = P.pack_end(1000, 1000, 17, 5, 6, 7, 8)
    assert P.as_end(np.frombuffer(z, np.uint8))[0]["orders_out"] == 17
    bb = P.pack_bbo(3, 1, 5, -100, 1, 200, 2)
    assert P.unpack_bbo(bb)[4] == -100
    mask = P.golden_mask(np.frombuffer(z + r, np.uint8).reshape(2, 32))
    assert (mask[0, 16:] == 0).all() and (mask[1, 24:] == 0).all() and mask[1, 16] == 30


@pytest.mark.skipif(cxx.find_gxx() is None, reason="g++ not available")
@pytest.mark.parametrize("kind", ["msg", "ord", "end", "bbo"])
def test_cpp_roundtrip(kind, tmp_path):
    binary = cxx.build("proto_roundtrip", ["tests/proto_roundtrip.cpp"])
    src = tmp_path / "in.bin"
    dst = tmp_path / "out.bin"
    rng = np.random.default_rng(11)
    raw = rng.integers(0, 256, size=20000 * 32, dtype=np.uint8)
    if kind == "msg":
        random_msgs(20000).tofile(src)
    else:
        raw.tofile(src)
    cxx.run(binary, kind, src, dst)
    assert src.read_bytes() == dst.read_bytes()


@pytest.mark.skipif(cxx.find_gxx() is None, reason="g++ not available")
def test_cpp_roundtrip_hand_case(tmp_path):
    """Real records (orders, BBO, Z) written by Python survive a C++ decode/encode."""
    from tests import hand_case as hc

    binary = cxx.build("proto_roundtrip", ["tests/proto_roundtrip.cpp"])
    for name, arr in (("ord", hc.expected_orders_bytes()), ("bbo", hc.expected_bbo_bytes()),
                      ("feed", hc.feed_array().view(np.uint8).reshape(-1, 32))):
        src, dst = tmp_path / f"{name}.bin", tmp_path / f"{name}_rt.bin"
        arr.tofile(src)
        cxx.run(binary, "auto", src, dst)
        assert src.read_bytes() == dst.read_bytes(), name
