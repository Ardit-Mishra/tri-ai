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
    return terminal.DashboardSnapshot(
        tasks=(
            terminal.TaskView("t_ready", "Prepare evidence", "ready", None),
            terminal.TaskView("t_running", "Verify dashboard", "running", 9),
            terminal.TaskView("t_done", "Completed task", "done", 8),
        ),
        edges=(terminal.TaskEdge("t_ready", "t_running"),),
        ledger_events=(terminal.LedgerEvent(10.0, "t_running", "failed", 7, 1.2),),
        ledger_entry_count=12,
        ledger_errors=(),
        activated_rule_count=3,
        daemons=terminal.DaemonHealth(
            "running", (("supervisor", True), ("worker", True), ("telegram", False)), None,
        ),
    )


class WebSerializationTests(unittest.TestCase):
    def test_snapshot_json_preserves_authoritative_fields(self):
        payload = web.snapshot_payload(fixture_snapshot())
        self.assertEqual(payload["metrics"], {
            "total_tasks": 3, "active_runs": 1, "ledger_entries": 12, "accepted_rules": 3,
        })
        self.assertEqual(payload["tasks"][1]["id"], "t_running")
        self.assertEqual(payload["edges"], [{"parent_id": "t_ready", "child_id": "t_running"}])
        self.assertEqual(payload["daemons"]["processes"]["telegram"], False)

    def test_server_is_loopback_only_and_serves_html_json_and_sse(self):
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
        self.assertIn('"127.0.0.1"', source)


if __name__ == "__main__":
    unittest.main()
