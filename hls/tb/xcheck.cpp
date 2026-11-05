// xcheck.cpp -- runs many independent random message sequences through core/engine.hpp
// (fresh state per sequence) so tests/test_xcheck.py can compare against ref/model.py.
//
//   xcheck <dir>
//   in : <dir>/xseq.bin  all messages (32 B each), sequences back to back
//        <dir>/xidx.bin  per sequence 9 x uint32: n_msgs, ctrl, clip_qty, max_order_qty,
//                        max_pos, collar, min_gap_ns, max_orders, lockout_ms
//   out: <dir>/xout_ord.bin, <dir>/xout_dbg.bin  concatenated records
//        <dir>/xout_cnt.bin  per sequence: n_ord, n_dbg, 15 counters, P_MAX positions (int32)
#include <stdio.h>

#include <string>
#include <vector>

#include "../src/core/engine.hpp"
#include "tb_util.hpp"

static t2t::State g_st;  // large: keep off the stack

int main(int argc, char** argv) {
    if (argc < 2) {
        printf("usage: xcheck <dir>\n");
        return 2;
    }
    std::string d = argv[1];
    std::vector<uint8_t> seq, idx;
    if (!tbu::read_file(d + "/xseq.bin", seq) || !tbu::read_file(d + "/xidx.bin", idx)) {
        printf("xcheck: cannot read inputs in %s\n", d.c_str());
        return 2;
    }
    const uint32_t* ix = (const uint32_t*)&idx[0];
    size_t nseq = idx.size() / (9 * 4);
    std::vector<uint8_t> ord, dbg;
    std::vector<int32_t> cnt;
    size_t off = 0;
    for (size_t s = 0; s < nseq; ++s) {
        const uint32_t* r = ix + 9 * s;
        uint32_t n = r[0];
        t2t::Config c;
        c.ctrl = r[1];
        c.clip_qty = r[2];
        c.max_order_qty = r[3];
        c.max_pos = (int32_t)r[4];
        c.collar = r[5];
        c.min_gap_ns = r[6];
        c.max_orders = r[7];
        c.lockout_ms = r[8];
        t2t::reset_all(g_st);
        uint32_t n_ord = 0, n_dbg = 0;
        for (uint32_t i = 0; i < n; ++i, ++off) {
            uint64_t w[4];
            dk1::bytes_to_beats(&seq[off * 32], w);
            t2t::Out o;
            t2t::process(w, i * 4u, c, g_st, o);
            uint8_t b[32];
            if (o.ord_valid) {
                dk1::beats_to_bytes(o.ord, b);
                ord.insert(ord.end(), b, b + 32);
                n_ord++;
            }
            if (o.dbg_valid) {
                dk1::beats_to_bytes(o.dbg, b);
                dbg.insert(dbg.end(), b, b + 32);
                n_dbg++;
            }
        }
        const t2t::Counters& k = g_st.c;
        int32_t v[] = {(int32_t)n_ord, (int32_t)n_dbg, (int32_t)k.msgs_in, (int32_t)k.orders_out,
                       (int32_t)k.rejects, (int32_t)k.err_unknown_type, (int32_t)k.err_bad_product,
                       (int32_t)k.err_not_found, (int32_t)k.err_table_full, (int32_t)k.err_dup_id,
                       (int32_t)k.err_oow, (int32_t)k.err_grid, (int32_t)k.err_seq_gap,
                       (int32_t)k.err_qty_over, (int32_t)k.err_anchor, (int32_t)k.err_bad_state,
                       (int32_t)k.last_seq};
        cnt.insert(cnt.end(), v, v + sizeof(v) / sizeof(v[0]));
        for (int p = 0; p < t2t::P_MAX; ++p) cnt.push_back(g_st.pos[p]);
    }
    tbu::write_file(d + "/xout_ord.bin", ord);
    tbu::write_file(d + "/xout_dbg.bin", dbg);
    std::vector<uint8_t> cb((const uint8_t*)&cnt[0], (const uint8_t*)&cnt[0] + cnt.size() * 4);
    tbu::write_file(d + "/xout_cnt.bin", cb);
    printf("xcheck: %u sequences, %u msgs, %u ord, %u dbg records\n", (unsigned)nseq, (unsigned)off,
           (unsigned)(ord.size() / 32), (unsigned)(dbg.size() / 32));
    return 0;
}
