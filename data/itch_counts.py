"""Independent ITCH 5.0 message counter (cross-check for itch2dk.py, SPEC 7.4).

    python data/itch_counts.py <ITCH file(.gz)> [--max-messages 100000]

Walks the file using only the 2-byte length prefix and the type byte, and checks every length
against the ITCH 5.0 table, so it shares no parsing code with itch2dk.py.  To compare against
an existing open-source parser, run it on the same first N messages and diff the per-type
counts (itch2dk.py writes its counts to meta.json).
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter

LENGTHS = {"S": 12, "R": 39, "H": 25, "Y": 20, "L": 26, "V": 35, "W": 12, "K": 28, "J": 35,
           "h": 21, "A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35, "P": 44,
           "Q": 40, "B": 19, "I": 50, "N": 20}


def count(path: str, max_messages: int | None = None) -> dict:
    c: Counter = Counter()
    bad = 0
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        n = 0
        while max_messages is None or n < max_messages:
            h = f.read(2)
            if len(h) < 2:
                break
            ln = (h[0] << 8) | h[1]
            body = f.read(ln)
            if len(body) < ln:
                break
            t = chr(body[0])
            c[t] += 1
            bad += LENGTHS.get(t) != ln
            n += 1
    return {"counts": dict(sorted(c.items())), "length_mismatches": bad, "messages": n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("itch")
    ap.add_argument("--max-messages", type=int, default=100_000)
    a = ap.parse_args()
    print(json.dumps(count(a.itch, a.max_messages), indent=2))
