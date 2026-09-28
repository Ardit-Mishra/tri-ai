"""Read-only web surface for the KAYA evidence snapshot.

Loopback by default; a wider binding exists for reaching the HUD from a phone
over a trusted network, and has to be asked for explicitly. There is no
mutating endpoint at any binding.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Sequence, Mapping

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard import kaya_terminal, private_index
else:
    from . import kaya_terminal, private_index


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


PRODUCT_NAME = "CORTEX"

_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>TRI-AI // KAYA</title>
  <style>
    :root { color-scheme:dark; --bg:#08100e; --surface:#101b18; --line:rgba(185,224,207,.18); --muted:#a7bbb3; --text:#f4f7f2; --signal:#58d2b0; --signal-strong:#a5f0d2; --mineral:#efb66e; --crimson:#ef767a; --violet:#a99ad6; --ink:#05100c; }
    * { box-sizing:border-box; }
    body { margin:0; min-width:320px; background:var(--bg); color:var(--text); font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
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
    .demo-badge { background:rgba(167,139,250,.12); border:1px solid rgba(167,139,250,.52); color:var(--violet); font-size:9px; letter-spacing:.06em; padding:4px 10px; text-transform:uppercase; white-space:nowrap; }
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
    .now-what { color:var(--text); font-family:ui-sans-serif,system-ui,"Segoe UI",sans-serif; font-size:19px; font-weight:600; line-height:1.35; margin:10px 0 0; overflow-wrap:anywhere; }
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
    /* Cortex Chamber replaces the generic HUD shell. The graph is the primary
       workspace; telemetry and inspection exist to explain it, not compete. */
    :root { --bg:#08100e; --surface:#101a16; --surface-strong:#0b1511; --line:rgba(180,218,200,.20); --text:#f3f6f0; --muted:#9fb2a9; --cyan:#63d9b6; --emerald:#8de0bf; --amber:#e9b86d; --crimson:#ed7d80; --violet:#aa9bd0; --ink:#05100c; }
    body { background:var(--bg); font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; letter-spacing:0; }
    body::before { border-left:1px solid rgba(180,218,200,.07); content:""; inset:0 auto 0 50%; pointer-events:none; position:fixed; z-index:-1; }
    body[data-theme="light"] { --bg:#edf1ec; --surface:#f7faf5; --surface-strong:#e7eee7; --line:rgba(21,53,40,.18); --text:#10291e; --muted:#587066; --cyan:#167a5e; --emerald:#25795f; --amber:#9b5d16; --crimson:#a94c54; --violet:#67578f; color-scheme:light; }
    body[data-theme="light"] #spatialGraph { background:#e7eee7; }
    .shell { max-width:1720px; padding:18px 28px 32px; }
    header { border-color:var(--line); min-height:58px; padding:0 0 14px; }
    .brand { align-items:baseline; color:var(--text); display:flex; font-size:17px; font-weight:780; gap:10px; letter-spacing:0; }
    .brand::before { background:var(--cyan); border-radius:50%; box-shadow:0 0 0 5px color-mix(in srgb,var(--cyan) 12%,transparent); content:""; display:inline-block; height:8px; width:8px; }
    .brand span { color:var(--muted); font-family:inherit; font-size:10px; font-weight:650; letter-spacing:.12em; margin:0; }
    .header-right { align-items:center; flex-direction:row; gap:9px; }
    .services { gap:5px; }
    .service { background:transparent; border:0; color:var(--muted); font-size:9px; padding:4px 0; }
    .service:not(:last-child)::after { color:var(--line); content:"/"; margin-left:5px; }
    .stream-badge,.demo-badge { border-radius:0; font-size:9px; padding:5px 8px; }
    .theme-toggle { background:transparent; border:1px solid var(--line); color:var(--text); cursor:pointer; font:650 10px/1 ui-sans-serif,system-ui,sans-serif; letter-spacing:.08em; min-height:30px; padding:0 10px; text-transform:uppercase; }
    .theme-toggle:hover { border-color:var(--cyan); color:var(--cyan); }
    .theme-toggle:focus-visible,.view-switch button:focus-visible { outline:2px solid var(--cyan); outline-offset:3px; }
    .cortex-intent { border-bottom:1px solid var(--line); display:grid; gap:22px; grid-template-columns:minmax(0,1fr) auto; padding:25px 0 22px; }
    .now { background:transparent; border:0; margin:0; min-height:0; padding:0; }
    .now-kicker { color:var(--cyan); font-size:10px; font-weight:750; letter-spacing:.14em; }
    .now-what { font-size:clamp(24px,2.45vw,40px); font-weight:720; letter-spacing:0; line-height:1.08; margin:10px 0 0; max-width:19ch; }
    .now-sub { font-size:13px; max-width:70ch; }
    .now-steps { margin-top:14px; }
    .now-step { border-color:var(--line); border-radius:0; color:var(--muted); font-size:9px; padding:5px 8px; }
    .now-step.active { background:color-mix(in srgb,var(--cyan) 10%,transparent); border-color:var(--cyan); color:var(--cyan); }
    .now-say { background:transparent; border-left:1px solid var(--cyan); color:var(--muted); max-width:70ch; }
    .cortex-readout { align-self:end; border-left:1px solid var(--line); display:grid; gap:12px; min-width:235px; padding:5px 0 5px 20px; }
    .readout-label { color:var(--muted); font-size:9px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
    .readout-value { color:var(--text); font-size:13px; font-weight:650; line-height:1.35; }
    .readout-value span { color:var(--cyan); }
    .metrics { background:transparent; border:0; border-bottom:1px solid var(--line); display:grid; gap:0; grid-template-columns:repeat(6,minmax(0,1fr)); margin:0; }
    .metric { background:transparent; border-right:1px solid var(--line); min-height:82px; padding:16px 14px 15px 0; }
    .metric:not(:first-child) { padding-left:14px; }
    .metric:last-child { border-right:0; }
    .metric label { color:var(--muted); font-size:9px; font-weight:700; letter-spacing:.1em; }
    .metric strong { color:var(--text); font-family:ui-sans-serif,system-ui,sans-serif; font-size:25px; font-variant-numeric:tabular-nums; font-weight:680; letter-spacing:0; margin-top:6px; }
    .hud-grid { gap:22px; grid-template-columns:minmax(0,1fr) minmax(300px,340px); margin-top:22px; }
    .hud-panel { background:transparent; border:0; box-shadow:none; padding:0; }
    .hud-panel::before,.hud-panel::after { display:none; }
    .graph-panel { background:var(--surface-strong); border:1px solid var(--line); min-height:620px; overflow:hidden; padding:18px; position:relative; }
    .graph-panel::before { background:var(--surface-strong); border:0; content:""; display:block; inset:0; opacity:.4; pointer-events:none; position:absolute; }
    .graph-panel > * { position:relative; }
    .panel-head { color:var(--muted); font-size:9px; letter-spacing:.12em; margin-bottom:8px; }
    .panel-head strong { color:var(--text); font-size:11px; letter-spacing:.08em; }
    .graph-head-tools { gap:12px; }
    .view-switch { border-color:var(--line); }
    .view-switch button { color:var(--muted); font-size:9px; }
    .view-switch button.active { background:var(--cyan); color:var(--ink); }
    #spatialGraph,#neuralGraph { height:490px; }
    .cortex-stage-label { background:rgba(5,16,13,.82); border-left:2px solid var(--cyan); left:30px; max-width:31ch; padding:10px 12px 11px; pointer-events:none; position:absolute; top:62px; z-index:2; }
    .cortex-stage-label span { color:var(--cyan); display:block; font-size:9px; font-weight:750; letter-spacing:.18em; text-transform:uppercase; }
    .cortex-stage-label strong { color:var(--text); display:block; font-size:clamp(20px,2vw,30px); font-weight:710; letter-spacing:0; margin-top:6px; }
    .cortex-stage-label em { color:var(--muted); display:block; font-size:11px; font-style:normal; line-height:1.45; margin-top:6px; max-width:29ch; }
    .graph-hint { border-top:1px solid var(--line); color:var(--muted); font-size:9px; margin-top:4px; padding-top:11px; }
    .graph-legend { display:none; }
    .model-lanes { border-top:1px solid var(--line); display:flex; gap:0; margin-top:10px; overflow:auto; }
    .model-lane { border-right:1px solid var(--line); flex:1 0 150px; min-height:62px; padding:10px 12px 8px 0; }
    .model-lane:not(:first-child) { padding-left:12px; }
    .model-lane:last-child { border-right:0; }
    .model-lane .lane-kind { color:var(--muted); display:block; font-size:8px; letter-spacing:.11em; text-transform:uppercase; }
    .model-lane strong { color:var(--text); display:block; font-size:11px; font-weight:650; margin-top:5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .model-lane b { color:var(--cyan); font-size:10px; font-weight:650; }
    .hud-aside { border-left:1px solid var(--line); gap:0; padding-left:20px; }
    .hud-aside .hud-panel { border-bottom:1px solid var(--line); padding:0 0 18px; }
    .hud-aside .hud-panel + .hud-panel { padding-top:18px; }
    .memory-rule { border-left:1px solid var(--amber); padding-left:10px; }
    .registry-item,.radar-item { background:transparent; border-left:1px solid var(--line); padding-left:10px; }
    .registry-item h3,.radar-item h3 { font-size:11px; }
    .section-title { border-top:1px solid var(--line); color:var(--muted); font-family:ui-sans-serif,system-ui,sans-serif; font-size:9px; font-weight:750; letter-spacing:.14em; margin:28px 0 0; padding-top:16px; }
    .evidence { background:transparent; border:0; min-height:0; }
    .terminal { font-family:ui-sans-serif,system-ui,sans-serif; font-size:11px; min-width:0; padding:0; }
    .event { grid-template-columns:125px minmax(170px,1fr) 105px 80px 95px; }
    .cortex-footer { color:var(--muted); font-size:10px; padding-top:16px; }
    .orchestration-stage { border-bottom:1px solid var(--line); padding:28px 0 24px; }
    .orchestration-head { align-items:end; display:flex; gap:30px; justify-content:space-between; margin-bottom:18px; }
    .orchestration-eyebrow { color:var(--cyan); font-size:9px; font-weight:750; letter-spacing:.16em; text-transform:uppercase; }
    .orchestration-head h1 { color:var(--text); font-size:clamp(29px,3.2vw,50px); font-weight:760; letter-spacing:0; line-height:1; margin:8px 0 0; max-width:15ch; }
    .orchestration-head p { color:var(--muted); font-size:13px; line-height:1.55; margin:0; max-width:48ch; }
    .orchestration-proof { border-left:1px solid var(--mineral); color:var(--muted); font-size:10px; line-height:1.5; max-width:27ch; padding-left:13px; }
    .orchestration-proof strong { color:var(--text); display:block; font-size:11px; margin-bottom:3px; }
    .orchestration-grid { border:1px solid var(--line); display:grid; grid-template-columns:220px minmax(470px,1fr) 230px; min-height:440px; }
    .agent-lens { border-right:1px solid var(--line); padding:18px; }
    .lens-kicker,.map-inspector label { color:var(--muted); display:block; font-size:9px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
    .agent-tabs { display:flex; flex-direction:column; gap:6px; margin-top:12px; }
    .agent-tab { background:transparent; border:1px solid var(--line); color:var(--muted); cursor:pointer; font:650 11px/1 ui-sans-serif,system-ui,sans-serif; min-height:38px; padding:0 10px; text-align:left; }
    .agent-tab:hover,.agent-tab[aria-selected="true"] { border-color:var(--cyan); color:var(--text); }
    .agent-tab[aria-selected="true"] { background:color-mix(in srgb,var(--cyan) 12%,transparent); }
    .agent-tab:focus-visible { outline:2px solid var(--cyan); outline-offset:3px; }
    .agent-context { border-top:1px solid var(--line); margin-top:18px; padding-top:15px; }
    .agent-context strong { color:var(--text); display:block; font-size:14px; line-height:1.2; }
    .agent-context p { color:var(--muted); font-size:11px; line-height:1.5; margin:8px 0 0; }
    .lens-rows { display:grid; gap:9px; margin-top:15px; }
    .lens-row { border-left:1px solid var(--line); padding-left:9px; }
    .lens-row span { color:var(--muted); display:block; font-size:8px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }
    .lens-row b { color:var(--text); display:block; font-size:10px; font-weight:600; line-height:1.4; margin-top:3px; }
    .world-map-wrap { min-width:0; overflow:hidden; padding:15px 10px 12px; position:relative; }
    .world-map { display:block; height:100%; min-height:410px; overflow:visible; width:100%; }
    .world-map .guide { fill:none; stroke:var(--line); stroke-width:1; }
    .world-map .route { fill:none; stroke:var(--cyan); stroke-dasharray:8 12; stroke-linecap:round; stroke-width:1.8; }
    .world-map .route.warm { stroke:var(--mineral); }
    .world-map .route.muted { stroke:rgba(180,218,200,.34); stroke-dasharray:3 9; }
    .world-map .route.signal { animation:route-flow 3.4s linear infinite; }
    .world-map .node { fill:var(--surface-strong); stroke:var(--line); stroke-width:1.25; }
    .world-map .node-world { stroke:var(--cyan); }
    .world-map .node-execute { stroke:var(--mineral); }
    .world-map .node-brain { fill:color-mix(in srgb,var(--cyan) 12%,var(--surface-strong)); stroke:var(--cyan); stroke-width:1.7; }
    .world-map .node-gate { fill:color-mix(in srgb,var(--mineral) 12%,var(--surface-strong)); stroke:var(--mineral); stroke-width:1.5; }
    .world-map .node-title { fill:var(--text); font-family:ui-sans-serif,system-ui,sans-serif; font-size:10px; font-weight:700; }
    .world-map .node-detail { fill:var(--muted); font-family:ui-monospace,"Cascadia Code",monospace; font-size:7px; letter-spacing:.08em; }
    .world-map .group-label { fill:var(--cyan); font-family:ui-monospace,"Cascadia Code",monospace; font-size:8px; font-weight:700; letter-spacing:.14em; }
    .world-map .pulse { animation:node-pulse 2.2s ease-in-out infinite; transform-box:fill-box; transform-origin:center; }
    @keyframes route-flow { to { stroke-dashoffset:-80; } }
    @keyframes node-pulse { 50% { opacity:.55; transform:scale(1.07); } }
    .map-inspector { border-left:1px solid var(--line); padding:18px; }
    .map-inspector h2 { color:var(--text); font-size:18px; font-weight:730; letter-spacing:0; line-height:1.14; margin:8px 0 0; }
    .map-inspector > p { color:var(--muted); font-size:11px; line-height:1.5; margin:9px 0 0; }
    .map-proof-list { border-top:1px solid var(--line); display:grid; gap:12px; margin-top:20px; padding-top:14px; }
    .map-proof-list div { border-left:1px solid var(--line); padding-left:10px; }
    .map-proof-list span { color:var(--muted); display:block; font-size:8px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }
    .map-proof-list b { color:var(--text); display:block; font-size:11px; line-height:1.38; margin-top:3px; }
    .orchestration-foot { display:flex; flex-wrap:wrap; gap:12px 26px; margin-top:13px; }
    .orchestration-foot span { color:var(--muted); font-size:10px; line-height:1.4; }
    .orchestration-foot b { color:var(--text); font-weight:650; }
    /* Cortex Theater is intentionally a new visual system: one large live
       topology with sparse, inspectable edges instead of dashboard tiles. */
    .cortex-theater { border-bottom:1px solid var(--line); display:grid; gap:0; grid-template-columns:190px minmax(0,1fr) 250px; grid-template-rows:minmax(660px,calc(100dvh - 112px)) auto; margin-top:18px; min-height:700px; }
    .theater-rail { border-top:1px solid var(--line); color:var(--muted); min-width:0; padding:22px 16px; position:relative; }
    .theater-sources { border-right:1px solid var(--line); }
    .theater-control { border-left:1px solid var(--line); }
    .rail-head { align-items:center; display:flex; font-size:9px; font-weight:720; justify-content:space-between; letter-spacing:.1em; text-transform:uppercase; }
    .rail-head b { color:var(--cyan); font-size:8px; font-weight:720; }
    .source-cluster { align-items:flex-start; display:flex; gap:9px; margin-top:15px; }
    .source-dot { background:var(--source-color,var(--cyan)); box-shadow:0 0 0 4px color-mix(in srgb,var(--source-color,var(--cyan)) 12%,transparent); flex:0 0 auto; height:7px; margin-top:5px; width:7px; }
    .source-cluster b { color:var(--text); display:block; font-size:11px; font-weight:680; line-height:1.2; }
    .source-cluster small { color:var(--muted); display:block; font-size:9px; line-height:1.35; margin-top:3px; }
    .rail-rule { border-top:1px solid var(--line); margin:24px 0 16px; }
    .rail-copy { color:var(--muted); font-size:10px; line-height:1.55; margin:11px 0 0; }
    .rail-copy b { color:var(--text); font-weight:680; }
    .cortex-core { background:#050b09; border:0; min-height:0; overflow:hidden; padding:0; position:relative; }
    .cortex-core::before { background:transparent; display:none; }
    .cortex-core > * { position:absolute; }
    .core-topline { color:var(--muted); display:flex; font-family:ui-monospace,"Cascadia Code",monospace; font-size:8px; font-weight:700; gap:18px; justify-content:center; left:0; letter-spacing:.11em; padding:16px 20px; right:0; text-transform:uppercase; top:0; z-index:4; }
    .core-topline span { align-items:center; display:flex; gap:6px; }
    .core-topline i { background:var(--cyan); border-radius:50%; box-shadow:0 0 12px var(--cyan); display:inline-block; height:5px; width:5px; }
    .theater-tabs { border:1px solid var(--line); display:flex; flex-direction:row; gap:0; left:50%; margin:0; padding:3px; position:absolute; top:40px; transform:translateX(-50%); width:auto; z-index:5; }
    .theater-tabs .agent-tab { border:0; font-size:10px; min-height:31px; padding:0 13px; text-align:center; white-space:nowrap; }
    .theater-tabs .agent-tab[aria-selected="true"] { background:#eef3ee; color:#102319; }
    .core-title { bottom:48px; display:grid; justify-items:center; left:0; pointer-events:none; right:0; text-align:center; z-index:3; }
    .core-title span { color:var(--cyan); font-family:ui-monospace,"Cascadia Code",monospace; font-size:9px; font-weight:720; letter-spacing:.18em; }
    .core-title strong { color:#f3f6f0; font-size:clamp(36px,3.4vw,54px); font-weight:760; letter-spacing:0; line-height:.95; margin-top:6px; text-transform:uppercase; }
    .core-title em { color:#b6c8be; font-size:10px; font-style:normal; line-height:1.4; margin-top:8px; max-width:44ch; }
    .cortex-core #spatialGraph,.cortex-core #neuralGraph { bottom:0; height:100%; left:0; right:0; top:0; width:100%; z-index:1; }
    .cortex-core .spatial-tooltip { z-index:8; }
    .core-bottom { align-items:center; bottom:14px; color:var(--muted); display:flex; font-family:ui-monospace,"Cascadia Code",monospace; font-size:8px; justify-content:space-between; left:18px; letter-spacing:.08em; right:18px; text-transform:uppercase; z-index:4; }
    .core-bottom .view-switch { border-color:var(--line); }
    .core-bottom .view-switch button { color:var(--muted); font-size:8px; }
    .core-bottom .view-switch button.active { background:var(--cyan); color:#07100d; }
    .theater-control h1 { color:var(--text); font-size:19px; font-weight:730; letter-spacing:0; line-height:1.08; margin:11px 0 0; }
    .theater-control > p { color:var(--muted); font-size:10px; line-height:1.55; margin:10px 0 0; }
    .theater-control .map-proof-list { gap:12px; margin-top:18px; }
    .theater-control .map-proof-list b { font-size:10px; }
    .theater-control .model-lanes { display:block; margin-top:12px; overflow:visible; }
    .theater-control .model-lane { border-bottom:1px solid var(--line); border-right:0; min-height:0; padding:10px 0; }
    .theater-control .model-lane:not(:first-child) { padding-left:0; }
    .source-open { border-bottom:1px solid var(--cyan); color:var(--cyan); display:inline-block; font-size:9px; margin-top:13px; padding-bottom:3px; text-decoration:none; text-transform:uppercase; }
    .source-open:hover { color:var(--text); }
    .theater-footer { border-top:1px solid var(--line); color:var(--muted); display:flex; flex-wrap:wrap; font-size:9px; gap:12px 28px; grid-column:1 / -1; line-height:1.45; padding:10px 16px 13px; }
    .theater-footer b { color:var(--text); font-weight:680; text-transform:uppercase; }
    .hud-grid { display:block; margin-top:28px; }
    .hud-aside { border-left:0; display:grid; gap:20px; grid-template-columns:1.3fr 1fr 1fr 1fr; padding-left:0; }
    .hud-aside .hud-panel { border-bottom:0; padding:0; }
    .hud-aside .hud-panel + .hud-panel { border-left:1px solid var(--line); padding:0 0 0 18px; }
    body[data-theme="light"] .cortex-core { background:#102119; }
    body[data-theme="light"] .theater-tabs .agent-tab[aria-selected="true"] { background:#163b2d; color:#f3f6f0; }
    body[data-theme="light"] .graph-panel { background:#102119; }
    body[data-theme="light"] .graph-panel .panel-head strong,body[data-theme="light"] .cortex-stage-label strong { color:#f3f6f0; }
    body[data-theme="light"] .cortex-stage-label { background:rgba(5,16,13,.86); }
    body[data-theme="light"] .cortex-stage-label em,body[data-theme="light"] .graph-hint,body[data-theme="light"] .model-lane .lane-kind { color:#c6d7cd; }
    body[data-theme="light"] .model-lane strong { color:#f3f6f0; }
    @media (max-width:1100px) { .cortex-theater { grid-template-columns:180px minmax(0,1fr); grid-template-rows:minmax(590px,calc(100vh - 112px)) auto auto; } .theater-control { border-left:0; border-top:1px solid var(--line); grid-column:1 / -1; } .theater-control .map-proof-list { grid-template-columns:repeat(3,minmax(0,1fr)); } .theater-control .model-lanes { display:flex; } .theater-control .model-lane { border-bottom:0; border-right:1px solid var(--line); padding:0 10px 0 0; } .theater-control .model-lane:not(:first-child) { padding-left:10px; } .hud-aside { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width:700px) { .shell { padding:12px; } header { align-items:flex-start; display:grid; gap:14px; grid-template-columns:1fr; } .header-right { align-items:center; display:grid; gap:8px; grid-template-columns:1fr auto; justify-content:stretch; width:100%; } .demo-badge { min-width:0; text-align:center; } .stream-badge { justify-content:center; } .theme-toggle { grid-column:1 / -1; min-height:42px; width:100%; } .services { display:none; } .cortex-theater { display:flex; flex-direction:column; margin-left:-12px; margin-right:-12px; } .cortex-core { min-height:440px; order:-1; } .theater-sources,.theater-control { border-left:0; border-right:0; padding:16px 14px; } .theater-sources { display:grid; gap:8px; grid-template-columns:repeat(2,minmax(0,1fr)); } .theater-sources .rail-head,.theater-sources .rail-rule,.theater-sources .rail-copy { grid-column:1 / -1; } .source-cluster { margin-top:0; } .core-topline { font-size:7px; gap:8px; padding:12px 8px; } .core-topline span:nth-child(2) { display:none; } .theater-tabs { top:36px; width:calc(100% - 32px); } .theater-tabs .agent-tab { font-size:9px; padding:0 7px; } .core-title { bottom:49px; } .core-title strong { font-size:42px; } .core-title em { font-size:9px; max-width:30ch; } .core-bottom { bottom:13px; font-size:7px; left:10px; right:10px; } .core-bottom > span:first-child { display:none; } .theater-control .map-proof-list { grid-template-columns:1fr; } .theater-control .model-lanes { display:block; } .theater-control .model-lane { border-bottom:1px solid var(--line); border-right:0; padding:10px 0; } .hud-aside { display:block; } .hud-aside .hud-panel + .hud-panel { border-left:0; border-top:1px solid var(--line); margin-top:18px; padding:18px 0 0; } .cortex-intent { grid-template-columns:1fr; } .cortex-readout { border-left:0; border-top:1px solid var(--line); min-width:0; padding:14px 0 0; } .metrics { grid-template-columns:repeat(3,minmax(0,1fr)); } .metric:nth-child(3) { border-right:0; } .metric:nth-child(n+4) { border-top:1px solid var(--line); } .metric { min-height:70px; } }

    /* Neural observatory: the file universe is the interface. Edge telemetry
       floats over the field and never boxes the organism into a dashboard. */
    body { background:#020504; overflow-x:hidden; }
    body::before { display:none; }
    .shell { max-width:none; padding:0 24px 28px; }
    header { background:linear-gradient(180deg,rgba(2,5,4,.98),rgba(2,5,4,.55) 72%,transparent); border:0; left:24px; min-height:72px; padding:18px 0 22px; position:absolute; right:24px; top:0; z-index:20; }
    .brand { font-family:ui-monospace,"Cascadia Code",monospace; font-size:18px; letter-spacing:.02em; }
    .brand span { font-size:8px; letter-spacing:.22em; }
    .cortex-clock { color:var(--text); font-family:ui-monospace,"Cascadia Code",monospace; font-size:18px; font-variant-numeric:tabular-nums; letter-spacing:.04em; }
    .cortex-theater { border:0; display:block; height:100dvh; margin:0; min-height:720px; overflow:hidden; position:relative; }
    .cortex-core { background:radial-gradient(circle at 50% 45%,rgba(25,92,72,.16),rgba(3,8,6,.36) 34%,#020504 72%); inset:0; min-height:100%; position:absolute; }
    .cortex-core::after { background:linear-gradient(90deg,rgba(2,5,4,.92),transparent 20%,transparent 80%,rgba(2,5,4,.92)); content:""; inset:0; pointer-events:none; position:absolute; z-index:2; }
    .theater-rail { background:linear-gradient(180deg,rgba(4,10,8,.62),rgba(4,10,8,.18)); border:0; bottom:72px; padding:16px 14px; position:absolute; top:104px; width:218px; z-index:7; }
    .theater-sources { left:0; }
    .theater-control { right:0; }
    .rail-head { border-bottom:1px solid rgba(126,226,191,.18); padding-bottom:9px; }
    .source-cluster { margin-top:13px; }
    .source-cluster b { font-family:ui-monospace,"Cascadia Code",monospace; font-size:10px; }
    .source-cluster small { font-size:8px; }
    .source-count { color:var(--cyan); font-family:ui-monospace,"Cascadia Code",monospace; font-size:10px; margin-left:auto; }
    .brain-search { border-top:1px solid rgba(126,226,191,.18); margin-top:20px; padding-top:14px; }
    .brain-search label { color:var(--muted); display:block; font-size:8px; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
    .brain-search input { background:rgba(1,4,3,.55); border:0; border-bottom:1px solid rgba(126,226,191,.28); color:var(--text); font:10px/1.2 ui-monospace,"Cascadia Code",monospace; margin-top:8px; outline:0; padding:8px 4px; width:100%; }
    .brain-search input:focus { border-color:var(--cyan); }
    .brain-search-results { display:grid; gap:2px; margin-top:7px; max-height:170px; overflow:auto; }
    .brain-search-results button { background:transparent; border:0; color:var(--muted); cursor:pointer; display:grid; font:9px/1.3 ui-monospace,"Cascadia Code",monospace; gap:2px; padding:5px 4px; text-align:left; }
    .brain-search-results button:hover,.brain-search-results button:focus-visible { background:rgba(99,217,182,.08); color:var(--text); outline:0; }
    .brain-search-results small { color:var(--cyan); font-size:7px; letter-spacing:.08em; text-transform:uppercase; }
    .core-topline { padding-top:91px; }
    .theater-tabs { background:rgba(3,8,6,.72); border-color:rgba(126,226,191,.2); top:24px; }
    .theater-tabs .agent-tab { min-width:92px; }
    .core-title { bottom:60px; }
    .core-title span { opacity:.78; }
    .core-title strong { font-family:ui-monospace,"Cascadia Code",monospace; font-size:clamp(48px,5vw,76px); font-weight:400; letter-spacing:.12em; text-shadow:0 0 34px rgba(99,217,182,.24); }
    .core-title em { font-family:ui-monospace,"Cascadia Code",monospace; font-size:9px; letter-spacing:.08em; max-width:52ch; text-transform:uppercase; }
    .core-bottom { bottom:18px; left:238px; right:238px; }
    .theater-footer { background:rgba(2,5,4,.72); border-color:rgba(126,226,191,.14); bottom:0; left:0; padding:9px 14px 11px; position:absolute; right:0; z-index:8; }
    .theater-control h1 { font-size:17px; }
    .theater-control > p { font-size:9px; }
    .theater-control .model-lanes { border-color:rgba(126,226,191,.16); }
    .theater-control .model-lane { border-color:rgba(126,226,191,.13); }
    .hud-grid { margin-top:24px; }
    body[data-theme="light"] { background:#e9eee9; }
    body[data-theme="light"] header { background:linear-gradient(180deg,rgba(237,241,236,.98),rgba(237,241,236,.64) 72%,transparent); }
    body[data-theme="light"] .cortex-core { background:radial-gradient(circle at 50% 45%,rgba(38,137,103,.26),rgba(223,235,226,.58) 40%,#dce5dd 78%); }
    body[data-theme="light"] .cortex-core::after { background:linear-gradient(90deg,rgba(225,234,226,.86),transparent 22%,transparent 78%,rgba(225,234,226,.86)); }
    body[data-theme="light"] .theater-rail { background:linear-gradient(180deg,rgba(239,245,239,.72),rgba(239,245,239,.28)); }
    body[data-theme="light"] .theater-footer { background:rgba(232,239,232,.82); }
    @media (max-width:1100px) {
      .theater-rail { width:190px; }
      .core-bottom { left:204px; right:204px; }
    }
    /* A phone can request its browser's "desktop site" while still having a
       portrait, coarse-pointer viewport. Keep the observatory in its touch
       layout in that case; otherwise the central canvas becomes a flex item
       squeezed between both rails. */
    @media (max-width:760px), (pointer:coarse) and (orientation:portrait) {
      .shell { padding:0 12px 64px; }
      header { display:grid; left:12px; padding-top:14px; position:relative; right:auto; }
      .cortex-clock,.services { display:none; }
      .cortex-theater { align-items:stretch; display:flex; flex-direction:column; height:auto; min-height:0; overflow:visible; }
      .cortex-core { flex:0 0 590px; height:590px; min-height:590px; min-width:0; order:1; position:relative !important; width:100%; z-index:0; }
      .cortex-core::after { background:linear-gradient(180deg,rgba(2,5,4,.74),transparent 16%,transparent 86%,rgba(2,5,4,.7)); }
      .theater-rail { background:transparent; bottom:auto; position:relative !important; top:auto; width:auto; z-index:1; }
      .theater-sources { order:2; }
      .theater-control { order:3; }
      .theater-sources,.theater-control { left:auto; right:auto; }
      .core-topline { padding-top:76px; }
      .theater-tabs { top:20px; width:auto; }
      .theater-tabs .agent-tab { min-width:0; }
      .core-title { bottom:62px; }
      .core-title strong { font-size:42px; }
      .core-bottom { left:10px; right:10px; }
      .theater-footer { bottom:auto; order:4; position:relative; }
    }
    </style>
</head>
<body data-theme="dark">
  <main class="shell">
    <header>
      <div class="brand">TRI-AI // KAYA <span>READ ONLY</span></div>
      <div class="header-right">
        <!-- Outside #services on purpose: render() clears that container on
             every snapshot, and the stream badge must survive a re-render. -->
        <span class="demo-badge" id="demoBadge" hidden>Demonstration data</span>
        <span class="stream-badge down" id="streamBadge" role="status" aria-atomic="true">Evidence stream connecting</span>
        <div class="services" id="services" aria-label="Daemon service state"></div>
        <time class="cortex-clock" id="cortexClock"></time>
        <button class="theme-toggle" id="themeToggle" type="button" aria-pressed="false">Light interface</button>
      </div>
    </header>
    <section class="cortex-theater" aria-label="Tri-AI private operator console">
      <aside class="theater-rail theater-sources" aria-label="Authorized source clusters">
        <div class="rail-head"><span>System vitals</span><b>private by default</b></div>
        <div id="sourceIndex" aria-live="polite"></div>
        <div class="brain-search"><label for="fileSearch">Find a node</label><input id="fileSearch" type="search" autocomplete="off" placeholder="file or folder name"><div class="brain-search-results" id="fileSearchResults"></div></div>
        <div class="rail-rule"></div>
        <div class="rail-head"><span>Ingress</span><b>bounded</b></div>
        <p class="rail-copy">Telegram and voice requests become a scoped brief before any model receives context.</p>
      </aside>
      <section class="cortex-core graph-panel">
        <div class="core-topline"><span><i></i> cortex online</span><span id="brainIndexState">authorized index awaiting data</span><span>motion enabled</span></div>
        <div class="agent-tabs theater-tabs" role="tablist" aria-label="Choose an execution lane">
          <button class="agent-tab" id="lensClaude" type="button" role="tab" aria-selected="true" aria-controls="agentContext" data-lane="claude">Claude Code</button>
          <button class="agent-tab" id="lensCodex" type="button" role="tab" aria-selected="false" aria-controls="agentContext" data-lane="codex">Codex</button>
          <button class="agent-tab" id="lensLocal" type="button" role="tab" aria-selected="false" aria-controls="agentContext" data-lane="local">Local + free</button>
        </div>
        <div class="core-title"><span>TRI-AI PRIVATE NEURAL FIELD</span><strong id="activeLaneLabel">CLAUDE</strong><em id="brainIndexSummary">Every authorized file becomes a provenance-linked node.</em></div>
        <div id="spatialGraph" role="img" aria-label="Interactive three-dimensional Tri-AI system topology"><div class="spatial-tooltip" id="spatialTooltip"></div></div>
        <canvas id="neuralGraph" role="img" aria-label="Interactive task, memory, capability, and technology-radar graph"></canvas>
        <div class="core-bottom"><span id="graphSummary">Awaiting evidence</span><span class="view-switch" aria-label="Topology view"><button id="graph3d" type="button" disabled>3D</button><button id="graph2d" type="button" class="active">2D</button><button id="motionToggle" type="button" aria-pressed="false">Pause</button></span><span>drag to inspect a node</span></div>
      </section>
      <aside class="theater-rail theater-control" aria-label="Execution lane details">
        <div class="rail-head"><span id="mapPhase">Claude lane</span><b>active</b></div>
        <h1 id="mapAgentTitle">Research and planning</h1>
        <p id="mapAgentSummary">Claude receives an approved brief, source boundaries, and the project constraints.</p>
        <div class="map-proof-list" id="mapProofList"></div>
        <div class="rail-rule"></div>
        <div class="rail-head"><span>Observed routes</span><b>recorded</b></div>
        <div class="model-lanes" id="modelLanes" aria-label="Observed model lanes"></div>
        <p class="rail-copy"><b>Free routing:</b> Ollama is local. OmniRoute and FreeLLMAPI are policy-gated free routes that must retain requested and resolved model evidence.</p>
      </aside>
      <footer class="theater-footer"><span id="privacyBoundary"><b>sealed demonstration</b> no private source, credential, chat, artifact, or workspace is loaded here</span><span><b>human gate</b> publishing, deployment, DNS, and paid actions require approval</span></footer>
    </section>
    <section class="cortex-intent" aria-label="Current operating intent">
      <section class="now" id="now" aria-live="polite">
        <div class="now-kicker"><i class="now-idle" id="nowPip"></i><span id="nowKicker">Checking…</span></div>
        <p class="now-what" id="nowWhat">—</p>
        <div class="now-sub" id="nowSub"></div>
        <div class="now-steps" id="nowSteps"></div>
        <div class="now-say" id="nowSay" hidden></div>
        <div class="now-files" id="nowFiles"></div>
      </section>
      <aside class="cortex-readout" aria-label="Cortex state">
        <div><span class="readout-label">Private intelligence</span><div class="readout-value">Evidence-led <span>only</span></div></div>
        <div><span class="readout-label">Execution posture</span><div class="readout-value">Verify before release</div></div>
      </aside>
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
    <div class="cortex-footer" id="connection">Connecting to local evidence stream...</div>
  </main>
  <script>
    const byId = id => document.getElementById(id);
    const themeToggle=byId('themeToggle');
    function setTheme(theme) {
      document.body.dataset.theme=theme;
      const light=theme==='light';
      themeToggle.setAttribute('aria-pressed',String(light));
      themeToggle.textContent=light?'Dark interface':'Light interface';
      try { localStorage.setItem('tri-ai-theme',theme); } catch (_) { /* storage is optional */ }
    }
    try { setTheme(localStorage.getItem('tri-ai-theme')||'dark'); } catch (_) { setTheme('dark'); }
    themeToggle.addEventListener('click',()=>setTheme(document.body.dataset.theme==='light'?'dark':'light'));
    const clear = node => { while (node.firstChild) node.removeChild(node.firstChild); };
    const make = (tag, text, cls) => { const node=document.createElement(tag); node.textContent=text; if(cls) node.className=cls; return node; };
    const AGENT_LENSES={
      claude:{label:'Claude lane',title:'Research and planning',summary:'Claude receives an approved brief, source boundaries, and the project constraints. It returns a plan that can be challenged, cited research, and explicit acceptance checks.',received:'Approved brief + source pack',tools:'Research, files, documentation',returns:'Plan, citations, acceptance checks'},
      codex:{label:'Codex lane',title:'Implementation and verification',summary:'Codex receives a build contract, repository context, and review notes. It returns an inspectable diff, test evidence, and a release packet rather than an unverified claim.',received:'Build contract + repository scope',tools:'Code, browser checks, test harness',returns:'Diff, tests, rollback-ready packet'},
      local:{label:'Local and free-route lane',title:'Local and free-route execution',summary:'Ollama handles eligible local work with no per-token charge. OmniRoute and FreeLLMAPI are separate policy-gated free routes, each required to retain its requested model, resolved model, provider, and usage evidence.',received:'Bounded subtask + approved context',tools:'Ollama, OmniRoute, FreeLLMAPI, route policy',returns:'Resolved model, usage record, transcript, verifier input'},
    };
    let activeAgentLens='claude';
    function renderAgentLens(lane) {
      const detail=AGENT_LENSES[lane]||AGENT_LENSES.claude; activeAgentLens=lane;
      document.querySelectorAll('.agent-tab').forEach(button=>{
        const selected=button.dataset.lane===lane;
        button.setAttribute('aria-selected',String(selected));
      });
      setText(byId('activeLaneLabel'),lane.toUpperCase());
      setText(byId('mapPhase'),detail.label);
      setText(byId('mapAgentTitle'),detail.title);
      setText(byId('mapAgentSummary'),detail.summary);
      const proof=byId('mapProofList'); clear(proof);
      [['Receives',detail.received],['Tool permissions',detail.tools],['Evidence returned',detail.returns]].forEach(([label,value])=>{
        const row=make('div',''); row.append(make('span',label),make('b',value)); proof.append(row);
      });
      const graph=byId('spatialGraph');
      if(graph) graph.dataset.activeLane=lane;
      window.dispatchEvent(new CustomEvent('tri-ai:lane',{detail:{lane}}));
    }
    document.querySelectorAll('.agent-tab').forEach(button=>button.addEventListener('click',()=>renderAgentLens(button.dataset.lane)));
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
    function updateClock(){const node=byId('cortexClock');if(node)node.textContent=new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});}
    updateClock(); setInterval(updateClock,1000);
    function renderSystemInspection(node) {
      const detail=node.detail||{};
      setText(byId('mapPhase'),detail.group||'System node');
      setText(byId('mapAgentTitle'),node.label||'Tri-AI system node');
      setText(byId('mapAgentSummary'),detail.description||'No recorded system detail.');
      const proof=byId('mapProofList'); clear(proof);
      [['Receives',detail.context||'Not recorded'],['Tool permissions',detail.tools||'Not recorded'],['Evidence returned',detail.evidence||'Not recorded']].forEach(([label,value])=>{
        const row=make('div',''); row.append(make('span',label),make('b',value)); proof.append(row);
      });
      if(detail.modifiedAt){const row=make('div','');row.append(make('span','Modified'),make('b',new Date(detail.modifiedAt).toLocaleString()));proof.append(row);}
      if(detail.url){const link=make('a','Open source','source-open');link.href=detail.url;link.target='_blank';link.rel='noopener';proof.append(link);}
    }
    window.addEventListener('tri-ai:system-select',event=>renderSystemInspection(event.detail));
    renderAgentLens(activeAgentLens);
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
      renderInspector(); renderMemory(); renderCapabilities(data); renderRadar(data); renderModelLanes(data);
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
    function renderModelLanes(data) {
      const root=byId('modelLanes'); clear(root);
      const observed=new Map();
      data.tasks.forEach(task=>{
        const telemetry=task.telemetry||{};
        if(!telemetry.model)return;
        const key=`${telemetry.provider||'unspecified'}::${telemetry.model}`;
        const lane=observed.get(key)||{model:telemetry.model,provider:telemetry.provider||'provider not recorded',count:0,running:0};
        lane.count+=1; if(task.status==='running')lane.running+=1; observed.set(key,lane);
      });
      const observedLanes=[...observed.values()].slice(0,4);
      if(!observedLanes.length){
        const lane=make('div','','model-lane');
        lane.append(make('span','Observed model lane','lane-kind'),make('strong','No routed run recorded'));
        root.append(lane); return;
      }
      observedLanes.forEach(lane=>{
        const card=make('div','','model-lane');
        card.append(make('span',lane.provider,'lane-kind'),make('strong',lane.model),make('b',lane.running?`${lane.running} active`: `${lane.count} retained run${lane.count===1?'':'s'}`));
        root.append(card);
      });
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
    const demoMode=new URLSearchParams(window.location.search).get('demo')==='1';
    // Kept in sync with tri_space.js: the source rail is a readable legend
    // for the same stable regions rendered in the private neural field.
    const SOURCE_REGION_CSS=Object.freeze({drive:'#42d7c7',desktop:'#5ba7ff',laptop:'#b493ff',phone:'#ff8c78',omniroute:'#f7c85c',freellmapi:'#f7c85c',ollama:'#72d989',obsidian:'#a78bfa',github:'#cbd5e1',deploys:'#71c99a',sessions:'#9ca3af',default:'#7f9690'});
    function sourceColorFor(sourceId){const source=String(sourceId||'').toLowerCase();if(source.startsWith('drive'))return SOURCE_REGION_CSS.drive;if(source.startsWith('desktop'))return SOURCE_REGION_CSS.desktop;if(source.startsWith('laptop'))return SOURCE_REGION_CSS.laptop;if(source.startsWith('phone'))return SOURCE_REGION_CSS.phone;if(source.includes('omniroute'))return SOURCE_REGION_CSS.omniroute;if(source.includes('freellmapi'))return SOURCE_REGION_CSS.freellmapi;if(source.includes('ollama'))return SOURCE_REGION_CSS.ollama;if(source.startsWith('obsidian'))return SOURCE_REGION_CSS.obsidian;if(source.startsWith('github'))return SOURCE_REGION_CSS.github;if(source.startsWith('deploy'))return SOURCE_REGION_CSS.deploys;if(source.startsWith('session'))return SOURCE_REGION_CSS.sessions;return SOURCE_REGION_CSS.default;}
    function renderSourceIndex(fileGraph) {
      const root=byId('sourceIndex'); if(!root)return; clear(root);
      const descriptions={'laptop-desktop':'this machine',laptop:'remote machine',drive:'Google Drive metadata',github:'repositories',obsidian:'notes and graph',sessions:'authorized exports',deploys:'deployment records',ollama:'local model inventory',omniroute:'route evidence pending',freellmapi:'route evidence pending'};
      const sources=Array.isArray(fileGraph?.sources)?fileGraph.sources:[];
      if(!sources.length){root.append(make('p',fileGraph?.diagnostic||'No private source index attached.','rail-copy'));return;}
      sources.forEach(source=>{
        const row=make('div','','source-cluster');
        const detail=make('div','');
        const sourceDetail=source.id==='desktop'?(source.node_count>0?'Desktop metadata':'awaiting private index'):(descriptions[source.id]||source.status||'authorized metadata');
        detail.append(make('b',source.label||source.id),make('small',sourceDetail));
        const dot=make('span','','source-dot');dot.style.setProperty('--source-color',sourceColorFor(source.id));dot.setAttribute('aria-hidden','true');
        row.append(dot,detail,make('span',String(source.node_count??0),'source-count'));
        root.append(row);
      });
    }
    let searchableFiles=[];
    function updateFileSearch() {
      const root=byId('fileSearchResults'),query=byId('fileSearch').value.trim().toLocaleLowerCase(); clear(root);
      if(query.length<2)return;
      const matches=[];
      for(const item of searchableFiles){
        if(String(item.label||'').toLocaleLowerCase().includes(query)){matches.push(item);if(matches.length===8)break;}
      }
      matches.forEach(item=>{
        const button=make('button','');button.type='button';
        button.append(make('span',item.label||item.id),make('small',`${item.source} / ${item.kind}`));
        button.addEventListener('click',()=>window.dispatchEvent(new CustomEvent('tri-ai:file-focus',{detail:{id:item.id}})));
        root.append(button);
      });
      if(!matches.length)root.append(make('span','No matching indexed node.','rail-copy'));
    }
    byId('fileSearch').addEventListener('input',updateFileSearch);
    function renderFileSearch(fileGraph){searchableFiles=Array.isArray(fileGraph?.items)?fileGraph.items:[];if(byId('fileSearch').value)updateFileSearch();}
    function render(data) {
      hud.data=data; setText(byId('total'),data.metrics.total_tasks);setText(byId('active'),data.metrics.active_runs);setText(byId('ledger'),data.metrics.ledger_entries);setText(byId('rules'),data.metrics.accepted_rules);setText(byId('capabilityCount'),data.capabilities?.total??0);setText(byId('radarCount'),data.radar?.candidate_count??0);
      const demoBadge=byId('demoBadge'); demoBadge.hidden=!data.demo; if(data.demo)setText(demoBadge,'Demonstration data // no live work');
      renderSourceIndex(data.file_graph);
      renderFileSearch(data.file_graph);
      const boundary=byId('privacyBoundary'); clear(boundary);
      boundary.append(make('b',data.demo?'sealed demonstration':'private local view'),document.createTextNode(data.demo?' no private source, credential, chat, artifact, or workspace is loaded here':' metadata stays on this machine; file contents are not copied into the index'));
      const services=byId('services');clear(services);for(const [name,alive] of Object.entries(data.daemons.processes)){const item=make('div','',`service ${alive?'up':'down'}`);item.append(make('i','','dot'),make('span',`${name} ${alive?'up':'down'}`));services.append(item);} syncGraph(data);
      const events=byId('events');clear(events);const heading=make('div','','event');['Time','Task','Outcome','Verify','Duration'].forEach(label=>heading.append(make('span',label)));events.append(heading);data.ledger_events.forEach(event=>{const row=make('div','','event'),exit=event.verify_exit===0?'0':event.verify_exit===null?'-':String(event.verify_exit);row.append(timeCell(event.timestamp),make('span',event.task_id),make('span',event.outcome,`outcome-badge ${event.outcome==='passed'?'pass':event.outcome==='skipped'?'warn':'fail'}`),make('span',exit,event.verify_exit===0?'pass':event.verify_exit===null?'warn':'fail'),make('span',duration(event.seconds)));events.append(row);}); if(data.ledger_errors.length){const issue=make('div',data.ledger_errors.join(' | '),'event warn');issue.style.gridTemplateColumns='1fr';events.append(issue);}
      hud.lastSnapshotAt=Date.now()/1000;
      setText(byId('connection'),`Supervisor state: ${data.daemons.status} // snapshot ${timeAgo(hud.lastSnapshotAt)}`);
      // The spatial module is loaded after this inline controller. Keep the
      // latest snapshot as well as emitting it: an extremely fast local SSE
      // response must not leave the Cortex with its placeholder index state.
      window.__triAiSnapshot=data;
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
    let privateFileGraph=null;
    let privateFileGraphRequest=null;
    async function hydrateFileGraph(data) {
      const summary=data.file_graph||{};
      const sceneUrl=summary.scene_url||summary.index_url;
      if(data.demo||!sceneUrl)return data;
      if(privateFileGraph&&privateFileGraph.revision===summary.revision){data.file_graph=privateFileGraph;return data;}
      if(!privateFileGraphRequest){
        privateFileGraphRequest=fetch(sceneUrl,{cache:'no-store'})
          .then(response=>{if(!response.ok)throw new Error(`private index ${response.status}`);return response.json();})
          .then(graph=>{privateFileGraph=graph;return graph;})
          .finally(()=>{privateFileGraphRequest=null;});
      }
      try { data.file_graph=await privateFileGraphRequest; }
      catch (_) { data.file_graph=summary; }
      return data;
    }
    function setStreamBadge(live,detail) {
      const badge=byId('streamBadge'); if(!badge)return;
      badge.className=`stream-badge ${live?'live':'down'}`;
      setText(badge,live?'STREAM LIVE':detail||'RECONNECTING...');
    }
    function openStream() {
      if(streamState.timer){clearTimeout(streamState.timer);streamState.timer=null;}
      const source=new EventSource(demoMode?'/events?demo=1':'/events'); streamState.source=source;
      source.addEventListener('snapshot',async event=>{
        streamState.attempt=0; setStreamBadge(true);
        render(await hydrateFileGraph(JSON.parse(event.data)));
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


_PRIVATE_SOURCE_ROOT = Path(
    os.environ.get("TRI_AI_PRIVATE_SOURCE_DIR", Path.home() / ".tri-ai" / "private-sources")
)
_SOURCE_REGISTRY_FILENAME = "sources.json"
_private_graph_cache: tuple[tuple[tuple[str, int, int], ...], dict[str, object]] | None = None


def _load_private_file_graph() -> dict[str, object]:
    """Load metadata-only indexes for the loopback operator surface.

    Index files are generated out of band and contain no file contents. Their
    filesystem signature becomes the browser revision, so the heavyweight
    graph is fetched once and refreshed only when an index actually changes.
    """
    global _private_graph_cache
    registry_path = _PRIVATE_SOURCE_ROOT / _SOURCE_REGISTRY_FILENAME
    paths = sorted(
        path for path in _PRIVATE_SOURCE_ROOT.glob("*.json")
        if path.name != _SOURCE_REGISTRY_FILENAME and not path.name.endswith(".summary.json")
    )
    signature_parts: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            metadata = path.stat()
        except OSError:
            continue
        signature_parts.append((path.name, metadata.st_mtime_ns, metadata.st_size))
        summary_path = private_index.private_index_summary_path(path)
        try:
            summary_metadata = summary_path.stat()
        except OSError:
            continue
        signature_parts.append((summary_path.name, summary_metadata.st_mtime_ns, summary_metadata.st_size))
    try:
        registry_metadata = registry_path.stat()
    except OSError:
        registry_metadata = None
    if registry_metadata is not None:
        signature_parts.append((registry_path.name, registry_metadata.st_mtime_ns, registry_metadata.st_size))
    signature = tuple(signature_parts)
    if _private_graph_cache is not None and _private_graph_cache[0] == signature:
        return _private_graph_cache[1]

    declared_source_ids, registry_diagnostic = _load_declared_source_ids(registry_path)
    sources: list[dict[str, object]] = []
    items: list[dict[str, object]] = []
    folder_counts: dict[str, int] = {}
    diagnostics: list[str] = []
    statuses: list[str] = []
    source_ids: set[str] = set()
    item_ids: set[str] = set()
    indexed_item_count = 0
    for path in paths:
        try:
            summary = private_index.load_private_index_summary(path)
            graph: dict[str, object] = {
                "status": summary["status"],
                "diagnostic": "Path-free local index summary loaded.",
                "sources": [
                    {"id": source["id"], "node_count": source["node_count"], "authorized": True}
                    for source in summary["sources"]
                ],
                "items": [],
                "folder_counts": summary["folder_counts"],
            }
        except (OSError, ValueError, json.JSONDecodeError) as error:
            try:
                graph = private_index.load_private_index(path)
                diagnostics.append(f"{path.name}: full index fallback ({type(error).__name__})")
            except (OSError, ValueError, json.JSONDecodeError) as fallback_error:
                diagnostics.append(f"{path.name}: {type(fallback_error).__name__}")
                continue
        statuses.append(str(graph["status"]))
        diagnostics.append(str(graph["diagnostic"]))
        for source in graph["sources"]:
            source_id = str(source["id"])
            if source_id in source_ids:
                diagnostics.append(f"{path.name}: duplicate source {source_id}")
                continue
            source_ids.add(source_id)
            node_count = source.get("node_count")
            if type(node_count) is not int:
                diagnostics.append(f"{path.name}: invalid source count")
                continue
            sources.append({
                "id": source_id, "node_count": node_count,
                "authorized": source.get("authorized") is True,
            })
            indexed_item_count += node_count
            region_id = _source_region_id(source_id)
            summary_folder_count = graph.get("folder_counts", {}).get(source_id, 0)
            if type(summary_folder_count) is int:
                folder_counts[region_id] = folder_counts.get(region_id, 0) + summary_folder_count
        for item in graph["items"]:
            item_id = str(item["id"])
            if item_id in item_ids or str(item["source"]) not in source_ids:
                continue
            item_ids.add(item_id)
            # The browser needs hierarchy and inspectable metadata, never a
            # local absolute path or file body.
            items.append(item)

    indexed_region_ids = {_source_region_id(source_id) for source_id in source_ids}
    sources = _merge_declared_sources(sources, declared_source_ids)
    missing_sources = [
        source_id for source_id in declared_source_ids
        if _source_region_id(source_id) not in indexed_region_ids
    ]
    if registry_diagnostic:
        diagnostics.append(registry_diagnostic)
    if missing_sources:
        diagnostics.append(f"Awaiting metadata index from {len(missing_sources)} authorized source(s).")

    if not indexed_item_count:
        status = "unavailable"
    elif missing_sources or (diagnostics and ("partial" in statuses or len(statuses) != len(paths))):
        status = "partial"
    else:
        status = "indexed"
    revision = hashlib.sha256(repr(signature).encode("utf-8")).hexdigest()[:16]
    result: dict[str, object] = {
        "status": status,
        "synthetic": False,
        "item_count": indexed_item_count,
        "sources": sources,
        "items": items,
        "folder_counts": folder_counts,
        "diagnostic": " ".join(diagnostics) if diagnostics else "No private index is attached.",
        "revision": revision,
    }
    _private_graph_cache = (signature, result)
    return result


_SAFE_SOURCE_LABELS = {
    "desktop": "Desktop",
    "drive": "Google Drive",
    "google-drive": "Google Drive",
    "github": "GitHub",
    "laptop": "Laptop",
    "laptop-desktop": "Laptop Desktop",
    "laptop-documents": "Laptop Documents",
    "laptop-downloads": "Laptop Downloads",
    "laptop-music": "Laptop Music",
    "laptop-pictures": "Laptop Pictures",
    "laptop-videos": "Laptop Videos",
    "obsidian": "Obsidian",
    "phone": "Phone",
    "sessions": "Claude and Codex",
    "claude-codex": "Claude and Codex",
    "vercel-render": "Vercel and Render",
    "ollama": "Local Ollama",
    "omniroute": "OmniRoute",
    "freellmapi": "FreeLLMAPI",
}


def _safe_source_label(source_id: str) -> str:
    """Return a generic source label rather than operator-supplied metadata."""
    return _SAFE_SOURCE_LABELS.get(source_id, "Authorized source")


def _source_region_id(source_id: str) -> str:
    """Fold technical root identifiers into the operator's source regions."""
    if source_id == "google-drive":
        return "drive"
    for device in ("desktop", "laptop"):
        if source_id.startswith(f"{device}-"):
            return device
    if source_id.startswith("ollama-"):
        return "ollama"
    return source_id


