"""Golden outputs for a dataset folder.

    python ref/run_ref.py data/out/<name> [--max-msgs N]

Writes golden_orders.bin ('O'/'R'/'Z' per config dbg_en), golden_bbo.bin ('B'/'Z',
only if dbg_en), golden_counters.json (counters + final positions).  With --max-msgs the
first N messages are used and files get the suffix _N (HLS cosim subsets).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402
from ref.model import Config, run  # noqa: E402


def run_dataset(d: Path, max_msgs: int | None = None, quiet: bool = False) -> dict:
    msgs = P.read_msgs(d / "feed.bin")
    cfg = Config.load(d / "config.json") if (d / "config.json").exists() else Config()
    sfx = ""
    if max_msgs is not None and max_msgs < len(msgs):
        msgs = msgs[:max_msgs]
        sfx = f"_{max_msgs}"
    elif max_msgs is not None:
        sfx = f"_{max_msgs}"
    t0 = time.perf_counter()
    res = run(msgs, cfg)
    dt = time.perf_counter() - t0
    res.orders.tofile(d / f"golden_orders{sfx}.bin")
    res.bbo.tofile(d / f"golden_bbo{sfx}.bin")
    out = dict(res.counters)
    out["pos"] = res.pos
    (d / f"golden_counters{sfx}.json").write_text(json.dumps(out, indent=2) + "\n")
    rate = len(msgs) / dt if dt > 0 else float("inf")
    if not quiet:
        kinds = {chr(k): int((res.orders[:, 0] == k).sum()) for k in (79, 82, 90)}
        print(f"{d.name}: {len(msgs)} msgs in {dt:.2f}s ({rate/1e6:.2f} M msg/s) -> "
              f"O={kinds['O']} R={kinds['R']} Z={kinds['Z']} B={len(res.bbo)} "
              f"rejects={res.counters['rejects']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--max-msgs", type=int, default=None)
    a = ap.parse_args(argv)
    for d in a.dirs:
        run_dataset(d, a.max_msgs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
