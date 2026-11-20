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

## References

* S. Friedenthal, A. Moore, R. Steiner, *A Practical Guide to SysML*, 3rd ed., Morgan Kaufmann, 2014.
* D. D. Gajski, S. Abdi, A. Gerstlauer, G. Schirner, *Embedded System Design: Modeling, Synthesis and Verification*, Springer, 2009.
* "A HW/SW Co-design Methodology based on UML", ERTS course literature (full citation: *to be added by the author*).
* B. P. Douglass, *Design Patterns for Embedded Systems in C*, Newnes, 2010.
* AMD/Xilinx, *Vivado Design Suite User Guide: High-Level Synthesis* (UG902, 2017.2; successor UG1399).
* Nasdaq, *TotalView-ITCH 5.0 specification*.
* Nord Pool, *Product specifications Nordic and Baltic* (January 2026).
* D. K. Gode, S. Sunder, "Allocative efficiency of markets with zero-intelligence traders", *J. Political Economy*, 1993.
* E. Smith, J. D. Farmer, L. Gillemot, S. Krishnamurthy, "Statistical theory of the continuous double auction", *Quantitative Finance*, 2003.
