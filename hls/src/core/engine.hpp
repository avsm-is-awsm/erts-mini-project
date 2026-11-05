// engine.hpp -- step() over a State struct (SPEC 6.4).  One message in, at most one record
// on ord_out and one on dbg_out.  Used unchanged by the HLS top (t2t_top.cpp), the g++
// C-simulation (tb_t2t.cpp, xcheck.cpp) and the ARM SwEngine (Architecture A baseline).
//
// Hardware-oriented structure (same semantics as ref/model.py):
//  * step() is the single entry point for both a message and one soft-reset clear step,
//    so every piece of state has exactly one store per invocation (no merged stores from
//    different control branches -> short loop-carried paths at II = 4);
//  * one order-table set read + one write, one ladder read-modify-write, one read + one
//    write of the message's product record (early group at dispatch, late group at the
//    strategy / risk stage);
//  * no early returns.
// Interpretations of the spec are listed in docs/DECISIONS.md and referenced as D-xx.
#ifndef T2T_ENGINE_HPP
#define T2T_ENGINE_HPP

#include "order_table.hpp"
#include "price_ladder.hpp"
#include "risk.hpp"
#include "strategy.hpp"
#include "types.hpp"

namespace t2t {

inline bool is_known_type(uint8_t t) {
#pragma HLS INLINE
    return t == dk1::T_ADD || t == dk1::T_DEL || t == dk1::T_EXEC || t == dk1::T_RED ||
           t == dk1::T_STATE || t == dk1::T_FV || t == dk1::T_HB;
}

inline void make_end(const Counters& c, uint32_t seq, uint32_t t_in, uint64_t w[4]) {
#pragma HLS INLINE
    dk1::EndRec z;
    z.seq = seq;
    z.msgs_in = c.msgs_in;
    z.orders_out = c.orders_out;
    z.first_t_in = c.first_t_in;
    z.last_t_in = c.last_t_in;
    z.t_in = t_in;
    z.t_out = 0;
    dk1::pack_end(z, w);
}

// ---------------------------------------------------------------- one step
// clear == true : soft-reset sweep step clr_i in [0, CLR_N): zero set clr_i of every way,
//                 level clr_i of every ladder bank, bitmap word clr_i, product clr_i, and
//                 (clr_i == 0) all counters / globals.  `in` is ignored.
// clear == false: process message `in` (4 beats) captured at cycle t_in.
inline void step(bool clear, uint32_t clr_i, const uint64_t in[4], uint32_t t_in,
                 const Config& cfg, State& st, Out& out) {
#pragma HLS INLINE
    const dk1::Msg m = dk1::unpack_msg(in);
    const bool dbg_en = (cfg.ctrl & CTRL_DBG_EN) != 0;
    const bool msg = !clear;
    const bool clr0 = clear && clr_i == 0;

    out.ord_valid = false;
    out.ord_last = false;
    out.dbg_valid = false;
    out.dbg_last = false;
    out.pos_upd = false;
    out.pos_p = 0;
    out.pos_val = 0;
    for (int k = 0; k < 4; ++k) {
#pragma HLS UNROLL
        out.ord[k] = 0;
        out.dbg[k] = 0;
    }

    // ---- steps 2, 3: classification
    const uint8_t type = m.type;
    const bool known = msg && is_known_type(type);
    const bool is_h = known && (type == dk1::T_HB);
    const bool bad_p = known && !is_h && (m.product >= (uint16_t)P_MAX);
    const bool book = known && !is_h && !bad_p;  // A/X/E/M/S/V with a valid product
    const bool is_end = is_h && (m.flags & dk1::FLAG_END) != 0;
    const uint32_t p = (uint32_t)m.product & (uint32_t)(P_MAX - 1);  // meaningful if book
    const bool is_a = book && (type == dk1::T_ADD);
    const bool is_x = book && (type == dk1::T_DEL);
    const bool is_em = book && ((type == dk1::T_EXEC) || (type == dk1::T_RED));
    const bool is_s = book && (type == dk1::T_STATE);
    const bool is_v = book && (type == dk1::T_FV);
    const uint32_t side = (uint32_t)(m.flags & dk1::FLAG_SIDE);

    // error / event increments (added once at the end: one store per counter)
    uint32_t inc_dup = 0, inc_grid = 0, inc_full = 0, inc_oow = 0, inc_nf = 0, inc_over = 0;
    uint32_t inc_anchor = 0, inc_bad_state = 0;

    // ---- early product group: read, dispatch, write
    uint8_t ps = st.pstate[p];
    int32_t anchor = st.anchor[p];
    uint8_t anchor_res = st.anchor_res[p];
    uint64_t gc_ts = st.gc_ts[p];
    uint32_t live = st.live_count[p];
    bool s_preopen = false;  // late-group effects of S(PREOPEN) / V, applied at step 6
    bool ok = true;          // false => dispatch error, step 6 skipped (D-03)

    // order table (A/X/E/M); key = (order_id, product) (D-10)
    const uint32_t set = oid_set(m.order_id);
    SetView sv;
    table_read_set(st, set, sv);
    // per-way match / free flags (parallel), then one-hot selects
    bool hit_w[WAYS], free_w[WAYS];
    bool any_hit = false, any_free = false;
    uint32_t e_qty = 0, e_meta = 0;
TABLE_MATCH:
    for (int w = 0; w < WAYS; ++w) {
#pragma HLS UNROLL
        hit_w[w] = meta_valid(sv.meta[w]) && sv.oid[w] == m.order_id && meta_prod(sv.meta[w]) == p;
        free_w[w] = !meta_valid(sv.meta[w]) && !any_free;  // lowest free way only
        any_free = any_free || !meta_valid(sv.meta[w]);
        any_hit = any_hit || hit_w[w];
        e_qty |= hit_w[w] ? sv.qty[w] : 0u;   // at most one way hits
        e_meta |= hit_w[w] ? sv.meta[w] : 0u;
    }
    const int hit = any_hit ? 0 : -1;        // only "found / not found" is used below
    const int fw = any_free ? 0 : -1;

    bool on_grid, in_win;
    uint32_t a_off;
    ladder_offset(m.price, anchor, anchor_res, on_grid, in_win, a_off);

    // Flat decision logic (each state update has its own explicit condition; no if/else
    // chains, so e.g. the anchor update never waits for the Add grid check in hardware).
    const bool xm = is_x || is_em;
    const bool a_dup  = is_a && hit >= 0;
    const bool a_grid = is_a && hit < 0 && !on_grid;
    const bool a_full = is_a && hit < 0 && on_grid && fw < 0;
    const bool a_ins  = is_a && hit < 0 && on_grid && fw >= 0;
    const bool xm_nf  = xm && hit < 0;
    const bool xm_hit = xm && hit >= 0;
    const uint32_t r  = is_em ? ((m.qty < e_qty) ? m.qty : e_qty) : e_qty;
    const bool rm = xm_hit && (is_x || m.qty >= e_qty);  // entry fully removed
    const uint32_t ns = m.qty;
    const bool s_bad = is_s && ns > dk1::ST_HALT;                          // D-11
    const bool s_ok = is_s && ns <= dk1::ST_HALT;
    const bool s_open = s_ok && ns == dk1::ST_OPEN && ps == dk1::ST_PREOPEN;
    const bool s_anchor = s_open && live == 0;
    s_preopen = s_ok && ns == dk1::ST_PREOPEN;
    ok = !(a_dup || a_grid || a_full || xm_nf || s_bad);

    inc_dup = a_dup ? 1u : 0u;
    inc_grid = a_grid ? 1u : 0u;
    inc_full = a_full ? 1u : 0u;
    inc_oow = (a_ins && !in_win) ? 1u : 0u;
    inc_nf = xm_nf ? 1u : 0u;
    inc_over = (xm_hit && is_em && m.qty > e_qty) ? 1u : 0u;
    inc_anchor = ((s_open || s_preopen) && live != 0) ? 1u : 0u;
    inc_bad_state = s_bad ? 1u : 0u;

    const bool lad_do = (a_ins && in_win) || (xm_hit && !meta_oow(e_meta));
    const bool lad_add = a_ins;
    const uint32_t lad_side = a_ins ? side : meta_side(e_meta);
    const uint32_t lad_idx = off_to_idx(a_ins ? a_off : meta_off(e_meta));
    const uint32_t lad_q = a_ins ? m.qty : r;
    const uint64_t qm_add = qm_make(m.qty, meta_make(p, side, in_win ? a_off : 0u, !in_win));

    live = live + (a_ins ? 1u : 0u) - (rm ? 1u : 0u);
    anchor = s_anchor ? m.price : anchor;
    anchor_res = s_anchor ? (uint8_t)grid_residue(m.price) : anchor_res;
    gc_ts = s_anchor ? m.order_id : gc_ts;
    ps = s_ok ? (uint8_t)ns : ps;

    // Stores below are unconditional (write-back of the unchanged value when nothing changes)
    // so the hardware has no control-flow merges on its state memories.
    const bool clr_p = clear && clr_i < (uint32_t)P_MAX;
    const uint32_t pa = clear ? (clr_i & (uint32_t)(P_MAX - 1)) : p;  // product address
    st.pstate[pa] = clear ? (uint8_t)dk1::ST_PREOPEN : ps;
    st.anchor[pa] = clear ? 0 : anchor;
    st.anchor_res[pa] = clear ? (uint8_t)grid_residue(0) : anchor_res;
    st.gc_ts[pa] = clear ? 0 : gc_ts;
    st.live_count[pa] = clear ? 0 : live;

    // ---- order table write (one way) or clear (all ways of set clr_i)
    const uint32_t ta = clear ? (clr_i & (uint32_t)(SETS - 1)) : set;
TABLE_STORE:
    for (int w = 0; w < WAYS; ++w) {
#pragma HLS UNROLL
        // each way's new entry depends only on its own data and its own hit / free flag
        const bool ins_w = a_ins && free_w[w];
        const bool upd_w = xm && hit_w[w];
        const uint32_t q_w = sv.qty[w];
        const bool rm_w = is_x || m.qty >= q_w;
        const uint32_t left_w = q_w - ((m.qty < q_w) ? m.qty : q_w);
        const uint64_t qm_upd = rm_w ? 0ull : qm_make(left_w, sv.meta[w]);  // meta 0 = removed
        st.t_oid[w][ta] = clear ? 0 : (ins_w ? m.order_id : sv.oid[w]);
        st.t_qm[w][ta]  = clear ? 0 : (ins_w ? qm_add : (upd_w ? qm_upd : sv.qm[w]));
    }

    // ---- ladder read-modify-write (one level) or clear (one entry of each ladder memory)
    const uint32_t lbank = bank_of(p, lad_side);
    const uint32_t li = lq_addr(lbank, lad_idx);
    const uint32_t old_q = st.lq[li];
    const uint32_t new_q = lad_add ? (uint32_t)(old_q + lad_q) : (uint32_t)(old_q - lad_q);
    const uint32_t wi = lad_idx >> 5;
    const uint32_t bi_ = bm_addr(lbank, wi);
    const uint32_t wd = st.bm[bi_];
    const uint32_t s_old = st.sm[lbank];
    // A level update flips at most one bit of one bitmap word and one summary bit, so both
    // outcomes (bit set / bit cleared) and their priority encodes are computed from the
    // pre-update words in parallel with the quantity read; new_q only selects between them.
    const uint32_t bitm = 1u << (lad_idx & 31u);
    const uint32_t sbit = 1u << wi;
    const uint32_t wd_set = wd | bitm, wd_clr = wd & ~bitm;
    const uint32_t s_set = s_old | sbit, s_clr = s_old & ~sbit;
    const uint8_t wset_hi = (uint8_t)msb32(wd_set), wset_lo = (uint8_t)lsb32(wd_set);
    const uint8_t wclr_hi = (uint8_t)msb32(wd_clr), wclr_lo = (uint8_t)lsb32(wd_clr);
    const uint8_t sset_hi = (uint8_t)msb32(s_set), sset_lo = (uint8_t)lsb32(s_set);
    const uint8_t sclr_hi = (uint8_t)msb32(s_clr), sclr_lo = (uint8_t)lsb32(s_clr);
    const bool q_nz = new_q != 0;
    const bool w_nz = q_nz || wd_clr != 0;
    const uint32_t nwd = q_nz ? wd_set : wd_clr;
    const uint8_t nwd_hi = q_nz ? wset_hi : wclr_hi;
    const uint8_t nwd_lo = q_nz ? wset_lo : wclr_lo;
    const uint32_t s_new = w_nz ? s_set : s_clr;
    const uint8_t s_new_hi = w_nz ? sset_hi : sclr_hi;
    const uint8_t s_new_lo = w_nz ? sset_lo : sclr_lo;
    const bool w_lq = lad_do || (clear && clr_i < (uint32_t)LQ_N);
    const bool w_bm = lad_do || (clear && clr_i < (uint32_t)BM_N);
    const bool w_sm = lad_do || (clear && clr_i < (uint32_t)BANKS);
    const uint32_t lqa = clear ? (clr_i & (uint32_t)(LQ_N - 1)) : li;
    const uint32_t bma = clear ? (clr_i & (uint32_t)(BM_N - 1)) : bi_;
    const uint32_t sma = clear ? (clr_i & (uint32_t)(BANKS - 1)) : lbank;
    if (w_lq) st.lq[lqa] = clear ? 0u : new_q;
    if (w_bm) {
        st.bm[bma] = clear ? 0u : nwd;
        st.bm_hi[bma] = clear ? (uint8_t)0 : nwd_hi;
        st.bm_lo[bma] = clear ? (uint8_t)0 : nwd_lo;
    }
    if (w_sm) {
        st.sm[sma] = clear ? 0u : s_new;
        st.sm_hi[sma] = clear ? (uint8_t)0 : s_new_hi;
        st.sm_lo[sma] = clear ? (uint8_t)0 : s_new_lo;
    }

    // ---- step 5: BBO of the message's product
    Bbo b;
    ladder_bbo(st, p, anchor, b);
    if (book && dbg_en) {
        dk1::BboRec br;
        br.flags = (uint8_t)((b.bid_ok ? 1u : 0u) | (b.ask_ok ? 2u : 0u));
        br.product = (uint16_t)p;
        br.seq = m.seq;
        br.bid_px = b.bid_px;
        br.bid_qty = b.bid_qty;
        br.ask_px = b.ask_px;
        br.ask_qty = b.ask_qty;
        br.t_in = t_in;
        br.t_out = 0;
        dk1::pack_bbo(br, out.dbg);
        out.dbg_valid = true;
    }

    // ---- late product group: read, apply S(PREOPEN) / V, strategy, risk, write
    PView v;
    v.state = ps;
    v.anchor = anchor;
    v.gc_ts = gc_ts;
    v.live = live;
    v.fv = st.fv[p];
    v.edge = st.edge[p];
    v.fv_valid = st.fv_valid[p];
    v.lb_px = st.last_buy_px[p];
    v.lb_ok = st.last_buy_ok[p];
    v.ls_px = st.last_sell_px[p];
    v.ls_ok = st.last_sell_ok[p];
    v.pos = st.pos[p];
    if (s_preopen) {
        v.fv_valid = false;
        v.pos = 0;
        v.lb_ok = false;
        v.ls_ok = false;
    }
    if (is_v) {
        v.fv = m.price;
        v.edge = m.qty;
        v.fv_valid = true;
        v.lb_ok = false;
        v.ls_ok = false;
    }

    // ---- step 6: strategy -> risk -> order (only after a successful dispatch)
    Candidate c;
    c.valid = false;
    c.side = 0;
    c.price = 0;
    c.qty = 0;
    if (book && ok) c = strategy_step(v, cfg, m.ts_ns, b);
    const RiskResult rr = risk_check(v, st, cfg, m.ts_ns, c);
    const bool accept = c.valid && rr.reason == dk1::RJ_NONE;
    const bool reject = c.valid && rr.reason != dk1::RJ_NONE;
    if (accept) v.pos = rr.new_pos;
    dk1::OrdRec o;
    o.type = accept ? (uint8_t)dk1::O_ORDER : (uint8_t)dk1::O_REJECT;
    o.flags = (uint8_t)((c.side & 1u) | dk1::FLAG_IOC);
    o.product = (uint16_t)p;
    o.trig_seq = m.seq;
    o.coid = accept ? st.next_coid : 0u;  // 'R': 0 (D-06)
    o.price = c.price;
    o.qty = accept ? rr.qty : c.qty;
    o.reason = rr.reason;
    o.t_in = t_in;
    o.t_out = 0;
    if (accept || (reject && dbg_en)) {
        dk1::pack_ord(o, out.ord);
        out.ord_valid = true;
    }
    st.fv[pa] = clear ? 0 : v.fv;
    st.edge[pa] = clear ? 0u : v.edge;
    st.fv_valid[pa] = clear ? false : v.fv_valid;
    st.last_buy_px[pa] = clear ? 0 : v.lb_px;
    st.last_buy_ok[pa] = clear ? false : v.lb_ok;
    st.last_sell_px[pa] = clear ? 0 : v.ls_px;
    st.last_sell_ok[pa] = clear ? false : v.ls_ok;
    st.pos[pa] = clear ? 0 : v.pos;
    out.pos_upd = book || clr_p;
    out.pos_p = pa;
    out.pos_val = clear ? 0 : v.pos;

    // ---- risk globals and counters: exactly one store each
    st.last_order_ts = clr0 ? 0 : (accept ? m.ts_ns : st.last_order_ts);
    st.has_ordered = clr0 ? false : (accept || st.has_ordered);
    st.next_coid = clr0 ? 1u : st.next_coid + (accept ? 1u : 0u);

    Counters& k = st.c;
    const bool first = msg && !st.have_first;
    st.have_first = clr0 ? false : (st.have_first || msg);
    k.first_t_in = clr0 ? 0u : (first ? t_in : k.first_t_in);
    k.last_t_in = clr0 ? 0u : (msg ? t_in : k.last_t_in);
    const bool gap = msg && k.last_seq != 0 && m.seq != (uint32_t)(k.last_seq + 1u);
    k.err_seq_gap = clr0 ? 0u : k.err_seq_gap + (gap ? 1u : 0u);
    k.last_seq = clr0 ? 0u : (msg ? m.seq : k.last_seq);
    k.msgs_in = clr0 ? 0u : k.msgs_in + (msg ? 1u : 0u);
    k.err_unknown_type = clr0 ? 0u : k.err_unknown_type + ((msg && !known) ? 1u : 0u);
    k.err_bad_product = clr0 ? 0u : k.err_bad_product + (bad_p ? 1u : 0u);
    k.err_dup_id = clr0 ? 0u : k.err_dup_id + inc_dup;
    k.err_grid = clr0 ? 0u : k.err_grid + inc_grid;
    k.err_table_full = clr0 ? 0u : k.err_table_full + inc_full;
    k.err_oow = clr0 ? 0u : k.err_oow + inc_oow;
    k.err_not_found = clr0 ? 0u : k.err_not_found + inc_nf;
    k.err_qty_over = clr0 ? 0u : k.err_qty_over + inc_over;
    k.err_anchor = clr0 ? 0u : k.err_anchor + inc_anchor;
    k.err_bad_state = clr0 ? 0u : k.err_bad_state + inc_bad_state;
    k.rejects = clr0 ? 0u : k.rejects + (reject ? 1u : 0u);
    k.orders_out = clr0 ? 0u : k.orders_out + (accept ? 1u : 0u);

    // ---- H(END) -> 'Z' end-of-session record (counters include the END message, D-16)
    if (is_end) {
        make_end(k, m.seq, t_in, out.ord);
        out.ord_valid = true;
        out.ord_last = true;
        if (dbg_en) {
            make_end(k, m.seq, t_in, out.dbg);
            out.dbg_valid = true;
            out.dbg_last = true;
        }
    }
}

// ---------------------------------------------------------------- software conveniences
inline void process(const uint64_t in[4], uint32_t t_in, const Config& cfg, State& st, Out& out) {
    step(false, 0, in, t_in, cfg, st, out);
}

// Full soft reset in software: the same CLR_N clear steps the hardware runs.
inline void reset_all(State& st) {
    const uint64_t zero[4] = {0, 0, 0, 0};
    const Config cfg = default_config();
    Out o;
    for (uint32_t i = 0; i < (uint32_t)CLR_N; ++i) step(true, i, zero, 0, cfg, st, o);
}

}  // namespace t2t

#endif
