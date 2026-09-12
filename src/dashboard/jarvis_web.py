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


def _workspace_key(path: Optional[str]) -> Optional[str]:
    """Compare displayed workspace scopes without opening or modifying them."""
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return None


def _rule_task_links(snapshot: jarvis_terminal.DashboardSnapshot) -> list[dict[str, str]]:
    """Derive the read-only graph links for rules scoped to task workspaces."""
    tasks_by_workspace: dict[str, list[str]] = {}
    for task in snapshot.tasks:
        key = _workspace_key(task.workspace_path)
        if key is not None:
            tasks_by_workspace.setdefault(key, []).append(task.task_id)
    links: list[dict[str, str]] = []
    for rule in snapshot.rules:
        for task_id in tasks_by_workspace.get(_workspace_key(rule.workspace_path) or "", []):
            links.append({"proposal_id": rule.proposal_id, "task_id": task_id})
    return links


def _telemetry_payload(task: jarvis_terminal.TaskView) -> dict[str, object]:
    """Project one task's run telemetry exactly as the reader recorded it."""
    telemetry = task.telemetry
    if telemetry is None:
        return {"phases": [], "logs": []}
    return {
        "run_id": telemetry.run_id,
        "run_status": telemetry.run_status,
        "run_outcome": telemetry.run_outcome,
        "started_at": telemetry.started_at,
        "ended_at": telemetry.ended_at,
        "heartbeat_at": telemetry.heartbeat_at,
        "worker_pid": telemetry.worker_pid,
        "claim_lock": telemetry.claim_lock,
        "claim_expires": telemetry.claim_expires,
        "step_key": telemetry.step_key,
        "branch_name": telemetry.branch_name,
        "worktree_path": telemetry.worktree_path,
        "workspace_kind": telemetry.workspace_kind,
        "model": telemetry.model,
        "provider": telemetry.provider,
        "summary": telemetry.summary,
        "error": telemetry.error,
        "phases": [
            {"key": phase.key, "state": phase.state, "evidence": phase.evidence}
            for phase in telemetry.phases
        ],
        "logs": [
            {
                "name": log.name,
                "path": log.path,
                "lines": list(log.lines),
                "truncated": log.truncated,
                "error": log.error,
            }
            for log in telemetry.logs
        ],
    }


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
    :root { --bg:#05070a; --surface:rgba(9,14,20,.80); --line:rgba(0,240,255,.20); --muted:#94a3b8; --text:#e6f7ff; --cyan:#00f0ff; --amber:#ffb703; --emerald:#00ff9d; --crimson:#ff4d6d; }
    body { background-color:var(--bg); background-image:repeating-linear-gradient(0deg,transparent 0,transparent 31px,rgba(0,240,255,.045) 32px),repeating-linear-gradient(90deg,transparent 0,transparent 31px,rgba(0,240,255,.045) 32px); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; }
    .shell { max-width:1540px; } header { border-color:var(--line); } .brand { color:var(--cyan); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; } .brand span { color:var(--amber); } .service { background:rgba(9,14,20,.62); border-color:var(--line); } .service.up .dot { background:var(--emerald); box-shadow:0 0 12px rgba(0,255,157,.7); } .dot { background:var(--crimson); }
    .metrics { background:var(--line); border-color:var(--line); } .metric { background:var(--surface); min-height:74px; } .metric strong { color:var(--cyan); }
    .hud-grid { display:grid; gap:14px; grid-template-columns:minmax(0,1fr) minmax(300px,350px); margin-top:14px; } .hud-aside { display:grid; gap:14px; align-content:start; }
    .hud-panel { background:var(--surface); border:1px solid var(--line); box-shadow:0 12px 34px rgba(0,0,0,.22),inset 0 0 36px rgba(0,240,255,.025); min-width:0; padding:14px; position:relative; } .hud-panel::before,.hud-panel::after { content:""; height:15px; position:absolute; width:15px; } .hud-panel::before { border-left:2px solid var(--cyan); border-top:2px solid var(--cyan); left:-1px; top:-1px; } .hud-panel::after { border-bottom:2px solid var(--cyan); border-right:2px solid var(--cyan); bottom:-1px; right:-1px; }
    .panel-head { align-items:center; color:var(--muted); display:flex; font-size:10px; justify-content:space-between; margin-bottom:10px; text-transform:uppercase; } .panel-head strong { color:var(--cyan); font-weight:700; } .graph-panel { min-height:480px; } #neuralGraph { cursor:crosshair; display:block; height:420px; touch-action:none; width:100%; } .graph-legend { color:var(--muted); display:flex; flex-wrap:wrap; font-size:9px; gap:12px; margin-top:8px; text-transform:uppercase; } .legend-dot { border-radius:50%; display:inline-block; height:7px; margin-right:4px; width:7px; }
    .inspector-empty,.memory-empty { color:var(--muted); font-size:11px; line-height:1.6; } .inspect-title { color:var(--cyan); font-size:14px; margin:0 0 10px; overflow-wrap:anywhere; } .inspect-grid { display:grid; gap:8px; } .inspect-row { border-top:1px solid rgba(0,240,255,.12); padding-top:8px; } .inspect-row label { color:var(--muted); display:block; font-size:9px; margin-bottom:4px; text-transform:uppercase; } .inspect-row div { color:var(--text); font-size:11px; overflow-wrap:anywhere; } .chip { border:1px solid rgba(255,183,3,.42); color:var(--amber); display:inline-block; font-size:9px; margin:0 4px 4px 0; padding:3px 5px; }
    .memory-list { display:grid; gap:9px; max-height:318px; overflow:auto; } .memory-rule { border-left:2px solid var(--amber); padding:8px 0 8px 9px; } .memory-rule h3 { color:var(--amber); font-size:11px; margin:0 0 5px; overflow-wrap:anywhere; } .memory-rule p { color:var(--muted); font-size:9px; line-height:1.5; margin:0; overflow-wrap:anywhere; }
    #matrix { display:none; } .section-title { color:var(--cyan); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; } .evidence { background:var(--surface); border-color:var(--line); } .event { border-color:rgba(0,240,255,.12); } .pass { color:var(--emerald); } .fail { color:var(--crimson); } .warn { color:var(--amber); } .state { color:var(--muted); }
    @media (max-width:1000px) { .hud-grid { grid-template-columns:1fr; } .hud-aside { grid-template-columns:repeat(2,minmax(0,1fr)); } } @media (max-width:650px) { .hud-aside { grid-template-columns:1fr; } .graph-panel { min-height:390px; } #neuralGraph { height:330px; } }
    .service { align-items:center; display:inline-flex; gap:6px; }
    .dot { border-radius:9999px; flex-shrink:0; height:8px; width:8px; }
    .outcome-badge { border-radius:9999px; display:inline-block; font-size:9px; justify-self:start; letter-spacing:.05em; padding:2px 8px; text-transform:uppercase; }
    .outcome-badge.pass { background:rgba(0,255,157,.1); border:1px solid #00ff9d40; color:#00ff9d; }
    .outcome-badge.fail { background:rgba(255,0,85,.1); border:1px solid #ff005540; color:#ff0055; }
    .outcome-badge.warn { background:rgba(255,183,3,.1); border:1px solid #ffb70340; color:#ffb703; }
    .graph-hint { color:var(--muted); font-size:9px; margin-top:6px; text-transform:uppercase; }
    .phase-rail { display:grid; gap:4px; margin:2px 0 4px; }
    .phase-step { align-items:center; display:grid; gap:8px; grid-template-columns:9px minmax(0,1fr); }
    .phase-key { color:var(--text); font-size:10px; letter-spacing:.04em; overflow-wrap:anywhere; }
    .phase-note { color:var(--muted); font-size:9px; line-height:1.45; }
    .phase-pip { border-radius:9999px; height:9px; width:9px; }
    .phase-step.done .phase-pip { background:var(--emerald); box-shadow:0 0 8px rgba(0,255,157,.55); }
    .phase-step.active .phase-pip { background:var(--cyan); box-shadow:0 0 10px rgba(0,240,255,.8); animation:pulse 1.6s ease-in-out infinite; }
    .phase-step.pending .phase-pip { border:1px solid rgba(148,163,184,.5); }
    .phase-step.skipped .phase-pip { background:rgba(148,163,184,.28); }
    .phase-step.skipped .phase-key, .phase-step.pending .phase-key { color:var(--muted); }
    .room-window { border:1px solid rgba(0,240,255,.18); margin-top:10px; }
    .room-window > summary { color:var(--cyan); cursor:pointer; font-size:10px; list-style:none; padding:7px 9px; text-transform:uppercase; }
    .room-window > summary::-webkit-details-marker { display:none; }
    .room-window[open] > summary { border-bottom:1px solid rgba(0,240,255,.18); }
    .room-log { background:rgba(2,5,8,.72); max-height:190px; overflow:auto; padding:8px 9px; }
    .room-log h4 { color:var(--muted); font-size:9px; margin:0 0 4px; text-transform:uppercase; }
    .room-log pre { color:#bfe9ff; font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; font-size:10px; line-height:1.5; margin:0 0 8px; white-space:pre-wrap; word-break:break-word; }
    .room-note { color:var(--amber); font-size:9px; }
    .sheet-handle { display:none; }
    @media (max-width:767px) {
      .hud-grid { grid-template-columns:1fr; }
      .hud-aside { background:rgba(4,7,11,.97); border-top:1px solid var(--cyan); bottom:0; box-shadow:0 -14px 34px rgba(0,0,0,.55); gap:10px; grid-template-columns:1fr; left:0; max-height:78vh; overflow-y:auto; padding:0 12px 16px; position:fixed; right:0; transform:translateY(calc(100% - 44px)); transition:transform .26s ease; z-index:40; }
      .hud-aside.open { transform:translateY(0); }
      .sheet-handle { background:rgba(4,7,11,.97); cursor:pointer; display:block; padding:10px 0 8px; position:sticky; text-align:center; top:0; }
      .sheet-handle span { background:rgba(0,240,255,.45); border-radius:9999px; display:inline-block; height:4px; width:46px; }
      .sheet-handle em { color:var(--muted); display:block; font-size:9px; font-style:normal; margin-top:5px; text-transform:uppercase; }
      .shell { padding-bottom:64px; }
    }
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
    <section class="hud-grid" aria-label="Neural task and memory map">
      <section class="hud-panel graph-panel">
        <div class="panel-head"><strong>Neural Task Topology</strong><span id="graphSummary">Awaiting evidence</span></div>
        <canvas id="neuralGraph" role="img" aria-label="Interactive task dependency and accepted-rule graph"></canvas>
        <div class="graph-hint">Drag canvas to pan // double-tap or wheel to zoom // tap a node to inspect</div>
        <div class="graph-legend"><span><i class="legend-dot" style="background:#00f0ff"></i>ready</span><span><i class="legend-dot" style="background:#00ff9d"></i>done</span><span><i class="legend-dot" style="background:#ff4d6d"></i>failed/cancelled</span><span><i class="legend-dot" style="background:#ffb703"></i>accepted rule</span></div>
      </section>
      <aside class="hud-aside" id="hudAside">
        <div class="sheet-handle" id="sheetHandle" role="button" tabindex="0" aria-label="Toggle inspector sheet"><span></span><em id="sheetLabel">Inspector</em></div>
        <section class="hud-panel"><div class="panel-head"><strong>Node Inspector</strong><span>read only</span></div><div id="inspector" class="inspector-empty">Select a task or accepted rule.</div></section>
        <section class="hud-panel"><div class="panel-head"><strong>Memory Bank</strong><span id="memoryCount">0 accepted</span></div><div id="memoryBank" class="memory-empty">No accepted procedural rules.</div></section>
      </aside>
    </section>
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
      for (const [name, alive] of Object.entries(data.daemons.processes)) { const item=make('div','',`service ${alive ? 'up' : 'down'}`); item.append(make('i','','dot'), make('span',`${name} ${alive ? 'up' : 'down'}`)); services.append(item); }
      const matrix=byId('matrix'); clear(matrix); const buckets=Object.fromEntries(lanes.map(([key])=>[key,[]]));
      for (const task of data.tasks) buckets[buckets[task.status] ? task.status : 'failed'].push(task);
      const last=Object.fromEntries(data.ledger_events.map(event=>[event.task_id,event]));
      for (const [key,label] of lanes) { const lane=make('section','', 'lane'); lane.append(make('h2', `${label} / ${buckets[key].length}`)); for (const task of buckets[key]) { const event=last[task.id]; const card=make('article','',`task ${task.status}`); card.append(make('div',task.id,'task-id'),make('div',task.title,'task-title')); const meta=make('div','', 'meta'); meta.append(make('span',task.run_id === null ? 'run -' : `run ${task.run_id}`)); const badge=make('span',event ? event.outcome : task.status,event && event.outcome === 'passed' ? 'badge passed' : event ? 'badge failed' : 'badge pending'); meta.append(badge); card.append(meta); lane.append(card); } matrix.append(lane); }
      const events=byId('events'); clear(events); const header=make('div','', 'event'); ['Time','Task','Outcome','Verify','Duration'].forEach(label=>header.append(make('span',label))); events.append(header);
      for (const event of data.ledger_events) { const row=make('div','', 'event'); row.append(make('span',monoTime(event.timestamp)),make('span',event.task_id),make('span',event.outcome,event.outcome === 'passed' ? 'pass' : 'fail')); const exit=event.verify_exit === 0 ? '0' : event.verify_exit === null ? '-' : String(event.verify_exit); row.append(make('span',exit,event.verify_exit === 0 ? 'pass' : event.verify_exit === null ? 'warn' : 'fail'),make('span',event.seconds === null ? '-' : `${event.seconds}s`)); events.append(row); }
      if (data.ledger_errors.length) { const issue=make('div',data.ledger_errors.join(' | '),'event warn'); issue.style.gridTemplateColumns='1fr'; events.append(issue); }
      byId('connection').textContent=`Supervisor state: ${data.daemons.status} | snapshot refreshed ${new Date().toLocaleTimeString()}`;
    }
    const hud={data:null,nodes:[],nodeById:new Map(),edges:[],groups:[],selected:null,dragging:null,view:{x:0,y:0,k:1},panning:null,lastTapAt:0,moved:false};
    const canvas=byId('neuralGraph'); const ctx=canvas.getContext('2d');
    const PHASE_LABEL={claimed:'CLAIMED',worktree_prep:'WORKTREE_PREP',agent_active:'AGENT_ACTIVE',verify_gate:'VERIFY_GATE'};
    const PHASE_COLOR={done:'#00ff9d',active:'#00f0ff',pending:'rgba(148,163,184,.40)',skipped:'rgba(148,163,184,.18)'};
    const statusColor=status=>status==='done'?'#00ff9d':status==='running'?'#00f0ff':(status==='failed'||status==='cancelled')?'#ff4d6d':'#00f0ff';
    const shortPath=value=>String(value).replace(/\\/g,'/').split('/').slice(-2).join('/');
    const setText=(node,value)=>{ node.textContent=value; return node; };
    const isPhone=()=>window.matchMedia('(max-width:767px)').matches;
    const telemetryOf=node=>(node&&node.kind==='task'&&node.detail.telemetry)||null;
    function elapsed(from,to) {
      if(typeof from!=='number')return null;
      const end=typeof to==='number'?to:Math.floor(Date.now()/1000),span=Math.max(0,end-from);
      if(span<60)return `${span}s`;
      if(span<3600)return `${Math.floor(span/60)}m ${span%60}s`;
      return `${Math.floor(span/3600)}h ${Math.floor((span%3600)/60)}m`;
    }
    function graphModel(data) {
      const nodes=[...data.tasks.map(task=>({id:`task:${task.id}`,kind:'task',label:task.id,detail:task})),...data.rules.map(rule=>({id:`rule:${rule.proposal_id}`,kind:'rule',label:rule.rule_id,detail:rule}))];
      const edges=[];
      data.edges.forEach(edge=>edges.push({source:`task:${edge.parent_id}`,target:`task:${edge.child_id}`,kind:'dependency'}));
      data.rule_task_links.forEach(link=>edges.push({source:`rule:${link.proposal_id}`,target:`task:${link.task_id}`,kind:'governs'}));
      const childIds=new Set(data.edges.map(edge=>`task:${edge.child_id}`));
      nodes.forEach(node=>{ node.tier=node.kind!=='task'?'rule':childIds.has(node.id)?'stage':'room'; });
      return {nodes,edges};
    }
    function workspaceGroups() {
      const buckets=new Map();
      hud.nodes.forEach(node=>{
        if(node.kind!=='task')return;
        const key=node.detail.workspace_path; if(!key)return;
        if(!buckets.has(key))buckets.set(key,[]);
        buckets.get(key).push(node);
      });
      return [...buckets.entries()].map(([path,members])=>({path,members}));
    }
    function syncGraph(data) {
      const model=graphModel(data); const old=hud.nodeById; const rect=canvas.getBoundingClientRect();
      hud.nodeById=new Map(); hud.nodes=model.nodes.map((node,index)=>{
        const prior=old.get(node.id); const angle=(index/Math.max(model.nodes.length,1))*Math.PI*2;
        const item={...node,x:prior?prior.x:rect.width*.5+Math.cos(angle)*Math.min(rect.width*.28,190),y:prior?prior.y:rect.height*.5+Math.sin(angle)*Math.min(rect.height*.26,150),vx:prior?prior.vx:0,vy:prior?prior.vy:0};
        hud.nodeById.set(item.id,item); return item;
      }); hud.edges=model.edges.filter(edge=>hud.nodeById.has(edge.source)&&hud.nodeById.has(edge.target));
      hud.groups=workspaceGroups();
      if (!hud.selected || !hud.nodeById.has(hud.selected)) hud.selected=hud.nodes[0]?.id||null;
      const rooms=hud.nodes.filter(node=>node.tier==='room').length,stages=hud.nodes.filter(node=>node.tier==='stage').length;
      setText(byId('graphSummary'),`${rooms} rooms // ${stages} stages // ${data.rules.length} rules // ${hud.edges.length} links`);
      renderInspector(); renderMemory();
    }
    function resizeCanvas() { const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1; const width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio)); if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;} ctx.setTransform(ratio,0,0,ratio,0,0); return rect; }
    function screenPoint(event) { const rect=canvas.getBoundingClientRect(); return {x:event.clientX-rect.left,y:event.clientY-rect.top}; }
    function graphPoint(event) { const point=screenPoint(event); return {x:(point.x-hud.view.x)/hud.view.k,y:(point.y-hud.view.y)/hud.view.k}; }
    function visibleWorld(rect) { const k=hud.view.k; return {x0:(0-hud.view.x)/k,y0:(0-hud.view.y)/k,x1:(rect.width-hud.view.x)/k,y1:(rect.height-hud.view.y)/k}; }
    function nodeRadius(node) { return node.kind==='rule'?11:node.tier==='room'?15:10; }
    function hitNode(point) { return hud.nodes.slice().reverse().find(node=>Math.hypot(node.x-point.x,node.y-point.y)<nodeRadius(node)+6); }
    function zoomAt(px,py,next) {
      const k=Math.max(.5,Math.min(3,next)),wx=(px-hud.view.x)/hud.view.k,wy=(py-hud.view.y)/hud.view.k;
      hud.view.k=k; hud.view.x=px-wx*k; hud.view.y=py-wy*k;
    }
    function openSheet(open) {
      const aside=byId('hudAside'); if(!aside)return;
      aside.classList.toggle('open',open);
      const label=byId('sheetLabel'); if(label)setText(label,open?'Close inspector':'Inspector');
    }
    function renderPhases(telemetry) {
      const rail=make('div','','phase-rail');
      (telemetry.phases||[]).forEach(phase=>{
        const step=make('div','',`phase-step ${phase.state}`);
        step.append(make('i','','phase-pip'));
        const body=make('div','');
        body.append(make('div',`${PHASE_LABEL[phase.key]||phase.key} // ${phase.state}`,'phase-key'),make('div',phase.evidence,'phase-note'));
        step.append(body); rail.append(step);
      });
      return rail;
    }
    function renderRoomWindow(telemetry) {
      const box=document.createElement('details'); box.className='room-window';
      const logs=telemetry.logs||[];
      const summary=document.createElement('summary');
      setText(summary,logs.length?`Room window // ${logs.map(log=>log.name).join(' + ')} log`:'Room window // no active run output');
      box.append(summary);
      const body=make('div','','room-log');
      if(!logs.length)body.append(make('p','This room has no retained output for an active run.','room-note'));
      logs.forEach(log=>{
        body.append(make('h4',`${log.name}.log // ${shortPath(log.path)}`));
        if(log.error){body.append(make('p',log.error,'room-note'));return;}
        const pre=document.createElement('pre'); setText(pre,log.lines.join('\n')||'(empty)'); body.append(pre);
        if(log.truncated)body.append(make('p','tail bounded — older output not shown','room-note'));
      });
      box.append(body); return box;
    }
    function renderInspector() {
      const root=byId('inspector'); clear(root); const node=hud.nodeById.get(hud.selected);
      if(!node){root.className='inspector-empty'; root.textContent='Select a task or accepted rule.'; return;} root.className='';
      root.append(make('h2',node.kind==='task'?node.detail.title:node.detail.rule_id,'inspect-title'));
      const grid=make('div','','inspect-grid'); const row=(label,value)=>{const item=make('div','','inspect-row');item.append(make('label',label),make('div',value));grid.append(item);};
      if(node.kind==='task'){
        const telemetry=telemetryOf(node)||{phases:[],logs:[]};
        row('Task ID',node.detail.id);
        row('State',`${node.detail.status}${node.tier==='stage'?' // sub-stage':' // root room'}`);
        row('Run',telemetry.run_id===null||telemetry.run_id===undefined?'No recorded run':`${telemetry.run_id} // ${telemetry.run_status||'unknown'}${telemetry.run_outcome?` // ${telemetry.run_outcome}`:''}`);
        row('Active step',telemetry.step_key||'No step key recorded');
        const runtime=elapsed(telemetry.started_at,telemetry.ended_at);
        row('Runtime',runtime?`${runtime}${telemetry.ended_at?'':' // still running'}`:'Not started');
        row('Agent model',telemetry.model?`${telemetry.model}${telemetry.provider?` @ ${telemetry.provider}`:''}`:'No model recorded for this run');
        row('Worker',telemetry.worker_pid?`PID ${telemetry.worker_pid}${telemetry.claim_lock?` // ${telemetry.claim_lock}`:''}`:'No claim holder');
        row('Branch / worktree',telemetry.branch_name||telemetry.worktree_path||`${telemetry.workspace_kind||'workspace'} // no worktree recorded`);
        row('Workspace',node.detail.workspace_path||'No recorded workspace');
        if(telemetry.error)row('Run error',telemetry.error);
        if(telemetry.summary)row('Run summary',telemetry.summary);
        const phases=make('div','','inspect-row'); phases.append(make('label','Lifecycle phases'),renderPhases(telemetry)); grid.append(phases);
        const governing=(hud.data?hud.data.rules:[]).filter(rule=>(hud.data.rule_task_links||[]).some(link=>link.proposal_id===rule.proposal_id&&link.task_id===node.detail.id));
        const rules=make('div','','inspect-row'); rules.append(make('label','Governing memory'));
        if(!governing.length)rules.append(make('div','No accepted rule is bound to this workspace.'));
        governing.forEach(rule=>{rules.append(make('div',`${rule.rule_id} // ${rule.checks.join(' · ')}`));});
        grid.append(rules);
        root.append(grid); root.append(renderRoomWindow(telemetry));
      } else {
        row('Accepted rule',node.detail.rule_id);row('Scope',`${node.detail.task_kind} @ ${node.detail.workspace_path}`);
        const checks=make('div','');node.detail.checks.forEach(check=>checks.append(make('span',check,'chip')));
        const item=make('div','','inspect-row');item.append(make('label','Constraints'),checks);grid.append(item);
        const citation=make('div','','inspect-row');citation.append(make('label','Provenance'));
        node.detail.citations.forEach(source=>citation.append(make('div',`${shortPath(source.source_path)}:${source.source_line} // ${source.line_digest.slice(0,12)}`)));
        grid.append(citation); root.append(grid);
      }
    }
    function renderMemory() {
      const root=byId('memoryBank'),data=hud.data; clear(root); setText(byId('memoryCount'),`${data.rules.length} accepted`);
      if(!data.rules.length){root.className='memory-empty';root.textContent='No accepted procedural rules.';return;} root.className='memory-list';
      data.rules.forEach(rule=>{const card=make('article','','memory-rule');card.append(make('h3',rule.rule_id),make('p',`${rule.task_kind} // ${rule.checks.join(' · ')}`),make('p',`${rule.citations.length} provenance link${rule.citations.length===1?'':'s'} // ${shortPath(rule.workspace_path)}`));card.addEventListener('click',()=>{hud.selected=`rule:${rule.proposal_id}`;renderInspector();if(isPhone())openSheet(true);});root.append(card);});
      if(data.memory_errors.length){const warning=make('p',data.memory_errors.join(' | '),'warn');root.append(warning);}
    }
    function anchoredLayout() { return !hud.edges.some(edge=>edge.kind==='dependency'); }
    function coreGeometry(rect) { return {cx:rect.width*.5,cy:rect.height*.5,orbit:Math.min(rect.width,rect.height)*.307}; }
    function advanceGraph() {
      const rect=canvas.getBoundingClientRect(),nodes=hud.nodes; if(nodes.length&&anchoredLayout()){
        const {cx,cy,orbit}=coreGeometry(rect);
        nodes.forEach((node,index)=>{if(hud.dragging===node.id)return;const angle=(index/nodes.length)*Math.PI*2-Math.PI/2,tx=cx+Math.cos(angle)*orbit,ty=cy+Math.sin(angle)*orbit;node.vx=0;node.vy=0;node.x+=(tx-node.x)*.07;node.y+=(ty-node.y)*.07;});
      } else if(nodes.length){
        for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){const a=nodes[i],b=nodes[j],dx=a.x-b.x||.01,dy=a.y-b.y||.01,d2=Math.min(dx*dx+dy*dy,40000),force=1200/d2;a.vx+=dx*force;a.vy+=dy*force;b.vx-=dx*force;b.vy-=dy*force;}
        hud.edges.forEach(edge=>{const a=hud.nodeById.get(edge.source),b=hud.nodeById.get(edge.target),dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy)),force=(d-130)*.003;a.vx+=dx/d*force;a.vy+=dy/d*force;b.vx-=dx/d*force;b.vy-=dy/d*force;});
        nodes.forEach(node=>{if(hud.dragging===node.id)return;node.vx+=(rect.width*.5-node.x)*.0008;node.vy+=(rect.height*.5-node.y)*.0008;node.vx*=.84;node.vy*=.84;node.x=Math.max(24,Math.min(rect.width-24,node.x+node.vx));node.y=Math.max(24,Math.min(rect.height-24,node.y+node.vy));});
      } drawGraph(); requestAnimationFrame(advanceGraph);
    }
    function drawBackdrop(rect) {
      const bounds=visibleWorld(rect),{cx,cy}=coreGeometry(rect),step=40; ctx.save(); ctx.lineWidth=1/hud.view.k;
      ctx.strokeStyle='rgba(0,240,255,.05)'; ctx.beginPath();
      for(let x=cx+Math.ceil((bounds.x0-cx)/step)*step;x<bounds.x1;x+=step){ctx.moveTo(x,bounds.y0);ctx.lineTo(x,bounds.y1);}
      for(let y=cy+Math.ceil((bounds.y0-cy)/step)*step;y<bounds.y1;y+=step){ctx.moveTo(bounds.x0,y);ctx.lineTo(bounds.x1,y);}
      ctx.stroke();
      ctx.strokeStyle='rgba(0,240,255,.16)'; ctx.beginPath(); ctx.moveTo(cx,bounds.y0); ctx.lineTo(cx,bounds.y1); ctx.moveTo(bounds.x0,cy); ctx.lineTo(bounds.x1,cy); ctx.stroke();
      ctx.strokeStyle='rgba(0,240,255,.07)'; const span=Math.min(rect.width,rect.height)*.46;
      for(let ring=1;ring<=3;ring++){ctx.beginPath();ctx.arc(cx,cy,span*ring/3,0,Math.PI*2);ctx.stroke();}
      ctx.restore();
    }
    function drawLabelPill(text,x,y,bounds) {
      ctx.save(); ctx.shadowBlur=0; ctx.font='10px "JetBrains Mono", monospace'; ctx.textBaseline='middle';
      const width=ctx.measureText(text).width+10,height=15,top=y-height/2;
      let left=x-5; if(bounds&&left+width>bounds.x1-4)left=Math.max(bounds.x0+4,bounds.x1-4-width);
      ctx.beginPath(); if(ctx.roundRect)ctx.roundRect(left,top,width,height,4); else ctx.rect(left,top,width,height);
      ctx.fillStyle='rgba(5,7,10,.74)'; ctx.fill(); ctx.strokeStyle='rgba(0,240,255,.18)'; ctx.lineWidth=1/hud.view.k; ctx.stroke();
      ctx.fillStyle='#d9faff'; ctx.fillText(text,left+5,y); ctx.restore();
    }
    function drawCore(rect) {
      if(!anchoredLayout())return;
      const {cx,cy}=coreGeometry(rect); ctx.save(); ctx.lineWidth=1/hud.view.k;
      ctx.strokeStyle='rgba(0,240,255,.10)'; ctx.setLineDash([3,5]);
      hud.nodes.forEach(node=>{ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(node.x,node.y);ctx.stroke();});
      ctx.setLineDash([]);
      [[26,'rgba(0,240,255,.20)'],[46,'rgba(0,240,255,.12)'],[68,'rgba(0,240,255,.07)']].forEach(([radius,tint])=>{ctx.strokeStyle=tint;ctx.beginPath();ctx.arc(cx,cy,radius,0,Math.PI*2);ctx.stroke();});
      ctx.fillStyle='rgba(0,240,255,.10)'; ctx.beginPath(); ctx.arc(cx,cy,9,0,Math.PI*2); ctx.fill();
      ctx.strokeStyle='rgba(0,240,255,.52)'; ctx.stroke(); ctx.restore();
      drawLabelPill('[TRI-AI CORE]',cx+16,cy,visibleWorld(rect));
    }
    function convexHull(points) {
      if(points.length<3)return points.slice();
      const sorted=points.slice().sort((a,b)=>a.x-b.x||a.y-b.y);
      const cross=(o,a,b)=>(a.x-o.x)*(b.y-o.y)-(a.y-o.y)*(b.x-o.x);
      const lower=[],upper=[];
      for(const point of sorted){while(lower.length>=2&&cross(lower[lower.length-2],lower[lower.length-1],point)<=0)lower.pop();lower.push(point);}
      for(let i=sorted.length-1;i>=0;i--){const point=sorted[i];while(upper.length>=2&&cross(upper[upper.length-2],upper[upper.length-1],point)<=0)upper.pop();upper.push(point);}
      lower.pop(); upper.pop(); return lower.concat(upper);
    }
    function drawHulls(bounds) {
      hud.groups.forEach(group=>{
        const hull=convexHull(group.members.map(node=>({x:node.x,y:node.y})));
        if(!hull.length)return;
        ctx.save(); ctx.lineJoin='round'; ctx.lineCap='round';
        ctx.beginPath(); ctx.moveTo(hull[0].x,hull[0].y);
        hull.slice(1).forEach(point=>ctx.lineTo(point.x,point.y));
        if(hull.length>2)ctx.closePath();
        ctx.strokeStyle='rgba(0,240,255,.05)'; ctx.lineWidth=64; ctx.stroke();
        ctx.strokeStyle='rgba(0,240,255,.16)'; ctx.lineWidth=66; ctx.setLineDash([7,9]); ctx.stroke(); ctx.setLineDash([]);
        ctx.restore();
        const anchor=hull.reduce((best,point)=>point.y<best.y?point:best,hull[0]);
        drawLabelPill(`[ ${shortPath(group.path)} ]`,anchor.x-6,anchor.y-42,bounds);
      });
    }
    function drawPhaseRing(node,radius) {
      const telemetry=telemetryOf(node),phases=telemetry?telemetry.phases||[]:[];
      if(!phases.length)return;
      const outer=radius+9,gap=.16,span=(Math.PI*2)/phases.length;
      ctx.save(); ctx.lineCap='butt';
      phases.forEach((phase,index)=>{
        const from=-Math.PI/2+index*span+gap/2,to=from+span-gap;
        ctx.beginPath(); ctx.lineWidth=3; ctx.strokeStyle=PHASE_COLOR[phase.state]||PHASE_COLOR.pending;
        if(phase.state==='active'){ctx.shadowColor='#00f0ff';ctx.shadowBlur=12;} else {ctx.shadowBlur=0;}
        ctx.arc(node.x,node.y,outer,from,to); ctx.stroke();
      });
      ctx.restore();
    }
    function drawReactorCore(node,radius) {
      const beat=(Math.sin(Date.now()/320)+1)/2;
      ctx.save(); ctx.shadowColor='#00f0ff'; ctx.shadowBlur=10+beat*16;
      ctx.fillStyle=`rgba(0,240,255,${.22+beat*.30})`;
      ctx.beginPath(); ctx.arc(node.x,node.y,radius*(.34+beat*.16),0,Math.PI*2); ctx.fill();
      ctx.shadowBlur=0; ctx.strokeStyle=`rgba(217,250,255,${.35+beat*.35})`; ctx.lineWidth=1;
      ctx.beginPath(); ctx.arc(node.x,node.y,radius*.62,beat*Math.PI*2,beat*Math.PI*2+Math.PI*1.1); ctx.stroke();
      ctx.restore();
    }
    function drawRoomFrame(node,radius) {
      const size=radius+7; ctx.save(); ctx.strokeStyle='rgba(0,240,255,.34)'; ctx.lineWidth=1.5;
      [[-1,-1],[1,-1],[-1,1],[1,1]].forEach(([sx,sy])=>{
        ctx.beginPath();
        ctx.moveTo(node.x+sx*size,node.y+sy*size-sy*5);
        ctx.lineTo(node.x+sx*size,node.y+sy*size);
        ctx.lineTo(node.x+sx*size-sx*5,node.y+sy*size);
        ctx.stroke();
      });
      ctx.restore();
    }
    function drawGraph() {
      const rect=resizeCanvas();ctx.clearRect(0,0,rect.width,rect.height);
      ctx.save();ctx.translate(hud.view.x,hud.view.y);ctx.scale(hud.view.k,hud.view.k);
      const bounds=visibleWorld(rect);
      drawBackdrop(rect);drawCore(rect);drawHulls(bounds);ctx.lineWidth=1;
      hud.edges.forEach(edge=>{const a=hud.nodeById.get(edge.source),b=hud.nodeById.get(edge.target);if(!a||!b)return;ctx.save();ctx.strokeStyle=edge.kind==='governs'?'rgba(255,183,3,.72)':'rgba(0,240,255,.42)';ctx.setLineDash(edge.kind==='governs'?[5,5]:[]);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();ctx.restore();});
      hud.nodes.forEach(node=>{
        const selected=node.id===hud.selected,status=node.kind==='rule'?'rule':node.detail.status;
        const color=node.kind==='rule'?'#ffb703':statusColor(status),radius=nodeRadius(node);
        ctx.save();ctx.shadowColor=color;ctx.shadowBlur=selected?22:(status==='done'||status==='running'?16:7);
        ctx.strokeStyle=color;ctx.fillStyle='#05070a';ctx.lineWidth=selected?2.5:1.5;
        if(node.kind==='rule'){ctx.beginPath();ctx.rect(node.x-radius,node.y-radius,radius*2,radius*2);ctx.fill();ctx.stroke();}
        else{
          ctx.beginPath();ctx.arc(node.x,node.y,radius,0,Math.PI*2);ctx.fill();ctx.stroke();
          if(status==='failed'||status==='cancelled'){ctx.setLineDash([4,4]);ctx.beginPath();ctx.arc(node.x,node.y,radius+5,.28,Math.PI*1.35);ctx.stroke();ctx.setLineDash([]);}
        }
        ctx.shadowBlur=0;
        if(node.tier==='room')drawRoomFrame(node,radius);
        if(status==='running'){drawReactorCore(node,radius);drawPhaseRing(node,radius);}
        if(hud.nodes.length<26||selected){drawLabelPill(node.label,node.x+radius+(status==='running'?14:6),node.y,bounds);}
        ctx.restore();
      });
      ctx.restore();
    }
    canvas.addEventListener('pointerdown',event=>{
      const node=hitNode(graphPoint(event)); hud.moved=false;
      if(node){hud.selected=node.id;hud.dragging=node.id;renderInspector();if(isPhone())openSheet(true);}
      else {const point=screenPoint(event);hud.panning={px:point.x,py:point.y,ox:hud.view.x,oy:hud.view.y};}
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',event=>{
      if(hud.dragging){const node=hud.nodeById.get(hud.dragging),point=graphPoint(event);node.x=point.x;node.y=point.y;node.vx=node.vy=0;hud.moved=true;return;}
      if(!hud.panning)return;
      const point=screenPoint(event);
      if(Math.hypot(point.x-hud.panning.px,point.y-hud.panning.py)>4)hud.moved=true;
      hud.view.x=hud.panning.ox+(point.x-hud.panning.px); hud.view.y=hud.panning.oy+(point.y-hud.panning.py);
    });
    canvas.addEventListener('pointerup',event=>{
      const now=Date.now(),point=screenPoint(event);
      if(!hud.moved&&now-hud.lastTapAt<320)zoomAt(point.x,point.y,hud.view.k>1.4?1:2);
      hud.lastTapAt=now; hud.dragging=null; hud.panning=null; canvas.releasePointerCapture?.(event.pointerId);
    });
    canvas.addEventListener('pointercancel',()=>{hud.dragging=null;hud.panning=null;});
    canvas.addEventListener('wheel',event=>{event.preventDefault();const point=screenPoint(event);zoomAt(point.x,point.y,hud.view.k*(event.deltaY<0?1.12:.89));},{passive:false});
    canvas.addEventListener('dblclick',event=>{const point=screenPoint(event);zoomAt(point.x,point.y,hud.view.k>1.4?1:2);});
    byId('sheetHandle').addEventListener('click',()=>openSheet(!byId('hudAside').classList.contains('open')));
    window.addEventListener('resize',drawGraph); requestAnimationFrame(advanceGraph);
    function render(data) {
      hud.data=data; setText(byId('total'),data.metrics.total_tasks);setText(byId('active'),data.metrics.active_runs);setText(byId('ledger'),data.metrics.ledger_entries);setText(byId('rules'),data.metrics.accepted_rules);
      const services=byId('services');clear(services);for(const [name,alive] of Object.entries(data.daemons.processes)){const item=make('div','',`service ${alive?'up':'down'}`);item.append(make('i','','dot'),make('span',`${name} ${alive?'up':'down'}`));services.append(item);} syncGraph(data);
      const events=byId('events');clear(events);const heading=make('div','','event');['Time','Task','Outcome','Verify','Duration'].forEach(label=>heading.append(make('span',label)));events.append(heading);data.ledger_events.forEach(event=>{const row=make('div','','event'),exit=event.verify_exit===0?'0':event.verify_exit===null?'-':String(event.verify_exit);row.append(make('span',monoTime(event.timestamp)),make('span',event.task_id),make('span',event.outcome,`outcome-badge ${event.outcome==='passed'?'pass':event.outcome==='skipped'?'warn':'fail'}`),make('span',exit,event.verify_exit===0?'pass':event.verify_exit===null?'warn':'fail'),make('span',event.seconds===null?'-':`${event.seconds}s`));events.append(row);}); if(data.ledger_errors.length){const issue=make('div',data.ledger_errors.join(' | '),'event warn');issue.style.gridTemplateColumns='1fr';events.append(issue);} setText(byId('connection'),`Supervisor state: ${data.daemons.status} // SSE snapshot ${new Date().toLocaleTimeString()}`);
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
            {
                "id": task.task_id, "title": task.title, "status": task.status,
                "run_id": task.run_id, "workspace_path": task.workspace_path,
                "telemetry": _telemetry_payload(task),
            }
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
        "rules": [
            {
                "proposal_id": rule.proposal_id,
                "rule_id": rule.rule_id,
                "workspace_path": rule.workspace_path,
                "task_kind": rule.task_kind,
                "checks": list(rule.checks),
                "citations": [
                    {
                        "source_path": citation.source_path,
                        "source_line": citation.source_line,
                        "line_digest": citation.line_digest,
                    }
                    for citation in rule.citations
                ],
            }
            for rule in snapshot.rules
        ],
        "rule_task_links": _rule_task_links(snapshot),
        "memory_errors": list(snapshot.memory_errors),
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
            runs_root=runtime / "runs",
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
