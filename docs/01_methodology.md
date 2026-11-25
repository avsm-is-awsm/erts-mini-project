# 1. Methodology

The project follows a model-based HW/SW co-design flow. It runs from a SysML problem
description, through technology-independent logical models and architecture exploration, down
to an executable golden model, HLS, an FPGA implementation and on-target verification.
Every phase produces diagrams (PlantUML sources in `docs/diagrams`, rendered to `docs/img`) and
evidence files that later phases are checked against.

| # | Phase | Main artefacts | Evidence |
|---|---|---|---|
| 1 | Problem statement & context | D01 package diagram, D02 context BDD | — |
| 2 | Requirements | D03 use cases, D04 requirement diagram, [02_requirements](02_requirements.md) | traceability matrix |
| 3 | Behaviour | D05/D06 activities, D07–D09 state machines, D15/D16 sequences | executable in `ref/model.py` |
| 4 | Logical structure | D10 BDD, D11 IBD, D17 latency budget | — |
| 5 | Architecture exploration & allocation | D12/D13 physical, D14 allocation A/B/C, SystemC model | `systemc/out/` |
| 6 | Executable model & HLS verification | golden model, C++ core, csim, cosim, RTL sim | `hls/reports`, `build/rtlsim` |
| 7 | Implementation & on-target verification | Vivado design, PS application | `hw/reports`, board runs (`runs/board/`) |
| 8 | Evaluation | [08_results](08_results.md), requirement verdicts | this repository |

## From models to code

* **Behaviour → executable model.** The activity D05 and the state machines D07/D08 are
  implemented once as the reference model `ref/model.py`. That model is the *source of truth*:
  every other implementation must be bit-exact with it (timestamps masked).
* **Logical blocks → C++ modules.** The logical blocks of D10 map one-to-one onto
  `hls/src/core/*.hpp` (`order_table`, `price_ladder` = PriceLadder + BboTracker, `strategy`,
  `risk`, `engine` = FeedParser + OrderEncoder + Telemetry). The same C++ is
  * synthesised by Vivado HLS into the PL IP (architecture C),
  * compiled for the Cortex-A9 as the software baseline (architecture A),
  * compiled with g++ on the PC for C-simulation and the random cross-check.
* **Allocation → SystemC.** The PS/PL allocations of D14 are evaluated with a loosely-timed
  SystemC model (`systemc/arch_model.cpp`). Its parameters come from measured data (HLS
  report, RTL simulation) where available and are marked *assumed* otherwise. This answers
  "why C?" before building it, and is compared against the measurements afterwards.

## Relation to Gajski's process state machines

Each state machine of D07–D09 is a *process state machine* in the sense of Gajski et al.: a
set of states with sequential behaviour per state, communicating over channels.
* **Product-slot PSM (D07) and strategy PSM (D08):** these become a single process in hardware.
  The HLS pipeline evaluates all eight product PSMs in time multiplex (one message per 4
  cycles). Their state variables live in LUT-RAMs indexed by product, so the PSM "state" is data.
* **Session PSM (D09):** an explicit `SessionController` class on the ARM (State pattern).
* **Channels:** AXI4-Stream channels (`md_in`, `ord_out`, `dbg_out`) carry messages between
  the PS-side producer (DMA) and the PL process, and AXI-Lite carries configuration. These are
  exactly the item flows of D11, refined into the physical connections of D13.

The SystemC model applies the same PSM/channel view to the three allocations. It instantiates
the processes (`FeedSource`, `PlPipeline`, `ArmCpu`, `Delay` for DMA + IRQ) and binds them
with `sc_fifo` channels according to D14.

## Verification ladder

```
Python golden model ──(12k random sequences, all scenarios)──► C++ core (g++)
        │                                                          │ same source
        │                                                          ▼
        └──────────(cosim_10k, xsim)──────────────────────► HLS RTL (C/RTL cosim)
                                                                   │ exported IP
        golden ──(RTL system sim: IP + probes, Icarus)───────────► │
        golden ──(board: orders_hw.bin, compare.py)──────────────► ZYBO (bit-exact)
```

## Related work (state of the art)

* **Leber, Geib and Litz (2011)** [L1] offload the UDP stack and the FAST market-data decoding of
  a trading system to an FPGA (a microcoded FAST decoder at up to 160 MHz). Their focus is the
  feed handler; the order book and the strategy stay in software.
