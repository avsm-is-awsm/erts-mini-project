// price_ladder.hpp -- aggregated L2 ladder + 2-level bitmap per (product, side) (SPEC 6.2, 6.4.5).
//
// bank = product*2 + side.  lq[bank][i] is the aggregated quantity (mod 2^32) at level i;
// bm[bank][i/32] bit i%32 = (lq != 0); sm[bank] bit j = (bm[bank][j] != 0).
// Best bid = highest set level of the BID bank, best ask = lowest set level of the ASK bank:
// two priority encodes (summary word, then level-1 word) instead of a W-wide scan.
#ifndef T2T_PRICE_LADDER_HPP
#define T2T_PRICE_LADDER_HPP

#include "bitops.hpp"
#include "types.hpp"

namespace t2t {

inline uint32_t bank_of(uint32_t prod, uint32_t side) {
#pragma HLS INLINE
    return (prod << 1) | (side & 1u);
}

// ---------------------------------------------------------------- price -> level index
// Exact, division-free: d = price - anchor (33-bit signed).
//   on_grid : d mod LADDER_TICK == 0
//   in_win  : on_grid && 0 <= d/LADDER_TICK + W/2 < W
//   idx     : d/LADDER_TICK + W/2 (valid if in_win)
// In-window offsets are < W*LADDER_TICK, so idx is a reciprocal multiply on a short operand.
enum {
    WIN_SPAN = W * LADDER_TICK,  // offsets [0, WIN_SPAN) are inside the window (<= 2^16)
    // floor(o / TICK) == (o * ceil(2^S / TICK)) >> S exactly for o < WIN_SPAN when
    // 2^S > WIN_SPAN * TICK (error < WIN_SPAN / 2^S < 1 / TICK)
    RECIP_SHIFT = (WIN_SPAN * LADDER_TICK < (1 << 17)) ? 17 : 31
};

// Residue class of a price modulo LADDER_TICK (any encoding that is equal for two prices
// iff they are congruent).  (price - anchor) mod TICK == 0  <=>  residue(price) == residue(anchor),
// so the Add grid check compares a residue computed from the message alone with the
// anchor's residue stored when the anchor is set.
inline uint32_t grid_residue(int32_t x) {
#pragma HLS INLINE
#if (T2T_LADDER_TICK & (T2T_LADDER_TICK - 1)) == 0
    return (uint32_t)x & (uint32_t)(LADDER_TICK - 1);
#elif T2T_LADDER_TICK == 10
    // x = u - 2^32*sign with u = (uint32)x.  2^32 = 1 (mod 5) and 16 = 1 (mod 5):
    //   x mod 5 = (nibble_sum(u) - sign) mod 5 ;  x mod 2 = u mod 2.
    const uint32_t u = (uint32_t)x;
    uint32_t n[8];
NIB:
    for (int k = 0; k < 8; ++k) {
#pragma HLS UNROLL
        n[k] = (u >> (4 * k)) & 0xFu;
    }
    uint32_t s = ((n[0] + n[1]) + (n[2] + n[3])) + ((n[4] + n[5]) + (n[6] + n[7])) + 4u * (u >> 31);
    s = (s & 0xFu) + (s >> 4);  // <= 22
    s = (s & 0xFu) + (s >> 4);  // <= 16
    s = (s & 0xFu) + (s >> 4);  // <= 15
    const uint32_t r5 = (s >= 15u) ? s - 15u : (s >= 10u) ? s - 10u : (s >= 5u) ? s - 5u : s;
    return (r5 << 1) | (u & 1u);
#else
    const int64_t r = (int64_t)x % (int64_t)LADDER_TICK;
    return (uint32_t)(r < 0 ? r + LADDER_TICK : r);
#endif
}

// Exact level of an in-window offset: floor(off / LADDER_TICK) via reciprocal multiply.
inline uint32_t off_to_idx(uint32_t off) {
#pragma HLS INLINE
    const uint32_t recip = (uint32_t)((((uint64_t)1 << RECIP_SHIFT) + (uint64_t)LADDER_TICK - 1) /
                                      (uint64_t)LADDER_TICK);
    return (uint32_t)(((uint64_t)(off & 0xFFFFu) * recip) >> RECIP_SHIFT) & (uint32_t)(W - 1);
}

// Grid and window classification of an Add price relative to the anchor.
inline void ladder_offset(int32_t price, int32_t anchor, uint32_t anchor_res, bool& on_grid,
                          bool& in_win, uint32_t& off) {
#pragma HLS INLINE
    on_grid = grid_residue(price) == anchor_res;
    const int64_t o = (int64_t)price - (int64_t)anchor + (int64_t)HALF_W * (int64_t)LADDER_TICK;
    in_win = on_grid && (o >= 0) && (o < (int64_t)WIN_SPAN);
    off = (uint32_t)o & 0xFFFFu;  // meaningful if in_win (WIN_SPAN <= 2^16)
}

struct Bbo {
    bool     bid_ok;
    bool     ask_ok;
    int32_t  bid_px;
    uint32_t bid_qty;
    int32_t  ask_px;
    uint32_t ask_qty;
};

inline int32_t level_price(int32_t anchor, uint32_t idx) {
#pragma HLS INLINE
    // exact: a non-empty level always corresponds to an Add price that fitted in int32
    return (int32_t)((uint32_t)anchor + (uint32_t)(((int32_t)idx - (int32_t)HALF_W) * (int32_t)LADDER_TICK));
}

inline uint32_t lq_addr(uint32_t bank, uint32_t idx) {
#pragma HLS INLINE
    return bank * (uint32_t)W + idx;
}
inline uint32_t bm_addr(uint32_t bank, uint32_t word) {
#pragma HLS INLINE
    return bank * (uint32_t)BM_WORDS + word;
}

// Best bid = highest non-empty level of the BID bank, best ask = lowest of the ASK bank.
// The priority encodes are precomputed when a bitmap / summary word is written
// (bm_hi/bm_lo, sm_hi/sm_lo), so the search is three dependent memory reads:
// summary -> bitmap word -> level quantity.
inline void ladder_bbo(const State& st, uint32_t prod, int32_t anchor, Bbo& b) {
#pragma HLS INLINE
    const uint32_t bb = bank_of(prod, dk1::BID);
    const uint32_t ab = bank_of(prod, dk1::ASK);
    const bool bid_ok = st.sm[bb] != 0;
    const bool ask_ok = st.sm[ab] != 0;
    const uint32_t bw = (uint32_t)st.sm_hi[bb] & (uint32_t)(BM_WORDS - 1);
    const uint32_t aw = (uint32_t)st.sm_lo[ab] & (uint32_t)(BM_WORDS - 1);
    const uint32_t bi = (bw << 5) | ((uint32_t)st.bm_hi[bm_addr(bb, bw)] & 31u);
    const uint32_t ai = (aw << 5) | ((uint32_t)st.bm_lo[bm_addr(ab, aw)] & 31u);
    const uint32_t bq = st.lq[lq_addr(bb, bi)];
    const uint32_t aq = st.lq[lq_addr(ab, ai)];
    b.bid_ok  = bid_ok;
    b.ask_ok  = ask_ok;
    b.bid_px  = bid_ok ? level_price(anchor, bi) : 0;
    b.bid_qty = bid_ok ? bq : 0u;
    b.ask_px  = ask_ok ? level_price(anchor, ai) : 0;
    b.ask_qty = ask_ok ? aq : 0u;
}

}  // namespace t2t

#endif
