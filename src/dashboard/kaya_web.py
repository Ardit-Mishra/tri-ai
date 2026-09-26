"""Read-only web surface for the KAYA evidence snapshot.

Loopback by default; a wider binding exists for reaching the HUD from a phone
over a trusted network, and has to be asked for explicitly. There is no
mutating endpoint at any binding.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard import kaya_terminal
else:
    from . import kaya_terminal


SnapshotReader = Callable[[], kaya_terminal.DashboardSnapshot]


class KayaHTTPServer(ThreadingHTTPServer):
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


def _rule_task_links(snapshot: kaya_terminal.DashboardSnapshot) -> list[dict[str, str]]:
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


def _telemetry_payload(task: kaya_terminal.TaskView) -> dict[str, object]:
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
        "artifacts": [dict(item) for item in telemetry.artifacts],
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


PRODUCT_NAME = "KAYA"

_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TRI-AI // KAYA</title>
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
    .metrics { display:grid; gap:1px; grid-template-columns:repeat(6,minmax(0,1fr)); background:var(--line); border:1px solid var(--line); margin:18px 0; }
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
    .graph-head-tools { align-items:center; display:flex; gap:8px; }
    .view-switch { border:1px solid var(--line); display:flex; }
    .view-switch button { background:transparent; border:0; color:var(--muted); cursor:pointer; font:inherit; min-height:28px; padding:4px 8px; text-transform:uppercase; }
    .view-switch button+button { border-left:1px solid var(--line); }
    .view-switch button.active { background:rgba(0,240,255,.12); color:var(--cyan); }
    .view-switch button:disabled { cursor:not-allowed; opacity:.35; }
    #spatialGraph { display:none; height:420px; min-width:0; overflow:hidden; position:relative; touch-action:none; width:100%; }
    #spatialGraph canvas { display:block; height:100%; width:100%; }
    .graph-panel.view-3d #spatialGraph { display:block; }
    .graph-panel.view-3d #neuralGraph { display:none; }
    .spatial-tooltip { background:rgba(3,6,10,.94); border:1px solid rgba(0,240,255,.45); color:var(--text); display:none; font-size:10px; max-width:240px; padding:7px 9px; pointer-events:none; position:absolute; z-index:3; }
    .spatial-tooltip b { color:var(--cyan); display:block; margin-bottom:3px; }
    .inspector-empty,.memory-empty { color:var(--muted); font-size:11px; line-height:1.6; } .inspect-title { color:var(--cyan); font-size:14px; margin:0 0 10px; overflow-wrap:anywhere; } .inspect-grid { display:grid; gap:8px; } .inspect-row { border-top:1px solid rgba(0,240,255,.12); padding-top:8px; } .inspect-row label { color:var(--muted); display:block; font-size:9px; margin-bottom:4px; text-transform:uppercase; } .inspect-row div { color:var(--text); font-size:11px; overflow-wrap:anywhere; } .chip { border:1px solid rgba(255,183,3,.42); color:var(--amber); display:inline-block; font-size:9px; margin:0 4px 4px 0; padding:3px 5px; }
    .memory-list { display:grid; gap:9px; max-height:318px; overflow:auto; } .memory-rule { border-left:2px solid var(--amber); padding:8px 0 8px 9px; } .memory-rule h3 { color:var(--amber); font-size:11px; margin:0 0 5px; overflow-wrap:anywhere; } .memory-rule p { color:var(--muted); font-size:9px; line-height:1.5; margin:0; overflow-wrap:anywhere; }
    .registry-list,.radar-list { display:grid; gap:7px; max-height:290px; overflow:auto; }
    .registry-item,.radar-item { background:rgba(2,5,8,.36); border-left:2px solid rgba(0,240,255,.58); min-width:0; padding:8px 9px; }
    .radar-item { border-left-color:rgba(244,114,182,.72); }
    .registry-item h3,.radar-item h3 { color:var(--text); font-size:10px; margin:0 0 5px; overflow-wrap:anywhere; }
    .registry-item p,.radar-item p { color:var(--muted); font-size:9px; line-height:1.45; margin:0; overflow-wrap:anywhere; }
    .registry-item .state-token,.radar-item .state-token { border:1px solid rgba(148,163,184,.3); color:var(--muted); display:inline-block; font-size:8px; margin:5px 4px 0 0; padding:2px 4px; text-transform:uppercase; }
    .state-token.ready { border-color:rgba(0,255,157,.38); color:var(--emerald); }
    .state-token.gated { border-color:rgba(255,183,3,.42); color:var(--amber); }
    .state-token.blocked { border-color:rgba(255,77,109,.42); color:var(--crimson); }
    .registry-summary { color:var(--muted); font-size:9px; line-height:1.55; margin-bottom:9px; }
    .section-title { color:var(--cyan); font-family:"JetBrains Mono","Fira Code",ui-monospace,monospace; } .evidence { background:var(--surface); border-color:var(--line); } .event { border-color:rgba(0,240,255,.12); } .pass { color:var(--emerald); } .fail { color:var(--crimson); } .warn { color:var(--amber); } .state { color:var(--muted); }
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
    .header-right { align-items:flex-end; display:flex; flex-direction:column; gap:8px; }
    .stream-badge { border-radius:9999px; font-size:9px; letter-spacing:.06em; padding:4px 10px; text-transform:uppercase; white-space:nowrap; }
    .stream-badge.live { background:rgba(0,255,157,.10); border:1px solid rgba(0,255,157,.45); color:var(--emerald); }
    .stream-badge.down { background:rgba(255,183,3,.10); border:1px solid rgba(255,183,3,.45); color:var(--amber); }
    /* Removes the 300ms synthetic click delay without disabling pinch zoom. */
    .sheet-handle, .memory-rule, .room-window > summary { touch-action:manipulation; }
    .sheet-handle { display:none; }
    .preview { align-items:center; background:rgba(2,5,9,.86); bottom:0; display:none; justify-content:center; left:0; padding:20px; position:fixed; right:0; top:0; z-index:90; }
    .preview.open { display:flex; }
    .preview-pane { background:var(--surface); border:1px solid var(--cyan); box-shadow:0 24px 70px rgba(0,0,0,.6); display:flex; flex-direction:column; max-height:88vh; max-width:min(1100px,94vw); width:100%; }
    .preview-head { align-items:center; border-bottom:1px solid var(--line); display:flex; gap:12px; justify-content:space-between; padding:11px 14px; }
    .preview-title { color:var(--cyan); font-size:11px; overflow-wrap:anywhere; text-transform:uppercase; }
    .preview-actions { display:flex; flex:none; gap:8px; }
    .preview-actions a, .preview-actions button { background:transparent; border:1px solid var(--line); color:var(--muted); cursor:pointer; font-family:inherit; font-size:10px; padding:5px 10px; text-decoration:none; text-transform:uppercase; }
    .preview-actions a:hover, .preview-actions button:hover { border-color:var(--cyan); color:var(--cyan); }
    .preview-body { background:#05070a; flex:1; min-height:52vh; }
    .preview-body iframe { border:0; display:block; height:100%; min-height:52vh; width:100%; }
    .preview-note { color:var(--muted); font-size:9px; padding:8px 14px; }
    @media (max-width:767px) { .preview { padding:0; } .preview-pane { max-height:100vh; max-width:100vw; } }
    .now { background:linear-gradient(180deg,rgba(0,240,255,.055),rgba(9,14,20,.72)); border:1px solid var(--line); margin-top:14px; padding:16px 18px; position:relative; }
    .now::before { border-left:2px solid var(--cyan); border-top:2px solid var(--cyan); content:""; height:14px; left:-1px; position:absolute; top:-1px; width:14px; }
    .now-kicker { align-items:center; color:var(--muted); display:flex; font-size:10px; gap:8px; letter-spacing:.08em; text-transform:uppercase; }
    .now-live { background:var(--emerald); border-radius:9999px; box-shadow:0 0 10px rgba(0,255,157,.7); height:8px; width:8px; animation:pulse 1.6s ease-in-out infinite; }
    .now-idle { background:rgba(148,163,184,.5); border-radius:9999px; height:8px; width:8px; }
    .now-what { color:var(--text); font-family:Inter,ui-sans-serif,system-ui,"Segoe UI",sans-serif; font-size:19px; font-weight:600; line-height:1.35; margin:10px 0 0; overflow-wrap:anywhere; }
    .now-sub { color:var(--muted); font-size:12px; margin-top:8px; }
    .now-sub b { color:var(--cyan); font-weight:600; }
    .now-steps { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
    .now-step { border:1px solid rgba(148,163,184,.28); border-radius:9999px; color:var(--muted); font-size:10px; padding:4px 10px; }
    .now-step.done { border-color:#00ff9d55; color:var(--emerald); }
    .now-step.active { background:rgba(0,240,255,.12); border-color:#00f0ff88; color:var(--cyan); }
    .now-step.skipped { opacity:.45; }
    .now-say { background:rgba(2,5,8,.6); border-left:2px solid rgba(0,240,255,.4); color:#bfe9ff; font-size:11px; line-height:1.55; margin-top:12px; padding:9px 11px; overflow-wrap:anywhere; }
    .now-files { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
    .now-file { align-items:center; background:rgba(0,255,157,.08); border:1px solid #00ff9d44; border-radius:9999px; color:var(--emerald); display:inline-flex; font-size:11px; gap:6px; padding:6px 12px; text-decoration:none; }
    .now-file:hover { background:rgba(0,255,157,.16); }
    @media (max-width:767px) {
      .now { padding:14px; }
      .now-what { font-size:17px; }
      .graph-panel { min-height:300px; }
      #neuralGraph { height:260px; }
      .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }
      .terminal { min-width:0; }
      .event { gap:6px; grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto; }
      .event span:nth-child(4), .event span:nth-child(5) { display:none; }
      .event:first-child span:nth-child(4), .event:first-child span:nth-child(5) { display:none; }
    }
    @media (prefers-reduced-motion: reduce) {
      *,*::before,*::after { animation-duration:.01ms!important; animation-iteration-count:1!important; scroll-behavior:auto!important; transition-duration:.01ms!important; }
    }
    @media (max-width:767px) {
      .hud-grid { grid-template-columns:1fr; }
      .hud-aside { background:rgba(4,7,11,.97); border-top:1px solid var(--cyan); bottom:0; box-shadow:0 -14px 34px rgba(0,0,0,.55); gap:10px; grid-template-columns:1fr; left:0; max-height:78vh; overflow-y:auto; padding:0 12px 16px; position:fixed; right:0; transform:translateY(calc(100% - 44px)); transition:transform .26s ease; z-index:40; }
      .hud-aside.open { transform:translateY(0); }
      .sheet-handle { align-content:center; background:rgba(4,7,11,.97); cursor:pointer; display:block; min-height:48px; padding:10px 0 8px; position:sticky; text-align:center; top:0; }
      .sheet-handle span { background:rgba(0,240,255,.45); border-radius:9999px; display:inline-block; height:4px; width:46px; }
      .sheet-handle em { color:var(--muted); display:block; font-size:9px; font-style:normal; margin-top:5px; text-transform:uppercase; }
      .shell { padding-bottom:64px; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div class="brand">TRI-AI // KAYA <span>READ ONLY</span></div>
      <div class="header-right">
        <!-- Outside #services on purpose: render() clears that container on
             every snapshot, and the stream badge must survive a re-render. -->
        <span class="stream-badge down" id="streamBadge" role="status" aria-atomic="true">Evidence stream connecting</span>
        <div class="services" id="services" aria-label="Daemon service state"></div>
      </div>
    </header>
    <section class="now" id="now" aria-live="polite">
      <div class="now-kicker"><i class="now-idle" id="nowPip"></i><span id="nowKicker">Checking…</span></div>
      <p class="now-what" id="nowWhat">—</p>
      <div class="now-sub" id="nowSub"></div>
      <div class="now-steps" id="nowSteps"></div>
      <div class="now-say" id="nowSay" hidden></div>
      <div class="now-files" id="nowFiles"></div>
    </section>
    <section class="metrics" aria-label="System metrics">
      <div class="metric"><label>Tasks</label><strong id="total">-</strong></div>
      <div class="metric"><label>Running now</label><strong id="active">-</strong></div>
      <div class="metric"><label>Finished runs</label><strong id="ledger">-</strong></div>
      <div class="metric"><label>Learned rules</label><strong id="rules">-</strong></div>
      <div class="metric"><label>Indexed resources</label><strong id="capabilityCount">-</strong></div>
      <div class="metric"><label>Radar candidates</label><strong id="radarCount">-</strong></div>
    </section>
    <section class="hud-grid" aria-label="Neural task and memory map">
      <section class="hud-panel graph-panel">
        <div class="panel-head"><strong>Execution topology</strong><span class="graph-head-tools"><span id="graphSummary">Awaiting evidence</span><span class="view-switch" aria-label="Topology view"><button id="graph3d" type="button" disabled>3D</button><button id="graph2d" type="button" class="active">2D</button><button id="motionToggle" type="button" aria-pressed="false">Pause</button></span></span></div>
        <div id="spatialGraph" role="img" aria-label="Interactive three-dimensional Tri-AI execution and capability topology"><div class="spatial-tooltip" id="spatialTooltip"></div></div>
        <canvas id="neuralGraph" role="img" aria-label="Interactive task, memory, capability, and technology-radar graph"></canvas>
        <div class="graph-hint">Drag canvas to pan // double-tap or wheel to zoom // tap a node to inspect</div>
        <div class="graph-legend"><span><i class="legend-dot" style="background:#00f0ff"></i>task</span><span><i class="legend-dot" style="background:#00ff9d"></i>done</span><span><i class="legend-dot" style="background:#ff4d6d"></i>failed/cancelled</span><span><i class="legend-dot" style="background:#ffb703"></i>accepted rule</span><span><i class="legend-dot" style="background:#a78bfa"></i>brain memory</span><span><i class="legend-dot" style="background:#38bdf8"></i>capability</span><span><i class="legend-dot" style="background:#f472b6"></i>radar</span></div>
      </section>
      <aside class="hud-aside" id="hudAside">
        <div class="sheet-handle" id="sheetHandle" role="button" tabindex="0" aria-label="Toggle inspector sheet"><span></span><em id="sheetLabel">Inspector</em></div>
        <section class="hud-panel"><div class="panel-head"><strong>Details</strong><span>read only</span></div><div id="inspector" class="inspector-empty">Select a task or accepted rule.</div></section>
        <section class="hud-panel"><div class="panel-head"><strong>Brain inbox</strong><span id="memoryCount">0 memories</span></div><div id="memoryBank" class="memory-empty">Brain has no indexed memories.</div></section>
        <section class="hud-panel"><div class="panel-head"><strong>Capability registry</strong><span id="capabilityStatus">uninitialized</span></div><div id="capabilityRegistry" class="memory-empty">Capability evidence is not available.</div></section>
        <section class="hud-panel"><div class="panel-head"><strong>Technology radar</strong><span id="radarStatus">uninitialized</span></div><div id="technologyRadar" class="memory-empty">No discovery run has been recorded.</div></section>
      </aside>
    </section>
    <div class="section-title">Recent activity</div>
    <section class="evidence"><div class="terminal" id="events"></div></section>
    <div class="preview" id="preview" role="dialog" aria-modal="true" aria-label="Artifact preview" hidden>
      <div class="preview-pane">
        <div class="preview-head">
          <span class="preview-title" id="previewTitle">Artifact</span>
          <span class="preview-actions">
            <a id="previewOpen" href="#" target="_blank" rel="noopener">Open in tab</a>
            <button type="button" id="previewClose">Close</button>
          </span>
        </div>
        <div class="preview-body"><iframe id="previewFrame" sandbox="allow-scripts" title="Artifact preview"></iframe></div>
        <p class="preview-note">Rendered in a sandboxed frame. Tap outside, press Escape, or use Close to dismiss.</p>
      </div>
    </div>
    <div class="state" id="connection">Connecting to local evidence stream...</div>
  </main>
  <script>
    const byId = id => document.getElementById(id);
    const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); };
    const make = (tag, text, cls) => { const node=document.createElement(tag); node.textContent=text; if(cls) node.className=cls; return node; };
    // Relative time is what an operator glancing at a phone actually reads;
    // the exact clock value stays reachable as the element's title so nothing
    // is lost. Both come from the same recorded epoch seconds.
    const absoluteTime = ts => typeof ts==='number' ? new Date(ts*1000).toLocaleString() : 'not recorded';
    function elapsedSince(ts) {
      if(typeof ts!=='number')return null;
      const span=Math.max(0,Math.floor(Date.now()/1000-ts));
      if(span<60)return `${span}s ago`;
      if(span<3600)return `${Math.floor(span/60)}m ${span%60}s ago`;
      return `${Math.floor(span/3600)}h ${Math.floor((span%3600)/60)}m ago`;
    }
    function timeAgo(ts) {
      if(typeof ts!=='number')return '-';
      const span=Math.max(0,Math.floor(Date.now()/1000-ts));
      if(span<5)return 'just now';
      if(span<60)return `${span}s ago`;
      if(span<3600)return `${Math.floor(span/60)}m ago`;
      if(span<86400)return `${Math.floor(span/3600)}h ${Math.floor((span%3600)/60)}m ago`;
      return `${Math.floor(span/86400)}d ago`;
    }
    function duration(seconds) {
      if(typeof seconds!=='number')return '-';
      if(seconds<60)return `${seconds.toFixed(seconds<10?1:0)}s`;
      if(seconds<3600)return `${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s`;
      return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
    }
    const timeCell = ts => { const cell=make('span',timeAgo(ts)); cell.title=absoluteTime(ts); return cell; };
    const hud={data:null,nodes:[],nodeById:new Map(),edges:[],groups:[],selected:null,hover:null,dragging:null,view:{x:0,y:0,k:1},panning:null,lastTapAt:0,moved:false,claimedLabels:[],pendingHullLabels:[],pointers:new Map(),pinch:null};
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
      const brain=data.brain||{items:[],edges:[]};
      const capabilities=data.capabilities||{items:[]},radar=data.radar||{candidates:[],evaluations:[]};
      const evaluationByName=new Map((radar.evaluations||[]).map(item=>[item.name,item]));
      const nodes=[
        ...data.tasks.map(task=>({id:`task:${task.id}`,kind:'task',label:task.id,detail:task})),
        ...data.rules.map(rule=>({id:`rule:${rule.proposal_id}`,kind:'rule',label:rule.rule_id,detail:rule})),
        ...brain.items.map(item=>({id:`brain:${item.id}`,kind:'brain',label:item.title,detail:item})),
        ...(capabilities.items||[]).map(item=>({id:`capability:${item.id}`,kind:'capability',label:item.name,detail:item})),
        ...(radar.candidates||[]).map(item=>({id:`radar:${item.source}:${item.name}`,kind:'radar',label:item.name,detail:{...item,evaluation:evaluationByName.get(item.name)||null}})),
      ];
      const edges=[];
      data.edges.forEach(edge=>edges.push({source:`task:${edge.parent_id}`,target:`task:${edge.child_id}`,kind:'dependency'}));
      data.rule_task_links.forEach(link=>edges.push({source:`rule:${link.proposal_id}`,target:`task:${link.task_id}`,kind:'governs'}));
      brain.edges.forEach(edge=>edges.push({source:`brain:${edge.source_id}`,target:`brain:${edge.target_id}`,kind:'memory'}));
      const childIds=new Set(data.edges.map(edge=>`task:${edge.child_id}`));
      nodes.forEach(node=>{ node.tier=node.kind!=='task'?node.kind:childIds.has(node.id)?'stage':'room'; });
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
      renderNow(data); setText(byId('graphSummary'),`${rooms + stages} task${rooms + stages === 1 ? '' : 's'}${hud.edges.length ? ` // ${hud.edges.length} linked` : ''}`);
      renderInspector(); renderMemory(); renderCapabilities(data); renderRadar(data);
    }
    function resizeCanvas() { const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1; const width=Math.max(1,Math.round(rect.width*ratio)),height=Math.max(1,Math.round(rect.height*ratio)); if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;} ctx.setTransform(ratio,0,0,ratio,0,0); return rect; }
    function screenPoint(event) { const rect=canvas.getBoundingClientRect(); return {x:event.clientX-rect.left,y:event.clientY-rect.top}; }
    function graphPoint(event) { const point=screenPoint(event); return {x:(point.x-hud.view.x)/hud.view.k,y:(point.y-hud.view.y)/hud.view.k}; }
    function visibleWorld(rect) { const k=hud.view.k; return {x0:(0-hud.view.x)/k,y0:(0-hud.view.y)/k,x1:(rect.width-hud.view.x)/k,y1:(rect.height-hud.view.y)/k}; }
    function nodeRadius(node) { return node.kind==='rule'||node.kind==='brain'?11:node.kind==='capability'?9:node.kind==='radar'?8:node.tier==='room'?15:10; }
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
      if(!node){root.className='inspector-empty'; root.textContent='Select a task, memory, rule, capability, or radar candidate.'; return;} root.className='';
      const inspectTitle=node.kind==='task'?node.detail.title:node.kind==='rule'?node.detail.rule_id:node.kind==='brain'?node.detail.title:node.detail.name;
      root.append(make('h2',inspectTitle,'inspect-title'));
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
      } else if(node.kind==='rule') {
        row('Accepted rule',node.detail.rule_id);row('Scope',`${node.detail.task_kind} @ ${node.detail.workspace_path}`);
        const checks=make('div','');node.detail.checks.forEach(check=>checks.append(make('span',check,'chip')));
        const item=make('div','','inspect-row');item.append(make('label','Constraints'),checks);grid.append(item);
        const citation=make('div','','inspect-row');citation.append(make('label','Provenance'));
        node.detail.citations.forEach(source=>citation.append(make('div',`${shortPath(source.source_path)}:${source.source_line} // ${source.line_digest.slice(0,12)}`)));
        grid.append(citation); root.append(grid);
      } else if(node.kind==='brain') {
        row('Brain ID',node.detail.id);row('Trust',node.detail.trust.toUpperCase());
        row('Kind / project',`${node.detail.kind} // ${node.detail.project||'global'}`);
        row('Source',node.detail.source);row('Captured',absoluteTime(node.detail.created_at));
        root.append(grid);
      } else if(node.kind==='capability') {
        row('Resource ID',node.detail.id);row('Kind',node.detail.kind);
        row('Availability',node.detail.availability);row('Adapter',node.detail.adapter_status);
        row('Health evidence',node.detail.health_status);row('Risk review',node.detail.risk_status);
        row('Routing tags',(node.detail.tags||[]).join(' · ')||'No tags recorded');
        root.append(grid);
      } else {
        const evaluation=node.detail.evaluation;
        row('Discovery source',node.detail.source);row('Disposition',node.detail.disposition);
        row('Adoption signal',`${node.detail.stars||0} stars // popularity never auto-approves`);
        row('Static review',evaluation?evaluation.static_verdict:'Not evaluated');
        row('Dynamic probe',evaluation?evaluation.dynamic_status:'Dynamic probe not run');
        row('Signals',(node.detail.signals||[]).join(' · ')||'No signals recorded');
        root.append(grid);
      }
    }
    function renderNow(data) {
      const running=data.tasks.filter(task=>task.status==='running');
      const pip=byId('nowPip'),steps=byId('nowSteps'),say=byId('nowSay'),files=byId('nowFiles');
      clear(steps); clear(files); say.hidden=true;

      if(running.length){
        const task=running[0],tel=task.telemetry||{phases:[],logs:[]};
        pip.className='now-live';
        setText(byId('nowKicker'),running.length>1?`Working on ${running.length} things`:'Working on it');
        setText(byId('nowWhat'),taskLabel(task));
        const phase=(tel.phases||[]).find(p=>p.state==='active');
        const elapsed=elapsedSince(tel.started_at);
        const sub=byId('nowSub'); clear(sub);
        sub.append(make('span','Started '),make('b',elapsed||'just now'),
                   make('span',phase?` · now ${(PHASE_PLAIN[phase.key]||phase.key).toLowerCase()}`:''));
        (tel.phases||[]).forEach(p=>steps.append(make('span',PHASE_PLAIN[p.key]||p.key,`now-step ${p.state}`)));
        const agent=(tel.logs||[]).find(log=>log.name==='agent'&&log.lines&&log.lines.length);
        if(agent){ setText(say,agent.lines[agent.lines.length-1]); say.hidden=false; }
        return;
      }

      // Nothing running: report the most recent finished task and what it made.
      const finished=data.tasks.filter(task=>task.telemetry&&task.telemetry.ended_at)
        .sort((a,b)=>(b.telemetry.ended_at||0)-(a.telemetry.ended_at||0));
      pip.className='now-idle';
      if(!finished.length){
        setText(byId('nowKicker'),'Idle');
        setText(byId('nowWhat'),'Nothing has run yet.');
        clear(byId('nowSub'));
        return;
      }
      const task=finished[0],tel=task.telemetry;
      const capability=data.capabilities||{active:0,archived:0};
      setText(byId('nowKicker'),'System idle // last verified run retained');
      setText(byId('nowWhat'),`${capability.active||0} active resources available to the planner. ${capability.archived||0} archived references remain searchable.`);
      const sub=byId('nowSub'); clear(sub);
      const verdict=task.status==='done'?'Finished':task.status==='cancelled'?'Cancelled':'Stopped';
      sub.append(make('b',verdict),make('span',` ${timeAgo(tel.ended_at)}`));
      setText(say,`Last run // ${taskLabel(task)}`); say.hidden=false;
      (tel.phases||[]).forEach(p=>steps.append(make('span',PHASE_PLAIN[p.key]||p.key,`now-step ${p.state}`)));
      const made=(tel.artifacts||[]);
      if(made.length){
        files.append(make('span',`Made ${made.length} file${made.length===1?'':'s'}:`,'now-sub'));
        made.forEach((artifact,index)=>{
          const link=document.createElement('a');
          link.className='now-file'; link.href=`/artifact/${task.id}/${index}`;
          link.target='_blank'; link.rel='noopener';
          setText(link,`${artifact.path} ↗`);
          files.append(link);
        });
      }
    }
    function renderMemory() {
      const root=byId('memoryBank'),data=hud.data,brain=data.brain||{items:[],item_count:0,inbox_count:0,edge_count:0}; clear(root); setText(byId('memoryCount'),`${brain.item_count} memories // ${data.rules.length} rules`);
      if(!brain.items.length&&!data.rules.length){root.className='memory-empty';root.textContent=brain.diagnostic||'Brain has no indexed memories.';return;} root.className='memory-list';
      brain.items.forEach(item=>{const card=make('article','','memory-rule');card.append(make('h3',item.title),make('p',`${item.kind} // ${(item.project||'global')} // ${item.trust}`),make('p',`${item.source} // ${timeAgo(item.created_at)}`));card.addEventListener('click',()=>{hud.selected=`brain:${item.id}`;renderInspector();if(isPhone())openSheet(true);});root.append(card);});
      data.rules.forEach(rule=>{const card=make('article','','memory-rule');card.append(make('h3',rule.rule_id),make('p',`${rule.task_kind} // ${rule.checks.join(' · ')}`),make('p',`${rule.citations.length} provenance link${rule.citations.length===1?'':'s'} // ${shortPath(rule.workspace_path)}`));card.addEventListener('click',()=>{hud.selected=`rule:${rule.proposal_id}`;renderInspector();if(isPhone())openSheet(true);});root.append(card);});
      if(data.memory_errors.length){const warning=make('p',data.memory_errors.join(' | '),'warn');root.append(warning);}
      if(brain.diagnostic){root.append(make('p',brain.diagnostic,'warn'));}
    }
    function readinessLabel(item) {
      if(item.adapter_status==='gated'||item.availability==='gated')return 'Gated';
      if(item.availability==='source-only')return 'Source only';
      if(['executable','registered','active'].includes(item.availability))return 'Ready';
      return item.availability||'Unknown';
    }
    function renderCapabilities(data) {
      const root=byId('capabilityRegistry'),view=data.capabilities||{status:'uninitialized',items:[]};clear(root);
      setText(byId('capabilityStatus'),view.status||'unknown');
      if(!view.items.length){root.className='memory-empty';root.textContent=view.diagnostic||'Capability evidence is not available.';return;}
      root.className='registry-list';
      root.append(make('p',`${view.active} active resources // ${view.archived} archived references preserved // ${view.routable} routable`,'registry-summary'));
      view.items.forEach(item=>{
        const label=readinessLabel(item),card=make('article','','registry-item');
        const tokenClass=label==='Ready'?'ready':label==='Gated'?'gated':'';
        card.append(make('h3',item.name),make('p',`${item.kind} // ${(item.tags||[]).join(' · ')||'untagged'}`),make('span',label,`state-token ${tokenClass}`),make('span',item.health_status||'health unknown','state-token'));
        card.addEventListener('click',()=>{hud.selected=`capability:${item.id}`;renderInspector();if(isPhone())openSheet(true);});root.append(card);
      });
    }
    function renderRadar(data) {
      const root=byId('technologyRadar'),view=data.radar||{status:'uninitialized',candidates:[],evaluations:[]};clear(root);
      setText(byId('radarStatus'),view.status||'unknown');
      if(!view.candidates.length){root.className='memory-empty';root.textContent=view.diagnostic||'No discovery run has been recorded.';return;}
      root.className='radar-list';const evaluations=new Map((view.evaluations||[]).map(item=>[item.name,item]));
      view.candidates.forEach(item=>{
        const evaluation=evaluations.get(item.name),card=make('article','','radar-item');
        const staticLabel=evaluation?evaluation.static_verdict:'Static review pending';
        const dynamicLabel=evaluation&&evaluation.dynamic_status!=='not_run'?evaluation.dynamic_status:'Dynamic probe not run';
        card.append(make('h3',item.name),make('p',`${item.source} // ${item.stars||0} stars // ${item.disposition}`),make('span',staticLabel,'state-token'),make('span',dynamicLabel,`state-token ${dynamicLabel==='Dynamic probe not run'?'gated':''}`));
        card.addEventListener('click',()=>{hud.selected=`radar:${item.source}:${item.name}`;renderInspector();if(isPhone())openSheet(true);});root.append(card);
      });
      if(view.error_count)root.append(make('p',`${view.error_count} source error${view.error_count===1?'':'s'} recorded; inspect the retained radar report.`,'warn'));
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
    // A hex task id tells an operator checking in from a phone nothing at all,
    // so intent leads: the title is the primary label, the id is demoted to a
    // muted subtitle, and the workspace becomes a colour-coded tag. The colour
    // is derived from the workspace path rather than configured, so a project
    // added later is tagged without anyone editing a palette.
    const WORKSPACE_TINTS=[[0,240,255],[255,183,3],[167,139,250],[0,255,157],[255,122,182],[125,211,252]];
    const workspaceTints=new Map();
    function workspaceTint(path) {
      if(!path)return [148,163,184];
      const key=String(path).replace(/\\/g,'/').toLowerCase();
      if(!workspaceTints.has(key))workspaceTints.set(key,WORKSPACE_TINTS[workspaceTints.size%WORKSPACE_TINTS.length]);
      return workspaceTints.get(key);
    }
    const workspaceTag = path => path ? `[${String(path).replace(/\\/g,'/').split('/').filter(Boolean).pop().toUpperCase()}]` : '[UNSCOPED]';
    function truncate(text,limit) {
      const value=String(text==null?'':text).trim();
      if(!value)return '(untitled)';
      return value.length<=limit?value:`${value.slice(0,limit-1).trimEnd()}…`;
    }
    // The intake title is generic ("Telegram: tri-ai"); the prompt is what the
    // operator actually asked for, and is the only label that tells them apart.
    const taskLabel = task => (task && task.prompt && task.prompt.trim()) || (task && task.title) || '(untitled)';
    const narrowCanvas = () => canvas.getBoundingClientRect().width < 420;
    const PHASE_PLAIN = {claimed:'Picked up', worktree_prep:'Workspace ready', agent_active:'Building', verify_gate:'Testing'};
    const shortId = id => { const value=String(id); return value.length>12?`${value.slice(0,10)}…`:value; };
    function drawNamePlate(node,x,y,bounds,leftward) {
      if(node.kind!=='task'){drawLabelPill(node.label,x,y,bounds);return;}
      const title=truncate(taskLabel(node.detail),narrowCanvas()?18:22),id=shortId(node.detail.id);
      const tag=workspaceTag(node.detail.workspace_path),tint=workspaceTint(node.detail.workspace_path);
      ctx.save(); ctx.shadowBlur=0; ctx.textBaseline='middle';
      ctx.font='700 11px "JetBrains Mono", monospace'; const titleWidth=ctx.measureText(title).width;
      ctx.font='9px "JetBrains Mono", monospace';
      const width=Math.max(titleWidth,ctx.measureText(id).width,ctx.measureText(tag).width)+12;
      // Plates anchor outward from the core. With nodes on one orbit and no
      // dependency edges, a plate drawn to the right of a left-hand node lands
      // underneath its neighbour - which is what hid the cancelled task's name.
      const height=40; let left=leftward?x-width+5:x-5; let top=y-height/2;
      if(bounds){
        if(left+width>bounds.x1-4)left=Math.max(bounds.x0+4,bounds.x1-4-width);
        if(left<bounds.x0+4)left=Math.min(bounds.x1-4-width,bounds.x0+4);
      }
      for(let attempt=0;attempt<5;attempt++){
        const candidate={left,top,width,height};
        if(!hud.claimedLabels.some(taken=>rectsOverlap(candidate,taken,2)))break;
        top+=height+4;
      }
      hud.claimedLabels.push({left,top,width,height});
      ctx.beginPath();
      if(ctx.roundRect)ctx.roundRect(left,top,width,height,4); else ctx.rect(left,top,width,height);
      ctx.fillStyle='rgba(5,7,10,.82)'; ctx.fill();
      ctx.strokeStyle=`rgba(${tint[0]},${tint[1]},${tint[2]},.34)`; ctx.lineWidth=1/hud.view.k; ctx.stroke();
      ctx.beginPath(); ctx.moveTo(left,top+2); ctx.lineTo(left,top+height-2);
      ctx.strokeStyle=`rgba(${tint[0]},${tint[1]},${tint[2]},.85)`; ctx.lineWidth=2/hud.view.k; ctx.stroke();
      ctx.fillStyle=`rgba(${tint[0]},${tint[1]},${tint[2]},.86)`;
      ctx.font='9px "JetBrains Mono", monospace'; ctx.fillText(tag,left+6,top+9);
      ctx.fillStyle='#eaffff'; ctx.font='700 11px "JetBrains Mono", monospace'; ctx.fillText(title,left+6,top+21);
      ctx.fillStyle='rgba(148,163,184,.92)'; ctx.font='9px "JetBrains Mono", monospace'; ctx.fillText(id,left+6,top+32);
      ctx.restore();
    }
    function pillRect(text,x,y,bounds) {
      ctx.save(); ctx.font='10px "JetBrains Mono", monospace';
      const width=ctx.measureText(text).width+10,height=15,top=y-height/2;
      let left=x-5;
      if(bounds){
        if(left+width>bounds.x1-4)left=Math.max(bounds.x0+4,bounds.x1-4-width);
        if(left<bounds.x0+4)left=Math.min(bounds.x1-4-width,bounds.x0+4);
      }
      ctx.restore();
      return {left,top,width,height};
    }
    function rectsOverlap(a,b,pad) {
      const gap=pad||0;
      return a.left-gap < b.left+b.width && a.left+a.width+gap > b.left
          && a.top-gap < b.top+b.height && a.top+a.height+gap > b.top;
    }
    // Step a label outward until it stops landing on something already placed.
    function placeClear(text,x,y,bounds,dx,dy) {
      let px=x,py=y;
      for(let attempt=0;attempt<6;attempt++){
        const candidate=pillRect(text,px,py,bounds);
        if(!hud.claimedLabels.some(taken=>rectsOverlap(candidate,taken,3)))break;
        px+=dx*19; py+=dy*19;
      }
      const finalRect=pillRect(text,px,py,bounds);
      hud.claimedLabels.push(finalRect);
      return {x:px,y:py};
    }
    function drawLabelPill(text,x,y,bounds) {
      // Placement must agree with pillRect, or a label measured as fitting is
      // drawn somewhere else - which is how these ran off the left edge.
      const box=pillRect(text,x,y,bounds);
      ctx.save(); ctx.shadowBlur=0; ctx.font='10px "JetBrains Mono", monospace'; ctx.textBaseline='middle';
      const width=box.width,height=box.height,top=box.top;
      let left=box.left;
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
      const coreLabel='[TRI-AI CORE]',coreBounds=visibleWorld(rect);
      hud.claimedLabels.push(pillRect(coreLabel,cx+16,cy,coreBounds));
      drawLabelPill(coreLabel,cx+16,cy,coreBounds);
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
      // Two workspaces whose hulls top out at a similar height put their
      // labels on the same line and overlap - visible immediately at phone
      // width, where the canvas is narrow. Stagger by group index.
      hud.groups.forEach((group,groupIndex)=>{
        const hull=convexHull(group.members.map(node=>({x:node.x,y:node.y})));
        if(!hull.length)return;
        ctx.save(); ctx.lineJoin='round'; ctx.lineCap='round';
        ctx.beginPath(); ctx.moveTo(hull[0].x,hull[0].y);
        hull.slice(1).forEach(point=>ctx.lineTo(point.x,point.y));
        if(hull.length>2)ctx.closePath();
        // Three widening passes read as a soft field with depth rather than a
        // hard boundary; a cluster holding live work glows a little warmer.
        const firing=group.members.some(isFiring);
        const base=firing?[0,255,200]:[0,240,255];
        [[74,.030],[62,.045],[48,.060]].forEach(([lineWidth,alpha])=>{
          ctx.strokeStyle=`rgba(${base[0]},${base[1]},${base[2]},${alpha})`;
          ctx.lineWidth=lineWidth; ctx.stroke();
        });
        ctx.strokeStyle=`rgba(${base[0]},${base[1]},${base[2]},${firing?.22:.13})`;
        ctx.lineWidth=40; ctx.setLineDash([9,13]); ctx.stroke(); ctx.setLineDash([]);
        ctx.restore();
        const leaf=String(group.path).replace(/\\/g,'/').split('/').filter(Boolean).pop();
        const label=narrowCanvas()?`[ ${leaf} ]`:`[ ${shortPath(group.path)} ]`;
        ctx.save(); ctx.font='10px "JetBrains Mono", monospace';
        const labelWidth=ctx.measureText(label).width; ctx.restore();
        if(labelWidth>canvas.getBoundingClientRect().width*.5)return;
        const centre=hull.reduce((sum,point)=>({x:sum.x+point.x/hull.length,y:sum.y+point.y/hull.length}),{x:0,y:0});
        const {cx,cy}=coreGeometry(canvas.getBoundingClientRect());
        let dx=centre.x-cx,dy=centre.y-cy;
        const span=Math.hypot(dx,dy);
        if(span<1){dx=0;dy=-1;} else {dx/=span;dy/=span;}
        const reach=48+groupIndex*6;
        hud.pendingHullLabels.push({
          label, x:centre.x+dx*reach, y:centre.y+dy*reach, dx, dy,
        });
      });
    }
    // An axon bows perpendicular to its own run, so two nodes never sit on a
    // straight line through a third and parallel edges stay readable.
    function axonCurve(a,b) {
      const dx=b.x-a.x,dy=b.y-a.y,span=Math.max(1,Math.hypot(dx,dy));
      const bow=Math.min(span*.22,54);
      return {
        cx:(a.x+b.x)/2 - (dy/span)*bow,
        cy:(a.y+b.y)/2 + (dx/span)*bow,
      };
    }
    function axonPoint(a,b,control,t) {
      const u=1-t;
      return {
        x:u*u*a.x + 2*u*t*control.cx + t*t*b.x,
        y:u*u*a.y + 2*u*t*control.cy + t*t*b.y,
      };
    }
    function isFiring(node) {
      return node && node.kind==='task' && node.detail.status==='running';
    }
    function drawAxon(edge) {
      const a=hud.nodeById.get(edge.source),b=hud.nodeById.get(edge.target);
      if(!a||!b)return;
      const control=axonCurve(a,b);
      const governs=edge.kind==='governs';
      // An axon lights when either end is connected to what the operator is
      // looking at; otherwise it stays part of the quiet lattice.
      const lit=[hud.hover,hud.selected].some(id=>id&&(id===edge.source||id===edge.target));
      ctx.save();
      ctx.lineCap='round';
      ctx.strokeStyle=governs
        ? `rgba(255,183,3,${lit?.85:.42})`
        : `rgba(0,240,255,${lit?.72:.26})`;
      ctx.lineWidth=lit?2:1.2;
      if(lit){ctx.shadowColor=governs?'#ffb703':'#00f0ff';ctx.shadowBlur=10;}
      ctx.setLineDash(governs?[5,5]:[]);
      ctx.beginPath(); ctx.moveTo(a.x,a.y);
      ctx.quadraticCurveTo(control.cx,control.cy,b.x,b.y);
      ctx.stroke();
      ctx.restore();
      // A pulse only travels when the upstream task is actually running. It is
      // a readout of live execution, not ambient decoration.
      if(!isFiring(a))return;
      ctx.save(); ctx.shadowColor='#9ef4ff'; ctx.shadowBlur=12;
      for(let index=0;index<2;index++){
        const t=((Date.now()/1100)+index*.5)%1;
        const spot=axonPoint(a,b,control,t);
        const fade=Math.sin(t*Math.PI);
        ctx.fillStyle=`rgba(190,250,255,${.28+fade*.62})`;
        ctx.beginPath(); ctx.arc(spot.x,spot.y,1.6+fade*2.1,0,Math.PI*2); ctx.fill();
      }
      ctx.restore();
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
      const rect=resizeCanvas();ctx.clearRect(0,0,rect.width,rect.height);hud.claimedLabels=[];hud.pendingHullLabels=[];
      ctx.save();ctx.translate(hud.view.x,hud.view.y);ctx.scale(hud.view.k,hud.view.k);
      const bounds=visibleWorld(rect);
      drawBackdrop(rect);drawCore(rect);drawHulls(bounds);ctx.lineWidth=1;
      hud.edges.forEach(edge=>drawAxon(edge));
      hud.nodes.forEach(node=>{
        const selected=node.id===hud.selected,status=node.kind==='rule'?'rule':node.kind==='brain'?'brain':node.kind==='capability'?'capability':node.kind==='radar'?'radar':node.detail.status;
        const color=node.kind==='rule'?'#ffb703':node.kind==='brain'?'#a78bfa':node.kind==='capability'?'#38bdf8':node.kind==='radar'?'#f472b6':statusColor(status),radius=nodeRadius(node);
        ctx.save();ctx.shadowColor=color;ctx.shadowBlur=selected?22:(status==='done'||status==='running'?16:7);
        ctx.strokeStyle=color;ctx.fillStyle='#05070a';ctx.lineWidth=selected?2.5:1.5;
        if(node.kind==='rule'){ctx.beginPath();ctx.rect(node.x-radius,node.y-radius,radius*2,radius*2);ctx.fill();ctx.stroke();}
        else if(node.kind==='brain'){ctx.beginPath();for(let i=0;i<6;i++){const angle=-Math.PI/2+i*Math.PI/3,px=node.x+Math.cos(angle)*radius,py=node.y+Math.sin(angle)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);}ctx.closePath();ctx.fill();ctx.stroke();}
        else if(node.kind==='capability'){ctx.beginPath();ctx.moveTo(node.x,node.y-radius);ctx.lineTo(node.x+radius,node.y+radius);ctx.lineTo(node.x-radius,node.y+radius);ctx.closePath();ctx.fill();ctx.stroke();}
        else if(node.kind==='radar'){ctx.beginPath();for(let i=0;i<4;i++){const angle=Math.PI/4+i*Math.PI/2,px=node.x+Math.cos(angle)*radius,py=node.y+Math.sin(angle)*radius;i?ctx.lineTo(px,py):ctx.moveTo(px,py);}ctx.closePath();ctx.fill();ctx.stroke();}
        else{
          ctx.beginPath();ctx.arc(node.x,node.y,radius,0,Math.PI*2);ctx.fill();ctx.stroke();
          if(status==='failed'||status==='cancelled'){
            // A dormant synaptic trace: still part of the web, visibly inactive,
            // with an amber fringe rather than an alarm colour.
            ctx.setLineDash([3,5]);
            ctx.strokeStyle='rgba(255,183,3,.42)';
            ctx.beginPath(); ctx.arc(node.x,node.y,radius+5,.28,Math.PI*1.35); ctx.stroke();
            ctx.setLineDash([]);
          } else if(status==='done'){
            // Settled, still luminescent, still wired into the lattice.
            ctx.strokeStyle='rgba(0,255,157,.20)'; ctx.lineWidth=1;
            ctx.beginPath(); ctx.arc(node.x,node.y,radius+4,0,Math.PI*2); ctx.stroke();
          }
        }
        ctx.shadowBlur=0;
        if(node.tier==='room')drawRoomFrame(node,radius);
        if(status==='running'){drawReactorCore(node,radius);drawPhaseRing(node,radius);}
        const crowded=narrowCanvas()||hud.nodes.length>=26;
        const wantsPlate=selected||status==='running'||node.id===hud.hover||!crowded;
        if(wantsPlate){
          const gap=radius+(status==='running'?14:6),leftward=node.x<coreGeometry(rect).cx;
          drawNamePlate(node,leftward?node.x-gap:node.x+gap,node.y,bounds,leftward);
        }
        ctx.restore();
      });
      hud.nodes.forEach(node=>{
        const radius=nodeRadius(node)+10;
        hud.claimedLabels.push({
          left:node.x-radius, top:node.y-radius, width:radius*2, height:radius*2,
        });
      });
      hud.pendingHullLabels.forEach(item=>{
        const spot=placeClear(item.label,item.x,item.y,bounds,item.dx,item.dy);
        drawLabelPill(item.label,spot.x,spot.y,bounds);
      });
      drawMicroCard(bounds);
      ctx.restore();
    }
    // The floating card answers "what is this, and what is it doing right now"
    // without committing the operator to opening the inspector - the question a
    // hover or a tap is actually asking. Every line is recorded evidence; a
    // field with nothing behind it says so rather than rendering blank.
    function microCardLines(node) {
      if(node.kind==='rule')return [['RULE',node.detail.rule_id],['SCOPE',shortPath(node.detail.workspace_path||'')]];
      if(node.kind==='brain')return [['MEMORY',node.detail.id],['TRUST',node.detail.trust],['SOURCE',node.detail.source]];
      if(node.kind==='capability')return [['KIND',node.detail.kind],['STATE',readinessLabel(node.detail)],['HEALTH',node.detail.health_status||'unknown']];
      if(node.kind==='radar')return [['SOURCE',node.detail.source],['DECISION',node.detail.disposition],['PROBE',node.detail.evaluation?node.detail.evaluation.dynamic_status:'not run']];
      const telemetry=telemetryOf(node)||{phases:[],logs:[]};
      const active=(telemetry.phases||[]).find(phase=>phase.state==='active');
      const runtime=elapsed(telemetry.started_at,telemetry.ended_at);
      return [
        ['TASK',node.detail.id],
        ['WORKSPACE',node.detail.workspace_path?shortPath(node.detail.workspace_path):'not recorded'],
        ['BRANCH',telemetry.branch_name||telemetry.worktree_path||`${telemetry.workspace_kind||'dir'} workspace // no branch recorded`],
        ['PHASE',active?(PHASE_LABEL[active.key]||active.key):(telemetry.run_status||node.detail.status)],
        ['RUNTIME',runtime?(telemetry.ended_at?runtime:`firing for ${runtime}`):'not started'],
      ];
    }
    function drawMicroCard(bounds) {
      const node=hud.nodeById.get(hud.hover); if(!node)return;
      const title=truncate(node.kind==='task'?taskLabel(node.detail):node.kind==='rule'?node.detail.rule_id:node.kind==='brain'?node.detail.title:node.detail.name,44);
      const lines=microCardLines(node);
      ctx.save(); ctx.shadowBlur=0; ctx.textBaseline='middle';
      ctx.font='700 11px "JetBrains Mono", monospace';
      let width=ctx.measureText(title).width;
      ctx.font='9px "JetBrains Mono", monospace';
      lines.forEach(([label,value])=>{width=Math.max(width,ctx.measureText(`${label}  ${value}`).width);});
      width+=18; const height=26+lines.length*13;
      let left=node.x+nodeRadius(node)+10,top=node.y-height-12;
      if(bounds){
        if(left+width>bounds.x1-6)left=Math.max(bounds.x0+6,node.x-nodeRadius(node)-10-width);
        if(top<bounds.y0+6)top=Math.min(bounds.y1-6-height,node.y+nodeRadius(node)+12);
      }
      ctx.beginPath();
      if(ctx.roundRect)ctx.roundRect(left,top,width,height,5); else ctx.rect(left,top,width,height);
      ctx.fillStyle='rgba(3,6,10,.94)'; ctx.fill();
      ctx.strokeStyle='rgba(0,240,255,.45)'; ctx.lineWidth=1/hud.view.k; ctx.stroke();
      ctx.fillStyle='#eaffff'; ctx.font='700 11px "JetBrains Mono", monospace'; ctx.fillText(title,left+9,top+14);
      ctx.font='9px "JetBrains Mono", monospace';
      lines.forEach(([label,value],index)=>{
        const y=top+30+index*13;
        ctx.fillStyle='rgba(148,163,184,.9)'; ctx.fillText(label,left+9,y);
        ctx.fillStyle='#bfe9ff'; ctx.fillText(value,left+9+ctx.measureText(`${label}  `).width,y);
      });
      ctx.restore();
    }
    function pinchState() {
      const points=[...hud.pointers.values()];
      if(points.length<2)return null;
      const [first,second]=points;
      return {
        distance:Math.max(1,Math.hypot(first.x-second.x,first.y-second.y)),
        midX:(first.x+second.x)/2,
        midY:(first.y+second.y)/2,
      };
    }
    canvas.addEventListener('pointerdown',event=>{
      const node=hitNode(graphPoint(event)); hud.moved=false;
      hud.pointers.set(event.pointerId,screenPoint(event));
      if(hud.pointers.size>=2){
        // A second finger converts the gesture into a pinch: stop dragging or
        // panning so the two never fight over the same movement.
        hud.dragging=null; hud.panning=null; hud.pinch=pinchState(); hud.moved=true;
        canvas.setPointerCapture(event.pointerId);
        return;
      }
      if(node){hud.selected=node.id;hud.hover=node.id;hud.dragging=node.id;renderInspector();if(isPhone())openSheet(true);}
      else {hud.hover=null;const point=screenPoint(event);hud.panning={px:point.x,py:point.y,ox:hud.view.x,oy:hud.view.y};}
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',event=>{
      if(hud.pointers.has(event.pointerId))hud.pointers.set(event.pointerId,screenPoint(event));
      if(hud.pinch&&hud.pointers.size>=2){
        const next=pinchState(); if(!next)return;
        zoomAt(next.midX,next.midY,hud.view.k*(next.distance/hud.pinch.distance));
        // Panning with two fingers moves the lattice as well as scaling it.
        hud.view.x+=next.midX-hud.pinch.midX; hud.view.y+=next.midY-hud.pinch.midY;
        hud.pinch=next;
        event.preventDefault();
        return;
      }
      if(hud.dragging){const node=hud.nodeById.get(hud.dragging),point=graphPoint(event);node.x=point.x;node.y=point.y;node.vx=node.vy=0;hud.moved=true;return;}
      if(!hud.panning){
        const node=hitNode(graphPoint(event));
        const next=node?node.id:null;
        if(next!==hud.hover){hud.hover=next;canvas.style.cursor=next?'pointer':'crosshair';}
        return;
      }
      const point=screenPoint(event);
      if(Math.hypot(point.x-hud.panning.px,point.y-hud.panning.py)>4)hud.moved=true;
      hud.view.x=hud.panning.ox+(point.x-hud.panning.px); hud.view.y=hud.panning.oy+(point.y-hud.panning.py);
    });
    canvas.addEventListener('pointerleave',()=>{hud.hover=null;});
    canvas.addEventListener('pointerup',event=>{
      const now=Date.now(),point=screenPoint(event);
      hud.pointers.delete(event.pointerId);
      if(hud.pointers.size<2)hud.pinch=null;
      if(!hud.moved&&now-hud.lastTapAt<320)zoomAt(point.x,point.y,hud.view.k>1.4?1:2);
      hud.lastTapAt=now; hud.dragging=null; hud.panning=null; canvas.releasePointerCapture?.(event.pointerId);
    });
    canvas.addEventListener('pointercancel',event=>{
      hud.pointers.delete(event.pointerId);
      if(hud.pointers.size<2)hud.pinch=null;
      hud.dragging=null; hud.panning=null;
    });
    canvas.addEventListener('wheel',event=>{event.preventDefault();const point=screenPoint(event);zoomAt(point.x,point.y,hud.view.k*(event.deltaY<0?1.12:.89));},{passive:false});
    canvas.addEventListener('dblclick',event=>{const point=screenPoint(event);zoomAt(point.x,point.y,hud.view.k>1.4?1:2);});
    byId('sheetHandle').addEventListener('click',()=>openSheet(!byId('hudAside').classList.contains('open')));
    window.addEventListener('resize',drawGraph); requestAnimationFrame(advanceGraph);
    function render(data) {
      hud.data=data; setText(byId('total'),data.metrics.total_tasks);setText(byId('active'),data.metrics.active_runs);setText(byId('ledger'),data.metrics.ledger_entries);setText(byId('rules'),data.metrics.accepted_rules);setText(byId('capabilityCount'),data.capabilities?.total??0);setText(byId('radarCount'),data.radar?.candidate_count??0);
      const services=byId('services');clear(services);for(const [name,alive] of Object.entries(data.daemons.processes)){const item=make('div','',`service ${alive?'up':'down'}`);item.append(make('i','','dot'),make('span',`${name} ${alive?'up':'down'}`));services.append(item);} syncGraph(data);
      const events=byId('events');clear(events);const heading=make('div','','event');['Time','Task','Outcome','Verify','Duration'].forEach(label=>heading.append(make('span',label)));events.append(heading);data.ledger_events.forEach(event=>{const row=make('div','','event'),exit=event.verify_exit===0?'0':event.verify_exit===null?'-':String(event.verify_exit);row.append(timeCell(event.timestamp),make('span',event.task_id),make('span',event.outcome,`outcome-badge ${event.outcome==='passed'?'pass':event.outcome==='skipped'?'warn':'fail'}`),make('span',exit,event.verify_exit===0?'pass':event.verify_exit===null?'warn':'fail'),make('span',duration(event.seconds)));events.append(row);}); if(data.ledger_errors.length){const issue=make('div',data.ledger_errors.join(' | '),'event warn');issue.style.gridTemplateColumns='1fr';events.append(issue);}
      hud.lastSnapshotAt=Date.now()/1000;
      setText(byId('connection'),`Supervisor state: ${data.daemons.status} // snapshot ${timeAgo(hud.lastSnapshotAt)}`);
      window.dispatchEvent(new CustomEvent('tri-ai:snapshot',{detail:data}));
    }
    window.addEventListener('tri-ai:select',event=>{const id=event.detail&&event.detail.id;if(!id||!hud.nodeById.has(id))return;hud.selected=id;hud.hover=id;renderInspector();if(isPhone())openSheet(true);});
    function openPreview(url,label) {
      const shell=byId('preview'),frame=byId('previewFrame');
      setText(byId('previewTitle'),label||url);
      byId('previewOpen').href=url;
      // Rebuilt each time so closing genuinely stops whatever the artifact ran.
      frame.setAttribute('src',url);
      shell.hidden=false; shell.classList.add('open');
    }
    function closePreview() {
      const shell=byId('preview'),frame=byId('previewFrame');
      shell.classList.remove('open'); shell.hidden=true;
      frame.setAttribute('src','about:blank');
    }
    byId('previewClose').addEventListener('click',closePreview);
    byId('preview').addEventListener('click',event=>{ if(event.target===byId('preview'))closePreview(); });
    document.addEventListener('keydown',event=>{ if(event.key==='Escape')closePreview(); });
    // Artifact links render in the HUD; the anchor still works if scripting is
    // unavailable, and a modified click keeps its normal meaning.
    document.addEventListener('click',event=>{
      const link=event.target.closest&&event.target.closest('a.now-file');
      if(!link||event.metaKey||event.ctrlKey||event.shiftKey||event.button!==0)return;
      event.preventDefault();
      openPreview(link.getAttribute('href'),link.textContent.replace(/\s*\u2197$/,''));
    });
    // EventSource reconnects on its own, but on a fixed short interval - which
    // against a server that is down means a steady stream of failed requests
    // and no way for the operator to tell a live page from a frozen one. So
    // the stream is closed on error and reopened on a doubling delay, and the
    // badge states which of the two the page currently is.
    const streamState={source:null,attempt:0,timer:null};
    function setStreamBadge(live,detail) {
      const badge=byId('streamBadge'); if(!badge)return;
      badge.className=`stream-badge ${live?'live':'down'}`;
      setText(badge,live?'STREAM LIVE':detail||'RECONNECTING...');
    }
    function openStream() {
      if(streamState.timer){clearTimeout(streamState.timer);streamState.timer=null;}
      const source=new EventSource('/events'); streamState.source=source;
      source.addEventListener('snapshot',event=>{
        streamState.attempt=0; setStreamBadge(true);
        render(JSON.parse(event.data));
      });
      source.onopen=()=>{streamState.attempt=0;setStreamBadge(true);};
      source.onerror=()=>{
        source.close();
        if(streamState.source===source)streamState.source=null;
        streamState.attempt+=1;
        const delay=Math.min(30000,1000*Math.pow(2,streamState.attempt-1));
        setStreamBadge(false,`RECONNECTING IN ${Math.round(delay/1000)}S`);
        setText(byId('connection'),`Evidence stream lost - retry ${streamState.attempt} in ${Math.round(delay/1000)}s`);
        streamState.timer=setTimeout(openStream,delay);
      };
    }
    // Relative labels go stale on a page that is no longer receiving snapshots,
    // which is exactly when an operator most needs to know how old the view is.
    setInterval(()=>{ if(hud.lastSnapshotAt&&!streamState.source){ setText(byId('connection'),`Evidence stream lost - last snapshot ${timeAgo(hud.lastSnapshotAt)}`);} },5000);
    openStream();
  </script>
  <script type="module" src="/assets/tri-space.js"></script>
</body>
</html>"""

