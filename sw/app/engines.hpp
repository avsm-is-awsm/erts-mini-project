// engines.hpp -- Strategy pattern: one session runs either engine through IEngine.
//   HwEngine  Architecture C: t2t_core in the PL, fed by AXI DMA (MM2S chunks, S2MM until TLAST)
//   SwEngine  Architecture A baseline: the *same* core/engine.hpp on the Cortex-A9, t_in/t_out
//             stamped with the global timer (lower 32 bits, ticks at CPU/2)
// ConfigManager applies the runtime configuration and soft reset through the proxy.
#ifndef DK1_ENGINES_HPP
#define DK1_ENGINES_HPP

#include <string.h>

#include "../../hls/src/core/engine.hpp"
#include "../osal/osal.hpp"
#include "dataset.hpp"
#include "platform.hpp"
#include "t2t_proxy.hpp"

namespace app {

struct RunResult {
    const char* engine;
    bool     ok;
    char     error[96];
    uint8_t* ord;  uint32_t ord_len;   // bytes ('O'/'R'/'Z' records)
    uint8_t* dbg;  uint32_t dbg_len;   // bytes ('B'/'Z' records)
    t2t::Counters counters;
    int32_t  pos[t2t::P_MAX];
    uint64_t wall_ticks;               // osal::Clock ticks, first input byte to last output
    uint64_t ticks_per_sec;
    uint32_t n_msgs;
    uint32_t dma_chunks;
};

class IEngine {
public:
    virtual ~IEngine() {}
    virtual const char* name() const = 0;
    virtual bool run(const Dataset& ds, const t2t::Config& cfg, RunResult& r) = 0;
};

// ------------------------------------------------------------------ configuration
class ConfigManager {
public:
    explicit ConfigManager(T2tProxy& p) : proxy_(p) {}
    // Full soft reset of the IP state, then the runtime configuration (SPEC 6.7).
    bool reset_and_apply(const t2t::Config& cfg, uint64_t timeout_ticks) {
        proxy_.write_config(cfg);
        proxy_.pulse_soft_reset(cfg.ctrl);
        // STATUS.busy is written at the end of the IP pipeline: it rises ~30 cycles after the
        // reset edge (RTL simulation), so wait before polling it.
        osal::Timer::sleep_us(5);
        const uint64_t t0 = osal::Clock::now();
        while (proxy_.busy()) {
            if (osal::Clock::now() - t0 > timeout_ticks) return false;
        }
        proxy_.write_config(cfg);
        return true;
    }
    void kill(bool on) {
        uint32_t c = proxy_.ctrl();
        proxy_.set_ctrl(on ? (c | t2t::CTRL_KILL) : (c & ~(uint32_t)t2t::CTRL_KILL));
    }
    HwCounters read() { return proxy_.counters(); }
private:
    T2tProxy& proxy_;
};

// ------------------------------------------------------------------ Architecture C
class HwEngine : public IEngine {
public:
    HwEngine(ConfigManager& cm, IDmaChannel& mm2s, IDmaChannel& s2mm_ord, IDmaChannel& s2mm_dbg, ICache& cache)
        : cm_(cm), mm2s_(mm2s), s2mm_ord_(s2mm_ord), s2mm_dbg_(s2mm_dbg), cache_(cache) {}
    const char* name() const { return "hw"; }

