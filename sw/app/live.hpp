// live.hpp -- live replay: the feed is released at the pace of its own market timestamps
// (optionally sped up), as a set of periodic real-time jobs (docs/09_realtime_analysis.md).
//
//   feed_job      (T = 1 ms,   highest priority)  releases the messages that are due, at most
//                 `budget` per job: HW -> one MM2S transfer (never waits for the DMA),
//                 SW -> runs them through the core on the Cortex-A9
//   monitor_job   (T = 10 ms)  drains / re-arms the S2MM channels, detects the end, and runs the
//                 stale-data guard: when released data is older than guard_ms, CTRL.kill is set
//                 (no orders on stale market data) and cleared again at guard_ms / 2
//   telemetry     (T = 100 ms, lowest periodic)  one '@T {json}' line on the UART
//
// Everything here is RTOS-independent: each job takes `now` (osal::Clock ticks) and the class
// is unit-tested on the PC with simulated time.  The FreeRTOS layer (main_rtos.cpp) only
// schedules the jobs and measures response times (TaskStats).
// Integer time only (no FPU use inside the tasks): the market-to-timer ratio is reduced exactly.
#ifndef DK1_LIVE_HPP
#define DK1_LIVE_HPP

#include <stdio.h>
#include <string.h>

#include "../../hls/src/core/engine.hpp"
#include "engines.hpp"

namespace app {

struct LiveParams {
    bool     hw;
    uint32_t speed;     // replay speed factor; 0 = everything is due at once (as fast as possible)
    uint32_t budget;    // max messages released per feed job (bounds the job's execution time)
    uint32_t guard_ms;  // stale-data guard threshold, 0 = off
    uint32_t max_s;     // stop releasing after this many seconds, 0 = no limit
};

// log2 histogram of lateness in timer ticks: bucket k holds values in [2^(k-1), 2^k)
struct LatHist {
    enum { NB = 40 };
    uint64_t b[NB];
    uint64_t n, max, sum;
    void clear() { memset(this, 0, sizeof(*this)); }
    void add(uint64_t v) {
        int k = v ? 64 - __builtin_clzll(v) : 0;  // smallest k with 2^k > v, one CLZ instruction
        if (k > NB - 1) k = NB - 1;
        ++b[k];
        ++n;
        sum += v;
        if (v > max) max = v;
    }
    // upper bound of the bucket that holds the p-quantile (p in per mille)
    uint64_t quantile_ub(uint32_t permille) const {
        if (!n) return 0;
        const uint64_t want = (n * permille + 999) / 1000;
        uint64_t acc = 0;
        for (int k = 0; k < NB; ++k) {
            acc += b[k];
            if (acc >= want) return k ? (1ull << k) : 0;
        }
        return max;
    }
};

// per periodic task, maintained by the scheduler layer
struct TaskStats {
    const char* name;
    uint32_t prio;
    uint64_t period;          // ticks
    uint32_t jobs, misses;    // misses: response > period (implicit deadline)
    uint64_t rmax, cmax, csum;  // max response (end - release), max/sum of end - start
    uint64_t rmin;              // min response: shows a constant release offset, if any
    void reset(const char* n, uint32_t p, uint64_t t) {
        memset(this, 0, sizeof(*this));
        name = n; prio = p; period = t;
    }
    void job(uint64_t release, uint64_t start, uint64_t end) {
        ++jobs;
        const uint64_t r = end - release, c = end - start;
        if (r > rmax) rmax = r;
        if (jobs == 1 || r < rmin) rmin = r;
        if (c > cmax) cmax = c;
        csum += c;
        if (r > period) ++misses;
    }
};

class LiveReplay {
public:
    typedef uint64_t (*ClockFn)();

