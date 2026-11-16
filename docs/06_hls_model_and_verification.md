# 6. Executable model, HLS core and verification

## Golden reference model (`ref/model.py`)

* Implements SPEC §6 exactly, plus the interpretations in [DECISIONS](DECISIONS.md).
* It is deliberately *different* from the C++ core in representation, so a shared bug is
  unlikely. The table stores the order price, not the window offset; best bid/ask are tracked
  incrementally with rescans, not with bitmaps; the risk check uses the literal spec formulas,
  not the shallow hardware form (D-14).
* Numba-JIT: 5.5–8 M msg/s on the PC (`run_ref.py` prints the rate), far above the
  200 k msg/s target.
* Hand-checked case: `tests/hand_case.py`. It has 24 messages whose expected 10 order records,
  22 BBO records and counters were **derived by hand** in the comments, not by the model. Both the
  model and the C++ core reproduce them exactly.

## C++ core (`hls/src/core`, C++98)

One function, `step()`, handles a message *or* one soft-reset clear step. Every piece of state
then has exactly one store per invocation, which is what lets the HLS pipeline close at II = 4.
The micro-architectural transformations are equivalent rewrites of the spec semantics
(DECISIONS H-1..H-6):

| Problem found in synthesis | Fix |
|---|---|
| II = 28, 41 ns: helpers not inlined, 64-bit divide/modulo for the grid check | inline everything; division-free window offset + reciprocal multiply; grid check by residues (H-2) |
| merged stores of reset and message branches, all state loaded early / stored late | single `step()` for message and clear; per-product state in LUT-RAMs, split into an early and a late group (H-4, H-5) |
| 19.7 k LUT estimate: 875 wide muxes from per-product registers | LUT-RAMs instead of registers; counters as unconditional adds |
| bank muxes + 5-level priority encoders in the loop-carried BBO path | flattened ladder memories; per-word msb/lsb precomputed on write, both candidates computed in parallel (H-3) |
| anchor / live_count / table loops chained with the grid check and qty compares | flat dispatch logic; qty+meta in one 64-bit word; per-way parallel table update |
| risk loop: position subtract + min + priority select + add | equivalent shallow form (D-14) |

## HLS results (Vivado HLS 2017.2, xc7z010clg400-1, 10 ns target)

From `hls/reports/impl1/` (`make hls`; summary in `hls/reports/summary.json`):

| Metric | Value |
|---|---|
| II | **4** (line rate, one 32-byte message per 4 cycles) |
| Pipeline latency (csynth) | 28 cycles |
| HLS timing estimate | 11.05 ns (HLS model, pessimistic; post-route below) |
| Post-route OOC, `t2t_core` alone | **WNS +1.67 ns @ 10 ns** (≈ 120 MHz possible); 6 333 LUT (36.0 %), 9 544 FF (27.1 %), 32 BRAM36 (53.3 %), 3 DSP |

## Verification evidence

| Level | What | Result |
|---|---|---|
| Python ↔ C++ random cross-check | 12 000 adversarial sequences (2.5 M messages), default geometry and a tiny geometry (SETS = 4, W = 64); every error counter, all 4 reject reasons and both sides covered | bit-exact (`pytest tests/test_xcheck.py`) |
| C++ unit tests | `hls/tb/test_core.cpp`: hashing, collisions, full set, re-insert, ladder edges 0 / W−1, negative prices, OOW, residues (exhaustive sampling), HW bit encoders vs builtins, strategy dedup/lockout/kill/HALT, every risk reason, protocol pack/unpack, reset | 1 082 checks pass |
| csim (g++ + shim) | all 9 scenarios incl. `day_default` (4.69 M msgs) and `stress_burst` (1.37 M) + hand case | bit-exact, orders + BBO + counters |
| Vivado HLS csim | `cosim_10k` with the HLS 2017.2 toolchain | pass |
| **C/RTL cosim** (xsim) | `cosim_10k` incl. a full soft-reset sweep | **pass**, bit-exact |
| RTL probes (cocotb + Icarus, and xsim) | random gaps and backpressure, 6 000 beats per probe, TUSER/t_out exact, TLAST/TKEEP preserved | 3/3 pass |
| **RTL system simulation** | exported HLS RTL + probes + counter (as in the BD), AXI-Lite config + soft reset, `cosim_10k` and `edge_errors` at line rate, random backpressure, truncated transfers, kill switch | 4/4 tests; bit-exact; **28 cycles** for every order; 4.0 cycles/msg; fragments dropped and counted; kill honoured |

C/RTL cosim note: Vivado HLS cannot co-simulate an `ap_ctrl_none` design with II > 1, so cosim
uses the same datapath with `ap_ctrl_hs` (D-17). The cosim "latency" column reflects the
AXI-Lite start/done handshake of that wrapper, not the pipeline. The cycle-exact latency comes
from the RTL system simulation of the exported (`ap_ctrl_none`) IP with the timestamp probes.
