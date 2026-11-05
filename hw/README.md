# hw/ — Vivado block design, bitstream, hardware handoff

Target: original **ZYBO** (XC7Z010-1CLG400C), board files `digilentinc.com:zybo:part0:1.0`
(ship with Vivado 2017.2), Vivado **2017.2**.

```bash
make hls        # Vivado HLS: csim + csynth + export IP to hls/ip (+ OOC place & route report)
make vivado     # this folder: block design + synth + impl + bitstream + .hdf
```

`hw/build.py` stages `rtl/`, `hls/ip/` and `build_bd.tcl` into a space-free build folder
(`%LOCALAPPDATA%/dk1-t2t-build`, override with `T2T_BUILD_ROOT`), runs Vivado in batch mode and
copies back:

| File | Content |
|---|---|
| `dk1_t2t.bit` | bitstream |
| `dk1_t2t.hdf`, `dk1_t2t.sysdef` | SDK 2017.2 hardware handoff (Vitis 2019.2+ would use `.xsa`) |
| `address_map.txt` | AXI-Lite address map seen by the PS |
| `system_bd_export.tcl` | `write_bd_tcl` export of the block design |
| `reports/utilization*.rpt`, `timing_summary.rpt`, `power.rpt`, `summary.txt` | post-route reports |

Options: `python hw/build.py --ila` adds an ILA on the probe stream valids and the timestamp,
and `--bd-only` validates the block design without building.

Windows notes:
* Vivado 2017.2 needs a space-free project path (this repo lives in "ERTS Mini Project").
* It also needs valid standard handles. `tools/xilinx.py` launches it with a null stdin and
  piped stdout; see DECISIONS D-18..D-20.
