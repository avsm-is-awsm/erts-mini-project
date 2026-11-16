// t2t_proxy.hpp -- Hardware Proxy for the t2t_core AXI-Lite registers (SPEC 10.2).
// The only class that knows register offsets; everything else talks to it.
#ifndef DK1_T2T_PROXY_HPP
#define DK1_T2T_PROXY_HPP

#include "../../hls/src/core/types.hpp"
#include "platform.hpp"
#include "t2t_regs.h"

namespace app {

struct HwCounters {
    t2t::Counters c;
    int32_t pos[t2t::P_MAX];
    uint32_t err_framing;  // truncated messages dropped by the IP top (D-34)
};

class T2tProxy {
public:
    explicit T2tProxy(IRegBus& bus) : bus_(bus) {}

    void set_ctrl(uint32_t v) { bus_.write32(T2T_REG_CTRL, v); }
    uint32_t ctrl() { return bus_.read32(T2T_REG_CTRL); }
    bool busy() { return (bus_.read32(T2T_REG_STATUS) & T2T_STATUS_BUSY) != 0; }

    void write_config(const t2t::Config& c) {
        bus_.write32(T2T_REG_CLIP_QTY, c.clip_qty);
        bus_.write32(T2T_REG_MAX_ORDER_QTY, c.max_order_qty);
        bus_.write32(T2T_REG_MAX_POS, (uint32_t)c.max_pos);
        bus_.write32(T2T_REG_COLLAR, c.collar);
        bus_.write32(T2T_REG_MIN_GAP_NS, c.min_gap_ns);
        bus_.write32(T2T_REG_MAX_ORDERS, c.max_orders);
        bus_.write32(T2T_REG_LOCKOUT_MS, c.lockout_ms);
        bus_.write32(T2T_REG_CTRL, c.ctrl & ~(uint32_t)t2t::CTRL_SOFT_RESET);
    }

    // Rising edge on CTRL.soft_reset; the IP clears its memories (STATUS.busy) by itself.
    void pulse_soft_reset(uint32_t ctrl) {
        bus_.write32(T2T_REG_CTRL, ctrl & ~(uint32_t)t2t::CTRL_SOFT_RESET);
        bus_.write32(T2T_REG_CTRL, ctrl | t2t::CTRL_SOFT_RESET);
        bus_.write32(T2T_REG_CTRL, ctrl & ~(uint32_t)t2t::CTRL_SOFT_RESET);
    }

    HwCounters counters() {
        HwCounters h;
        t2t::Counters& c = h.c;
        c.msgs_in = bus_.read32(T2T_REG_MSGS_IN);
        c.orders_out = bus_.read32(T2T_REG_ORDERS_OUT);
        c.rejects = bus_.read32(T2T_REG_REJECTS);
        c.err_unknown_type = bus_.read32(T2T_REG_ERR_UNKNOWN_TYPE);
        c.err_bad_product = bus_.read32(T2T_REG_ERR_BAD_PRODUCT);
        c.err_not_found = bus_.read32(T2T_REG_ERR_NOT_FOUND);
        c.err_table_full = bus_.read32(T2T_REG_ERR_TABLE_FULL);
        c.err_dup_id = bus_.read32(T2T_REG_ERR_DUP_ID);
        c.err_oow = bus_.read32(T2T_REG_ERR_OOW);
        c.err_grid = bus_.read32(T2T_REG_ERR_GRID);
        c.err_seq_gap = bus_.read32(T2T_REG_ERR_SEQ_GAP);
        c.err_qty_over = bus_.read32(T2T_REG_ERR_QTY_OVER);
        c.err_anchor = bus_.read32(T2T_REG_ERR_ANCHOR);
        c.err_bad_state = bus_.read32(T2T_REG_ERR_BAD_STATE);
        c.last_seq = bus_.read32(T2T_REG_LAST_SEQ);
        c.first_t_in = bus_.read32(T2T_REG_FIRST_T_IN);
        c.last_t_in = bus_.read32(T2T_REG_LAST_T_IN);
        for (int p = 0; p < t2t::P_MAX; ++p) h.pos[p] = (int32_t)bus_.read32(T2T_REG_POS(p));
        h.err_framing = bus_.read32(T2T_REG_ERR_FRAMING);
        return h;
    }

private:
    IRegBus& bus_;
};

}  // namespace app

#endif
