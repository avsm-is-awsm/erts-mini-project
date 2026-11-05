// cli.hpp -- UART command line (Command pattern, SPEC 10.2).
//   ls | load <dir> | cfg show | cfg set <k> <v> | run hw|sw|both | stats | kill [on|off] |
//   reset | help
// Each command is an object with name/help/execute; the table is fixed (no allocation).
#ifndef DK1_CLI_HPP
#define DK1_CLI_HPP

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "session.hpp"

namespace app {

typedef int (*PrintFn)(const char* fmt, ...);

class Cli;

class ICommand {
public:
    virtual ~ICommand() {}
    virtual const char* name() const = 0;
    virtual const char* help() const = 0;
    virtual void execute(Cli& cli, int argc, char** argv) = 0;
};

// Everything the commands act on.
struct CliContext {
    SessionController* session;
    IEngine* hw;
    IEngine* sw;
    ConfigManager* cm;   // may be 0 (no hardware)
    IFileSystem* fs;
    RunResult* last;     // last run result (for stats)
};

class Cli : public ISessionObserver {
public:
    enum { MAX_CMDS = 16, MAX_ARGS = 8 };

    Cli(CliContext ctx, PrintFn out) : ctx_(ctx), out_(out), n_(0) {}

    void add(ICommand* c) {
        if (n_ < MAX_CMDS) cmds_[n_++] = c;
    }

    // Tokenize and dispatch one line.  Returns false for an unknown command.
    bool execute_line(char* line) {
        char* argv[MAX_ARGS];
        int argc = 0;
        for (char* t = strtok(line, " \t\r\n"); t && argc < MAX_ARGS; t = strtok(0, " \t\r\n")) argv[argc++] = t;
        if (argc == 0) return true;
        for (int i = 0; i < n_; ++i)
            if (!strcmp(cmds_[i]->name(), argv[0])) {
                cmds_[i]->execute(*this, argc, argv);
                return true;
            }
        out_("unknown command '%s' (try help)\r\n", argv[0]);
        return false;
    }

    void print_help() {
        for (int i = 0; i < n_; ++i) out_("  %-6s %s\r\n", cmds_[i]->name(), cmds_[i]->help());
    }

    CliContext& ctx() { return ctx_; }
    PrintFn out() const { return out_; }

