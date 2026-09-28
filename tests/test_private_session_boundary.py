"""Nothing private leaves this process without an authenticated session.

Codex named the gap itself and it is still open:

    "the current Tailnet page is private at the network layer, but it does
    not yet have an in-app login or source-by-source step-up. Anyone
    authorized into your Tailnet can reach the dashboard."

796,684 indexed file and folder names — the whole shape of a Google Drive, a
desktop and a laptop — are served to any device on the Tailnet with no login
at all. Network privacy is not authorization: it means one compromised or
shared device sees everything.

This is step 1 of the agreed order (sealed-public versus authenticated-
private), built entirely locally. It creates no Cloudflare resource, opens
no port and exposes nothing new. It is the boundary that Tailscale identity
and Cloudflare Access later plug into, rather than either of those services.

The rule, chosen to match the actual risk rather than to add ceremony:

* **loopback is the operator.** Someone already on the machine can read the
  files directly; a password would protect nothing and would break every
  existing local workflow.
* **everything else must present a session.** That is exactly the Tailnet
  case, and the one that is currently open.
* **sealed is the default.** With no session the caller gets the same
  demonstration view a public visitor gets — never a partial private one.
  A failure to authenticate must never be a smaller leak; it must be none.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import kaya_web  # noqa: E402


LOOPBACK = ("127.0.0.1", 51234)
REMOTE = ("100.118.189.88", 51234)      # a Tailnet peer


class SessionDecisionTest(unittest.TestCase):
    """The decision alone, before any HTTP is involved."""

    def _allowed(self, client, *, token=None, presented=None, cookie=None):
        return kaya_web.private_access_allowed(
            client_address=client,
            configured_token=token,
            header_token=presented,
            cookie_header=cookie,
        )

    def test_loopback_is_the_operator(self):
        self.assertTrue(self._allowed(LOOPBACK))

    def test_ipv6_loopback_counts_too(self):
        self.assertTrue(self._allowed(("::1", 51234)))

    def test_a_tailnet_peer_without_a_session_is_sealed(self):
        self.assertFalse(self._allowed(REMOTE))

    def test_a_tailnet_peer_with_no_token_configured_is_still_sealed(self):
        """Fail closed. An unconfigured deployment must not be an open one."""
        self.assertFalse(self._allowed(REMOTE, token=None, presented="anything"))

    def test_the_right_token_in_a_header_opens_it(self):
        self.assertTrue(self._allowed(REMOTE, token="s3cret", presented="s3cret"))

    def test_the_right_token_in_a_cookie_opens_it(self):
        self.assertTrue(self._allowed(
            REMOTE, token="s3cret", cookie="kaya_session=s3cret; other=1"))

    def test_a_wrong_token_does_not(self):
        self.assertFalse(self._allowed(REMOTE, token="s3cret", presented="guess"))

    def test_a_blank_configured_token_never_authorizes(self):
        """An empty env var must not become a password of empty string."""
        self.assertFalse(self._allowed(REMOTE, token="   ", presented="   "))
        self.assertFalse(self._allowed(REMOTE, token="", presented=""))

    def test_comparison_does_not_leak_length_by_short_circuit(self):
        """Uses compare_digest; this pins the intent so it is not 'simplified'
        back into ==."""
        import inspect
        source = inspect.getsource(kaya_web.private_access_allowed)
        self.assertIn("compare_digest", source)


class SealedPayloadTest(unittest.TestCase):
    """What an unauthenticated caller actually receives."""

    def _payload(self, *, allowed):
        return kaya_web.seal_payload(_PRIVATE_PAYLOAD, private_allowed=allowed)

    def test_an_authorized_caller_gets_the_private_graph(self):
        payload = self._payload(allowed=True)
        self.assertEqual(payload["file_graph"]["item_count"], 796684)
        self.assertEqual(len(payload["tasks"]), 1)

    def test_an_unauthorized_caller_gets_no_file_counts(self):
        payload = self._payload(allowed=False)
        self.assertNotEqual(payload["file_graph"].get("item_count"), 796684)

    def test_an_unauthorized_caller_gets_no_source_names(self):
        blob = json.dumps(self._payload(allowed=False))
        for private in ("Google Drive", "486925", "796684", "field-guide"):
            self.assertNotIn(private, blob, f"{private!r} leaked while sealed")

    def test_an_unauthorized_caller_gets_no_tasks_or_brain(self):
        payload = self._payload(allowed=False)
        self.assertEqual(payload.get("tasks"), [])
        self.assertEqual(payload.get("brain", {}).get("item_count", 0), 0)

    def test_the_seal_is_announced_not_disguised(self):
        """A locked view must say it is locked, or it reads as an empty one."""
        payload = self._payload(allowed=False)
        self.assertTrue(payload.get("sealed"))

    def test_sealing_does_not_mutate_the_private_payload(self):
        before = json.dumps(_PRIVATE_PAYLOAD, sort_keys=True)
        self._payload(allowed=False)
        self.assertEqual(json.dumps(_PRIVATE_PAYLOAD, sort_keys=True), before)


_PRIVATE_PAYLOAD: dict = {
    "demo": False,
    "file_graph": {
        "status": "partial", "synthetic": False, "item_count": 796684,
        "sources": [
            {"id": "drive", "label": "Google Drive", "node_count": 2606},
            {"id": "desktop", "label": "Desktop", "node_count": 486925},
        ],
        "items": [],
    },
    "tasks": [{"id": "t_x", "title": "Build the field-guide page",
               "status": "done", "prompt": "field-guide"}],
    "brain": {"status": "ready", "item_count": 12, "inbox_count": 3},
    "metrics": {"total_tasks": 19},
    "edges": [], "rules": [], "ledger_events": [],
}


if __name__ == "__main__":
    unittest.main()


# --- enforcement, against a real server -------------------------------------
#
# `private_access_allowed` returning the right answer is not the same as the
# server asking it. Four modules in this codebase were complete, tested, and
# called by nothing — `lane_select`, the cartographer's runtime path, the
# learning loop, the brain kernel. A security boundary is the worst possible
# fifth, so these drive real HTTP.
#
# The tests reach the server over 127.0.0.1 because that is what a test can
# bind, and loopback is trusted by design — so they pass `trust_loopback=False`
# to make the server treat them as the remote caller they are standing in for.

import threading  # noqa: E402
from urllib import error, request  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dashboard_web import fixture_snapshot as _snapshot  # noqa: E402


class EnforcementTest(unittest.TestCase):
    GRAPH = {
        "status": "indexed", "synthetic": False, "item_count": 796684,
        "sources": [{"id": "drive", "label": "Google Drive",
                     "node_count": 2606, "authorized": True}],
        "items": [], "diagnostic": "metadata only", "revision": "r1",
    }

    def _serve(self, **kw):
        server = kaya_web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=_snapshot,
            private_graph_fn=lambda: self.GRAPH, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def _get(self, base, path, *, token=None):
        req = request.Request(base + path)
        if token:
            req.add_header("X-Kaya-Token", token)
        with request.urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_an_unauthenticated_caller_gets_the_sealed_snapshot(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        payload = self._get(base, "/api/snapshot")
        self.assertTrue(payload.get("sealed"))
        self.assertNotIn("Google Drive", json.dumps(payload))

    def test_the_token_unseals_it(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        payload = self._get(base, "/api/snapshot", token="s3cret")
        self.assertFalse(payload.get("sealed"))
        self.assertEqual(payload["file_graph"]["item_count"], 796684)

    def test_a_wrong_token_stays_sealed(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        self.assertTrue(self._get(base, "/api/snapshot", token="nope")["sealed"])

    def test_the_scene_endpoint_is_refused_without_a_session(self):
        """The scene is the whole graph. It must not be a side door."""
        base = self._serve(session_token="s3cret", trust_loopback=False)
        with self.assertRaises(error.HTTPError) as caught:
            self._get(base, "/api/file-graph/scene")
        self.assertEqual(caught.exception.code, 404)

    def test_the_scene_endpoint_opens_with_a_session(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        self.assertIsNotNone(self._get(base, "/api/file-graph/scene",
                                       token="s3cret"))

    def test_loopback_still_works_untouched_by_default(self):
        """Every existing local workflow must keep working."""
        base = self._serve()
        payload = self._get(base, "/api/snapshot")
        self.assertFalse(payload.get("sealed"))
        self.assertEqual(payload["file_graph"]["item_count"], 796684)

    def test_no_token_configured_seals_a_remote_caller(self):
        base = self._serve(trust_loopback=False)
        self.assertTrue(self._get(base, "/api/snapshot")["sealed"])
