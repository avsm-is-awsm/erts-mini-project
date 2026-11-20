// main_rtos.cpp -- dk1-t2t PS application on FreeRTOS 9 (freertos901_xilinx, SDK 2017.2).
//
// Same drivers, engines and CLI as the bare-metal build (main.cpp), plus the real-time live
// replay (live.hpp) as fixed-priority periodic tasks, analysed with RMA / response-time
// analysis in docs/09_realtime_analysis.md:
//
//   task       prio  period  job
//   feed         4    1 ms   LiveReplay::feed_job      (release due messages, budget-bounded)
//   monitor      3   10 ms   LiveReplay::monitor_job   (S2MM drain, end detection, stale guard)
//   telemetry    2  100 ms   one '@T {json}' line on the UART (holds the UART mutex)
//   cli          1    --     command line, polls the UART every tick (background)
//   (tick ISR, 1 kHz, above all tasks; idle task priority 0)
//
// All periodic tasks are released together at one tick T0 (the critical instant of RMA).
// Release times are exact: tick k of the FreeRTOS tick and the global timer both run at
// CPU/2, so release_k = T0 + k * period in global-timer ticks.  Each job records its response
// time (end - release) and end - start; `wcet` measures execution times in isolation.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

#include "board.hpp"
#include "cli.hpp"
#include "engines.hpp"
#include "live.hpp"
#include "result_writer.hpp"
#include "session.hpp"
#include "xil_io.h"
#include "xparameters.h"
#include "xuartps_hw.h"

#ifndef XPAR_T2T_CORE_0_S_AXI_CTRL_BASEADDR
#error "t2t_core AXI-Lite base address missing from xparameters.h (check the hardware handoff)"
#endif
#if configTICK_RATE_HZ != 1000
#error "the RTOS build expects a 1 kHz tick (configbsp tick_rate 1000)"
#endif
#ifdef STDIN_BASEADDRESS
#define UART_BASE STDIN_BASEADDRESS
#else
#define UART_BASE XPAR_PS7_UART_1_BASEADDR
#endif
#define SCU_TIMER_LOAD (XPAR_PS7_SCUTIMER_0_BASEADDR + 0x00u)
#define SCU_TIMER_COUNTER (XPAR_PS7_SCUTIMER_0_BASEADDR + 0x04u)
#define SCU_TIMER_ISR (XPAR_PS7_SCUTIMER_0_BASEADDR + 0x0Cu)

using namespace app;

enum { PRIO_CLI = 1, PRIO_TEL = 2, PRIO_MON = 3, PRIO_FEED = 4, PRIO_BENCH = 6, PRIO_PROBE = 7 };

static void uses_fpu() {
#ifdef portTASK_USES_FLOATING_POINT
    portTASK_USES_FLOATING_POINT();  // the compiler may use VFP registers in any task
#endif
}

// ------------------------------------------------------------------ UART (shared, mutex)
static osal::Mutex* g_uart_mutex = 0;
static int uart_printf(const char* fmt, ...) {
    if (g_uart_mutex) g_uart_mutex->lock();
    va_list ap;
    va_start(ap, fmt);
    const int n = vprintf(fmt, ap);
    va_end(ap);
    fflush(stdout);
    if (g_uart_mutex) g_uart_mutex->unlock();
    return n;
}
static bool uart_getc(char& c) {  // non-blocking
    if (!XUartPs_IsReceiveData(UART_BASE)) return false;
    c = (char)XUartPs_ReadReg(UART_BASE, XUARTPS_FIFO_OFFSET);
    return true;
}

static uint64_t clock_now() { return osal::Clock::now(); }

// ------------------------------------------------------------------ application objects
static Cli* g_cli = 0;
static SessionController* g_session = 0;
static ResultWriter* g_writer = 0;
static IFileSystem* g_fs = 0;
static LiveReplay* g_live = 0;

// ------------------------------------------------------------------ periodic tasks
struct Periodic {
    const char* name;
    UBaseType_t prio;
    uint32_t period_ms;
    void (*job)(uint64_t now);
    TaskStats* st;
    TickType_t t0_tick;
    uint64_t t0_time;
    volatile bool stop;
    SemaphoreHandle_t exited;
};

