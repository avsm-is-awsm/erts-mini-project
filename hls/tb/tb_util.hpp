// tb_util.hpp -- small C++98 helpers shared by the testbenches and host tools:
// whole-file I/O, a flat {"key": int} JSON reader, masked golden comparison.
#ifndef T2T_TB_UTIL_HPP
#define T2T_TB_UTIL_HPP

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "../src/core/types.hpp"

namespace tbu {

inline bool read_file(const std::string& path, std::vector<uint8_t>& buf) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    buf.resize((size_t)(n > 0 ? n : 0));
    size_t got = n > 0 ? fread(&buf[0], 1, (size_t)n, f) : 0;
    fclose(f);
    return got == (size_t)(n > 0 ? n : 0);
}

inline bool write_file(const std::string& path, const std::vector<uint8_t>& buf) {
    FILE* f = fopen(path.c_str(), "wb");
    if (!f) return false;
    size_t put = buf.empty() ? 0 : fwrite(&buf[0], 1, buf.size(), f);
    fclose(f);
    return put == buf.size();
}

inline bool file_exists(const std::string& path) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fclose(f);
    return true;
}

// Find "key" in a flat JSON object and parse the integer after the colon.
inline bool json_int(const std::string& js, const char* key, long long& v) {
    std::string pat = std::string("\"") + key + "\"";
    size_t p = js.find(pat);
    if (p == std::string::npos) return false;
    p = js.find(':', p + pat.size());
    if (p == std::string::npos) return false;
    ++p;
    while (p < js.size() && (js[p] == ' ' || js[p] == '\t' || js[p] == '\n' || js[p] == '\r')) ++p;
    v = strtoll(js.c_str() + p, 0, 10);
    return true;
}

inline t2t::Config load_config(const std::string& path, bool& ok) {
    t2t::Config c = t2t::default_config();
    std::vector<uint8_t> b;
    ok = read_file(path, b);
    if (!ok) return c;
    std::string js(b.begin(), b.end());
    long long v;
    if (json_int(js, "ctrl", v)) c.ctrl = (uint32_t)v;
    if (json_int(js, "clip_qty", v)) c.clip_qty = (uint32_t)v;
    if (json_int(js, "max_order_qty", v)) c.max_order_qty = (uint32_t)v;
    if (json_int(js, "max_pos", v)) c.max_pos = (int32_t)v;
    if (json_int(js, "collar", v)) c.collar = (uint32_t)v;
    if (json_int(js, "min_gap_ns", v)) c.min_gap_ns = (uint32_t)v;
    if (json_int(js, "max_orders", v)) c.max_orders = (uint32_t)v;
    if (json_int(js, "lockout_ms", v)) c.lockout_ms = (uint32_t)v;
    return c;
}

// Write counters (+ positions) as JSON, golden key order.  t_in fields optional.
inline bool write_counters(const std::string& path, const t2t::Counters& c, const int32_t* pos,
                           int npos, bool with_t) {
    FILE* f = fopen(path.c_str(), "w");
    if (!f) return false;
    fprintf(f, "{\n");
    fprintf(f, "  \"msgs_in\": %u,\n", c.msgs_in);
    fprintf(f, "  \"orders_out\": %u,\n", c.orders_out);
    fprintf(f, "  \"rejects\": %u,\n", c.rejects);
    fprintf(f, "  \"err_unknown_type\": %u,\n", c.err_unknown_type);
    fprintf(f, "  \"err_bad_product\": %u,\n", c.err_bad_product);
    fprintf(f, "  \"err_not_found\": %u,\n", c.err_not_found);
    fprintf(f, "  \"err_table_full\": %u,\n", c.err_table_full);
    fprintf(f, "  \"err_dup_id\": %u,\n", c.err_dup_id);
    fprintf(f, "  \"err_oow\": %u,\n", c.err_oow);
    fprintf(f, "  \"err_grid\": %u,\n", c.err_grid);
    fprintf(f, "  \"err_seq_gap\": %u,\n", c.err_seq_gap);
    fprintf(f, "  \"err_qty_over\": %u,\n", c.err_qty_over);
    fprintf(f, "  \"err_anchor\": %u,\n", c.err_anchor);
    fprintf(f, "  \"err_bad_state\": %u,\n", c.err_bad_state);
    fprintf(f, "  \"last_seq\": %u,\n", c.last_seq);
    if (with_t) {
        fprintf(f, "  \"first_t_in\": %u,\n", c.first_t_in);
        fprintf(f, "  \"last_t_in\": %u,\n", c.last_t_in);
    }
    fprintf(f, "  \"pos\": [");
    for (int i = 0; i < npos; ++i) fprintf(f, "%s%d", i ? ", " : "", pos[i]);
    fprintf(f, "]\n}\n");
    fclose(f);
    return true;
}

// Masked comparison (SPEC 5.3 + DECISIONS D-07).  Returns number of differing records;
// prints the first few.
inline long compare_records(const std::vector<uint8_t>& a, const std::vector<uint8_t>& g,
                            const char* what) {
    long na = (long)(a.size() / 32), ng = (long)(g.size() / 32);
    long bad = 0;
    if (na != ng) {
        printf("[%s] record count differs: got %ld, golden %ld\n", what, na, ng);
        bad++;
    }
    long n = na < ng ? na : ng;
    for (long r = 0; r < n; ++r) {
        const uint8_t* x = &a[(size_t)r * 32];
        const uint8_t* y = &g[(size_t)r * 32];
        int lim = (x[0] == 'Z' || y[0] == 'Z') ? 16 : 24;
        if (memcmp(x, y, (size_t)lim) != 0) {
            if (bad < 5) {
                printf("[%s] record %ld differs:\n  got   ", what, r);
                for (int i = 0; i < 32; ++i) printf("%02x", x[i]);
                printf("\n  gold  ");
                for (int i = 0; i < 32; ++i) printf("%02x", y[i]);
                printf("\n");
            }
            bad++;
        }
    }
    return bad;
}

}  // namespace tbu

#endif
