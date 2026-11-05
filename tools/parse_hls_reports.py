"""Parse Vivado HLS / Vivado reports into tables (markdown + JSON) for docs/08_results.md.

    python tools/parse_hls_reports.py [--sol impl1] [--cosim cosim1]

Reads hls/reports/<sol>/t2t_core_csynth.rpt (+ ooc_* post-route reports) and
hls/reports/<cosim>/t2t_core_cosim.rpt, hw/reports/utilization.rpt + summary.txt.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def csynth(path: Path) -> dict:
    t = path.read_text(errors="replace")
    d: dict = {}
    m = re.search(r"\|ap_clk\s*\|\s*([\d.]+)\|\s*([\d.]+)\|\s*([\d.]+)\|", t)
    if m:
        d["target_ns"], d["estimated_ns"], d["uncertainty_ns"] = map(float, m.groups())
    m = re.search(r"Latency.*?\|\s*min\s*\|\s*max\s*\|\s*min\s*\|\s*max\s*\|.*?\n.*?\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|", t, re.S)
    if m:
        d["latency_min"], d["latency_max"], d["ii_min"], d["ii_max"] = map(int, m.groups())
    m = re.search(r"\|Total\s*\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|", t)
    if m:
        d["est_bram18"], d["est_dsp"], d["est_ff"], d["est_lut"] = map(int, m.groups())
    return d


def vivado_util(path: Path) -> dict:
    t = path.read_text(errors="replace")
    d = {}
    for key, label in (("lut", r"Slice LUTs\*?"), ("ff", r"Slice Registers"), ("bram_tiles", r"Block RAM Tile"),
                       ("dsp", r"DSPs")):
        m = re.search(r"\|\s*" + label + r"\s*\|\s*([\d.]+)\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|", t)
        if m:
            d[key] = float(m.group(1))
            d[key + "_avail"] = int(m.group(2))
            d[key + "_pct"] = float(m.group(3))
    return d


def kv(path: Path) -> dict:
    out = {}
    for ln in path.read_text().splitlines():
        p = ln.split()
        if len(p) == 2:
            try:
                out[p[0]] = float(p[1])
            except ValueError:
                out[p[0]] = p[1]
    return out


def cosim(path: Path) -> dict:
    t = path.read_text(errors="replace")
    m = re.search(r"\|\s*Verilog\|\s*(\w+)\|", t)
    return {"verilog_status": m.group(1) if m else "n/a"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sol", default="impl1")
    ap.add_argument("--cosim", default="cosim1")
    a = ap.parse_args(argv)
    res: dict = {}
    rep = ROOT / "hls" / "reports" / a.sol
    if (rep / "t2t_core_csynth.rpt").exists():
        res["csynth"] = csynth(rep / "t2t_core_csynth.rpt")
    if (rep / "ooc_utilization.rpt").exists():
        res["ooc_post_route"] = vivado_util(rep / "ooc_utilization.rpt")
        res["ooc_post_route"].update(kv(rep / "ooc_summary.txt"))
    crep = ROOT / "hls" / "reports" / a.cosim / "t2t_core_cosim.rpt"
    if crep.exists():
        res["cosim"] = cosim(crep)
        log = (crep.parent / "hls.log").read_text(errors="replace")
        m = re.search(r"tb_t2t: (\d+) msgs, (\d+) ord records.*?(\d+) dbg records", log)
        if m:
            res["cosim"].update(msgs=int(m.group(1)), ord_records=int(m.group(2)), dbg_records=int(m.group(3)))
        res["cosim"]["tb_pass"] = "tb_t2t: PASS" in log
    hw = ROOT / "hw" / "reports"
    if (hw / "utilization.rpt").exists():
        res["system_post_route"] = vivado_util(hw / "utilization.rpt")
        if (hw / "summary.txt").exists():
            res["system_post_route"].update(kv(hw / "summary.txt"))
    out = ROOT / "hls" / "reports" / "summary.json"
    out.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
