"""cocotb tests for the RTL timestamp probes (SPEC 9.3, M6): ts_counter, axis_ts_in,
axis_ts_out with random source gaps and random sink backpressure.

Run with pytest (builds with Icarus Verilog through the cocotb runner):
    pytest rtl/tests/test_probes.py
"""
from __future__ import annotations

import os
import random
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RTL = HERE.parent
ROOT = RTL.parent

N_MSGS = int(os.environ.get("DK1_PROBE_MSGS", "1500"))


def beat_data(n: int, k: int) -> int:
    return ((n & 0xFFFFFFFF) << 32) | (0xA5 << 24) | ((k & 0xFF) << 16) | (0x5A00 | ((n ^ k) & 0xFF))


# ============================================================================ cocotb side
try:
    import cocotb
    from cocotb.clock import Clock
    from cocotb.triggers import ReadOnly, RisingEdge
except ImportError:  # pragma: no cover
    cocotb = None


if cocotb is not None:

    async def reset(dut):
        dut.aresetn.value = 0
        for sig in ("in_s_tvalid", "in_m_tready", "out_s_tvalid", "out_m_tready"):
            getattr(dut, sig).value = 0
        dut.in_s_tkeep.value = 0xFF
        dut.out_s_tkeep.value = 0xFF
        dut.in_s_tdata.value = 0
        dut.out_s_tdata.value = 0
        dut.in_s_tlast.value = 0
        dut.out_s_tlast.value = 0
        for _ in range(4):
            await RisingEdge(dut.aclk)
        await ReadOnly()
        assert int(dut.count.value) == 0, "ts_counter must be held in reset"
        await RisingEdge(dut.aclk)
        dut.aresetn.value = 1

    async def source(dut, prefix, n_beats, last_fn, p_valid, rng):
        """AXIS master obeying the protocol: data held stable while valid && !ready."""
        valid, data, last = getattr(dut, f"{prefix}_tvalid"), getattr(dut, f"{prefix}_tdata"), \
            getattr(dut, f"{prefix}_tlast")
        ready = getattr(dut, prefix.replace("_s", "_s") + "_tready")
        sent = 0
        while sent < n_beats:
            if rng.random() < p_valid:
                valid.value = 1
                data.value = beat_data(sent // 4, sent % 4)
                last.value = 1 if last_fn(sent) else 0
                while True:
                    await RisingEdge(dut.aclk)
                    if int(ready.value):
                        break
                sent += 1
            else:
                valid.value = 0
                await RisingEdge(dut.aclk)
        valid.value = 0

    async def backpressure(dut, sig, p_ready, rng, stop):
        while not stop[0]:
            getattr(dut, sig).value = 1 if rng.random() < p_ready else 0
            await RisingEdge(dut.aclk)

    @cocotb.test()
    async def counter_counts(dut):
        """ts_counter: held at 0 in reset, then +1 every cycle."""
        cocotb.start_soon(Clock(dut.aclk, 10, unit="ns").start())
        await reset(dut)
        await RisingEdge(dut.aclk)
        await ReadOnly()
        prev = int(dut.count.value)
        for _ in range(500):
            await RisingEdge(dut.aclk)
            await ReadOnly()
            cur = int(dut.count.value)
            assert cur == prev + 1
            prev = cur

    @cocotb.test()
    async def ts_in_random_backpressure(dut):
        """axis_ts_in: TUSER == counter[31:0] on every beat-0 handshake, 0 otherwise; data,
        TKEEP, TLAST pass through; beat counter realigns after TLAST (chunked DMA)."""
        rng = random.Random(1)
        cocotb.start_soon(Clock(dut.aclk, 10, unit="ns").start())
        await reset(dut)
        stop = [False]
        n = 4 * N_MSGS
        chunk = 7
        cocotb.start_soon(source(dut, "in_s", n, lambda b: b % (4 * chunk) == 4 * chunk - 1 or b == n - 1,
                                 0.75, rng))
        cocotb.start_soon(backpressure(dut, "in_m_tready", 0.7, rng, stop))
        seen = checked = 0
        while seen < n:
            await RisingEdge(dut.aclk)
            await ReadOnly()
            if int(dut.in_m_tvalid.value) and int(dut.in_m_tready.value):
                assert int(dut.in_m_tdata.value) == beat_data(seen // 4, seen % 4), f"data beat {seen}"
                assert int(dut.in_m_tkeep.value) == 0xFF
                ts = int(dut.count.value) & 0xFFFFFFFF
                if seen % 4 == 0:
                    assert int(dut.in_m_tuser.value) == ts, f"TUSER at beat {seen}"
                    checked += 1
                else:
                    assert int(dut.in_m_tuser.value) == 0
                seen += 1
        stop[0] = True
        assert checked == N_MSGS
        dut._log.info(f"axis_ts_in: {seen} beats, {checked} t_in stamps checked")

    @cocotb.test()
    async def ts_out_random_backpressure(dut):
        """axis_ts_out: beat 3 TDATA[63:32] == counter[31:0] at its handshake, TDATA[31:0]
        and beats 0..2 untouched; TLAST / TKEEP preserved."""
        rng = random.Random(2)
        cocotb.start_soon(Clock(dut.aclk, 10, unit="ns").start())
        await reset(dut)
        stop = [False]
        n = 4 * N_MSGS
        cocotb.start_soon(source(dut, "out_s", n, lambda b: b % 20 == 19, 0.66, rng))
        cocotb.start_soon(backpressure(dut, "out_m_tready", 0.6, rng, stop))
        seen = stamps = lasts = 0
        while seen < n:
            await RisingEdge(dut.aclk)
            await ReadOnly()
            if int(dut.out_m_tvalid.value) and int(dut.out_m_tready.value):
                exp = beat_data(seen // 4, seen % 4)
                got = int(dut.out_m_tdata.value)
                ts = int(dut.count.value) & 0xFFFFFFFF
                if seen % 4 == 3:
                    assert got >> 32 == ts and (got & 0xFFFFFFFF) == (exp & 0xFFFFFFFF), f"beat {seen}"
                    stamps += 1
                else:
                    assert got == exp, f"beat {seen} modified"
                assert int(dut.out_m_tlast.value) == (1 if seen % 20 == 19 else 0)
                lasts += int(dut.out_m_tlast.value)
                assert int(dut.out_m_tkeep.value) == 0xFF
                seen += 1
        stop[0] = True
        assert stamps == N_MSGS and lasts == n // 20
        dut._log.info(f"axis_ts_out: {seen} beats, {stamps} t_out stamps, {lasts} TLAST")


# ============================================================================ pytest side
def find_iverilog() -> str | None:
    w = shutil.which("iverilog")
    if w:
        return w
    for c in (Path("C:/iverilog/bin/iverilog.exe"), Path("C:/Program Files/iverilog/bin/iverilog.exe")):
        if c.exists():
            return str(c)
    return None


@pytest.mark.skipif(find_iverilog() is None, reason="Icarus Verilog not installed")
def test_probes_cocotb(tmp_path):
    iv = find_iverilog()
    os.environ["PATH"] = str(Path(iv).parent) + os.pathsep + os.environ["PATH"]
    from cocotb_tools.runner import get_runner

    runner = get_runner("icarus")
    build = ROOT / "build" / "cocotb_probes"
    runner.build(
        sources=[RTL / "ts_counter.v", RTL / "axis_ts_in.v", RTL / "axis_ts_out.v", HERE / "probe_harness.v"],
        hdl_toplevel="probe_harness",
        build_dir=build,
        always=True,
        timescale=("1ns", "1ps"),
    )
    results = runner.test(hdl_toplevel="probe_harness", test_module="test_probes", build_dir=build,
                          test_dir=HERE, results_xml=str(build / "results.xml"))
    import xml.etree.ElementTree as ET

    tree = ET.parse(results if isinstance(results, (str, Path)) else build / "results.xml")
    cases = tree.findall(".//testcase")
    failed = [c.get("name") for c in cases if c.find("failure") is not None or c.find("error") is not None]
    assert len(cases) == 3 and not failed, f"cocotb failures: {failed}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
