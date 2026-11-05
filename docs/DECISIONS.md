# Design decisions and spec interpretations

Every interpretation of an ambiguity in `SPEC.md`, and every deliberate deviation, is listed
here and applied identically in **all** implementations (Python reference `ref/model.py`, C++
core `hls/src/core/*`, HLS IP, ARM SwEngine). Code comments refer to these IDs (`D-xx`).

## Platform / toolchain (confirmed with the user at M0, 2026-11-05)

| ID | Decision | Why |
|---|---|---|
| D-01 | Board is the **original ZYBO** (XC7Z010-1CLG400C, 512 MB DDR3), not the Z7-10. Same FPGA, half the DDR. Vivado board files `digilentinc.com:zybo:part0:1.0` (rev B.3, shipped with Vivado 2017.2). | User's board; matches installed board files. DDR budget: board datasets ≤ 6 M messages (192 MB) instead of 8 M. |
| D-01b | Toolchain is **Vivado / Vivado HLS / SDK 2017.2** (installed locally). HLS, cosim, bitstream and the bare-metal build run on this PC; only on-board runs are `NEEDS-USER`. | User's installed tools. |
| D-01c | Shared C++ (core, protocol, testbenches) is **C++98**. Vivado HLS 2017.2's front end (clang 3.1 + libstdc++ 4.5) fails on `-std=c++0x`. Fixed-width ints come from `<stdint.h>`. | Tool limitation; the code also builds as C++11/14 with modern g++/Vitis. |
| D-02 | AXIS types are `ap_axiu<64,32,1,1>` (input) and `ap_axiu<64,1,1,1>` (outputs) instead of `ap_axiu<64,32,0,0>` / `<64,0,0,0>`. The 1-bit TUSER/TID/TDEST are tied to 0. | HLS 2017.2 rejects zero-width sideband fields. |
| D-17 | **C/RTL co-simulation uses `ap_ctrl_hs`** (`-DT2T_COSIM_CTRL_HS`), the exported IP uses `ap_ctrl_none`. The datapath and schedule are the same. | Vivado HLS cannot co-simulate an `ap_ctrl_none` design whose II > 1. |
| D-18 | Vivado/HLS builds run in a space-free staging folder (`%LOCALAPPDATA%/dk1-t2t-build`, override `T2T_BUILD_ROOT`) and results are copied back into the repo. | HLS 2017.2 refuses project paths containing spaces ("ERTS Mini Project"). |
| D-19 | The HLS-generated IP packaging script is patched with a valid `core_revision` (`YYMMDD`) and re-run. `export_design -flow impl` is replaced by our own Vivado out-of-context synth + place & route (`hls/scripts/ooc_impl.tcl`). | HLS ≤ 2019 builds `core_revision` from the date as `YYMMDDHHMM`, which overflows a 32-bit int since 2022 (AMD AR 76960). |
| D-20 | Vivado is launched with a null stdin and without Git-Bash variables (`PWD`, `SHELL`, …). | Without valid standard channels, Vivado's Tcl adopts the first opened file as stdin, so `synth_design` fails with "couldn't read file …: No error". |
| D-21 | Bare-metal OSAL (no course OSAL package). | User has no course package (A4). |

## Processing semantics (SPEC §6)

