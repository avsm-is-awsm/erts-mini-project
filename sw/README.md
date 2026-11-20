# sw/ — PS bare-metal application (Part 2 of the Two-Part Architecture)

| Path | Content |
|---|---|
| `app/` | C++ application: `T2tProxy`, `ConfigManager`, `HwEngine` / `SwEngine`, `SessionController`, `ResultWriter`, `Cli`, board drivers (`board.cpp`), `main.cpp`, generated `t2t_regs.h` |
| `osal/` | OS abstraction layer: `osal.hpp`, bare-metal (`osal_baremetal.cpp`) and host (`osal_host.cpp`) adapters |
| `host_test/` | PC unit tests with mocks and a *virtual board* (real HLS top behind mock DMA) |
| `third_party/osal_course/` | place for the course OS package if it becomes available (not used: A4 = no) |
| `build_sdk.tcl`, `build.py` | xsct / SDK 2017.2 batch build (`--rtos`: FreeRTOS build with the live replay, docs/09) |
| `run_board.tcl` | program FPGA + run the ELF over JTAG |
| `bin/` | `t2t_app.elf`, `ps7_init.tcl`, build log |

```bash
make sw-host-test   # PC: g++ host tests (no Xilinx tools)
make sw             # SDK 2017.2: BSP (standalone + xilffs) + application -> sw/bin/t2t_app.elf
xsct sw/run_board.tcl   # with the ZYBO on USB (JTAG + UART)
```

The OSAL used is the bare-metal implementation (no course OS package was supplied). It is
single-threaded with polled DMA completion; the IRQ lines are wired in hardware for a future
interrupt-driven variant.

UART CLI (115 200 8N1): `help`, `ls [dir]`, `load <dir>`, `cfg show`, `cfg set <key> <value>`
(`ctrl`, `strat_en`, `dbg_en`, `kill`, `clip_qty`, `max_order_qty`, `max_pos`, `collar`,
`min_gap_ns`, `max_orders`, `lockout_ms`), `run hw|sw|both`, `stats`, `kill [on|off]`, `reset`.
A failed run prints its reason (`hw run FAILED: ...`) and leaves the dataset loaded.

The SD card must be FAT32; long file names are enabled in the BSP. `feed.bin` must end with
`H(END_OF_SESSION)` (all generated datasets do).

Outputs written next to the dataset on the SD card: `orders_hw.bin`, `dbg_hw.bin`,
`counters_hw.json`, `run_info_hw.json`, and the same with `_sw` for the software baseline.
