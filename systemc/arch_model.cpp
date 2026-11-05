// arch_model.cpp -- loosely-timed SystemC model of the three HW/SW partitionings (SPEC 11).
//
//   A  all-SW    : DMA/memory -> ArmCpu (parse, book, BBO, strategy, risk, order)
//   B  split     : PlPipeline (parse, book, BBO) -> S2MM DMA -> IRQ -> ArmCpu (strategy, risk)
//                  -> AXI-Lite order write
//   C  all-HW    : PlPipeline (everything) at II = pl_ii_cycles, latency pl_latency_cycles
//
// Trace-driven: the message stream is the dataset's feed.bin and the set of messages that
// trigger an order is taken from golden_orders.bin ('O' and 'R'), so all three architectures
// see exactly the same work.  Two arrival modes:
//   replay -- messages back to back from DDR via 4 MB DMA chunks (the board measurement setup)
//   live   -- message k arrives at its exchange timestamp (queueing in the slow resources)
// Tick-to-trade of a triggering message = order out - message arrival at the system input.
//
//   arch_model <dataset_dir> <params.yaml> <out.json> [max_msgs]
#include <stdint.h>

#include <systemc.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

struct Params {
    std::map<std::string, double> v;
    double operator[](const std::string& k) const {
        std::map<std::string, double>::const_iterator it = v.find(k);
        if (it == v.end()) {
            std::fprintf(stderr, "missing parameter %s\n", k.c_str());
            std::exit(2);
        }
        return it->second;
    }
    static Params load(const std::string& path) {
        Params p;
        std::ifstream f(path.c_str());
        std::string line;
        while (std::getline(f, line)) {
            size_t h = line.find('#');
            if (h != std::string::npos) line = line.substr(0, h);
            size_t c = line.find(':');
            if (c == std::string::npos) continue;
            std::string k = line.substr(0, c);
            k.erase(0, k.find_first_not_of(" \t"));
            k.erase(k.find_last_not_of(" \t") + 1);
            if (!k.empty()) p.v[k] = std::atof(line.c_str() + c + 1);
        }
        return p;
    }
};

struct Trace {
    std::vector<uint64_t> ts;       // exchange time (ns)
    std::vector<bool> trig;         // message triggers an order / reject
    std::vector<bool> order_ok;     // 'O' (true) vs 'R'
    std::vector<bool> bbo_change;   // book message changed its product's BBO (from golden_bbo)
};

static bool read_file(const std::string& p, std::vector<unsigned char>& b) {
    std::ifstream f(p.c_str(), std::ios::binary);
    if (!f) return false;
    b.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    return true;
}

static Trace load_trace(const std::string& dir, size_t max_msgs) {
    Trace t;
    std::vector<unsigned char> feed, ord;
    if (!read_file(dir + "/feed.bin", feed) || !read_file(dir + "/golden_orders.bin", ord)) {
        std::fprintf(stderr, "cannot read %s/feed.bin or golden_orders.bin\n", dir.c_str());
        std::exit(2);
    }
    size_t n = std::min(feed.size() / 32, max_msgs);
    t.ts.resize(n);
    t.trig.assign(n, false);
    t.order_ok.assign(n, false);
    t.bbo_change.assign(n, true);
    std::vector<uint32_t> seq(n);
    for (size_t i = 0; i < n; ++i) {
        uint64_t ts = 0;
        for (int b = 7; b >= 0; --b) ts = (ts << 8) | feed[i * 32 + 8 + b];
        t.ts[i] = ts;
        seq[i] = feed[i * 32 + 4] | (feed[i * 32 + 5] << 8) | (feed[i * 32 + 6] << 16) | ((uint32_t)feed[i * 32 + 7] << 24);
    }
    std::map<uint32_t, size_t> idx;
    for (size_t i = 0; i < n; ++i) idx[seq[i]] = i;
    for (size_t r = 0; r + 32 <= ord.size(); r += 32) {
        unsigned char ty = ord[r];
        if (ty != 'O' && ty != 'R') continue;
        uint32_t s = ord[r + 4] | (ord[r + 5] << 8) | (ord[r + 6] << 16) | ((uint32_t)ord[r + 7] << 24);
        std::map<uint32_t, size_t>::iterator it = idx.find(s);
        if (it != idx.end()) {
            t.trig[it->second] = true;
            t.order_ok[it->second] = (ty == 'O');
        }
    }
    std::vector<unsigned char> bbo;
    if (read_file(dir + "/golden_bbo.bin", bbo)) {
        std::map<uint16_t, std::string> last;
        for (size_t r = 0; r + 32 <= bbo.size(); r += 32) {
            if (bbo[r] != 'B') continue;
            uint32_t s = bbo[r + 4] | (bbo[r + 5] << 8) | (bbo[r + 6] << 16) | ((uint32_t)bbo[r + 7] << 24);
            uint16_t prod = (uint16_t)(bbo[r + 2] | (bbo[r + 3] << 8));
            std::string key((const char*)&bbo[r + 1], 1);
            key.append((const char*)&bbo[r + 8], 16);
            std::map<uint32_t, size_t>::iterator it = idx.find(s);
            if (it == idx.end()) continue;
            t.bbo_change[it->second] = (last[prod] != key);
            last[prod] = key;
        }
    }
    return t;
}

