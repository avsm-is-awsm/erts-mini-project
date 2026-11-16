"""Scenario -> dataset folder (SPEC 7.2, 7.3).

    python -m data.generator data/scenarios/<name>.yaml [--out data/out/<name>] [--eds]

Steps: fundamentals (synthetic offline, or EDS CSVs with --eds) -> one ProductSpec per
quarter-hour delivery period -> independent per-product simulations -> merge by
(timestamp, delivery, local order) -> global order ids by first appearance -> heartbeats ->
optional fault injection -> sequence numbers -> END heartbeat -> files.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data import make_fundamentals as mf  # noqa: E402
from data.generator import GENERATOR_VERSION, faults  # noqa: E402
from data.generator.fundamentals import load as load_fundamentals  # noqa: E402
from data.generator.sim import AGENT_NAMES, MS_NS, S_NS, ProductSpec, simulate  # noqa: E402
from proto import dk1proto as P  # noqa: E402
from ref.model import PARAMS as CORE_PARAMS, Config  # noqa: E402

DEFAULT_PARAMS = dict(
    tick=CORE_PARAMS["LADDER_TICK"], px_min=-999_900, px_max=999_900,
    # fair value
    fv_step_s=60, ramp_k=8.0, fv_clip=4000, beta=2.0,           # beta: 0.01 EUR per MW (=0.02 EUR/MW)
    minor_rev_per_h=4.0, minor_rev_sigma_mw=80.0, rev_1h_before_gc_s=300,
    # market makers
    n_mm=4, mm_half_spread=(40.0, 120.0), mm_gap=30.0, mm_levels_max=3, mm_size=(20.0, 60.0),
    mm_sigma=80.0, mm_refresh_s=7.0, mm_rush_a=3.0, mm_reduce_p=0.3, mm_react_ms=200.0, mm_react_sigma=0.5,
    # noise traders
    noise_rate=0.12, rush_a=4.0, rush_tau_s=1200.0, noise_size_med=20.0, noise_aggr_p=0.45,
    noise_far_p=0.0, noise_far_eur=(6000.0, 9000.0), noise_sigma=150.0, noise_half_spread=150.0,
    noise_life_s=25.0, noise_reduce_p=0.2,
    # competitor takers
    n_ct=6, ct_lat_min_us=50.0, ct_lat_max_us=50_000.0, ct_lat_sigma=0.3, ct_edge=50, ct_clip=50,
    # our slow path (V messages)
    sigma_model=30.0, d_model_ms=200, edge=100,
)

REC_DTYPE = np.dtype([
    ("ts", "<u8"), ("type", "u1"), ("side", "u1"), ("d", "<i4"), ("slot", "<u2"),
    ("oid", "<u8"), ("price", "<i4"), ("qty", "<u4"), ("agent", "u1"), ("agent_id", "<u2"),
    ("true_fv", "<i4"), ("local", "<i4"),
])


def _ns(t: datetime) -> int:
    return int(t.timestamp()) * S_NS


def load_scenario(path: Path) -> dict:
    sc = yaml.safe_load(Path(path).read_text())
    if not isinstance(sc, dict):
        raise ValueError(f"{path}: scenario file is empty or not a mapping")
    sc.setdefault("name", Path(path).stem)
    sc.setdefault("seed", 1)
    sc.setdefault("day", "2026-01-15")
    sc.setdefault("deliveries", [0, 96])
    sc.setdefault("window_min", 120)
    sc.setdefault("gc_min", 60)
    sc.setdefault("heartbeat_s", 5)
    sc.setdefault("fundamentals", {})
    sc.setdefault("faults", {})
    sc.setdefault("config", {})
    prm = dict(DEFAULT_PARAMS)
    for k, v in (sc.get("params") or {}).items():
        if k not in prm:
            raise KeyError(f"unknown generator param '{k}'")
        prm[k] = tuple(v) if isinstance(v, list) else v
    sc["params"] = prm
    d0, d1 = sc["deliveries"]
    if not (isinstance(d0, int) and isinstance(d1, int) and 0 <= d0 < d1):
        raise ValueError(f"deliveries must be [first, last) with first < last, got {sc['deliveries']}")
    max_window = CORE_PARAMS["P_MAX"] * 15
    if not 0 < sc["window_min"] <= max_window:
        raise ValueError(f"window_min must be in (0, {max_window}] minutes: a longer window would put "
                         f"more than {CORE_PARAMS['P_MAX']} products on the {CORE_PARAMS['P_MAX']} slots")
    if prm["tick"] != CORE_PARAMS["LADDER_TICK"]:
        raise ValueError("generator tick must equal the core LADDER_TICK")
    if prm["fv_clip"] + 1000 > CORE_PARAMS["W"] // 2 * CORE_PARAMS["LADDER_TICK"] and not sc.get("allow_oow"):
        raise ValueError("fv_clip leaves < 10 EUR margin to the ladder window (set allow_oow: true to force)")
    return sc


def build_specs(sc: dict, deliveries) -> list[ProductSpec]:
    prm = sc["params"]
    day0 = datetime.fromisoformat(sc["day"]).replace(tzinfo=timezone.utc)
    by_start = {d.start_ns: d for d in deliveries}
    rng = random.Random(sc["seed"] * 7919 + 17)
    over = sc.get("da_override") or {}
    specs = []
    d0, d1 = sc["deliveries"]
    for d in range(d0, d1):
        start = _ns(day0 + timedelta(minutes=15 * d))
        if start not in by_start:
            raise ValueError(f"no fundamentals for delivery {d} ({start})")
        fd = by_start[start]
        da_eur = float(over.get(d, over.get("all", fd.da_eur)))
        imb_eur = fd.imb_eur + (da_eur - fd.da_eur)
        gc = start - sc["gc_min"] * 60 * S_NS
        t_open = gc - int(round(sc["window_min"] * 60 * S_NS))
        da = int(round(da_eur * 100))
        imb = int(round(imb_eur * 100))
        tick = prm["tick"]
        anchor = int(round(da / tick)) * tick
        anchor = max(prm["px_min"], min(prm["px_max"], anchor))
        beta = prm["beta"]
        shocks = []
        if fd.f_da or fd.f_5h:
            shocks.append((t_open + 1, -beta * (fd.f_5h - fd.f_da)))           # 5-hour revision
            shocks.append((gc - prm["rev_1h_before_gc_s"] * S_NS, -beta * (fd.f_1h - fd.f_5h)))
        span_h = (gc - t_open) / 3600 / S_NS
        n_minor = _poisson(rng, prm["minor_rev_per_h"] * span_h)
        for _ in range(n_minor):
            ts = t_open + int(rng.random() * (gc - t_open))
            shocks.append((ts, -beta * rng.gauss(0.0, prm["minor_rev_sigma_mw"])))
        specs.append(ProductSpec(d=d, slot=d % CORE_PARAMS["P_MAX"], open_ns=t_open, gc_ns=gc,
                                 da=da, imb=imb, anchor=anchor, shocks=shocks))
    return specs


def _poisson(rng: random.Random, lam: float) -> int:
    k, t = 0, rng.expovariate(1.0) if lam > 0 else math.inf
    while t < lam:
        k += 1
        t += rng.expovariate(1.0)
    return k


def _to_array(msgs: list, spec: ProductSpec) -> np.ndarray:
    a = np.zeros(len(msgs), dtype=REC_DTYPE)
    if not msgs:
        return a
    ts, typ, side, oid, px, q, extra, ag, aid, fv = zip(*msgs)
    a["ts"] = ts
    a["type"] = np.frombuffer("".join(typ).encode(), dtype=np.uint8)
    a["side"] = side
    a["d"] = spec.d
    a["slot"] = spec.slot
    oid = np.array(oid, dtype=np.int64)
    extra = np.array(extra, dtype=np.uint64)
    a["oid"] = np.where(a["type"] == ord("S"), extra, oid.astype(np.uint64))
    a["price"] = px
    a["qty"] = q
    a["agent"] = ag
    a["agent_id"] = aid
    a["true_fv"] = fv
    a["local"] = np.arange(len(msgs))
    return a


def generate(sc: dict, out: Path, use_eds: bool = False, quiet: bool = False) -> dict:
    t_start = time.perf_counter()
    prm = sc["params"]
    fcfg = sc["fundamentals"]
    if use_eds or fcfg.get("source") == "eds":
        paths = mf.eds_paths(sc["day"])
        missing = [p for p in paths.values() if not p.exists()]
        if missing:
            raise FileNotFoundError(f"EDS data missing ({missing[0]}); run make fetch first")
        source = "eds"
    else:
        paths = mf.make_day(sc["day"], seed=fcfg.get("seed", 1), profile=fcfg.get("profile", "default"),
                            out_dir=ROOT / "data" / "out" / "eds_synth" / fcfg.get("profile", "default"))
        source = f"synthetic:{fcfg.get('profile', 'default')}"
    deliveries = load_fundamentals(paths)
    specs = build_specs(sc, deliveries)

    parts, products = [], []
    for spec in specs:
        msgs, st = simulate(spec, prm, seed=sc["seed"] * 1_000_003 + spec.d)
        parts.append(_to_array(msgs, spec))
        products.append(dict(d=spec.d, slot=spec.slot, open_ns=spec.open_ns, gc_ns=spec.gc_ns,
                             start_ns=spec.gc_ns + sc["gc_min"] * 60 * S_NS, da=spec.da, imb=spec.imb,
                             anchor=spec.anchor, final_fv=st["final_fv"], n_msgs=len(msgs)))
    recs = np.concatenate(parts) if parts else np.zeros(0, REC_DTYPE)
    order = np.lexsort((recs["local"], recs["d"], recs["ts"]))
    recs = recs[order]

    # global order ids: sequential in order of first appearance (like exchange order refs)
    has_oid = np.isin(recs["type"], np.frombuffer(b"AXEM", np.uint8))
    key = recs["d"][has_oid].astype(np.uint64) * np.uint64(2**32) + recs["oid"][has_oid]
    uniq, first = np.unique(key, return_index=True)
    rank = np.empty(len(uniq), dtype=np.uint64)
    rank[np.argsort(first, kind="stable")] = np.arange(1, len(uniq) + 1, dtype=np.uint64)
    recs["oid"][has_oid] = rank[np.searchsorted(uniq, key)] + np.uint64(sc.get("oid_base", 0))

    # heartbeats
    if sc["heartbeat_s"] and len(recs):
        t0, t1 = int(recs["ts"][0]), int(recs["ts"][-1])
        hb_ts = np.arange(t0 - t0 % (sc["heartbeat_s"] * S_NS) + sc["heartbeat_s"] * S_NS, t1,
                          sc["heartbeat_s"] * S_NS, dtype=np.uint64)
        hb = np.zeros(len(hb_ts), dtype=REC_DTYPE)
        hb["ts"] = hb_ts
        hb["type"] = ord("H")
        hb["d"] = -1
        recs = np.concatenate([recs, hb])
        recs = recs[np.lexsort((recs["local"], recs["d"], recs["ts"]))]

    rng = np.random.default_rng(sc["seed"] + 99)
    recs = faults.apply(recs, sc["faults"], rng, CORE_PARAMS["P_MAX"], CORE_PARAMS["SETS"])

    # END_OF_SESSION
    end = np.zeros(1, dtype=REC_DTYPE)
    end["ts"] = (int(recs["ts"][-1]) + 1) if len(recs) else 1
    end["type"] = ord("H")
    end["side"] = 0
    end["d"] = -1
    recs = np.concatenate([recs, end])

    # feed records
    n = len(recs)
    m = np.zeros(n, dtype=P.MSG_DTYPE)
    m["type"] = recs["type"]
    m["flags"] = recs["side"] & 1
    m["flags"][-1] = P.FLAG_END
    m["product"] = recs["slot"]
    m["ts_ns"] = recs["ts"]
    m["order_id"] = np.where(recs["type"] == ord("H"), 0, recs["oid"])
    m["price"] = recs["price"]
    m["qty"] = recs["qty"]
    seq = np.arange(1, n + 1, dtype=np.int64)
    gaps = int(sc["faults"].get("seq_gaps", 0))
    if gaps:
        jump = np.zeros(n, dtype=np.int64)
        jump[rng.integers(1, n, size=gaps)] = rng.integers(1, 5, size=gaps)
        seq += np.cumsum(jump)
    m["seq"] = seq.astype(np.uint32)

    out.mkdir(parents=True, exist_ok=True)
    m.tofile(out / "feed.bin")
    P.msgs_to_csv(m, out / "feed.csv")
    cfg = Config(**{k: int(v) for k, v in sc["config"].items()})
    cfg.save(out / "config.json")
    _write_events(out / "events.parquet", recs, m)
    meta = dict(
        scenario=sc["name"], seed=sc["seed"], generator_version=GENERATOR_VERSION,
        fundamentals=source, day=sc["day"], deliveries=sc["deliveries"], window_min=sc["window_min"],
        gc_min=sc["gc_min"], params={k: list(v) if isinstance(v, tuple) else v for k, v in prm.items()},
        faults=sc["faults"], core_params=CORE_PARAMS, n_msgs=int(n),
        counts={chr(t): int((m["type"] == t).sum()) for t in np.unique(m["type"])},
        products=products, agents=AGENT_NAMES,
        units=dict(price="0.01 EUR/MWh", qty="0.1 MW", ts="ns since Unix epoch (UTC)"),
        build_s=round(time.perf_counter() - t_start, 2),
    )
    (out / "meta.json").write_text(json.dumps(meta, indent=1, default=int) + "\n")
    if not quiet:
        print(f"{sc['name']}: {n} msgs, {len(specs)} products, {meta['build_s']} s -> {out}")
    return meta


def _write_events(path: Path, recs: np.ndarray, m: np.ndarray) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:  # pragma: no cover
        np.save(path.with_suffix(".npy"), recs)
        return
    tbl = pa.table({
        "seq": m["seq"], "ts_ns": m["ts_ns"], "type": m["type"], "product": m["product"],
        "delivery": recs["d"], "agent": recs["agent"], "agent_id": recs["agent_id"],
        "true_fv": recs["true_fv"],
    })
    pq.write_table(tbl, path, compression="zstd")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--eds", action="store_true", help="use downloaded Energi Data Service CSVs")
    a = ap.parse_args(argv)
    sc = load_scenario(a.scenario)
    out = a.out or (ROOT / "data" / "out" / sc["name"])
    generate(sc, out, use_eds=a.eds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
