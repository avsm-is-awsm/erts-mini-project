"""Offline synthetic fundamentals in the Energi Data Service CSV schema (no network needed).

    python data/make_fundamentals.py --day 2026-01-15 [--seed 1] [--profile default]

Writes data/out/eds/{DayAheadPrices,ImbalancePrice,Forecasts_5Min}_<start>_<end>.csv for one
UTC day.  The day is synthetic but plausible for DK1:
  * day-ahead price 20-150 EUR/MWh: night trough, morning and evening peaks, midday solar dip;
  * imbalance price = DA + noise with occasional spikes;
  * onshore + offshore wind 500-3500 MW with forecast errors that shrink with lead time
    (day-ahead > 5-hour > 1-hour > current), solar 0-800 MW midday.
Profiles: 'default', 'negative' (high wind, negative prices), 'volatile' (big imbalance spikes).
CI and all default scenarios use this script; fetch_eds.py produces the same schema from real data.
"""
from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDS_DIR = ROOT / "data" / "out" / "eds"


def stamp(t: datetime) -> str:
    return t.strftime("%Y%m%dT%H%M")


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def eds_paths(day: str, out_dir: Path = EDS_DIR) -> dict[str, Path]:
    t0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    return {ds: out_dir / f"{ds}_{stamp(t0)}_{stamp(t1)}.csv"
            for ds in ("DayAheadPrices", "ImbalancePrice", "Forecasts_5Min")}


def _da_shape(h: float) -> float:
    """EUR/MWh vs hour of day (UTC)."""
    base = 45.0
    morning = 45.0 * math.exp(-((h - 7.5) / 1.6) ** 2)
    evening = 85.0 * math.exp(-((h - 17.5) / 2.0) ** 2)
    solar_dip = -25.0 * math.exp(-((h - 12.5) / 2.2) ** 2)
    night = -20.0 * math.exp(-((h - 3.0) / 2.5) ** 2)
    return base + morning + evening + solar_dip + night


def make_day(day: str, seed: int = 1, profile: str = "default", out_dir: Path = EDS_DIR) -> dict[str, Path]:
    rng = random.Random(seed)
    t0 = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = eds_paths(day, out_dir)

    # ---- wind & solar, 5-minute resolution
    n5 = 288
    wind = []
    w = 1800.0 + rng.uniform(-600, 600)
    level = 3000.0 if profile == "negative" else 1800.0
    for i in range(n5):
        w += rng.gauss(0, 35) + 0.01 * (level - w)
        w = min(3500.0, max(500.0, w))
        wind.append(w)
    solar = [max(0.0, 800.0 * math.exp(-(((i / 12.0) - 12.0) / 3.0) ** 2) + rng.gauss(0, 15)) for i in range(n5)]

    def err_series(sigma: float, rho: float = 0.98) -> list[float]:
        e, out = 0.0, []
        for _ in range(n5):
            e = rho * e + math.sqrt(1 - rho * rho) * rng.gauss(0, sigma)
            out.append(e)
        return out

    e_da, e_5h, e_1h = err_series(320), err_series(160), err_series(60)
    with open(paths["Forecasts_5Min"], "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["Minutes5UTC", "PriceArea", "ForecastType", "ForecastDayAhead",
                     "Forecast5Hour", "Forecast1Hour", "ForecastCurrent"])
        for i in range(n5):
            t = iso(t0 + timedelta(minutes=5 * i))
            for typ, share in (("Onshore Wind", 0.6), ("Offshore Wind", 0.4)):
                a = wind[i] * share
                wr.writerow([t, "DK1", typ, round(a + e_da[i] * share, 2), round(a + e_5h[i] * share, 2),
                             round(a + e_1h[i] * share, 2), round(a, 2)])
            s = solar[i]
            wr.writerow([t, "DK1", "Solar", round(max(0, s + 0.2 * e_da[i]), 2),
                         round(max(0, s + 0.1 * e_5h[i]), 2), round(max(0, s + 0.05 * e_1h[i]), 2), round(s, 2)])

    # ---- prices, 15-minute resolution
    da, imb = [], []
    for q in range(96):
        h = q / 4.0
        wq = sum(wind[3 * q: 3 * q + 3]) / 3.0
        p = _da_shape(h) - 0.012 * (wq - 1800.0) + rng.gauss(0, 3.0)
        if profile == "negative":
            p = p - 70.0
        else:
            p = min(150.0, max(20.0, p))
        da.append(round(p, 2))
        # imbalance: DA + error correlated with the 1-hour wind forecast error, plus spikes
        err = -0.02 * (sum(e_1h[3 * q: 3 * q + 3]) / 3.0) * 4 + rng.gauss(0, 6.0)
        spike_p = 0.12 if profile == "volatile" else 0.05
        if rng.random() < spike_p:
            err += rng.choice((-1, 1)) * rng.uniform(15, 35 if profile != "volatile" else 120)
        imb.append(round(p + err, 2))

    with open(paths["DayAheadPrices"], "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["TimeUTC", "PriceArea", "DayAheadPriceEUR"])
        for q in range(96):
            wr.writerow([iso(t0 + timedelta(minutes=15 * q)), "DK1", da[q]])
    with open(paths["ImbalancePrice"], "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["TimeUTC", "PriceArea", "ImbalancePriceEUR"])
        for q in range(96):
            wr.writerow([iso(t0 + timedelta(minutes=15 * q)), "DK1", imb[q]])
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", default="2026-01-15")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--profile", default="default", choices=["default", "negative", "volatile"])
    ap.add_argument("--out", type=Path, default=EDS_DIR)
    a = ap.parse_args(argv)
    for k, p in make_day(a.day, a.seed, a.profile, a.out).items():
        print(f"{k}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