// ------------------------------------------------------------------ transaction
struct Tok {
    uint32_t idx;
    double t_arrive_ns;  // arrival at the system input (start of tick-to-trade)
};
inline std::ostream& operator<<(std::ostream& o, const Tok& t) { return o << t.idx; }

// ------------------------------------------------------------------ modules
SC_MODULE(FeedSource) {
    sc_fifo_out<Tok> out;
    const Trace* tr;
    bool live;
    double ii_ns, chunk_msgs, chunk_gap_ns;
    void run() {
        const uint64_t t0 = tr->ts.empty() ? 0 : tr->ts[0];
        for (size_t i = 0; i < tr->ts.size(); ++i) {
            if (live) {
                double at = (double)(tr->ts[i] - t0);
                double now = sc_time_stamp().to_seconds() * 1e9;
                if (at > now) wait(sc_time(at - now, SC_NS));
            } else if (i > 0 && std::fmod((double)i, chunk_msgs) == 0.0) {
                wait(sc_time(chunk_gap_ns, SC_NS));  // DMA chunk re-arm gap
            }
            Tok t;
            t.idx = (uint32_t)i;
            t.t_arrive_ns = sc_time_stamp().to_seconds() * 1e9;
            out.write(t);
            if (!live) wait(sc_time(ii_ns, SC_NS));  // AXIS line rate (4 beats)
        }
    }
    SC_CTOR(FeedSource) : tr(0), live(false), ii_ns(40), chunk_msgs(1e18), chunk_gap_ns(0) { SC_THREAD(run); }
};

// Pipelined PL fast path: accepts one message per II, result after `latency_ns`.
SC_MODULE(PlPipeline) {
    sc_fifo_in<Tok> in;
    sc_fifo_out<Tok> out;      // tokens leaving the pipeline (orders for C, BBO updates for B)
    double ii_ns, latency_ns;
    const Trace* tr;
    bool all_msgs;             // B: BBO changes (+ triggers) go to the ARM; C: only triggers
    sc_event_queue done_q;
    std::vector<Tok> pending;  // FIFO of in-flight tokens (constant latency keeps order)
    size_t head;
    void accept() {
        for (;;) {
            Tok t = in.read();
            pending.push_back(t);
            done_q.notify(sc_time(latency_ns, SC_NS));
            wait(sc_time(ii_ns, SC_NS));
        }
    }
    void emit() {
        for (;;) {
            wait(done_q.default_event());
            Tok t = pending[head++];
            if (tr->trig[t.idx] || (all_msgs && tr->bbo_change[t.idx])) out.write(t);
        }
    }
    SC_CTOR(PlPipeline) : ii_ns(40), latency_ns(280), tr(0), all_msgs(false), head(0) {
        SC_THREAD(accept);
        SC_THREAD(emit);
    }
};

// Fixed-latency link (DMA S2MM + interrupt), unlimited concurrency.
SC_MODULE(Delay) {
    sc_fifo_in<Tok> in;
    sc_fifo_out<Tok> out;
    double ns;
    sc_event_queue q;
    std::vector<Tok> buf;
    size_t head;
    void take() {
        for (;;) {
            buf.push_back(in.read());
            q.notify(sc_time(ns, SC_NS));
        }
    }
    void give() {
        for (;;) {
            wait(q.default_event());
            out.write(buf[head++]);
        }
    }
    SC_CTOR(Delay) : ns(0), head(0) {
        SC_THREAD(take);
        SC_THREAD(give);
    }
};

// Single in-order Cortex-A9 core: service time per token, FIFO queueing.
SC_MODULE(ArmCpu) {
    sc_fifo_in<Tok> in;
    sc_fifo_out<Tok> out;
    const Trace* tr;
    double msg_ns, order_ns, out_ns;  // per message, extra per order, hand-off of an order
    bool stamp_on_read;               // replay A: the CPU pulls messages from DDR itself, so
                                      // tick-to-trade starts when it starts a message (SwEngine t_in)
    void run() {
        for (;;) {
            Tok t = in.read();
            if (stamp_on_read) t.t_arrive_ns = sc_time_stamp().to_seconds() * 1e9;
            double cost = msg_ns + (tr->trig[t.idx] ? order_ns : 0.0);
            wait(sc_time(cost, SC_NS));
            if (tr->trig[t.idx]) {
                if (out_ns > 0) wait(sc_time(out_ns, SC_NS));
                out.write(t);
            }
        }
    }
    SC_CTOR(ArmCpu) : tr(0), msg_ns(1000), order_ns(0), out_ns(0), stamp_on_read(false) { SC_THREAD(run); }
};

