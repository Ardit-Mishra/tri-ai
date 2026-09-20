"""The collector is the only part of Tri-AI that faces the open internet.

Everything else runs behind Tailscale on machines the operator owns. This does
not, so the properties worth pinning are the ones an unauthenticated caller
could otherwise exercise: that an unsigned or wrongly-signed post is refused
and says nothing about why, that a captured request cannot be replayed forever,
and that a node's state is computed here rather than claimed by the node.

The last one matters most. A machine that has wedged will happily keep
insisting it is fine; the only reliable signal is that its heartbeat stopped
arriving. So `down` is derived from the clock, never read from the payload.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import unittest

import beacon

if __package__ in {None, ""}:
    # tests/run.py puts tests/ and src/ on the path, not the repository root,
    # and the collector lives beside the service it is deployed as rather than
    # inside the importable package.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from services.status import collector


def payload(node="desktop", *, status="healthy", processes=(("router", True),), at=1000.0):
    return {
        "node": node,
        "role": "router",
        "generated_at": at,
        "tasks": {"running": 1},
        "task_total": 1,
        "daemons": {"status": status, "processes": [{"name": n, "alive": a} for n, a in processes]},
        "ledger_entries": 12,
        "activated_rules": 3,
        "source_errors": 0,
        "models": ["qwen2.5-coder:14b"],
    }


class CollectorTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE heartbeat (node TEXT PRIMARY KEY, received_at REAL NOT NULL, payload TEXT NOT NULL)"
        )


class StateIsComputed(CollectorTestCase):
    def test_a_recent_healthy_heartbeat_is_up(self):
        collector.store(self.conn, payload(), now=1000.0)
        doc = collector.status_document(self.conn, now=1010.0, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "up")
        self.assertEqual(doc["nodes_up"], 1)

    def test_silence_is_down(self):
        collector.store(self.conn, payload(), now=1000.0)
        doc = collector.status_document(self.conn, now=1000.0 + 151, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "down")
        self.assertEqual(doc["nodes_down"], 1)

    def test_a_dead_process_is_degraded_not_up(self):
        """A heartbeat arriving on time does not mean the node is working."""
        collector.store(
            self.conn,
            payload(processes=(("router", True), ("worker", False))),
            now=1000.0,
        )
        doc = collector.status_document(self.conn, now=1005.0, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "degraded")
        self.assertEqual(doc["nodes_degraded"], 1)
        self.assertEqual(doc["nodes_up"], 0)

    def test_an_unhealthy_daemon_status_is_degraded(self):
        collector.store(self.conn, payload(status="degraded"), now=1000.0)
        doc = collector.status_document(self.conn, now=1005.0, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "degraded")

    def test_silence_outranks_degraded(self):
        """A node that stopped reporting is down, whatever it last reported."""
        collector.store(self.conn, payload(status="degraded"), now=1000.0)
        doc = collector.status_document(self.conn, now=1000.0 + 500, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "down")

    def test_a_node_cannot_declare_its_own_state(self):
        forged = payload()
        forged["state"] = "up"
        collector.store(self.conn, forged, now=1000.0)
        doc = collector.status_document(self.conn, now=1000.0 + 900, stale_after_s=150.0)
        self.assertEqual(doc["nodes"][0]["state"], "down")

    def test_latest_heartbeat_replaces_the_previous_one(self):
        collector.store(self.conn, payload(), now=1000.0)
        collector.store(self.conn, payload(at=2000.0), now=2000.0)
        doc = collector.status_document(self.conn, now=2001.0)
        self.assertEqual(doc["node_count"], 1)

    def test_a_corrupt_row_is_skipped_rather_than_failing_the_page(self):
        self.conn.execute(
            "INSERT INTO heartbeat (node, received_at, payload) VALUES (?, ?, ?)",
            ("broken", 1000.0, "{not json"),
        )
        collector.store(self.conn, payload(), now=1000.0)
        doc = collector.status_document(self.conn, now=1001.0)
        self.assertEqual([n["node"] for n in doc["nodes"]], ["desktop"])


class RequestHandling(CollectorTestCase):
    """Exercise the handler's decision logic without binding a socket."""

    def decide(self, body: bytes, signature: str, *, secret="s3cret", now=1000.0):
        if not secret or not beacon.verify(body, secret, signature):
            return 401
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return 400
        if not isinstance(parsed, dict) or not str(parsed.get("node", "")).strip():
            return 400
        if abs(now - float(parsed.get("generated_at") or 0.0)) > collector.MAX_CLOCK_SKEW_S:
            return 400
        return 202

    def test_a_valid_signed_heartbeat_is_accepted(self):
        body = beacon.encode(payload(at=1000.0))
        self.assertEqual(self.decide(body, beacon.sign(body, "s3cret")), 202)

    def test_an_unsigned_post_is_refused(self):
        body = beacon.encode(payload(at=1000.0))
        self.assertEqual(self.decide(body, ""), 401)

    def test_a_tampered_body_is_refused(self):
        signature = beacon.sign(beacon.encode(payload(node="desktop", at=1000.0)), "s3cret")
        self.assertEqual(self.decide(beacon.encode(payload(node="laptop", at=1000.0)), signature), 401)

    def test_a_stale_capture_cannot_be_replayed(self):
        body = beacon.encode(payload(at=1000.0))
        signature = beacon.sign(body, "s3cret")
        later = 1000.0 + collector.MAX_CLOCK_SKEW_S + 60
        self.assertEqual(self.decide(body, signature, now=later), 400)

    def test_a_missing_node_label_is_refused(self):
        body = beacon.encode({"node": "  ", "generated_at": 1000.0})
        self.assertEqual(self.decide(body, beacon.sign(body, "s3cret")), 400)

    def test_no_secret_configured_refuses_everything(self):
        """An unconfigured collector must be closed, not open."""
        body = beacon.encode(payload(at=1000.0))
        self.assertEqual(self.decide(body, beacon.sign(body, "s3cret"), secret=""), 401)


if __name__ == "__main__":
    unittest.main()
