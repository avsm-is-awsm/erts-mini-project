"""Live dashboard for the real-time replay (FreeRTOS build, docs/09).

    # board: program the FreeRTOS ELF, run commands, stream the '@T' telemetry to a browser
    python tools/live_dashboard.py --program -- "load itch_sample" "live sw 1000" "live hw 1000"

    # no board: replay a recorded UART log at its original pace (x4 faster with --rate 4)
    python tools/live_dashboard.py --replay runs/rt/uart_live.log [--rate 4]

Opens http://localhost:8765 (Server-Sent Events, no external libraries).  Telemetry lines are also
written to runs/rt/telemetry_<time>.jsonl for the report figures.
"""
from __future__ import annotations

import argparse
import json
import sys
import queue
import re
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Hub:
    """Fan-out of console lines to every connected browser (and the JSONL log)."""

    def __init__(self, jsonl: Path | None):
        self.clients: list[queue.Queue] = []
        self.history: list[str] = []
        self.lock = threading.Lock()
        self.jsonl = open(jsonl, "a", encoding="utf-8") if jsonl else None

    def line(self, ln: str) -> None:
        ln = ln.strip("\r")
        if not ln:
            return
        if ln.startswith("@T ") and self.jsonl:
            self.jsonl.write(ln[3:] + "\n")
            self.jsonl.flush()
        with self.lock:
            self.history.append(ln)
            del self.history[:-4000]
            for q in self.clients:
                q.put(ln)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            for ln in self.history:
                q.put(ln)
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


def make_handler(hub: Hub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            if self.path == "/":
                body = PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                q = hub.subscribe()
                try:
                    while True:
                        try:
                            ln = q.get(timeout=15)
                            self.wfile.write(b"data: " + ln.encode("utf-8", "replace") + b"\n\n")
                        except queue.Empty:
                            self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                finally:
                    hub.unsubscribe(q)
                return
            self.send_error(404)

    return H


def replay(hub: Hub, path: Path, rate: float) -> None:
    """Feed a recorded UART log to the hub, pacing '@T' lines by their t_ms field."""
    last_t = None
    for ln in path.read_text(errors="replace").splitlines():
        m = re.match(r"@T (\{.*\})", ln.strip())
        if m:
            try:
                d = json.loads(m.group(1))
                t = d.get("t", d.get("t_ms"))
            except (ValueError, KeyError):
                t = None
            if t is not None and last_t is not None and t > last_t:
                time.sleep(min((t - last_t) / 1000.0 / rate, 2.0))
            last_t = t if t is not None else last_t
        elif "[live]" in ln and "release at tick" in ln:
            last_t = None
        hub.line(ln)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", type=Path, default=None, help="replay a recorded UART log instead of the board")
    ap.add_argument("--rate", type=float, default=1.0, help="replay speed-up")
    ap.add_argument("--port", default=None)
    ap.add_argument("--program", action="store_true")
    ap.add_argument("--elf", type=Path, default=ROOT / "sw" / "bin" / "t2t_rtos.elf")
    ap.add_argument("--http", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("commands", nargs="*")
    a = ap.parse_args(argv)

    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    out = ROOT / "runs" / "rt"
    out.mkdir(parents=True, exist_ok=True)
    hub = Hub(None if a.replay else out / f"telemetry_{stamp}.jsonl")
    # on Windows SO_REUSEADDR lets a second server bind a busy port silently (the old one keeps
    # answering), so refuse a busy port instead
    ThreadingHTTPServer.allow_reuse_address = sys.platform != "win32"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.http), make_handler(hub))
    except OSError as e:
        raise SystemExit(f"port {a.http} is busy (another dashboard running?): {e}")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://localhost:{a.http}/"
    print(f"[dashboard] {url}")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        if a.replay:
            time.sleep(1.0)  # let the browser connect
            replay(hub, a.replay, a.rate)
            print("[dashboard] replay finished; Ctrl+C to quit")
        else:
            from tools.board_session import connect, run_commands  # noqa: E402

            log_path = out / f"uart_{stamp}.log"
            con, log = connect(a.port, a.program, a.elf if a.program else None, log_path, on_line=hub.line)
            ok = run_commands(con, log, a.commands, a.timeout)
            con.close()
            log.close()
            print(f"[dashboard] commands {'done' if ok else 'stopped'}; log {log_path}; Ctrl+C to quit")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    return 0


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live Replay Dashboard</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1d2330;--muted:#667085;--line:#d9dde4;--hw:#2f6db5;--sw:#d9822b;
--ok:#2e8b57;--bad:#c0392b;--grid:#eceff3}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--panel:#1b2029;--ink:#e6e9ef;--muted:#98a2b3;--line:#2c3340;
--hw:#6aa3e8;--sw:#f0a35a;--ok:#4cc38a;--bad:#ef6b5c;--grid:#252b36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,Segoe UI,sans-serif}
header{padding:14px 18px;border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:baseline;flex-wrap:wrap}
h1{font-size:17px;margin:0}.muted{color:var(--muted)}main{padding:14px 18px;display:grid;gap:14px;
grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.card h2{font-size:13px;margin:0 0 8px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;grid-column:1/-1}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.tile .v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}.tile .k{color:var(--muted);font-size:12px}
canvas{width:100%;height:220px;display:block}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:4px 6px;
border-bottom:1px solid var(--line)}th:first-child,td:first-child{text-align:left}
pre{margin:0;max-height:220px;overflow:auto;font:12px/1.35 ui-monospace,Consolas,monospace;white-space:pre-wrap}
.ok{color:var(--ok)}.bad{color:var(--bad)}.wide{grid-column:1/-1}
</style></head><body>
<header><h1>dk1-t2t live replay</h1><span class="muted" id="status">waiting for telemetry…</span></header>
<main>
<div class="tiles">
 <div class="tile"><div class="k">run</div><div class="v" id="t-run">–</div></div>
 <div class="tile"><div class="k">released / total</div><div class="v" id="t-rel">–</div></div>
 <div class="tile"><div class="k">lateness p99 (bucket bound) / max</div><div class="v" id="t-lat">–</div></div>
 <div class="tile"><div class="k">orders out</div><div class="v" id="t-ord">–</div></div>
 <div class="tile"><div class="k">stale-data guard</div><div class="v" id="t-kill">–</div></div>
 <div class="tile"><div class="k">deadline misses</div><div class="v" id="t-miss">–</div></div>
