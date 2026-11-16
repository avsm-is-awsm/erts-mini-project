# 8. Results

Every number on this page comes from a run in this repository, and the command that produced
it is given next to it. **No on-board measurement exists yet.** The ZYBO was not connected to the
build PC; those rows are marked **NEEDS-USER**, and [07_implementation_zybo](07_implementation_zybo.md)
gives the procedure. Latency figures are therefore from cycle-accurate **RTL simulation of the
exported IP with the timestamp probes**, the same RTL that is in the bitstream.

Toolchain: Vivado / Vivado HLS / SDK 2017.2, xc7z010clg400-1 (original ZYBO), 100 MHz PL clock.
Generator version 1.0.0, scenario seeds in `data/scenarios/*.yaml`.

## R1. Correctness (bit-exact vs. golden, timestamps masked)

| Check | Scope | Result | Command |
|---|---|---|---|
| Python ↔ C++ random cross-check | 12 000 sequences, 2.53 M messages, 2 geometries; all 14 error counters, 4 reject reasons, both sides hit | identical | `pytest tests/test_xcheck.py` |
| Hand-derived expectations | 24 messages → 10 order, 22 BBO records, counters | model and C++ identical to hand values | `tests/hand_case.py`, `build/tb_t2t tests/hand_case build` |
| C++ unit tests | table, ladder, residues, encoders, strategy, risk, protocol, reset | 1 082 / 1 082 | `make test-cpp` |
| csim (g++ + shim) | all 10 datasets below (orders + BBO trace + counters) | all PASS | `make csim-all` |
| Vivado HLS csim | `cosim_10k` | PASS | `make hls` |
| C/RTL co-simulation (xsim) | `cosim_10k`: 12 004 msgs → 25 ord + 11 603 BBO records, after a 16 384-step soft reset | **PASS** (`*** C/RTL co-simulation finished: PASS ***`) | `make cosim`, `hls/reports/cosim1/` |
| RTL system simulation, line rate | `cosim_10k` (12 004 msgs), `edge_errors` (12 048), `stress_gc_rush` (968 538) | 0 mismatching records | `make rtl-system` |
| RTL system simulation, framing recovery (D-34) | 3 fragments cut by TLAST on beat 0/1/2, then the full `cosim_10k` / `edge_errors` stream | `err_framing` = 3, `msgs_in` exact, 0 mismatching records, latency still 28 | idem |
| RTL system simulation, random backpressure | `cosim_10k`, `edge_errors` | 0 mismatching records | idem |
| PS software on host (SW engine + virtual-board HW engine, failure injection) | hand case, unit_tiny, cosim_10k, edge_errors, edge_table_full, stress_burst; DMA error, dead IP, unwritable SD, bad datasets, CLI errors | 99 + 59 checks pass | `make sw-host-test` |
| On board: HW (dbg on) + SW | — | **NEEDS-USER** | §07 procedure, `tools/compare.py` |

## R2. Datasets and capacity / robustness scenarios

`make data-all ref-all` (golden counters from `ref/run_ref.py`):

| Scenario | Messages | Products | Fundamentals | 'O' | 'R' | Error counters (golden = csim = RTL) |
|---|---|---|---|---|---|---|
| `unit_tiny` | 1,335 | 2 | synthetic:default | 3 | 0 | none |
| `cosim_10k` | 12,004 | 3 | synthetic:default | 14 | 10 | none |
| `day_default` | 4,692,944 | 96 | synthetic:default | 2069 | 294 | none |
| `day_eds` | 4,707,049 | 96 | **real Energi Data Service DK1, 2026-01-15** | 2096 | 301 | none |
| `stress_burst` | 1,371,029 | 12 | synthetic:default | 182 | 63 | none |
| `stress_gc_rush` | 968,538 | 20 | synthetic:default | 337 | 68 | none |
| `edge_negative_prices` | 61,810 | 5 | synthetic:negative (+ anchors at ±9 990 EUR) | 55 | 17 | none |
| `edge_oow` | 62,601 | 5 | synthetic:default | 41 | 2 | oow 1280 |
| `edge_table_full` | 36,644 | 4 | synthetic:default | 29 | 2 | table_full 2913, not_found 3090 (> 4096 live orders + crafted set collisions) |
| `edge_errors` | 12,048 | 3 | synthetic:default | 4 | 0 | unknown_type 60, bad_product 60, not_found 60, dup_id 60, grid 40, seq_gap 40, qty_over 60 |