def _load_declared_source_ids(path: Path) -> tuple[list[str], str | None]:
    """Load the optional private source registry without accepting labels/paths.

    The registry only declares source identifiers. It deliberately has no root
    paths, account labels, or credentials, so a source can appear in Cortex as
    pending before its device-side metadata index arrives.
    """
    if not path.exists():
        return [], None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return [], "Private source registry could not be read."
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        return [], "Private source registry has an invalid format."

    source_ids: list[str] = []
    for entry in payload["sources"]:
        source_id = entry if isinstance(entry, str) else entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source_id):
            return [], "Private source registry has an invalid source identifier."
        if source_id not in source_ids:
            source_ids.append(source_id)
    return source_ids, None


def _merge_declared_sources(
    indexed_sources: list[dict[str, object]], declared_source_ids: list[str],
) -> list[dict[str, object]]:
    """Preserve indexed counts while making authorized pending sources visible."""
    merged: list[dict[str, object]] = []
    positions: dict[str, int] = {}
    for source in indexed_sources:
        source_id = source.get("id")
        node_count = source.get("node_count")
        if not isinstance(source_id, str) or type(node_count) is not int:
            continue
        region_id = _source_region_id(source_id)
        existing_position = positions.get(region_id)
        if existing_position is None:
            positions[region_id] = len(merged)
            merged.append({
                "id": region_id,
                "label": _safe_source_label(region_id),
                "node_count": node_count,
                "authorized": source.get("authorized") is True,
            })
        else:
            existing = merged[existing_position]
            existing["node_count"] = int(existing["node_count"]) + node_count
            existing["authorized"] = bool(existing["authorized"]) or source.get("authorized") is True
    for source_id in declared_source_ids:
        region_id = _source_region_id(source_id)
        if region_id not in positions:
            positions[region_id] = len(merged)
            merged.append({
                "id": region_id,
                "label": _safe_source_label(region_id),
                "node_count": 0,
                "authorized": True,
            })
    return merged


