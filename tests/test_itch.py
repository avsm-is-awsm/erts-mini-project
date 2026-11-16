"""ITCH 5.0 converter tests on a synthetic ITCH byte stream written from the spec layouts."""
from __future__ import annotations

import gzip
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.itch2dk import convert  # noqa: E402
from data.itch_counts import count  # noqa: E402
from proto import dk1proto as P  # noqa: E402
from ref.model import Config, run  # noqa: E402


def hdr(t: str, locate: int, ts: int) -> bytes:
    return t.encode() + struct.pack(">HH", locate, 0) + ts.to_bytes(6, "big")


def msg(body: bytes) -> bytes:
    return struct.pack(">H", len(body)) + body


def stock_dir(locate, sym):
    return hdr("R", locate, 1) + sym.encode().ljust(8) + b"Q" + b"N" + struct.pack(">I", 100) + \
        b"N" + b"C" + b"Z " + b"P" + b"N" + b"N" + b"1" + b"N" + struct.pack(">I", 0) + b"N"


def add(locate, ts, ref, side, shares, sym, price, mpid=False):
    b = hdr("F" if mpid else "A", locate, ts) + struct.pack(">Qc", ref, side) + struct.pack(">I", shares) + \
        sym.encode().ljust(8) + struct.pack(">I", price)
    return b + (b"MPID" if mpid else b"")


def build_file(path: Path):
    m = [stock_dir(1, "AAPL"), stock_dir(2, "MSFT"), stock_dir(3, "IBM")]
    m.append(hdr("S", 0, 2) + b"O")
    m.append(add(1, 10, 100, b"B", 200, "AAPL", 1500000))          # $150.00
    m.append(add(1, 11, 101, b"S", 300, "AAPL", 1500100, True))    # $150.01
    m.append(add(2, 12, 200, b"B", 50, "MSFT", 1000000))
    m.append(add(3, 13, 300, b"B", 50, "IBM", 1200000))           # not selected
    m.append(hdr("E", 1, 20) + struct.pack(">QIQ", 101, 100, 7))  # partial execute
    m.append(hdr("C", 1, 21) + struct.pack(">QIQ", 101, 50, 8) + b"Y" + struct.pack(">I", 1500100))
    m.append(hdr("X", 1, 22) + struct.pack(">QI", 100, 20))       # partial cancel -> M
    m.append(hdr("U", 1, 23) + struct.pack(">QQII", 100, 102, 180, 1499900))  # replace
    m.append(hdr("D", 2, 24) + struct.pack(">Q", 200))
    m.append(hdr("H", 1, 25) + b"AAPL    " + b"H" + b" " + b"    ")
    m.append(hdr("H", 1, 26) + b"AAPL    " + b"T" + b" " + b"    ")
    m.append(hdr("P", 1, 27) + b"\0" * 33)                        # ignored trade message
    m.append(add(2, 28, 2**64 - 5, b"S", 7, "MSFT", 1000100))      # full 64-bit order reference
    m.append(hdr("S", 0, 30) + b"C")                               # end of messages
    m.append(add(1, 31, 103, b"B", 1, "AAPL", 1500000))            # after end: not converted
    with gzip.open(path, "wb") as f:
        for x in m:
            f.write(msg(x))


def test_itch_conversion(tmp_path):
    src = tmp_path / "sample.itch.gz"
    build_file(src)
    c = count(str(src), None)
    assert c["length_mismatches"] == 0 and c["messages"] == 19
    meta = convert(src, ["AAPL", "MSFT"], None, tmp_path / "ds", "2019-01-30")
    msgs = P.read_msgs(tmp_path / "ds" / "feed.bin")
    types = "".join(chr(t) for t in msgs["type"])
    # S(open AAPL) A A S(open MSFT) A E E M X A X S(HALT) S(OPEN) H(END)
    assert types == "SAASAEEMXAXSSAH", types
    assert int(msgs["order_id"][-2]) == 2**64 - 5
    assert (np.diff(msgs["seq"].astype(np.int64)) == 1).all()
    a = msgs[msgs["type"] == P.T_ADD]
    assert list(a["price"][:2]) == [150000, 150010]               # 1 cent = 10 units
    assert a["flags"][1] & 1 == 1 and a["product"][2] == 1        # sell side, MSFT -> slot 1
    assert meta["skipped"]["other_symbol"] == 1                   # IBM
    # converted counts per type agree with the independent counter (up to the END cut)
    for t in ("A", "F", "E", "C", "X", "U", "D", "H", "R"):
        assert meta["itch_counts"].get(t, 0) == c["counts"].get(t, 0) - (1 if t == "A" else 0)
    # the reference model processes it without unexpected errors
    r = run(msgs, Config.load(tmp_path / "ds" / "config.json"))
    errs = {k: v for k, v in r.counters.items() if k.startswith("err") and v}
    assert errs == {}, errs
    assert r.counters["msgs_in"] == len(msgs)
