"""Per-symbol statistics of a Nasdaq ITCH 5.0 day, to choose symbols that fit the IP (SPEC 7.4).

    python data/itch_stats.py <ITCH file(.gz)> [--out itch_stats.json] [--top 40]

One streaming pass (numba kernel over decompressed 64 MB chunks).  For every stock it records
the order-message count, the peak number of resting orders, and how many adds are on whole
cents and inside the IP's price window (+-W/2 ladder ticks of 1 cent around the first add price,
as itch2dk.py anchors it).  The IP has 8 product slots and a 4-way, SETS-set order table shared
by all products, so the symbols for `itch_sample` must have a small combined resting book.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import numba as nb
import numpy as np
from numba import types
from numba.typed import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ref.model import PARAMS  # noqa: E402

NLOC = 65536
# per-locate stat columns
C_MSGS, C_ADDS, C_LIVE, C_PEAK, C_FIRST, C_INWIN, C_SUBCENT, C_SUMPX, C_TOTAL = range(9)


@nb.njit(cache=True)
def _be(buf, o, n):
    v = np.uint64(0)
    for k in range(n):
        v = (v << np.uint64(8)) | np.uint64(buf[o + k])
    return v


@nb.njit(cache=True)
def scan(buf, n, st, names, refs, half):
    """Walk complete messages in buf[:n]; return bytes consumed and message count."""
    o = 0
    cnt = 0
    while o + 2 <= n:
        ln = (np.int64(buf[o]) << 8) | np.int64(buf[o + 1])
        if o + 2 + ln > n:
            break
        b = o + 2
        t = buf[b]
        loc = (np.int64(buf[b + 1]) << 8) | np.int64(buf[b + 2])
        st[loc, C_TOTAL] += 1
        if t == 82:  # 'R' stock directory
            for k in range(8):
                names[loc, k] = buf[b + 11 + k]
        elif t == 65 or t == 70:  # 'A' / 'F'
            ref = _be(buf, b + 11, 8)
            shares = np.int64(_be(buf, b + 20, 4))
            px = np.int64(_be(buf, b + 32, 4))
            st[loc, C_MSGS] += 1
            st[loc, C_ADDS] += 1
            st[loc, C_SUMPX] += px // 100
            if px % 100 != 0:
                st[loc, C_SUBCENT] += 1
            else:
                if st[loc, C_FIRST] == 0:
                    st[loc, C_FIRST] = px // 100
                d = px // 100 - st[loc, C_FIRST]
                if -half <= d < half:
                    st[loc, C_INWIN] += 1
            refs[ref] = (loc << 32) | shares
            st[loc, C_LIVE] += 1
            if st[loc, C_LIVE] > st[loc, C_PEAK]:
                st[loc, C_PEAK] = st[loc, C_LIVE]
        elif t == 69 or t == 67 or t == 88 or t == 68 or t == 85:  # E C X D U
            ref = _be(buf, b + 11, 8)
            st[loc, C_MSGS] += 1
            if ref in refs:
                v = refs[ref]
                left = v & 0xFFFFFFFF
                if t == 68 or t == 85:
                    left = 0
                else:
                    left -= np.int64(_be(buf, b + 19, 4))
                if left <= 0:
                    del refs[ref]
                    st[loc, C_LIVE] -= 1
                else:
                    refs[ref] = (v & ~np.int64(0xFFFFFFFF)) | left
                if t == 85:  # replace: the new order rests
                    nref = _be(buf, b + 19, 8)
                    shares = np.int64(_be(buf, b + 27, 4))
                    refs[nref] = (loc << 32) | shares
                    st[loc, C_LIVE] += 1
                    if st[loc, C_LIVE] > st[loc, C_PEAK]:
                        st[loc, C_PEAK] = st[loc, C_LIVE]
        cnt += 1
        o += 2 + ln
    return o, cnt


def stats(path: Path, chunk: int = 64 << 20) -> tuple[list[dict], int]:
    st = np.zeros((NLOC, 9), dtype=np.int64)
    names = np.zeros((NLOC, 8), dtype=np.uint8)
    refs = Dict.empty(key_type=types.uint64, value_type=types.int64)
    half = PARAMS["W"] // 2
    opener = gzip.open if str(path).endswith(".gz") else open
    total = 0
    rest = b""
    t0 = time.time()
    with opener(path, "rb") as f:
        while True:
            try:
                data = f.read(chunk)
            except EOFError:  # truncated file: use what was read
                data = b""
            if not data:
                break
            buf = np.frombuffer(rest + data, dtype=np.uint8)
            used, c = scan(buf, len(buf), st, names, refs, half)
            total += c
            rest = bytes(buf[used:])
            print(f"\r  {total / 1e6:8.1f} M msgs  {time.time() - t0:6.0f} s", end="", file=sys.stderr)
    print(file=sys.stderr)
    out = []
    for loc in np.nonzero(st[:, C_MSGS])[0]:
        s = st[loc]
        adds = int(s[C_ADDS])
        out.append(dict(
            symbol=bytes(names[loc]).decode("ascii", "replace").strip(), locate=int(loc),
            order_msgs=int(s[C_MSGS]), adds=adds, peak_resting=int(s[C_PEAK]),
            first_px_usd=s[C_FIRST] / 100, mean_add_px_usd=round(s[C_SUMPX] / max(adds, 1) / 100, 2),
            in_window=round(s[C_INWIN] / max(adds, 1), 4), subcent=round(s[C_SUBCENT] / max(adds, 1), 4)))
    out.sort(key=lambda d: -d["order_msgs"])
    return out, total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("itch", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args(argv)
    rows, total = stats(a.itch)
    if a.out:
        a.out.write_text(json.dumps(dict(file=a.itch.name, messages=total, symbols=rows), indent=1) + "\n")
    print(f"{total} messages, {len(rows)} symbols with order messages")
    for r in rows[: a.top]:
        print(f"{r['symbol']:8s} msgs {r['order_msgs']:9d} peak {r['peak_resting']:6d} px {r['mean_add_px_usd']:9.2f} "
              f"win {r['in_window']:.3f} subc {r['subcent']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
