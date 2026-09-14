"""A claim outlives its TTL only while the agent is demonstrably working.

The gap review found: `worker.py` never wrote `last_heartbeat_at`. It only ever
set it to NULL on release. The kernel extends an expired claim whenever the
worker PID is alive, and its staleness backstop reads

    heartbeat_stale = hb is not None and (now - hb) > MAX_STALE

so a NULL heartbeat is never stale and a live worker holds its claim forever -
wedged or not. The backstop cannot engage until a heartbeat exists to go stale.

The trap in fixing it is that the obvious fix is worse than the bug. A thread
that beats on a timer satisfies the kernel while proving nothing: it reports a
healthy worker for a run hung on a dead socket, which is exactly the case the
backstop exists to catch. The heartbeat has to be *earned*.

So the property under test is not "the worker heartbeats". It is: **a beat
happens if and only if the agent produced output since the last one.** These
tests drive the real `_heartbeat_while_active` against a real board and assert
both directions, because only one of them is the bug.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402


class HeartbeatIsEarned(BoardTestCase):
    """`_heartbeat_while_active` against the real kernel, at speed."""

    INTERVAL = 0.05

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        self.task_id = board.create_task(
            self.conn, title="long job", prompt="work",
            verify_command="python verify.py", repo=self.repo, verify_timeout=60,
        )
        claimed = self.kb.claim_task(
            self.conn, self.task_id, claimer=worker.worker_id(),
        )
        self.assertIsNotNone(claimed)
        self.run_id = claimed.current_run_id

    def heartbeat_at(self):
        row = self.conn.execute(
            "SELECT last_heartbeat_at FROM tasks WHERE id = ?", (self.task_id,)
        ).fetchone()
        return row["last_heartbeat_at"]

    def test_the_field_starts_null_which_is_the_whole_problem(self):
        self.assertIsNone(
            self.heartbeat_at(),
            "a claim begins with no heartbeat; the kernel reads NULL as never-stale",
        )

    def test_output_earns_a_beat(self):
        activity = worker._AgentActivity()
        with worker._heartbeat_while_active(
            self.task_id, self.run_id, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
        ):
            for _ in range(6):
                activity.touch()
                time.sleep(self.INTERVAL)
        self.assertIsNotNone(
            self.heartbeat_at(),
            "the agent produced output throughout and no heartbeat was recorded",
        )

    def test_silence_earns_nothing(self):
        # The case the whole fix exists for: the worker is alive, the agent is
        # not producing. A timer-based heartbeat would report health here.
        activity = worker._AgentActivity()
        with worker._heartbeat_while_active(
            self.task_id, self.run_id, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
        ):
            time.sleep(self.INTERVAL * 8)
        self.assertIsNone(
            self.heartbeat_at(),
            "a wedged agent was reported as alive - the heartbeat is a timer, "
            "not evidence",
        )

    def test_a_beat_stops_when_the_output_stops(self):
        # Output, then silence. The heartbeat must freeze at the last real
        # activity so the kernel's staleness window can start running.
        activity = worker._AgentActivity()
        with worker._heartbeat_while_active(
            self.task_id, self.run_id, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
        ):
            for _ in range(4):
                activity.touch()
                time.sleep(self.INTERVAL)
            beat_while_working = self.heartbeat_at()
            time.sleep(self.INTERVAL * 8)
            beat_after_silence = self.heartbeat_at()

        self.assertIsNotNone(beat_while_working)
        self.assertEqual(
            beat_while_working, beat_after_silence,
            "the heartbeat kept advancing after the agent went quiet",
        )

    def test_it_never_beats_for_a_run_it_no_longer_owns(self):
        # expected_run_id guards this: if the claim moved, a stale thread must
        # not refresh someone else's row.
        activity = worker._AgentActivity()
        with worker._heartbeat_while_active(
            self.task_id, self.run_id + 999, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
        ):
            for _ in range(6):
                activity.touch()
                time.sleep(self.INTERVAL)
        self.assertIsNone(
            self.heartbeat_at(),
            "a heartbeat was written for a run this worker does not own",
        )

    def test_the_thread_is_always_joined(self):
        before = {t.name for t in threading.enumerate()}
        activity = worker._AgentActivity()
        with worker._heartbeat_while_active(
            self.task_id, self.run_id, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
        ):
            activity.touch()
            time.sleep(self.INTERVAL * 2)
        time.sleep(self.INTERVAL * 2)
        after = {t.name for t in threading.enumerate()}
        self.assertNotIn("claim-heartbeat", after - before,
                         "the heartbeat thread outlived the run")

    def test_a_board_failure_does_not_reach_the_run(self):
        # Losing a beat is a missed extension, never a reason to fail a task.
        activity = worker._AgentActivity()
        with mock.patch.object(
            board.kanban(), "heartbeat_worker",
            side_effect=RuntimeError("board is on fire"),
        ):
            with worker._heartbeat_while_active(
                self.task_id, self.run_id, activity,
            db_path=str(self.db_path), interval=self.INTERVAL,
            ):
                activity.touch()
                time.sleep(self.INTERVAL * 3)
        # Reaching here without raising is the assertion.
        self.assertIsNone(self.heartbeat_at())


class ActivityIsCheapAndThreadSafe(unittest.TestCase):
    """It is touched from the pipe-draining thread, which must never block."""

    def test_touch_advances_the_reading(self):
        a = worker._AgentActivity()
        first = a.at
        time.sleep(0.01)
        a.touch()
        self.assertGreater(a.at, first)

    def test_concurrent_touches_do_not_corrupt_it(self):
        a = worker._AgentActivity()
        done = threading.Event()

        def hammer():
            for _ in range(2000):
                a.touch()
                a.at
            done.set()

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertTrue(done.is_set())
        self.assertIsInstance(a.at, float)


class AgentOutputReachesTheCallback(unittest.TestCase):
    """`run_agent` must report output as it arrives, not once at the end."""

    def _fake_contained(self, lines, exit_code=0):
        class FakeProc:
            returncode = exit_code

            def __init__(self):
                self.stdout = iter(lines)

            def wait(self, timeout=None):
                return exit_code

        class FakeContained:
            def __init__(self):
                self.proc = FakeProc()

            def terminate_tree(self, grace=None):
                return True, []

            def close(self):
                pass

        return FakeContained()

    def test_every_chunk_fires_the_callback(self):
        beats = []
        lines = ["one\n", "two\n", "three\n"]
        with mock.patch.object(
            executor, "spawn_contained",
            side_effect=lambda *a, **k: self._fake_contained(lines),
        ):
            result = executor.run_agent(
                Path.cwd(), "p", timeout=30, on_activity=lambda: beats.append(1),
            )
        self.assertEqual(len(beats), len(lines))
        self.assertEqual(result.output, "one\ntwo\nthree\n")

    def test_output_is_still_captured_whole_without_a_callback(self):
        lines = ["alpha\n", "beta\n"]
        with mock.patch.object(
            executor, "spawn_contained",
            side_effect=lambda *a, **k: self._fake_contained(lines),
        ):
            result = executor.run_agent(Path.cwd(), "p", timeout=30)
        self.assertEqual(result.output, "alpha\nbeta\n")
        self.assertEqual(result.exit_code, 0)

    def test_a_raising_callback_cannot_break_the_run(self):
        def boom():
            raise RuntimeError("callback exploded")

        with mock.patch.object(
            executor, "spawn_contained",
            side_effect=lambda *a, **k: self._fake_contained(["x\n"]),
        ):
            result = executor.run_agent(
                Path.cwd(), "p", timeout=30, on_activity=boom,
            )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("x", result.output)


class TheKernelCanNowReclaimAWedgedWorker(BoardTestCase):
    """End to end: the gap was that this was impossible, not that beats were missing.

    `release_stale_claims` extends an expired claim whenever the worker PID is
    alive, and only declines when `last_heartbeat_at` is present AND older than
    `DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS`. With the field permanently
    NULL the second condition could never hold, so a live-but-wedged worker was
    unreclaimable. These drive the real kernel function with this process as
    the live worker.
    """

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        self.task_id = board.create_task(
            self.conn, title="wedged", prompt="work",
            verify_command="python verify.py", repo=self.repo, verify_timeout=60,
        )
        claimed = self.kb.claim_task(
            self.conn, self.task_id, claimer=worker.worker_id(),
        )
        self.run_id = claimed.current_run_id
        # This process is the worker, and it is genuinely alive - so PID
        # liveness alone can never distinguish working from wedged.
        self.conn.execute(
            "UPDATE tasks SET worker_pid = ? WHERE id = ?",
            (os.getpid(), self.task_id),
        )
        self.conn.commit()

    def expire_claim(self) -> None:
        past = int(time.time()) - 120
        self.conn.execute(
            "UPDATE tasks SET claim_expires = ? WHERE id = ?", (past, self.task_id),
        )
        self.conn.execute(
            "UPDATE task_runs SET claim_expires = ? WHERE id = ?", (past, self.run_id),
        )
        self.conn.commit()

    def set_heartbeat(self, seconds_ago: int) -> None:
        when = int(time.time()) - seconds_ago
        self.conn.execute(
            "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?", (when, self.task_id),
        )
        self.conn.commit()

    def status(self) -> str:
        return self.conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (self.task_id,)
        ).fetchone()["status"]

    def last_event(self) -> str:
        return self.conn.execute(
            "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1",
            (self.task_id,),
        ).fetchone()["kind"]

    def tick(self) -> int:
        # signal_fn is a no-op so this process survives "termination" - which is
        # what makes the deferral path observable without killing the test run.
        return board.release_stale_claims(self.conn, signal_fn=lambda *a, **k: None)

    def test_a_working_agents_claim_is_extended(self):
        self.expire_claim()
        self.set_heartbeat(seconds_ago=30)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.last_event(), "claim_extended")
        self.assertEqual(self.status(), "running", "a working agent was interrupted")

    def test_a_wedged_agent_stops_being_extended(self):
        # The gap, closed. A live PID with a stale beat no longer renews: the
        # kernel drops into the termination path instead. Release itself is
        # deferred only because this very process is the "worker" and is alive -
        # never releasing beside a live worker is a separate, correct rule, and
        # in production the worker is actually terminated here.
        self.expire_claim()
        self.set_heartbeat(
            seconds_ago=self.kb.DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS + 600,
        )
        self.tick()
        self.assertEqual(
            self.last_event(), "reclaim_deferred",
            "a wedged worker had its claim renewed - the staleness backstop "
            "never engaged",
        )

    def test_without_any_heartbeat_a_wedged_worker_is_renewed_forever(self):
        # Why the heartbeat is required at all, pinned so the regression is
        # visible rather than silent: with NULL there is nothing to go stale,
        # so this is indistinguishable from a healthy worker no matter how long
        # it has been hung.
        self.expire_claim()
        self.assertIsNone(
            self.conn.execute(
                "SELECT last_heartbeat_at FROM tasks WHERE id = ?", (self.task_id,)
            ).fetchone()["last_heartbeat_at"]
        )
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.last_event(), "claim_extended")



if __name__ == "__main__":
    unittest.main()
