"""Serve only the sealed, self-contained Kaya Cortex demonstration."""

from __future__ import annotations

import argparse
import json
import os
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


INDEX = Path(__file__).with_name("index.html")


class _SnapshotParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._inside = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and dict(attrs).get("id") == "demoSnapshot":
            self._inside = True

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False


def sealed_snapshot(page: bytes) -> dict:
    parser = _SnapshotParser()
    parser.feed(page.decode("utf-8"))
    snapshot = json.loads("".join(parser.parts))
    graph = snapshot.get("file_graph") or {}
    if (snapshot.get("demo") is not True or graph.get("synthetic") is not True
            or graph.get("status") != "synthetic-demonstration"):
        raise ValueError("Public Cortex requires synthetic demonstration data")
    if any(item.get("synthetic") is not True or
           not str(item.get("id", "")).startswith("demo:")
           for item in graph.get("items", [])):
        raise ValueError("Public Cortex contains a non-synthetic source item")
    if any(not str(task.get("id", "")).startswith("demo_")
           for task in snapshot.get("tasks", [])):
        raise ValueError("Public Cortex contains a non-demo task")
    for section in ("brain", "routing", "capabilities", "radar", "daemons"):
        if (snapshot.get(section) or {}).get("status") != "demonstration":
            raise ValueError(f"Public Cortex {section} is not a demonstration")
    snapshot["sealed"] = True
    return snapshot


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    page = INDEX.read_bytes()
    snapshot = sealed_snapshot(page)
    payload = json.dumps(snapshot, separators=(",", ":")).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; "
                "frame-src 'none'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/", "/index.html"}:
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/api/snapshot":
                self._send(200, payload, "application/json; charset=utf-8")
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_HEAD(self) -> None:
            self.do_GET()

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3018")))
    args = parser.parse_args(argv)
    with make_server(args.host, args.port) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
