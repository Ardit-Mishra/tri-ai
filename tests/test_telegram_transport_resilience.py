"""A lost HTTPS request is a blip, not a dead bot.

On 2026-09-12 the Telegram daemon exited 1 on a single ``TelegramTransportError``
and the supervisor, which then stopped on any child exit, took the worker down
with it mid-run. The daemon now distinguishes a transient transport failure
(retry with backoff) from a persistent one (exit, and let the supervisor decide),
and it never silently swallows either.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interfaces import telegram_daemon as daemon  # noqa: E402


class RunForeverStub:
    """The smallest object that can exercise TelegramDaemon.run_forever.

    ``run_forever`` only ever calls ``self.poll_once``, so binding the real
    method onto this stub tests the actual retry logic rather than a
    re-implementation of it.
    """

    run_forever = daemon.TelegramDaemon.run_forever

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.slept: list[float] = []

    def poll_once(self, *, offset, timeout):
        self.calls += 1
        if not self.outcomes:
            raise StopIteration("test exhausted its scripted outcomes")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class TelegramTransportResilienceTests(unittest.TestCase):
    def test_a_transient_transport_failure_is_retried_rather_than_fatal(self):
        stub = RunForeverStub([
            daemon.TelegramTransportError("Telegram HTTPS request failed: URLError"),
            daemon.TelegramTransportError("Telegram HTTPS request failed: TimeoutError"),
            7,  # the connection comes back and the poll succeeds
            StopIteration("done"),
        ])
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(StopIteration):
            stub.run_forever(poll_timeout=30, sleep=stub.sleep)

        self.assertEqual(stub.slept, [2.0, 4.0], "backoff must double between attempts")
        text = stderr.getvalue()
        self.assertIn("telegram transport failure 1/continuous", text)
        self.assertIn("URLError", text, "the failure class must survive into the log")
        self.assertNotIn("bot", text.lower().split("telegram transport")[0])

    def test_a_successful_poll_clears_the_failure_streak(self):
        stub = RunForeverStub([
            daemon.TelegramTransportError("blip"),
            1,
            daemon.TelegramTransportError("blip"),
            StopIteration("done"),
        ])
        with redirect_stderr(io.StringIO()), self.assertRaises(StopIteration):
            stub.run_forever(poll_timeout=30, sleep=stub.sleep)
        # Both delays are the first-attempt delay: the streak reset in between.
        self.assertEqual(stub.slept, [2.0, 2.0])

    def test_an_explicit_transport_budget_can_still_stop_a_test_or_one_shot_runner(self):
        stub = RunForeverStub([daemon.TelegramTransportError("network down")] * 10)
        with redirect_stderr(io.StringIO()), self.assertRaises(daemon.TelegramTransportError):
            stub.run_forever(poll_timeout=30, max_transport_failures=4, sleep=stub.sleep)
        self.assertEqual(stub.calls, 4)
        self.assertEqual(stub.slept, [2.0, 4.0, 8.0])

    def test_the_daemon_default_keeps_retrying_network_outages(self):
        failures = [daemon.TelegramTransportError("network down")] * 12
        stub = RunForeverStub([*failures, 9, StopIteration("done")])
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(StopIteration):
            stub.run_forever(
                poll_timeout=30,
                backoff_base_seconds=0.01,
                backoff_max_seconds=0.01,
                sleep=stub.sleep,
            )

        self.assertEqual(stub.calls, 14)
        self.assertEqual(len(stub.slept), 12)
        self.assertIn("12/continuous", stderr.getvalue())

    def test_a_permanent_api_failure_is_not_retried(self):
        stub = RunForeverStub([daemon.TelegramPermanentError("unauthorized")])
        with redirect_stderr(io.StringIO()), self.assertRaises(daemon.TelegramPermanentError):
            stub.run_forever(poll_timeout=30, sleep=stub.sleep)
        self.assertEqual(stub.calls, 1)
        self.assertEqual(stub.slept, [])

    def test_backoff_is_capped(self):
        stub = RunForeverStub([daemon.TelegramTransportError("blip")] * 12)
        with redirect_stderr(io.StringIO()), self.assertRaises(daemon.TelegramTransportError):
            stub.run_forever(
                poll_timeout=30, max_transport_failures=9,
                backoff_base_seconds=2.0, backoff_max_seconds=16.0, sleep=stub.sleep,
            )
        self.assertEqual(stub.slept, [2.0, 4.0, 8.0, 16.0, 16.0, 16.0, 16.0, 16.0])

    def test_a_non_transport_error_is_not_retried(self):
        """Only transport failures are transient; a bug must surface at once."""
        stub = RunForeverStub([ValueError("programming error")])
        with self.assertRaises(ValueError):
            stub.run_forever(poll_timeout=30, sleep=stub.sleep)
        self.assertEqual(stub.slept, [])


if __name__ == "__main__":
    unittest.main()


class FourXXClassificationTest(unittest.TestCase):
    """Not every 4xx is permanent, and two of them are the opposite.

    Observed on DESKTOP-JHQ7HJM on 2026-09-25: the daemon exited on HTTP 409
    and the supervisor restarted it eight times in a row. 409 means another
    getUpdates is already polling that bot - which is exactly what happens when
    a second machine, or a stale process, holds the slot. It clears the moment
    the other poller stops, so treating it as fatal turns a passing condition
    into a restart loop that exhausts the supervisor's budget.

    429 is transient by definition; Telegram even sends `retry_after` with it.
    """

    def _raise(self, code: int):
        from urllib import error
        return error.HTTPError("https://api.telegram.org/", code, "e", {}, None)

    def _classify(self, code: int):
        """Run one HTTP status through the real client's error handling."""
        def opener(request, timeout=None):
            raise self._raise(code)
        api = daemon.HttpsTelegramApi("t", opener=opener)
        try:
            api.get_updates(offset=None, timeout=1)
        except daemon.TelegramPermanentError as exc:
            return "permanent", exc
        except daemon.TelegramTransportError as exc:
            return "retryable", exc
        return "no error", None

    def test_409_conflict_is_retryable_not_permanent(self):
        kind, _ = self._classify(409)
        self.assertEqual(kind, "retryable",
                         "409 means another poller holds the slot; it self-resolves")

    def test_429_rate_limited_is_retryable_not_permanent(self):
        kind, _ = self._classify(429)
        self.assertEqual(kind, "retryable")

    def test_401_unauthorized_is_still_permanent(self):
        """A bad token cannot be fixed by waiting, and must stop loudly."""
        kind, _ = self._classify(401)
        self.assertEqual(kind, "permanent")

    def test_404_is_still_permanent(self):
        kind, _ = self._classify(404)
        self.assertEqual(kind, "permanent")

    def _classify_body(self, code: int):
        """Telegram also reports errors as HTTP 200 with ok:false in the body."""
        import json as _json

        class _Resp:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): return False
            def read(self_inner):
                return _json.dumps({"ok": False, "error_code": code}).encode()

        api = daemon.HttpsTelegramApi("t", opener=lambda *a, **k: _Resp())
        try:
            api.get_updates(offset=None, timeout=1)
        except daemon.TelegramPermanentError:
            return "permanent"
        except daemon.TelegramTransportError:
            return "retryable"
        return "no error"

    def test_409_in_the_response_body_is_also_retryable(self):
        """The same rule must hold on both error paths, or the bug survives."""
        self.assertEqual(self._classify_body(409), "retryable")

    def test_429_in_the_response_body_is_also_retryable(self):
        self.assertEqual(self._classify_body(429), "retryable")

    def test_401_in_the_response_body_is_still_permanent(self):
        self.assertEqual(self._classify_body(401), "permanent")