SC_MODULE(Sink) {
    sc_fifo_in<Tok> in;
    std::vector<double>* lat;
    void run() {
        for (;;) {
            Tok t = in.read();
            lat->push_back(sc_time_stamp().to_seconds() * 1e9 - t.t_arrive_ns);
        }
    }
    SC_CTOR(Sink) : lat(0) { SC_THREAD(run); }
};

// ------------------------------------------------------------------ one simulation
static std::vector<double> simulate(char arch, bool live, const Trace& tr, const Params& P) {
    std::vector<double> lat;
    const double clk_ns = 1e9 / P["pl_clk_hz"];
    const double ii_ns = P["pl_ii_cycles"] * clk_ns;
    const double chunk_msgs = P["dma_chunk_bytes"] / 32.0;

    sc_fifo<Tok> f_in(1 << 16), f_mid(1 << 16), f_mid2(1 << 16), f_out(1 << 16);
    FeedSource src("src");
    src.tr = &tr;
    src.live = live;
    src.ii_ns = ii_ns;
    src.chunk_msgs = chunk_msgs;
    src.chunk_gap_ns = P["dma_chunk_gap_ns"];
    src.out(f_in);
    Sink sink("sink");
    sink.lat = &lat;

    if (arch == 'C') {
        PlPipeline pl("pl");
        pl.tr = &tr;
        pl.ii_ns = ii_ns;
        pl.latency_ns = P["pl_latency_cycles"] * clk_ns;
        pl.in(f_in);
        pl.out(f_out);
        sink.in(f_out);
        sc_start();
    } else if (arch == 'A') {
        ArmCpu cpu("cpu");
        cpu.tr = &tr;
        cpu.msg_ns = P["arm_msg_ns"];
        cpu.order_ns = P["arm_order_extra_ns"];
        cpu.stamp_on_read = !live;
        cpu.in(f_in);
        cpu.out(f_out);
        sink.in(f_out);
        sc_start();
    } else {  // 'B'
        PlPipeline pl("pl");
        pl.tr = &tr;
        pl.all_msgs = true;
        pl.ii_ns = ii_ns;
        pl.latency_ns = P["pl_bbo_cycles"] * clk_ns;
        Delay link("link");
        link.ns = P["dma_s2mm_latency_ns"] + P["irq_latency_ns"];
        ArmCpu cpu("cpu");
        cpu.tr = &tr;
        cpu.msg_ns = P["arm_strategy_ns"];
        cpu.order_ns = 0;
        cpu.out_ns = P["axi_lite_write_ns"];
        pl.in(f_in);
        pl.out(f_mid);
        link.in(f_mid);
        link.out(f_mid2);
        cpu.in(f_mid2);
        cpu.out(f_out);
        sink.in(f_out);
        sc_start();
    }
    return lat;
}

static void put_stats(std::ostream& o, std::vector<double> v) {
    std::sort(v.begin(), v.end());
    o << "{\"n\": " << v.size();
    if (!v.empty()) {
        const double ps[] = {50, 90, 99, 99.9};
        const char* nm[] = {"p50", "p90", "p99", "p99.9"};
        o << ", \"min\": " << v.front() << ", \"max\": " << v.back();
        for (int i = 0; i < 4; ++i) {
            size_t k = (size_t)std::min((double)v.size() - 1, std::floor(ps[i] / 100.0 * (v.size() - 1) + 0.5));
            o << ", \"" << nm[i] << "\": " << v[k];
        }
    }
    o << "}";
}

int sc_main(int argc, char** argv) {
    if (argc < 4) {
        std::fprintf(stderr, "usage: arch_model <dataset_dir> <params.yaml> <out.json> [max_msgs]\n");
        return 2;
    }
    sc_report_handler::set_actions(SC_WARNING, SC_DO_NOTHING);
    const size_t max_msgs = argc > 4 ? (size_t)std::atoll(argv[4]) : (size_t)-1;
    const Trace tr = load_trace(argv[1], max_msgs);
    const Params P = Params::load(argv[2]);
    // SystemC allows one elaboration per process: run the requested (arch, mode) given by
    // DK1_SYSC_RUN (e.g. "A:replay"), or all six through child processes driven by the Makefile.
    const char* run = std::getenv("DK1_SYSC_RUN");
    std::string r = run ? run : "C:replay";
    const char arch = r[0];
    const bool live = r.find("live") != std::string::npos;
    std::vector<double> lat = simulate(arch, live, tr, P);
    std::ofstream o(argv[3]);
    o << "{\"arch\": \"" << arch << "\", \"mode\": \"" << (live ? "live" : "replay") << "\", \"msgs\": "
      << tr.ts.size() << ", \"sim_end_ns\": " << sc_time_stamp().to_seconds() * 1e9 << ", \"latency_ns\": ";
    put_stats(o, lat);
    o << "}\n";
    std::printf("arch %c %s: %zu orders, sim time %.3f s\n", arch, live ? "live" : "replay", lat.size(),
                sc_time_stamp().to_seconds());
    return 0;
}
