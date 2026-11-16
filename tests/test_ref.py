"""Reference model tests: hand-derived expectations, determinism, stateful runs, config I/O."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proto import dk1proto as P  # noqa: E402
from ref.model import Config, RefModel, run  # noqa: E402
from tests import hand_case as hc  # noqa: E402


def test_hand_case_exact():
    r = run(hc.feed_array(), Config(ctrl=5))
    assert np.array_equal(P.golden_mask(r.orders), P.golden_mask(hc.expected_orders_bytes()))
    assert np.array_equal(P.golden_mask(r.bbo), P.golden_mask(hc.expected_bbo_bytes()))
    assert r.counters == hc.EXP_COUNTERS
    assert r.pos[0] == hc.EXP_POS0


def test_dbg_off_drops_only_rejects_and_bbo():
    full = run(hc.feed_array(), Config(ctrl=5))
    lean = run(hc.feed_array(), Config(ctrl=1))
    o_full = P.as_ord(full.orders)
    keep = full.orders[o_full["type"] != P.O_REJECT]
    assert np.array_equal(P.golden_mask(lean.orders), P.golden_mask(keep))
    assert len(lean.bbo) == 0 and lean.counters == full.counters


def test_split_run_equals_single_run():
    msgs = hc.feed_array()
    one = run(msgs, Config(ctrl=5))
    m = RefModel(Config(ctrl=5))
    a, b = m.run(msgs[:9]), m.run(msgs[9:])
    assert np.array_equal(np.concatenate([a.orders, b.orders]), one.orders)
    assert b.counters == one.counters


def test_config_validation(tmp_path):
    import json

    import pytest

    for bad in (dict(clip_qty=-1), dict(max_pos=2**31), dict(collar=2**32), dict(ctrl=1.5)):
        with pytest.raises(ValueError):
            Config(**bad).validate()
    Config(max_pos=-(2**31), collar=2**32 - 1).validate()
    (tmp_path / "c.json").write_text(json.dumps({"ctrl": 5, "colar": 3}))
    with pytest.raises(ValueError):
        Config.load(tmp_path / "c.json")       # misspelled key must not be silently ignored


def test_config_roundtrip(tmp_path):
    c = Config(ctrl=7, clip_qty=3, max_pos=-4, lockout_ms=9)
    c.save(tmp_path / "config.json")
    assert Config.load(tmp_path / "config.json") == c
    kv = (tmp_path / "config.txt").read_text()
    assert "max_pos=-4" in kv and "ctrl=7" in kv