    // Observer: progress messages on the UART
    void on_state(SessionState from, SessionState to, const char* why) {
        out_("[session] %s -> %s (%s)\r\n", state_name(from), state_name(to), why);
    }
    void on_result(const Dataset&, const RunResult& r) {
        const double s = (double)r.wall_ticks / (double)r.ticks_per_sec;
        out_("[%s] %lu msgs, %lu order bytes, %lu dbg bytes, wall %lu us (%lu msg/s)\r\n", r.engine,
             (unsigned long)r.n_msgs, (unsigned long)r.ord_len, (unsigned long)r.dbg_len,
             (unsigned long)(s * 1e6), (unsigned long)(s > 0 ? r.n_msgs / s : 0));
    }

private:
    CliContext ctx_;
    PrintFn out_;
    ICommand* cmds_[MAX_CMDS];
    int n_;
};

// ------------------------------------------------------------------ commands
#define DK1_CMD(cls, nm, hl) \
    class cls : public ICommand { \
    public: \
        const char* name() const { return nm; } \
        const char* help() const { return hl; } \
        void execute(Cli& cli, int argc, char** argv); \
    };

DK1_CMD(CmdHelp, "help", "list commands")
DK1_CMD(CmdLs, "ls", "list a directory on the SD card (default /)")
DK1_CMD(CmdLoad, "load", "<dir>  load <dir>/feed.bin + config.txt into DDR")
DK1_CMD(CmdCfg, "cfg", "show | set <key> <value>   runtime configuration (SPEC 6.7)")
DK1_CMD(CmdRun, "run", "hw | sw | both   replay the loaded dataset")
DK1_CMD(CmdStats, "stats", "counters of the last run (+ live IP counters)")
DK1_CMD(CmdKill, "kill", "[on|off]  CTRL.kill on the IP (default on)")
DK1_CMD(CmdReset, "reset", "session back to Loaded/Idle")
#undef DK1_CMD

inline void CmdHelp::execute(Cli& cli, int, char**) { cli.print_help(); }

inline void ls_cb(const char* name, uint32_t size, void* ctx) {
    ((Cli*)ctx)->out()("  %10lu  %s\r\n", (unsigned long)size, name);
}
inline void CmdLs::execute(Cli& cli, int argc, char** argv) {
    if (!cli.ctx().fs->list(argc > 1 ? argv[1] : "/", ls_cb, &cli)) cli.out()("ls failed\r\n");
}

inline void CmdLoad::execute(Cli& cli, int argc, char** argv) {
    if (argc < 2) { cli.out()("usage: load <dir>\r\n"); return; }
    if (cli.ctx().session->load(argv[1]))
        cli.out()("loaded %lu messages\r\n", (unsigned long)cli.ctx().session->dataset().n_msgs);
}

inline bool cfg_set(t2t::Config& c, const char* k, long long v) {
    if (!strcmp(k, "ctrl")) c.ctrl = (uint32_t)v;
    else if (!strcmp(k, "strat_en")) c.ctrl = v ? (c.ctrl | t2t::CTRL_STRAT_EN) : (c.ctrl & ~(uint32_t)t2t::CTRL_STRAT_EN);
    else if (!strcmp(k, "dbg_en")) c.ctrl = v ? (c.ctrl | t2t::CTRL_DBG_EN) : (c.ctrl & ~(uint32_t)t2t::CTRL_DBG_EN);
    else if (!strcmp(k, "kill")) c.ctrl = v ? (c.ctrl | t2t::CTRL_KILL) : (c.ctrl & ~(uint32_t)t2t::CTRL_KILL);
    else if (!strcmp(k, "clip_qty")) c.clip_qty = (uint32_t)v;
    else if (!strcmp(k, "max_order_qty")) c.max_order_qty = (uint32_t)v;
    else if (!strcmp(k, "max_pos")) c.max_pos = (int32_t)v;
    else if (!strcmp(k, "collar")) c.collar = (uint32_t)v;
    else if (!strcmp(k, "min_gap_ns")) c.min_gap_ns = (uint32_t)v;
    else if (!strcmp(k, "max_orders")) c.max_orders = (uint32_t)v;
    else if (!strcmp(k, "lockout_ms")) c.lockout_ms = (uint32_t)v;
    else return false;
    return true;
}

inline void CmdCfg::execute(Cli& cli, int argc, char** argv) {
    t2t::Config& c = cli.ctx().session->config();
    if (argc >= 4 && !strcmp(argv[1], "set")) {
        if (!cfg_set(c, argv[2], strtoll(argv[3], 0, 0))) { cli.out()("unknown key %s\r\n", argv[2]); return; }
        cli.ctx().session->configure();
        return;
    }
    cli.out()("ctrl=0x%lx (strat_en=%d kill=%d dbg_en=%d) clip_qty=%lu max_order_qty=%lu max_pos=%ld\r\n"
              "collar=%lu min_gap_ns=%lu max_orders=%lu lockout_ms=%lu\r\n",
              (unsigned long)c.ctrl, (int)(c.ctrl & 1), (int)((c.ctrl >> 1) & 1), (int)((c.ctrl >> 2) & 1),
              (unsigned long)c.clip_qty, (unsigned long)c.max_order_qty, (long)c.max_pos,
              (unsigned long)c.collar, (unsigned long)c.min_gap_ns, (unsigned long)c.max_orders,
              (unsigned long)c.lockout_ms);
}

inline void CmdRun::execute(Cli& cli, int argc, char** argv) {
    const char* which = argc > 1 ? argv[1] : "both";
    CliContext& x = cli.ctx();
    if (!x.session->dataset().loaded) { cli.out()("load a dataset first\r\n"); return; }
    if (!strcmp(which, "hw") || !strcmp(which, "both")) {
        if (!x.hw) cli.out()("no hardware engine\r\n");
        else x.session->run(*x.hw, *x.last);
    }
    if (!strcmp(which, "sw") || !strcmp(which, "both")) x.session->run(*x.sw, *x.last);
}

inline void CmdStats::execute(Cli& cli, int, char**) {
    const t2t::Counters& c = cli.ctx().last->counters;
    cli.out()("last run (%s): msgs_in=%lu orders_out=%lu rejects=%lu seq_gap=%lu not_found=%lu "
              "table_full=%lu oow=%lu\r\n",
              cli.ctx().last->engine ? cli.ctx().last->engine : "-", (unsigned long)c.msgs_in,
              (unsigned long)c.orders_out, (unsigned long)c.rejects, (unsigned long)c.err_seq_gap,
              (unsigned long)c.err_not_found, (unsigned long)c.err_table_full, (unsigned long)c.err_oow);
    if (cli.ctx().cm) {
        HwCounters h = cli.ctx().cm->read();
        cli.out()("IP now: msgs_in=%lu orders_out=%lu last_seq=%lu first_t_in=%lu last_t_in=%lu\r\n",
                  (unsigned long)h.c.msgs_in, (unsigned long)h.c.orders_out, (unsigned long)h.c.last_seq,
                  (unsigned long)h.c.first_t_in, (unsigned long)h.c.last_t_in);
    }
}

inline void CmdKill::execute(Cli& cli, int argc, char** argv) {
    const bool on = !(argc > 1 && !strcmp(argv[1], "off"));
    cfg_set(cli.ctx().session->config(), "kill", on ? 1 : 0);
    if (cli.ctx().cm) cli.ctx().cm->kill(on);
    cli.out()("kill %s\r\n", on ? "ON" : "off");
}

inline void CmdReset::execute(Cli& cli, int, char**) { cli.ctx().session->reset(); }

}  // namespace app

#endif
