"""Work that cannot move must say so, and keep saying so.

Two failures found by draining a real build on the desktop:

**A cancelled parent strands its children forever.** `Write automated tests`
waits on two cancelled tasks, and `Integrate secure payment processing`
waits on that. Cancelled tasks never complete, so those two sit in `todo`
permanently. The board looks busy; nothing will ever run.

**A stall is announced once and then never again.** Completion notices are
one per (task, run), deduped by a receipt. A stalled task produces no new
runs, so it produces no new notices. The desktop sat still for three days
after a single message on the evening it stopped.

The detectors here are read-only and do not touch the Hermes kanban
kernel's readiness rule, which this project uses and does not edit. A
stranded task is made *visible*, not force-run: its input genuinely does
not exist, and running it anyway would produce confident garbage.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import standstill  # noqa: E402

SCHEMA = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT);
CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, ended_at INTEGER);
"""


def board(tasks, links=(), runs=()):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO tasks VALUES (?,?,?)", tasks)
    conn.executemany("INSERT INTO task_links VALUES (?,?)", links)
    conn.executemany("INSERT INTO task_runs (task_id, ended_at) VALUES (?,?)", runs)
    return conn


class StrandedTest(unittest.TestCase):
    def test_a_child_of_a_cancelled_task_is_stranded(self):
        conn = board(
            [("a", "Build the backend", "cancelled"),
             ("b", "Write automated tests", "todo")],
            [("a", "b")])
        found = standstill.stranded(conn)
        self.assertEqual([item.task_id for item in found], ["b"])
        self.assertEqual(found[0].blocked_by_title, "Build the backend")
        self.assertEqual(found[0].blocked_by_status, "cancelled")

    def test_it_follows_the_chain_more_than_one_step(self):
        """`Integrate payments` waits on `Write tests`, which waits on a
        cancelled task. Only reporting direct parents would miss it."""
        conn = board(
            [("a", "Build the backend", "cancelled"),
             ("b", "Write automated tests", "todo"),
             ("c", "Integrate payments", "todo")],
            [("a", "b"), ("b", "c")])
        self.assertEqual(sorted(i.task_id for i in standstill.stranded(conn)),
                         ["b", "c"])

    def test_a_child_of_a_done_parent_is_not_stranded(self):
        conn = board([("a", "Design", "done"), ("b", "Implement", "todo")],
                     [("a", "b")])
        self.assertEqual(standstill.stranded(conn), [])

    def test_a_child_waiting_on_ordinary_unfinished_work_is_not_stranded(self):
        """Waiting is normal. Only a parent that can never finish counts."""
        conn = board([("a", "Design", "todo"), ("b", "Implement", "todo")],
                     [("a", "b")])
        self.assertEqual(standstill.stranded(conn), [])

    def test_a_finished_child_is_not_reported(self):
        conn = board([("a", "Build", "cancelled"), ("b", "Tests", "done")],
                     [("a", "b")])
        self.assertEqual(standstill.stranded(conn), [])

    def test_a_link_to_a_task_that_does_not_exist_is_reported(self):
        """An edge to a missing task is unsatisfiable in exactly the same
        way, and silently so."""
        conn = board([("b", "Tests", "todo")], [("ghost", "b")])
        found = standstill.stranded(conn)
        self.assertEqual([i.task_id for i in found], ["b"])
        self.assertEqual(found[0].blocked_by_status, "missing")

    def test_a_cycle_does_not_hang_the_walk(self):
        conn = board([("a", "A", "todo"), ("b", "B", "todo")],
                     [("a", "b"), ("b", "a")])
        self.assertEqual(standstill.stranded(conn), [])


class StalledTest(unittest.TestCase):
    # A realistic epoch: the detector ignores timestamps from
    # before the project existed, so a toy value would be
    # filtered out and the test would prove nothing.
    NOW = 1_790_000_000

    def test_work_waiting_with_no_recent_run_is_stalled(self):
        conn = board([("a", "Implement", "todo")], runs=[("a", self.NOW - 90_000)])
        report = standstill.stalled(conn, now=self.NOW, quiet_for=3600)
        self.assertTrue(report.is_stalled)
        self.assertEqual(report.waiting, 1)
        self.assertGreater(report.quiet_seconds, 3600)

    def test_a_board_that_ran_recently_is_not_stalled(self):
        conn = board([("a", "Implement", "todo")], runs=[("a", self.NOW - 60)])
        self.assertFalse(standstill.stalled(conn, now=self.NOW, quiet_for=3600).is_stalled)

    def test_an_empty_board_is_not_stalled(self):
        """Nothing waiting is not a stall; it is a finished board."""
        conn = board([("a", "Done thing", "done")], runs=[("a", self.NOW - 90_000)])
        self.assertFalse(standstill.stalled(conn, now=self.NOW, quiet_for=3600).is_stalled)

    def test_a_running_task_is_not_a_stall_however_long_it_takes(self):
        """Work in progress is not a standstill, and calling it one trains
        the operator to ignore the message.

        The board must also hold a *waiting* task, or this passes for the
        wrong reason: with nothing waiting the report is false anyway, and
        deleting the running check would not be noticed."""
        conn = board([("a", "Slow thing", "running"),
                      ("b", "Queued behind it", "todo")],
                     runs=[("a", self.NOW - 90_000)])
        report = standstill.stalled(conn, now=self.NOW, quiet_for=3600)
        self.assertFalse(report.is_stalled)
        self.assertEqual(report.waiting, 1, "the waiting task must be counted")

    def test_a_board_that_never_ran_anything_is_not_called_stalled(self):
        """No run history is not evidence. A board created a moment ago with
        work queued is indistinguishable from one abandoned for a year, and
        guessing produced "nothing has finished for 497408h" - the shape of
        an alert nobody will trust twice."""
        conn = board([("a", "Implement", "todo")])
        report = standstill.stalled(conn, now=self.NOW, quiet_for=3600)
        self.assertFalse(report.is_stalled)
        self.assertEqual(report.quiet_seconds, 0)


    def test_an_implausible_timestamp_is_not_evidence_either(self):
        """A fixture run that "ended" at t=1 is not a real run. Measuring
        from it produced "nothing has finished for 497409h"."""
        conn = board([("a", "Implement", "todo")], runs=[("a", 1)])
        report = standstill.stalled(conn, now=self.NOW, quiet_for=3600)
        self.assertFalse(report.is_stalled)
        self.assertEqual(report.quiet_seconds, 0)


