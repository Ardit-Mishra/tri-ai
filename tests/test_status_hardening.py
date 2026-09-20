"""What a review found after the collector's own tests were already green.

The first suite tested the collector's decisions and none of its operation, so
all of it passed while three things were wrong:

The beacon read `board.sqlite3` and a `daemons.json` at the runtime root. The
runtime keeps `board.db` and `logs/daemons.json`, as the dashboard and the
daemon scripts have all along. So `beacon.py` raised DashboardSourceError and
exited before posting anything: the node board it feeds had never once been
reachable from a real machine. The layout now has one definition and this file
pins every reader to it.

One SQLite connection was shared across request threads. `check_same_thread=
False` silences the guard without making each execute/commit its own
transaction; eight writers doing forty stores each produced 67 errors.

And an unauthenticated caller could declare a Content-Length and then send
nothing, holding a thread and a socket indefinitely.
"""

from __future__ import annotations

import pathlib
import socket
import sqlite3
import sys
import threading
import time
import unittest

if __package__ in {None, ""}:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import beacon
from dashboard import jarvis_terminal
from services.status import collector


class RuntimeLayoutHasOneDefinition(unittest.TestCase):
    def test_the_paths_match_what_the_runtime_actually_uses(self):
        paths = jarvis_terminal.runtime_paths(pathlib.Path("/rt"))
        self.assertEqual(paths["board_path"].name, "board.db")
        self.assertEqual(paths["ledger_path"].name, "ledger.jsonl")
        self.assertEqual(paths["daemon_state_path"].parent.name, "logs")
        self.assertEqual(paths["daemon_state_path"].name, "daemons.json")

    def test_the_dashboard_cli_defaults_agree_with_the_helper(self):
        """Two readers disagreeing about the layout is what broke the beacon."""
        paths = jarvis_terminal.runtime_paths(jarvis_terminal.DEFAULT_RUNTIME_ROOT)
        self.assertEqual(paths["board_path"], jarvis_terminal.DEFAULT_RUNTIME_ROOT / "board.db")
        self.assertEqual(
            paths["daemon_state_path"],
            jarvis_terminal.DEFAULT_RUNTIME_ROOT / "logs" / "daemons.json",
        )

    def test_the_beacon_reads_through_the_helper(self):
        """A literal path in the beacon is how the first version got it wrong."""
        source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "beacon.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("runtime_paths(", source)
        self.assertNotIn("board.sqlite3", source)


class ConcurrentWritesDoNotCorrupt(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE heartbeat (node TEXT PRIMARY KEY, received_at REAL NOT NULL, payload TEXT NOT NULL)"
        )

    def test_many_nodes_posting_at_once_all_land(self):
        errors: list[str] = []

        def hammer(i: int) -> None:
            for n in range(25):
                try:
                    collector.store(
                        self.conn, {"node": f"node{i}", "generated_at": 1000.0}, now=1000.0 + n
                    )
                    collector.status_document(self.conn, now=1000.0 + n)
                except Exception as exc:  # noqa: BLE001 - the point is that none occur
                    errors.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"concurrent access raised: {errors[:3]}")
        self.assertEqual(collector.status_document(self.conn, now=1100.0)["node_count"], 8)


class SlowRequestsCannotHoldTheServer(unittest.TestCase):
    """A real socket, because this defect does not exist at the function level."""

    def setUp(self):
        self.conn = collector.connect(":memory:")
        handler = collector.handler_class(self.conn, "s3cret")
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def test_a_declared_body_that_never_arrives_is_dropped(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(sock.close)
        sock.sendall(
            b"POST /api/beacon HTTP/1.1\r\nHost: x\r\nContent-Length: 500\r\n"
            b"X-Tri-AI-Signature: deadbeef\r\n\r\n"
        )
        # Send nothing more. The server must not wait on this forever.
        sock.settimeout(collector.REQUEST_TIMEOUT_S + 8)
        started = time.monotonic()
        try:
            data = sock.recv(1024)
        except socket.timeout:
            self.fail("the server held a stalled request past its own deadline")
        waited = time.monotonic() - started
        self.assertLess(waited, collector.REQUEST_TIMEOUT_S + 6)
        # Either a refusal or a closed connection is correct; hanging is not.
        self.assertTrue(data == b"" or data.startswith(b"HTTP/1."), data[:40])

    def test_the_server_still_answers_afterwards(self):
        """A stalled connection must not take the collector down with it."""
        stalled = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(stalled.close)
        stalled.sendall(b"POST /api/beacon HTTP/1.1\r\nHost: x\r\nContent-Length: 400\r\n\r\n")

        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=10) as r:
            self.assertEqual(r.status, 200)


class SignedPostsStillWorkEndToEnd(unittest.TestCase):
    def setUp(self):
        self.conn = collector.connect(":memory:")
        handler = collector.handler_class(self.conn, "s3cret", clock=lambda: 1000.0)
        from http.server import ThreadingHTTPServer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def test_a_signed_heartbeat_is_accepted_and_readable(self):
        payload = {"node": "desktop", "role": "router", "generated_at": 1000.0,
                   "daemons": {"status": "healthy", "processes": []}}
        self.assertEqual(
            beacon.post(f"http://127.0.0.1:{self.port}/api/beacon", payload, secret="s3cret"),
            202,
        )
        import json
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=10) as r:
            doc = json.load(r)
        self.assertEqual([n["node"] for n in doc["nodes"]], ["desktop"])


if __name__ == "__main__":
    unittest.main()
