"""Driver for the bare-metal application build (make sw) with Xilinx SDK 2017.2 (xsct).

    python sw/build.py            bare metal  -> sw/bin/t2t_app.elf
    python sw/build.py --rtos     FreeRTOS 9  -> sw/bin/t2t_rtos.elf (live replay, docs/09)

Stages the application sources + shared core into a space-free SDK workspace, runs xsct
(sw/build_sdk.tcl) against hw/dk1_t2t.hdf, and copies the ELF + build log to sw/bin/.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.xilinx import ROOT, build_root, find_tool, run_tool  # noqa: E402


def main(argv=None) -> int:
    rtos = "--rtos" in (sys.argv[1:] if argv is None else argv)
    hdf = ROOT / "hw" / "dk1_t2t.hdf"
    if not hdf.exists():
        raise SystemExit("hw/dk1_t2t.hdf missing: run make vivado first")
    ws = build_root() / ("sdk_ws_rtos" if rtos else "sdk_ws")
    skip = "main.cpp" if rtos else "main_rtos.cpp"
    if ws.exists():
        shutil.rmtree(ws)
    (ws / "app_src").mkdir(parents=True)
    for f in (ROOT / "sw" / "app").glob("*"):
        if f.suffix in (".cpp", ".hpp", ".h") and f.name != skip:
            shutil.copy2(f, ws / "app_src" / f.name)
    osal = "osal_freertos.cpp" if rtos else "osal_baremetal.cpp"
    shutil.copy2(ROOT / "sw" / "osal" / osal, ws / "app_src" / osal)
    # relative includes: app/src/../../hls/src/core, app/src/../osal, hls/src/core/../../../proto
    shutil.copytree(ROOT / "hls" / "src", ws / "hls" / "src")
    (ws / "proto").mkdir()
    shutil.copy2(ROOT / "proto" / "dk1proto.hpp", ws / "proto" / "dk1proto.hpp")
    (ws / "t2t_app" / "osal").mkdir(parents=True)
    shutil.copy2(ROOT / "sw" / "osal" / "osal.hpp", ws / "t2t_app" / "osal" / "osal.hpp")
    # osal_baremetal.cpp includes "osal.hpp" from its own directory
    shutil.copy2(ROOT / "sw" / "osal" / "osal.hpp", ws / "app_src" / "osal.hpp")
    shutil.copy2(hdf, ws / "dk1_t2t.hdf")
    shutil.copy2(ROOT / "sw" / "build_sdk.tcl", ws / "build_sdk.tcl")
    xsct = find_tool("xsct", "SDK")
    log = ws / "sdk_build.log"
    print(f"[sdk] building in {ws} (log {log})")
    t0 = time.time()
    rc = run_tool([xsct, (ws / "build_sdk.tcl").as_posix(), ws.as_posix(), (ws / "dk1_t2t.hdf").as_posix(),
                   "freertos" if rtos else "standalone"], ws, log)
    text = log.read_text(errors="replace")
    elf = list((ws / "t2t_app").rglob("t2t_app.elf"))
    ffconf = list((ws / "bsp0").rglob("xparameters.h"))
    lfn = any("FILE_SYSTEM_USE_LFN" in f.read_text(errors="replace") for f in ffconf)
    if not lfn:
        print("[sdk] ERROR: BSP built without FatFs long-file-name support (use_lfn)")
    ok = "SDK_BUILD_DONE" in text and bool(elf) and lfn
    print(f"[sdk] exit {rc} after {time.time() - t0:.0f} s: {'OK' if ok else 'FAILED'}")
    for ln in [x for x in text.splitlines() if " error" in x.lower() or x.startswith("ERROR")][:20]:
        print("  " + ln)
    out = ROOT / "sw" / "bin"
    out.mkdir(exist_ok=True)
    shutil.copy2(log, out / ("sdk_build_rtos.log" if rtos else "sdk_build.log"))
    if elf:
        name = "t2t_rtos.elf" if rtos else "t2t_app.elf"
        shutil.copy2(elf[0], out / name)
        print(f"[sdk] ELF -> {out / name}")
    for init in (ws / "hw0").glob("ps7_init.tcl"):
        shutil.copy2(init, out / "ps7_init.tcl")  # used by sw/run_board.tcl
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