</div>
<div class="card"><h2>Messages: due (market clock) vs released</h2><canvas id="c-prog"></canvas></div>
<div class="card"><h2>Lateness of released data (µs, log)</h2><canvas id="c-lat"></canvas></div>
<div class="card"><h2>Task response time vs deadline</h2><table id="tasks"><thead><tr><th>task</th><th>jobs</th>
<th>R max µs</th><th>deadline µs</th><th>C max µs</th><th>CPU % (avg)</th><th>misses</th></tr></thead><tbody></tbody></table></div>
<div class="card"><h2>Console</h2><pre id="log"></pre></div>
</main>
<script>
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const period={feed:1000,monitor:10000,telemetry:100000};
let run=null;                       // current run: {mode, pts:[...], prev}
function newRun(m){run={mode:m,pts:[],prev:null};}
function fmt(n){return n>=1e6?(n/1e6).toFixed(2)+' M':n>=1e4?(n/1e3).toFixed(1)+' k':String(n)}
function us(n){return n>=1e6?(n/1e6).toFixed(2)+' s':n>=1e3?(n/1e3).toFixed(1)+' ms':n+' µs'}
function draw(id,series,opt){
  const c=document.getElementById(id),r=c.getBoundingClientRect(),dpr=devicePixelRatio||1;
  c.width=r.width*dpr;c.height=r.height*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);
  const W=r.width,H=r.height,L=52,B=22,T=8,R=8;g.clearRect(0,0,W,H);
  const xs=series.flatMap(s=>s.pts.map(p=>p[0])),ys=series.flatMap(s=>s.pts.map(p=>p[1]));
  if(!xs.length)return;const x0=Math.min(...xs),x1=Math.max(...xs)||1;
  const tr=v=>opt.log?Math.log10(Math.max(v,1)):v;let y0=opt.log?0:0,y1=Math.max(...ys.map(tr))||1;
  if(opt.log)y1=Math.ceil(y1);const X=v=>L+(W-L-R)*(v-x0)/Math.max(x1-x0,1e-9),Y=v=>H-B-(H-B-T)*(tr(v)-y0)/(y1-y0||1);
  g.strokeStyle=css('--grid');g.fillStyle=css('--muted');g.font='11px system-ui';g.lineWidth=1;
  const ticks=opt.log?[...Array(y1+1).keys()].map(k=>10**k):[0,.25,.5,.75,1].map(f=>f*Math.max(...ys));
  for(const t of ticks){const y=Y(t);g.beginPath();g.moveTo(L,y);g.lineTo(W-R,y);g.stroke();
    g.fillText(opt.log?us(t):fmt(Math.round(t)),4,y+4);}
  g.fillText(((x1-x0)/1000).toFixed(1)+' s',W-R-40,H-6);
  for(const s of series){g.strokeStyle=s.color;g.lineWidth=s.w||2;g.setLineDash(s.dash||[]);g.beginPath();
    s.pts.forEach((p,i)=>i?g.lineTo(X(p[0]),Y(p[1])):g.moveTo(X(p[0]),Y(p[1])));g.stroke();g.setLineDash([]);
    const last=s.pts[s.pts.length-1];if(last){g.fillStyle=s.color;g.fillText(s.label,Math.min(X(last[0])-60,W-R-90),Y(last[1])-4);}}
}
const NAMES=['feed','monitor','telemetry'];
function norm(c){if(c.t_ms!==undefined)return c;  // long form (older logs)
  return {t_ms:c.t,mode:c.m,speed:c.x,n:c.n,due:c.d,rel:c.r,in:c.i,ord:c.o,stale_us:c.s,lat_p50_us:c.p5,
   lat_p99_us:c.p9,lat_max_us:c.mx,kill:c.k,kills:c.ks,budget_hits:c.bh,dma_busy:c.db,run:c.u,
   tasks:(c.tk||[]).map((v,i)=>({name:NAMES[i]||('task'+i),jobs:v[0],miss:v[1],rmax_us:v[2],cmax_us:v[3],csum_us:v[4]}))};}
