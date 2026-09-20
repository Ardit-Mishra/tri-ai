"""The hosted half of the status board: receive heartbeats, serve them back.

This runs on a public host. The nodes do not. Everything here therefore treats
the request as hostile until the signature says otherwise, and treats its own
stored data as the only truth it has - it never reaches back toward a node.

Two endpoints and one page:

    POST /api/beacon   signed, writes one node's latest heartbeat
    GET  /api/status   public, read-only, returns every node's latest
    GET  /             the page

There is no endpoint that mutates anything except the heartbeat table, and no
endpoint that takes a path, an id, or a query the caller controls. Storage is
last-write-wins per node: history is the ledger's job on the node itself, and
keeping a log here would turn a status page into a second copy of private
operational data.

Liveness is computed, never reported. A node cannot claim to be up; it can only
post recently. `stale_after_s` turns silence into the `down` state, so a wedged
node that stops posting goes red without anything having to notice it died.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

import beacon  # noqa: E402  (path set above so the node and host agree on the format)


SECRET_ENV = "TRI_AI_BEACON_SECRET"
DB_ENV = "TRI_AI_STATUS_DB"
MAX_BODY_BYTES = 16 * 1024

# A heartbeat whose clock is further off than this is refused. It bounds replay
# of a captured request without requiring the nodes to agree on a clock to the
# second.
MAX_CLOCK_SKEW_S = 300.0


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    target = path or os.environ.get(DB_ENV) or str(HERE / "status.sqlite3")
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS heartbeat (
            node        TEXT PRIMARY KEY,
            received_at REAL NOT NULL,
            payload     TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def store(conn: sqlite3.Connection, payload: dict[str, Any], *, now: float) -> None:
    conn.execute(
        "INSERT INTO heartbeat (node, received_at, payload) VALUES (?, ?, ?) "
        "ON CONFLICT(node) DO UPDATE SET received_at = excluded.received_at, "
        "payload = excluded.payload",
        (str(payload["node"]), now, json.dumps(payload, sort_keys=True)),
    )
    conn.commit()


def status_document(
    conn: sqlite3.Connection,
    *,
    now: float,
    stale_after_s: float = beacon.DEFAULT_STALE_AFTER_S,
) -> dict[str, Any]:
    """Every node's latest heartbeat, with liveness derived from the clock."""
    nodes = []
    for node, received_at, raw in conn.execute(
        "SELECT node, received_at, payload FROM heartbeat ORDER BY node"
    ):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        age = max(0.0, now - float(received_at))
        payload["age_s"] = round(age, 1)
        # Reachable and healthy are different questions, and a board that
        # answers only the first shows a green node whose router has died.
        # `down` means the heartbeat stopped; `degraded` means it is arriving
        # and reporting a problem. Only the first is inferred from the clock.
        daemons = payload.get("daemons") or {}
        processes = daemons.get("processes") or []
        unhealthy = daemons.get("status") not in (None, "healthy")
        any_dead = any(not proc.get("alive", True) for proc in processes)
        if age > stale_after_s:
            payload["state"] = "down"
        elif unhealthy or any_dead:
            payload["state"] = "degraded"
        else:
            payload["state"] = "up"
        nodes.append(payload)
    return {
        "generated_at": now,
        "stale_after_s": stale_after_s,
        "nodes": nodes,
        "nodes_up": sum(1 for n in nodes if n["state"] == "up"),
        "nodes_degraded": sum(1 for n in nodes if n["state"] == "degraded"),
        "nodes_down": sum(1 for n in nodes if n["state"] == "down"),
        "node_count": len(nodes),
    }


def handler_class(conn: sqlite3.Connection, secret: str, clock=time.time):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TriAIStatus/1.0"

        def log_message(self, fmt, *args):  # quieter, and no request bodies in logs
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, document: dict[str, Any]) -> None:
            self._send(code, json.dumps(document).encode("utf-8"), "application/json")

        def do_GET(self):  # noqa: N802  (stdlib naming)
            if self.path.split("?")[0] == "/api/status":
                self._json(200, status_document(conn, now=clock()))
                return
            if self.path.split("?")[0] in {"/", "/index.html"}:
                page = HERE / "index.html"
                if page.exists():
                    self._send(200, page.read_bytes(), "text/html; charset=utf-8")
                    return
            self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.split("?")[0] != "/api/beacon":
                self._json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "bad body length"})
                return
            body = self.rfile.read(length)

            signature = self.headers.get(beacon.SIGNATURE_HEADER, "")
            if not secret or not beacon.verify(body, secret, signature):
                # Same answer for a missing, malformed and wrong signature: a
                # caller probing this endpoint learns nothing from the code.
                self._json(401, {"error": "unauthorized"})
                return

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._json(400, {"error": "malformed json"})
                return
            if not isinstance(payload, dict) or not str(payload.get("node", "")).strip():
                self._json(400, {"error": "node is required"})
                return

            now = clock()
            generated_at = float(payload.get("generated_at") or 0.0)
            if abs(now - generated_at) > MAX_CLOCK_SKEW_S:
                self._json(400, {"error": "clock skew"})
                return

            store(conn, payload, now=now)
            self._json(202, {"stored": payload["node"]})

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    port = int(os.environ.get("PORT", "8099"))
    secret = os.environ.get(SECRET_ENV, "")
    if not secret:
        print(f"collector: {SECRET_ENV} is not set; refusing to accept unsigned posts", file=sys.stderr)
        return 2
    conn = connect()
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class(conn, secret))
    print(f"collector: listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
