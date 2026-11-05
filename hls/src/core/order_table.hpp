// order_table.hpp -- 4-way set-associative order table (SPEC 6.3), bit-exact hashing.
// Every way is a separate memory so a set is read in parallel in one cycle.
// Key = (order_id, product): FR-02 "per-product L3 order table" (DECISIONS D-10).
#ifndef T2T_ORDER_TABLE_HPP
#define T2T_ORDER_TABLE_HPP

#include "types.hpp"

namespace t2t {

inline uint64_t oid_fold(uint64_t oid) {
#pragma HLS INLINE
    return oid ^ (oid >> 10) ^ (oid >> 20) ^ (oid >> 30) ^ (oid >> 40) ^ (oid >> 50) ^ (oid >> 60);
}

inline uint32_t oid_set(uint64_t oid) {
#pragma HLS INLINE
    return (uint32_t)(oid_fold(oid) & (uint64_t)(SETS - 1));
}

// Result of reading one set.
struct SetView {
    uint64_t oid[WAYS];
    uint32_t qty[WAYS];
    uint32_t meta[WAYS];
    uint64_t qm[WAYS];  // raw qty|meta words (for write-back)
};

inline void table_read_set(const State& st, uint32_t set, SetView& v) {
#pragma HLS INLINE
TABLE_READ:
    for (int w = 0; w < WAYS; ++w) {
#pragma HLS UNROLL
        v.oid[w]  = st.t_oid[w][set];
        v.qm[w]   = st.t_qm[w][set];
        v.qty[w]  = qm_qty(v.qm[w]);
        v.meta[w] = qm_meta(v.qm[w]);
    }
}

// lookup: way with valid && oid match && same product, or -1 (at most one exists)
inline int table_find(const SetView& v, uint64_t oid, uint32_t prod) {
#pragma HLS INLINE
    int hit = -1;
TABLE_FIND:
    for (int w = WAYS - 1; w >= 0; --w) {
#pragma HLS UNROLL
        if (meta_valid(v.meta[w]) && v.oid[w] == oid && meta_prod(v.meta[w]) == prod) hit = w;
    }
    return hit;
}

// insert target: lowest-index way with !valid, or -1 (set full)
inline int table_free_way(const SetView& v) {
#pragma HLS INLINE
    int fw = -1;
TABLE_FREE:
    for (int w = WAYS - 1; w >= 0; --w) {
#pragma HLS UNROLL
        if (!meta_valid(v.meta[w])) fw = w;
    }
    return fw;
}

}  // namespace t2t

#endif
