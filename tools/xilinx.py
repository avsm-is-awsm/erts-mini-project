"""Helpers to run the AMD/Xilinx tools from this repo.

Vivado / Vivado HLS 2017.2 refuse project paths containing spaces, and this repository may
live in such a path.  The drivers therefore stage the needed sources into a space-free
build root (T2T_BUILD_ROOT, default %LOCALAPPDATA%/dk1-t2t-build on Windows, ./build/xil
elsewhere), run the tool there and copy results back into the repo.
"""
from __future__ import annotations

import os
import shutil
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

XILINX_ROOTS = [Path(os.environ.get("XILINX_ROOT", "C:/Xilinx")), Path("/opt/Xilinx"), Path("/tools/Xilinx")]
VERSION = os.environ.get("T2T_XILINX_VERSION", "2017.2")


def build_root() -> Path:
    env = os.environ.get("T2T_BUILD_ROOT")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "C:/dk1-t2t-build"))
        p = base / "dk1-t2t-build" if " " not in str(base) else Path("C:/dk1-t2t-build")
    else:
        p = ROOT / "build" / "xil"
    if " " in str(p.resolve()):
        raise SystemExit(f"build root {p} contains a space; set T2T_BUILD_ROOT to a space-free path")
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_tool(name: str, product: str) -> Path:
    """name: vivado_hls / vivado / xsct; product: Vivado_HLS / Vivado / SDK."""
    exe = name + (".bat" if sys.platform == "win32" else "")
    w = shutil.which(exe) or shutil.which(name)
    if w:
        return Path(w)
    for r in XILINX_ROOTS:
        p = r / product / VERSION / "bin" / exe
        if p.exists():
            return p
    raise SystemExit(f"{name} not found (install AMD {product} {VERSION}, or put it on PATH)")


def stage(rel_paths: list[str], dst_root: Path, patterns=("*",)) -> None:
    """Copy repo-relative files/dirs into dst_root keeping their relative layout."""
    for rel in rel_paths:
        src = ROOT / rel
        dst = dst_root / rel
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__", "*.pyc"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


# Variables a POSIX shell (Git Bash / MSYS) exports that break Vivado 2017.2 on Windows:
# with PWD pointing elsewhere (here: a path with spaces) synth_design fails with
# "couldn't read file .../rtSynthParallelPrep.tcl".
_SHELL_VARS = ("PWD", "OLDPWD", "SHELL", "SHLVL", "MSYSTEM", "TERM", "EXEPATH", "_")


HANG_SECS = float(os.environ.get("T2T_RUN_HANG_SECS", "240"))


def kill_hung_runs(watch: Path, done: set, now: float | None = None) -> list[str]:
    """Kill Vivado sub-runs that started but never produced any log output.

    Vivado 2017.2 on Windows sometimes hangs while a launched run (out-of-context synthesis,
    synth_1, impl_1) is starting: the child process sits at ~10 MB, runme.log holds only the
    two-line launcher header, and wait_on_run blocks forever (DECISIONS D-40).  A run counts as
    hung when its .vivado.begin.rst is older than HANG_SECS, it has no end/error marker and
    runme.log is still only the header.  Killing the process tree makes the run fail, and
    build_bd.tcl relaunches it.  Returns the names of the runs killed.
    """
    now = time.time() if now is None else now
    killed = []
    for begin in Path(watch).glob("**/*.runs/*/.vivado.begin.rst"):
        d = begin.parent
        try:
            if (d / ".vivado.end.rst").exists() or (d / ".vivado.error.rst").exists():
                continue
            if now - begin.stat().st_mtime < HANG_SECS:
                continue
            rl = d / "runme.log"
            if rl.exists() and rl.stat().st_size > 400:
                continue
            m = re.search(r'Pid="(\d+)"', begin.read_text(errors="replace"))
        except OSError:
            continue  # the run directory is being reset
        if not m or (str(d), m.group(1)) in done:
            continue
        done.add((str(d), m.group(1)))
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", m.group(1)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["pkill", "-KILL", "-P", m.group(1)], stdin=subprocess.DEVNULL)
            subprocess.run(["kill", "-KILL", m.group(1)], stdin=subprocess.DEVNULL)
        killed.append(d.name)
    return killed


def run_tool(cmd: list[str], cwd: Path, log: Path, env: dict | None = None,
             watch_runs: Path | None = None) -> int:
    """Run a Xilinx tool; with watch_runs, kill sub-runs under that directory that hang."""
    e = dict(os.environ)
    if sys.platform == "win32":
        for k in _SHELL_VARS:
            e.pop(k, None)
    if sys.platform == "win32":
        e["PWD"] = str(Path(cwd).resolve())  # some Vivado init scripts resolve paths via $PWD
    if env:
        e.update(env)
    if sys.platform == "win32" and str(cmd[0]).lower().endswith(".bat"):
        cmd = ["cmd", "/c", *map(str, cmd)]
    # Standard handles must be valid pipes/devices: Tcl adopts the first file it opens as a
    # standard channel when one is missing ("couldn't read file ...: No error"), Vivado blocks
    # on an inherited console stdin, and its synthesis helper processes fail when stdout is
    # a plain file handle.  So: null stdin, stdout/stderr through a pipe copied to the log.
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e)
        assert p.stdout is not None
        stop = threading.Event()
        lock = threading.Lock()

        def watchdog() -> None:
            done: set = set()
            while not stop.wait(15.0):
                for name in kill_hung_runs(watch_runs, done):
                    msg = f"[watchdog] run {name} hung at start-up (> {HANG_SECS:.0f} s without output): killed"
                    print(msg, flush=True)
                    with lock:
                        f.write("WARNING: " + msg + "\n")
                        f.flush()

        th = None
        if watch_runs is not None:
            th = threading.Thread(target=watchdog, daemon=True)
            th.start()
        for raw in iter(p.stdout.readline, b""):
            with lock:
                f.write(raw.decode("utf-8", errors="replace"))
                f.flush()
        p.wait()
        stop.set()
        if th is not None:
            th.join(timeout=20)
    return p.returncode
