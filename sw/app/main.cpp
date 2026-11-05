// main.cpp -- dk1-t2t PS application (Zynq-7000 bare metal, SDK 2017.2).
//
// Two-Part Architecture (SPEC 10.3): this program is Part 2 (soft / non-real-time: dataset
// I/O, configuration, DMA control, logging, CLI, Architecture-A software baseline).  Part 1
// (hard real-time) is the PL fast path t2t_core + timestamp probes + DMA streaming.
#include <stdio.h>
#include <string.h>

#include "board.hpp"
#include "cli.hpp"
#include "engines.hpp"
#include "result_writer.hpp"
#include "session.hpp"
#include "xparameters.h"

#ifndef XPAR_T2T_CORE_0_S_AXI_CTRL_BASEADDR
#error "t2t_core AXI-Lite base address missing from xparameters.h (check the hardware handoff)"
#endif

extern "C" char inbyte(void);

using namespace app;

static char read_line(char* buf, int cap) {
    int n = 0;
    for (;;) {
        char c = inbyte();
        if (c == '\r' || c == '\n') {
            printf("\r\n");
            buf[n] = 0;
            return 1;
        }
        if ((c == 8 || c == 127) && n > 0) {
            --n;
            printf("\b \b");
            continue;
        }
        if (n < cap - 1 && c >= 32) {
            buf[n++] = c;
            putchar(c);
            fflush(stdout);
        }
    }
}

int main() {
    printf("\r\ndk1-t2t: DK1 tick-to-trade engine (ZYBO, PL @ 100 MHz)\r\n");

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

    static HwEngine hw(cm, mm2s, s2mm_ord, s2mm_dbg, cache);
    static SwEngine sw;
    static DatasetLoader loader(fs);
    static SessionController session(loader);
    static ResultWriter writer(fs, 100000000u);  // FCLK_CLK0 = PL clock of t2t_core + probes
    static RunResult last;
    memset(&last, 0, sizeof(last));

    CliContext ctx = {&session, &hw, &sw, &cm, &fs, &last};
    static Cli cli(ctx, printf);
    static CmdHelp c_help; static CmdLs c_ls; static CmdLoad c_load; static CmdCfg c_cfg;
    static CmdRun c_run; static CmdStats c_stats; static CmdKill c_kill; static CmdReset c_reset;
    cli.add(&c_help); cli.add(&c_ls); cli.add(&c_load); cli.add(&c_cfg);
    cli.add(&c_run); cli.add(&c_stats); cli.add(&c_kill); cli.add(&c_reset);
    session.attach(&cli);
    session.attach(&writer);

    printf("timer %llu Hz; type 'help'\r\n", (unsigned long long)osal::Clock::ticks_per_sec());
    char line[128];
    for (;;) {
        printf("t2t> ");
        fflush(stdout);
        read_line(line, sizeof(line));
        cli.execute_line(line);
    }
    return 0;
}
