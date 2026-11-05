"""Locate g++ and build the small C++ test binaries used by the pytest suite."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"

_CANDIDATES = [
    r"C:\Program Files\JetBrains\CLion 2026.2.1\bin\mingw\bin",
]


def find_gxx() -> str | None:
    cxx = os.environ.get("CXX")
    if cxx and shutil.which(cxx):
        return shutil.which(cxx)
    g = shutil.which("g++")
    if g:
        return g
    for c in _CANDIDATES:
        p = Path(c) / "g++.exe"
        if p.exists():
            return str(p)
    return None


def env_with_gxx() -> dict:
    env = dict(os.environ)
    g = find_gxx()
    if g:
        env["PATH"] = str(Path(g).parent) + os.pathsep + env.get("PATH", "")
    return env


def exe(name: str) -> Path:
    return BUILD / (name + (".exe" if sys.platform == "win32" else ""))


def build(name: str, sources: list[str], defines: list[str] | None = None,
          std: str = "c++98") -> Path:
    """Compile sources (paths relative to repo root) into build/<name>. Rebuild if stale."""
    g = find_gxx()
    if g is None:
        raise RuntimeError("g++ not found (set CXX or add it to PATH)")
    out = exe(name)
    BUILD.mkdir(exist_ok=True)
    deps = [ROOT / s for s in sources] + list((ROOT / "hls" / "src").rglob("*.hpp")) + \
        list((ROOT / "proto").glob("*.hpp")) + list((ROOT / "hls" / "tb").glob("*.hpp"))
    if out.exists() and all(d.stat().st_mtime < out.stat().st_mtime for d in deps):
        return out
    cmd = [g, f"-std={std}", "-O2", "-Wall", "-Wno-unknown-pragmas", "-Wno-unused-label",
           "-I", str(ROOT / "hls" / "shim"), "-I", str(ROOT / "hls" / "src")]
    cmd += [f"-D{d}" for d in (defines or [])]
    cmd += [str(ROOT / s) for s in sources] + ["-o", str(out)]
    if sys.platform == "win32":
        cmd += ["-static-libgcc", "-static-libstdc++"]
    subprocess.run(cmd, check=True, env=env_with_gxx())
    return out


def run(binary: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True,
                          check=check, env=env_with_gxx())
