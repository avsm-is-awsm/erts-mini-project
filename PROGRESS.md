# PROGRESS

Milestone tracker (SPEC §14). Status: **DONE** (acceptance criteria verified here),
**DONE (sim)** (verified in simulation; board part pending), **NEEDS-USER** (needs the physical
board or the user's action).

Environment: Windows 11, Vivado / Vivado HLS / SDK 2017.2 (local), CLion MinGW g++ 15.2,
Python 3.14, Icarus Verilog 12, SystemC 2.3.4 (built locally with Windows fibers), PlantUML 1.2025.4.
Board: original ZYBO (confirmed at M0), **not attached** to this PC (`xsct` JTAG scan: no targets).

| # | Milestone | Status | Verified | Open |
|---|---|---|---|---|
| M0 | Scaffold, §1.1 confirmed, CI skeleton | DONE | layout, venv, Makefile, `.github/workflows/ci.yml`, DECISIONS, this file. A1 original ZYBO, A2 2017.2 local, A3 PlantUML, A4 bare-metal OSAL, A5 English | CI workflow written but not run (repo is not on GitHub yet) |
| M1 | Protocol | DONE | `proto/*`, `pytest tests/test_proto.py` (9 tests; Python ↔ C++ round trip on 20 k random records of every kind) | — |
| M2 | Generator + fundamentals + scenarios | DONE | 10 datasets generated (`make data-all`, plus `day_eds` from real EDS data); invariants (`tests/test_generator.py`, plus a replay of 5.9 M messages: no crossed book, no unknown ids, lifecycle ok); stylised-facts plots; `day_default` = 4.69 M msgs | — |
| M3 | Reference model + evaluate | DONE | golden outputs for all scenarios; hand-checked case `tests/hand_case/` committed (24 msgs, expected outputs derived by hand); P&L / latency sweep plot | — |
| M4 | C++ core + HLS top + csim | DONE | bit-exact csim on **all** scenarios; `test_core` 1 082 checks; random cross-check 12 000 sequences | — |
| M5 | Vivado HLS synth + cosim | DONE | II = 4, latency 28 cycles, timing met post-route (OOC WNS +1.67 ns); HLS csim pass; C/RTL cosim pass on `cosim_10k`; reports parsed (`hls/reports/summary.json`) | — |
| M6 | RTL probes | DONE | cocotb + Icarus 3/3 (random backpressure); same TB on xsim; RTL system simulation of IP + probes (4 tests) | — |
| M7 | Vivado BD + bitstream | DONE | `hw/dk1_t2t.bit`, `.hdf`; timing met (WNS +0.969 ns); 59.5 % LUT, 60.8 % BRAM | — |
| M8 | PS app + SW baseline | DONE (host) / **NEEDS-USER** (board) | host tests (99 checks + 59 on stress_burst); ELF built with SDK 2017.2 (`sw/bin/t2t_app.elf`) | on board: `run both` on `day_default`, bit-exact vs golden (HW dbg on a ≤ 1.5 M subset, SW full) |
| M9 | Analysis + results | DONE (sim) / **NEEDS-USER** | `docs/08_results.md` from real runs: RTL sim latency, implementation, SystemC, P&L; every NFR has a verdict | board latency/throughput rows |
| M10 | SysML model + docs + README | DONE | D01–D18 render (`make docs`), docs 01–08, traceability, README | course-paper citation to add |
| M11 | SystemC A/B/C model | DONE (model) / NEEDS-USER (A validation) | `make sysc`; predictions for A/B/C, replay and live; C prediction = RTL measurement | replace assumed ARM costs with the board SwEngine measurement, then compare A |
| M12 | ITCH converter + `itch_sample` on board | partial | converter + independent counter, test on a synthetic ITCH 5.0 stream (`tests/test_itch.py`) | **NEEDS-USER**: download a Nasdaq ITCH 5.0 sample (multi-GB), run `data/itch2dk.py`, compare counts with an open-source parser, run on board |
| S1–S4 | stretch | not started | — | — |

## What the user needs to run (board)

1. Copy `data/out/<dataset>/{feed.bin,config.txt}` folders (at least `day_default`,
   `cosim_10k`, `stress_burst`) to a FAT32 microSD card and insert it in the ZYBO. Set the boot
   jumper to JTAG and connect USB (PROG/UART).
2. `xsct sw/run_board.tcl` (programs `hw/dk1_t2t.bit` and runs `sw/bin/t2t_app.elf`). Open the
   UART at 115 200 8N1.
3. On the UART:
   * `load cosim_10k`, `cfg set dbg_en 1`, `run both`;
   * `load stress_burst`, `cfg set dbg_en 1`, `run both`;
   * `load day_default`, `cfg set dbg_en 0`, `run both`.
4. Copy each dataset folder back from the SD card into `runs/board/<dataset>/`, then for each one run
   `make analyze RUN=runs/board/<dataset> SCEN=<dataset>`.
5. Put the measured SW p50 into `systemc/params.yaml` (`arm_msg_ns`), run `make sysc`, and fill the
   board rows of `docs/08_results.md` and the README table.

## Robustness audit (2026-11-15/16)

Code audit after M12; details in DECISIONS D-34..D-39. Found and fixed:

* **Board blocker:** the BSP built FatFs without long file names, so `load day_default` and
  every result file would have failed on the SD card. Stack/heap were 8 KB.
* IP: a transfer ending inside a message could stall or misalign the IP until a hard reset.
  It now drops the fragment and counts it (`err_framing`). II = 4, latency 28 unchanged.
* PS: runs now fail fast with a reason (DMA error, reset not effective, missing `'Z'`, message
  count mismatch, order buffer overflow, SD write failure) instead of timing out or passing
  silently; datasets without a final END are rejected; `run both` survives a hardware failure.
* Builds fail on II > 4, on timing violations (HLS OOC and Vivado), and on a register map that
  does not match the software. A Vivado sub-run that hangs at start-up (seen twice here) is
  killed by a watchdog and launched again instead of blocking the build forever.
* Python: config and scenario validation, 64-bit ITCH order references, an orders/feed mismatch
  check in `evaluate.py`, a structural check in `compare.py`.

Re-verified after the changes: 26 Python tests, 1 082 core checks, csim on all scenarios, 12 000
random sequences, 99 + 59 host checks, HLS csim + C/RTL cosim, RTL system simulation (4 tests on
`cosim_10k` and `edge_errors`, line rate on `stress_gc_rush`: 968 538 messages, 337 orders, all at
28 cycles, 0 mismatches), bitstream and ELF rebuilt (timing met, WNS +0.969 ns).

## Open issues / notes

* Vivado HLS 2017.2 needs a space-free project path, C++98, non-zero AXIS sideband widths, a
  patched IP revision and valid stdio handles. All of this is handled by `tools/xilinx.py`,
  `hls/scripts/hls.py`, `hw/build.py` and `sw/build.py` (DECISIONS D-17..D-20).
* AXI DMA 7.1 in 2017.2 is limited to 8 MB per transfer, so the input is chunked (4 MB) and debug
  packets are TLAST-terminated every 131 072 records (D-29/D-30).
* The repository is a local git repo (branch `main`), with no remote yet. The CI workflow will run
  once it is pushed to GitHub.
