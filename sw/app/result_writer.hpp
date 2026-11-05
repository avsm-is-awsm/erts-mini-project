// result_writer.hpp -- ResultWriter (Observer): writes run outputs next to the dataset on SD.
//   orders_hw.bin / dbg_hw.bin / counters_hw.json   (HwEngine)
//   orders_sw.bin / dbg_sw.bin / counters_sw.json   (SwEngine)
//   run_info_<engine>.json: clock rates, wall time, message count, DMA chunks
#ifndef DK1_RESULT_WRITER_HPP
#define DK1_RESULT_WRITER_HPP

#include <stdio.h>

#include "session.hpp"

namespace app {

class ResultWriter : public ISessionObserver {
public:
    explicit ResultWriter(IFileSystem& fs, uint32_t pl_clk_hz = 100000000u) : fs_(fs), pl_hz_(pl_clk_hz) {}

    void on_state(SessionState, SessionState, const char*) {}

    void on_result(const Dataset& ds, const RunResult& r) {
        char path[160];
        snprintf(path, sizeof(path), "%s/orders_%s.bin", ds.dir, r.engine);
        fs_.write(path, r.ord, r.ord_len);
        if (r.dbg_len) {
            snprintf(path, sizeof(path), "%s/dbg_%s.bin", ds.dir, r.engine);
            fs_.write(path, r.dbg, r.dbg_len);
        }
        char js[2048];
        int n = counters_json(js, sizeof(js), r);
        snprintf(path, sizeof(path), "%s/counters_%s.json", ds.dir, r.engine);
        fs_.write(path, js, (uint32_t)n);
        n = snprintf(js, sizeof(js),
                     "{\n  \"engine\": \"%s\",\n  \"n_msgs\": %lu,\n  \"wall_ticks\": %llu,\n"
                     "  \"timer_hz\": %llu,\n  \"pl_clk_hz\": %lu,\n  \"dma_chunks\": %lu,\n"
                     "  \"ord_bytes\": %lu,\n  \"dbg_bytes\": %lu,\n"
                     "  \"t_units\": \"%s\"\n}\n",
                     r.engine, (unsigned long)r.n_msgs, (unsigned long long)r.wall_ticks,
                     (unsigned long long)r.ticks_per_sec, (unsigned long)pl_hz_,
                     (unsigned long)r.dma_chunks, (unsigned long)r.ord_len, (unsigned long)r.dbg_len,
                     (r.engine[0] == 'h') ? "PL cycles (FCLK0)" : "global timer ticks (CPU/2)");
        snprintf(path, sizeof(path), "%s/run_info_%s.json", ds.dir, r.engine);
        fs_.write(path, js, (uint32_t)n);
    }

    static int counters_json(char* js, size_t cap, const RunResult& r) {
        const t2t::Counters& c = r.counters;
        int n = snprintf(js, cap,
            "{\n  \"msgs_in\": %lu,\n  \"orders_out\": %lu,\n  \"rejects\": %lu,\n"
            "  \"err_unknown_type\": %lu,\n  \"err_bad_product\": %lu,\n  \"err_not_found\": %lu,\n"
            "  \"err_table_full\": %lu,\n  \"err_dup_id\": %lu,\n  \"err_oow\": %lu,\n"
            "  \"err_grid\": %lu,\n  \"err_seq_gap\": %lu,\n  \"err_qty_over\": %lu,\n"
            "  \"err_anchor\": %lu,\n  \"err_bad_state\": %lu,\n  \"last_seq\": %lu,\n"
            "  \"first_t_in\": %lu,\n  \"last_t_in\": %lu,\n  \"pos\": [",
            (unsigned long)c.msgs_in, (unsigned long)c.orders_out, (unsigned long)c.rejects,
            (unsigned long)c.err_unknown_type, (unsigned long)c.err_bad_product,
            (unsigned long)c.err_not_found, (unsigned long)c.err_table_full,
            (unsigned long)c.err_dup_id, (unsigned long)c.err_oow, (unsigned long)c.err_grid,
            (unsigned long)c.err_seq_gap, (unsigned long)c.err_qty_over,
            (unsigned long)c.err_anchor, (unsigned long)c.err_bad_state,
            (unsigned long)c.last_seq, (unsigned long)c.first_t_in, (unsigned long)c.last_t_in);
        for (int p = 0; p < t2t::P_MAX && n < (int)cap; ++p)
            n += snprintf(js + n, cap - (size_t)n, "%s%ld", p ? ", " : "", (long)r.pos[p]);
        n += snprintf(js + n, cap - (size_t)n, "]\n}\n");
        return n;
    }

private:
    IFileSystem& fs_;
    uint32_t pl_hz_;
};

}  // namespace app

#endif
