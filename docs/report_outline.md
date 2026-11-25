# Report outline: assignment steps → docs, diagrams, evidence

| Assignment step | Report section | Source docs | Diagrams | Evidence |
|---|---|---|---|---|
| 1. SysML/UML HW/SW co-design: problem, context, behaviour, structure | Introduction, System model | [01](01_methodology.md), [03](03_system_model.md) | D01, D02, D05–D11, D15–D17 | `ref/model.py` (executable behaviour) |
| 2. Requirements | Requirements | [02](02_requirements.md) | D03, D04 | traceability matrix, [08 verdicts](08_results.md#requirement-verdicts) |
| 3. Architecture alternatives and allocation | Architecture exploration | [04](04_architecture_alternatives.md) | D12–D14 | `systemc/out/summary.json`, `docs/img/sysc_arch_*.png` |
| 4. Design patterns | Software design | [05](05_sw_design_patterns.md) | D18 | `sw/app`, `sw/host_test` (207 checks) |
| 5. HLS model + testbench | HLS design and verification | [06](06_hls_model_and_verification.md), [DECISIONS](DECISIONS.md) | D17 | `hls/reports/*`, `tests/test_xcheck.py`, `hls/tb/*`, `rtl/tests/*` |
| 6. Implementation on the ZYBO with an HLS IP core | Implementation and results | [07](07_implementation_zybo.md), [08](08_results.md) | D13, D16 | `hw/reports/*`, `sw/bin/*`, board runs (`runs/board/`) |
| Course topic: scheduling analysis (RMA) | Real-time analysis | [09](09_realtime_analysis.md) | — | `runs/rt/*`, `docs/img/rta_*.png` |
| Course objective: performance, cost, power, area | Results | [08 R4, R8](08_results.md) | — | `hw/reports/utilization.rpt`, `hw/reports/power.rpt` (estimate) |
| State-of-the-art research | Related work | [01](01_methodology.md#related-work-state-of-the-art) | — | [L1]–[L3] |

Data and evaluation (supporting all steps): generator realism plots `docs/img/data_*`, the latency
sweep `docs/img/pnl_vs_latency_*`, and the honesty statements in the README.

Course methodology reference: K. Bjerge, "A HW/SW Codesign Methodology based on UML", Danish Technological Institute, 2008. Online: https://www.teknologisk.dk/_root/media/31292_A_HW_SW_CodesignMethodology.pdf (ERTS course literature).
