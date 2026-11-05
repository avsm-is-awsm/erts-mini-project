// types.hpp -- runtime config, counters, engine state and per-message output.
// Pure C++98, no HLS headers: shared by the HLS top, the g++ csim and the ARM SwEngine.
#ifndef T2T_TYPES_HPP
#define T2T_TYPES_HPP

#include <stdint.h>

#include "config.hpp"
#include "../../../proto/dk1proto.hpp"

namespace t2t {

// ---------------------------------------------------------------- runtime config (SPEC 6.7)
enum {
    CTRL_STRAT_EN   = 1u << 0,
    CTRL_KILL       = 1u << 1,
    CTRL_DBG_EN     = 1u << 2,
    CTRL_SOFT_RESET = 1u << 3
};

struct Config {
    uint32_t ctrl;
    uint32_t clip_qty;
    uint32_t max_order_qty;
    int32_t  max_pos;
    uint32_t collar;
    uint32_t min_gap_ns;
    uint32_t max_orders;
    uint32_t lockout_ms;
};

inline Config default_config() {
    Config c;
    c.ctrl = 0;
    c.clip_qty = 50;
    c.max_order_qty = 100;
    c.max_pos = 200;
    c.collar = 2000;
    c.min_gap_ns = 0;
    c.max_orders = 1000000;
    c.lockout_ms = 0;
    return c;
}

// ---------------------------------------------------------------- counters (SPEC 6.2)
struct Counters {
    uint32_t msgs_in;
    uint32_t orders_out;
    uint32_t rejects;
    uint32_t err_unknown_type;
    uint32_t err_bad_product;
    uint32_t err_not_found;
    uint32_t err_table_full;
    uint32_t err_dup_id;
    uint32_t err_oow;
    uint32_t err_grid;
    uint32_t err_seq_gap;
    uint32_t err_qty_over;
    uint32_t err_anchor;
    uint32_t err_bad_state;   // DECISIONS D-11 (extension)
    uint32_t last_seq;
    uint32_t first_t_in;      // HW/SW only, not in golden
    uint32_t last_t_in;       // HW/SW only, not in golden
};

// ---------------------------------------------------------------- order-table entry meta word
// meta[31] valid, meta[25] oow, meta[24] side, meta[23:16] product,
// meta[15:0] ladder offset = price - anchor + W/2*LADDER_TICK (level = offset / LADDER_TICK).
// Storing the offset (not the level) keeps the divide off the order-table write path.
enum {
    META_VALID = 1u << 31,
    META_OOW   = 1u << 25,
    META_SIDE  = 1u << 24
};

inline uint32_t meta_make(uint32_t prod, uint32_t side, uint32_t off, bool oow) {
    return META_VALID | (oow ? (uint32_t)META_OOW : 0u) | (side ? (uint32_t)META_SIDE : 0u) |
           ((prod & 0xFFu) << 16) | (off & 0xFFFFu);
}
inline bool     meta_valid(uint32_t m) { return (m & META_VALID) != 0; }
inline bool     meta_oow(uint32_t m) { return (m & META_OOW) != 0; }
inline uint32_t meta_side(uint32_t m) { return (m >> 24) & 1u; }
inline uint32_t meta_prod(uint32_t m) { return (m >> 16) & 0xFFu; }
inline uint32_t meta_off(uint32_t m) { return m & 0xFFFFu; }
inline uint64_t qm_make(uint32_t qty, uint32_t meta) { return (uint64_t)qty | ((uint64_t)meta << 32); }
inline uint32_t qm_qty(uint64_t qm) { return (uint32_t)qm; }
inline uint32_t qm_meta(uint64_t qm) { return (uint32_t)(qm >> 32); }

// ---------------------------------------------------------------- engine state (SPEC 6.2)
struct State {
    // order table: WAYS x SETS; each way is its own memory (parallel read)
    uint64_t t_oid[WAYS][SETS];
    uint64_t t_qm[WAYS][SETS];   // remaining qty (bits 31:0) | meta word (bits 63:32)

    // price ladder: bank = product*2 + side; flattened so each is one memory
    uint32_t lq[LQ_N];   // lq[bank*W + i]: aggregated qty at level i (mod 2^32)
    uint32_t bm[BM_N];   // bm[bank*BM_WORDS + j]: bit k = (lq[bank*W + 32j + k] != 0)
    uint8_t  bm_hi[BM_N];  // msb32(bm[...]), maintained on every bitmap write
    uint8_t  bm_lo[BM_N];  // lsb32(bm[...])
    uint32_t sm[BANKS];  // summary: bit j = (bm[bank*BM_WORDS + j] != 0)
    uint8_t  sm_hi[BANKS]; // msb32(sm[bank]) -> best bid word
    uint8_t  sm_lo[BANKS]; // lsb32(sm[bank]) -> best ask word

    // per product
    uint8_t  pstate[P_MAX];
    int32_t  anchor[P_MAX];
    uint8_t  anchor_res[P_MAX];  // grid_residue(anchor): Add grid check without a subtract
    uint64_t gc_ts[P_MAX];
    uint32_t live_count[P_MAX];

    // strategy
    int32_t  fv[P_MAX];
    uint32_t edge[P_MAX];
    bool     fv_valid[P_MAX];
    int32_t  last_buy_px[P_MAX];
    bool     last_buy_ok[P_MAX];   // false == NONE sentinel (DECISIONS D-04)
    int32_t  last_sell_px[P_MAX];
    bool     last_sell_ok[P_MAX];

    // risk
    int32_t  pos[P_MAX];
    uint64_t last_order_ts;
    bool     has_ordered;
    uint32_t next_coid;

    bool     have_first;
    Counters c;
};

// ---------------------------------------------------------------- per-message output
struct Out {
    bool     ord_valid;   // one 'O' / 'R' / 'Z' record on ord_out
    bool     ord_last;    // TLAST on beat 3 ('Z' only)
    uint64_t ord[4];
    bool     dbg_valid;   // one 'B' / 'Z' record on dbg_out
    bool     dbg_last;
    uint64_t dbg[4];
    bool     pos_upd;     // POS[pos_p] register update (AXI-Lite read-back)
    uint32_t pos_p;
    int32_t  pos_val;
};

}  // namespace t2t

#endif
