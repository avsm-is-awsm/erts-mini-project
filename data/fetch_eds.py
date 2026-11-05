"""Energi Data Service (Energinet) downloader for DK1 fundamentals (SPEC 7.1).  Needs network.

    python data/fetch_eds.py --start 2026-01-15 --end 2026-01-16

No API key.  For each dataset the field names are first checked against
https://api.energidataservice.dk/meta/dataset/<name>; the script fails with a clear message if
Energinet renamed them.  Output: data/out/eds/<Dataset>_<start>_<end>.csv in exactly the schema
of data/make_fundamentals.py, so `python -m data.generator <scenario> --eds` can use it.
Downloaded data is never committed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.energidataservice.dk"
OUT = ROOT / "data" / "out" / "eds"

DATASETS = {
    # dataset: (time field, value fields we need)
    "DayAheadPrices": ("TimeUTC", ["PriceArea", "DayAheadPriceEUR"]),
    "ImbalancePrice": ("TimeUTC", ["PriceArea", "ImbalancePriceEUR"]),
    "Forecasts_5Min": ("Minutes5UTC", ["PriceArea", "ForecastType", "ForecastDayAhead", "Forecast5Hour",
                                       "Forecast1Hour", "ForecastCurrent"]),
}


def _get(url: str, params: dict | None = None) -> dict:
    import re
    import time

    import requests

    for attempt in range(4):
        r = requests.get(url, params=params, timeout=60, headers={"User-Agent": "dk1-t2t/1.0"})
        if r.status_code == 429 and attempt < 3:  # rate limited: wait as the server asks
            m = re.search(r"(\d+) seconds", r.text)
            time.sleep(int(m.group(1)) + 2 if m else 60)
            continue
        if r.status_code != 200:
            raise SystemExit(f"HTTP {r.status_code} for {r.url}: {r.text[:200]}")
        return r.json()
    raise SystemExit("rate limit persisted")


def check_fields(ds: str, needed: list[str]) -> None:
    meta = _get(f"{API}/meta/dataset/{ds}")
    names = set()
    for key in ("columns", "fields", "attributes"):
        for c in meta.get(key, []) or []:
            names.add(c.get("dbColumn") or c.get("name") or c.get("columnName"))
    if not names:  # unknown meta format: fall back to the first record
        names = set(_get(f"{API}/dataset/{ds}", {"limit": 1}).get("records", [{}])[0].keys())
    missing = [f for f in needed if f not in names]
    if missing:
        raise SystemExit(f"Energi Data Service dataset '{ds}' no longer has fields {missing} "
                         f"(available: {sorted(n for n in names if n)}). Update data/fetch_eds.py.")


def fetch(start: str, end: str, out: Path = OUT) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    stamp = lambda t: t.strftime("%Y%m%dT%H%M")  # noqa: E731
    paths = {}
    for ds, (tf, fields) in DATASETS.items():
        check_fields(ds, [tf] + fields)
        # the API's start/end filter on Danish local time: widen by a day, keep UTC [t0, t1)
        q0, q1 = t0 - timedelta(days=1), t1 + timedelta(days=1)
        js = _get(f"{API}/dataset/{ds}", {
            "start": q0.strftime("%Y-%m-%dT%H:%M"), "end": q1.strftime("%Y-%m-%dT%H:%M"),
            "filter": json.dumps({"PriceArea": ["DK1"]}), "limit": 0, "sort": f"{tf} ASC"})
        lo, hi = t0.strftime("%Y-%m-%dT%H:%M:%S"), t1.strftime("%Y-%m-%dT%H:%M:%S")
        recs = [r for r in js.get("records", []) if lo <= str(r.get(tf))[:19] < hi]
        if not recs:
            raise SystemExit(f"no records for {ds} between {start} and {end}")
        p = out / f"{ds}_{stamp(t0)}_{stamp(t1)}.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([tf] + fields)
            for r in recs:
                w.writerow([r.get(tf)] + [r.get(k) for k in fields])
        paths[ds] = p
        print(f"{ds}: {len(recs)} records -> {p}")
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (UTC), exclusive")
    a = ap.parse_args(argv)
    fetch(a.start, a.end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
