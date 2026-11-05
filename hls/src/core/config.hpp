// config.hpp -- compile-time parameters (SPEC 6.1). Mirrored in ref/model.py (PARAMS).
// Override with -DT2T_P_MAX=... etc.  All must be powers of two except LADDER_TICK.
#ifndef T2T_CONFIG_HPP
#define T2T_CONFIG_HPP

#ifndef T2T_P_MAX
#define T2T_P_MAX 8
#endif
#ifndef T2T_SETS
#define T2T_SETS 1024
#endif
#ifndef T2T_WAYS
#define T2T_WAYS 4
#endif
#ifndef T2T_W
#define T2T_W 1024
#endif
#ifndef T2T_DBG_PKT
#define T2T_DBG_PKT 131072  // dbg_out TLAST every N records (4 MB S2MM packets, D-30)
#endif
#ifndef T2T_LADDER_TICK
#define T2T_LADDER_TICK 10
#endif

namespace t2t {

enum {
    P_MAX       = T2T_P_MAX,        // product slots
    SETS        = T2T_SETS,         // order-table sets
    WAYS        = T2T_WAYS,         // order-table associativity
    W           = T2T_W,            // ladder levels per side per product
    LADDER_TICK = T2T_LADDER_TICK,  // price units per ladder level
    DBG_PKT     = T2T_DBG_PKT,      // dbg_out records per TLAST-terminated packet
    BANKS       = P_MAX * 2,        // ladder banks: bank = product*2 + side
    BM_WORDS    = W / 32,           // level-1 bitmap words per bank (summary is 1 word)
    HALF_W      = W / 2,
    LQ_N        = BANKS * W,        // flattened ladder memory: lq[bank * W + level]
    BM_N        = BANKS * BM_WORDS, // flattened bitmap memory: bm[bank * BM_WORDS + word]
    // soft-reset clear sweep length: one entry of every memory per step
    CLR_N       = (LQ_N > SETS) ? LQ_N : SETS
};

}  // namespace t2t

#endif
