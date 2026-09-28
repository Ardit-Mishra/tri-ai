"""Endpoint and isolation proofs for the local KAYA web surface."""

from __future__ import annotations

import ast
import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from urllib import request
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import kaya_terminal as terminal  # noqa: E402
from dashboard import kaya_web as web  # noqa: E402


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
        brain=terminal.BrainView(
            status="ready", item_count=1, inbox_count=2, edge_count=1,
            items=(terminal.BrainItemView(
                "m_memory", "Dashboard is Tri-AI's control surface", "context",
                "operator", "tri-ai", "unreviewed", 11.0,
            ),),
            edges=(terminal.BrainEdgeView(
                "e_memory", "m_memory", "m_memory", "documents", "m_memory",
            ),),
        ),
        capabilities=terminal.CapabilityView(
            status="ready", total=10219, active=837, archived=9343,
            routable=841, gated=16, candidates=11,
            items=(terminal.CapabilityItemView(
                "adapter:browser-use", "Browser Use", "command", "executable",
                "gated", "entrypoint-present", "reviewed", ("browser", "qa"),
            ),),
        ),
        radar=terminal.RadarView(
            status="ready", generated_at="2026-09-21T00:00:00Z",
            candidate_count=7, evaluated_count=1, error_count=0,
            candidates=(terminal.RadarCandidateView(
                "example/new-agent", "github", "https://github.com/example/new-agent",
                "evaluate", 3, ("recency",),
            ),),
            evaluations=(terminal.RadarEvaluationView(
                "example/new-agent", "probe-incomplete", "static_clear", "not_run",
            ),),
        ),
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
        self.assertEqual(payload["brain"]["item_count"], 1)
        self.assertEqual(payload["brain"]["items"][0]["id"], "m_memory")
        self.assertEqual(payload["brain"]["edges"][0]["relation"], "documents")
        self.assertEqual(payload["capabilities"]["total"], 10219)
        self.assertEqual(payload["capabilities"]["items"][0]["name"], "Browser Use")
        self.assertEqual(payload["radar"]["candidate_count"], 7)
        self.assertEqual(payload["radar"]["evaluations"][0]["static_verdict"], "static_clear")

    def test_demo_snapshot_is_explicitly_labeled_and_never_reads_live_runtime(self):
        payload = web.snapshot_payload(web.demo_snapshot(), demo=True)
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["metrics"]["total_tasks"], 4)
        self.assertEqual(payload["metrics"]["active_runs"], 1)
        self.assertGreaterEqual(payload["brain"]["item_count"], 5)
        self.assertIn("No live task", payload["daemons"]["diagnostic"])
        self.assertTrue(payload["file_graph"]["synthetic"])
        self.assertGreaterEqual(payload["file_graph"]["item_count"], 400)
        self.assertEqual(
            {source["label"] for source in payload["file_graph"]["sources"]},
            {"Desktop", "Laptop", "Google Drive", "GitHub", "Obsidian", "Claude and Codex", "Vercel and Render"},
        )
        self.assertNotIn("C:/", json.dumps(payload["file_graph"]))

    def test_live_snapshot_does_not_invent_or_scan_a_file_graph(self):
        payload = web.snapshot_payload(fixture_snapshot())
        self.assertEqual(payload["file_graph"]["status"], "not-indexed")
        self.assertEqual(payload["file_graph"]["items"], [])

    def test_private_graph_is_summarized_in_stream_and_fetched_once_from_its_endpoint(self):
        graph = {
            "status": "indexed", "synthetic": False, "item_count": 1,
            "sources": [{"id": "desktop", "label": "Desktop", "node_count": 1, "authorized": True}],
            "items": [{"id": "private:root", "label": "Desktop", "kind": "folder", "source": "desktop", "parent_id": None}],
            "diagnostic": "metadata only", "revision": "revision-1",
        }
        server = web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=fixture_snapshot,
            private_graph_fn=lambda: graph,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())
        base = f"http://127.0.0.1:{server.server_port}"

        with request.urlopen(base + "/api/snapshot", timeout=2) as response:
            summary = json.loads(response.read().decode("utf-8"))["file_graph"]
        self.assertEqual(summary["item_count"], 1)
        self.assertEqual(summary["items"], [])
        self.assertEqual(summary["scene_url"], "/api/file-graph/scene")
        with self.assertRaisesRegex(Exception, "HTTP Error 404"):
            request.urlopen(base + "/api/file-graph", timeout=2)

    def test_private_scene_keeps_the_dense_field_without_shipping_file_metadata(self):
        graph = {
            "status": "indexed", "synthetic": False, "item_count": 3,
            "sources": [{"id": "desktop", "label": "Desktop", "node_count": 3, "authorized": True}],
            "items": [
                {"id": "private:root", "label": "Desktop", "kind": "folder", "source": "desktop", "parent_id": None},
                {"id": "private:folder", "label": "private-plans", "kind": "folder", "source": "desktop", "parent_id": "private:root"},
                {"id": "private:file", "label": "classified-notes.txt", "kind": "file", "source": "desktop", "parent_id": "private:folder"},
            ],
            "diagnostic": "metadata only", "revision": "revision-1",
        }
        server = web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=fixture_snapshot,
            private_graph_fn=lambda: graph,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())

        with request.urlopen(f"http://127.0.0.1:{server.server_port}/api/file-graph/scene", timeout=2) as response:
            scene = json.loads(response.read().decode("utf-8"))

        self.assertEqual(scene["item_count"], 3)
        self.assertEqual(scene["ambient"]["node_count"], 3)
        self.assertEqual(scene["clusters"], [{
            "id": "cluster:desktop", "source": "desktop", "label": "Desktop",
            "node_count": 3, "folder_count": 2, "state": "indexed",
        }])
        self.assertNotIn("items", scene)
        self.assertNotIn("classified-notes.txt", json.dumps(scene))
        self.assertNotIn("private-plans", json.dumps(scene))

    def test_private_scene_sanitizes_configured_source_labels(self):
        graph = {
            "status": "indexed", "synthetic": False, "item_count": 1,
            "sources": [{"id": "desktop", "label": "Desktop (private-machine-name)", "node_count": 1, "authorized": True}],
            "items": [{"id": "private:root", "label": "Desktop", "kind": "folder", "source": "desktop", "parent_id": None}],
            "diagnostic": "metadata only", "revision": "revision-1",
        }

        scene = web._private_file_graph_scene(graph)

        self.assertEqual(scene["sources"][0]["label"], "Desktop")
        self.assertNotIn("private-machine-name", json.dumps(scene))

    def test_private_scene_marks_empty_registered_sources_as_pending(self):
        graph = {
            "status": "partial", "synthetic": False, "item_count": 0,
            "sources": [{"id": "phone", "label": "Phone", "node_count": 0, "authorized": True}],
            "items": [], "diagnostic": "awaiting source", "revision": "revision-1",
        }

        scene = web._private_file_graph_scene(graph)

        self.assertEqual(scene["clusters"][0]["state"], "pending")

    def test_registered_private_sources_remain_visible_before_an_index_arrives(self):
        graph = {
            "status": "partial", "synthetic": False, "item_count": 0,
            "sources": [{"id": "desktop", "label": "Desktop", "node_count": 0, "authorized": True}],
            "items": [], "diagnostic": "Awaiting index", "revision": "revision-1",
        }

        scene = web._private_file_graph_scene(graph)

        self.assertEqual(scene["clusters"][0]["label"], "Desktop")
        self.assertEqual(scene["clusters"][0]["state"], "pending")
        self.assertEqual(scene["ambient"]["node_count"], 0)

    def test_source_registry_adds_pending_sources_without_exposing_configuration_labels(self):
        sources = web._merge_declared_sources(
            [{"id": "drive", "label": "untrusted local account label", "node_count": 4, "authorized": True}],
            ["desktop", "drive", "phone"],
        )

        self.assertEqual(
            sources,
            [
                {"id": "drive", "label": "Google Drive", "node_count": 4, "authorized": True},
                {"id": "desktop", "label": "Desktop", "node_count": 0, "authorized": True},
                {"id": "phone", "label": "Phone", "node_count": 0, "authorized": True},
            ],
        )
        self.assertNotIn("untrusted local account label", json.dumps(sources))

    def test_source_registry_groups_multiple_laptop_roots_into_one_visual_region(self):
        sources = web._merge_declared_sources(
            [
                {"id": "laptop-desktop", "label": "Desktop", "node_count": 4, "authorized": True},
                {"id": "laptop-documents", "label": "Documents", "node_count": 9, "authorized": True},
            ],
            ["laptop"],
        )

        self.assertEqual(sources, [{
            "id": "laptop", "label": "Laptop", "node_count": 13, "authorized": True,
        }])

    def test_source_registry_groups_multiple_desktop_roots_into_one_visual_region(self):
        """Desktop roots must not appear as unrelated visual sources."""
        sources = web._merge_declared_sources(
            [
                {"id": "desktop-desktop", "label": "Desktop", "node_count": 4, "authorized": True},
                {"id": "desktop-documents", "label": "Documents", "node_count": 9, "authorized": True},
            ],
            ["desktop"],
        )

        self.assertEqual(sources, [{
            "id": "desktop", "label": "Desktop", "node_count": 13, "authorized": True,
        }])

    def test_source_rail_describes_indexed_desktop_metadata_truthfully(self):
        page = web.HTML
        self.assertIn("Desktop metadata", page)
        self.assertIn("awaiting private index", page)

    def test_source_rail_uses_the_same_stable_source_color_as_the_neural_field(self):
        page = web.HTML
        self.assertIn("SOURCE_REGION_CSS", page)
        self.assertIn("--source-color", page)
        self.assertIn("sourceColorFor(source.id)", page)

    def test_private_graph_refreshes_when_the_registry_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            registry = source_root / "sources.json"
            registry.write_text('{"sources":["desktop"]}', encoding="utf-8")
            previous_root, previous_cache = web._PRIVATE_SOURCE_ROOT, web._private_graph_cache
            try:
                web._PRIVATE_SOURCE_ROOT = source_root
                web._private_graph_cache = None
                first = web._load_private_file_graph()
                registry.write_text('{"sources":["desktop","phone"]}', encoding="utf-8")
                second = web._load_private_file_graph()
            finally:
                web._PRIVATE_SOURCE_ROOT, web._private_graph_cache = previous_root, previous_cache

        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual([source["id"] for source in second["sources"]], ["desktop", "phone"])

    def test_private_graph_uses_matching_summary_without_loading_large_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            source_root = Path(temporary)
            index = source_root / "desktop.json"
            payload = {
                "status": "indexed", "synthetic": False, "item_count": 1,
                "sources": [{"id": "desktop", "label": "Desktop", "node_count": 1, "authorized": True}],
                "items": [{"id": "private:root", "label": "Desktop", "name": "Desktop", "relative_path": ".", "provenance": "desktop:.", "kind": "folder", "source": "desktop", "parent_id": None, "size": None, "modified_at": "2026-09-27T00:00:00Z", "synthetic": False}],
                "diagnostic": "metadata only",
            }
            from dashboard import private_index
            private_index.write_private_index_atomic(index, payload)
            private_index.write_private_index_summary_atomic(index, payload)
            previous_root, previous_cache = web._PRIVATE_SOURCE_ROOT, web._private_graph_cache
            try:
                web._PRIVATE_SOURCE_ROOT = source_root
                web._private_graph_cache = None
                with mock.patch.object(web.private_index, "load_private_index", side_effect=AssertionError("full index loaded")):
                    graph = web._load_private_file_graph()
            finally:
                web._PRIVATE_SOURCE_ROOT, web._private_graph_cache = previous_root, previous_cache

        self.assertEqual(graph["item_count"], 1)
        self.assertEqual(graph["sources"][0]["id"], "desktop")
        self.assertEqual(graph["folder_counts"], {"desktop": 1})

    def test_server_is_loopback_only_and_serves_html_json_and_sse(self):
        # Non-loopback is refused unless the operator asks for it by name: a
        # typo, a default, or a port argument can never widen the binding.
        with self.assertRaisesRegex(ValueError, "loopback"):
            web.create_server(host="0.0.0.0", port=0, snapshot_fn=fixture_snapshot)

        private_graph = lambda: {
            "status": "unavailable", "synthetic": False, "item_count": 0,
            "sources": [], "items": [], "diagnostic": "fixture", "revision": "fixture",
        }
        server = web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=fixture_snapshot,
            private_graph_fn=private_graph, event_interval=0.01,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())
        base = f"http://127.0.0.1:{server.server_port}"

        with request.urlopen(base + "/", timeout=2) as response:
            page = response.read().decode("utf-8")
        # Read from the constant rather than repeated here, so the brand and
        # its test cannot drift apart the way they just did.
        self.assertIn(f"TRI-AI // {web.PRODUCT_NAME}", page)
        self.assertIn("EventSource", page)
        self.assertIn("#08100e", page)
        self.assertIn("Cortex Chamber", page)
        self.assertIn("neuralGraph", page)
        self.assertIn("advanceGraph", page)
        self.assertIn("Learned rules", page)
        self.assertIn("Brain inbox", page)
        self.assertIn("Capability registry", page)
        self.assertIn("Technology radar", page)
        self.assertIn('src="/assets/tri-space.js"', page)
        self.assertIn('id="demoBadge"', page)
        self.assertIn("Demonstration data", page)
        # The page must answer "what is happening" without a tap.
        self.assertIn('id="nowWhat"', page)
        self.assertIn("function renderNow(data)", page)
        self.assertIn('id="themeToggle"', page)
        self.assertIn("function setTheme(theme)", page)
        self.assertIn('id="modelLanes"', page)
        self.assertIn("function renderModelLanes(data)", page)
        self.assertIn('class="cortex-theater"', page)
        self.assertIn('class="cortex-core graph-panel"', page)
        self.assertIn('id="spatialGraph"', page)
        self.assertIn('id="lensClaude"', page)
        self.assertIn('id="lensCodex"', page)
        self.assertIn("function renderAgentLens(lane)", page)
        self.assertIn("Evidence returned", page)

        with request.urlopen(base + "/api/snapshot", timeout=2) as response:
            api_payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(api_payload["metrics"]["ledger_entries"], 12)

        with request.urlopen(base + "/api/snapshot?demo=1", timeout=2) as response:
            demo_payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(demo_payload["demo"])
        self.assertEqual(demo_payload["metrics"]["total_tasks"], 4)

        with request.urlopen(base + "/assets/tri-space.js", timeout=2) as response:
            spatial_module = response.read().decode("utf-8")
            asset_cache_control = response.headers.get("Cache-Control", "")
        self.assertIn('from "/assets/three.module.min.js"', spatial_module)
        self.assertIn("no-store", asset_cache_control)

        with request.urlopen(base + "/events", timeout=2) as response:
            first_line = response.readline().decode("utf-8").strip()
            data_line = response.readline().decode("utf-8").strip()
        self.assertEqual(first_line, "event: snapshot")
        self.assertEqual(json.loads(data_line.removeprefix("data: "))["metrics"]["total_tasks"], 3)

    def test_demo_server_never_reads_the_runtime_snapshot(self):
        def live_snapshot() -> terminal.DashboardSnapshot:
            raise AssertionError("public demo must not read the local runtime")

        def private_graph():
            raise AssertionError("public demo must not read the private file index")

        server = web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=live_snapshot,
            private_graph_fn=private_graph, demo=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())
        with request.urlopen(f"http://127.0.0.1:{server.server_port}/api/snapshot", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["metrics"]["total_tasks"], 4)
        with self.assertRaisesRegex(Exception, "HTTP Error 404"):
            request.urlopen(f"http://127.0.0.1:{server.server_port}/api/file-graph", timeout=2)

    def test_demo_server_refuses_artifacts_even_when_a_reader_is_supplied(self):
        def unexpected_artifact(_task_id: str):
            raise AssertionError("public demo must not expose task artifacts")

        server = web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=fixture_snapshot,
            artifact_fn=unexpected_artifact, demo=True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: server.shutdown())
        with self.assertRaisesRegex(Exception, "HTTP Error 404"):
            request.urlopen(f"http://127.0.0.1:{server.server_port}/artifact/t_ready/0", timeout=2)


class WebBoundaryTests(unittest.TestCase):
    source = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "kaya_web.py"

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
        self.assertIn("node.tier=node.kind!=='task'?node.kind:childIds.has(node.id)?'stage':'room'", web.HTML)
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
