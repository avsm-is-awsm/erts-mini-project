"""Nasdaq TotalView-ITCH 5.0 -> dk1 feed converter (SPEC 7.4): real-world traffic test.

    python data/itch2dk.py <ITCH file(.gz)> --symbols AAPL,MSFT,... [--max-messages N]
                           [--out data/out/itch_sample] [--synthetic-fv] [--date 2019-01-30]

Input: a Nasdaq TotalView-ITCH 5.0 sample file (download it yourself from Nasdaq's public sample
data at emi.nasdaq.com/ITCH/; multi-GB .gz, streamed here; never commit it).
Each ITCH message is preceded by a 2-byte big-endian length.

Mapping (documented rescaling; the test is about parsing and book mechanics, not prices):
  A/F add         -> 'A'      E/C executed -> 'E'      X cancel (partial) -> 'M'
  D delete        -> 'X'      U replace    -> 'X'(old) + 'A'(new, same side)
  H trading action 'T' -> S(OPEN), 'H'/'P' -> S(HALT); first add of a symbol -> S(OPEN, anchor)
  S system event 'C' (end of messages) / end of input -> H(END)
  price: ITCH Price(4) = USD * 1e4; 1 US cent = LADDER_TICK units -> price = itch / 100 * 10
  qty: shares; product slot: index of the symbol in --symbols (<= 8)
No 'V' messages (strategy off) unless --synthetic-fv (V = last trade price, edge 1 cent / tick,
every --fv-every messages per symbol; ctrl = strat_en | dbg_en).
"""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from proto import dk1proto as P  # noqa: E402
from ref.model import PARAMS, Config  # noqa: E402

# ITCH 5.0 message lengths (without the 2-byte length prefix), from the specification
ITCH_LEN = {
    "S": 12, "R": 39, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12, "K": 28, "J": 35, "h": 21,
    "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35, "P": 44, "Q": 40, "B": 19,
    "I": 50, "N": 20,
}


def _ts(b: bytes) -> int:  # 6-byte big-endian ns since midnight
    return int.from_bytes(b, "big")


