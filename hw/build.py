"""Driver for the Vivado build (make vivado).

    python hw/build.py [--ila] [--jobs 4] [--bd-only]

Stages rtl/, hls/ip/ and hw/build_bd.tcl into a space-free build root, runs Vivado in batch
mode, and copies the bitstream, hardware handoff (.hdf/.sysdef), address map and reports into
hw/ and hw/reports/.  Requires `make hls` (exported IP in hls/ip) first.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.xilinx import ROOT, build_root, find_tool, run_tool, stage  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ila", action="store_true")
    ap.add_argument("--jobs", default="4")
    ap.add_argument("--bd-only", action="store_true")
    a = ap.parse_args(argv)
    if not (ROOT / "hls" / "ip" / "component.xml").exists():
        raise SystemExit("hls/ip/component.xml missing: run make hls (export) first")
    br = build_root()
    stage(["rtl", "hls/ip", "hw/build_bd.tcl"], br)
    work = br / "hw" / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    args = ["-tclargs", work.as_posix(), (br / "hls" / "ip").as_posix(), (br / "rtl").as_posix(),
            "-jobs", a.jobs]
    if a.ila:
        args.append("-ila")
    if a.bd_only:
        args.append("-nobit")
    viv = find_tool("vivado", "Vivado")
    log = work / "vivado_build.log"
    print(f"[vivado] building in {work} (log {log})")
    t0 = time.time()
    rc = run_tool([viv, "-notrace", "-mode", "batch", "-source", (br / "hw" / "build_bd.tcl").as_posix(),
                   *args], work, log, watch_runs=work)
    dt = time.time() - t0
    text = log.read_text(errors="replace")
    ok = ("BUILD_DONE" in text) or (a.bd_only and "BD_ONLY_DONE" in text)
    timing_failed = "ERROR: timing not met" in text
    print(f"[vivado] exit {rc} after {dt/60:.1f} min: {'OK' if ok else 'FAILED'}")
    for ln in [x for x in text.splitlines() if x.startswith(("ERROR", "CRITICAL WARNING"))][:20]:
        print("  " + ln)
    out = ROOT / "hw"
    rep = out / "reports"
    rep.mkdir(exist_ok=True)
    shutil.copy2(log, rep / "vivado_build.log")
    for f in ("dk1_t2t.bit", "dk1_t2t.hdf", "dk1_t2t.sysdef", "address_map.txt", "system_bd_export.tcl"):
        if timing_failed and f != "address_map.txt":
            continue  # never publish a bitstream / handoff that violates timing
        if (work / f).exists():
            shutil.copy2(work / f, out / f)
    if (work / "reports").exists():
        for f in (work / "reports").glob("*"):
            shutil.copy2(f, rep / f.name)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
