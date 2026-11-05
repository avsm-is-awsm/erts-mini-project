// test_core.cpp -- unit tests of the C++ core modules (SPEC 9.2), g++ only.
//   order table: hashing, collisions, full set, remove / re-insert, key = (oid, product)
//   ladder: best search at levels 0 and W-1, negative prices, out-of-window, grid residues
//   strategy: dedup by last price, fair-value refresh, lockout, kill switch
//   risk: every reject reason, clamps, position limits both sides
//   protocol: pack/unpack of every record type; hardware bit encoders vs builtins
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../src/core/engine.hpp"

using namespace t2t;

static int g_pass = 0, g_fail = 0;
#define CHECK(c)                                                                          \
    do {                                                                                  \
        if (c) ++g_pass;                                                                  \
        else { ++g_fail; printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); }          \
    } while (0)

static State g_st;
static uint32_t g_seq;

struct Res {
    Out o;
    dk1::OrdRec ord;
    dk1::BboRec bbo;
};

static Res send(char type, uint32_t prod, uint32_t side, uint64_t oid, int32_t price, uint32_t qty,
                uint64_t ts, const Config& cfg, uint8_t extra_flags = 0) {
    dk1::Msg m;
    m.type = (uint8_t)type;
    m.flags = (uint8_t)(side | extra_flags);
    m.product = (uint16_t)prod;
    m.seq = ++g_seq;
    m.ts_ns = ts;
    m.order_id = oid;
    m.price = price;
    m.qty = qty;
    uint64_t w[4];
    dk1::pack_msg(m, w);
    Res r;
    process(w, g_seq * 4u, cfg, g_st, r.o);
    memset(&r.ord, 0, sizeof(r.ord));
    memset(&r.bbo, 0, sizeof(r.bbo));
    if (r.o.ord_valid) r.ord = dk1::unpack_ord(r.o.ord);
    if (r.o.dbg_valid) r.bbo = dk1::unpack_bbo(r.o.dbg);
    return r;
}

static Config cfg_dbg() {
    Config c = default_config();
    c.ctrl = CTRL_DBG_EN;
    return c;
}

static void fresh() {
    reset_all(g_st);
    g_seq = 0;
}

static void open_product(uint32_t p, int32_t anchor, uint64_t gc, const Config& c) {
    send('S', p, 0, gc, anchor, dk1::ST_OPEN, 1, c);
}

static uint64_t ids_in_set(uint32_t set, int k) {  // k-th oid hashing to `set`
    int found = 0;
    for (uint64_t oid = 1;; ++oid)
        if (oid_set(oid) == set && found++ == k) return oid;
}

// ------------------------------------------------------------------ tests
static void test_hash() {
    CHECK(oid_fold(0) == 0);
    const uint64_t x = 0x0123456789ABCDEFull;
    uint64_t f = x;
    for (int s = 10; s <= 60; s += 10) f ^= x >> s;
    CHECK(oid_fold(x) == f);
    CHECK(oid_set(x) == (uint32_t)(f & (SETS - 1)));
}

static void test_table() {
    fresh();
    Config c = cfg_dbg();
    open_product(0, 10000, 1ull << 60, c);
    const uint32_t set = 77;
    uint64_t ids[WAYS + 2];
    for (int k = 0; k < WAYS + 2; ++k) ids[k] = ids_in_set(set, k);
    for (int k = 0; k < WAYS; ++k) send('A', 0, 0, ids[k], 9000 + 10 * k, 5, 2, c);
    CHECK(g_st.c.err_table_full == 0);
    send('A', 0, 0, ids[WAYS], 9500, 5, 3, c);                      // set full
    CHECK(g_st.c.err_table_full == 1);
    CHECK(g_st.live_count[0] == WAYS);
    send('X', 0, 0, ids[1], 0, 0, 4, c);                            // free way 1
    CHECK(meta_valid(g_st.t_qm[1][set] >> 32) == false);
    send('A', 0, 0, ids[WAYS + 1], 9600, 5, 5, c);                 // re-insert -> lowest free way (1)
    CHECK(g_st.t_oid[1][set] == ids[WAYS + 1]);
    send('X', 0, 0, ids[WAYS], 0, 0, 6, c);                         // the dropped one: not found
    CHECK(g_st.c.err_not_found == 1);
    send('A', 0, 0, ids[0], 9000, 5, 7, c);                         // duplicate
    CHECK(g_st.c.err_dup_id == 1);
    // key is (oid, product): same id on another product is a different order (D-10).
    // The table is shared by all products, so make room in set 77 first.
    open_product(1, 10000, 1ull << 60, c);
    send('A', 1, 0, ids[2], 9000, 5, 8, c);                          // set full -> table_full, not dup
    CHECK(g_st.c.err_dup_id == 1 && g_st.c.err_table_full == 2);
    send('X', 0, 0, ids[WAYS + 1], 0, 0, 9, c);
    send('A', 1, 0, ids[2], 9000, 5, 10, c);                         // now inserted for product 1
    CHECK(g_st.c.err_dup_id == 1 && g_st.live_count[1] == 1);
    send('X', 1, 0, ids[3], 0, 0, 11, c);                            // ids[3] lives on product 0 only
    CHECK(g_st.c.err_not_found == 2);
    CHECK(g_st.live_count[0] == WAYS - 1 && g_st.live_count[1] == 1);
}

