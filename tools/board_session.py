"""Drive the ZYBO UART console from the PC and log everything (NEEDS-USER: board attached).

    python tools/board_session.py [--port COM5] [--program] [--log runs/board/uart_<time>.log]
                                  [--timeout 900] -- "load cosim_10k" "cfg set dbg_en 1" "run both"

--program runs `xsct sw/run_board.tcl` first (bitstream + ELF over JTAG), with no serial port
open.  Without --port, the USB serial ports are tried from the highest number down and the one
that answers with a prompt is used.  Each command waits for the next `t2t> `
prompt; the session fails if a prompt does not arrive within --timeout seconds.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]
PROMPT = b"t2t> "
XSCT = Path("C:/Xilinx/SDK/2017.2/bin/xsct.bat")


class Console:
    def __init__(self, port: serial.Serial, log):
        self.port, self.log = port, log
        self.buf = bytearray()
        self.lock = threading.Condition()
        self.alive = True
        self.reader = threading.Thread(target=self._reader, daemon=True)
        self.reader.start()

    def _reader(self) -> None:
        while self.alive:
            try:
                data = self.port.read(4096)
            except serial.SerialException:
                break
            if data:
                text = data.decode("ascii", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                self.log.write(text)
                self.log.flush()
                with self.lock:
                    self.buf += data
                    self.lock.notify_all()

    def wait_for(self, token: bytes, timeout: float) -> bool:
        end = time.time() + timeout
        with self.lock:
            while token not in self.buf:
                left = end - time.time()
                if left <= 0:
                    return False
                self.lock.wait(min(left, 1.0))
            i = self.buf.index(token) + len(token)
            del self.buf[:i]
            return True

    def send(self, line: str) -> None:
        for ch in line:  # the CLI echoes per character; pace it gently
            self.port.write(ch.encode())
            time.sleep(0.003)
        self.port.write(b"\r")

    def close(self) -> None:
        self.alive = False
        self.reader.join(timeout=2)  # let the pending read (0.2 s timeout) finish before closing
        self.port.close()


def candidate_ports() -> list[str]:
    ps = [p.device for p in list_ports.comports()
          if (p.vid == 0x0403) or "USB Serial" in (p.description or "")]
    return sorted(set(ps), key=lambda s: int("".join(c for c in s if c.isdigit()) or 0), reverse=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--program", action="store_true")
    ap.add_argument("--log", type=Path)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("commands", nargs="*")
    a = ap.parse_args(argv)

    log_path = a.log or ROOT / "runs" / "board" / f"uart_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = open(log_path, "a", encoding="utf-8")
    if a.program:
        # JTAG first, with no serial port open: on the ZYBO the FTDI channel A carries JTAG and
        # also appears as a COM port, and holding it open hides the board from hw_server.
        rc = subprocess.run(["cmd", "/c", str(XSCT), str(ROOT / "sw" / "run_board.tcl")],
                            cwd=ROOT, stdin=subprocess.DEVNULL).returncode
        if rc != 0:
            raise SystemExit(f"xsct run_board.tcl failed (exit {rc})")
    ports = [a.port] if a.port else candidate_ports()  # highest number first = UART channel B
    if not ports:
        raise SystemExit("no USB serial port found: is the board on and connected?")
    con = None
    for p in ports:
        try:
            c = Console(serial.Serial(p, 115200, timeout=0.2), log)
        except serial.SerialException as e:
            print(f"[session] cannot open {p}: {e}")
            continue
        c.send("")
        if c.wait_for(PROMPT, 4):
            con = c
            print(f"\n[session] console is {p}; log {log_path}")
            time.sleep(0.5)  # drop any further prompts already queued (banner + poke)
            with c.lock:
                c.buf.clear()
            break
        c.close()
    if con is None:
        raise SystemExit("no t2t> prompt on any port (program the board with --program)")

    ok = True
    for cmd in a.commands:
        print(f"\n[session] >>> {cmd}")
        log.write(f"\n[session {datetime.now():%H:%M:%S}] >>> {cmd}\n")
        con.send(cmd)
        t0 = time.time()
        if not con.wait_for(PROMPT, a.timeout):
            print(f"\n[session] no prompt after {a.timeout:.0f} s: stopping")
            ok = False
            break
        log.write(f"[session] ({time.time() - t0:.1f} s)\n")
    con.close()
    log.close()
    print(f"\n[session] log: {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
