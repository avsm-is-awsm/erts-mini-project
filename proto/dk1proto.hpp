// dk1proto.hpp -- C++ codec for the dk1-t2t wire format (SPEC section 5, proto/PROTOCOL.md).
//
// Shared by the HLS IP (Vivado HLS 2017.2), the g++ C-simulation, the ARM bare-metal
// application and the host tools.  Deliberately C++98: the HLS 2017.2 front end
// (clang 3.1 + libstdc++ 4.5) does not accept -std=c++0x.  No STL, no exceptions.
//
// A record is 32 bytes little-endian = 4 x 64-bit AXIS beats; beat k = bytes 8k..8k+7.
#ifndef DK1PROTO_HPP
#define DK1PROTO_HPP

#include <stdint.h>

namespace dk1 {

enum { REC_SIZE = 32, REC_BEATS = 4 };

// inbound message types
enum {
    T_ADD = 'A', T_DEL = 'X', T_EXEC = 'E', T_RED = 'M',
    T_STATE = 'S', T_FV = 'V', T_HB = 'H'
};
// outbound record types
enum { O_ORDER = 'O', O_REJECT = 'R', O_END = 'Z', O_BBO = 'B' };

enum { FLAG_SIDE = 0x01, FLAG_BIT1 = 0x02, FLAG_END = 0x02, FLAG_IOC = 0x02 };
enum { BID = 0, ASK = 1 };
enum { BUY = 0, SELL = 1 };
enum { ST_PREOPEN = 0, ST_OPEN = 1, ST_CLOSED = 2, ST_HALT = 3 };
enum { RJ_NONE = 0, RJ_COLLAR = 1, RJ_POSITION = 2, RJ_RATE = 3, RJ_MAX_ORDERS = 4 };

// ------------------------------------------------------------------ inbound
struct Msg {
    uint8_t  type;
    uint8_t  flags;
    uint16_t product;
    uint32_t seq;
    uint64_t ts_ns;
    uint64_t order_id;
    int32_t  price;
    uint32_t qty;
};

inline void pack_msg(const Msg& m, uint64_t w[4]) {
    w[0] = (uint64_t)m.type | ((uint64_t)m.flags << 8) | ((uint64_t)m.product << 16) |
           ((uint64_t)m.seq << 32);
    w[1] = m.ts_ns;
    w[2] = m.order_id;
    w[3] = (uint64_t)(uint32_t)m.price | ((uint64_t)m.qty << 32);
}

inline Msg unpack_msg(const uint64_t w[4]) {
    Msg m;
    m.type     = (uint8_t)(w[0] & 0xFF);
    m.flags    = (uint8_t)((w[0] >> 8) & 0xFF);
    m.product  = (uint16_t)((w[0] >> 16) & 0xFFFF);
    m.seq      = (uint32_t)(w[0] >> 32);
    m.ts_ns    = w[1];
    m.order_id = w[2];
    m.price    = (int32_t)(uint32_t)(w[3] & 0xFFFFFFFFu);
    m.qty      = (uint32_t)(w[3] >> 32);
    return m;
}

// ------------------------------------------------------------------ outbound
// 'O' order / 'R' reject on ord_out
struct OrdRec {
    uint8_t  type;
    uint8_t  flags;     // bit0 side, bit1 IOC
    uint16_t product;
    uint32_t trig_seq;
    uint32_t coid;      // client order id ('R': 0, see DECISIONS D-06)
    int32_t  price;
    uint32_t qty;       // 'R': requested qty before risk clamps
    uint32_t reason;    // 0 for 'O'
    uint32_t t_in;
    uint32_t t_out;
};

// 'Z' end of session (ord_out and, if dbg_en, dbg_out)
struct EndRec {
    uint32_t seq;
    uint32_t msgs_in;
    uint32_t orders_out;
    uint32_t first_t_in;
    uint32_t last_t_in;
    uint32_t t_in;
    uint32_t t_out;
};

// 'B' BBO record on dbg_out
struct BboRec {
    uint8_t  flags;     // bit0 bid_valid, bit1 ask_valid
    uint16_t product;
    uint32_t seq;
    int32_t  bid_px;
    uint32_t bid_qty;
    int32_t  ask_px;
    uint32_t ask_qty;
    uint32_t t_in;
    uint32_t t_out;
};

inline uint64_t lo_hi(uint32_t lo, uint32_t hi) { return (uint64_t)lo | ((uint64_t)hi << 32); }

inline void pack_ord(const OrdRec& r, uint64_t w[4]) {
    w[0] = (uint64_t)r.type | ((uint64_t)r.flags << 8) | ((uint64_t)r.product << 16) |
           ((uint64_t)r.trig_seq << 32);
    w[1] = lo_hi(r.coid, (uint32_t)r.price);
    w[2] = lo_hi(r.qty, r.reason);
    w[3] = lo_hi(r.t_in, r.t_out);
}

inline OrdRec unpack_ord(const uint64_t w[4]) {
    OrdRec r;
    r.type     = (uint8_t)(w[0] & 0xFF);
    r.flags    = (uint8_t)((w[0] >> 8) & 0xFF);
    r.product  = (uint16_t)((w[0] >> 16) & 0xFFFF);
    r.trig_seq = (uint32_t)(w[0] >> 32);
    r.coid     = (uint32_t)w[1];
    r.price    = (int32_t)(uint32_t)(w[1] >> 32);
    r.qty      = (uint32_t)w[2];
    r.reason   = (uint32_t)(w[2] >> 32);
    r.t_in     = (uint32_t)w[3];
    r.t_out    = (uint32_t)(w[3] >> 32);
    return r;
}

inline void pack_end(const EndRec& r, uint64_t w[4]) {
    w[0] = (uint64_t)O_END | ((uint64_t)r.seq << 32);
    w[1] = lo_hi(r.msgs_in, r.orders_out);
    w[2] = lo_hi(r.first_t_in, r.last_t_in);
    w[3] = lo_hi(r.t_in, r.t_out);
}

inline EndRec unpack_end(const uint64_t w[4]) {
    EndRec r;
    r.seq        = (uint32_t)(w[0] >> 32);
    r.msgs_in    = (uint32_t)w[1];
    r.orders_out = (uint32_t)(w[1] >> 32);
    r.first_t_in = (uint32_t)w[2];
    r.last_t_in  = (uint32_t)(w[2] >> 32);
    r.t_in       = (uint32_t)w[3];
    r.t_out      = (uint32_t)(w[3] >> 32);
    return r;
}

inline void pack_bbo(const BboRec& r, uint64_t w[4]) {
    w[0] = (uint64_t)O_BBO | ((uint64_t)r.flags << 8) | ((uint64_t)r.product << 16) |
           ((uint64_t)r.seq << 32);
    w[1] = lo_hi((uint32_t)r.bid_px, r.bid_qty);
    w[2] = lo_hi((uint32_t)r.ask_px, r.ask_qty);
    w[3] = lo_hi(r.t_in, r.t_out);
}

inline BboRec unpack_bbo(const uint64_t w[4]) {
    BboRec r;
    r.flags   = (uint8_t)((w[0] >> 8) & 0xFF);
    r.product = (uint16_t)((w[0] >> 16) & 0xFFFF);
    r.seq     = (uint32_t)(w[0] >> 32);
    r.bid_px  = (int32_t)(uint32_t)w[1];
    r.bid_qty = (uint32_t)(w[1] >> 32);
    r.ask_px  = (int32_t)(uint32_t)w[2];
    r.ask_qty = (uint32_t)(w[2] >> 32);
    r.t_in    = (uint32_t)w[3];
    r.t_out   = (uint32_t)(w[3] >> 32);
    return r;
}

// ------------------------------------------------------------------ byte <-> beat helpers
inline uint64_t load_le64(const uint8_t* p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; --i) v = (v << 8) | p[i];
    return v;
}

inline void store_le64(uint8_t* p, uint64_t v) {
    for (int i = 0; i < 8; ++i) { p[i] = (uint8_t)(v & 0xFF); v >>= 8; }
}

inline void bytes_to_beats(const uint8_t* rec, uint64_t w[4]) {
    for (int k = 0; k < 4; ++k) w[k] = load_le64(rec + 8 * k);
}

inline void beats_to_bytes(const uint64_t w[4], uint8_t* rec) {
    for (int k = 0; k < 4; ++k) store_le64(rec + 8 * k, w[k]);
}

}  // namespace dk1

#endif  // DK1PROTO_HPP