def iter_itch(path: Path, max_messages: int | None = None):
    """Yield raw ITCH messages (bytes, starting with the type byte)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as f:
        n = 0
        while max_messages is None or n < max_messages:
            hdr = f.read(2)
            if len(hdr) < 2:
                return
            (ln,) = struct.unpack(">H", hdr)
            body = f.read(ln)
            if len(body) < ln:
                return
            n += 1
            yield body


def convert(path: Path, symbols: list[str], max_messages: int | None, out: Path, date: str,
            synthetic_fv: bool = False, fv_every: int = 200) -> dict:
    if not 1 <= len(symbols) <= PARAMS["P_MAX"]:
        raise SystemExit(f"--symbols needs 1..{PARAMS['P_MAX']} symbols")
    tick = PARAMS["LADDER_TICK"]
    day0 = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()) * 10**9
    sym_slot = {s.encode().ljust(8): i for i, s in enumerate(symbols)}
    locate_slot: dict[int, int] = {}
    orders: dict[int, list] = {}        # ref -> [slot, side, price]
    opened = [False] * len(symbols)
    last_px = [0] * len(symbols)
    n_fv = [0] * len(symbols)
    rows: list[tuple] = []
    counts: dict[str, int] = {}
    skipped = {"other_symbol": 0, "unknown_ref": 0}

    def emit(t, slot, side, ts, oid, px, q):
        rows.append((ord(t), side & 1, slot, ts, oid, px, q))

    def px_of(itch_price: int) -> int:
        return itch_price // 100 * tick if itch_price % 100 == 0 else itch_price * tick // 100 + 1  # off grid

    for m in iter_itch(path, max_messages):
        t = chr(m[0])
        counts[t] = counts.get(t, 0) + 1
        if len(m) != ITCH_LEN.get(t, len(m)):
            raise ValueError(f"bad length {len(m)} for ITCH type {t}")
        locate = int.from_bytes(m[1:3], "big")
        ts = day0 + _ts(m[5:11])
        if t == "R":
            stock = m[11:19]
            if stock in sym_slot:
                locate_slot[locate] = sym_slot[stock]
        elif t == "S":
            if m[11:12] == b"C":
                break
        elif t in ("A", "F"):
            if locate not in locate_slot:
                skipped["other_symbol"] += 1
                continue
            slot = locate_slot[locate]
            ref, side_c, shares = struct.unpack(">QcI", m[11:24])
            price = px_of(int.from_bytes(m[32:36], "big"))
            side = 0 if side_c == b"B" else 1
            if not opened[slot]:
                emit("S", slot, 0, ts, 2**62, px_of(int.from_bytes(m[32:36], "big")), P.ST_OPEN)
                opened[slot] = True
            orders[ref] = [slot, side, price]
            emit("A", slot, side, ts, ref, price, shares)
        elif t in ("E", "C", "X", "D", "U"):
            ref = int.from_bytes(m[11:19], "big")
            o = orders.get(ref)
            if o is None:
                skipped["unknown_ref"] += 1
                continue
            slot, side, price = o
            if t == "E":
                (q,) = struct.unpack(">I", m[19:23])
                emit("E", slot, side, ts, ref, price, q)
                last_px[slot] = price
            elif t == "C":
                (q,) = struct.unpack(">I", m[19:23])
                xp = px_of(int.from_bytes(m[32:36], "big"))
                emit("E", slot, side, ts, ref, xp, q)
                last_px[slot] = xp
            elif t == "X":
                (q,) = struct.unpack(">I", m[19:23])
                emit("M", slot, side, ts, ref, 0, q)
            elif t == "D":
                emit("X", slot, side, ts, ref, 0, 0)
                orders.pop(ref, None)
            else:  # U
                new_ref, shares, itch_px = struct.unpack(">QII", m[19:35])
                emit("X", slot, side, ts, ref, 0, 0)
                orders.pop(ref, None)
                npx = px_of(itch_px)
                orders[new_ref] = [slot, side, npx]
                emit("A", slot, side, ts, new_ref, npx, shares)
            if synthetic_fv and last_px[slot]:
                n_fv[slot] += 1
                if n_fv[slot] % fv_every == 0:
                    emit("V", slot, 0, ts, 0, last_px[slot], tick)
        elif t == "H":
            if locate in locate_slot:
                state = m[19:20]
                code = P.ST_OPEN if state == b"T" else P.ST_HALT if state in (b"H", b"P") else None
                if code is not None and opened[locate_slot[locate]]:
                    emit("S", locate_slot[locate], 0, ts, 0, 0, code)

    n = len(rows) + 1
    msgs = np.zeros(n, dtype=P.MSG_DTYPE)
    if rows:
        # column-wise: order references are full uint64 and do not fit an int64 matrix
        col = list(zip(*rows))
        msgs["type"][:-1] = col[0]
        msgs["flags"][:-1] = col[1]
        msgs["product"][:-1] = col[2]
        msgs["ts_ns"][:-1] = np.array(col[3], dtype=np.uint64)
        msgs["order_id"][:-1] = np.array(col[4], dtype=np.uint64)
        msgs["price"][:-1] = np.clip(np.array(col[5], dtype=np.int64), -(2**31), 2**31 - 1)
        msgs["qty"][:-1] = np.array(col[6], dtype=np.uint64) & np.uint64(0xFFFFFFFF)
    msgs["type"][-1] = P.T_HB
    msgs["flags"][-1] = P.FLAG_END
    msgs["ts_ns"][-1] = (msgs["ts_ns"][-2] + 1) if n > 1 else day0
    msgs["seq"] = np.arange(1, n + 1, dtype=np.uint32)
    out.mkdir(parents=True, exist_ok=True)
    msgs.tofile(out / "feed.bin")
    P.msgs_to_csv(msgs, out / "feed.csv")
    Config(ctrl=(5 if synthetic_fv else 4)).save(out / "config.json")
    meta = dict(source=str(path.name), symbols=symbols, itch_counts=counts, skipped=skipped,
                n_msgs=int(n), date=date, synthetic_fv=synthetic_fv,
                price_scaling="1 US cent = LADDER_TICK units (price = ITCH/100*10)")
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("itch", type=Path)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--max-messages", type=int, default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "out" / "itch_sample")
    ap.add_argument("--date", default="2019-01-30")
    ap.add_argument("--synthetic-fv", action="store_true")
    a = ap.parse_args(argv)
    meta = convert(a.itch, a.symbols.split(","), a.max_messages, a.out, a.date, a.synthetic_fv)
    print(json.dumps({k: meta[k] for k in ("n_msgs", "itch_counts", "skipped")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