static void test_ladder_edges() {
    fresh();
    Config c = cfg_dbg();
    const int32_t anchor = -1234560;  // negative prices
    open_product(2, anchor, 1ull << 60, c);
    const int32_t lo = anchor - HALF_W * LADDER_TICK;               // level 0
    const int32_t hi = anchor + (HALF_W - 1) * LADDER_TICK;         // level W-1
    Res r = send('A', 2, dk1::BID, 1, lo, 7, 2, c);
    CHECK(r.bbo.flags == 1 && r.bbo.bid_px == lo && r.bbo.bid_qty == 7);
    r = send('A', 2, dk1::ASK, 2, hi, 9, 3, c);
    CHECK(r.bbo.flags == 3 && r.bbo.ask_px == hi && r.bbo.ask_qty == 9);
    r = send('A', 2, dk1::BID, 3, hi, 4, 4, c);                     // best bid moves to W-1 (crossed ok)
    CHECK(r.bbo.bid_px == hi && r.bbo.bid_qty == 4);
    r = send('A', 2, dk1::ASK, 4, lo, 6, 5, c);                     // best ask moves to 0
    CHECK(r.bbo.ask_px == lo && r.bbo.ask_qty == 6);
    r = send('A', 2, dk1::ASK, 5, lo - LADDER_TICK, 6, 6, c);       // level -1: out of window
    CHECK(g_st.c.err_oow == 1 && r.bbo.ask_px == lo);
    r = send('A', 2, dk1::BID, 6, hi + LADDER_TICK, 6, 7, c);       // level W: out of window
    CHECK(g_st.c.err_oow == 2 && r.bbo.bid_px == hi);
    r = send('A', 2, dk1::BID, 7, lo + 3, 6, 8, c);                 // off grid
    CHECK(g_st.c.err_grid == 1);
    r = send('X', 2, 0, 4, 0, 0, 9, c);                             // ask back to W-1
    CHECK(r.bbo.ask_px == hi && r.bbo.ask_qty == 9);
    r = send('M', 2, 0, 3, 0, 1, 10, c);                            // reduce bid at W-1
    CHECK(r.bbo.bid_px == hi && r.bbo.bid_qty == 3);
    r = send('E', 2, 0, 3, 0, 99, 11, c);                           // over-execute removes it
    CHECK(g_st.c.err_qty_over == 1 && r.bbo.bid_px == lo);
    r = send('X', 2, 0, 5, 0, 0, 12, c);                            // remove an oow order: no ladder change
    CHECK(r.bbo.ask_px == hi && g_st.live_count[2] == 3);         // orders 1, 2, 6 remain
    // same-level aggregation and wrap of lq (mod 2^32, D-05)
    send('A', 2, dk1::ASK, 8, hi, 0xFFFFFFF7u, 13, c);              // 9 + (2^32 - 9) = 0 -> level empty
    CHECK(g_st.lq[lq_addr(bank_of(2, dk1::ASK), W - 1)] == 0);
}

static void test_grid_residue() {
    long bad = 0;
    for (long long v = -2147483648LL; v <= 2147483647LL; v += 7777777) {
        for (int k = 0; k < 20; ++k) {
            long long x = v + k;
            if (x > 2147483647LL) break;
            for (int a = -25; a <= 25; a += 5) {
                long long y = x + a;
                if (y < -2147483648LL || y > 2147483647LL) continue;
                bool same = ((x - y) % LADDER_TICK) == 0;
                if (same != (grid_residue((int32_t)x) == grid_residue((int32_t)y))) bad++;
            }
        }
    }
    CHECK(bad == 0);
    for (uint32_t off = 0; off < (uint32_t)WIN_SPAN; off += 10) CHECK(off_to_idx(off) == off / LADDER_TICK);
}

