"""dk1proto: Python codec for the dk1-t2t wire format (SPEC section 5, proto/PROTOCOL.md).

Every record is exactly 32 bytes, little-endian. On the 64-bit AXI4-Stream a record
is 4 beats; beat k carries bytes 8k..8k+7 (byte i of a beat is bits [8i+7:8i]).

Bulk files are read/written with numpy structured dtypes; single records with
pack_*/unpack_* helpers. The C++ mirror is proto/dk1proto.hpp.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REC_SIZE = 32

# ---------------------------------------------------------------- message types
T_ADD = ord("A")
T_DEL = ord("X")
T_EXEC = ord("E")
T_RED = ord("M")
T_STATE = ord("S")
T_FV = ord("V")
T_HB = ord("H")
KNOWN_TYPES = (T_ADD, T_DEL, T_EXEC, T_RED, T_STATE, T_FV, T_HB)
BOOK_TYPES = (T_ADD, T_DEL, T_EXEC, T_RED, T_STATE, T_FV)  # types that produce a 'B' record

O_ORDER = ord("O")
O_REJECT = ord("R")
O_END = ord("Z")
O_BBO = ord("B")

FLAG_SIDE = 0x01  # 0 = bid/buy, 1 = ask/sell
FLAG_BIT1 = 0x02  # type specific: H -> END_OF_SESSION; O/R -> IOC
FLAG_END = FLAG_BIT1
FLAG_IOC = FLAG_BIT1

BID, ASK = 0, 1
BUY, SELL = 0, 1

ST_PREOPEN, ST_OPEN, ST_CLOSED, ST_HALT = 0, 1, 2, 3
STATE_NAMES = {0: "PREOPEN", 1: "OPEN", 2: "CLOSED", 3: "HALT"}

# reject reason codes (SPEC 6.6)
RJ_COLLAR, RJ_POSITION, RJ_RATE, RJ_MAX_ORDERS = 1, 2, 3, 4
REJECT_NAMES = {1: "COLLAR", 2: "POSITION", 3: "RATE", 4: "MAX_ORDERS"}

# ---------------------------------------------------------------- dtypes
MSG_DTYPE = np.dtype(
    [
        ("type", "u1"),
        ("flags", "u1"),
        ("product", "<u2"),
        ("seq", "<u4"),
        ("ts_ns", "<u8"),
        ("order_id", "<u8"),
        ("price", "<i4"),
        ("qty", "<u4"),
    ]
)

# 'O' / 'R' records (ord_out)
ORD_DTYPE = np.dtype(
    [
        ("type", "u1"),
        ("flags", "u1"),
        ("product", "<u2"),
        ("trig_seq", "<u4"),
        ("coid", "<u4"),
        ("price", "<i4"),
        ("qty", "<u4"),
        ("reason", "<u4"),
        ("t_in", "<u4"),
        ("t_out", "<u4"),
    ]
)

# 'Z' record (same 32 bytes, different field meaning)
END_DTYPE = np.dtype(
    [
        ("type", "u1"),
        ("flags", "u1"),
        ("product", "<u2"),
        ("seq", "<u4"),
        ("msgs_in", "<u4"),
        ("orders_out", "<u4"),
        ("first_t_in", "<u4"),
        ("last_t_in", "<u4"),
        ("t_in", "<u4"),
        ("t_out", "<u4"),
    ]
)

# 'B' records (dbg_out)
BBO_DTYPE = np.dtype(
    [
        ("type", "u1"),
        ("flags", "u1"),
        ("product", "<u2"),
        ("seq", "<u4"),
        ("bid_px", "<i4"),
        ("bid_qty", "<u4"),
        ("ask_px", "<i4"),
        ("ask_qty", "<u4"),
        ("t_in", "<u4"),
        ("t_out", "<u4"),
    ]
)

for _dt in (MSG_DTYPE, ORD_DTYPE, END_DTYPE, BBO_DTYPE):
    assert _dt.itemsize == REC_SIZE

_MSG_STRUCT = struct.Struct("<BBHIQQiI")
_ORD_STRUCT = struct.Struct("<BBHIIiIIII")
_END_STRUCT = struct.Struct("<BBHIIIIIII")
_BBO_STRUCT = struct.Struct("<BBHIiIiIII")


# ---------------------------------------------------------------- single records
@dataclass(frozen=True)
class Msg:
    type: int
    flags: int = 0
    product: int = 0
    seq: int = 0
    ts_ns: int = 0
    order_id: int = 0
    price: int = 0
    qty: int = 0

    @property
    def side(self) -> int:
        return self.flags & FLAG_SIDE


def pack_msg(m: Msg) -> bytes:
    return _MSG_STRUCT.pack(m.type, m.flags, m.product, m.seq, m.ts_ns, m.order_id, m.price, m.qty)


def unpack_msg(b: bytes) -> Msg:
    return Msg(*_MSG_STRUCT.unpack(b))


def pack_ord(type_, flags, product, trig_seq, coid, price, qty, reason, t_in=0, t_out=0) -> bytes:
    return _ORD_STRUCT.pack(type_, flags, product, trig_seq, coid, price, qty, reason, t_in, t_out)


def unpack_ord(b: bytes) -> tuple:
    return _ORD_STRUCT.unpack(b)


def pack_end(seq, msgs_in, orders_out, first_t_in=0, last_t_in=0, t_in=0, t_out=0) -> bytes:
    return _END_STRUCT.pack(O_END, 0, 0, seq, msgs_in, orders_out, first_t_in, last_t_in, t_in, t_out)


def unpack_end(b: bytes) -> tuple:
    return _END_STRUCT.unpack(b)


def pack_bbo(flags, product, seq, bid_px, bid_qty, ask_px, ask_qty, t_in=0, t_out=0) -> bytes:
    return _BBO_STRUCT.pack(O_BBO, flags, product, seq, bid_px, bid_qty, ask_px, ask_qty, t_in, t_out)


def unpack_bbo(b: bytes) -> tuple:
    return _BBO_STRUCT.unpack(b)


def to_beats(rec: bytes) -> list[int]:
    """Split a 32-byte record into the 4 AXIS beats (uint64, beat k = bytes 8k..8k+7)."""
    assert len(rec) == REC_SIZE
    return [int.from_bytes(rec[8 * k : 8 * k + 8], "little") for k in range(4)]


def from_beats(beats: list[int]) -> bytes:
    assert len(beats) == 4
    return b"".join(int(b).to_bytes(8, "little") for b in beats)


# ---------------------------------------------------------------- bulk files
def read_msgs(path: str | Path) -> np.ndarray:
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size % REC_SIZE:
        raise ValueError(f"{path}: size {raw.size} is not a multiple of {REC_SIZE}")
    return raw.view(MSG_DTYPE)


def write_records(path: str | Path, arr: np.ndarray) -> None:
    np.ascontiguousarray(arr).tofile(path)


def read_out(path: str | Path) -> np.ndarray:
    """Read an ord_out / dbg_out file as raw (N, 32) uint8 records."""
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size % REC_SIZE:
        raise ValueError(f"{path}: size {raw.size} is not a multiple of {REC_SIZE}")
    return raw.reshape(-1, REC_SIZE)


def as_ord(recs: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(recs).view(ORD_DTYPE).reshape(-1)


def as_end(recs: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(recs).view(END_DTYPE).reshape(-1)


def as_bbo(recs: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(recs).view(BBO_DTYPE).reshape(-1)


def golden_mask(recs: np.ndarray) -> np.ndarray:
    """Return a copy with the timestamp bytes zeroed (SPEC 5.3 + DECISIONS D-07).

    Bytes 24..31 (t_in, t_out) are masked for every record; for 'Z' records bytes
    16..23 (first_t_in, last_t_in) are masked as well.
    """
    out = np.array(recs, dtype=np.uint8, copy=True).reshape(-1, REC_SIZE)
    out[:, 24:32] = 0
    z = out[:, 0] == O_END
    out[z, 16:24] = 0
    return out


def msgs_to_csv(msgs: np.ndarray, path: str | Path, max_rows: int = 1_000_000) -> None:
    """Human-readable dump of a feed (first max_rows rows)."""
    n = min(len(msgs), max_rows)
    with open(path, "w", newline="") as f:
        f.write("seq,type,side,flags,product,ts_ns,order_id,price,qty\n")
        m = msgs[:n]
        types = m["type"]
        for i in range(n):
            f.write(
                f"{m['seq'][i]},{chr(types[i]) if 32 <= types[i] < 127 else types[i]},"
                f"{m['flags'][i] & 1},{m['flags'][i]},{m['product'][i]},{m['ts_ns'][i]},"
                f"{m['order_id'][i]},{m['price'][i]},{m['qty'][i]}\n"
            )