HTML = _HTML_TEMPLATE.replace("KAYA", PRODUCT_NAME)


def snapshot_payload(snapshot: kaya_terminal.DashboardSnapshot) -> dict[str, object]:
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
                "prompt": task.prompt,
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
        "brain": {
            "status": snapshot.brain.status,
            "item_count": snapshot.brain.item_count,
            "inbox_count": snapshot.brain.inbox_count,
            "edge_count": snapshot.brain.edge_count,
            "diagnostic": snapshot.brain.diagnostic,
            "items": [
                {
                    "id": item.item_id, "title": item.title, "kind": item.kind,
                    "source": item.source, "project": item.project,
                    "trust": item.trust, "created_at": item.created_at,
                }
                for item in snapshot.brain.items
            ],
            "edges": [
                {
                    "id": edge.edge_id, "source_id": edge.source_id,
                    "target_id": edge.target_id, "relation": edge.relation,
                    "evidence_item_id": edge.evidence_item_id,
                }
                for edge in snapshot.brain.edges
            ],
        },
        "capabilities": {
            "status": snapshot.capabilities.status,
            "total": snapshot.capabilities.total,
            "active": snapshot.capabilities.active,
            "archived": snapshot.capabilities.archived,
            "routable": snapshot.capabilities.routable,
            "gated": snapshot.capabilities.gated,
            "candidates": snapshot.capabilities.candidates,
            "diagnostic": snapshot.capabilities.diagnostic,
            "items": [
                {
                    "id": item.resource_id, "name": item.name, "kind": item.kind,
                    "availability": item.availability,
                    "adapter_status": item.adapter_status,
                    "health_status": item.health_status,
                    "risk_status": item.risk_status, "tags": list(item.tags),
                }
                for item in snapshot.capabilities.items
            ],
        },
        "radar": {
            "status": snapshot.radar.status,
            "generated_at": snapshot.radar.generated_at,
            "candidate_count": snapshot.radar.candidate_count,
            "evaluated_count": snapshot.radar.evaluated_count,
            "error_count": snapshot.radar.error_count,
            "diagnostic": snapshot.radar.diagnostic,
            "candidates": [
                {
                    "name": item.name, "source": item.source, "url": item.url,
                    "disposition": item.disposition, "stars": item.stars,
                    "signals": list(item.signals),
                }
                for item in snapshot.radar.candidates
            ],
            "evaluations": [
                {
                    "name": item.name, "disposition": item.disposition,
                    "static_verdict": item.static_verdict,
                    "dynamic_status": item.dynamic_status,
                }
                for item in snapshot.radar.evaluations
            ],
        },
        "daemons": {
            "status": snapshot.daemons.status,
            "processes": dict(snapshot.daemons.processes),
            "diagnostic": snapshot.daemons.diagnostic,
        },
    }