static void test_bitops() {
    long bad = 0;
    for (uint64_t v = 1; v <= 0xFFFFFFFFull; v += 104729) {
        uint32_t x = (uint32_t)v;
        if (msb32_hw(x) != (uint32_t)(31 - __builtin_clz(x))) bad++;
        if (lsb32_hw(x) != (uint32_t)__builtin_ctz(x)) bad++;
    }
    for (int i = 0; i < 32; ++i) {
        if (msb32_hw(1u << i) != (uint32_t)i || lsb32_hw(1u << i) != (uint32_t)i) bad++;
        if (msb32_hw(~0u >> i) != (uint32_t)(31 - i) || lsb32_hw(~0u << i) != (uint32_t)i) bad++;
    }
    CHECK(bad == 0);
}

static void test_strategy() {
    fresh();
    Config c = cfg_dbg();
    c.ctrl |= CTRL_STRAT_EN;
    const uint64_t gc = 1000000000ull;  // 1 s
    open_product(3, 5000, gc, c);
    send('A', 3, dk1::ASK, 1, 5020, 30, 10, c);
    Res r = send('V', 3, 0, 0, 5100, 50, 20, c);                    // ask 5020 <= 5050 -> BUY
    CHECK(r.o.ord_valid && r.ord.type == 'O' && r.ord.price == 5020 && r.ord.qty == 30 && r.ord.coid == 1);
    r = send('M', 3, 0, 1, 0, 10, 30, c);                           // same best ask: dedup
    CHECK(!r.o.ord_valid);
    r = send('V', 3, 0, 0, 5100, 50, 40, c);                        // new V clears last px -> fires
    CHECK(r.o.ord_valid && r.ord.qty == 20 && r.ord.coid == 2);
    c.lockout_ms = 1000;                                            // ts + 1 s >= gc: locked
    r = send('V', 3, 0, 0, 5100, 50, 50, c);
    CHECK(!r.o.ord_valid);
    c.lockout_ms = 0;
    c.ctrl |= CTRL_KILL;
    r = send('V', 3, 0, 0, 5100, 50, 60, c);                        // kill switch
    CHECK(!r.o.ord_valid);
    c.ctrl &= ~(uint32_t)CTRL_KILL;
    r = send('V', 3, 0, 0, 5100, 50, gc, c);                        // ts == gc: not before gate closure
    CHECK(!r.o.ord_valid);
    r = send('S', 3, 0, 0, 0, dk1::ST_HALT, 70, c);                 // halted
    r = send('V', 3, 0, 0, 5100, 50, 80, c);
    CHECK(!r.o.ord_valid);
    r = send('S', 3, 0, 0, 0, dk1::ST_OPEN, 90, c);                 // HALT -> OPEN keeps anchor and,
    CHECK(g_st.anchor[3] == 5000);                                  // with a valid FV, trades again
    CHECK(r.o.ord_valid && r.ord.type == 'O' && r.ord.coid == 3);
    r = send('V', 3, 0, 0, 4900, 20, 100, c);                       // SELL side: bid >= fv + edge
    CHECK(!r.o.ord_valid);
    send('A', 3, dk1::BID, 2, 4930, 5, 110, c);
    CHECK(g_st.c.orders_out == 4 && g_st.pos[3] == 50 + 20 - 5);    // bid 4930 >= 4920 -> SELL 5
}

