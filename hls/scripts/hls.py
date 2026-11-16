"""Driver for the Vivado HLS flow (make hls / make cosim).

    python hls/scripts/hls.py [--steps csim,csynth,cosim,export] [--ds cosim_10k] [--max-msgs N]
                              [--clock 10] [--sol sol1]

Stages hls/src, hls/tb, hls/scripts and proto/ into a space-free build root, runs
run_hls.tcl there, then copies the reports into hls/reports/<sol>/ and (for export)
the IP catalog zip + unpacked IP into hls/ip/.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.xilinx import ROOT, build_root, find_tool, run_tool, stage  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="csim,csynth")
    ap.add_argument("--ds", default="cosim_10k")
    ap.add_argument("--max-msgs", default="")
    ap.add_argument("--clock", default="10")
    ap.add_argument("--sol", default="sol1")
    ap.add_argument("--part", default="xc7z010clg400-1")
    a = ap.parse_args(argv)

    br = build_root()
    stage(["hls/src", "hls/tb", "hls/scripts", "proto/dk1proto.hpp"], br)
    work = br / "hls" / "build"
    work.mkdir(parents=True, exist_ok=True)
    ds_src = ROOT / "data" / "out" / a.ds
    if not (ds_src / "feed.bin").exists():
        raise SystemExit(f"dataset {ds_src} missing: run make data SCEN={a.ds} && make ref SCEN={a.ds}")
    ds = work / "ds"
    if ds.exists():
        shutil.rmtree(ds)
    ds.mkdir()
    for f in ["feed.bin", "config.json", *[p.name for p in ds_src.glob("golden_*")]]:
        shutil.copy2(ds_src / f, ds / f)

    tool = find_tool("vivado_hls", "Vivado_HLS")
    log = work / f"hls_{a.sol}.log"
    env = dict(T2T_STEPS=a.steps, T2T_DS="__staged__", T2T_MAXMSGS=a.max_msgs, T2T_CLOCK=a.clock,
               T2T_SOL=a.sol, T2T_PART=a.part)
    print(f"[hls] {a.steps} in {work} (log {log})")
    t0 = time.time()
    rc = run_tool([tool, "-f", "../scripts/run_hls.tcl"], work, log, env)
    print(f"[hls] exit {rc} after {time.time() - t0:.0f} s")

    # copy results back (stale reports are removed first)
    sol = work / "t2t_prj" / a.sol
    rep = ROOT / "hls" / "reports" / a.sol
    if rep.exists():
        shutil.rmtree(rep)
    rep.mkdir(parents=True, exist_ok=True)
    # keep the copied log small: drop the per-transaction cosim progress lines
    (rep / "hls.log").write_text("".join(ln for ln in log.read_text(errors="replace").splitlines(True)
                                         if not ln.startswith("// RTL Simulation :")))
    for sub in ("syn/report", "sim/report"):
        d = sol / sub
        if d.exists():
            for f in d.glob("*.rpt"):
                shutil.copy2(f, rep / f.name)
    for f in (sol / "csim" / "report").glob("*.log") if (sol / "csim" / "report").exists() else []:
        shutil.copy2(f, rep / f.name)
    text = log.read_text(errors="replace")
    if ("export" in a.steps or "impl" in a.steps) and "Failed to generate IP" in text:
        # Vivado HLS <= 2019 derives the IP core_revision from the date (YYMMDDHHMM), which
        # overflows a 32-bit int since 2022 (AMD AR 76960).  Patch the generated packaging
        # script with a valid revision and re-run it (DECISIONS D-19).
        ipdir = sol / "impl" / "ip"
        pk = ipdir / "run_ippack.tcl"
        if not pk.exists():
            print("[hls] FAILED: IP packaging script was not generated (see log)")
            return 1
        src = pk.read_text()
        rev = time.strftime("%y%m%d")
        src = re.sub(r'set Revision\s+"\d+"', f'set Revision    "{rev}"', src)
        pk.write_text(src)
        viv = find_tool("vivado", "Vivado")
        rc2 = run_tool([viv, "-notrace", "-mode", "batch", "-source", "run_ippack.tcl"], ipdir,
                       work / f"ippack_{a.sol}.log")
        ok = rc2 == 0 and any(ipdir.glob("*.zip"))
        print(f"[hls] IP packaging re-run with core_revision {rev}: {'OK' if ok else 'FAILED'}")
        if ok:
            text = text.replace("Failed to generate IP", "Failed to generate IP (patched, see ippack log)")
            log.write_text(text)
            shutil.copy2(log, rep / "hls.log")
            rc = 0
    if "impl" in a.steps:
        vdir = sol / "impl" / "verilog"
        shutil.copy2(ROOT / "hls" / "scripts" / "ooc_impl.tcl", vdir / "ooc_impl.tcl")
        viv = find_tool("vivado", "Vivado")
        print("[hls] Vivado out-of-context synthesis + place & route ...")
        t1 = time.time()
        rc3 = run_tool([viv, "-notrace", "-mode", "batch", "-source", "ooc_impl.tcl", "-tclargs",
                        a.part, a.clock, "ooc"], vdir, work / f"ooc_{a.sol}.log")
        print(f"[hls] OOC impl exit {rc3} after {time.time() - t1:.0f} s")
        for f in (vdir / "ooc").glob("*"):
            shutil.copy2(f, rep / f.name)
        shutil.copy2(work / f"ooc_{a.sol}.log", rep / "ooc_vivado.log")
        rc = rc or rc3
    if ("export" in a.steps or "impl" in a.steps) and (sol / "impl" / "ip").exists():
        ipd = sol / "impl" / "ip"
        dst = ROOT / "hls" / "ip"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ipd, dst)
        print(f"[hls] IP exported to {dst}")
    # surface errors
    errs = [ln for ln in text.splitlines()
            if (ln.startswith("ERROR") and "Failed to generate IP (patched" not in text)
            or "CRITICAL WARNING" in ln]
    for ln in errs[:15]:
        print("  " + ln)
    if any(ln.startswith("ERROR") for ln in errs) or "Synthesis failed" in text:
        print("[hls] FAILED (see log)")
        rc = rc or 1
    # design gates: the schedule must keep II = 4 and the OOC run must meet timing
    crpt = rep / "t2t_core_csynth.rpt"
    if crpt.exists():
        m = re.search(r"Latency.*?\|\s*min\s*\|\s*max\s*\|\s*min\s*\|\s*max\s*\|.*?\n.*?\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|",
                      crpt.read_text(errors="replace"), re.S)
        if m:
            ii = int(m.group(4))
            print(f"[hls] II = {ii}, pipeline latency = {m.group(2)} cycles")
            if ii > 4 and "T2T_COSIM" not in text:
                print("[hls] FAILED: II > 4 (line rate lost)")
                rc = rc or 1
    osum = rep / "ooc_summary.txt"
    if "impl" in a.steps and osum.exists():
        m = re.search(r"wns_ns\s+(-?[\d.]+)", osum.read_text())
        if m and float(m.group(1)) < 0:
            print(f"[hls] FAILED: OOC timing not met (WNS {m.group(1)} ns)")
            rc = rc or 1
    if "cosim" in a.steps:
        ok = "*** C/RTL co-simulation finished: PASS ***" in text
        print(f"[hls] cosim {'PASS' if ok else 'FAIL'}")
        if not ok:
            rc = rc or 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