static void job_feed(uint64_t now) { g_live->feed_job(now); }
static void job_monitor(uint64_t now) { g_live->monitor_job(now); }
static char g_tel_buf[1024];
static void job_telemetry(uint64_t now) {
    if (g_uart_mutex) g_uart_mutex->lock();
    const int n = g_live->telemetry(now, g_tel_buf, sizeof(g_tel_buf));
    fwrite(g_tel_buf, 1, (size_t)n, stdout);
    fflush(stdout);
    if (g_uart_mutex) g_uart_mutex->unlock();
}

static void periodic_task(void* arg) {
    uses_fpu();
    Periodic* p = (Periodic*)arg;
    const uint64_t period = osal::Clock::ticks_per_sec() * p->period_ms / 1000u;
    // Block until T0 first: vTaskDelayUntil() assumes its previous wake time is in the past; a
    // future one is taken for a tick-counter wrap and the task would never block (found on the
    // board: the feed task spun at priority 4 and starved the CLI, D-43).
    const TickType_t now_tick = xTaskGetTickCount();
    if ((TickType_t)(p->t0_tick - now_tick) < (TickType_t)0x80000000u && p->t0_tick != now_tick)
        vTaskDelay(p->t0_tick - now_tick);
    TickType_t last = p->t0_tick;
    for (uint64_t k = 1; !p->stop; ++k) {
        vTaskDelayUntil(&last, pdMS_TO_TICKS(p->period_ms));
        if (p->stop) break;
        const uint64_t release = p->t0_time + k * period;
        const uint64_t start = osal::Clock::now();
        p->job(start);
        const uint64_t end = osal::Clock::now();
        p->st->job(release, start, end);
    }
    xSemaphoreGive(p->exited);
    vTaskDelete(0);
}

// Time of the most recent tick in global-timer ticks.  The SCU private timer (tick source) and
// the global timer both count at CPU/2, so the time since the last tick is LOAD - COUNTER.
static void tick_epoch(TickType_t& tick, uint64_t& at) {
    taskENTER_CRITICAL();
    const uint64_t now = osal::Clock::now();
    tick = xTaskGetTickCount();
    const uint32_t load = Xil_In32(SCU_TIMER_LOAD), ctr = Xil_In32(SCU_TIMER_COUNTER);
    const bool pending = (Xil_In32(SCU_TIMER_ISR) & 1u) != 0;
    taskEXIT_CRITICAL();
    at = now - (uint64_t)(load - ctr);
    if (pending) {  // the tick interrupt fired inside the critical section: count it
        tick += 1;
        at += (uint64_t)load + 1u;
    }
}

static TaskStats g_stats[3];
static Periodic g_tasks[3] = {
    {"feed", PRIO_FEED, 1, job_feed, &g_stats[0], 0, 0, false, 0},
    {"monitor", PRIO_MON, 10, job_monitor, &g_stats[1], 0, 0, false, 0},
    {"telemetry", PRIO_TEL, 100, job_telemetry, &g_stats[2], 0, 0, false, 0},
};

static unsigned long ul(uint64_t v) { return (unsigned long)v; }

// ------------------------------------------------------------------ live command
static bool parse_live(int argc, char** argv, int first, LiveParams& p) {
    char* e = 0;
    p.speed = 1000; p.budget = p.hw ? 8192u : 128u; p.guard_ms = 0; p.max_s = 0;
    uint32_t* f[4] = {&p.speed, &p.budget, &p.guard_ms, &p.max_s};
    for (int i = first, k = 0; i < argc && k < 4; ++i, ++k) {
        const unsigned long v = strtoul(argv[i], &e, 10);
        if (!*argv[i] || *e) return false;
        *f[k] = (uint32_t)v;
    }
    return p.budget > 0;
}

static void write_live_files(const Dataset& ds, const RunResult& r) {
    g_writer->on_result(ds, r);  // orders_/dbg_/counters_/run_info_<hw|sw>_live_x<speed>
    static char js[4096];
    const int n = g_live->stats_json(js, sizeof(js));
    char path[160];
    snprintf(path, sizeof(path), "%s/rt_%s.json", ds.dir, r.engine);  // rt_sw_live_x1000.json
    if (!g_fs->write(path, js, (uint32_t)n)) uart_printf("[live] could not write %s\r\n", path);
}

