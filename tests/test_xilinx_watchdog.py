"""Hang detection for launched Vivado runs (tools/xilinx.py, DECISIONS D-40).

No Xilinx tool is needed: the run directories are faked and the kill command is intercepted.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import xilinx  # noqa: E402

BEGIN = '<?xml version="1.0"?>\n<ProcessHandle Version="1" Minor="0">\n' \
        '    <Process Command="vivado.bat" Owner="x" Host="y" Pid="{pid}">\n    </Process>\n</ProcessHandle>\n'
HEADER = "\n*** Running vivado\n    with args -log a.vds -m64 -mode batch -source a.tcl\n\n"


def make_run(root: Path, name: str, pid: int, age_s: float, log: str = HEADER, marker: str | None = None) -> Path:
    d = root / "vivado" / "p.runs" / name
    d.mkdir(parents=True)
    b = d / ".vivado.begin.rst"
    b.write_text(BEGIN.format(pid=pid))
    (d / "runme.log").write_text(log)
    if marker:
        (d / marker).write_text("")
    t = b.stat().st_mtime - age_s
    os.utime(b, (t, t))
    return d


def test_only_silent_old_unfinished_runs_are_killed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(xilinx.subprocess, "run", lambda cmd, **kw: calls.append([str(c) for c in cmd]))
    old = xilinx.HANG_SECS + 60
    make_run(tmp_path, "hung_synth_1", 1111, old)
    make_run(tmp_path, "starting_synth_1", 2222, 5)                                   # too young
    make_run(tmp_path, "working_synth_1", 3333, old, log=HEADER + "x" * 2000)          # has output
    make_run(tmp_path, "done_synth_1", 4444, old, marker=".vivado.end.rst")
    make_run(tmp_path, "failed_synth_1", 5555, old, marker=".vivado.error.rst")
    (tmp_path / "vivado" / "p.runs" / "queued_synth_1").mkdir()                        # never started

    done = set()
    assert xilinx.kill_hung_runs(tmp_path, done) == ["hung_synth_1"]
    pids = [a for c in calls for a in c if a.isdigit()]
    assert pids and set(pids) == {"1111"}

    # the same process is not killed twice; a relaunch (new pid) of the same run is
    n = len(calls)
    assert xilinx.kill_hung_runs(tmp_path, done) == []
    assert len(calls) == n
    b = tmp_path / "vivado" / "p.runs" / "hung_synth_1" / ".vivado.begin.rst"
    b.write_text(BEGIN.format(pid=6666))
    t = b.stat().st_mtime - old
    os.utime(b, (t, t))
    assert xilinx.kill_hung_runs(tmp_path, done) == ["hung_synth_1"]
    assert "6666" in [a for c in calls[n:] for a in c]


def test_missing_or_unreadable_directories_are_ignored(tmp_path):
    assert xilinx.kill_hung_runs(tmp_path / "nope", set()) == []
    d = tmp_path / "vivado" / "p.runs" / "odd_synth_1"
    d.mkdir(parents=True)
    (d / ".vivado.begin.rst").write_text("no pid here")
    t = (d / ".vivado.begin.rst").stat().st_mtime - xilinx.HANG_SECS - 60
    os.utime(d / ".vivado.begin.rst", (t, t))
    assert xilinx.kill_hung_runs(tmp_path, set()) == []
