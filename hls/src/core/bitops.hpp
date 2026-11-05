// bitops.hpp -- 32-bit highest / lowest set-bit index (priority encoders).
// Under __SYNTHESIS__ (or -DT2T_HW_BITOPS) shallow nibble-group encoders; otherwise
// compiler builtins.  tests/test_core.cpp checks both forms against each other.  Result for x == 0 is 0 (callers check validity).
#ifndef T2T_BITOPS_HPP
#define T2T_BITOPS_HPP

#include <stdint.h>

namespace t2t {

// Hardware form: branch-free nibble-group priority encoders (pure AND/OR logic):
// 8 nibble-nonzero flags -> 3-bit group index -> 8:1 nibble select -> 2-bit index.
inline uint32_t msb4(uint32_t n) {  // n in [0,15]; 0 for n == 0
#pragma HLS INLINE
    const uint32_t b3 = (n >> 3) & 1u, b2 = (n >> 2) & 1u, b1 = (n >> 1) & 1u;
    const uint32_t hi = b3 | b2;
    const uint32_t lo = b3 | (~b2 & b1 & 1u);
    return (hi << 1) | lo;
}
inline uint32_t lsb4(uint32_t n) {  // n in [0,15]; 0 for n == 0
#pragma HLS INLINE
    const uint32_t b0 = n & 1u, b1 = (n >> 1) & 1u, b2 = (n >> 2) & 1u, b3 = (n >> 3) & 1u;
    const uint32_t hi = (~b0 & ~b1 & (b2 | b3)) & 1u;
    const uint32_t lo = (~b0 & (b1 | (~b2 & b3))) & 1u;
    return (hi << 1) | lo;
}
inline uint32_t nib_flags(uint32_t x) {  // bit k = nibble k non-zero
#pragma HLS INLINE
    uint32_t g = 0;
NZ:
    for (int k = 0; k < 8; ++k) {
#pragma HLS UNROLL
        const uint32_t n = (x >> (4 * k)) & 0xFu;
        g |= (((n >> 3) | (n >> 2) | (n >> 1) | n) & 1u) << k;
    }
    return g;
}
inline uint32_t nib_at(uint32_t x, uint32_t gi) {  // 8:1 nibble select
#pragma HLS INLINE
    uint32_t r = 0;
NSEL:
    for (int k = 0; k < 8; ++k) {
#pragma HLS UNROLL
        r |= ((x >> (4 * k)) & 0xFu) & (0u - (uint32_t)(gi == (uint32_t)k));
    }
    return r;
}

inline uint32_t msb32_hw(uint32_t x) {
#pragma HLS INLINE
    const uint32_t g = nib_flags(x);
    const uint32_t g_hi = ((g >> 4) != 0) ? 1u : 0u;
    const uint32_t gsel = (g >> (4 * g_hi)) & 0xFu;
    const uint32_t gi = (g_hi << 2) | msb4(gsel);
    return (gi << 2) | msb4(nib_at(x, gi));
}

inline uint32_t lsb32_hw(uint32_t x) {
#pragma HLS INLINE
    const uint32_t g = nib_flags(x);
    const uint32_t g_lo_empty = ((g & 0xFu) == 0) ? 1u : 0u;
    const uint32_t gsel = (g >> (4 * g_lo_empty)) & 0xFu;
    const uint32_t gi = (g_lo_empty << 2) | lsb4(gsel);
    return (gi << 2) | lsb4(nib_at(x, gi));
}

inline uint32_t msb32(uint32_t x) {
#pragma HLS INLINE
#if defined(__SYNTHESIS__) || defined(T2T_HW_BITOPS) || !defined(__GNUC__)
    return msb32_hw(x);
#else
    return x ? (uint32_t)(31 - __builtin_clz(x)) : 0u;
#endif
}

inline uint32_t lsb32(uint32_t x) {
#pragma HLS INLINE
#if defined(__SYNTHESIS__) || defined(T2T_HW_BITOPS) || !defined(__GNUC__)
    return lsb32_hw(x);
#else
    return x ? (uint32_t)__builtin_ctz(x) : 0u;
#endif
}

}  // namespace t2t

#endif