ARTIFACT_ROUTE = re.compile(r"^/artifact/([A-Za-z0-9_.-]{1,64})/(\d{1,4})$")
ARTIFACT_MAX_BYTES = 25 * 1024 * 1024
STATIC_ASSETS = {
    "/assets/tri-space.js": (Path(__file__).with_name("tri_space.js"), "text/javascript; charset=utf-8"),
    "/assets/three.module.min.js": (
        Path(__file__).with_name("vendor") / "three.module.min.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/three.core.min.js": (
        Path(__file__).with_name("vendor") / "three.core.min.js",
        "text/javascript; charset=utf-8",
    ),
}
ARTIFACT_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
}


def _handler(snapshot_fn: SnapshotReader, event_interval: float, artifact_fn=None) -> type[BaseHTTPRequestHandler]:
    class KayaHandler(BaseHTTPRequestHandler):
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

        def _serve_static(self, source: Path, content_type: str) -> None:
            try:
                body = source.read_bytes()
            except OSError:
                self.send_error(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _serve_artifact(self, task_id: str, index: int) -> None:
            """Serve one recorded artifact. The URL selects; it never supplies a path."""
            if artifact_fn is None:
                self.send_error(404, "not found")
                return
            artifacts = artifact_fn(task_id)
            if index >= len(artifacts):
                self.send_error(404, "not found")
                return
            artifact = artifacts[index]
            target = Path(artifact.absolute)
            try:
                if not target.is_file():
                    self.send_error(404, "not found")
                    return
                size = target.stat().st_size
                if size > ARTIFACT_MAX_BYTES:
                    self.send_error(413, "artifact too large to serve")
                    return
                body = target.read_bytes()
            except OSError:
                self.send_error(404, "not found")
                return
            content_type = ARTIFACT_TYPES.get(target.suffix.lower())
            self.send_response(200)
            if content_type is None:
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{target.name}"',
                )
            else:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

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
            if self.path in STATIC_ASSETS:
                self._serve_static(*STATIC_ASSETS[self.path])
                return
            match = ARTIFACT_ROUTE.match(self.path)
            if match is not None:
                self._serve_artifact(match.group(1), int(match.group(2)))
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

    return KayaHandler


