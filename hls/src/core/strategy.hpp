// strategy.hpp -- fair-value taker (SPEC 6.5).  All comparisons are exact (64-bit).
#ifndef T2T_STRATEGY_HPP
#define T2T_STRATEGY_HPP

#include "price_ladder.hpp"
#include "types.hpp"

namespace t2t {

// Copy of one product's state, loaded once per message and stored back at the end
// (a single read-modify-write per message keeps the hardware loop-carried paths short).
struct PView {
    uint8_t  state;
    int32_t  anchor;
    uint64_t gc_ts;
    uint32_t live;
    int32_t  fv;
    uint32_t edge;
    bool     fv_valid;
    int32_t  lb_px;   // last_buy_px  (valid iff lb_ok; NONE otherwise, D-04)
    bool     lb_ok;
    int32_t  ls_px;   // last_sell_px
    bool     ls_ok;
    int32_t  pos;
};

struct Candidate {
    bool     valid;
    uint32_t side;   // dk1::BUY / dk1::SELL
    int32_t  price;
    uint32_t qty;    // requested qty (after CLIP_QTY, before risk clamps)
};

// Gate: strat_en && !kill && OPEN && fv_valid && ts + lockout_ms*1e6 < gc_ts (evaluated exactly).
inline bool strategy_armed(const PView& v, const Config& cfg, uint64_t ts) {
#pragma HLS INLINE
    uint64_t lock_ns = (uint64_t)cfg.lockout_ms * 1000000ull;
    bool before_gc = (v.gc_ts > ts) && ((v.gc_ts - ts) > lock_ns);
    return ((cfg.ctrl & CTRL_STRAT_EN) != 0) && ((cfg.ctrl & CTRL_KILL) == 0) &&
           (v.state == dk1::ST_OPEN) && v.fv_valid && before_gc;
}

// Decision plus the dedup update of last_*_px (which happens even if risk later rejects).
inline Candidate strategy_step(PView& v, const Config& cfg, uint64_t ts, const Bbo& b) {
#pragma HLS INLINE
    Candidate c;
    c.valid = false;
    c.side = dk1::BUY;
    c.price = 0;
    c.qty = 0;
    const bool armed = strategy_armed(v, cfg, ts);
    const int64_t fv = (int64_t)v.fv;
    const int64_t edge = (int64_t)v.edge;
    const bool buy_new  = !(v.lb_ok && v.lb_px == b.ask_px);
    const bool sell_new = !(v.ls_ok && v.ls_px == b.bid_px);
    const bool buy  = armed && b.ask_ok && (int64_t)b.ask_px <= fv - edge && buy_new;
    const bool sell = armed && !buy && b.bid_ok && (int64_t)b.bid_px >= fv + edge && sell_new;
    if (buy) {
        c.valid = true;
        c.side  = dk1::BUY;
        c.price = b.ask_px;
        c.qty   = (b.ask_qty < cfg.clip_qty) ? b.ask_qty : cfg.clip_qty;
        v.lb_px = b.ask_px;
        v.lb_ok = true;
    } else if (sell) {
        c.valid = true;
        c.side  = dk1::SELL;
        c.price = b.bid_px;
        c.qty   = (b.bid_qty < cfg.clip_qty) ? b.bid_qty : cfg.clip_qty;
        v.ls_px = b.bid_px;
        v.ls_ok = true;
    }
    return c;
}

}  // namespace t2t

#endif