| ID | Decision |
|---|---|
| D-03 | **Meaning of "stop" in §6.4 step 4.** A dispatch error (dup id, off grid, table full, not found, bad state code) ends the dispatch and **skips step 6** (strategy/risk). The `'B'` record (step 5) is still emitted for every A/X/E/M/S/V message with a valid product, so §5.3's "one per input message …" holds. Errors in steps 2/3 (unknown type, bad product) emit nothing. |
| D-04 | The `NONE` sentinel of `last_buy_px` / `last_sell_px` is a separate valid flag, not a magic price, so every int32 price is representable. |
| D-05 | All comparisons on prices, fair value, edge, collar, room and time are **exact** (64-bit / Python ints). Ladder quantities `lq` wrap modulo 2³² (`lq += qty`, `lq -= r` on uint32). Bitmap bit = `lq != 0` after every update. |
| D-06 | `'R'` (reject) records carry `client_order_id = 0`; rejects do not consume ids. `'R'.qty` is the candidate qty after `CLIP_QTY` and before the risk clamps. |
| D-07 | Golden comparison masks bytes 24..31 (`t_in`, `t_out`) of every record and additionally bytes 16..23 (`first_t_in`, `last_t_in`) of `'Z'` records. Golden files write 0 there. |
| D-08 | `MIN_GAP_NS == 0` disables the RATE check entirely. Otherwise `RATE` ⇔ `has_ordered && (ts < last_order_ts || ts − last_order_ts < MIN_GAP_NS)`, i.e. the spec formula on signed exact integers. |
| D-09 | Lockout is evaluated exactly as `gc_ts > ts && gc_ts − ts > LOCKOUT_MS·10⁶`, which equals `ts + LOCKOUT_MS·10⁶ < gc_ts` without overflow. |
| D-10 | **The order table key is (`order_id`, `product`)**, following FR-02 ("per-product L3 order table"). The set index is still `fold(order_id) & (SETS−1)` (§6.3). An X/E/M whose product differs from the resting order's product is `err_not_found`; an Add with an id that is live only on another product is not a duplicate. All ladder and `live_count` updates therefore stay on the message's product. |
| D-11 | `'S'` with a state code > 3 is a dispatch error counted in a new counter **`err_bad_state`** (also exposed over AXI-Lite). State, anchor and late-group state are unchanged. |
| D-12 | `'S'(PREOPEN)` with live orders counts `err_anchor` **and still** resets `fv_valid`, `pos`, `last_*_px` and sets the state. `'S'(OPEN)` from PREOPEN with live orders counts `err_anchor` and keeps the old anchor/gc but sets the state. |
| D-13 | Strategy candidate qty = `min(level_qty, CLIP_QTY)` (uint32). The `elif` means SELL is only considered when the BUY branch is not taken (including the dedup case). |
| D-14 | Risk step 3 is implemented in the C++ core with the equivalent shallow form `reject ⇔ q == 0 ∨ ¬(BUY ? pos < MAX_POS : pos > −MAX_POS)`, new position `BUY ? min(pos+q, MAX_POS) : max(pos−q, −MAX_POS)`. The Python model keeps the literal spec formula; the cross-check proves equality, including negative and extreme `MAX_POS`. |
| D-15 | `'H'` is not subject to the product check. `'H'` without END does nothing (no record). |
| D-16 | `'Z'` is emitted after the END message is counted: `msgs_in` includes it and `seq` is its seq. Processing continues normally after END (a second session without reset continues the counters). `first_t_in` = `t_in` of the first message after reset, `last_t_in` = `t_in` of the latest message. |
| D-22 | Sequence check: `last_seq != 0 && seq != last_seq + 1 (mod 2³²)`. A message with `seq == 0` therefore disables the next check (spec formula as written). `msgs_in` counts every message, including unknown types. |
| D-23 | Golden runs are generated with `dbg_en = 1` (complete O/R/Z + B/Z). For a `dbg_en = 0` run, `tools/compare.py --no-dbg` drops the `'R'` records from the golden orders, since `dbg_en` only adds records and never changes state. |

## Hardware micro-architecture (equivalent transformations, verified bit-exact)

