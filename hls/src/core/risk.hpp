// risk.hpp -- pre-trade risk gate (SPEC 6.6).  First failing check rejects.
//
// Written with equivalent shallow formulas so the loop-carried position update is short in
// hardware (checked against the literal spec formulas by ref/model.py in the cross-check):
//   step 3 "qty = min(qty, room); reject if qty <= 0"  with room = MAX_POS -/+ pos
//     <=> reject if q == 0 or !(BUY ? pos < MAX_POS : pos > -MAX_POS)
//     accepted position = BUY ? min(pos + q, MAX_POS) : max(pos - q, -MAX_POS)
//     accepted qty      = |new position - pos|
#ifndef T2T_RISK_HPP
#define T2T_RISK_HPP

#include "strategy.hpp"
#include "types.hpp"

namespace t2t {

struct RiskResult {
    uint32_t reason;   // 0 = accepted, else dk1::RJ_*
    uint32_t qty;      // final qty if accepted
    int32_t  new_pos;  // position after an accepted order (assume full fill)
};

inline RiskResult risk_check(const PView& v, const State& st, const Config& cfg, uint64_t ts,
                             const Candidate& c) {
#pragma HLS INLINE
    RiskResult r;
    const bool buy = (c.side == dk1::BUY);
    // 1. price collar around fair value
    int64_t dev = (int64_t)c.price - (int64_t)v.fv;
    if (dev < 0) dev = -dev;
    const bool collar_hit = dev > (int64_t)cfg.collar;
    // 2. max order size clamp
    const uint32_t q = (c.qty < cfg.max_order_qty) ? c.qty : cfg.max_order_qty;
    // 3. position room (pos assumed fully filled)
    const int64_t pos = (int64_t)v.pos;
    const int64_t mp = (int64_t)cfg.max_pos;
    const bool room_ok = buy ? (pos < mp) : (pos > -mp);
    const bool pos_hit = (q == 0) || !room_ok;
    const int64_t up = pos + (int64_t)q, dn = pos - (int64_t)q;
    const int64_t np = buy ? ((up < mp) ? up : mp) : ((dn > -mp) ? dn : -mp);
    // 4. min gap between orders (MIN_GAP_NS == 0 disables, DECISIONS D-08)
    const bool rate_hit = st.has_ordered && cfg.min_gap_ns != 0 &&
                          (ts < st.last_order_ts || (ts - st.last_order_ts) < (uint64_t)cfg.min_gap_ns);
    // 5. order budget
    const bool budget_hit = st.c.orders_out >= cfg.max_orders;

    r.reason = collar_hit ? (uint32_t)dk1::RJ_COLLAR
             : pos_hit    ? (uint32_t)dk1::RJ_POSITION
             : rate_hit   ? (uint32_t)dk1::RJ_RATE
             : budget_hit ? (uint32_t)dk1::RJ_MAX_ORDERS
                          : (uint32_t)dk1::RJ_NONE;
    r.new_pos = (int32_t)np;
    r.qty = (uint32_t)(buy ? (np - pos) : (pos - np));
    return r;
}

}  // namespace t2t

#endif