class CmdLive : public ICommand {
public:
    const char* name() const { return "live"; }
    const char* help() const { return "hw|sw [speed=1000] [budget] [guard_ms=0] [max_s=0]  real-time replay (q = stop)"; }
    void execute(Cli& cli, int argc, char** argv);
};
void CmdLive::execute(Cli& cli, int argc, char** argv) {
    LiveParams p;
    memset(&p, 0, sizeof(p));
    if (argc < 2 || (strcmp(argv[1], "hw") && strcmp(argv[1], "sw"))) {
        cli.out()("usage: live hw|sw [speed] [budget] [guard_ms] [max_s]   (speed 0 = as fast as possible)\r\n");
        return;
    }
    p.hw = argv[1][0] == 'h';
    if (!parse_live(argc, argv, 2, p)) {
        cli.out()("live: arguments must be non-negative integers, budget > 0\r\n");
        return;
    }
    if (!g_session->dataset().loaded) {
        cli.out()("live: load a dataset first\r\n");
        return;
    }
    // 1) everything slow first (schedule table, IP reset, cache maintenance with IRQs off)
    if (!g_live->prepare(g_session->dataset(), g_session->config(), p, osal::Clock::ticks_per_sec())) {
        cli.out()("live: %s\r\n", g_live->error());
        return;
    }
    // 2) then the common release instant T0, 20 ticks ahead of the current tick
    TickType_t tick;
    uint64_t at;
    tick_epoch(tick, at);
    const uint64_t tick_len = osal::Clock::ticks_per_sec() / configTICK_RATE_HZ;
    const TickType_t t0_tick = tick + 20;
    const uint64_t t0_time = at + 20u * tick_len;
    g_live->arm(t0_time);
    cli.out()("[live] %s x%lu budget %lu guard %lu ms: %lu msgs, release at tick %lu (press q to stop)\r\n",
              p.hw ? "hw" : "sw", (unsigned long)p.speed, (unsigned long)p.budget, (unsigned long)p.guard_ms,
              (unsigned long)g_session->dataset().n_msgs, (unsigned long)t0_tick);
    for (int i = 0; i < 3; ++i) {
        Periodic& t = g_tasks[i];
        t.st->reset(t.name, (uint32_t)t.prio, osal::Clock::ticks_per_sec() * t.period_ms / 1000u);
        t.t0_tick = t0_tick;
        t.t0_time = t0_time;
        t.stop = false;
        if (!t.exited) t.exited = xSemaphoreCreateBinary();
        // feed runs the core (static state) and the others format JSON: 8 KB stacks
        if (xTaskCreate(periodic_task, t.name, 2048, &t, t.prio, 0) != pdPASS) {
            cli.out()("live: could not create task %s\r\n", t.name);
            for (int j = 0; j < i; ++j) g_tasks[j].stop = true;
            for (int j = 0; j < i; ++j) xSemaphoreTake(g_tasks[j].exited, portMAX_DELAY);
            g_live->abort(osal::Clock::now());
            return;
        }
    }
    g_live->set_tasks(g_stats, 3);
    // background: wait for the end, or 'q'
    while (!g_live->finished()) {
        char c;
        if (uart_getc(c) && (c == 'q' || c == 'Q')) {
            g_live->abort(osal::Clock::now());
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    vTaskDelay(pdMS_TO_TICKS(120));  // one more telemetry line with the final state
    for (int i = 0; i < 3; ++i) g_tasks[i].stop = true;
    for (int i = 0; i < 3; ++i) xSemaphoreTake(g_tasks[i].exited, portMAX_DELAY);

    static RunResult r;
    g_live->result(r);
    const LatHist& h = g_live->lateness();
    const uint64_t us = osal::Clock::ticks_per_sec() / 1000000u;
    cli.out()("[live] %s: %s, %lu/%lu msgs, wall %lu ms, lateness p50<=%lu p99<=%lu max %lu us, kills %lu\r\n",
              r.engine, r.ok ? "complete" : r.error, (unsigned long)g_live->released(),
              (unsigned long)g_session->dataset().n_msgs, ul(r.wall_ticks * 1000u / r.ticks_per_sec),
              ul(h.quantile_ub(500) / us), ul(h.quantile_ub(990) / us), ul(h.max / us),
              (unsigned long)g_live->kill_events());
    for (int i = 0; i < 3; ++i) {
        const TaskStats& s = g_stats[i];
        cli.out()("[live] %-9s prio %lu T %4lu ms: %6lu jobs, %lu misses, R min %5lu max %6lu us, C max %6lu us, U %lu.%02lu %%\r\n",
                  s.name, (unsigned long)s.prio, (unsigned long)g_tasks[i].period_ms, (unsigned long)s.jobs,
                  (unsigned long)s.misses, ul(s.rmin / us), ul(s.rmax / us), ul(s.cmax / us),
                  ul(s.jobs ? s.csum * 100u / (s.jobs * s.period) : 0),
                  ul(s.jobs ? (s.csum * 10000u / (s.jobs * s.period)) % 100u : 0));
    }
    write_live_files(g_session->dataset(), r);
    cli.out()("@L done\r\n");
}

// ------------------------------------------------------------------ wcet command
static volatile uint32_t g_probe_runs = 0;
static volatile bool g_probe_stop = false;
static void probe_task(void*) {  // empty periodic job: its cost is the scheduling overhead
    TickType_t last = xTaskGetTickCount();
    while (!g_probe_stop) {
        vTaskDelayUntil(&last, 1);
        ++g_probe_runs;
    }
    vTaskDelete(0);
}

// Longest interruption of a busy loop: tick ISR (+ the probe task, if running).
static void gap_probe(uint32_t ms, uint64_t& gmax, uint64_t& gsum, uint32_t& gn) {
    const uint64_t thr = osal::Clock::ticks_per_sec() / 2000000u;  // 0.5 us
    const uint64_t end = osal::Clock::now() + osal::Clock::ticks_per_sec() * ms / 1000u;
    uint64_t prev = osal::Clock::now();
    gmax = gsum = 0;
    gn = 0;
    for (;;) {
        const uint64_t t = osal::Clock::now();
        const uint64_t g = t - prev;
        prev = t;
        if (g > thr) {
            ++gn;
            gsum += g;
            if (g > gmax) gmax = g;
        }
        if (t > end) break;
    }
}

class CmdWcet : public ICommand {
public:
    const char* name() const { return "wcet"; }
    const char* help() const { return "hw|sw [budget]  execution times of the jobs in isolation (@W line)"; }
    void execute(Cli& cli, int argc, char** argv);
};
void CmdWcet::execute(Cli& cli, int argc, char** argv) {
    LiveParams p;
    memset(&p, 0, sizeof(p));
    if (argc < 2 || (strcmp(argv[1], "hw") && strcmp(argv[1], "sw"))) {
        cli.out()("usage: wcet hw|sw [budget]\r\n");
        return;
    }
    p.hw = argv[1][0] == 'h';
    p.speed = 0;  // everything due: every job releases a full budget (worst case for feed)
    p.budget = p.hw ? 8192u : 128u;
    if (argc > 2) {
        char* e = 0;
        p.budget = (uint32_t)strtoul(argv[2], &e, 10);
        if (*e || !p.budget) { cli.out()("wcet: budget must be a positive integer\r\n"); return; }
    }
    if (!g_session->dataset().loaded) { cli.out()("wcet: load a dataset first\r\n"); return; }
    const uint64_t us = osal::Clock::ticks_per_sec() / 1000000u;
    vTaskPrioritySet(0, PRIO_BENCH);  // above every application task: isolation

    // 1) tick ISR alone, then tick + one empty periodic task (scheduling overhead)
    uint64_t tmax, tsum, pmax, psum;
    uint32_t tn, pn;
    gap_probe(2000, tmax, tsum, tn);
    g_probe_stop = false;
    g_probe_runs = 0;
    xTaskCreate(probe_task, "probe", 512, 0, PRIO_PROBE, 0);
    gap_probe(2000, pmax, psum, pn);
    g_probe_stop = true;
    vTaskDelay(5);

    // 2) feed + monitor jobs back to back over the whole dataset
    TaskStats f, m, t;
    f.reset("feed", PRIO_FEED, 0);
    m.reset("monitor", PRIO_MON, 0);
    t.reset("telemetry", PRIO_TEL, 0);
    const uint64_t t0 = osal::Clock::now();
    if (!g_live->start(g_session->dataset(), g_session->config(), p, t0, osal::Clock::ticks_per_sec())) {
        cli.out()("wcet: %s\r\n", g_live->error());
        vTaskPrioritySet(0, PRIO_CLI);
        return;
    }
    // telemetry is measured with three task entries of maximal width (the line length is what
    // costs time at 115200 baud)
    static TaskStats wide[3];
    for (int i = 0; i < 3; ++i) {
        wide[i].reset(g_tasks[i].name, (uint32_t)g_tasks[i].prio, 0);
        wide[i].jobs = 99999999u; wide[i].misses = 99999u;
        wide[i].rmax = wide[i].cmax = 99999999ull * us; wide[i].csum = 9999999999ull * us;
    }
    g_live->set_tasks(wide, 3);
    uint64_t now = t0;
    for (uint32_t k = 1; !g_live->finished() && k < 50000000u; ++k) {
        uint64_t s = osal::Clock::now();
        g_live->feed_job(s);
        uint64_t e = osal::Clock::now();
        f.job(s, s, e);
        if (k % 10 == 0) {
            s = osal::Clock::now();
            g_live->monitor_job(s);
            e = osal::Clock::now();
            m.job(s, s, e);
        }
        now = e;
    }
    // 3) telemetry job (formats + prints one line while holding the UART mutex)
    for (int k = 0; k < 30; ++k) {
        const uint64_t s = osal::Clock::now();
        job_telemetry(s);
        const uint64_t e = osal::Clock::now();
        t.job(s, s, e);
    }
    vTaskPrioritySet(0, PRIO_CLI);
    static RunResult r;
    g_live->result(r);
    cli.out()("@W {\"mode\":\"%s\",\"budget\":%lu,\"timer_hz\":%lu,\"n_msgs\":%lu,\"complete\":%d,"
              "\"tick_isr\":{\"max_ns\":%lu,\"mean_ns\":%lu,\"n\":%lu},"
              "\"tick_plus_task\":{\"max_ns\":%lu,\"mean_ns\":%lu,\"n\":%lu,\"probe_jobs\":%lu},"
              "\"feed\":{\"jobs\":%lu,\"cmax_ns\":%lu,\"cmean_ns\":%lu},"
              "\"monitor\":{\"jobs\":%lu,\"cmax_ns\":%lu,\"cmean_ns\":%lu},"
              "\"telemetry\":{\"jobs\":%lu,\"cmax_ns\":%lu,\"cmean_ns\":%lu}}\r\n",
              p.hw ? "hw" : "sw", (unsigned long)p.budget, ul(osal::Clock::ticks_per_sec()),
              (unsigned long)g_session->dataset().n_msgs, r.ok ? 1 : 0,
              ul(tmax * 1000u / us), ul(tn ? tsum * 1000u / us / tn : 0), (unsigned long)tn,
              ul(pmax * 1000u / us), ul(pn ? psum * 1000u / us / pn : 0), (unsigned long)pn,
              (unsigned long)g_probe_runs,
              (unsigned long)f.jobs, ul(f.cmax * 1000u / us), ul(f.jobs ? f.csum * 1000u / us / f.jobs : 0),
              (unsigned long)m.jobs, ul(m.cmax * 1000u / us), ul(m.jobs ? m.csum * 1000u / us / m.jobs : 0),
              (unsigned long)t.jobs, ul(t.cmax * 1000u / us), ul(t.jobs ? t.csum * 1000u / us / t.jobs : 0));
    if (!r.ok) cli.out()("wcet: replay did not complete (%s)\r\n", r.error);
    (void)now;
}

// ------------------------------------------------------------------ CLI task
static void cli_task(void*) {
    uses_fpu();
    uart_printf("timer %llu Hz, FreeRTOS tick %lu Hz; type 'help'\r\n",
                (unsigned long long)osal::Clock::ticks_per_sec(), (unsigned long)configTICK_RATE_HZ);
    char line[128];
    for (;;) {
        uart_printf("t2t> ");
        int n = 0;
        for (;;) {
            char c;
            if (!uart_getc(c)) {
                vTaskDelay(1);
                continue;
            }
            if (c == '\r' || c == '\n') break;
            if ((c == 8 || c == 127) && n > 0) {
                --n;
                uart_printf("\b \b");
            } else if (n < (int)sizeof(line) - 1 && c >= 32) {
                line[n++] = c;
                uart_printf("%c", c);
            }
        }
        line[n] = 0;
        uart_printf("\r\n");
        g_cli->execute_line(line);
    }
}

extern "C" void vApplicationStackOverflowHook(TaskHandle_t, char* name) {
    printf("\r\nFATAL: stack overflow in task %s\r\n", name);
    for (;;) {
    }
}
extern "C" void vApplicationMallocFailedHook(void) {
    printf("\r\nFATAL: FreeRTOS heap exhausted\r\n");
    for (;;) {
    }
}

int main() {
    printf("\r\ndk1-t2t: DK1 tick-to-trade engine (ZYBO, PL @ 100 MHz, FreeRTOS)\r\n");
    static osal::Mutex uart_mutex;
    g_uart_mutex = &uart_mutex;

    static XilRegBus bus(XPAR_T2T_CORE_0_S_AXI_CTRL_BASEADDR);
    static T2tProxy proxy(bus);
    static ConfigManager cm(proxy);
    static XilDma dma0, dma1;
    if (!dma0.init(XPAR_AXI_DMA_0_DEVICE_ID) || !dma1.init(XPAR_AXI_DMA_1_DEVICE_ID)) {
        printf("DMA init failed\r\n");
        return 1;
    }
    static XilDmaChannel mm2s(dma0, XAXIDMA_DMA_TO_DEVICE, true);
    static XilDmaChannel s2mm_ord(dma0, XAXIDMA_DEVICE_TO_DMA, false);
    static XilDmaChannel s2mm_dbg(dma1, XAXIDMA_DEVICE_TO_DMA, true);
    static XilCache cache;
    static FatFsFileSystem fs;
    if (!fs.mount()) printf("warning: SD card not mounted\r\n");
    g_fs = &fs;

    static HwEngine hw(cm, mm2s, s2mm_ord, s2mm_dbg, cache);
    static SwEngine sw;
    static DatasetLoader loader(fs);
    static SessionController session(loader);
    static ResultWriter writer(fs, 100000000u, uart_printf);
    static LiveReplay live(&cm, &mm2s, &s2mm_ord, &s2mm_dbg, &cache, clock_now);
    static RunResult last;
    memset(&last, 0, sizeof(last));
    g_session = &session;
    g_writer = &writer;
    g_live = &live;

    CliContext ctx = {&session, &hw, &sw, &cm, &fs, &last};
    static Cli cli(ctx, uart_printf);
    static CmdHelp c_help; static CmdLs c_ls; static CmdLoad c_load; static CmdCfg c_cfg;
    static CmdRun c_run; static CmdStats c_stats; static CmdKill c_kill; static CmdReset c_reset;
    static CmdLive c_live; static CmdWcet c_wcet;
    cli.add(&c_help); cli.add(&c_ls); cli.add(&c_load); cli.add(&c_cfg); cli.add(&c_run);
    cli.add(&c_stats); cli.add(&c_kill); cli.add(&c_reset); cli.add(&c_live); cli.add(&c_wcet);
    session.attach(&cli);
    session.attach(&writer);
    g_cli = &cli;

    // the CLI task loads datasets (FatFs), formats JSON and runs `run`: 32 KB stack
    if (xTaskCreate(cli_task, "cli", 8192, 0, PRIO_CLI, 0) != pdPASS) {
        printf("could not create the CLI task\r\n");
        return 1;
    }
    vTaskStartScheduler();
    printf("scheduler returned\r\n");
    return 1;
}