    bool run(const Dataset& ds, const t2t::Config& cfg, RunResult& r) {
        init(r, ds);
        const uint64_t tps = osal::Clock::ticks_per_sec();
        const bool dbg = (cfg.ctrl & t2t::CTRL_DBG_EN) != 0;
        if (dbg && ds.n_msgs > MAX_DBG_MSGS) return fail(r, "dbg_en needs <= 1.5 M messages (S2MM limit)");
        mm2s_.reset();
        s2mm_ord_.reset();
        s2mm_dbg_.reset();
        if (!cm_.reset_and_apply(cfg, tps)) return fail(r, "soft reset timeout (STATUS.busy)");
        r.ord = ddr(ORD_HW_BUF);
        r.dbg = ddr(DBG_HW_BUF);
        cache_.invalidate(r.ord, ORD_BUF_SIZE);
        if (dbg) cache_.invalidate(r.dbg, DBG_BUF_SIZE);
        if (!s2mm_ord_.start(ORD_HW_BUF, ORD_BUF_SIZE)) return fail(r, "S2MM (orders) start failed");
        // dbg_out arrives as TLAST-terminated packets of DBG_PKT records (D-30): re-arm per packet
        uint32_t dbg_off = 0;
        bool dbg_done = !dbg;
        if (dbg && !s2mm_dbg_.start(DBG_HW_BUF, DBG_PKT_BYTES)) return fail(r, "S2MM (dbg) start failed");
        const uint32_t total = ds.n_msgs * 32u;
        cache_.flush(ds.data, total);

        // Queuing pattern: bounded chunk queue, chunks sent back to back (< 8 MB each, D-29)
        osal::Queue<uint32_t, 64> q;
        for (uint32_t off = 0; off < total; off += DMA_CHUNK)
            if (!q.push(off)) return fail(r, "too many DMA chunks");
        bool ord_done = false;
        const uint64_t t0 = osal::Clock::now();
        for (;;) {
            if (!mm2s_.busy() && !q.empty()) {
                uint32_t off = 0;
                q.pop(off);
                const uint32_t len = (total - off < DMA_CHUNK) ? (total - off) : DMA_CHUNK;
                if (!mm2s_.start(IN_BUF + off, len)) return fail(r, "MM2S start failed");
                r.dma_chunks++;
            }
            if (!dbg_done && !s2mm_dbg_.busy()) {
                const uint32_t got = s2mm_dbg_.transferred();
                cache_.invalidate(r.dbg + dbg_off, got);
                const bool z = got >= 32 && r.dbg[dbg_off + got - 32] == dk1::O_END;
                dbg_off += got;
                if (z) dbg_done = true;
                else if (dbg_off + DBG_PKT_BYTES > DBG_BUF_SIZE) return fail(r, "dbg buffer full");
                else if (!s2mm_dbg_.start(DBG_HW_BUF + dbg_off, DBG_PKT_BYTES))
                    return fail(r, "S2MM (dbg) re-arm failed");
            }
            if (!ord_done && !s2mm_ord_.busy()) ord_done = true;
            if (ord_done && dbg_done && q.empty() && !mm2s_.busy()) break;
            if (osal::Clock::now() - t0 > 60 * tps)
                return fail(r, ord_done ? "dbg S2MM timeout" : "S2MM (orders) timeout: no 'Z' / TLAST");
        }
        r.wall_ticks = osal::Clock::now() - t0;
        r.ord_len = s2mm_ord_.transferred();
        r.dbg_len = dbg_off;
        cache_.invalidate(r.ord, r.ord_len);
        const HwCounters h = cm_.read();
        r.counters = h.c;
        memcpy(r.pos, h.pos, sizeof(r.pos));
        r.ok = true;
        return true;
    }

private:
    static void init(RunResult& r, const Dataset& ds) {
        memset(&r, 0, sizeof(r));
        r.engine = "hw";
        r.n_msgs = ds.n_msgs;
        r.ticks_per_sec = osal::Clock::ticks_per_sec();
    }
    static bool fail(RunResult& r, const char* msg) {
        r.ok = false;
        strncpy(r.error, msg, sizeof(r.error) - 1);
        return false;
    }
    static bool wait(IDmaChannel& ch, uint64_t t0, uint64_t timeout) {
        while (ch.busy())
            if (osal::Clock::now() - t0 > timeout) return false;
        return true;
    }
    ConfigManager& cm_;
    IDmaChannel& mm2s_;
    IDmaChannel& s2mm_ord_;
    IDmaChannel& s2mm_dbg_;
    ICache& cache_;
};

// ------------------------------------------------------------------ Architecture A
class SwEngine : public IEngine {
public:
    const char* name() const { return "sw"; }

    bool run(const Dataset& ds, const t2t::Config& cfg, RunResult& r) {
        memset(&r, 0, sizeof(r));
        r.engine = "sw";
        r.n_msgs = ds.n_msgs;
        r.ticks_per_sec = osal::Clock::ticks_per_sec();
        t2t::reset_all(state());
        uint8_t* ord = ddr(ORD_SW_BUF);
        uint8_t* dbg = ddr(DBG_SW_BUF);
        const bool dbg_en = (cfg.ctrl & t2t::CTRL_DBG_EN) != 0;
        if (dbg_en && ds.n_msgs > MAX_DBG_MSGS) {
            strncpy(r.error, "dbg_en needs <= 1.5 M messages", sizeof(r.error) - 1);
            return false;
        }
        uint32_t no = 0, nd = 0;
        t2t::Out o;
        const uint64_t t0 = osal::Clock::now();
        for (uint32_t i = 0; i < ds.n_msgs; ++i) {
            uint64_t w[4];
            dk1::bytes_to_beats(ds.data + 32u * i, w);
            const uint32_t t_in = (uint32_t)osal::Clock::now();
            t2t::process(w, t_in, cfg, state(), o);
            if (o.ord_valid) {
                dk1::beats_to_bytes(o.ord, ord + no);
                const uint32_t t_out = (uint32_t)osal::Clock::now();  // record complete
                memcpy(ord + no + 28, &t_out, 4);
                no += 32;
                if (no + 32 > ORD_BUF_SIZE) no -= 32;  // saturate (never expected)
            }
            if (o.dbg_valid) {
                dk1::beats_to_bytes(o.dbg, dbg + nd);
                nd += 32;
            }
        }
        r.wall_ticks = osal::Clock::now() - t0;
        r.ord = ord;
        r.ord_len = no;
        r.dbg = dbg;
        r.dbg_len = nd;
        r.counters = state().c;
        for (int p = 0; p < t2t::P_MAX; ++p) r.pos[p] = state().pos[p];
        r.ok = true;
        return true;
    }

private:
    static t2t::State& state() {
        static t2t::State st;  // ~0.3 MB: static, not on the stack
        return st;
    }
};

}  // namespace app

#endif
