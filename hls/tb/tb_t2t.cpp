// tb_t2t.cpp -- C testbench for t2t_core (csim with g++ + shim, Vivado HLS csim and cosim).
//
//   tb_t2t <dataset_dir> [out_dir] [max_msgs]
//
// Reads <dataset_dir>/feed.bin + config.json, drives md_in (TUSER = msg_index * 4),
// collects ord_out / dbg_out, writes csim_orders.bin, csim_bbo.bin, csim_counters.json
// to out_dir and compares against golden_orders.bin / golden_bbo.bin / golden_counters.json
// (timestamp bytes masked).  Exit code != 0 on any mismatch (HLS csim/cosim convention).
// If max_msgs is given, only the first max_msgs messages are fed and the golden files
// golden_orders_<max_msgs>.bin etc. are used when present (cosim subsets).
#include <stdio.h>
#include <stdlib.h>

#include <string>
#include <vector>

#include "../src/t2t_top.hpp"
#include "tb_util.hpp"

#ifndef TB_DEFAULT_DIR
#define TB_DEFAULT_DIR "data/out/cosim_10k"
#endif

struct Regs {
    uint32_t status, msgs_in, orders_out, rejects, err_unknown_type, err_bad_product,
        err_not_found, err_table_full, err_dup_id, err_oow, err_grid, err_seq_gap, err_qty_over,
        err_anchor, err_bad_state, last_seq, first_t_in, last_t_in, err_framing;
    int32_t pos[8];
};

static void call_core(hls::stream<axis_in_t>& in, hls::stream<axis_out_t>& ord,
                      hls::stream<axis_out_t>& dbg, const t2t::Config& c, uint32_t ctrl, Regs& r) {
    t2t_core(in, ord, dbg, ctrl, c.clip_qty, c.max_order_qty, c.max_pos, c.collar, c.min_gap_ns,
             c.max_orders, c.lockout_ms, &r.status, &r.msgs_in, &r.orders_out, &r.rejects,
             &r.err_unknown_type, &r.err_bad_product, &r.err_not_found, &r.err_table_full,
             &r.err_dup_id, &r.err_oow, &r.err_grid, &r.err_seq_gap, &r.err_qty_over,
             &r.err_anchor, &r.err_bad_state, &r.last_seq, &r.first_t_in, &r.last_t_in,
             &r.pos[0], &r.pos[1], &r.pos[2], &r.pos[3], &r.pos[4], &r.pos[5], &r.pos[6],
             &r.pos[7], &r.err_framing);
}

static void drain(hls::stream<axis_out_t>& s, std::vector<uint8_t>& buf, long& lasts) {
    while (!s.empty()) {
        axis_out_t b = s.read();
        uint8_t tmp[8];
        dk1::store_le64(tmp, (uint64_t)b.data);
        buf.insert(buf.end(), tmp, tmp + 8);
        if (b.last) lasts++;
    }
}

static long compare_counters(const std::string& gpath, const Regs& r) {
    std::vector<uint8_t> b;
    if (!tbu::read_file(gpath, b)) return 0;
    std::string js(b.begin(), b.end());
    struct { const char* k; uint32_t v; } items[] = {
        {"msgs_in", r.msgs_in}, {"orders_out", r.orders_out}, {"rejects", r.rejects},
        {"err_unknown_type", r.err_unknown_type}, {"err_bad_product", r.err_bad_product},
        {"err_not_found", r.err_not_found}, {"err_table_full", r.err_table_full},
        {"err_dup_id", r.err_dup_id}, {"err_oow", r.err_oow}, {"err_grid", r.err_grid},
        {"err_seq_gap", r.err_seq_gap}, {"err_qty_over", r.err_qty_over},
        {"err_anchor", r.err_anchor}, {"err_bad_state", r.err_bad_state},
        {"last_seq", r.last_seq}};
    long bad = 0;
    for (size_t i = 0; i < sizeof(items) / sizeof(items[0]); ++i) {
        long long g;
        if (tbu::json_int(js, items[i].k, g) && (uint32_t)g != items[i].v) {
            printf("[counters] %s: got %u golden %lld\n", items[i].k, items[i].v, g);
            bad++;
        }
    }
    return bad;
}