* **Lockwood et al. (2012)** [L2] describe a library of low-latency FPGA components for trading
  (network interface, feed handling, order book, order entry) so that a complete tick-to-trade
  path can be built in hardware.
* **Boutros, Grady, Abbas and Chow (2017)** [L3] are the closest to this project: a complete HFT
  system written with **Vivado HLS** on a Kintex UltraScale (XCKU115) board at 156.2 MHz, with
  Ethernet/UDP, a FAST decoder, a heap-based order book in BRAM (2¹² entries) and a trading
  algorithm. The trading subsystem takes 269 ns (42 cycles); the full round trip including the
  network stack is below 870 ns.

**Position of this project.** Like [L3] the engine is written in C++ and synthesised with HLS,
but it targets the smallest Zynq (XC7Z010) and a different market: the DK1 continuous intraday
power market, which has products with a gate closure, negative prices and an integer price grid.
The core latency of 280 ns (28 cycles at 100 MHz, constant, measured on the board) is of the same
order as the 269 ns trading subsystem of [L3], but this project has **no network front end**
(the feed is replayed from DDR by DMA), so it measures processing latency, not wire-to-wire
latency. What it adds compared with the cited work is the course's HW/SW co-design process: a
SysML model, an executable golden model with bit-exact verification at six levels, a SystemC
exploration of three HW/SW partitionings validated against the board, and a schedulability
analysis of the software side (§09).

## References

* S. Friedenthal, A. Moore, R. Steiner, *A Practical Guide to SysML*, 3rd ed., Morgan Kaufmann, 2014.
* D. D. Gajski, S. Abdi, A. Gerstlauer, G. Schirner, *Embedded System Design: Modeling, Synthesis and Verification*, Springer, 2009.
* K. Bjerge, "A HW/SW Codesign Methodology based on UML", Danish Technological Institute, 2008. Online: https://www.teknologisk.dk/_root/media/31292_A_HW_SW_CodesignMethodology.pdf (ERTS course literature).
* B. P. Douglass, *Design Patterns for Embedded Systems in C*, Newnes, 2010.
* AMD/Xilinx, *Vivado Design Suite User Guide: High-Level Synthesis* (UG902, 2017.2; successor UG1399).
* Nasdaq, *TotalView-ITCH 5.0 specification*.
* Nord Pool, *Product specifications Nordic and Baltic* (January 2026).
* [L1] C. Leber, B. Geib, H. Litz, "High Frequency Trading Acceleration Using FPGAs", *Proc. 21st Int. Conf. on Field Programmable Logic and Applications (FPL)*, 2011, doi:10.1109/FPL.2011.64.
* [L2] J. W. Lockwood, A. Gupte, N. Mehta, M. Blott, T. English, K. Vissers, "A Low-Latency Library in FPGA Hardware for High-Frequency Trading (HFT)", *IEEE Symp. on High-Performance Interconnects (HOTI)*, 2012.
* [L3] A. Boutros, B. Grady, M. Abbas, P. Chow, "Build fast, trade fast: FPGA-based high-frequency trading using high-level synthesis", *Int. Conf. on ReConFigurable Computing and FPGAs (ReConFig)*, 2017, pp. 1-6.
* C. L. Liu, J. W. Layland, "Scheduling algorithms for multiprogramming in a hard-real-time environment", *J. ACM* 20(1), 1973.
* M. Joseph, P. Pandya, "Finding response times in a real-time system", *The Computer Journal* 29(5), 1986.
* N. Audsley, A. Burns, M. Richardson, K. Tindell, A. Wellings, "Applying new scheduling theory to static priority pre-emptive scheduling", *Software Engineering Journal* 8(5), 1993.
* E. Bini, G. Buttazzo, G. Buttazzo, "Rate monotonic analysis: the hyperbolic bound", *IEEE Trans. Computers* 52(7), 2003.
* D. K. Gode, S. Sunder, "Allocative efficiency of markets with zero-intelligence traders", *J. Political Economy*, 1993.
* E. Smith, J. D. Farmer, L. Gillemot, S. Krishnamurthy, "Statistical theory of the continuous double auction", *Quantitative Finance*, 2003.
