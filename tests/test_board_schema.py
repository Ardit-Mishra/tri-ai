"""Phase 1, criteria 1-2: the verify columns exist, are idempotent, and a task
row records everything a worker needs to execute and judge it."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402


class VerifyColumnMigration(BoardTestCase):
    def columns(self) -> set[str]:
        return {
            r["name"]
            for r in self.conn.execute("PRAGMA table_info(tasks)").fetchall()
        }

    def test_columns_present_after_connect(self):
        cols = self.columns()
        for name, _ddl in board.VERIFY_COLUMNS:
            self.assertIn(name, cols)

    def test_rerunning_the_migration_is_a_noop(self):
        # connect() already migrated. A second and third pass must add nothing,
        # raise nothing, and leave the column list unchanged.
        before = self.columns()
        second = board.migrate(self.conn)
        third = board.migrate(self.conn)
        self.assertEqual(second, {n: False for n, _ in board.VERIFY_COLUMNS})
        self.assertEqual(third, {n: False for n, _ in board.VERIFY_COLUMNS})
        self.assertEqual(self.columns(), before)

    def test_migrating_a_table_that_already_owns_the_name_raises(self):
        # The real scenario: a future Hermes release ships its own
        # verify_command with different semantics. add_column_if_missing
        # swallows SQLite's "duplicate column name", so without the type guard
        # migrate() would report success and we would then read and write a
        # column that means something else - the board still working while
        # verification quietly means nothing.
        #
        # Build a tasks table that already holds that name at the wrong type and
        # run the real migrate() against it. This must raise, not proceed.
        probe_path = self.tmp / "collision.db"
        probe = sqlite3.connect(probe_path)
        probe.row_factory = sqlite3.Row
        self.addCleanup(probe.close)
        probe.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, verify_command INTEGER)"
        )
        probe.commit()

        with self.assertRaises(board.SchemaCollision) as caught:
            board.migrate(probe)
        self.assertIn("verify_command", str(caught.exception))

        # The guard fired before writing: the foreign column is untouched, and
        # none of our other columns were added to a table we do not own.
        cols = board._column_types(probe, "tasks")
        self.assertEqual(cols["verify_command"], "INTEGER")
        self.assertNotIn("verify_timeout", cols)
        self.assertNotIn("expected_artifacts", cols)

    def test_a_declaration_that_disagrees_with_the_live_schema_raises(self):
        # The same guard from the other side: our own table is correct, but the
        # declaration changed under us (a retype in VERIFY_COLUMNS that nobody
        # migrated). Also proves a rejected migration leaves nothing corrupted.
        original = board.VERIFY_COLUMNS
        board.VERIFY_COLUMNS = (("verify_command", "verify_command INTEGER"),)
        try:
            with self.assertRaises(board.SchemaCollision):
                board.migrate(self.conn)
        finally:
            board.VERIFY_COLUMNS = original

        self.assertEqual(
            board.migrate(self.conn), {n: False for n, _ in board.VERIFY_COLUMNS}
        )

    def test_kernel_migration_pass_does_not_drop_our_columns(self):
        # The Hermes install updates itself; the kernel re-runs its own schema
        # and migration pass on every fresh connect. Ours must survive it.
        self.kb.init_db(self.db_path)
        with self.kb.connect_closing(self.db_path) as conn2:
            cols = {
                r["name"] for r in conn2.execute("PRAGMA table_info(tasks)").fetchall()
            }
        for name, _ddl in board.VERIFY_COLUMNS:
            self.assertIn(name, cols)


class TaskRowRecordsTheWholeSpec(BoardTestCase):
    def test_row_carries_repo_prompt_verify_deps_and_artifacts(self):
        repo = self.tmp / "repo"
        repo.mkdir()

        parent = board.create_task(
            self.conn,
            title="build",
            prompt="run the build",
            verify_command="npm run build",
            repo=repo,
        )
        child = board.create_task(
            self.conn,
            title="test",
            prompt="run the suite",
            verify_command="npm test",
            repo=repo,
            verify_timeout=600,
            expected_artifacts=["coverage/lcov.info"],
            parents=[parent],
        )

        spec = board.verify_spec(self.conn, child)
        self.assertEqual(spec["verify_command"], "npm test")
        self.assertEqual(spec["verify_timeout"], 600)
        self.assertEqual(spec["expected_artifacts"], ["coverage/lcov.info"])
        self.assertEqual(Path(spec["repo"]), repo)
        self.assertEqual(spec["prompt"], "run the suite")

        # The dependency is a real task_links edge, not a field we invented.
        self.assertEqual(self.kb.parent_ids(self.conn, child), [parent])
        # ...and it gates promotion: the child is not claimable yet.
        self.assertEqual(self.task_row(child)["status"], "todo")
        self.assertEqual(self.task_row(parent)["status"], "ready")

    def test_expected_artifacts_round_trip_as_json(self):
        tid = board.create_task(
            self.conn,
            title="audit",
            prompt="audit deps",
            verify_command="npm audit --audit-level=high",
            expected_artifacts=["audit.json", "audit.txt"],
        )
        raw = self.task_row(tid)["expected_artifacts"]
        self.assertEqual(json.loads(raw), ["audit.json", "audit.txt"])


class UnverifiableTasksAreRefusedAtWriteTime(BoardTestCase):
    def test_missing_verify_command_raises_before_any_row_is_written(self):
        before = self.conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        for bad in (None, "", "   "):
            with self.assertRaises(ValueError):
                board.create_task(
                    self.conn,
                    title="rewrite the docs",
                    prompt="make the prose nicer",
                    verify_command=bad,
                )
        after = self.conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        self.assertEqual(after, before)

    def test_no_row_is_ever_visible_as_ready_without_a_verify_command(self):
        # The guard above rejects before writing, but rejecting early is only half
        # the property. The row and its verify command must also land in ONE
        # transaction: two commits leave a window where the task is `ready` with
        # verify_command NULL, and a worker polling in that window can claim an
        # unverifiable task. Force a failure after the kernel insert and assert
        # the whole thing rolled back.
        real_dumps = board.json.dumps

        def explode(*a, **kw):
            raise RuntimeError("boom, mid-create")

        board.json.dumps = explode
        try:
            with self.assertRaises(RuntimeError):
                board.create_task(
                    self.conn,
                    title="half-written",
                    prompt="x",
                    verify_command="true",
                )
        finally:
            board.json.dumps = real_dumps

        orphans = self.conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE verify_command IS NULL"
        ).fetchone()["c"]
        self.assertEqual(orphans, 0, "a task row survived without a verify command")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"], 0
        )

    def test_nonpositive_verify_timeout_is_refused(self):
        with self.assertRaises(ValueError):
            board.create_task(
                self.conn,
                title="x",
                prompt="x",
                verify_command="true",
                verify_timeout=0,
            )


if __name__ == "__main__":
    unittest.main()