LOOPBACK_HOST = "127.0.0.1"


def create_server(
    *,
    host: str = LOOPBACK_HOST,
    port: int = 8080,
    snapshot_fn: Optional[SnapshotReader] = None,
    event_interval: float = 2.0,
    allow_non_loopback: bool = False,
    artifact_fn=None,
) -> KayaHTTPServer:
    """Create the read-only dashboard server; loopback unless told otherwise.

    The dashboard has no control-plane endpoint and no credential access, but
    it does expose task titles, workspace paths, and live agent/verify log
    tails - so who can reach it is a real decision, not an implementation
    detail. It therefore stays on loopback by default and binds anything wider
    only when a caller passes ``allow_non_loopback`` explicitly. That flag is
    the operator saying "this network is one I trust" (a Tailscale interface,
    say); it is deliberately awkward enough that it cannot happen by accident
    or by a typo in a port argument.
    """
    if host != LOOPBACK_HOST and not allow_non_loopback:
        raise ValueError(
            "KAYA web server binds loopback 127.0.0.1 unless non-loopback "
            "binding is explicitly allowed (--host with --allow-non-loopback)"
        )
    if not str(host).strip():
        raise ValueError("host must not be empty")
    if event_interval <= 0:
        raise ValueError("event_interval must be positive")
    if snapshot_fn is None:
        runtime = kaya_terminal.DEFAULT_RUNTIME_ROOT
        snapshot_fn = lambda: kaya_terminal.read_snapshot(
            board_path=runtime / "board.db",
            ledger_path=runtime / "ledger.jsonl",
            daemon_state_path=runtime / "logs" / "daemons.json",
            runs_root=runtime / "runs",
            brain_path=runtime / "brain" / "brain.db",
            capability_catalog_path=runtime / "capabilities" / "catalog.json",
            radar_path=runtime / "radar" / "latest.json",
        )
        if artifact_fn is None:
            artifact_fn = lambda task_id: kaya_terminal.read_task_artifacts(
                runtime / "board.db", task_id,
            )
    return KayaHTTPServer(
        (host, int(port)), _handler(snapshot_fn, event_interval, artifact_fn),
    )