    LiveReplay(ConfigManager* cm, IDmaChannel* mm2s, IDmaChannel* s2o, IDmaChannel* s2d, ICache* cache,
               ClockFn clock)
        : cm_(cm), mm2s_(mm2s), s2o_(s2o), s2d_(s2d), cache_(cache), clock_(clock), tasks_(0), n_tasks_(0) {
        memset(&st_, 0, sizeof(st_));
        error_[0] = 0;
        engine_[0] = 0;
        ds_ = 0;
        sched_ = 0;
    }

    // prepare() + arm(t0): everything slow (schedule table, IP soft reset, cache maintenance,
    // which disables interrupts in the Xilinx library) happens in prepare(), *before* the
    // release instant T0 is chosen; arm() only sets T0 (D-43).
    bool start(const Dataset& ds, const t2t::Config& cfg, const LiveParams& p, uint64_t t0, uint64_t tps) {
        if (!prepare(ds, cfg, p, tps)) return false;
        arm(t0);
        return true;
    }
    void arm(uint64_t t0) {
        t0_ = t0;
        st_.running = error_[0] == 0;
    }
    bool prepare(const Dataset& ds, const t2t::Config& cfg, const LiveParams& p, uint64_t tps) {
        ds_ = &ds;
        p_ = p;
        cfg_ = cfg;
        tps_ = tps;
        t0_ = 0;
        error_[0] = 0;
        memset(&st_, 0, sizeof(st_));
        lat_.clear();
        if (!ds.loaded || ds.n_msgs == 0) return fail("no dataset loaded");
        if (p.budget == 0) return fail("budget must be > 0");
        if (p.hw && !cm_) return fail("no hardware");
        dbg_ = (cfg.ctrl & t2t::CTRL_DBG_EN) != 0;
        if (dbg_ && ds.n_msgs > MAX_DBG_MSGS) return fail("dbg_en needs <= 1.5 M messages");
        // exact ratio ticks/ns = num/den (325 MHz -> 13/40), divided by the speed factor
        uint64_t a = tps, b = 1000000000ull;
        while (b) { const uint64_t t = a % b; a = b; b = t; }
        num_ = tps / a;
        den_ = (1000000000ull / a) * (p.speed ? p.speed : 1);
        ts0_ = ts(0);
        for (uint32_t i = 1; i < ds.n_msgs; ++i)
            if (ts(i) < ts(i - 1)) return fail("feed timestamps are not monotonic");
        if (p.speed && (ts(ds.n_msgs - 1) - ts0_) > (~0ull / num_)) return fail("feed spans too long a time");
        // Schedule table, computed here (before the release, not real-time): the jobs then read
        // sched(i) instead of doing a 64-bit division per message, which on the 32-bit Cortex-A9
        // is a library call (measured: 3.5 ms per 8192-message job before, docs/09).
        if ((uint64_t)ds.n_msgs * 8u > SCHED_BUF_SIZE) return fail("too many messages for the schedule table");
        uint64_t* tab = (uint64_t*)ddr(SCHED_BUF);
        sched_ = tab;
        for (uint32_t i = 0; i < ds.n_msgs; ++i) tab[i] = p.speed ? (ts(i) - ts0_) * num_ / den_ : 0;
        guard_ticks_ = (uint64_t)p.guard_ms * tps / 1000u;
        kill_ = false;
        if (p.hw) {
            mm2s_->reset();
            s2o_->reset();
            s2d_->reset();
            if (!cm_->reset_and_apply(cfg, tps)) return fail("soft reset failed");
            cache_->flush(ds.data, ds.n_msgs * 32u);
            cache_->invalidate(ddr(ORD_HW_BUF), ORD_BUF_SIZE);
            if (!s2o_->start(ORD_HW_BUF, ORD_BUF_SIZE)) return fail("S2MM (orders) start failed");
            dbg_off_ = 0;
            if (dbg_) {
                cache_->invalidate(ddr(DBG_HW_BUF), DBG_BUF_SIZE);
                if (!s2d_->start(DBG_HW_BUF, (uint32_t)t2t::DBG_PKT * 32u)) return fail("S2MM (dbg) start failed");
            }
        } else {
            t2t::reset_all(state());
        }
        ord_done_ = !p.hw;
        dbg_done_ = !p.hw || !dbg_;
        return true;
    }