| ID | Decision |
|---|---|
| H-1 | Table entries store the ladder **window offset** `price − anchor + W/2·TICK` (16 bits) instead of the price or level. The level is `offset / TICK`, computed as an exact reciprocal multiply. This is valid because the anchor can only change while the product has no live orders. |
| H-2 | Add grid check: `(price − anchor) mod TICK == 0 ⇔ residue(price) == residue(anchor)`. The anchor residue is stored with the anchor. For TICK = 10 the residue is (parity, nibble-sum mod 5), exact for all int32. |
| H-3 | The ladder (`lq`) and bitmaps are single flattened memories (`lq[bank·W + level]`). The highest/lowest set bit of every bitmap and summary word is stored next to the word and maintained on write. Both candidate encodings (bit set / bit cleared) are computed from the pre-update word in parallel with the quantity read. |
| H-4 | Soft reset = `CLR_N` (16 384) clear steps through the same `step()` function as messages (one entry of every memory per step), so every state element has exactly one store per invocation. At II = 4 that is ≈ 0.66 ms at 100 MHz with TREADY low and `STATUS.busy = 1`. |
| H-5 | Per-product state lives in 8-entry LUT-RAMs, split into an early group (state/anchor/gc/live, dispatch stage) and a late group (fv/edge/last px/pos, strategy stage). |
| H-6 | AXI-Lite `POS[p]` are shadow registers updated when a product's position is written. HLS 2017.2 omits the adapter register of an output that is only written conditionally. |

## Data generator (SPEC §7.2)

| ID | Decision |
|---|---|
| D-24 | True fair value `FV(t) = DA + w(t)(IMB − DA) + (1 − w(t))·Σshocks`. The `(1 − w)` damping of forecast-revision shocks (not in the spec formula) makes trades converge to the imbalance price at gate closure, which §7.5 requires. |
| D-25 | The 5-hour forecast revision is applied as a shock at window open (it was published before the window). The 1-hour revision is applied 5 min before GC (spec). |
| D-26 | Products are simulated independently (one seeded simulation per delivery period) and merged by `(ts, delivery, local order)`. Order ids are assigned sequentially in order of first appearance, like exchange order references. |
| D-27 | Market makers post-only (they never cross the spread). Competitor takers send IOC orders (only `'E'` messages, no Add). The exchange clamps prices to ±9 999 EUR/MWh. FV is clipped to ±40 EUR around the anchor in default scenarios to stay inside the ±51.2 EUR ladder window. |
| D-28 | `edge_table_full` injects resting orders 30–50 EUR from the anchor (never traded by the simulation). Their ids are crafted to collide in a few sets, and they are deleted just before gate closure. Dropped Adds therefore later show up as `err_not_found` on their deletes. |

## Platform limits discovered during the build

| ID | Decision |
|---|---|
| D-29 | **AXI DMA v7.1 in Vivado 2017.2 allows a buffer-length register of at most 23 bits** (8 MB − 1 per transfer), not the spec's 26. The PS streams the input in 4 MB MM2S chunks, back to back from a bounded queue. The order S2MM buffer is < 8 MB (262 143 records per session; real datasets produce < 3 000). |
| D-30 | `dbg_out` asserts TLAST on the last beat of every 131 072nd `'B'` record (4 MB packets) and on `'Z'`. The PS re-arms S2MM per packet, so debug runs keep the spec's ≤ 1.5 M-message limit (48 MB buffer). TLAST is sideband only, and the record bytes and golden files are unchanged. |
| D-31 | Energi Data Service filters `start`/`end` on Danish local time. `fetch_eds.py` queries a day wider on each side and keeps the UTC window, and it retries politely on HTTP 429. |
| D-32 | ITCH conversion: the first add of a symbol opens its slot (`S(OPEN)`, anchor = that price, gate closure far in the future). Prices that are not whole cents become off-grid (counted by the IP as `err_grid`). The `'C'` execution price is used for `'E'`, and `'U'` becomes `X` + `A` on the same side. Conversion stops at system event `'C'`. |
| D-33 | C/RTL co-simulation bundles the `ap_ctrl_hs` handshake into the AXI-Lite slave, which the cosim requires when s_axilite ports exist. The cosim latency column therefore includes the AXI-Lite start/done polling and is not used as the latency result (see §08 R3). |