Generator realism (`python data/validate_stylized.py data/out/day_default`, plots `docs/img/data_day_default_*`):

| Stylised fact | day_default | day_eds |
|---|---|---|
| spread p50 / p90 | 0.30 / 1.00 EUR | 0.30 / 1.00 EUR |
| crossed book after matching | 0 % | 0 % |
| message rate, last 5 min vs window mean | 2.45× | 2.45× |
| depth at best (median) | 3.8 MW | 3.8 MW |
| \|trade − imbalance price\| first 10 % → last 10 % of window | 5.38 → 0.31 EUR | 29.5 → 0.55 EUR |
| order lifetime (median) | 3.3 s | 3.3 s |
| OOW / grid errors | 0 % / 0 % (< 0.5 % required) | 0 % / 0 % |

![convergence](img/data_day_default_convergence.png) ![rate](img/data_day_default_rate_vs_gc.png)

## R3. Fast-path latency and throughput

| Source | Orders | Latency t_out − t_in | Jitter | Input rate | Command |
|---|---|---|---|---|---|
| HLS csynth schedule | — | 28 cycles (pipeline) | — | II = 4 | `make hls` |
| **RTL system sim**, `cosim_10k`, no backpressure | 14 | **28 cycles = 280 ns** (min = p50 = p99 = max) | **0** | 4.0 cycles/msg = **25 M msg/s** | `make rtl-system`, `tools/analyze.py build/rtlsim/cosim_10k` |
| **RTL system sim**, `stress_gc_rush` (968 538 msgs), no backpressure | 337 | **28 cycles = 280 ns** for every order (min = p99 = max); 68 rejects also 28 | **0** | 4.0 cycles/msg over 3.87 M cycles | `DK1_SYS_DS=stress_gc_rush DK1_SYS_TESTS=line_rate pytest rtl/tests/test_system.py` (21 min) |
| Board probes (HW) | | **NEEDS-USER** | | `'Z'` record + wall clock incl. DMA gaps | `make analyze RUN=…` |
| Board SwEngine (SW, Arch A) | | **NEEDS-USER** (SystemC assumes ~1.05 µs) | | | idem |

![latency](img/latency_hist_rtlsim_cosim_10k.png)

## R4. Resources and timing

| Level | LUT | FF | BRAM36 | DSP | Timing @ 10 ns | Source |
|---|---|---|---|---|---|---|
| HLS estimate (`t2t_core`) | 16 467 | 19 199 | 32 (64 BRAM18) | 5 | 11.05 ns (HLS model) | `hls/reports/impl1/t2t_core_csynth.rpt` |
| Post-route, OOC, `t2t_core` only | 6 333 (36.0 %) | 9 544 (27.1 %) | 32 (53.3 %) | 3 | **WNS +1.665 ns** | `hls/reports/impl1/ooc_*.rpt` |
| **Post-route, whole system** (PS7, 2 DMAs, interconnects, probes, IP) | **10 472 (59.5 %)** | 15 123 (43.0 %) | **36.5 (60.8 %)** | 3 | **WNS +0.969 ns, WHS +0.017 ns** | `hw/reports/utilization.rpt`, `timing_summary.rpt` |

`python tools/parse_hls_reports.py` collects these into `hls/reports/summary.json`.

## R5. Kill switch (NFR-SAF-1)

RTL system simulation, `rtl/tests/test_system.py::kill_switch`. `CTRL.kill` is written over
AXI-Lite in the middle of the stream, right after the message that triggers the middle golden
order. The test records the cycle in which the write response completes:

| Dataset | Orders before kill | Orders whose first beat arrived ≥ 10 cycles after the write | Result |
|---|---|---|---|
| `cosim_10k` | 8 | 0 | PASS |
| `edge_errors` | 3 | 0 | PASS |

## R6. Architecture exploration: SystemC predictions vs. measurements

`make sysc` → `systemc/out/summary.json` (parameters in `systemc/params.yaml`, measured or assumed):

| | A all-SW | B split | C all-HW |
|---|---|---|---|
| replay (board setup), day_default | 1.05 µs *(assumed ARM cost)* | ARM saturates (p50 4.6 ms) | 280 ns |
| live (exchange timestamps), day_default | p50 3.75 µs, p99 7.35 µs | p50 2.01 µs, p99 2.55 µs | p50 400 ns, p99 560 ns |
| **measured** | NEEDS-USER (board SwEngine) | not built (model only) | **280 ns** (RTL sim, replay) |

