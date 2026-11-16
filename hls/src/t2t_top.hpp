// t2t_top.hpp -- HLS top-level interface of the t2t_core IP.
#ifndef T2T_TOP_HPP
#define T2T_TOP_HPP

#include <ap_axi_sdata.h>
#include <hls_stream.h>
#include <stdint.h>

#include "core/engine.hpp"

// HLS 2017.2 does not accept zero-width TUSER/TID/TDEST, so the dummy sideband
// signals are 1 bit wide (DECISIONS D-02).  md_in TUSER[31:0] = t_in from axis_ts_in.
typedef ap_axiu<64, 32, 1, 1> axis_in_t;
typedef ap_axiu<64, 1, 1, 1> axis_out_t;

void t2t_core(hls::stream<axis_in_t>& md_in, hls::stream<axis_out_t>& ord_out,
              hls::stream<axis_out_t>& dbg_out,
              // ---- configuration (AXI-Lite, write)
              uint32_t ctrl, uint32_t clip_qty, uint32_t max_order_qty, int32_t max_pos,
              uint32_t collar, uint32_t min_gap_ns, uint32_t max_orders, uint32_t lockout_ms,
              // ---- status / counters / positions (AXI-Lite, read)
              uint32_t* status, uint32_t* msgs_in, uint32_t* orders_out, uint32_t* rejects,
              uint32_t* err_unknown_type, uint32_t* err_bad_product, uint32_t* err_not_found,
              uint32_t* err_table_full, uint32_t* err_dup_id, uint32_t* err_oow,
              uint32_t* err_grid, uint32_t* err_seq_gap, uint32_t* err_qty_over,
              uint32_t* err_anchor, uint32_t* err_bad_state, uint32_t* last_seq,
              uint32_t* first_t_in, uint32_t* last_t_in, int32_t* pos0, int32_t* pos1,
              int32_t* pos2, int32_t* pos3, int32_t* pos4, int32_t* pos5, int32_t* pos6,
              int32_t* pos7,
              // transport error: messages cut short by TLAST before beat 3 (dropped, D-34)
              uint32_t* err_framing);

#endif
