"""Byte-compare run outputs against the golden model, masking timestamp fields (SPEC 5.3, D-07).

    python tools/compare.py <dataset_dir> [--orders orders_hw.bin] [--bbo dbg_hw.bin]
                            [--counters counters_hw.json] [--no-dbg]

--no-dbg: the run had CTRL.dbg_en = 0, so golden 'R' records are dropped before comparing
(dbg_en only adds records, D-23).  Exit code 0 = identical, 1 = mismatch, 2 = missing files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402

COUNTERS = ("msgs_in", "orders_out", "rejects", "err_unknown_type", "err_bad_product", "err_not_found",
            "err_table_full", "err_dup_id", "err_oow", "err_grid", "err_seq_gap", "err_qty_over",
            "err_anchor", "err_bad_state", "last_seq")


def compare_records(got: np.ndarray, gold: np.ndarray) -> dict:
    g, r = P.golden_mask(got), P.golden_mask(gold)
    res = dict(n_got=int(len(g)), n_golden=int(len(r)), equal=False, first_diff=None, n_diff=0)
    n = min(len(g), len(r))
    diff = np.nonzero((g[:n] != r[:n]).any(axis=1))[0]
    res["n_diff"] = int(len(diff)) + abs(len(g) - len(r))
    if len(diff):
        i = int(diff[0])
        res["first_diff"] = dict(index=i, got=g[i].tobytes().hex(), golden=r[i].tobytes().hex())
    elif len(g) != len(r):
        res["first_diff"] = dict(index=n, got=None, golden=None)
    res["equal"] = res["n_diff"] == 0
    return res


def compare_dataset(d: Path, orders: str, bbo: str | None, counters: str | None, no_dbg: bool) -> dict:
    out: dict = {"dataset": d.name}
    gold_o = P.read_out(d / "golden_orders.bin")
    if no_dbg:
        gold_o = gold_o[gold_o[:, 0] != P.O_REJECT]
    got_o = P.read_out(d / orders)
    out["orders"] = compare_records(got_o, gold_o)
    # structural sanity independent of the golden files
    kinds = set(got_o[:, 0].tolist())
    out["structure"] = dict(
        equal=bool(len(got_o) and got_o[-1, 0] == P.O_END and (got_o[:, 0] == P.O_END).sum() == 1
                   and kinds <= {P.O_ORDER, P.O_REJECT, P.O_END}),
        note="exactly one 'Z', as the last record; only O/R/Z types")
    if bbo and (d / bbo).exists():
        out["bbo"] = compare_records(P.read_out(d / bbo), P.read_out(d / "golden_bbo.bin"))
    if counters and (d / counters).exists():
        got = json.loads((d / counters).read_text())
        gold = json.loads((d / "golden_counters.json").read_text())
        bad = {k: (got.get(k), gold[k]) for k in COUNTERS if got.get(k) != gold[k]}
        if got.get("pos") is not None and got.get("pos") != gold.get("pos"):
            bad["pos"] = (got.get("pos"), gold.get("pos"))
        out["counters"] = dict(equal=not bad, diff=bad)
    out["equal"] = all(v["equal"] for k, v in out.items() if isinstance(v, dict) and "equal" in v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--orders", default="orders_hw.bin")
    ap.add_argument("--bbo", default=None)
    ap.add_argument("--counters", default=None)
    ap.add_argument("--no-dbg", action="store_true")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    if not (a.dataset / a.orders).exists() or not (a.dataset / "golden_orders.bin").exists():
        print(f"missing {a.dataset / a.orders} or golden files")
        return 2
    res = compare_dataset(a.dataset, a.orders, a.bbo, a.counters, a.no_dbg)
    text = json.dumps(res, indent=2)
    if a.json:
        a.json.write_text(text + "\n")
    print(text)
    print("COMPARE", "PASS" if res["equal"] else "FAIL")
    return 0 if res["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
