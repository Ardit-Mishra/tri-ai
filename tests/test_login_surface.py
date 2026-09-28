"""A session boundary nobody can cross is not a boundary, it is an outage.

Step 1 sealed private material behind a session token. Two things were then
found by looking at how the Cortex is actually reached, rather than at the
code:

**The deployment defeats the rule.** `tailscale serve` proxies the tailnet to
`http://127.0.0.1:3026`, so every tailnet visitor arrives at this server with
`client_address == 127.0.0.1`. "Loopback is the operator" therefore authorized
the whole tailnet. 1,260 tests passed and the live dashboard was exactly as
open as before, because this is a fact about the topology and not about the
code. The fix is not a cleverer guess at who is behind the proxy - a local
process can forge anything a proxy can send. It is to stop guessing: when the
operator asks for a session, *everyone* presents one, and the local path and
the remote path become the same path.

**No browser could ever authenticate.** A phone cannot set `X-Kaya-Token`,
and nothing set the cookie. Sealing every route without a way in makes the
dashboard unreachable rather than private.

So: one pure function deciding whether a session is required, and a login
surface that a phone can actually use.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard import kaya_web  # noqa: E402
from test_dashboard_web import fixture_snapshot as _snapshot  # noqa: E402


class SessionConfigTest(unittest.TestCase):
    """Requiring a session must switch loopback trust off in the same breath.

    Keeping them as two independent settings is what allowed the deployed
    server to be configured with a token and still trust every proxied
    caller. One function decides both, so they cannot disagree.
    """

    def test_no_token_configured_leaves_the_local_workflow_alone(self):
        token, trust_loopback = kaya_web.session_config({})
        self.assertIsNone(token)
        self.assertTrue(trust_loopback)

    def test_a_configured_token_stops_trusting_loopback(self):
        token, trust_loopback = kaya_web.session_config(
            {"KAYA_SESSION_TOKEN": "s3cret"})
        self.assertEqual(token, "s3cret")
        self.assertFalse(
            trust_loopback,
            "a proxied tailnet visitor arrives on loopback; trusting it "
            "hands the dashboard to the whole tailnet",
        )

    def test_a_blank_token_is_not_a_configured_one(self):
        self.assertEqual(kaya_web.session_config({"KAYA_SESSION_TOKEN": "  "}),
                         (None, True))

    def test_the_token_is_read_from_the_environment_not_the_command_line(self):
        """A command line is readable by any local process; an env var is not.

        Verified on this machine: `Get-CimInstance Win32_Process` printed the
        full command line of the running dashboard.
        """
        import inspect
        source = inspect.getsource(kaya_web.main)
        self.assertNotIn("--session-token", source)


class _ServerCase(unittest.TestCase):
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

    def _post_login(self, base, token, *, forwarded_proto=None):
        body = f"token={token}".encode("utf-8")
        req = request.Request(base + "/login", data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        if forwarded_proto:
            req.add_header("X-Forwarded-Proto", forwarded_proto)

        class _NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_kw):
                return None

        opener = request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, response.headers
        except error.HTTPError as caught:
            with caught:
                return caught.code, caught.headers

    def _snapshot_with(self, base, cookie=None):
        req = request.Request(base + "/api/snapshot")
        if cookie:
            req.add_header("Cookie", cookie)
        with request.urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))


class LoginSurfaceTest(_ServerCase):
    # --- the way in --------------------------------------------------

    def test_the_login_page_is_reachable_while_everything_else_is_sealed(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        with request.urlopen(base + "/login", timeout=3) as response:
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
        self.assertIn('type="password"', page)

    def test_the_login_page_never_contains_the_token(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        with request.urlopen(base + "/login", timeout=3) as response:
            self.assertNotIn("s3cret", response.read().decode("utf-8"))

    def test_the_right_token_sets_a_session_cookie(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        status, headers = self._post_login(base, "s3cret")
        self.assertIn(status, (302, 303))
        self.assertIn("kaya_session=s3cret", headers.get("Set-Cookie", ""))

    def test_that_cookie_actually_unseals_the_dashboard(self):
        """The end-to-end claim: a phone can get in and see its own data."""
        base = self._serve(session_token="s3cret", trust_loopback=False)
        _, headers = self._post_login(base, "s3cret")
        cookie = headers["Set-Cookie"].split(";")[0]
        payload = self._snapshot_with(base, cookie)
        self.assertFalse(payload.get("sealed"))
        self.assertEqual(payload["file_graph"]["item_count"], 796684)

    # --- and the ways that must stay shut --------------------------------

    def test_a_wrong_token_sets_no_cookie(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        status, headers = self._post_login(base, "guess")
        self.assertNotIn("kaya_session", headers.get("Set-Cookie", ""))
        self.assertNotIn(status, (302, 303))

    def test_login_is_refused_outright_when_no_token_is_configured(self):
        """Fail closed: an unconfigured deployment has no password, so it
        must not be given one by whoever asks first."""
        base = self._serve(trust_loopback=False)
        status, headers = self._post_login(base, "anything")
        self.assertNotIn("kaya_session", headers.get("Set-Cookie", ""))
        self.assertNotIn(status, (302, 303))

    def test_a_refused_post_leaves_the_connection_usable(self):
        """Keep-alive: a reply sent before the body is read aborts the next
        request on that connection. The refusing paths are where forgetting
        to drain is easiest, and this only showed up under the full suite."""
        base = self._serve(trust_loopback=False)
        self._post_login(base, "anything")
        with request.urlopen(base + "/api/snapshot", timeout=3) as response:
            self.assertEqual(response.status, 200)

    def test_the_card_does_not_overflow_a_phone_screen(self):
        """The form's padding sat outside its width, so the card ran off the
        right edge of a 375px screen - on the one device this page exists
        for. Seen in a browser at phone width, not by a test."""
        page = kaya_web._login_page().decode("utf-8")
        form = page.split("form {")[1].split("}")[0]
        self.assertIn("box-sizing:border-box", form)

    def test_the_cookie_is_httponly_so_a_script_cannot_read_it(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        _, headers = self._post_login(base, "s3cret")
        self.assertIn("HttpOnly", headers["Set-Cookie"])

    def test_the_cookie_is_samesite_so_another_site_cannot_ride_it(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        _, headers = self._post_login(base, "s3cret")
        self.assertIn("SameSite=", headers["Set-Cookie"])

    def test_the_cookie_is_secure_when_the_request_arrived_over_https(self):
        """Tailscale Serve terminates TLS and forwards over plain loopback.
        Without this the session token would be sent back in clear text on
        any later plain-http request to the same host."""
        base = self._serve(session_token="s3cret", trust_loopback=False)
        _, headers = self._post_login(base, "s3cret", forwarded_proto="https")
        self.assertIn("Secure", headers["Set-Cookie"])

    def test_logging_out_clears_the_session(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)
        req = request.Request(base + "/logout", data=b"", method="POST")

        class _NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_kw):
                return None

        try:
            with request.build_opener(_NoRedirect).open(req, timeout=3) as done:
                headers = done.headers
        except error.HTTPError as caught:
            with caught:
                headers = caught.headers
        self.assertIn("kaya_session=", headers.get("Set-Cookie", ""))
        self.assertIn("Max-Age=0", headers.get("Set-Cookie", ""))


class FormDecoderTest(unittest.TestCase):
    """The urlencoded decoder is hand-rolled, so it is pinned by example.

    `urllib` is banned wholesale from this module by the read-only boundary
    test, so the form body is decoded by hand. A first version read a
    truncated `%C` at the end of a body as the byte 0x0C instead of the
    literal text, which is the kind of quiet difference a session key would
    fail on with no message worth reading.
    """

    def test_it_decodes_what_a_browser_actually_sends(self):
        for body, expected in (
            ("token=abc", "abc"),
            ("x=1&token=hello+world", "hello world"),
            ("token=a%2Fb%3Dc", "a/b=c"),
            ("token=caf%C3%A9", "café"),
            ("token=", ""),
            ("other=1", ""),
        ):
            with self.subTest(body=body):
                self.assertEqual(kaya_web._form_field(body, "token"), expected)

    def test_a_broken_escape_stays_literal_rather_than_becoming_a_byte(self):
        for body, expected in (
            ("token=%zz", "%zz"),
            ("token=trailing%", "trailing%"),
            ("token=%C", "%C"),
        ):
            with self.subTest(body=body):
                self.assertEqual(kaya_web._form_field(body, "token"), expected)


if __name__ == "__main__":
    unittest.main()


class SealedLandingTest(_ServerCase):
    """A locked phone must be shown the door, not an empty room."""

    def test_a_sealed_visitor_landing_on_the_root_is_sent_to_login(self):
        base = self._serve(session_token="s3cret", trust_loopback=False)

        class _NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_kw):
                return None

        try:
            with request.build_opener(_NoRedirect).open(base + "/", timeout=3) as done:
                status, headers = done.status, done.headers
        except error.HTTPError as caught:
            with caught:
                status, headers = caught.code, caught.headers
        self.assertIn(status, (302, 303))
        self.assertEqual(headers.get("Location"), "/login")

    def test_an_authorized_visitor_still_gets_the_dashboard(self):
        base = self._serve()
        with request.urlopen(base + "/", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("<html", response.read().decode("utf-8").lower())
