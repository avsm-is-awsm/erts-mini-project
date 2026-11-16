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
    uint32_t err_framing;              // HW only: truncated messages dropped by the IP
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
        // reset edge (RTL simulation).  Wait (bounded) until it is seen high, then until the
        // clear sweep is over, and finally verify that the state really was cleared instead of
        // trusting the busy flag alone.
        const uint64_t t0 = osal::Clock::now();
        uint64_t rise = osal::Clock::ticks_per_sec() / 10000u;  // 100 us
        if (rise == 0) rise = 1;
        while (!proxy_.busy()) {
            if (osal::Clock::now() - t0 > rise) break;
        }
        while (proxy_.busy()) {
            if (osal::Clock::now() - t0 > timeout_ticks) return false;
        }
        const HwCounters h = proxy_.counters();
        if (h.c.msgs_in != 0 || h.c.last_seq != 0 || h.c.orders_out != 0 || h.err_framing != 0) return false;
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
        if (!cm_.reset_and_apply(cfg, tps)) return fail(r, "soft reset failed (busy timeout or state not cleared)");
        r.ord = ddr(ORD_HW_BUF);
        r.dbg = ddr(DBG_HW_BUF);
        cache_.invalidate(r.ord, ORD_BUF_SIZE);
        if (dbg) cache_.invalidate(r.dbg, DBG_BUF_SIZE);
        if (!s2mm_ord_.start(ORD_HW_BUF, ORD_BUF_SIZE)) return fail(r, "S2MM (orders) start failed");
        // dbg_out arrives as TLAST-terminated packets of DBG_PKT records (D-30): re-arm per packet
        uint32_t dbg_off = 0;
        bool dbg_done = !dbg;
        const uint32_t pkt = (uint32_t)t2t::DBG_PKT * 32u;  // bytes per dbg_out packet
        if (dbg && !s2mm_dbg_.start(DBG_HW_BUF, pkt)) return fail(r, "S2MM (dbg) start failed");
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
                else if (got == 0 || (got % 32u) != 0) return fail(r, "dbg S2MM returned a partial record");
                else if (dbg_off + pkt > DBG_BUF_SIZE) return fail(r, "dbg buffer full");
                else if (!s2mm_dbg_.start(DBG_HW_BUF + dbg_off, pkt))
                    return fail(r, "S2MM (dbg) re-arm failed");
            }
            if (!ord_done && !s2mm_ord_.busy()) {
                // the transfer must have been ended by the TLAST of the 'Z' record
                const uint32_t got = s2mm_ord_.transferred();
                cache_.invalidate(r.ord, got);
                if (got < 32 || (got % 32u) != 0 || r.ord[got - 32] != dk1::O_END)
                    return fail(r, "order stream ended without 'Z' (order buffer overflow?)");
                ord_done = true;
            }
            if (mm2s_.error() || s2mm_ord_.error() || (dbg && s2mm_dbg_.error()))
                return fail(r, "AXI DMA reported an error (status register)");
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
        r.err_framing = h.err_framing;
        memcpy(r.pos, h.pos, sizeof(r.pos));
        // self-check (NFR-THR-2): every message sent was counted by the IP, none was cut short
        if (h.c.msgs_in != ds.n_msgs || h.err_framing != 0)
            return fail(r, "IP message count differs from the dataset (loss or framing error)");
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
                if (no + 32 > ORD_BUF_SIZE) {
                    strncpy(r.error, "order buffer overflow", sizeof(r.error) - 1);
                    return false;
                }
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
