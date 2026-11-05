"""Generator invariants (SPEC 13): seq strictly +1, timestamps non-decreasing, no X/E/M for
unknown orders, product lifecycle per slot, book never crossed after matching, ends with
H(END), OOW/grid error fraction < 0.5% in default scenarios, determinism."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.generator.generate import generate, load_scenario  # noqa: E402
from proto import dk1proto as P  # noqa: E402
from ref.model import Config, run  # noqa: E402

CLEAN = ["unit_tiny", "cosim_10k"]


@pytest.fixture(scope="module")
def datasets(tmp_path_factory):
    out = {}
    for name in CLEAN + ["edge_errors"]:
        d = tmp_path_factory.mktemp(name)
        generate(load_scenario(ROOT / "data" / "scenarios" / f"{name}.yaml"), d, quiet=True)
        out[name] = d
    return out


def replay(msgs: np.ndarray):
    """Exchange-side replay: yields violations of book invariants."""
    live: dict[int, list] = {}            # oid -> [product, side, price, qty]
    levels = {}                           # (product, side) -> {price: qty}
    state = {}
    problems = []
    for m in msgs:
        t = int(m["type"])
        p = int(m["product"])
        if t == P.T_STATE:
            ns = int(m["qty"])
            prev = state.get(p, 0)
            allowed = {0: {0, 1}, 1: {2, 3}, 2: {0}, 3: {1, 2}}
            if ns not in allowed[prev] and not (prev == 0 and ns == 0):
                problems.append(("lifecycle", int(m["seq"]), prev, ns))
            state[p] = ns
            continue
        if t not in (P.T_ADD, P.T_DEL, P.T_EXEC, P.T_RED):
            continue
        oid = int(m["order_id"])
        if t == P.T_ADD:
            if state.get(p) != 1:
                problems.append(("add_not_open", int(m["seq"])))
            side, px, q = int(m["flags"]) & 1, int(m["price"]), int(m["qty"])
            live[oid] = [p, side, px, q]
            lv = levels.setdefault((p, side), {})
            lv[px] = lv.get(px, 0) + q
        else:
            if oid not in live:
                problems.append(("unknown_oid", int(m["seq"])))
                continue
            o = live[oid]
            if t in (P.T_EXEC, P.T_RED) and state.get(o[0]) != 1:
                problems.append(("exec_not_open", int(m["seq"])))
            r = o[3] if t == P.T_DEL else int(m["qty"])
            if r > o[3]:
                problems.append(("over_exec", int(m["seq"])))
                r = o[3]
            lv = levels[(o[0], o[1])]
            lv[o[2]] -= r
            if lv[o[2]] == 0:
                del lv[o[2]]
            o[3] -= r
            if o[3] == 0:
                del live[oid]
        bids, asks = levels.get((p, 0), {}), levels.get((p, 1), {})
        if bids and asks and max(bids) >= min(asks):
            problems.append(("crossed", int(m["seq"])))
    return problems


@pytest.mark.parametrize("name", CLEAN)
def test_invariants(datasets, name):
    msgs = P.read_msgs(datasets[name] / "feed.bin")
    assert len(msgs) > 500
    assert (np.diff(msgs["seq"].astype(np.int64)) == 1).all() and msgs["seq"][0] == 1
    assert (np.diff(msgs["ts_ns"].astype(np.int64)) >= 0).all()
    assert msgs["type"][-1] == P.T_HB and msgs["flags"][-1] & P.FLAG_END
    assert ((msgs["type"] == P.T_HB) & (msgs["flags"] & P.FLAG_END > 0)).sum() == 1
    assert set(np.unique(msgs["type"])) <= set(P.KNOWN_TYPES)
    assert (msgs["product"][msgs["type"] != P.T_HB] < 8).all()
    tick = 10
    px = msgs["price"][msgs["type"] == P.T_ADD]
    assert (px % tick == 0).all(), "all prices on the 0.10 EUR grid"
    assert replay(msgs) == []


@pytest.mark.parametrize("name", CLEAN)
def test_error_fractions(datasets, name):
    msgs = P.read_msgs(datasets[name] / "feed.bin")
    r = run(msgs, Config.load(datasets[name] / "config.json"))
    n_add = int((msgs["type"] == P.T_ADD).sum())
    assert (r.counters["err_oow"] + r.counters["err_grid"]) / n_add < 0.005
    for k, v in r.counters.items():
        if k.startswith("err_"):
            assert v == 0 or k == "err_oow", (k, v)


def test_edge_errors_has_errors(datasets):
    msgs = P.read_msgs(datasets["edge_errors"] / "feed.bin")
    r = run(msgs, Config.load(datasets["edge_errors"] / "config.json"))
    for k in ("err_unknown_type", "err_bad_product", "err_not_found", "err_dup_id", "err_grid",
              "err_seq_gap", "err_qty_over"):
        assert r.counters[k] > 0, k


def test_deterministic(tmp_path):
    sc = load_scenario(ROOT / "data" / "scenarios" / "unit_tiny.yaml")
    generate(sc, tmp_path / "a", quiet=True)
    generate(sc, tmp_path / "b", quiet=True)
    assert (tmp_path / "a" / "feed.bin").read_bytes() == (tmp_path / "b" / "feed.bin").read_bytes()