    // ---------------------------------------------------------------- periodic jobs
    void feed_job(uint64_t now) {
        if (!st_.running || st_.released == ds_->n_msgs || st_.truncated) return;
        if (p_.max_s && now - t0_ > (uint64_t)p_.max_s * tps_) {
            st_.truncated = true;
            return;
        }
        const uint32_t due = due_count(now);
        uint32_t k = due - st_.released;
        if (k == 0) return;
        if (k > p_.budget) {
            k = p_.budget;
            ++st_.budget_hits;
        }
        const uint32_t first = st_.released;
        if (p_.hw) {
            if (mm2s_->busy()) {  // previous transfer still running: try again next period
                ++st_.dma_busy;
                return;
            }
            if (!mm2s_->start(IN_BUF + first * 32u, k * 32u)) {
                fail("MM2S start failed");
                return;
            }
            ++st_.transfers;
            for (uint32_t i = first; i < first + k; ++i) lat_.add(now - sched(i));
        } else {
            t2t::Config c = cfg_;
            if (kill_) c.ctrl |= t2t::CTRL_KILL;
            uint8_t* ord = ddr(ORD_SW_BUF);
            uint8_t* dbg = ddr(DBG_SW_BUF);
            t2t::Out o;
            for (uint32_t i = first; i < first + k; ++i) {
                uint64_t w[4];
                dk1::bytes_to_beats(ds_->data + 32u * i, w);
                const uint64_t t_in = clock_();
                t2t::process(w, (uint32_t)t_in, c, state(), o);
                const uint64_t t_out = clock_();
                if (o.ord_valid) {
                    if (st_.ord_bytes + 32 > ORD_BUF_SIZE) {
                        fail("order buffer overflow");
                        return;
                    }
                    dk1::beats_to_bytes(o.ord, ord + st_.ord_bytes);
                    const uint32_t t32 = (uint32_t)t_out;
                    memcpy(ord + st_.ord_bytes + 28, &t32, 4);
                    st_.ord_bytes += 32;
                }
                if (o.dbg_valid) {
                    dk1::beats_to_bytes(o.dbg, dbg + st_.dbg_bytes);
                    st_.dbg_bytes += 32;
                }
                lat_.add(t_out - sched(i));  // released = processed on the ARM
            }
        }
        st_.released += k;
        st_.last_release = now;
    }

