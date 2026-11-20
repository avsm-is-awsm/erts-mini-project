"""Cross-check itch2dk.py against an existing open-source ITCH 5.0 parser (SPEC 7.4, M12).

    python data/itch_xcheck.py <ITCH file(.gz)> --symbols A,B,... [--max-messages 100000]
                               [--date 2019-12-30] [--json out.json]

Three independent decoders read the same first N messages:
  * MeatPy (open-source, `pip install meatpy`, ITCH50MessageReader),
  * data/itch_counts.py (length prefix + type byte only),
  * data/itch2dk.py (the converter under test).
Check 1: message counts per ITCH type are identical for all three.
Check 2: field level.  The dk1 feed for the chosen symbols is rebuilt from MeatPy's decoded
fields (its own byte offsets and integer decoding) with the mapping of SPEC 7.4 / DECISIONS D-32,
and compared record by record with what itch2dk.py writes (type, side, product, ts, order_id,
price, qty).  This catches wrong offsets, endianness or sign errors in the converter.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.itch2dk import convert  # noqa: E402
from data.itch_counts import count  # noqa: E402
from proto import dk1proto as P  # noqa: E402
from ref.model import PARAMS  # noqa: E402


def _int(v) -> int:
    return int.from_bytes(v, "big") if isinstance(v, (bytes, bytearray)) else int(v)


def meatpy_feed(path: Path, symbols: list[str], n: int, date: str):
    """Per-type counts and the expected dk1 records, from MeatPy's decoded messages."""
    from meatpy.itch50 import ITCH50MessageReader

    tick = PARAMS["LADDER_TICK"]
    day0 = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()) * 10**9
    want = {s: i for i, s in enumerate(symbols)}
    loc_slot: dict[int, int] = {}
    book: dict[int, tuple] = {}
    opened = [False] * len(symbols)
    counts: Counter = Counter()
    rec: list[tuple] = []

    def px(p: int) -> int:
        return p // 100 * tick if p % 100 == 0 else p * tick // 100 + 1

    with ITCH50MessageReader(path) as reader:
        for k, m in enumerate(reader):
            if k >= n:
                break
            t = m.type.decode()
            counts[t] += 1
            loc = _int(m.stock_locate)
            ts = day0 + _int(m.timestamp)
            if t == "R":
                s = m.stock.decode().strip()
                if s in want:
                    loc_slot[loc] = want[s]
            elif t == "S" and m.code == b"C":
                break
            elif t in "AF":
                if loc not in loc_slot:
                    continue
                sl = loc_slot[loc]
                side = 0 if m.bsindicator == b"B" else 1
                p = px(_int(m.price))
                if not opened[sl]:
                    rec.append(("S", 0, sl, ts, 2**62, p, P.ST_OPEN))
                    opened[sl] = True
                book[_int(m.order_ref)] = (sl, side, p)
                rec.append(("A", side, sl, ts, _int(m.order_ref), p, _int(m.shares)))
            elif t in "ECXDU":
                ref = _int(m.original_ref if t == "U" else m.order_ref)
                if ref not in book:
                    continue
                sl, side, p = book[ref]
                if t == "E":
                    rec.append(("E", side, sl, ts, ref, p, _int(m.shares)))
                elif t == "C":
                    rec.append(("E", side, sl, ts, ref, px(_int(m.execution_price)), _int(m.shares)))
                elif t == "X":
                    rec.append(("M", side, sl, ts, ref, 0, _int(m.canceled_shares)))
                elif t == "D":
                    rec.append(("X", side, sl, ts, ref, 0, 0))
                    del book[ref]
                else:
                    rec.append(("X", side, sl, ts, ref, 0, 0))
                    del book[ref]
                    np_ = px(_int(m.price))
                    book[_int(m.new_ref)] = (sl, side, np_)
                    rec.append(("A", side, sl, ts, _int(m.new_ref), np_, _int(m.shares)))
            elif t == "H" and loc in loc_slot and opened[loc_slot[loc]]:
                st = m.state
                code = P.ST_OPEN if st == b"T" else P.ST_HALT if st in (b"H", b"P") else None
                if code is not None:
                    rec.append(("S", 0, loc_slot[loc], ts, 0, 0, code))
    return dict(sorted(counts.items())), rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("itch", type=Path)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--max-messages", type=int, default=100_000)
    ap.add_argument("--date", default="2019-12-30")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    syms = a.symbols.split(",")
    n = a.max_messages

    mp_counts, expect = meatpy_feed(a.itch, syms, n, a.date)
    ic = count(str(a.itch), n)
    with tempfile.TemporaryDirectory() as td:
        meta = convert(a.itch, syms, n, Path(td), a.date)
        got = np.fromfile(Path(td) / "feed.bin", dtype=P.MSG_DTYPE)[:-1]  # drop the H(END)

    c_counts = dict(sorted(meta["itch_counts"].items()))
    counts_ok = mp_counts == ic["counts"] == c_counts and ic["length_mismatches"] == 0
    exp = np.zeros(len(expect), dtype=P.MSG_DTYPE)
    if expect:
        col = list(zip(*expect))
        exp["type"] = [ord(x) for x in col[0]]
        exp["flags"] = col[1]
        exp["product"] = col[2]
        exp["ts_ns"] = np.array(col[3], dtype=np.uint64)
        exp["order_id"] = np.array(col[4], dtype=np.uint64)
        exp["price"] = col[5]
        exp["qty"] = np.array(col[6], dtype=np.uint64)
    fields = ("type", "flags", "product", "ts_ns", "order_id", "price", "qty")
    same_len = len(exp) == len(got)
    diffs = {f: int((exp[f] != got[f]).sum()) for f in fields} if same_len else {}
    fields_ok = same_len and not any(diffs.values())
    res = dict(file=a.itch.name, messages=n, symbols=syms,
               counts=dict(meatpy=mp_counts, itch_counts=ic["counts"], itch2dk=c_counts,
                           length_mismatches=ic["length_mismatches"], equal=counts_ok),
               records=dict(expected=len(exp), itch2dk=len(got), field_diffs=diffs,
                            by_type=dict(Counter(chr(t) for t in got["type"])), equal=fields_ok),
               equal=counts_ok and fields_ok)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0 if res["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
