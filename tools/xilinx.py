"""Helpers to run the AMD/Xilinx tools from this repo.

Vivado / Vivado HLS 2017.2 refuse project paths containing spaces, and this repository may
live in such a path.  The drivers therefore stage the needed sources into a space-free
build root (T2T_BUILD_ROOT, default %LOCALAPPDATA%/dk1-t2t-build on Windows, ./build/xil
elsewhere), run the tool there and copy results back into the repo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
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


def run_tool(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> int:
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
        for raw in iter(p.stdout.readline, b""):
            f.write(raw.decode("utf-8", errors="replace"))
            f.flush()
        p.wait()
    return p.returncode
