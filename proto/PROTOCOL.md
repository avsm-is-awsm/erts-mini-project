# dk1 wire protocol

This is the human-readable form of SPEC §5. The code is in `dk1proto.py` (Python, numpy dtypes)
and `dk1proto.hpp` (C++98 PODs + pack/unpack to 4 × `uint64_t`). The round-trip tests are in
`tests/test_proto.py`.

## Transport

* Every record is **32 bytes, little-endian**.
* On the 64-bit AXI4-Stream a record is **4 beats**. Beat *k* carries bytes `8k..8k+7`, and
  byte *i* of a beat occupies bits `[8i+7:8i]`.
* `md_in` TUSER[31:0] carries `t_in` on beat 0 (driven by `axis_ts_in`). `ord_out` bytes 28..31
  are overwritten with `t_out` by `axis_ts_out` on beat 3.
* `ord_out` TLAST is set only on the last beat of `'Z'`. `dbg_out` TLAST is set on the last beat
  of `'Z'` and of every 131 072nd `'B'` record (4 MB DMA packets, DECISIONS D-30).

## Units

| Quantity | Type | Unit |
|---|---|---|
| `price` | int32 | 0.01 EUR/MWh (signed; DK1 prices can be negative) |
| `qty` | uint32 | 0.1 MW |
| `ts_ns` | uint64 | ns since the Unix epoch (UTC) |
| `order_id` | uint64 | exchange order reference |
| `product` | uint16 | product slot `0..7` |

## Inbound messages

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `type` (ASCII) |
| 1 | 1 | `flags`: bit0 side (0 bid/buy, 1 ask/sell), bit1 type-specific |
| 2 | 2 | `product` |
| 4 | 4 | `seq` (+1 per message, starts at 1) |
| 8 | 8 | `ts_ns` |
| 16 | 8 | `order_id` |
| 24 | 4 | `price` |
| 28 | 4 | `qty` |

| Type | Meaning | Fields used |
|---|---|---|
| `A` | add order | order_id, side, price, qty |
| `X` | delete order | order_id |
| `E` | execute | order_id, qty (executed), price (informational) |
| `M` | reduce | order_id, qty (to remove) |
| `S` | product state | qty = 0 PREOPEN / 1 OPEN / 2 CLOSED / 3 HALT; price = anchor; order_id = gate-closure ns |
| `V` | fair value | price = fair value, qty = edge (price units) |
| `H` | heartbeat | flags.bit1 = END_OF_SESSION (always the last message of a dataset) |

Unused fields are 0. Unknown types are counted (`err_unknown_type`) and skipped.

## Outbound records

`ord_out`: `'O'` order, `'R'` reject (only when `dbg_en`), `'Z'` end of session.

| Off | Size | `'O'` / `'R'` | `'Z'` |
|---|---|---|---|
| 0 | 1 | type | `'Z'` |
| 1 | 1 | bit0 side, bit1 IOC (= 1) | 0 |
| 2 | 2 | product | 0 |
| 4 | 4 | trig_seq | seq of the END message |
| 8 | 4 | client_order_id (`'R'`: 0) | msgs_in |
| 12 | 4 | price | orders_out |
| 16 | 4 | qty (`'R'`: requested qty before risk clamps) | first_t_in |
| 20 | 4 | 0 / reject reason (1 COLLAR, 2 POSITION, 3 RATE, 4 MAX_ORDERS) | last_t_in |
| 24 | 4 | t_in | t_in |
| 28 | 4 | t_out | t_out |

`dbg_out`: `'B'` BBO record after every A/X/E/M/S/V message with a valid product (when `dbg_en`):

| Off | Size | Field |
|---|---|---|
| 0 | 1 | `'B'` |
| 1 | 1 | bit0 bid_valid, bit1 ask_valid |
| 2 | 2 | product |
| 4 | 4 | seq |
| 8 / 12 | 4 / 4 | best bid price / qty (0 if invalid) |
| 16 / 20 | 4 / 4 | best ask price / qty (0 if invalid) |
| 24 | 8 | t_in, t_out slot (ignored) |

**Golden comparison** masks bytes 24..31 of every record, and also bytes 16..23 of `'Z'` records
(DECISIONS D-07).