    void monitor_job(uint64_t now) {
        if (!st_.running) return;
        // stale-data guard: age of the oldest message that is due but not yet released
        uint64_t stale = 0;
        if (st_.released < ds_->n_msgs && !st_.truncated) {
            const uint64_t s = sched(st_.released);
            if (s <= now) stale = now - s;
        }
        st_.stale = stale;
        if (guard_ticks_) {
            if (!kill_ && stale > guard_ticks_) set_kill(true);
            else if (kill_ && stale < guard_ticks_ / 2) set_kill(false);
        }
        if (p_.hw) {
            if (mm2s_->error() || s2o_->error() || (dbg_ && s2d_->error())) {
                fail("AXI DMA reported an error (status register)");
                return;
            }
            const HwCounters h = cm_->read();
            st_.msgs_in = h.c.msgs_in;
            st_.orders = h.c.orders_out;
            // re-arm the dbg channel for every packet that completed since the last job; at most
            // 8 per job, so the job's execution time stays bounded (RMA, docs/09)
            for (int pk = 0; pk < 8 && !dbg_done_ && !s2d_->busy(); ++pk) {
                const uint32_t pkt = (uint32_t)t2t::DBG_PKT * 32u;
                const uint32_t got = s2d_->transferred();
                uint8_t* dbg = ddr(DBG_HW_BUF);
                // only the last record is read here; the whole buffer is invalidated after the
                // run (a 4 MB invalidate took 3.8 ms, longer than the job's budget, docs/09)
                if (got >= 32) cache_->invalidate(dbg + dbg_off_ + got - 32, 32);
                const bool z = got >= 32 && dbg[dbg_off_ + got - 32] == dk1::O_END;
                dbg_off_ += got;
                if (z) dbg_done_ = true;
                else if (got == 0 || (got % 32u) != 0) { fail("dbg S2MM returned a partial record"); return; }
                else if (dbg_off_ + pkt > DBG_BUF_SIZE) { fail("dbg buffer full"); return; }
                else if (!s2d_->start(DBG_HW_BUF + dbg_off_, pkt)) { fail("S2MM (dbg) re-arm failed"); return; }
            }
            if (!ord_done_ && !s2o_->busy()) {
                const uint32_t got = s2o_->transferred();
                uint8_t* ord = ddr(ORD_HW_BUF);
                if (got >= 32) cache_->invalidate(ord + got - 32, 32);
                if (got < 32 || (got % 32u) != 0 || ord[got - 32] != dk1::O_END) {
                    fail("order stream ended without 'Z'");
                    return;
                }
                ord_done_ = true;
                st_.ord_bytes = got;
            }
            st_.dbg_bytes = dbg_off_;
        } else {
            st_.msgs_in = state().c.msgs_in;
            st_.orders = state().c.orders_out;
        }
        // complete when everything was released and both output streams ended with 'Z'; a
        // truncated run (max_s / operator stop) never sends END, so it ends at once
        if (st_.truncated || (st_.released == ds_->n_msgs && ord_done_ && dbg_done_)) {
            st_.running = false;
            st_.end = now;
        }
    }

    // one telemetry line; returns its length
    int telemetry(uint64_t now, char* buf, int cap) const {
        const uint64_t us = tps_ / 1000000u ? tps_ / 1000000u : 1;
        const uint32_t due = st_.running ? due_count(now) : st_.released;
        // compact keys: at 115200 baud every character costs 87 us of this job (docs/09)
        // t ms, m mode, x speed, n total, d due, r released, i IP msgs_in, o orders, s stale us,
        // p5/p9/mx lateness p50/p99/max us, k kill, ks kill events, bh budget hits, db dma busy,
        // u running, tk per task [jobs, misses, rmax us, cmax us, csum us]
        int n = snprintf(buf, (size_t)cap,
                         "@T {\"t\":%lu,\"m\":\"%s\",\"x\":%lu,\"n\":%lu,\"d\":%lu,\"r\":%lu,"
                         "\"i\":%lu,\"o\":%lu,\"s\":%lu,\"p5\":%lu,\"p9\":%lu,"
                         "\"mx\":%lu,\"k\":%d,\"ks\":%lu,\"bh\":%lu,\"db\":%lu,"
                         "\"u\":%d,\"tk\":[",
                         (unsigned long)((now - t0_) * 1000u / tps_), p_.hw ? "hw" : "sw", (unsigned long)p_.speed,
                         (unsigned long)ds_->n_msgs, (unsigned long)due, (unsigned long)st_.released,
                         (unsigned long)st_.msgs_in, (unsigned long)st_.orders, (unsigned long)(st_.stale / us),
                         (unsigned long)(lat_.quantile_ub(500) / us), (unsigned long)(lat_.quantile_ub(990) / us),
                         (unsigned long)(lat_.max / us), kill_ ? 1 : 0, (unsigned long)st_.kill_events,
                         (unsigned long)st_.budget_hits, (unsigned long)st_.dma_busy, st_.running ? 1 : 0);
        for (int i = 0; i < n_tasks_ && n < cap; ++i) {
            const TaskStats& t = tasks_[i];
            n += snprintf(buf + n, (size_t)(cap - n),
                          "%s[%lu,%lu,%lu,%lu,%lu]",
                          i ? "," : "", (unsigned long)t.jobs, (unsigned long)t.misses,
                          (unsigned long)(t.rmax / us), (unsigned long)(t.cmax / us), (unsigned long)(t.csum / us));
        }
        if (n < cap) n += snprintf(buf + n, (size_t)(cap - n), "]}\r\n");
        return n < cap ? n : cap - 1;
    }

