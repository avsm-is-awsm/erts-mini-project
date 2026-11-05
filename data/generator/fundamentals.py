"""Load Energi Data Service CSVs (real via fetch_eds.py or synthetic via make_fundamentals.py)
into per-delivery-period inputs for the generator."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Delivery:
    start_ns: int
    da_eur: float
    imb_eur: float
    # wind (onshore + offshore) MW, averaged over the quarter hour, per forecast horizon
    f_da: float = 0.0
    f_5h: float = 0.0
    f_1h: float = 0.0
    f_cur: float = 0.0
    extra: dict = field(default_factory=dict)


def _ns(ts: str) -> int:
    t = datetime.fromisoformat(ts.replace("Z", ""))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return int(t.timestamp()) * 1_000_000_000


def _read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load(paths: dict[str, Path]) -> list[Delivery]:
    da = {_ns(r["TimeUTC"]): float(r["DayAheadPriceEUR"]) for r in _read(paths["DayAheadPrices"])
          if r.get("PriceArea", "DK1") == "DK1" and r["DayAheadPriceEUR"] not in ("", None)}
    imb = {_ns(r["TimeUTC"]): float(r["ImbalancePriceEUR"]) for r in _read(paths["ImbalancePrice"])
           if r.get("PriceArea", "DK1") == "DK1" and r["ImbalancePriceEUR"] not in ("", None)}
    fc: dict[int, list[float]] = {}
    for r in _read(paths["Forecasts_5Min"]):
        if r.get("PriceArea", "DK1") != "DK1" or "Wind" not in r["ForecastType"]:
            continue
        t5 = _ns(r["Minutes5UTC"])
        q = t5 - (t5 % (15 * 60 * 1_000_000_000))
        acc = fc.setdefault(q, [0.0, 0.0, 0.0, 0.0, 0])
        for i, k in enumerate(("ForecastDayAhead", "Forecast5Hour", "Forecast1Hour", "ForecastCurrent")):
            v = r.get(k)
            acc[i] += float(v) if v not in ("", None) else 0.0
        acc[4] += 1
    out = []
    for t in sorted(da):
        d = Delivery(start_ns=t, da_eur=da[t], imb_eur=imb.get(t, da[t]))
        if t in fc and fc[t][4]:
            n = fc[t][4] / 2.0  # two wind types per 5-minute slot -> per-slot sum, averaged
            d.f_da, d.f_5h, d.f_1h, d.f_cur = (fc[t][i] / n for i in range(4))
        out.append(d)
    return out
