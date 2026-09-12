"""Endpoint and isolation proofs for the local JARVIS web surface."""

from __future__ import annotations

import ast
import json
import sys
import threading
import unittest
from pathlib import Path
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import jarvis_terminal as terminal  # noqa: E402
from dashboard import jarvis_web as web  # noqa: E402


def fixture_snapshot() -> terminal.DashboardSnapshot:
    workspace = str(Path(__file__).resolve().parents[1])
    return terminal.DashboardSnapshot(
        tasks=(
            terminal.TaskView("t_ready", "Prepare evidence", "ready", None, workspace),
            terminal.TaskView(
                "t_running", "Verify dashboard", "running", 9, workspace,
                terminal.TaskTelemetry(
                    run_id=9, run_status="running", started_at=1000, worker_pid=4242,
                    claim_lock="host:4242", workspace_kind="dir", model="auto/best-free",
                    provider="custom", step_key="verify",
                    phases=(
                        terminal.PhaseView("claimed", "done", "board recorded a claimed event"),
                        terminal.PhaseView("worktree_prep", "skipped", "dir workspace has no worktree stage"),
                        terminal.PhaseView("agent_active", "done", "board recorded a spawned worker child"),
                        terminal.PhaseView("verify_gate", "active", "verify log has retained output"),
                    ),
                    logs=(terminal.RunLogView("verify", "C:/runs/t_running/9/verify.log", ("verify: running",), False, None),),
                ),
            ),
            terminal.TaskView("t_done", "Completed task", "done", 8, workspace),
        ),
        edges=(terminal.TaskEdge("t_ready", "t_running"),),
        ledger_events=(terminal.LedgerEvent(10.0, "t_running", "failed", 7, 1.2),),
        ledger_entry_count=12,
        ledger_errors=(),
        activated_rule_count=1,
        daemons=terminal.DaemonHealth(
            "running", (("supervisor", True), ("worker", True), ("telegram", False)), None,
        ),
        rules=(terminal.RuleView(
            "evolution:fixture", "fixture-clean-workspace", workspace, "code",
            ("confirm_workspace_clean",),
            (terminal.RuleCitationView("C:/evidence/ledger.jsonl", 7, "a" * 64),),
        ),),
    )


class WebSerializationTests(unittest.TestCase):
    def test_snapshot_json_preserves_authoritative_fields(self):
        payload = web.snapshot_payload(fixture_snapshot())
        self.assertEqual(payload["metrics"], {
            "total_tasks": 3, "active_runs": 1, "ledger_entries": 12, "accepted_rules": 1,
        })
        self.assertEqual(payload["tasks"][1]["id"], "t_running")
        self.assertEqual(payload["edges"], [{"parent_id": "t_ready", "child_id": "t_running"}])
        self.assertEqual(payload["daemons"]["processes"]["telegram"], False)
        self.assertEqual(payload["rules"][0]["rule_id"], "fixture-clean-workspace")
        self.assertEqual(payload["rules"][0]["citations"][0]["source_line"], 7)
        self.assertEqual(len(payload["rule_task_links"]), 3)

    def test_server_is_loopback_only_and_serves_html_json_and_sse(self):
        # Non-loopback is refused unless the operator asks for it by name: a
        # typo, a default, or a port argument can never widen the binding.
        with self.assertRaisesRegex(ValueError, "loopback"):
            web.create_server(host="0.0.0.0", port=0, snapshot_fn=fixture_snapshot)

        server = web.create_server(host="127.0.0.1", port=0, snapshot_fn=fixture_snapshot, event_interval=0.01)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())
        base = f"http://127.0.0.1:{server.server_port}"

        with request.urlopen(base + "/", timeout=2) as response:
            page = response.read().decode("utf-8")
        self.assertIn("TRI-AI // JARVIS CORE", page)
        self.assertIn("EventSource", page)
        self.assertIn("#09090b", page)
        self.assertIn("#05070a", page)
        self.assertIn("neuralGraph", page)
        self.assertIn("advanceGraph", page)
        self.assertIn("Memory Bank", page)

        with request.urlopen(base + "/api/snapshot", timeout=2) as response:
            api_payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(api_payload["metrics"]["ledger_entries"], 12)

        with request.urlopen(base + "/events", timeout=2) as response:
            first_line = response.readline().decode("utf-8").strip()
            data_line = response.readline().decode("utf-8").strip()
        self.assertEqual(first_line, "event: snapshot")
        self.assertEqual(json.loads(data_line.removeprefix("data: "))["metrics"]["total_tasks"], 3)