function onT(d,redraw){
  if(!redraw){if(!run||run.mode!==d.mode||(run.pts.length&&d.t_ms<run.pts[run.pts.length-1].t_ms))newRun(d.mode);
  run.pts.push(d);}const col=d.mode==='hw'?css('--hw'):css('--sw');
  document.getElementById('status').textContent=`${d.mode.toUpperCase()} engine · speed ×${d.speed||'max'} · t = ${(d.t_ms/1000).toFixed(1)} s · ${d.run?'running':'finished'}`;
  document.getElementById('t-run').textContent=`${d.mode.toUpperCase()} ×${d.speed||'max'}`;
  document.getElementById('t-rel').textContent=`${fmt(d.rel)} / ${fmt(d.n)}`;
  document.getElementById('t-lat').textContent=`≤ ${us(Math.min(d.lat_p99_us,Math.max(d.lat_max_us,1)))} / ${us(d.lat_max_us)}`;
  document.getElementById('t-ord').textContent=fmt(d.ord);
  const k=document.getElementById('t-kill');k.textContent=(d.kill?'KILL ':'ok ')+`(${d.kills})`;k.className='v '+(d.kill?'bad':'ok');
  const miss=d.tasks.reduce((a,t)=>a+t.miss,0),m=document.getElementById('t-miss');m.textContent=miss;m.className='v '+(miss?'bad':'ok');
  const P=run.pts;
  draw('c-prog',[{pts:P.map(p=>[p.t_ms,p.due]),color:css('--muted'),label:'due',dash:[5,4]},
                 {pts:P.map(p=>[p.t_ms,p.rel]),color:col,label:'released'}],{});
  draw('c-lat',[{pts:P.map(p=>[p.t_ms,Math.max(p.lat_max_us,1)]),color:css('--bad'),label:'max',w:1.5},
                {pts:P.map(p=>[p.t_ms,Math.max(p.lat_p99_us,1)]),color:col,label:'p99'},
                {pts:P.map(p=>[p.t_ms,Math.max(p.stale_us,1)]),color:css('--muted'),label:'stale',dash:[3,3],w:1}],{log:true});
  const prev=run.prev,tb=document.querySelector('#tasks tbody');tb.innerHTML='';
  for(const t of d.tasks){const p=prev&&prev.tasks.find(x=>x.name===t.name),dt=prev?d.t_ms-prev.t_ms:0;
    const cpu=d.t_ms>0?(100*t.csum_us/(d.t_ms*1000)):0,dl=period[t.name]||0;  // average share since T0
    tb.insertAdjacentHTML('beforeend',`<tr><td>${t.name}</td><td>${fmt(t.jobs)}</td><td class="${t.rmax_us>dl?'bad':''}">${t.rmax_us}</td>`+
      `<td>${dl}</td><td>${t.cmax_us}</td><td>${cpu.toFixed(1)}</td><td class="${t.miss?'bad':'ok'}">${t.miss}</td></tr>`);}
  run.prev=d;
}
const logEl=document.getElementById('log');
new EventSource('/events').onmessage=e=>{const ln=e.data;
  if(ln.startsWith('@T ')){try{onT(norm(JSON.parse(ln.slice(3))))}catch(x){}return;}
  logEl.textContent+=ln+'\n';if(logEl.textContent.length>20000)logEl.textContent=logEl.textContent.slice(-15000);
  logEl.scrollTop=logEl.scrollHeight;};
addEventListener('resize',()=>{if(run&&run.prev)onT(run.prev,true)});
</script></body></html>
"""

if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
