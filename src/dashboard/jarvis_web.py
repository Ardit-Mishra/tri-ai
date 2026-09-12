"""Loopback-only read-only web surface for the JARVIS evidence snapshot."""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard import jarvis_terminal
else:
    from . import jarvis_terminal


SnapshotReader = Callable[[], jarvis_terminal.DashboardSnapshot]


class JarvisHTTPServer(ThreadingHTTPServer):
    """Suppress normal Windows disconnects while retaining real server errors."""

    def handle_error(self, request: object, client_address: object) -> None:
        _kind, exc, _traceback = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TRI-AI // JARVIS CORE</title>
  <style>
    :root { color-scheme: dark; --bg:#09090b; --surface:#18181b; --line:#27272a; --muted:#a1a1aa; --text:#fafafa; --emerald:#34d399; --crimson:#fb7185; --amber:#fbbf24; --violet:#a78bfa; }
    * { box-sizing:border-box; }
    body { margin:0; min-width:320px; background:var(--bg); color:var(--text); font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    .shell { width:min(1600px,100%); margin:0 auto; padding:20px; }
    header { border-bottom:1px solid var(--line); display:flex; gap:18px; align-items:center; justify-content:space-between; padding:0 0 18px; }
    .brand { letter-spacing:0; font-size:14px; font-weight:750; white-space:nowrap; }
    .brand span { color:var(--violet); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; font-size:11px; margin-left:8px; }
    .services { display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }
    .service { align-items:center; border:1px solid var(--line); border-radius:6px; color:var(--muted); display:flex; font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; font-size:10px; gap:7px; padding:7px 9px; text-transform:uppercase; }
    .dot { background:var(--crimson); border-radius:50%; height:7px; width:7px; }
    .service.up .dot { animation:pulse 1.8s ease-in-out infinite; background:var(--emerald); box-shadow:0 0 10px rgba(52,211,153,.55); }
    @keyframes pulse { 50% { box-shadow:0 0 16px rgba(52,211,153,.85); opacity:.55; } }
    .metrics { display:grid; gap:1px; grid-template-columns:repeat(4,minmax(0,1fr)); background:var(--line); border:1px solid var(--line); margin:18px 0; }
    .metric { background:var(--surface); min-height:84px; padding:14px; }
    .metric label { color:var(--muted); display:block; font-size:10px; font-weight:650; text-transform:uppercase; }
    .metric strong { display:block; font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; font-size:25px; letter-spacing:0; margin-top:8px; }
    .section-title { color:var(--muted); font-size:10px; font-weight:700; letter-spacing:0; margin:24px 0 9px; text-transform:uppercase; }
    .matrix { display:grid; gap:10px; grid-template-columns:repeat(4,minmax(180px,1fr)); overflow-x:auto; }
    .lane { background:var(--surface); border:1px solid var(--line); min-height:220px; padding:10px; }
    .lane h2 { color:var(--muted); font-size:11px; margin:0 0 10px; text-transform:uppercase; }
    .task { border:1px solid var(--line); border-radius:6px; margin-bottom:8px; min-height:94px; padding:10px; transition:border-color .16s ease, transform .16s ease; }
    .task:hover { border-color:#52525b; transform:translateY(-1px); }
    .task.running { border-color:rgba(52,211,153,.7); box-shadow:0 0 14px rgba(52,211,153,.10); }
    .task-id, .meta, .terminal { font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; }
    .task-id { color:var(--violet); font-size:10px; }
    .task-title { font-size:13px; line-height:1.35; margin:8px 0; overflow-wrap:anywhere; }
    .meta { color:var(--muted); display:flex; font-size:10px; gap:8px; justify-content:space-between; }
    .badge { border:1px solid var(--line); border-radius:4px; font-size:9px; padding:2px 5px; text-transform:uppercase; }
    .badge.passed { color:var(--emerald); } .badge.failed { color:var(--crimson); } .badge.pending { color:var(--amber); }
    .evidence { background:var(--surface); border:1px solid var(--line); min-height:270px; overflow:auto; }
    .terminal { font-size:11px; line-height:1.7; min-width:760px; padding:12px; }
    .event { border-bottom:1px solid var(--line); display:grid; gap:12px; grid-template-columns:145px 145px 120px 100px minmax(120px,1fr); padding:7px 0; }
    .event:first-child { color:var(--muted); font-size:10px; text-transform:uppercase; }
    .pass { color:var(--emerald); } .fail { color:var(--crimson); } .warn { color:var(--amber); }
    .state { color:var(--muted); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; font-size:10px; margin-top:10px; }
    @media (max-width:1000px) { header { align-items:flex-start; flex-direction:column; } .services { justify-content:flex-start; } .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); } .matrix { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width:600px) { .shell { padding:14px; } .matrix { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand">TRI-AI // JARVIS CORE <span>READ ONLY</span></div>
      <div class="services" id="services" aria-label="Daemon service state"></div>
    </header>
    <section class="metrics" aria-label="System metrics">
      <div class="metric"><label>Total tasks</label><strong id="total">-</strong></div>
      <div class="metric"><label>Active runs</label><strong id="active">-</strong></div>
      <div class="metric"><label>Ledger entries</label><strong id="ledger">-</strong></div>
      <div class="metric"><label>Accepted rules</label><strong id="rules">-</strong></div>
    </section>
    <div class="section-title">Task matrix</div>
    <section class="matrix" id="matrix" aria-label="Task matrix"></section>
    <div class="section-title">Evidence terminal</div>
    <section class="evidence"><div class="terminal" id="events"></div></section>
    <div class="state" id="connection">Connecting to local evidence stream...</div>
  </main>
  <script>
    const lanes = [['ready','Ready'],['running','Running'],['done','Done'],['failed','Failed']];
    const byId = id => document.getElementById(id);
    const monoTime = ts => typeof ts === 'number' ? new Date(ts * 1000).toLocaleTimeString() : '-';
    const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); };
    const make = (tag, text, cls) => { const node=document.createElement(tag); node.textContent=text; if(cls) node.className=cls; return node; };
    function render(data) {
      byId('total').textContent=data.metrics.total_tasks;
      byId('active').textContent=data.metrics.active_runs;
      byId('ledger').textContent=data.metrics.ledger_entries;
      byId('rules').textContent=data.metrics.accepted_rules;
      const services=byId('services'); clear(services);
      for (const [name, alive] of Object.entries(data.daemons.processes)) { const item=make('div','',`service ${alive ? 'up' : 'down'}`); item.append(make('i','dot','dot'), make('span',`${name} ${alive ? 'up' : 'down'}`)); services.append(item); }
      const matrix=byId('matrix'); clear(matrix); const buckets=Object.fromEntries(lanes.map(([key])=>[key,[]]));
      for (const task of data.tasks) buckets[buckets[task.status] ? task.status : 'failed'].push(task);
      const last=Object.fromEntries(data.ledger_events.map(event=>[event.task_id,event]));
      for (const [key,label] of lanes) { const lane=make('section','', 'lane'); lane.append(make('h2', `${label} / ${buckets[key].length}`)); for (const task of buckets[key]) { const event=last[task.id]; const card=make('article','',`task ${task.status}`); card.append(make('div',task.id,'task-id'),make('div',task.title,'task-title')); const meta=make('div','', 'meta'); meta.append(make('span',task.run_id === null ? 'run -' : `run ${task.run_id}`)); const badge=make('span',event ? event.outcome : task.status,event && event.outcome === 'passed' ? 'badge passed' : event ? 'badge failed' : 'badge pending'); meta.append(badge); card.append(meta); lane.append(card); } matrix.append(lane); }
      const events=byId('events'); clear(events); const header=make('div','', 'event'); ['Time','Task','Outcome','Verify','Duration'].forEach(label=>header.append(make('span',label))); events.append(header);
      for (const event of data.ledger_events) { const row=make('div','', 'event'); row.append(make('span',monoTime(event.timestamp)),make('span',event.task_id),make('span',event.outcome,event.outcome === 'passed' ? 'pass' : 'fail')); const exit=event.verify_exit === 0 ? '0' : event.verify_exit === null ? '-' : String(event.verify_exit); row.append(make('span',exit,event.verify_exit === 0 ? 'pass' : event.verify_exit === null ? 'warn' : 'fail'),make('span',event.seconds === null ? '-' : `${event.seconds}s`)); events.append(row); }
      if (data.ledger_errors.length) { const issue=make('div',data.ledger_errors.join(' | '),'event warn'); issue.style.gridTemplateColumns='1fr'; events.append(issue); }
      byId('connection').textContent=`Supervisor state: ${data.daemons.status} | snapshot refreshed ${new Date().toLocaleTimeString()}`;
    }
    const stream=new EventSource('/events');
    stream.addEventListener('snapshot', event => render(JSON.parse(event.data)));
    stream.onerror=() => { byId('connection').textContent='Evidence stream reconnecting...'; };
  </script>
</body>
</html>"""


def snapshot_payload(snapshot: jarvis_terminal.DashboardSnapshot) -> dict[str, object]:
    """Serialize only evidence already present in a terminal snapshot."""
    return {
        "metrics": {
            "total_tasks": len(snapshot.tasks),
            "active_runs": sum(task.status == "running" for task in snapshot.tasks),
            "ledger_entries": snapshot.ledger_entry_count,
            "accepted_rules": snapshot.activated_rule_count,
        },
        "tasks": [
            {"id": task.task_id, "title": task.title, "status": task.status, "run_id": task.run_id}
            for task in snapshot.tasks
        ],
        "edges": [
            {"parent_id": edge.parent_id, "child_id": edge.child_id} for edge in snapshot.edges
        ],
        "ledger_events": [
            {
                "timestamp": event.timestamp, "task_id": event.task_id, "outcome": event.outcome,
                "verify_exit": event.verify_exit, "seconds": event.seconds,
            }
            for event in snapshot.ledger_events
        ],
        "ledger_errors": list(snapshot.ledger_errors),
        "daemons": {
            "status": snapshot.daemons.status,
            "processes": dict(snapshot.daemons.processes),
            "diagnostic": snapshot.daemons.diagnostic,
        },
    }


def _handler(snapshot_fn: SnapshotReader, event_interval: float) -> type[BaseHTTPRequestHandler]:
    class JarvisHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _snapshot(self) -> dict[str, object]:
            return snapshot_payload(snapshot_fn())

        def do_GET(self) -> None:
            if self.path == "/":
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/snapshot":
                self._json(self._snapshot())
                return
            if self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        data = json.dumps(self._snapshot(), separators=(",", ":"))
                        self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(event_interval)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            self.send_error(404, "not found")

    return JarvisHandler


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    snapshot_fn: Optional[SnapshotReader] = None,
    event_interval: float = 2.0,
) -> JarvisHTTPServer:
    """Create a loopback-only server with no control-plane endpoints."""
    if host != "127.0.0.1":
        raise ValueError("JARVIS web server must bind loopback 127.0.0.1")
    if event_interval <= 0:
        raise ValueError("event_interval must be positive")
    if snapshot_fn is None:
        runtime = jarvis_terminal.DEFAULT_RUNTIME_ROOT
        snapshot_fn = lambda: jarvis_terminal.read_snapshot(
            board_path=runtime / "board.db",
            ledger_path=runtime / "ledger.jsonl",
            daemon_state_path=runtime / "logs" / "daemons.json",
        )
    return JarvisHTTPServer((host, int(port)), _handler(snapshot_fn, event_interval))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local read-only JARVIS dashboard.")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    try:
        server = create_server(port=args.port)
        print(f"JARVIS dashboard listening on http://127.0.0.1:{server.server_port}")
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"jarvis web stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if "server" in locals():
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