class WebBoundaryTests(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "jarvis_web.py"

    def test_web_surface_has_no_board_mutator_credential_or_process_capability(self):
        tree = ast.parse(self.source.read_text(encoding="utf-8"))
        forbidden_modules = {"board", "ledger", "subprocess", "socket", "requests", "httpx", "urllib"}
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden_modules)
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in forbidden_modules:
                imports.append(node.module)
        self.assertEqual(imports, [])
        source = self.source.read_text(encoding="utf-8")
        self.assertNotIn("TRI_AI_TELEGRAM_BOT_TOKEN", source)
        self.assertNotIn("create_task", source)
        self.assertIn("127.0.0.1", source)


class WebPresentationTests(unittest.TestCase):
    """Regression proofs for the operator-reported visual defects."""

    def test_no_class_name_leaks_into_rendered_text(self):
        # make(tag, text, cls): a class name passed as the text argument leaked
        # literal "dot" into the status badges and "inspect-grid" into the
        # node inspector.
        self.assertNotIn("make('i','dot','dot')", web.HTML)
        self.assertNotIn("make('div','inspect-grid')", web.HTML)
        self.assertIn("make('i','','dot')", web.HTML)
        self.assertIn("make('div','','inspect-grid')", web.HTML)

    def test_status_badge_reserves_space_for_its_indicator(self):
        self.assertIn(".service { align-items:center; display:inline-flex; gap:6px; }", web.HTML)
        self.assertIn(".dot { border-radius:9999px; flex-shrink:0; height:8px; width:8px; }", web.HTML)

    def test_outcome_states_render_as_tinted_micro_badges(self):
        self.assertIn("outcome-badge ${event.outcome==='passed'?'pass':", web.HTML)
        self.assertIn(".outcome-badge.pass { background:rgba(0,255,157,.1); border:1px solid #00ff9d40; color:#00ff9d; }", web.HTML)
        self.assertIn(".outcome-badge.fail { background:rgba(255,0,85,.1); border:1px solid #ff005540; color:#ff0055; }", web.HTML)
        self.assertIn(".outcome-badge.warn { background:rgba(255,183,3,.1); border:1px solid #ffb70340; color:#ffb703; }", web.HTML)

    def test_topology_canvas_draws_backdrop_anchor_and_label_pills(self):
        for symbol in ("function drawBackdrop(rect)", "function drawCore(rect)", "function drawLabelPill(text,x,y,bounds)"):
            self.assertIn(symbol, web.HTML)
        self.assertIn("drawBackdrop(rect);drawCore(rect);", web.HTML)
        self.assertIn("[TRI-AI CORE]", web.HTML)
        # The anchor is suppressed once real dependency edges connect the nodes.
        self.assertIn("if(!anchoredLayout())return;", web.HTML)
        self.assertIn("drawNamePlate(node,leftward?node.x-gap:node.x+gap,node.y,bounds,leftward);", web.HTML)
        # Without dependency edges the layout holds nodes on the core orbit
        # instead of letting mutual repulsion push them to the canvas edges.
        self.assertIn("function anchoredLayout() { return !hud.edges.some(edge=>edge.kind==='dependency'); }", web.HTML)
        self.assertIn("if(nodes.length&&anchoredLayout())", web.HTML)
        self.assertNotIn("ctx.fillText(node.label", web.HTML)


