"""Guessing the session key has to be bounded, or a typeable key is not safe.

A 23-character key was strong enough that nothing else mattered. It was also
unpleasant to type on a phone, which is the only device this login exists
for - and a login people avoid using is a login they will turn off. Shorten
the key and the arithmetic changes: the bound on guessing stops being the
key length and starts being the server.

Two facts about this deployment rule out the usual answers:

* the server is a `ThreadingHTTPServer`, so delaying a failed reply throttles
  nothing - an attacker runs the guesses in parallel and the delays overlap.
* `tailscale serve` proxies every caller from `127.0.0.1`, so per-address
  limits would either cover everybody or nobody.

So the gate is global and refuses immediately rather than sleeping: once
tripped, every attempt is turned away without the key being examined at all,
and parallelism buys nothing. It expires on its own, with a cap, because a
lockout the operator cannot wait out is its own outage.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard import kaya_web  # noqa: E402
from test_dashboard_web import fixture_snapshot as _snapshot  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ThrottleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.throttle = kaya_web.LoginThrottle(now=self.clock)

    def _fail(self, times: int) -> None:
        for _ in range(times):
            self.throttle.record_failure()

    def test_an_honest_first_attempt_is_never_delayed(self):
        self.assertFalse(self.throttle.blocked())

    def test_a_few_typos_do_not_lock_the_operator_out(self):
        """Typing a key on a phone goes wrong sometimes; that is not an attack."""
        self._fail(3)
        self.assertFalse(self.throttle.blocked())

    def test_sustained_guessing_trips_the_gate(self):
        self._fail(kaya_web.LOGIN_FREE_ATTEMPTS + 1)
        self.assertTrue(self.throttle.blocked())

    def test_the_gate_opens_again_on_its_own(self):
        """A lockout the operator cannot wait out is an outage, not security."""
        self._fail(kaya_web.LOGIN_FREE_ATTEMPTS + 1)
        self.clock.advance(kaya_web.LOGIN_MAX_BLOCK + 1)
        self.assertFalse(self.throttle.blocked())

    def test_each_further_failure_costs_more_than_the_last(self):
        self._fail(kaya_web.LOGIN_FREE_ATTEMPTS + 1)
        first = self.throttle.retry_after()
        self.clock.advance(first + 1)
        self.throttle.record_failure()
        self.assertGreater(self.throttle.retry_after(), first)

    def test_the_wait_is_capped_so_it_cannot_become_permanent(self):
        self._fail(kaya_web.LOGIN_FREE_ATTEMPTS + 40)
        self.assertLessEqual(self.throttle.retry_after(), kaya_web.LOGIN_MAX_BLOCK)

    def test_getting_in_clears_the_record(self):
        self._fail(kaya_web.LOGIN_FREE_ATTEMPTS + 1)
        self.throttle.record_success()
        self.assertFalse(self.throttle.blocked())
        self.assertEqual(self.throttle.retry_after(), 0)

    def test_it_is_safe_to_share_between_threads(self):
        """Every request runs on its own thread; a lost count is a free guess."""
        barrier = threading.Barrier(8)

        def guess() -> None:
            barrier.wait()
            for _ in range(25):
                self.throttle.record_failure()

        threads = [threading.Thread(target=guess) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.throttle.failures, 200)


class ThrottleIsWiredTest(unittest.TestCase):
    """The decision being right is not the same as the server asking it.

    Four modules in this codebase were complete, tested and called by
    nothing. This one drives real HTTP so it cannot join them.
    """

    def _serve(self, **kw):
        server = kaya_web.create_server(
            host="127.0.0.1", port=0, snapshot_fn=_snapshot,
            private_graph_fn=lambda: {"status": "indexed", "synthetic": False,
                                      "item_count": 1, "sources": [], "items": [],
                                      "diagnostic": "metadata only",
                                      "revision": "r1"},
            session_token="s3cret", trust_loopback=False, **kw)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def _login(self, base, token):
        req = request.Request(base + "/login", data=f"token={token}".encode(),
                              method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        class _NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *_a, **_kw):
                return None

        try:
            with request.build_opener(_NoRedirect).open(req, timeout=3) as done:
                return done.status, done.headers
        except error.HTTPError as caught:
            with caught:
                return caught.code, caught.headers

    def test_repeated_guessing_starts_being_refused(self):
        base = self._serve()
        for _ in range(kaya_web.LOGIN_FREE_ATTEMPTS + 1):
            self._login(base, "guess")
        status, _ = self._login(base, "guess")
        self.assertEqual(status, 429)

    def test_the_right_key_is_refused_too_while_the_gate_is_shut(self):
        """Otherwise the gate leaks: a refusal that still checks the key
        tells an attacker which guess was the right one."""
        base = self._serve()
        for _ in range(kaya_web.LOGIN_FREE_ATTEMPTS + 1):
            self._login(base, "guess")
        status, headers = self._login(base, "s3cret")
        self.assertEqual(status, 429)
        self.assertNotIn("kaya_session", headers.get("Set-Cookie", ""))

    def test_a_refusal_says_how_long_to_wait(self):
        base = self._serve()
        for _ in range(kaya_web.LOGIN_FREE_ATTEMPTS + 2):
            self._login(base, "guess")
        _, headers = self._login(base, "guess")
        self.assertTrue(int(headers.get("Retry-After", "0")) > 0)

    def test_an_existing_session_is_untouched_by_the_gate(self):
        """Locking the door must not throw out whoever is already inside."""
        base = self._serve()
        for _ in range(kaya_web.LOGIN_FREE_ATTEMPTS + 2):
            self._login(base, "guess")
        req = request.Request(base + "/api/snapshot")
        req.add_header("Cookie", "kaya_session=s3cret")
        with request.urlopen(req, timeout=3) as response:
            import json
            self.assertFalse(json.loads(response.read().decode()).get("sealed"))


if __name__ == "__main__":
    unittest.main()