    // after the run: results for the ResultWriter (engine "hw_live" / "sw_live")
    void result(RunResult& r) {
        memset(&r, 0, sizeof(r));
        snprintf(engine_, sizeof(engine_), "%s_live_x%lu", p_.hw ? "hw" : "sw", (unsigned long)p_.speed);
        r.engine = engine_;  // file names: orders_hw_live_x1000.bin, ...
        r.ok = error_[0] == 0 && st_.released == ds_->n_msgs;
        r.n_msgs = st_.released;
        r.ticks_per_sec = tps_;
        r.wall_ticks = (st_.end ? st_.end : st_.last_release) - t0_;
        r.dma_chunks = st_.transfers;
        r.ord = ddr(p_.hw ? ORD_HW_BUF : ORD_SW_BUF);
        r.ord_len = st_.ord_bytes;
        r.dbg = ddr(p_.hw ? DBG_HW_BUF : DBG_SW_BUF);
        r.dbg_len = st_.dbg_bytes;
        if (p_.hw) {
            cache_->invalidate(r.ord, ORD_BUF_SIZE);  // DMA wrote these during the run
            if (dbg_) cache_->invalidate(r.dbg, DBG_BUF_SIZE);
            const HwCounters h = cm_->read();
            r.counters = h.c;
            r.err_framing = h.err_framing;
            memcpy(r.pos, h.pos, sizeof(r.pos));
            if (r.ok && (h.c.msgs_in != ds_->n_msgs || h.err_framing != 0)) {
                r.ok = false;
                fail("IP message count differs from the dataset");
            }
        } else {
            r.counters = state().c;
            for (int p = 0; p < t2t::P_MAX; ++p) r.pos[p] = state().pos[p];
        }
        if (error_[0]) memcpy(r.error, error_, sizeof(r.error));  // same size, NUL-terminated
        else if (!r.ok) strncpy(r.error, st_.truncated ? "stopped by max_s" : "incomplete", sizeof(r.error) - 1);
    }

    // full statistics of the run (live_<mode>.json)
    int stats_json(char* js, int cap) const {
        const uint64_t us = tps_ / 1000000u ? tps_ / 1000000u : 1;
        int n = snprintf(js, (size_t)cap,
                         "{\n  \"mode\": \"%s\",\n  \"speed\": %lu,\n  \"budget\": %lu,\n  \"guard_ms\": %lu,\n"
                         "  \"max_s\": %lu,\n  \"timer_hz\": %llu,\n  \"n_msgs\": %lu,\n  \"released\": %lu,\n"
                         "  \"truncated\": %d,\n  \"wall_ticks\": %llu,\n  \"transfers\": %lu,\n"
                         "  \"budget_hits\": %lu,\n  \"dma_busy\": %lu,\n  \"kill_events\": %lu,\n"
                         "  \"error\": \"%s\",\n  \"lateness_ticks\": {\"n\": %llu, \"max\": %llu, \"sum\": %llu, "
                         "\"log2_buckets\": [",
                         p_.hw ? "hw" : "sw", (unsigned long)p_.speed, (unsigned long)p_.budget,
                         (unsigned long)p_.guard_ms, (unsigned long)p_.max_s, (unsigned long long)tps_,
                         (unsigned long)ds_->n_msgs, (unsigned long)st_.released, st_.truncated ? 1 : 0,
                         (unsigned long long)((st_.end ? st_.end : st_.last_release) - t0_),
                         (unsigned long)st_.transfers, (unsigned long)st_.budget_hits, (unsigned long)st_.dma_busy,
                         (unsigned long)st_.kill_events, error_, (unsigned long long)lat_.n,
                         (unsigned long long)lat_.max, (unsigned long long)lat_.sum);
        for (int k = 0; k < LatHist::NB && n < cap; ++k)
            n += snprintf(js + n, (size_t)(cap - n), "%s%llu", k ? ", " : "", (unsigned long long)lat_.b[k]);
        if (n < cap) n += snprintf(js + n, (size_t)(cap - n), "]},\n  \"tasks\": [");
        for (int i = 0; i < n_tasks_ && n < cap; ++i) {
            const TaskStats& t = tasks_[i];
            n += snprintf(js + n, (size_t)(cap - n),
                          "%s\n    {\"name\": \"%s\", \"prio\": %lu, \"period_ticks\": %llu, \"jobs\": %lu, "
                          "\"misses\": %lu, \"rmin_ticks\": %llu, \"rmax_ticks\": %llu, \"cmax_ticks\": %llu, \"csum_ticks\": %llu}",
                          i ? "," : "", t.name, (unsigned long)t.prio, (unsigned long long)t.period,
                          (unsigned long)t.jobs, (unsigned long)t.misses, (unsigned long long)t.rmin,
                          (unsigned long long)t.rmax,
                          (unsigned long long)t.cmax, (unsigned long long)t.csum);
        }
        if (n < cap) n += snprintf(js + n, (size_t)(cap - n), "\n  ]\n}\n");
        (void)us;
        return n < cap ? n : cap - 1;
    }