class TelemetryPayloadTests(unittest.TestCase):
    """The snapshot must carry run evidence verbatim, and only for real runs."""

    def payload(self) -> dict:
        return web.snapshot_payload(fixture_snapshot())

    def test_running_task_carries_phases_telemetry_and_bounded_logs(self):
        running = next(task for task in self.payload()["tasks"] if task["id"] == "t_running")
        telemetry = running["telemetry"]
        self.assertEqual(telemetry["run_id"], 9)
        self.assertEqual(telemetry["worker_pid"], 4242)
        self.assertEqual(telemetry["model"], "auto/best-free")
        self.assertEqual(telemetry["step_key"], "verify")
        self.assertEqual(
            [(phase["key"], phase["state"]) for phase in telemetry["phases"]],
            [("claimed", "done"), ("worktree_prep", "skipped"),
             ("agent_active", "done"), ("verify_gate", "active")],
        )
        self.assertEqual(telemetry["logs"][0]["lines"], ["verify: running"])
        self.assertFalse(telemetry["logs"][0]["truncated"])

    def test_task_without_a_run_reports_no_phases_rather_than_inventing_them(self):
        ready = next(task for task in self.payload()["tasks"] if task["id"] == "t_ready")
        self.assertEqual(ready["telemetry"], {"phases": [], "logs": []})

    def test_only_running_tasks_expose_log_tails(self):
        for task in self.payload()["tasks"]:
            if task["id"] != "t_running":
                self.assertEqual(task["telemetry"]["logs"], [])


class SiteInspectionPresentationTests(unittest.TestCase):
    """Hierarchy, progress rings, deep inspection, and mobile ergonomics."""

    def test_nodes_are_tiered_into_rooms_and_stages(self):
        self.assertIn("node.tier=node.kind!=='task'?'rule':childIds.has(node.id)?'stage':'room'", web.HTML)
        self.assertIn("function nodeRadius(node)", web.HTML)
        self.assertIn("function drawRoomFrame(node,radius)", web.HTML)

    def test_workspace_families_get_perimeter_hulls(self):
        self.assertIn("function convexHull(points)", web.HTML)
        self.assertIn("function drawHulls(bounds)", web.HTML)
        self.assertIn("function workspaceGroups()", web.HTML)

    def test_running_nodes_draw_a_reactor_core_and_phase_arc(self):
        self.assertIn("function drawReactorCore(node,radius)", web.HTML)
        self.assertIn("function drawPhaseRing(node,radius)", web.HTML)
        self.assertIn("if(status==='running'){drawReactorCore(node,radius);drawPhaseRing(node,radius);}", web.HTML)
        for label in ("CLAIMED", "WORKTREE_PREP", "AGENT_ACTIVE", "VERIFY_GATE"):
            self.assertIn(label, web.HTML)

    def test_inspector_exposes_memory_telemetry_and_a_room_window(self):
        for marker in ("Governing memory", "Active step", "Agent model",
                       "Branch / worktree", "Lifecycle phases",
                       "function renderRoomWindow(telemetry)", "room-window"):
            self.assertIn(marker, web.HTML)

    def test_canvas_supports_pan_and_double_tap_zoom(self):
        self.assertIn("function zoomAt(px,py,next)", web.HTML)
        self.assertIn("function visibleWorld(rect)", web.HTML)
        self.assertIn("hud.view.x=hud.panning.ox+(point.x-hud.panning.px)", web.HTML)
        self.assertIn("now-hud.lastTapAt<320", web.HTML)
        self.assertIn("'pointercancel'", web.HTML)

    def test_inspector_becomes_a_bottom_sheet_on_phone_widths(self):
        self.assertIn("@media (max-width:767px)", web.HTML)
        self.assertIn(".hud-aside.open { transform:translateY(0); }", web.HTML)
        self.assertIn("function openSheet(open)", web.HTML)
        self.assertIn("window.matchMedia('(max-width:767px)').matches", web.HTML)


if __name__ == "__main__":
    unittest.main()
