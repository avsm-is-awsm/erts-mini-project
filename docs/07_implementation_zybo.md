# 7. Implementation on the ZYBO

Board: **original Digilent ZYBO** (XC7Z010-1CLG400C, 512 MB DDR3), with Vivado / Vivado HLS /
SDK **2017.2**. See [hw/README](../hw/README.md) and [sw/README](../sw/README.md) for the
build steps.

## Block design (`hw/build_bd.tcl`)

![D13](img/D13_ibd_physical.svg)

* PS7 with the ZYBO board preset, FCLK0 = 100 MHz, GP0 master, HP0 64-bit slave, IRQ_F2P.
* `axi_dma_0`: MM2S → `axis_ts_in` → `t2t_core.md_in`; `t2t_core.ord_out` → `axis_ts_out` → S2MM.
* `axi_dma_1`: S2MM ← `t2t_core.dbg_out` (BBO trace, 4 MB packets).
* `ts_counter` (64-bit) → `[31:0]` → both probes. Probes are module references (plain Verilog).
* AXI-Lite map: `axi_dma_0` 0x4040_0000, `axi_dma_1` 0x4041_0000, `t2t_core` 0x43C0_0000.
* The HP0 interconnect is wired explicitly: the 2017.2 `axi4` automation rule fails for the DMA
  masters.
* AXI DMA v7.1 (2017.2) allows at most a 23-bit length register (8 MB − 1). The PS therefore
  streams the input in 4 MB chunks, and the IP terminates debug packets every 131 072 records (D-29/D-30).

## Implementation results (`hw/reports/`, `make vivado`)

| Metric | Value |
|---|---|
| Timing @ 100 MHz | **met**: WNS **+0.969 ns**, WHS +0.017 ns (post-route, whole system) |
| Slice LUTs | 10 472 / 17 600 (**59.5 %**) |
| Slice registers | 15 123 / 35 200 (43.0 %) |
| Block RAM tiles | 36.5 / 60 (**60.8 %**) |
| DSPs | 3 / 80 |
| Build time | about 7 min (synthesis + implementation + bitstream, 6 jobs) |

Outputs: `hw/dk1_t2t.bit`, `hw/dk1_t2t.hdf` (SDK 2017.2 hardware handoff; Vitis would use .xsa),
`hw/address_map.txt`, `hw/system_bd_export.tcl`.

The build fails (and publishes no bitstream) if timing is not met. A sub-run that hangs at
Vivado start-up is killed by the build driver and launched again (D-40).

## PS application (`sw/`, `make sw`)

Built with xsct / SDK 2017.2 against the HDF: standalone BSP + `xilffs`, C++ application
`t2t_app.elf` (text 272 KB, bss 673 KB; FatFs with long file names, 256 KB stack and heap). UART CLI at 115 200 baud:

```
t2t> ls
t2t> load day_default
t2t> cfg set dbg_en 0
t2t> run both
t2t> stats
```

## On-board measurement procedure (NEEDS-USER, SPEC 10.5)

1. Copy `data/out/<name>/{feed.bin,config.txt}` for the wanted datasets onto the microSD
   (FAT32, folder per dataset). Use `day_default` (4.69 M msgs, 150 MB) and a ≤ 1.5 M-message
   set for debug runs.
2. Program the FPGA with `hw/dk1_t2t.bit` and run `sw/bin/t2t_app.elf` (SDK: *Run As → Launch
   on Hardware*, or `xsct sw/run_board.tcl`). Open the UART at 115 200 8N1.
3. `load day_default`, `cfg set dbg_en 0`, `run hw`: throughput and latency (orders only).
4. `load cosim_10k` (or `stress_burst`), `cfg set dbg_en 1`, `run hw`: full BBO-trace verification.
5. `run sw` on the same datasets: Architecture A baseline.
6. Copy the SD outputs back to `runs/<board-date>/<dataset>/` and run
   `make analyze RUN=runs/<board-date>/<dataset> SCEN=<dataset>`. This writes `compare.py`
   verdicts, latency histograms and throughput into `latency_summary.json`, and updates the plots.

The board was not connected to this PC (an `xsct` JTAG scan found no targets), so no on-board
numbers exist yet. All results in §08 are from simulation and implementation reports, and are
labelled as such.