    void set_tasks(const TaskStats* t, int n) { tasks_ = t; n_tasks_ = n; }
    bool running() const { return st_.running; }
    bool finished() const { return !st_.running; }
    void abort(uint64_t now) {  // operator stop: no more releases, outputs stay incomplete
        st_.truncated = true;
        st_.running = false;
        st_.end = now;
    }
    const char* error() const { return error_; }
    const LatHist& lateness() const { return lat_; }
    uint32_t released() const { return st_.released; }
    uint32_t kill_events() const { return st_.kill_events; }
    bool kill_active() const { return kill_; }
    uint32_t budget_hits() const { return st_.budget_hits; }

    // schedule of message i in timer ticks (public for tests)
    uint64_t sched(uint32_t i) const { return t0_ + sched_[i]; }  // table holds offsets from T0
    uint32_t due_count(uint64_t now) const {  // first i with sched(i) > now
        if (now < t0_) return st_.released;
        uint32_t lo = st_.released, hi = ds_->n_msgs;
        while (lo < hi) {
            const uint32_t mid = lo + (hi - lo) / 2;
            if (sched(mid) <= now) lo = mid + 1;
            else hi = mid;
        }
        return lo;
    }

private:
    struct St {
        bool running, truncated;
        uint32_t released, transfers, budget_hits, dma_busy, kill_events;
        uint32_t msgs_in, orders, ord_bytes, dbg_bytes;
        uint64_t stale, last_release, end;
    };
    uint64_t ts(uint32_t i) const { return dk1::load_le64(ds_->data + 32u * i + 8); }
    void set_kill(bool on) {
        kill_ = on;
        if (on) ++st_.kill_events;
        if (p_.hw) cm_->kill(on);
    }
    bool fail(const char* msg) {
        if (!error_[0]) strncpy(error_, msg, sizeof(error_) - 1);
        error_[sizeof(error_) - 1] = 0;
        st_.running = false;
        return false;
    }
    static t2t::State& state() {
        static t2t::State st;  // separate from SwEngine's state
        return st;
    }

    ConfigManager* cm_;
    IDmaChannel *mm2s_, *s2o_, *s2d_;
    ICache* cache_;
    ClockFn clock_;
    const Dataset* ds_;
    const uint64_t* sched_;
    LiveParams p_;
    t2t::Config cfg_;
    uint64_t tps_, t0_, ts0_, num_, den_, guard_ticks_;
    bool dbg_, ord_done_, dbg_done_;
    volatile bool kill_;
    uint32_t dbg_off_;
    St st_;
    LatHist lat_;
    const TaskStats* tasks_;
    int n_tasks_;
    char error_[96];
    char engine_[24];
};

}  // namespace app

#endif
