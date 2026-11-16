// test_sw.cpp -- host unit tests of the PS application (g++ on the PC, no board, no Vitis).
//
// The "virtual board" (VirtualT2t) runs the real HLS top function t2t_core (via the hls/shim
// streams) behind mock IRegBus / IDmaChannel implementations, so the complete HwEngine path
// (proxy register offsets, soft reset polling, chunked MM2S, packetised dbg S2MM) is tested.
//
//   test_sw <dataset_dir> [<dataset_dir> ...]
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <map>
#include <string>
#include <vector>

#include "../../hls/src/t2t_top.hpp"
#include "../../hls/tb/tb_util.hpp"
#include "../app/cli.hpp"
#include "../app/engines.hpp"
#include "../app/result_writer.hpp"
#include "../app/session.hpp"

using namespace app;

static int g_fail = 0, g_pass = 0;
#define CHECK(c)                                                                      \
    do {                                                                              \
        if (c) ++g_pass;                                                              \
        else { ++g_fail; printf("  CHECK FAILED %s:%d: %s\n", __FILE__, __LINE__, #c); } \
    } while (0)

// ------------------------------------------------------------------ DDR arena
static uint8_t* g_arena = 0;
uint8_t* app::ddr(uint32_t addr) {
    if (!g_arena) g_arena = (uint8_t*)calloc(0x16000000u - IN_BUF, 1);
    return g_arena + (addr - IN_BUF);
}

// ------------------------------------------------------------------ in-memory SD card
class MemFs : public IFileSystem {
public:
    std::map<std::string, std::vector<uint8_t> > files;
    long read(const char* path, void* dst, uint32_t max) {
        std::map<std::string, std::vector<uint8_t> >::iterator it = files.find(path);
        if (it == files.end()) return -1;
        uint32_t n = (uint32_t)it->second.size() < max ? (uint32_t)it->second.size() : max;
        if (n) memcpy(dst, &it->second[0], n);
        return (long)n;
    }
    bool write(const char* path, const void* src, uint32_t len) {
        files[path].assign((const uint8_t*)src, (const uint8_t*)src + len);
        return true;
    }
    bool list(const char*, void (*cb)(const char*, uint32_t, void*), void* ctx) {
        for (std::map<std::string, std::vector<uint8_t> >::iterator it = files.begin(); it != files.end(); ++it)
            cb(it->first.c_str(), (uint32_t)it->second.size(), ctx);
        return true;
    }
    bool add_host_file(const std::string& host, const std::string& path) {
        std::vector<uint8_t> b;
        if (!tbu::read_file(host, b)) return false;
        files[path] = b;
        return true;
    }
};

class NullCache : public ICache {
public:
    void flush(const void*, uint32_t) {}
    void invalidate(const void*, uint32_t) {}
};

// ------------------------------------------------------------------ virtual t2t_core + DMA
class VirtualT2t : public IRegBus {
public:
    hls::stream<axis_in_t> md_in;
    hls::stream<axis_out_t> ord_out, dbg_out;
    uint32_t reg[0x130 / 4];
    uint32_t cycle;
    VirtualT2t() : cycle(0) { memset(reg, 0, sizeof(reg)); }

    void tick() {  // one invocation of the free-running IP (4 cycles)
        uint32_t* r = reg;
        t2t_core(md_in, ord_out, dbg_out, r[T2T_REG_CTRL / 4], r[T2T_REG_CLIP_QTY / 4],
                 r[T2T_REG_MAX_ORDER_QTY / 4], (int32_t)r[T2T_REG_MAX_POS / 4], r[T2T_REG_COLLAR / 4],
                 r[T2T_REG_MIN_GAP_NS / 4], r[T2T_REG_MAX_ORDERS / 4], r[T2T_REG_LOCKOUT_MS / 4],
                 &r[T2T_REG_STATUS / 4], &r[T2T_REG_MSGS_IN / 4], &r[T2T_REG_ORDERS_OUT / 4],
                 &r[T2T_REG_REJECTS / 4], &r[T2T_REG_ERR_UNKNOWN_TYPE / 4], &r[T2T_REG_ERR_BAD_PRODUCT / 4],
                 &r[T2T_REG_ERR_NOT_FOUND / 4], &r[T2T_REG_ERR_TABLE_FULL / 4], &r[T2T_REG_ERR_DUP_ID / 4],
                 &r[T2T_REG_ERR_OOW / 4], &r[T2T_REG_ERR_GRID / 4], &r[T2T_REG_ERR_SEQ_GAP / 4],
                 &r[T2T_REG_ERR_QTY_OVER / 4], &r[T2T_REG_ERR_ANCHOR / 4], &r[T2T_REG_ERR_BAD_STATE / 4],
                 &r[T2T_REG_LAST_SEQ / 4], &r[T2T_REG_FIRST_T_IN / 4], &r[T2T_REG_LAST_T_IN / 4],
                 (int32_t*)&r[T2T_REG_POS0 / 4], (int32_t*)&r[T2T_REG_POS1 / 4], (int32_t*)&r[T2T_REG_POS2 / 4],
                 (int32_t*)&r[T2T_REG_POS3 / 4], (int32_t*)&r[T2T_REG_POS4 / 4], (int32_t*)&r[T2T_REG_POS5 / 4],
                 (int32_t*)&r[T2T_REG_POS6 / 4], (int32_t*)&r[T2T_REG_POS7 / 4],
                 &r[T2T_REG_ERR_FRAMING / 4]);
        cycle += 4;
    }
    uint32_t read32(uint32_t off) {
        if (off == T2T_REG_STATUS) tick();  // time passes while the CPU polls
        return reg[off / 4];
    }
    void write32(uint32_t off, uint32_t v) {
        reg[off / 4] = v;
        if (off == T2T_REG_CTRL) tick();
    }
};

class VirtS2mm;

class VirtMm2s : public IDmaChannel {
public:
    VirtMm2s(VirtualT2t& c, VirtS2mm& o, VirtS2mm& d) : core(c), ord(o), dbg(d), msgs(0) {}
    bool start(uint64_t addr, uint32_t len);
    bool busy() { return false; }
    uint32_t transferred() { return 0; }
    void reset() {}
    VirtualT2t& core;
    VirtS2mm& ord;
    VirtS2mm& dbg;
    uint32_t msgs;
};

class VirtS2mm : public IDmaChannel {
public:
    VirtS2mm(hls::stream<axis_out_t>& s) : src(s), base(0), cap(0), got(0), armed(false), done(true) {}
    bool start(uint64_t addr, uint32_t len) {
        base = (uint32_t)addr; cap = len; got = 0; armed = true; done = false;
        drain();
        return true;
    }
    bool busy() { drain(); return armed && !done; }
    uint32_t transferred() { return got; }
    void reset() { armed = false; done = true; }
    void drain() {
        while (armed && !done && !src.empty() && got + 8 <= cap) {
            axis_out_t b = src.read();
            uint64_t d = (uint64_t)b.data;
            if ((got % 32) == 24) d = (d & 0xFFFFFFFFull) | ((uint64_t)(t_out++) << 32);  // fake probe
            dk1::store_le64(ddr(base) + got, d);
            got += 8;
            if (b.last) done = true;
        }
    }
    hls::stream<axis_out_t>& src;
    uint32_t base, cap, got;
    bool armed, done;
    uint32_t t_out;
};

bool VirtMm2s::start(uint64_t addr, uint32_t len) {
    const uint8_t* p = ddr((uint32_t)addr);
    for (uint32_t off = 0; off < len; off += 32) {
        for (int k = 0; k < 4; ++k) {
            axis_in_t b;
            b.data = dk1::load_le64(p + off + 8 * k);
            b.keep = 0xFF; b.strb = 0xFF; b.id = 0; b.dest = 0;
            b.user = (k == 0) ? (uint32_t)(msgs * 4u) : 0u;
            b.last = (off + 32 >= len && k == 3) ? 1 : 0;
            core.md_in.write(b);
        }
        ++msgs;
        core.tick();
        ord.drain();
        dbg.drain();
    }
    return true;
}

static int quiet_printf(const char*, ...) { return 0; }

// DMA channel that reports an error, and one that never finishes
class BadDma : public IDmaChannel {
public:
    explicit BadDma(bool err) : err_(err) {}
    bool start(uint64_t, uint32_t) { return true; }
    bool busy() { return !err_; }
    uint32_t transferred() { return 0; }
    void reset() {}
    bool error() { return err_; }
    bool err_;
};

static void push_beats(VirtualT2t& v, const uint64_t w[4], int n, bool last_on_final) {
    for (int k = 0; k < n; ++k) {
        axis_in_t b;
        b.data = w[k];
        b.keep = 0xFF; b.strb = 0xFF; b.id = 0; b.dest = 0;
        b.user = (k == 0) ? 77u : 0u;
        b.last = (last_on_final && k == n - 1) ? 1 : 0;
        v.md_in.write(b);
    }
}

// ------------------------------------------------------------------ tests
static void test_framing_recovery() {
    // A transfer that ends inside a message (TLAST on beat 0, 1 or 2) is dropped and counted;
    // the next message is processed normally (D-34).
    VirtualT2t v;
    v.reg[T2T_REG_CTRL / 4] = t2t::CTRL_DBG_EN;
    dk1::Msg m;
    memset(&m, 0, sizeof(m));
    m.type = 'S'; m.product = 1; m.seq = 1; m.ts_ns = 5; m.order_id = 1000; m.price = 5000; m.qty = dk1::ST_OPEN;
    uint64_t w[4];
    dk1::pack_msg(m, w);
    for (int cut = 1; cut <= 3; ++cut) {
        push_beats(v, w, cut, true);   // fragment of `cut` beats
        v.tick();
        CHECK(v.reg[T2T_REG_ERR_FRAMING / 4] == (uint32_t)cut);
        CHECK(v.reg[T2T_REG_MSGS_IN / 4] == 0 && v.md_in.empty() && v.ord_out.empty() && v.dbg_out.empty());
    }
    push_beats(v, w, 4, true);         // a complete message, TLAST on beat 3 is normal
    v.tick();
    CHECK(v.reg[T2T_REG_MSGS_IN / 4] == 1 && v.reg[T2T_REG_ERR_FRAMING / 4] == 3);
    CHECK(v.dbg_out.size() == 4);      // its 'B' record
    // soft reset clears the framing counter
    T2tProxy p(v);
    p.pulse_soft_reset(t2t::CTRL_DBG_EN);
    int guard = 0;
    while (p.busy() && ++guard < 40000) {}
    CHECK(guard < 40000 && v.reg[T2T_REG_ERR_FRAMING / 4] == 0 && v.reg[T2T_REG_MSGS_IN / 4] == 0);
}

static std::vector<uint8_t> tiny_feed(bool end_last, bool end_early) {
    std::vector<uint8_t> f(3 * 32, 0);
    for (int i = 0; i < 3; ++i) {
        f[32 * i] = 'H';
        f[32 * i + 4] = (uint8_t)(i + 1);
    }
    if (end_early) f[1] = 2;
    if (end_last) f[2 * 32 + 1] = 2;
    return f;
}

static void test_dataset_validation() {
    MemFs fs;
    DatasetLoader l(fs);
    Dataset ds;
    fs.files["a/feed.bin"] = tiny_feed(false, false);
    CHECK(*l.load("a", ds) != 0 && !ds.loaded);          // no END
    fs.files["b/feed.bin"] = tiny_feed(true, true);
    CHECK(*l.load("b", ds) != 0 && !ds.loaded);          // END before the last message
    fs.files["c/feed.bin"] = tiny_feed(true, false);
    CHECK(*l.load("c", ds) == 0 && ds.loaded && ds.n_msgs == 3);
}

static void test_hw_failures() {
    MemFs fs;
    fs.files["c/feed.bin"] = tiny_feed(true, false);
    DatasetLoader l(fs);
    SessionController s(l);
    CHECK(s.load("c"));
    NullCache cache;
    // 1. DMA error bit -> run fails with a message instead of hanging until the timeout
    {
        VirtualT2t core;
        VirtS2mm s2o(core.ord_out), s2d(core.dbg_out);
        s2o.t_out = 0; s2d.t_out = 0;
        BadDma bad(true);
        T2tProxy proxy(core);
        ConfigManager cm(proxy);
        HwEngine hw(cm, bad, s2o, s2d, cache);
        RunResult r;
        CHECK(!s.run(hw, r) && s.state() == S_ERROR && strstr(r.error, "DMA") != 0);
        CHECK(s.reset() && s.state() == S_LOADED);
    }
    // 2. IP that never comes out of / never clears on soft reset -> reported
    {
        struct DeadBus : public IRegBus {
            uint32_t read32(uint32_t off) { return off == T2T_REG_MSGS_IN ? 5u : 0u; }
            void write32(uint32_t, uint32_t) {}
        } dead;
        VirtualT2t core;
        VirtS2mm s2o(core.ord_out), s2d(core.dbg_out);
        s2o.t_out = 0; s2d.t_out = 0;
        VirtMm2s mm2s(core, s2o, s2d);
        T2tProxy proxy(dead);
        ConfigManager cm(proxy);
        HwEngine hw(cm, mm2s, s2o, s2d, cache);
        RunResult r;
        CHECK(!s.run(hw, r) && strstr(r.error, "soft reset") != 0);
        CHECK(s.reset());
    }
    // 3. result files that cannot be written are counted and logged
    {
        struct FullFs : public MemFs {
            bool write(const char*, const void*, uint32_t) { return false; }
        } full;
        full.files["c/feed.bin"] = tiny_feed(true, false);
        DatasetLoader l2(full);
        SessionController s2(l2);
        ResultWriter w(full);
        s2.attach(&w);
        CHECK(s2.load("c"));
        SwEngine sw;
        RunResult r;
        CHECK(s2.run(sw, r) && w.failures() == 1);
    }
}

static void test_cli_run_errors() {
    MemFs fs;
    fs.files["c/feed.bin"] = tiny_feed(true, false);
    DatasetLoader l(fs);
    SessionController s(l);
    SwEngine sw;
    BadDma bad(true);
    VirtualT2t core;
    VirtS2mm s2o(core.ord_out), s2d(core.dbg_out);
    s2o.t_out = 0; s2d.t_out = 0;
    NullCache cache;
    T2tProxy proxy(core);
    ConfigManager cm(proxy);
    HwEngine hw(cm, bad, s2o, s2d, cache);
    RunResult last;
    memset(&last, 0, sizeof(last));
    CliContext ctx = {&s, &hw, &sw, &cm, &fs, &last};
    Cli cli(ctx, quiet_printf);
    CmdRun run; CmdLoad load; CmdCfg cfg;
    cli.add(&run); cli.add(&load); cli.add(&cfg);
    char l0[] = "load c";
    CHECK(cli.execute_line(l0) && s.state() == S_LOADED);
    char l1[] = "cfg set collar 12abc";            // not a number: rejected, value unchanged
    cli.execute_line(l1);
    CHECK(s.config().collar == 2000);
    char l2[] = "run both";                        // hw fails (DMA error), sw must still run
    cli.execute_line(l2);
    CHECK(s.state() == S_DONE && last.engine && last.engine[0] == 's' && last.ok);
    char l3[] = "run sideways";
    cli.execute_line(l3);
    CHECK(s.state() == S_DONE);
}

struct Recorder : public ISessionObserver {
    std::vector<std::string> trans;
    int results;
    Recorder() : results(0) {}
    void on_state(SessionState f, SessionState t, const char*) {
        trans.push_back(std::string(state_name(f)) + ">" + state_name(t));
    }
    void on_result(const Dataset&, const RunResult&) { ++results; }
};


static void test_config_kv() {
    t2t::Config c = t2t::default_config();
    char text[] = "ctrl=5\nclip_qty=7\nmax_pos=-3\nbogus=1\nlockout_ms=1000\n";
    CHECK(parse_config_kv(text, c));
    CHECK(c.ctrl == 5 && c.clip_qty == 7 && c.max_pos == -3 && c.lockout_ms == 1000 && c.collar == 2000);
}

static void test_loader_errors() {
    MemFs fs;
    DatasetLoader l(fs);
    Dataset ds;
    CHECK(*l.load("nope", ds) != 0);
    fs.files["bad/feed.bin"] = std::vector<uint8_t>(33, 0);
    CHECK(*l.load("bad", ds) != 0 && !ds.loaded);
}

static void test_session_states() {
    MemFs fs;
    fs.files["d/feed.bin"] = std::vector<uint8_t>(32, 0);
    fs.files["d/feed.bin"][0] = 'H';
    fs.files["d/feed.bin"][1] = 2;  // END
    DatasetLoader l(fs);
    SessionController s(l);
    Recorder rec;
    s.attach(&rec);
    CHECK(s.state() == S_IDLE);
    CHECK(!s.configure());
    CHECK(s.load("d") && s.state() == S_LOADED);
    SwEngine sw;
    RunResult r;
    CHECK(s.run(sw, r) && s.state() == S_DONE);
    CHECK(rec.results == 1);
    const char* want[] = {"Idle>Loaded", "Loaded>Configured", "Configured>Running", "Running>Draining",
                          "Draining>Done"};
    CHECK(rec.trans.size() == 5);
    for (size_t i = 0; i < rec.trans.size() && i < 5; ++i) CHECK(rec.trans[i] == want[i]);
    CHECK(r.ord_len == 32 && r.ord[0] == 'Z');
    CHECK(!s.load("missing") && s.state() == S_ERROR);
    CHECK(s.reset() && s.state() == S_IDLE);  // a failed load invalidates the DDR dataset
}

static void test_cli() {
    MemFs fs;
    DatasetLoader l(fs);
    SessionController s(l);
    SwEngine sw;
    RunResult last;
    memset(&last, 0, sizeof(last));
    CliContext ctx = {&s, 0, &sw, 0, &fs, &last};
    Cli cli(ctx, quiet_printf);
    CmdCfg cfg; CmdKill kill; CmdHelp help;
    cli.add(&cfg); cli.add(&kill); cli.add(&help);
    char l1[] = "cfg set collar 123";
    CHECK(cli.execute_line(l1) && s.config().collar == 123);
    char l2[] = "cfg set max_pos -5";
    CHECK(cli.execute_line(l2) && s.config().max_pos == -5);
    char l3[] = "kill";
    CHECK(cli.execute_line(l3) && (s.config().ctrl & t2t::CTRL_KILL));
    char l4[] = "kill off";
    CHECK(cli.execute_line(l4) && !(s.config().ctrl & t2t::CTRL_KILL));
    char l5[] = "frobnicate";
    CHECK(!cli.execute_line(l5));
    char l6[] = "   ";
    CHECK(cli.execute_line(l6));
}

static void test_proxy_offsets() {
    VirtualT2t v;
    T2tProxy p(v);
    t2t::Config c = t2t::default_config();
    c.collar = 77; c.max_pos = -9; c.ctrl = 5;
    p.write_config(c);
    CHECK(v.reg[T2T_REG_COLLAR / 4] == 77 && (int32_t)v.reg[T2T_REG_MAX_POS / 4] == -9);
    CHECK(v.reg[T2T_REG_CTRL / 4] == 5);
}

static long compare_masked(const uint8_t* got, uint32_t got_len, const std::vector<uint8_t>& gold, const char* what,
                           const char* ds) {
    std::vector<uint8_t> g(got, got + got_len);
    long bad = tbu::compare_records(g, gold, what);
    if (bad) printf("  %s: %s mismatches %ld\n", ds, what, bad);
    return bad;
}

static void test_engines(const std::string& dir) {
    MemFs fs;
    std::vector<uint8_t> gold_o, gold_b;
    if (!fs.add_host_file(dir + "/feed.bin", "ds/feed.bin") ||
        !tbu::read_file(dir + "/golden_orders.bin", gold_o) || !tbu::read_file(dir + "/golden_bbo.bin", gold_b)) {
        printf("  skip %s (missing files)\n", dir.c_str());
        return;
    }
    // config.txt (key=value) is next to config.json
    if (!fs.add_host_file(dir + "/config.txt", "ds/config.txt")) printf("  (no config.txt, defaults)\n");
    DatasetLoader l(fs);
    SessionController s(l);
    ResultWriter w(fs);
    s.attach(&w);
    CHECK(s.load("ds"));
    printf("  %s: %lu msgs\n", dir.c_str(), (unsigned long)s.dataset().n_msgs);

    SwEngine sw;
    RunResult rs;
    CHECK(s.run(sw, rs));
    CHECK(compare_masked(rs.ord, rs.ord_len, gold_o, "sw orders", dir.c_str()) == 0);
    CHECK(compare_masked(rs.dbg, rs.dbg_len, gold_b, "sw bbo", dir.c_str()) == 0);
    CHECK(fs.files.count("ds/orders_sw.bin") && fs.files.count("ds/counters_sw.json"));

    VirtualT2t core;
    VirtS2mm s2o(core.ord_out), s2d(core.dbg_out);
    s2o.t_out = 1000; s2d.t_out = 1000;
    VirtMm2s mm2s(core, s2o, s2d);
    T2tProxy proxy(core);
    ConfigManager cm(proxy);
    NullCache cache;
    HwEngine hw(cm, mm2s, s2o, s2d, cache);
    RunResult rh;
    CHECK(s.run(hw, rh));
    if (!rh.ok) printf("  hw error: %s\n", rh.error);
    CHECK(compare_masked(rh.ord, rh.ord_len, gold_o, "hw orders", dir.c_str()) == 0);
    CHECK(compare_masked(rh.dbg, rh.dbg_len, gold_b, "hw bbo", dir.c_str()) == 0);
    CHECK(rh.counters.msgs_in == s.dataset().n_msgs && rh.counters.orders_out == rs.counters.orders_out);
    CHECK(memcmp(rh.pos, rs.pos, sizeof(rh.pos)) == 0);
    printf("  hw: %lu DMA chunks, %lu order bytes, %lu dbg bytes\n", (unsigned long)rh.dma_chunks,
           (unsigned long)rh.ord_len, (unsigned long)rh.dbg_len);
}

int main(int argc, char** argv) {
    test_config_kv();
    test_loader_errors();
    test_session_states();
    test_cli();
    test_proxy_offsets();
    test_framing_recovery();
    test_dataset_validation();
    test_hw_failures();
    test_cli_run_errors();
    for (int i = 1; i < argc; ++i) test_engines(argv[i]);
    printf("test_sw: %d checks passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