int main(int argc, char** argv) {
    std::string dir = argc > 1 ? argv[1] : TB_DEFAULT_DIR;
    std::string outdir = argc > 2 ? argv[2] : dir;
    long max_msgs = argc > 3 ? atol(argv[3]) : -1;

    std::vector<uint8_t> feed;
    if (!tbu::read_file(dir + "/feed.bin", feed) || feed.size() % 32) {
        printf("tb_t2t: cannot read %s/feed.bin (or size %% 32 != 0)\n", dir.c_str());
        return 2;
    }
    bool cfg_ok;
    t2t::Config cfg = tbu::load_config(dir + "/config.json", cfg_ok);
    if (!cfg_ok) printf("tb_t2t: no config.json, using defaults\n");
    long n = (long)(feed.size() / 32);
    if (max_msgs >= 0 && max_msgs < n) n = max_msgs;
    std::string sfx = (max_msgs >= 0) ? ("_" + std::string(argv[3])) : std::string("");

    hls::stream<axis_in_t> md_in;
    hls::stream<axis_out_t> ord_out, dbg_out;
    Regs r = Regs();  // AXI-Lite read-back registers (power-on 0)
    std::vector<uint8_t> ord_buf, dbg_buf;
    long ord_lasts = 0, dbg_lasts = 0;

    // soft reset: rising edge, then clear sweep until STATUS.busy drops
    call_core(md_in, ord_out, dbg_out, cfg, cfg.ctrl | t2t::CTRL_SOFT_RESET, r);
    int guard = 0;
    do {
        call_core(md_in, ord_out, dbg_out, cfg, cfg.ctrl, r);
    } while (r.status != 0 && ++guard < 2 * t2t::CLR_N + 4);
    if (r.status != 0) {
        printf("tb_t2t: soft reset did not finish\n");
        return 3;
    }

    for (long i = 0; i < n; ++i) {
        uint64_t w[4];
        dk1::bytes_to_beats(&feed[(size_t)i * 32], w);
        for (int k = 0; k < 4; ++k) {
            axis_in_t b;
            b.data = w[k];
            b.keep = 0xFF;
            b.strb = 0xFF;
            b.user = (k == 0) ? (uint32_t)(i * 4) : 0u;
            b.id = 0;
            b.dest = 0;
            b.last = (i == n - 1 && k == 3) ? 1 : 0;
            md_in.write(b);
        }
        call_core(md_in, ord_out, dbg_out, cfg, cfg.ctrl, r);
        drain(ord_out, ord_buf, ord_lasts);
        drain(dbg_out, dbg_buf, dbg_lasts);
    }

    tbu::write_file(outdir + "/csim_orders" + sfx + ".bin", ord_buf);
    tbu::write_file(outdir + "/csim_bbo" + sfx + ".bin", dbg_buf);
    t2t::Counters c;
    c.msgs_in = r.msgs_in; c.orders_out = r.orders_out; c.rejects = r.rejects;
    c.err_unknown_type = r.err_unknown_type; c.err_bad_product = r.err_bad_product;
    c.err_not_found = r.err_not_found; c.err_table_full = r.err_table_full;
    c.err_dup_id = r.err_dup_id; c.err_oow = r.err_oow; c.err_grid = r.err_grid;
    c.err_seq_gap = r.err_seq_gap; c.err_qty_over = r.err_qty_over; c.err_anchor = r.err_anchor;
    c.err_bad_state = r.err_bad_state; c.last_seq = r.last_seq; c.first_t_in = r.first_t_in;
    c.last_t_in = r.last_t_in;
    tbu::write_counters(outdir + "/csim_counters" + sfx + ".json", c, r.pos, 8, true);

    printf("tb_t2t: %ld msgs, %ld ord records (%ld TLAST), %ld dbg records (%ld TLAST)\n", n,
           (long)(ord_buf.size() / 32), ord_lasts, (long)(dbg_buf.size() / 32), dbg_lasts);

    long bad = 0;
    int compared = 0;
    std::vector<uint8_t> g;
    if (tbu::read_file(dir + "/golden_orders" + sfx + ".bin", g)) {
        bad += tbu::compare_records(ord_buf, g, "orders");
        compared++;
    }
    if ((cfg.ctrl & t2t::CTRL_DBG_EN) && tbu::read_file(dir + "/golden_bbo" + sfx + ".bin", g)) {
        bad += tbu::compare_records(dbg_buf, g, "bbo");
        compared++;
    }
    bad += compare_counters(dir + "/golden_counters" + sfx + ".json", r);
    if (!compared) {
        printf("tb_t2t: no golden files found in %s (run make ref first)\n", dir.c_str());
        return 4;
    }
    if (r.err_framing != 0) {
        printf("tb_t2t: unexpected err_framing = %u\n", r.err_framing);
        bad++;
    }
    printf("tb_t2t: %s (%ld mismatching items)\n", bad ? "FAIL" : "PASS", bad);
    return bad ? 1 : 0;
}
