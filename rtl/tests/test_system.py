"""RTL system simulation (cocotb + Icarus): exported HLS RTL of t2t_core between the RTL
timestamp probes, as wired in the Vivado block design.

What it checks (results written to build/rtlsim/<dataset>/):
  * line_rate      -- soft reset over AXI-Lite, config from config.json, the whole feed at
                      AXIS line rate with no backpressure; every ord/dbg record bit-exact vs.
                      golden (timestamps masked); per-order fabric latency t_out - t_in in
                      cycles (NFR-LAT-1/2) and input rate from the 'Z' record (NFR-THR-1).
  * backpressure   -- random input gaps + random output backpressure: still bit-exact, no
                      loss (NFR-THR-2 at RTL level).
  * kill_switch    -- CTRL.kill written over AXI-Lite mid-stream: no 'O' for any message
                      whose first beat arrives >= 10 cycles after the write response (NFR-SAF-1).

    pytest rtl/tests/test_system.py            (DK1_SYS_DS=cosim_10k by default)
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from proto import dk1proto as P  # noqa: E402

DS = os.environ.get("DK1_SYS_DS", "cosim_10k")

REG = dict(ctrl=0x010, clip_qty=0x018, max_order_qty=0x020, max_pos=0x028, collar=0x030,
           min_gap_ns=0x038, max_orders=0x040, lockout_ms=0x048, status=0x050, msgs_in=0x058,
           orders_out=0x060, rejects=0x068)


def load_case(ds_dir: Path):
    feed = P.read_msgs(ds_dir / "feed.bin")
    beats = feed.view(np.uint64).reshape(-1, 4)
    cfg = json.loads((ds_dir / "config.json").read_text())
    return beats, cfg


try:
    import cocotb
    from cocotb.clock import Clock
    from cocotb.triggers import ReadOnly, RisingEdge
except ImportError:  # pragma: no cover
    cocotb = None


if cocotb is not None:

    def u32(x: int) -> int:
        return int(x) & 0xFFFFFFFF

    async def axil_write(dut, addr: int, data: int) -> int:
        """AXI-Lite write; returns counter value of the cycle in which B completes."""
        dut.awaddr.value = addr
        dut.awvalid.value = 1
        dut.wdata.value = u32(data)
        dut.wstrb.value = 0xF
        dut.wvalid.value = 1
        dut.bready.value = 1
        aw = w = False
        while not (aw and w):
            await ReadOnly()
            aw_now = int(dut.awvalid.value) and int(dut.awready.value)
            w_now = int(dut.wvalid.value) and int(dut.wready.value)
            await RisingEdge(dut.aclk)
            if aw_now:
                aw = True
                dut.awvalid.value = 0
            if w_now:
                w = True
                dut.wvalid.value = 0
        while True:
            await ReadOnly()
            done = int(dut.bvalid.value) and int(dut.bready.value)
            cyc = int(dut.count.value)
            await RisingEdge(dut.aclk)
            if done:
                dut.bready.value = 0
                return cyc

    async def axil_read(dut, addr: int) -> int:
        dut.araddr.value = addr
        dut.arvalid.value = 1
        dut.rready.value = 1
        while True:
            await ReadOnly()
            acc = int(dut.arvalid.value) and int(dut.arready.value)
            await RisingEdge(dut.aclk)
            if acc:
                dut.arvalid.value = 0
                break
        while True:
            await ReadOnly()
            ok = int(dut.rvalid.value) and int(dut.rready.value)
            v = int(dut.rdata.value)
            await RisingEdge(dut.aclk)
            if ok:
                dut.rready.value = 0
                return v

    async def bring_up(dut, cfg: dict, ctrl_override: int | None = None):
        cocotb.start_soon(Clock(dut.aclk, 10, unit="ns").start())
        for s in ("s_tvalid", "s_tlast", "o_tready", "d_tready", "awvalid", "wvalid", "bready",
                  "arvalid", "rready"):
            getattr(dut, s).value = 0
        dut.s_tdata.value = 0
        dut.aresetn.value = 0
        for _ in range(8):
            await RisingEdge(dut.aclk)
        dut.aresetn.value = 1
        for _ in range(4):
            await RisingEdge(dut.aclk)
        ctrl = cfg["ctrl"] if ctrl_override is None else ctrl_override
        for k in ("clip_qty", "max_order_qty", "max_pos", "collar", "min_gap_ns", "max_orders", "lockout_ms"):
            await axil_write(dut, REG[k], int(cfg[k]))
        # soft reset: rising edge of CTRL.soft_reset, then wait for STATUS.busy == 0
        t_rst = await axil_write(dut, REG["ctrl"], ctrl | 0x8)
        await axil_write(dut, REG["ctrl"], ctrl)
        trace = []
        while True:
            st = await axil_read(dut, REG["status"])
            trace.append((int(dut.count.value) - t_rst, st))
            if not (st & 1) and len(trace) > 1:
                break
            if len(trace) > 200:
                raise AssertionError(f"soft reset never finished: {trace[:5]}")
            for _ in range(1000):
                await RisingEdge(dut.aclk)
        busy_cycles = trace[-1][0]
        dut._log.info(f"soft reset: STATUS trace (cycles since write, value) first={trace[:3]} "
                      f"last={trace[-2:]} -> busy for < {busy_cycles} cycles")
        bring_up.reset_cycles = busy_cycles

    async def stream(dut, beats: np.ndarray, p_in: float, p_out: float, rng, events=None):
        """Drive the feed and collect both output streams.  events: {beat_index: coroutine fn}"""
        n = beats.size
        flat = beats.reshape(-1)
        idx = 0
        orders, dbg = [], []
        o_cur, d_cur = [], []
        idle = 0
        dut.s_tvalid.value = 0
        dut.o_tready.value = 1
        dut.d_tready.value = 1
        cycles = 0
        while True:
            # drive for this cycle
            if idx < n and rng.random() < p_in:
                dut.s_tvalid.value = 1
                dut.s_tdata.value = int(flat[idx])
                dut.s_tlast.value = 1 if (idx % (4 * 1024) == 4 * 1024 - 1 or idx == n - 1) else 0
            else:
                dut.s_tvalid.value = 0
            dut.o_tready.value = 1 if rng.random() < p_out else 0
            dut.d_tready.value = 1 if rng.random() < p_out else 0
            await ReadOnly()
            if int(dut.s_tvalid.value) and int(dut.s_tready.value):
                if events and idx in events:
                    cocotb.start_soon(events[idx]())
                idx += 1
            if int(dut.o_tvalid.value) and int(dut.o_tready.value):
                o_cur.append(int(dut.o_tdata.value))
                if len(o_cur) == 4:
                    orders.append(o_cur)
                    o_cur = []
            if int(dut.d_tvalid.value) and int(dut.d_tready.value):
                d_cur.append(int(dut.d_tdata.value))
                if len(d_cur) == 4:
                    dbg.append(d_cur)
                    d_cur = []
            await RisingEdge(dut.aclk)
            cycles += 1
            if idx >= n:
                idle += 1
                if idle > 200:
                    break
        dut.s_tvalid.value = 0
        to_bytes = lambda recs: np.array(recs, dtype=np.uint64).view(np.uint8).reshape(-1, 32) \
            if recs else np.zeros((0, 32), np.uint8)
        return to_bytes(orders), to_bytes(dbg), cycles

    def compare(got: np.ndarray, gold: np.ndarray, what: str) -> int:
        g, r = P.golden_mask(got), P.golden_mask(gold)
        if g.shape != r.shape:
            return max(1, abs(len(g) - len(r)))
        return int((g != r).any(axis=1).sum())

    def outdir() -> Path:
        d = ROOT / "build" / "rtlsim" / DS
        d.mkdir(parents=True, exist_ok=True)
        return d

    @cocotb.test()
    async def line_rate(dut):
        ds_dir = ROOT / "data" / "out" / DS
        beats, cfg = load_case(ds_dir)
        await bring_up(dut, cfg)
        orders, dbg, cycles = await stream(dut, beats, 1.0, 1.0, random.Random(0))
        bad_o = compare(orders, P.read_out(ds_dir / "golden_orders.bin"), "orders")
        bad_d = compare(dbg, P.read_out(ds_dir / "golden_bbo.bin"), "bbo")
        od = outdir()
        orders.tofile(od / "rtl_orders.bin")
        dbg.tofile(od / "rtl_bbo.bin")
        recs = P.as_ord(orders)
        sel = np.isin(recs["type"], (P.O_ORDER, P.O_REJECT))
        lat = ((recs["t_out"][sel].astype(np.int64) - recs["t_in"][sel].astype(np.int64)) & 0xFFFFFFFF)
        lat_o = lat[recs["type"][sel] == P.O_ORDER]
        z = P.as_end(orders[orders[:, 0] == P.O_END])
        n_msgs = int(z["msgs_in"][0]) if len(z) else 0
        span = int(z["last_t_in"][0]) - int(z["first_t_in"][0]) if len(z) else 0
        res = dict(dataset=DS, source="RTL simulation (Icarus, exported HLS RTL + probes), no backpressure",
                   n_msgs=n_msgs, n_orders=int(len(lat_o)), n_rejects=int((recs["type"] == P.O_REJECT).sum()),
                   orders_mismatch=bad_o, bbo_mismatch=bad_d,
                   latency_cycles=dict(min=int(lat_o.min()), p50=float(np.percentile(lat_o, 50)),
                                       p99=float(np.percentile(lat_o, 99)), max=int(lat_o.max()))
                   if len(lat_o) else None,
                   latency_all_records=sorted(set(int(x) for x in lat)),
                   input_cycles_first_to_last=span,
                   cycles_per_msg=span / (n_msgs - 1) if n_msgs > 1 else None,
                   sim_cycles=cycles)
        (od / "rtl_line_rate.json").write_text(json.dumps(res, indent=2) + "\n")
        dut._log.info(json.dumps(res))
        assert bad_o == 0 and bad_d == 0, f"mismatch orders={bad_o} bbo={bad_d}"

    @cocotb.test()
    async def backpressure(dut):
        ds_dir = ROOT / "data" / "out" / DS
        beats, cfg = load_case(ds_dir)
        await bring_up(dut, cfg)
        orders, dbg, cycles = await stream(dut, beats, 0.8, 0.6, random.Random(7))
        bad_o = compare(orders, P.read_out(ds_dir / "golden_orders.bin"), "orders")
        bad_d = compare(dbg, P.read_out(ds_dir / "golden_bbo.bin"), "bbo")
        (outdir() / "rtl_backpressure.json").write_text(json.dumps(dict(
            dataset=DS, orders_mismatch=bad_o, bbo_mismatch=bad_d, n_orders=len(orders),
            n_bbo=len(dbg), sim_cycles=cycles), indent=2) + "\n")
        assert bad_o == 0 and bad_d == 0, f"mismatch orders={bad_o} bbo={bad_d}"

    @cocotb.test()
    async def kill_switch(dut):
        ds_dir = ROOT / "data" / "out" / DS
        beats, cfg = load_case(ds_dir)
        await bring_up(dut, cfg)
        gold = P.as_ord(P.read_out(ds_dir / "golden_orders.bin"))
        o_seqs = gold["trig_seq"][gold["type"] == P.O_ORDER]
        # kill right after the message that triggers the middle golden order
        mid_seq = int(o_seqs[len(o_seqs) // 2])
        kill_at_beat = 4 * mid_seq   # first beat of the next message
        state = {}

        async def do_kill():
            await RisingEdge(dut.aclk)  # leave the ReadOnly phase of the stream loop
            state["t_kill"] = await axil_write(dut, REG["ctrl"], int(cfg["ctrl"]) | 0x2)

        orders, _, _ = await stream(dut, beats, 1.0, 1.0, random.Random(3), events={kill_at_beat: do_kill})
        recs = P.as_ord(orders)
        o = recs[recs["type"] == P.O_ORDER]
        t_kill = state["t_kill"]
        late = o[((o["t_in"].astype(np.int64) - t_kill) & 0xFFFFFFFF) < 2**31]
        late = late[((late["t_in"].astype(np.int64) - t_kill) & 0xFFFFFFFF) >= 10]
        before = int((o["trig_seq"] <= mid_seq).sum())
        res = dict(dataset=DS, kill_write_done_cycle=int(t_kill), orders_before_kill=before,
                   orders_total=int(len(o)), orders_with_t_in_ge_kill_plus_10=int(len(late)),
                   golden_orders_total=int(len(o_seqs)))
        (outdir() / "rtl_kill_switch.json").write_text(json.dumps(res, indent=2) + "\n")
        dut._log.info(json.dumps(res))
        assert len(late) == 0 and before > 0


# ================================================================== pytest entry point
def find_iverilog() -> str | None:
    w = shutil.which("iverilog")
    if w:
        return w
    for c in (Path("C:/iverilog/bin/iverilog.exe"), Path("C:/Program Files/iverilog/bin/iverilog.exe")):
        if c.exists():
            return str(c)
    return None


IPV = ROOT / "hls" / "ip" / "hdl" / "verilog"


@pytest.mark.skipif(find_iverilog() is None or not IPV.exists(), reason="needs Icarus and exported HLS IP")
def test_system_rtl():
    iv = find_iverilog()
    os.environ["PATH"] = str(Path(iv).parent) + os.pathsep + os.environ["PATH"]
    ds_dir = ROOT / "data" / "out" / DS
    if not (ds_dir / "golden_orders.bin").exists():
        pytest.skip(f"dataset {DS} not generated")
    from cocotb_tools.runner import get_runner

    build = ROOT / "build" / "cocotb_system"
    build.mkdir(parents=True, exist_ok=True)
    for dat in IPV.glob("*.dat"):
        shutil.copy2(dat, build / dat.name)
    runner = get_runner("icarus")
    runner.build(sources=[ROOT / "rtl" / "ts_counter.v", ROOT / "rtl" / "axis_ts_in.v",
                          ROOT / "rtl" / "axis_ts_out.v", HERE / "system_harness.v",
                          *sorted(IPV.glob("*.v"))],
                 hdl_toplevel="system_harness", build_dir=build, always=True, timescale=("1ns", "1ps"))
    only = os.environ.get("DK1_SYS_TESTS")  # e.g. "line_rate" for long datasets
    runner.test(hdl_toplevel="system_harness", test_module="test_system", build_dir=build,
                test_dir=build, results_xml=str(build / "results.xml"),
                test_filter=only if only else None,
                extra_env={"PYTHONPATH": os.pathsep.join([str(HERE), str(ROOT)]), "DK1_SYS_DS": DS})
    import xml.etree.ElementTree as ET

    cases = ET.parse(build / "results.xml").findall(".//testcase")
    failed = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
    assert len(cases) == (1 if only else 3) and not failed, f"failed: {failed}"