static void test_risk() {
    Config c = default_config();
    c.ctrl = CTRL_STRAT_EN | CTRL_DBG_EN;
    const uint64_t gc = 1ull << 60;
    // 1 COLLAR
    fresh();
    open_product(0, 5000, gc, c);
    send('A', 0, dk1::ASK, 1, 5000, 10, 1, c);
    Res r = send('V', 0, 0, 0, 9000, 0, 2, c);
    CHECK(r.o.ord_valid && r.ord.type == 'R' && r.ord.reason == dk1::RJ_COLLAR && r.ord.coid == 0 &&
          g_st.c.rejects == 1);
    // 2 POSITION (buy side full) and SELL room 2*MAX_POS
    fresh();
    c.max_pos = 15;
    open_product(0, 5000, gc, c);
    send('A', 0, dk1::ASK, 1, 5000, 10, 1, c);
    r = send('V', 0, 0, 0, 5100, 50, 2, c);
    CHECK(r.ord.type == 'O' && r.ord.qty == 10);
    send('A', 0, dk1::ASK, 2, 4990, 10, 3, c);
    CHECK(g_st.pos[0] == 15);                                       // clamped to the room (5)
    r = send('A', 0, dk1::ASK, 3, 4980, 10, 4, c);
    CHECK(r.ord.type == 'R' && r.ord.reason == dk1::RJ_POSITION && r.ord.qty == 10);
    // negative MAX_POS: every order rejected
    fresh();
    c.max_pos = -1;
    open_product(0, 5000, gc, c);
    send('A', 0, dk1::BID, 1, 5200, 10, 1, c);
    r = send('V', 0, 0, 0, 5100, 50, 2, c);
    CHECK(r.ord.type == 'R' && r.ord.reason == dk1::RJ_POSITION && r.ord.flags == (dk1::SELL | dk1::FLAG_IOC));
    // 3 RATE
    fresh();
    c.max_pos = 200;
    c.min_gap_ns = 100;
    open_product(0, 5000, gc, c);
    send('A', 0, dk1::ASK, 1, 5000, 10, 1000, c);
    r = send('V', 0, 0, 0, 5100, 50, 1000, c);
    CHECK(r.ord.type == 'O');
    r = send('A', 0, dk1::ASK, 2, 4990, 10, 1050, c);
    CHECK(r.ord.type == 'R' && r.ord.reason == dk1::RJ_RATE);
    r = send('A', 0, dk1::ASK, 3, 4980, 10, 1100, c);
    CHECK(r.ord.type == 'O');
    // 4 MAX_ORDERS and clamps
    fresh();
    c.min_gap_ns = 0;
    c.max_orders = 1;
    c.clip_qty = 7;
    c.max_order_qty = 5;
    open_product(0, 5000, gc, c);
    send('A', 0, dk1::ASK, 1, 5000, 10, 1, c);
    r = send('V', 0, 0, 0, 5100, 50, 2, c);
    CHECK(r.ord.type == 'O' && r.ord.qty == 5);
    r = send('A', 0, dk1::ASK, 2, 4990, 10, 3, c);
    CHECK(r.ord.type == 'R' && r.ord.reason == dk1::RJ_MAX_ORDERS && r.ord.qty == 7);
    // no 'R' records without dbg_en, but rejects still counted
    c.ctrl = CTRL_STRAT_EN;
    r = send('A', 0, dk1::ASK, 3, 4980, 10, 4, c);
    CHECK(!r.o.ord_valid && g_st.c.rejects == 2);
}

static void test_protocol() {
    dk1::Msg m;
    m.type = 'A'; m.flags = 1; m.product = 7; m.seq = 0xDEADBEEF; m.ts_ns = 0x0102030405060708ull;
    m.order_id = ~0ull; m.price = -5; m.qty = 0xFFFFFFFFu;
    uint64_t w[4];
    dk1::pack_msg(m, w);
    dk1::Msg u = dk1::unpack_msg(w);
    CHECK(u.type == 'A' && u.flags == 1 && u.product == 7 && u.seq == 0xDEADBEEF && u.ts_ns == m.ts_ns &&
          u.order_id == ~0ull && u.price == -5 && u.qty == 0xFFFFFFFFu);
    uint8_t b[32];
    dk1::beats_to_bytes(w, b);
    CHECK(b[0] == 'A' && b[2] == 7 && b[24] == 0xFB && b[31] == 0xFF);
    dk1::OrdRec o = {dk1::O_ORDER, 3, 2, 9, 11, -77, 5, 0, 123, 456};
    dk1::pack_ord(o, w);
    dk1::OrdRec o2 = dk1::unpack_ord(w);
    CHECK(o2.price == -77 && o2.t_out == 456 && o2.coid == 11);
    dk1::BboRec bb = {3, 1, 42, -100, 1, 200, 2, 0, 0};
    dk1::pack_bbo(bb, w);
    CHECK(dk1::unpack_bbo(w).bid_px == -100 && (w[0] & 0xFF) == 'B');
    dk1::EndRec z = {9, 10, 2, 3, 4, 5, 6};
    dk1::pack_end(z, w);
    CHECK(dk1::unpack_end(w).orders_out == 2 && (w[0] & 0xFF) == 'Z');
}

static void test_reset() {
    fresh();
    Config c = cfg_dbg();
    open_product(0, 5000, 1ull << 60, c);
    send('A', 0, 0, 1, 5000, 3, 1, c);
    reset_all(g_st);
    CHECK(g_st.c.msgs_in == 0 && g_st.live_count[0] == 0 && g_st.pstate[0] == dk1::ST_PREOPEN);
    CHECK(g_st.next_coid == 1 && !g_st.has_ordered);
    bool clean = true;
    for (uint32_t i = 0; i < (uint32_t)LQ_N; ++i) clean = clean && g_st.lq[i] == 0;
    for (int w = 0; w < WAYS; ++w)
        for (int s = 0; s < SETS; ++s) clean = clean && g_st.t_qm[w][s] == 0;
    CHECK(clean);
}

int main() {
    test_hash();
    test_table();
    test_ladder_edges();
    test_grid_residue();
    test_bitops();
    test_strategy();
    test_risk();
    test_protocol();
    test_reset();
    printf("test_core: %d checks passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