def serve(servers: Sequence[KayaHTTPServer]) -> None:
    """Serve every bound interface until interrupted.

    One socket cannot cover both loopback and a single named interface, and
    binding 0.0.0.0 to get both would also publish the dashboard on every
    other network this machine is attached to - the home Wi-Fi included. So
    each requested interface gets its own server and the set is served
    together, which keeps the reachable surface exactly the list the operator
    named.
    """
    if not servers:
        raise ValueError("no servers to serve")
    threads = [
        threading.Thread(target=server.serve_forever, name=f"kaya-{index}", daemon=True)
        for index, server in enumerate(servers[1:], start=1)
    ]
    for thread in threads:
        thread.start()
    try:
        servers[0].serve_forever()
    finally:
        for server in servers[1:]:
            server.shutdown()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local read-only KAYA dashboard.")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        metavar="ADDRESS",
        help=(
            "interface to bind; repeat for several (e.g. --host 127.0.0.1 "
            "--host 100.118.189.88). Defaults to 127.0.0.1. Anything but "
            "127.0.0.1 also needs --allow-non-loopback."
        ),
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help=(
            "bind non-loopback interfaces, e.g. a Tailscale address so the HUD is "
            "reachable from a phone. The dashboard stays read-only, but task titles, "
            "workspace paths and live log tails become reachable from those networks."
        ),
    )
    args = parser.parse_args(argv)
    hosts: list[str] = []
    for host in args.hosts or [LOOPBACK_HOST]:
        if host not in hosts:
            hosts.append(host)

    servers: list[KayaHTTPServer] = []
    try:
        for host in hosts:
            servers.append(
                create_server(
                    host=host, port=args.port, allow_non_loopback=args.allow_non_loopback,
                )
            )
            if host != LOOPBACK_HOST:
                print(
                    f"KAYA dashboard is reachable beyond this machine on {host}:"
                    f"{args.port} - read-only, but it exposes task titles, workspace "
                    "paths and live log tails to that network",
                    file=sys.stderr,
                )
            print(f"KAYA dashboard listening on http://{host}:{servers[-1].server_port}")
        serve(servers)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"kaya web stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        for server in servers:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
