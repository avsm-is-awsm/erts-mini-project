// t2t_top.cpp -- HLS wrapper around core/engine.hpp.
//
// One invocation = one 32-byte message (4 x 64-bit beats) at II = 4, i.e. AXIS line rate.
// While a soft reset is clearing the memories (CLR_N invocations) md_in is not read
// (TREADY low) and STATUS.busy = 1.
#include "t2t_top.hpp"

// The AXI-Lite position registers pos0..pos7 assume P_MAX == 8.
typedef char t2t_p_max_must_be_8[(t2t::P_MAX == 8) ? 1 : -1];

static void write_rec(hls::stream<axis_out_t>& s, const uint64_t w[4], bool last) {
#pragma HLS INLINE
WRITE_BEATS:
    for (int k = 0; k < 4; ++k) {
        axis_out_t b;
        b.data = w[k];
        b.keep = 0xFF;
        b.strb = 0xFF;
        b.user = 0;
        b.id = 0;
        b.dest = 0;
        b.last = (last && k == 3) ? 1 : 0;
        s.write(b);
    }
}

void t2t_core(hls::stream<axis_in_t>& md_in, hls::stream<axis_out_t>& ord_out,
              hls::stream<axis_out_t>& dbg_out, uint32_t ctrl, uint32_t clip_qty,
              uint32_t max_order_qty, int32_t max_pos, uint32_t collar, uint32_t min_gap_ns,
              uint32_t max_orders, uint32_t lockout_ms, uint32_t* status, uint32_t* msgs_in,
              uint32_t* orders_out, uint32_t* rejects, uint32_t* err_unknown_type,
              uint32_t* err_bad_product, uint32_t* err_not_found, uint32_t* err_table_full,
              uint32_t* err_dup_id, uint32_t* err_oow, uint32_t* err_grid, uint32_t* err_seq_gap,
              uint32_t* err_qty_over, uint32_t* err_anchor, uint32_t* err_bad_state,
              uint32_t* last_seq, uint32_t* first_t_in, uint32_t* last_t_in, int32_t* pos0,
              int32_t* pos1, int32_t* pos2, int32_t* pos3, int32_t* pos4, int32_t* pos5,
              int32_t* pos6, int32_t* pos7) {
#ifdef T2T_COSIM_CTRL_HS
#pragma HLS INTERFACE s_axilite port=return bundle=ctrl
#else
#pragma HLS INTERFACE ap_ctrl_none port=return
#endif
#pragma HLS INTERFACE axis register both port=md_in
#pragma HLS INTERFACE axis register both port=ord_out
#pragma HLS INTERFACE axis register both port=dbg_out
#pragma HLS INTERFACE s_axilite port=ctrl bundle=ctrl
#pragma HLS INTERFACE s_axilite port=clip_qty bundle=ctrl
#pragma HLS INTERFACE s_axilite port=max_order_qty bundle=ctrl
#pragma HLS INTERFACE s_axilite port=max_pos bundle=ctrl
#pragma HLS INTERFACE s_axilite port=collar bundle=ctrl
#pragma HLS INTERFACE s_axilite port=min_gap_ns bundle=ctrl
#pragma HLS INTERFACE s_axilite port=max_orders bundle=ctrl
#pragma HLS INTERFACE s_axilite port=lockout_ms bundle=ctrl
#pragma HLS INTERFACE s_axilite port=status bundle=ctrl
#pragma HLS INTERFACE s_axilite port=msgs_in bundle=ctrl
#pragma HLS INTERFACE s_axilite port=orders_out bundle=ctrl
#pragma HLS INTERFACE s_axilite port=rejects bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_unknown_type bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_bad_product bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_not_found bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_table_full bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_dup_id bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_oow bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_grid bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_seq_gap bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_qty_over bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_anchor bundle=ctrl
#pragma HLS INTERFACE s_axilite port=err_bad_state bundle=ctrl
#pragma HLS INTERFACE s_axilite port=last_seq bundle=ctrl
#pragma HLS INTERFACE s_axilite port=first_t_in bundle=ctrl
#pragma HLS INTERFACE s_axilite port=last_t_in bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos0 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos1 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos2 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos3 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos4 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos5 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos6 bundle=ctrl
#pragma HLS INTERFACE s_axilite port=pos7 bundle=ctrl
#pragma HLS PIPELINE II=4

    static t2t::State st;
#pragma HLS ARRAY_PARTITION variable=st.t_oid complete dim=1
#pragma HLS ARRAY_PARTITION variable=st.t_qm complete dim=1
#pragma HLS RESOURCE variable=st.t_oid core=RAM_2P_BRAM
#pragma HLS RESOURCE variable=st.t_qm core=RAM_2P_BRAM
#pragma HLS RESOURCE variable=st.lq core=RAM_2P_BRAM
#pragma HLS RESOURCE variable=st.bm core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.sm core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.bm_hi core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.bm_lo core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.sm_hi core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.sm_lo core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.pstate core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.anchor core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.anchor_res core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.gc_ts core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.live_count core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.fv core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.edge core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.fv_valid core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.last_buy_px core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.last_buy_ok core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.last_sell_px core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.last_sell_ok core=RAM_2P_LUTRAM
#pragma HLS RESOURCE variable=st.pos core=RAM_2P_LUTRAM
    // AXI-Lite POS[p] read-back: shadow registers written on every invocation (Vivado HLS
    // 2017.2 drops the adapter register of an output that is only written conditionally)
    static int32_t pos_shadow[t2t::P_MAX];
#pragma HLS ARRAY_PARTITION variable=pos_shadow complete
    static uint32_t dbg_cnt = 0;  // records in the current dbg_out packet (D-30)
    static bool clearing = false;
    static uint32_t clr_i = 0;
    static bool rst_prev = false;

    t2t::Config cfg;
    cfg.ctrl = ctrl;
    cfg.clip_qty = clip_qty;
    cfg.max_order_qty = max_order_qty;
    cfg.max_pos = max_pos;
    cfg.collar = collar;
    cfg.min_gap_ns = min_gap_ns;
    cfg.max_orders = max_orders;
    cfg.lockout_ms = lockout_ms;

    // soft reset: rising edge of CTRL.soft_reset starts CLR_N clear steps (TREADY low)
    const bool rst = (ctrl & t2t::CTRL_SOFT_RESET) != 0;
    const bool rst_edge = rst && !rst_prev;
    const bool do_clear = clearing;
    const bool have_msg = !do_clear && !rst_edge && !md_in.empty();

    uint64_t w[4] = {0, 0, 0, 0};
    uint32_t t_in = 0;
    if (have_msg) {
    READ_BEATS:
        for (int k = 0; k < 4; ++k) {
            axis_in_t b = md_in.read();
            w[k] = b.data;
            if (k == 0) t_in = (uint32_t)b.user;
        }
    }
    t2t::Out o;
    if (do_clear || have_msg) {
        t2t::step(do_clear, clr_i, w, t_in, cfg, st, o);
        if (o.ord_valid) write_rec(ord_out, o.ord, o.ord_last);
        if (o.dbg_valid) {
            const bool pkt_end = o.dbg_last || dbg_cnt == (uint32_t)(t2t::DBG_PKT - 1);
            write_rec(dbg_out, o.dbg, pkt_end);
            dbg_cnt = pkt_end ? 0u : dbg_cnt + 1u;
        }
        if (o.pos_upd) pos_shadow[o.pos_p] = o.pos_val;
    }
    if (rst_edge) {
        clearing = true;
        clr_i = 0;
        dbg_cnt = 0;
    } else if (do_clear) {
        clearing = (clr_i != (uint32_t)(t2t::CLR_N - 1));
        clr_i++;
    }
    rst_prev = rst;
    const uint32_t busy = (rst_edge || clearing) ? 1u : 0u;

    *status = busy;
    *msgs_in = st.c.msgs_in;
    *orders_out = st.c.orders_out;
    *rejects = st.c.rejects;
    *err_unknown_type = st.c.err_unknown_type;
    *err_bad_product = st.c.err_bad_product;
    *err_not_found = st.c.err_not_found;
    *err_table_full = st.c.err_table_full;
    *err_dup_id = st.c.err_dup_id;
    *err_oow = st.c.err_oow;
    *err_grid = st.c.err_grid;
    *err_seq_gap = st.c.err_seq_gap;
    *err_qty_over = st.c.err_qty_over;
    *err_anchor = st.c.err_anchor;
    *err_bad_state = st.c.err_bad_state;
    *last_seq = st.c.last_seq;
    *first_t_in = st.c.first_t_in;
    *last_t_in = st.c.last_t_in;
    *pos0 = pos_shadow[0];
    *pos1 = pos_shadow[1];
    *pos2 = pos_shadow[2];
    *pos3 = pos_shadow[3];
    *pos4 = pos_shadow[4];
    *pos5 = pos_shadow[5];
    *pos6 = pos_shadow[6];
    *pos7 = pos_shadow[7];
}
