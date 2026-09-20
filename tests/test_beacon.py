"""What leaves the machine must be smaller than what the dashboard can see.

The local HUD is a full reader: it holds task titles that arrived over Telegram
from whoever was typing, the prompts behind them, workspace paths that name the
operator's home directory, and daemon diagnostics that quote command lines. The
public status page is the same data source pointed at the open internet.

So these tests do not check that the payload looks right. They check that
specific strings which exist in the snapshot are *absent* from the bytes that
go on the wire, and that the payload's key set is fixed. A future field added
to ``DashboardSnapshot`` should fail the key-set test rather than quietly
appear on a public page.
"""

from __future__ import annotations

import json
import unittest

import beacon
from dashboard import jarvis_terminal as terminal


# Distinctive enough that a substring search cannot match by accident.
SECRET_TITLE = "Build the Mishwan landing page"
SECRET_PROMPT = "client contact is jane.doe@example.com, budget 4200"
SECRET_PATH = r"C:\Users\ardit\worktrees\private-client-work"
SECRET_DIAGNOSTIC = r"router died: C:\Users\ardit\.tri-ai\runs\9\stderr.log"


def snapshot() -> terminal.DashboardSnapshot:
    return terminal.DashboardSnapshot(
        tasks=(
            terminal.TaskView("t_a", SECRET_TITLE, "running", 9, SECRET_PATH, None, SECRET_PROMPT),
            terminal.TaskView("t_b", "Another title", "ready", None, SECRET_PATH),
            terminal.TaskView("t_c", "Third title", "done", 8, None),
            terminal.TaskView("t_d", "Fourth title", "done", 7, None),
        ),
        edges=(terminal.TaskEdge("t_a", "t_b"),),
        ledger_events=(terminal.LedgerEvent(10.0, "t_a", "failed", 7, 1.2),),
        ledger_entry_count=12,
        ledger_errors=("ledger line 3 is not JSON",),
        activated_rule_count=3,
        daemons=terminal.DaemonHealth(
            status="degraded",
            processes=(("router", True), ("worker", False)),
            diagnostic=SECRET_DIAGNOSTIC,
        ),
        rules=(),
        memory_errors=(),
    )


class PublicPayloadRedaction(unittest.TestCase):
    def payload_bytes(self) -> bytes:
        return beacon.encode(
            beacon.public_payload(snapshot(), node="desktop", role="router", now=1000.0)
        )

    def test_no_task_title_or_prompt_or_path_survives(self):
        wire = self.payload_bytes().decode("utf-8")
        for secret in (SECRET_TITLE, SECRET_PROMPT, SECRET_PATH, SECRET_DIAGNOSTIC):
            with self.subTest(secret=secret[:28]):
                self.assertNotIn(secret, wire)

    def test_no_task_identifier_survives(self):
        """Task ids are short but they are still a handle on private work."""
        wire = self.payload_bytes().decode("utf-8")
        for task_id in ("t_a", "t_b", "t_c", "t_d"):
            with self.subTest(task_id=task_id):
                self.assertNotIn(f'"{task_id}"', wire)

    def test_key_set_is_closed(self):
        """A new snapshot field must not reach the page by default."""
        payload = beacon.public_payload(snapshot(), node="desktop", role="router", now=1000.0)
        self.assertEqual(
            set(payload),
            {
                "node", "role", "generated_at", "tasks", "task_total", "daemons",
                "ledger_entries", "activated_rules", "source_errors", "models",
            },
        )
        self.assertEqual(set(payload["daemons"]), {"status", "processes"})

    def test_counts_are_preserved(self):
        payload = beacon.public_payload(snapshot(), node="desktop", role="router", now=1000.0)
        self.assertEqual(payload["tasks"], {"done": 2, "ready": 1, "running": 1})
        self.assertEqual(payload["task_total"], 4)
        self.assertEqual(payload["ledger_entries"], 12)
        self.assertEqual(payload["activated_rules"], 3)
        self.assertEqual(payload["source_errors"], 1)

    def test_daemon_state_survives_without_its_reason(self):
        payload = beacon.public_payload(snapshot(), node="desktop", role="router", now=1000.0)
        self.assertEqual(payload["daemons"]["status"], "degraded")
        self.assertEqual(
            payload["daemons"]["processes"],
            [{"name": "router", "alive": True}, {"name": "worker", "alive": False}],
        )

    def test_node_label_is_required(self):
        with self.assertRaises(beacon.BeaconError):
            beacon.public_payload(snapshot(), node="", role="router")


class Signing(unittest.TestCase):
    def test_round_trip(self):
        body = beacon.encode({"node": "desktop"})
        self.assertTrue(beacon.verify(body, "s3cret", beacon.sign(body, "s3cret")))

    def test_tampered_body_fails(self):
        signature = beacon.sign(beacon.encode({"node": "desktop"}), "s3cret")
        self.assertFalse(beacon.verify(beacon.encode({"node": "laptop"}), "s3cret", signature))

    def test_wrong_secret_fails(self):
        body = beacon.encode({"node": "desktop"})
        self.assertFalse(beacon.verify(body, "other", beacon.sign(body, "s3cret")))

    def test_encoding_is_stable(self):
        """The signature covers bytes, so key order cannot drift between runs."""
        self.assertEqual(
            beacon.encode({"b": 1, "a": 2}),
            beacon.encode({"a": 2, "b": 1}),
        )


class Posting(unittest.TestCase):
    def test_refuses_to_post_unsigned(self):
        with self.assertRaises(beacon.BeaconError):
            beacon.post("https://example.invalid/api/beacon", {"node": "desktop"}, secret="")

    def test_signs_the_exact_bytes_sent(self):
        seen = {}

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            seen["body"] = request.data
            seen["signature"] = request.headers[beacon.SIGNATURE_HEADER.capitalize()]
            return Response()

        payload = beacon.public_payload(snapshot(), node="desktop", role="router", now=1000.0)
        code = beacon.post("https://example.invalid/api/beacon", payload, secret="s3cret", opener=opener)

        self.assertEqual(code, 202)
        self.assertTrue(beacon.verify(seen["body"], "s3cret", seen["signature"]))
        self.assertEqual(json.loads(seen["body"])["node"], "desktop")


if __name__ == "__main__":
    unittest.main()
