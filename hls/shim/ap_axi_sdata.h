// Minimal ap_axiu / ap_uint shim for plain g++ builds (widths <= 64 only).
#ifndef T2T_SHIM_AP_AXI_SDATA_H
#define T2T_SHIM_AP_AXI_SDATA_H

#include <stdint.h>

template <int N>
struct ap_uint {
    uint64_t v;
    static uint64_t mask() { return (N >= 64) ? ~(uint64_t)0 : (((uint64_t)1 << N) - 1); }
    ap_uint() : v(0) {}
    ap_uint(uint64_t x) : v(x & mask()) {}
    ap_uint& operator=(uint64_t x) { v = x & mask(); return *this; }
    operator uint64_t() const { return v; }
    uint64_t to_uint64() const { return v; }
    unsigned to_uint() const { return (unsigned)v; }
};

template <int D, int U, int TI, int TD>
struct ap_axiu {
    ap_uint<D> data;
    ap_uint<(D + 7) / 8> keep;
    ap_uint<(D + 7) / 8> strb;
    ap_uint<U> user;
    ap_uint<1> last;
    ap_uint<TI> id;
    ap_uint<TD> dest;
};

#endif