def _safe_scene_sources(graph: dict[str, object]) -> list[dict[str, object]]:
    """Expose source aliases only; configured labels may contain device names."""
    safe_sources: list[dict[str, object]] = []
    for source in graph.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = source.get("id")
        node_count = source.get("node_count")
        if not isinstance(source_id, str) or type(node_count) is not int:
            continue
        safe_sources.append({
            "id": _source_region_id(source_id),
            "label": _safe_source_label(_source_region_id(source_id)),
            "node_count": node_count,
            "authorized": source.get("authorized") is True,
        })
    return _merge_declared_sources(safe_sources, [])


def _private_file_graph_summary(graph: dict[str, object]) -> dict[str, object]:
    return {
        "status": graph["status"],
        "synthetic": False,
        "item_count": graph["item_count"],
        "sources": _safe_scene_sources(graph),
        "items": [],
        "diagnostic": graph["diagnostic"],
        "revision": graph.get("revision"),
        "scene_url": "/api/file-graph/scene",
    }


def _private_file_graph_scene(graph: dict[str, object]) -> dict[str, object]:
    """Return a bounded visual contract without shipping private file metadata.

    The dense Cortex field is generated procedurally in the browser from exact
    per-source counts. Names, paths, IDs, and parent relationships stay in
    the local index until a future authenticated source-expansion action asks
    for one bounded branch. This avoids both metadata overexposure and a
    multi-hundred-megabyte page load.
    """
    sources = _safe_scene_sources(graph)
    items = graph.get("items", [])
    folder_counts = graph.get("folder_counts")
    if not isinstance(folder_counts, dict):
        folder_counts = {}
        for item in items:
            if isinstance(item, dict) and item.get("kind") == "folder":
                source = item.get("source")
                if isinstance(source, str):
                    region_id = _source_region_id(source)
                    folder_counts[region_id] = folder_counts.get(region_id, 0) + 1

    clusters: list[dict[str, object]] = []
    ambient_sources: list[dict[str, object]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = source.get("id")
        label = source.get("label")
        node_count = source.get("node_count")
        if not isinstance(source_id, str) or not isinstance(label, str) or type(node_count) is not int:
            continue
        folder_count = folder_counts.get(source_id, 0)
        state = "indexed" if node_count else "pending"
        clusters.append({
            "id": f"cluster:{source_id}", "source": source_id, "label": label,
            "node_count": node_count, "folder_count": folder_count, "state": state,
        })
        ambient_sources.append({
            "source": source_id, "node_count": node_count, "folder_count": folder_count,
            "state": state,
        })

    return {
        "status": graph.get("status"),
        "synthetic": False,
        "item_count": graph.get("item_count"),
        "sources": sources,
        "clusters": clusters,
        "ambient": {
            "mode": "procedural-source-density",
            "node_count": graph.get("item_count"),
            "sources": ambient_sources,
        },
        "diagnostic": graph.get("diagnostic"),
        "revision": graph.get("revision"),
    }


def _demo_file_graph() -> dict[str, object]:
    """Build a dense, explicitly synthetic file universe for the public UI.

    The shape proves the one-node-per-item rendering contract without reading
    a local machine, cloud account, repository, or conversation export.
    Labels are generic on purpose; no user path is copied into the demo.
    """
    source_specs = (
        ("desktop", "Desktop", 72),
        ("laptop", "Laptop", 64),
        ("drive", "Google Drive", 86),
        ("github", "GitHub", 118),
        ("obsidian", "Obsidian", 44),
        ("sessions", "Claude and Codex", 36),
        ("deploys", "Vercel and Render", 20),
    )
    extensions = ("md", "py", "ts", "tsx", "json", "csv", "pdf", "yaml")
    items: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    for source_id, label, total in source_specs:
        root_id = f"demo:{source_id}:root"
        folder_count = min(6, max(2, total // 16))
        sources.append({"id": source_id, "label": label, "node_count": total, "authorized": True})
        items.append({
            "id": root_id, "label": label, "kind": "folder", "source": source_id,
            "parent_id": None, "synthetic": True,
        })
        folders = []
        for folder_index in range(folder_count):
            folder_id = f"demo:{source_id}:folder:{folder_index + 1}"
            folders.append(folder_id)
            items.append({
                "id": folder_id, "label": f"collection-{folder_index + 1:02d}",
                "kind": "folder", "source": source_id, "parent_id": root_id,
                "synthetic": True,
            })
        file_total = total - folder_count - 1
        for file_index in range(file_total):
            items.append({
                "id": f"demo:{source_id}:file:{file_index + 1}",
                "label": f"artifact-{file_index + 1:03d}.{extensions[file_index % len(extensions)]}",
                "kind": "file", "source": source_id,
                "parent_id": folders[file_index % len(folders)], "synthetic": True,
            })
    return {
        "status": "synthetic-demonstration", "synthetic": True,
        "item_count": len(items), "sources": sources, "items": items,
        "diagnostic": "Synthetic nodes only. No local or cloud source was read.",
    }


def snapshot_payload(
    snapshot: kaya_terminal.DashboardSnapshot, *, demo: bool = False,
    private_file_graph: dict[str, object] | None = None,
) -> dict[str, object]:
    """Serialize only evidence already present in a terminal snapshot."""
    return {
        "demo": demo,
        "file_graph": _demo_file_graph() if demo else private_file_graph or {
            "status": "not-indexed", "synthetic": False, "item_count": 0,
            "sources": [], "items": [],
            "diagnostic": "No authorized file index is attached to this snapshot.",
        },
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


def demo_snapshot() -> kaya_terminal.DashboardSnapshot:
    """Return a labeled local scenario for inspecting the UI without a live run.

    It never reads the runtime database, starts a worker, or represents a
    completed customer task. The scenario is only an inspectable explanation
    of the system's intended task, memory, capability, and release flows.
    """
    now = int(time.time())
    workspace = "C:/Tri-AI/demo/workspace"
    task = kaya_terminal.TaskView
    telemetry = kaya_terminal.TaskTelemetry
    phase = kaya_terminal.PhaseView
    return kaya_terminal.DashboardSnapshot(
        tasks=(
            task("demo_brief", "Frame the product brief", "done", 101, workspace, telemetry(
                run_id=101, run_status="done", run_outcome="passed", started_at=now - 190,
                ended_at=now - 155, model="local/planner", provider="ollama",
                summary="Requirements, constraints, and a release rubric were retained.",
                phases=(
                    phase("claimed", "done", "A bounded brief was accepted."),
                    phase("worktree_prep", "done", "Isolated demo workspace prepared."),
                    phase("agent_active", "done", "Planner produced a build contract."),
                    phase("verify_gate", "done", "Scope and evidence checks passed."),
                ),
            ), "Turn a customer problem into an evidence-backed build plan."),
            task("demo_research", "Research the operating context", "done", 102, workspace, telemetry(
                run_id=102, run_status="done", run_outcome="passed", started_at=now - 150,
                ended_at=now - 72, model="local/researcher", provider="ollama",
                summary="Sources and design constraints were recorded for downstream work.",
                phases=(
                    phase("claimed", "done", "Research role claimed."),
                    phase("worktree_prep", "done", "Source ledger opened."),
                    phase("agent_active", "done", "Evidence synthesis completed."),
                    phase("verify_gate", "done", "Citations inspected before handoff."),
                ),
            ), "Find the constraints, sources, and user expectations that shape the build."),
            task("demo_build", "Build and verify the release candidate", "running", 103, workspace, telemetry(
                run_id=103, run_status="running", started_at=now - 68, heartbeat_at=now - 2,
                worker_pid=103, claim_lock="demo-worker:103", workspace_kind="worktree",
                branch_name="demo/release-candidate", model="local/coder", provider="ollama",
                step_key="verify_gate", summary="Implementation is being checked against the release rubric.",
                phases=(
                    phase("claimed", "done", "Worker role bound to the task."),
                    phase("worktree_prep", "done", "Worktree and tool budget established."),
                    phase("agent_active", "done", "Implementation evidence retained."),
                    phase("verify_gate", "active", "Tests, review, and visual checks are running."),
                ),
                logs=(kaya_terminal.RunLogView(
                    "agent", "C:/Tri-AI/demo/runs/103/agent.log",
                    ("Verifier is comparing the candidate against the written acceptance checks.",), False,
                ),),
            ), "Produce a testable, reviewable release candidate rather than an unverified draft."),
            task("demo_release", "Prepare the human release decision", "ready", None, workspace,
                 prompt="Package diff, evidence, rollback notes, and the approval request."),
        ),
        edges=(
            kaya_terminal.TaskEdge("demo_brief", "demo_research"),
            kaya_terminal.TaskEdge("demo_research", "demo_build"),
            kaya_terminal.TaskEdge("demo_build", "demo_release"),
        ),
        ledger_events=(
            kaya_terminal.LedgerEvent(now - 72, "demo_research", "passed", 0, 78.0),
            kaya_terminal.LedgerEvent(now - 155, "demo_brief", "passed", 0, 35.0),
        ),
        ledger_entry_count=2,
        ledger_errors=(),
        activated_rule_count=2,
        daemons=kaya_terminal.DaemonHealth(
            "demonstration", (("planner", True), ("research", True), ("verifier", True)),
            "Demonstration scenario only. No live task or daemon state is represented.",
        ),
        rules=(
            kaya_terminal.RuleView("demo:verify-before-release", "verify-before-release", workspace, "release",
                ("tests_pass", "review_present", "rollback_note_present"),
                (kaya_terminal.RuleCitationView("demo://release-rubric", 1, "demo-release-rubric"),)),
            kaya_terminal.RuleView("demo:source-evidence", "retain-source-evidence", workspace, "research",
                ("citation_recorded", "claim_scoped"),
                (kaya_terminal.RuleCitationView("demo://research-contract", 1, "demo-research-contract"),)),
        ),
        brain=kaya_terminal.BrainView(
            status="demonstration", item_count=5, inbox_count=0, edge_count=4,
            items=(
                kaya_terminal.BrainItemView("m_goal", "Outcome: verified release candidate", "goal", "operator brief", "tri-ai", "reviewed", now - 210),
                kaya_terminal.BrainItemView("m_scope", "Constraint: public change requires approval", "constraint", "safety policy", "tri-ai", "reviewed", now - 205),
                kaya_terminal.BrainItemView("m_research", "Research ledger: cited constraints retained", "evidence", "research worker", "tri-ai", "reviewed", now - 90),
                kaya_terminal.BrainItemView("m_build", "Build contract: acceptance checks are explicit", "plan", "planner worker", "tri-ai", "reviewed", now - 145),
                kaya_terminal.BrainItemView("m_review", "Release packet: diff and rollback ready", "handoff", "verifier worker", "tri-ai", "unreviewed", now - 5),
            ),
            edges=(
                kaya_terminal.BrainEdgeView("e_goal_scope", "m_goal", "m_scope", "bounded_by", "m_scope"),
                kaya_terminal.BrainEdgeView("e_scope_research", "m_scope", "m_research", "guides", "m_research"),
                kaya_terminal.BrainEdgeView("e_research_build", "m_research", "m_build", "informs", "m_build"),
                kaya_terminal.BrainEdgeView("e_build_review", "m_build", "m_review", "verified_by", "m_review"),
            ),
        ),
        capabilities=kaya_terminal.CapabilityView(
            status="demonstration", total=6, active=4, archived=0, routable=4, gated=2, candidates=0,
            items=(
                kaya_terminal.CapabilityItemView("demo:research", "Research worker", "agent", "available", "routable", "demo-ready", "reviewed", ("web", "sources")),
                kaya_terminal.CapabilityItemView("demo:builder", "Build worker", "agent", "available", "routable", "demo-ready", "reviewed", ("code", "ui")),
                kaya_terminal.CapabilityItemView("demo:verifier", "Verification worker", "agent", "available", "gated", "demo-ready", "reviewed", ("tests", "review")),
                kaya_terminal.CapabilityItemView("demo:release", "Release gate", "policy", "approval-required", "gated", "human-gate", "reviewed", ("deploy", "rollback")),
            ),
        ),
        radar=kaya_terminal.RadarView(
            status="demonstration", generated_at="demo", candidate_count=2, evaluated_count=2, error_count=0,
            candidates=(
                kaya_terminal.RadarCandidateView("Visual regression harness", "capability radar", "demo://visual-regression", "evaluate", 0, ("ui", "verification")),
                kaya_terminal.RadarCandidateView("Source provenance adapter", "capability radar", "demo://provenance", "gated", 0, ("research", "evidence")),
            ),
            evaluations=(
                kaya_terminal.RadarEvaluationView("Visual regression harness", "evaluate", "demonstration", "not-run"),
                kaya_terminal.RadarEvaluationView("Source provenance adapter", "gated", "demonstration", "not-run"),
            ),
        ),
    )


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


def _handler(
    snapshot_fn: SnapshotReader, event_interval: float, artifact_fn=None, *,
    force_demo: bool = False, private_graph_fn=_load_private_file_graph,
    session_token: Optional[str] = None, trust_loopback: bool = True,
) -> type[BaseHTTPRequestHandler]:
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

        def _snapshot(self, *, demo: bool = False) -> dict[str, object]:
            graph = None if demo else _private_file_graph_summary(private_graph_fn())
            return snapshot_payload(
                demo_snapshot() if demo else snapshot_fn(), demo=demo,
                private_file_graph=graph,
            )

        def _private_allowed(self) -> bool:
            """Ask the boundary, once, per request."""
            return private_access_allowed(
                client_address=self.client_address,
                configured_token=session_token,
                header_token=self.headers.get("X-Kaya-Token"),
                cookie_header=self.headers.get("Cookie"),
                trust_loopback=trust_loopback,
            )

        def _demo_request(self) -> bool:
            return force_demo or self.path.endswith("?demo=1")

        def _serve_static(self, source: Path, content_type: str) -> None:
            try:
                body = source.read_bytes()
            except OSError:
                self.send_error(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The private observatory is often opened through a Tailnet URL
            # on a phone. Do not leave an old renderer alive for an hour
            # after a local visual repair.
            self.send_header("Cache-Control", "no-store")
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
            if self.path in {"/", "/?demo=1"}:
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path in {"/api/snapshot", "/api/snapshot?demo=1"}:
                self._json(seal_payload(
                    self._snapshot(demo=self._demo_request()),
                    private_allowed=self._private_allowed(),
                ))
                return
            if self.path == "/api/file-graph/scene":
                if not self._private_allowed():
                    self.send_error(404, "not found")
                    return
                if force_demo:
                    self.send_error(404, "not found")
                    return
                self._json(_private_file_graph_scene(private_graph_fn()))
                return
            if self.path in STATIC_ASSETS:
                self._serve_static(*STATIC_ASSETS[self.path])
                return
            match = ARTIFACT_ROUTE.match(self.path)
            if match is not None:
                # The hosted demonstration is intentionally a sealed scenario.
                # Do not make its artifact-shaped URLs a back door to an
                # accidentally supplied runtime reader.
                if force_demo or not self._private_allowed():
                    self.send_error(404, "not found")
                    return
                self._serve_artifact(match.group(1), int(match.group(2)))
                return
            if self.path in {"/events", "/events?demo=1"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                try:
                    while True:
                        data = json.dumps(
                            seal_payload(
                                self._snapshot(demo=self._demo_request()),
                                private_allowed=self._private_allowed(),
                            ),
                            separators=(",", ":"),
                        )
                        self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        time.sleep(event_interval)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            self.send_error(404, "not found")

    return KayaHandler


LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
SESSION_COOKIE = "kaya_session"


def private_access_allowed(
    *,
    client_address,
    configured_token,
    header_token=None,
    cookie_header=None,
    trust_loopback: bool = True,
) -> bool:
    """Whether this caller may see private material.

    Two rules, chosen to match the real risk rather than to add ceremony.

    **Loopback is the operator.** Someone already on this machine can read
    the indexed files directly, so a password there protects nothing and
    would break every existing local workflow.

    **Everything else must present the session token.** That is the Tailnet
    case, and it is the one currently open: network privacy is not
    authorization, and one shared or compromised device on the tailnet
    currently sees 796,684 file and folder names with no login.

    Fails closed. With no token configured, a non-loopback caller is refused
    outright - an unconfigured deployment must never be an open one - and a
    blank token authorizes nobody, so an empty environment variable cannot
    become a password of empty string.
    """
    host = str((client_address or ("",))[0] or "")
    # `trust_loopback` exists so a test can stand in for a remote caller
    # while still binding 127.0.0.1, which is the only address a test can
    # bind. Production leaves it True: someone already on this machine can
    # read the indexed files directly.
    if trust_loopback and (host in LOOPBACK_HOSTS
                           or host.startswith("::ffff:127.")):
        return True

    secret = str(configured_token or "").strip()
    if not secret:
        return False

    presented = str(header_token or "").strip()
    if not presented:
        presented = _cookie_value(cookie_header, SESSION_COOKIE)
    if not presented:
        return False
    return hmac.compare_digest(presented, secret)


def _cookie_value(cookie_header, name: str) -> str:
    for part in str(cookie_header or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() == name:
            return value.strip()
    return ""


def seal_payload(payload, *, private_allowed: bool):
    """Return the payload a caller is entitled to, never a partial private one.

    A refused caller gets an explicitly sealed view rather than a smaller
    leak: no counts, no source labels, no tasks, no brain. Failing to
    authenticate has to mean *nothing*, not *less*.

    `sealed` is set so the interface can say it is locked. A locked view that
    looks merely empty invites someone to conclude the system is broken, or
    worse, that there was nothing there to protect.
    """
    if private_allowed:
        return payload

    metrics = payload.get("metrics") if isinstance(payload, Mapping) else None
    return {
        "demo": bool(payload.get("demo")) if isinstance(payload, Mapping) else False,
        "sealed": True,
        "file_graph": {
            "status": "sealed", "synthetic": False,
            "item_count": 0, "sources": [], "items": [],
        },
        "tasks": [],
        "edges": [],
        "rules": [],
        "rule_task_links": [],
        "ledger_events": [],
        "ledger_errors": [],
        "memory_errors": [],
        "brain": {"status": "sealed", "item_count": 0, "inbox_count": 0,
                  "edge_count": 0, "diagnostic": "authentication required"},
        "capabilities": {"status": "sealed", "total": 0, "active": 0,
                         "archived": 0, "routable": 0},
        "radar": {"status": "sealed", "candidate_count": 0,
                  "evaluated_count": 0, "error_count": 0},
        "daemons": {"status": "sealed", "processes": [],
                    "diagnostic": "authentication required"},
        "metrics": {key: 0 for key in (metrics or {})} if metrics else {},
    }



def create_server(
    *,
    host: str = LOOPBACK_HOST,
    port: int = 8080,
    snapshot_fn: Optional[SnapshotReader] = None,
    event_interval: float = 2.0,
    allow_non_loopback: bool = False,
    artifact_fn=None,
    private_graph_fn=_load_private_file_graph,
    demo: bool = False,
    session_token: Optional[str] = None,
    trust_loopback: bool = True,
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
        if demo:
            # A public demo has no reason to construct runtime readers. This
            # keeps the production host detached even before requests arrive.
            snapshot_fn = demo_snapshot
        else:
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
        (host, int(port)), _handler(
            snapshot_fn, event_interval, artifact_fn,
            force_demo=demo, private_graph_fn=private_graph_fn,
            session_token=session_token, trust_loopback=trust_loopback,
        ),
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
        "--demo", action="store_true",
        help="serve only the labeled demonstration scenario; never read local runtime state",
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="hosts",
        metavar="ADDRESS",
        help=(
            "interface to bind; repeat for several (e.g. --host 127.0.0.1 "
            "--host <trusted-address>). Defaults to 127.0.0.1. Anything but "
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
                    demo=args.demo,
                )
            )
            if host != LOOPBACK_HOST and args.demo:
                print(
                    f"KAYA demonstration dashboard listening on http://{host}:{servers[-1].server_port} "
                    "with prebuilt data only",
                    file=sys.stderr,
                )
            elif host != LOOPBACK_HOST:
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