class DigestTest(unittest.TestCase):
    # A realistic epoch: the detector ignores timestamps from
    # before the project existed, so a toy value would be
    # filtered out and the test would prove nothing.
    NOW = 1_790_000_000

    def test_nothing_wrong_produces_no_message(self):
        conn = board([("a", "Done", "done")], runs=[("a", self.NOW - 60)])
        self.assertIsNone(standstill.digest(conn, now=self.NOW, quiet_for=3600))

    def test_it_names_the_task_and_what_is_holding_it(self):
        conn = board(
            [("a", "Build the backend", "cancelled"),
             ("b", "Write automated tests", "todo")],
            [("a", "b")])
        text = standstill.digest(conn, now=self.NOW, quiet_for=3600)
        self.assertIn("Write automated tests", text)
        self.assertIn("Build the backend", text)
        self.assertIn("cancelled", text)

    def test_it_says_how_long_it_has_been_quiet(self):
        conn = board([("a", "Implement", "todo")], runs=[("a", self.NOW - 90_000)])
        text = standstill.digest(conn, now=self.NOW, quiet_for=3600)
        self.assertRegex(text, r"\d+\s*h")


class RepeatTest(unittest.TestCase):
    """Announced once is the bug. Announced every tick is the other bug."""

    # A realistic epoch: the detector ignores timestamps from
    # before the project existed, so a toy value would be
    # filtered out and the test would prove nothing.
    NOW = 1_790_000_000

    def test_the_first_notice_is_sent(self):
        send, _ = standstill.should_send({}, "stuck", now=self.NOW, repeat_after=21600)
        self.assertTrue(send)

    def test_the_same_notice_is_not_repeated_immediately(self):
        _, state = standstill.should_send({}, "stuck", now=self.NOW, repeat_after=21600)
        send, _ = standstill.should_send(state, "stuck", now=self.NOW + 60,
                                         repeat_after=21600)
        self.assertFalse(send)

    def test_the_same_notice_is_repeated_after_the_interval(self):
        """Silence after one message is what let three days pass."""
        _, state = standstill.should_send({}, "stuck", now=self.NOW, repeat_after=21600)
        send, _ = standstill.should_send(state, "stuck", now=self.NOW + 21601,
                                         repeat_after=21600)
        self.assertTrue(send)

    def test_a_changed_situation_is_announced_at_once(self):
        _, state = standstill.should_send({}, "stuck on A", now=self.NOW,
                                          repeat_after=21600)
        send, _ = standstill.should_send(state, "stuck on B", now=self.NOW + 60,
                                         repeat_after=21600)
        self.assertTrue(send, "a different problem is news, not a repeat")

    def test_state_survives_a_round_trip_through_json(self):
        import json
        _, state = standstill.should_send({}, "stuck", now=self.NOW, repeat_after=21600)
        revived = json.loads(json.dumps(state))
        send, _ = standstill.should_send(revived, "stuck", now=self.NOW + 60,
                                         repeat_after=21600)
        self.assertFalse(send)


if __name__ == "__main__":
    unittest.main()


class ItIsActuallyWiredTest(unittest.TestCase):
    """Four modules in this repo were complete, tested, and called by
    nothing. A detector for silence is the worst possible fifth."""

    def _daemon_source(self) -> str:
        return (Path(__file__).resolve().parents[1]
                / "src" / "interfaces" / "telegram_daemon.py"
                ).read_text(encoding="utf-8")

    def test_the_poll_loop_calls_it_every_tick(self):
        source = self._daemon_source()
        self.assertIn("self.publish_standstill()", source)
        self.assertIn("def publish_standstill", source)

    def test_it_runs_beside_the_completion_notices(self):
        """Same cadence as the notices it exists to supplement."""
        source = self._daemon_source()
        completions = source.index("self.publish_completions()")
        standstill_call = source.index("self.publish_standstill()")
        self.assertLess(abs(standstill_call - completions), 200)

    def test_a_reporting_failure_cannot_kill_the_daemon(self):
        """The daemon's job is to keep talking. A report that raises would
        end the process whose silence this fix exists to prevent."""
        source = self._daemon_source()
        body = source[source.index("def publish_standstill"):]
        body = body[:body.index("def publish_progress")]
        self.assertIn("except Exception", body)

    def test_it_opens_the_board_read_only(self):
        body = self._daemon_source()
        body = body[body.index("def publish_standstill"):]
        self.assertIn("mode=ro", body[:body.index("def publish_progress")])