C: the replay prediction equals the measurement, because the model's PL latency parameter
*is* the RTL measurement and the line rate equals the II. Agreement is therefore a consistency
check, not a validation. The model's live-mode tail (560 ns) comes from same-timestamp bursts
queueing at 4 cycles per message. A: to be validated against the board `run sw` latency;
re-run `make sysc` with the measured p50 in `params.yaml` and record the discrepancy here.

![SystemC](img/sysc_arch_day_default.png)

## R7. Latency sensitivity: fill rate and P&L vs. latency (headline chart)

`python ref/evaluate.py data/out/day_default` (simulated exchange, IOC, no market impact, P&L
vs. the delivery period's imbalance price):

| Latency | 0 | 100 ns | 1 µs | 10 µs | 100 µs | 1 ms | 10 ms | 100 ms |
|---|---|---|---|---|---|---|---|---|
| fill rate (day_default) | 95.2 % | 95.2 % | 95.2 % | 95.2 % | 38.8 % | 33.2 % | 33.1 % | 32.1 % |
| P&L EUR (day_default) | 1 298 | 1 298 | 1 298 | 1 293 | 458 | 294 | 290 | 298 |
| fill rate (day_eds, real fundamentals) | 93.7 % | 93.7 % | 93.7 % | 93.6 % | 36.9 % | 31.4 % | 31.3 % | 30.8 % |
| P&L EUR (day_eds) | 10 274 | 10 274 | 10 274 | 10 255 | 591 | −1 181 | −1 196 | −1 011 |

![headline](img/pnl_vs_latency_day_default.png)

The simulated competitors react after 50 µs–50 ms, so the opportunities are gone between 10 µs
and 100 µs. The fabric latency (280 ns) sits on the flat, profitable plateau, and the software
baseline (~1 µs, assumed) does too. The difference that matters is to network-attached or
millisecond-class systems. Synthetic data, simplified exchange: this illustrates latency
sensitivity, not real profitability.

## Requirement verdicts

| Req | Verdict | Evidence |
|---|---|---|
| FR-01 parse / skip unknown | **PASS** | R1 (xcheck), R2 `edge_errors` |
| FR-02 order table + ladder | **PASS** | R1, `test_core` |
| FR-03 BBO after every product message | **PASS** | bit-exact `'B'` trace in R1 (csim, cosim, RTL, host) |
| FR-04 strategy | **PASS** | hand case, `test_core.test_strategy`, R1 |
| FR-05 pre-trade risk | **PASS** | `test_core.test_risk` (all reasons), xcheck coverage |
| FR-06 kill switch | **PASS** | `test_core`, R5 |
| FR-07 AXI-Lite config + counters | **PASS (sim)** | RTL system sim configures via AXI-Lite, reads STATUS; host proxy tests |
| FR-08 order format + timestamps | **PASS** | R1, R3 |
| FR-09 debug BBO trace | **PASS** | R1 |
| FR-10 identical core on the ARM | **PASS (host + ELF built)**, board NEEDS-USER | `sw-host-test`, `sw/bin/t2t_app.elf` |
| FR-11 product lifecycle / slot reuse | **PASS** | 96 products on 8 slots in `day_default`, generator invariants |
| NFR-LAT-1 p99 ≤ 64 cycles (stretch ≤ 32) | **PASS (RTL sim): 28 cycles**; board NEEDS-USER | R3 |
| NFR-LAT-2 jitter ≤ 8 cycles | **PASS (RTL sim): 0 cycles**; board NEEDS-USER | R3 |
| NFR-THR-1 1 msg / 4 cycles | **PASS (RTL sim): 4.0 cycles/msg** | R3, HLS II = 4 |
| NFR-THR-2 no loss on `stress_burst` | **PASS (csim + virtual board)**, board NEEDS-USER | R1 |
| NFR-CAP-1 capacity | **PASS** | R2 edge scenarios, `test_core` |
| NFR-COR-1 bit-exact | **PASS** | R1 |
| NFR-RES-1 ≤ 70 % LUT/BRAM, timing met | **PASS**: 59.5 % LUT, 60.8 % BRAM, WNS +0.969 ns | R4 |
| NFR-ROB-1 robustness | **PASS** | `edge_errors` csim + RTL sim (incl. backpressure), xcheck; truncated transfers recovered (RTL `framing_recovery`) |
| NFR-SAF-1 kill latency | **PASS (RTL sim)** | R5 |
| NFR-DET-1 determinism | **PASS** | `test_generator.py::test_deterministic`; integer-only core |
