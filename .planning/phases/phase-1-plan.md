# Phase 1 Plan — Verified Board Substrate

**Status:** complete (2026-09-08)
**Requirements:** GRAPH-02, GRAPH-04, BOARD-01, BOARD-02, BOARD-03, BOARD-04

## Approach

The kanban kernel is *used, not edited*.

The roadmap said to reuse `hermes_cli/kanban_db.py` directly and add one thing to it: a
`verify_command` column. Inspecting the install changed *where* that column gets added, not
whether. Every package directory in `~/AppData/Local/hermes/hermes-agent` has a
`*.hermes-update-staging` sibling — the install replaces whole package trees when it updates. An
in-place edit to `kanban_db.py` would be silently reverted, and the failure mode is the worst kind:
the board keeps working, the column quietly disappears, and tasks stop being verified.

So the additive migration lives in `src/board.py` and runs against Tri-AI's own board file
(`~/.tri-ai/board.db`, `TRIAI_BOARD_DB`), through the kernel's own `add_column_if_missing` helper.
This is still "reuse the kernel directly" — the claim, lease, heartbeat, reclaim and DAG-promotion
logic is imported and called, never copied. Zero lines of the kernel change.

Two properties make that safe, and both are asserted by tests rather than assumed:

- The kernel's own migration pass (`_migrate_add_optional_columns`) is purely additive.
- `tasks` is not in `_REBUILD_SPECS`, the drift-rebuild list. Only `task_events`, `task_comments`,
  `task_runs` and `kanban_notify_subs` are ever dropped and recreated from canonical DDL, so a
  Hermes update cannot take the column down with a table rebuild.

## What was built

| file | what it is |
|---|---|
| `src/board.py` | the adapter: kernel import, verify-column migration, verify-gated `create_task`, `verify_spec`, `release_stale_claims` |
| `tests/support.py` | `BoardTestCase` — a throwaway board per test, pinned so nothing can touch the operator's real boards |
| `tests/test_board_schema.py` | criteria 1-2 |
| `tests/test_claim_contention.py` + `tests/_claim_child.py` | criterion 3 |
| `tests/test_reclaim.py` | criterion 4 |
| `tests/run.py`, `tests/run.ps1` | the runner — exit 0 pass, 1 fail |

Three columns were added, not one: `verify_command TEXT`, `verify_timeout INTEGER`, and
`expected_artifacts TEXT` (a JSON array). Criterion 2 requires the row to record expected
artifacts, and the kernel's `task_attachments` records artifacts a task *produced*, which is a
different fact from the ones it is *required* to produce. Target repo needed no new column: the
kernel's `workspace_kind='dir'` + `workspace_path` already means exactly that.

`create_task` refuses a task with no verify command at write time, with a `ValueError`, before any
row exists. That is Phase 3's criterion 2 arriving early, because it is the natural shape of the
write API rather than a separate feature — an unverifiable task never reaches the board at all.

## The finding

**On Windows the kernel never reclaims a task whose worker is dead.**

`_terminate_reclaimed_worker` decides "was this worker already gone?" from the exception `os.kill`
raises, and treats `ProcessLookupError` as a successful termination. Windows never raises that:
a dead-but-unreaped PID gives `PermissionError` (WinError 5) and a PID that never existed gives
`OSError` (WinError 87). Both fall to the `except OSError: return info` branch with
`terminated=False`, so `_worker_survived_termination` returns True and the reclaim is deferred.
Every subsequent tick defers again.

Measured against the kernel's default signal function, with a worker that had been killed and
confirmed dead by the kernel's own `_pid_alive`:

```
kernel default signal_fn -> 0 reclaimed; status = running
events: ['created', 'claimed', 'spawned', 'reclaim_deferred']
```

This is load-bearing for Tri-AI: the desktop is the always-on node, it is Windows, and a crashed
worker leaving its task `running` forever would strand that branch of every graph — silently, since
the board would look busy rather than broken.

The fix stays in the adapter. `release_stale_claims` passes a `signal_fn` — the hook the kernel
already exposes for exactly this — that raises `ProcessLookupError` when the process is already
gone and otherwise calls `os.kill` for real. The guard is not weakened: a worker that is genuinely
alive is still signalled, and still defers the reclaim if it survives. `tests/test_reclaim.py`
asserts both halves.

## Verification

```
$ tests/run.ps1
Ran 20 tests in 18.9s
OK
```

| criterion | proven by |
|---|---|
| 1. `verify_command` column added via `add_column_if_missing`, re-running is a no-op | `VerifyColumnMigration` — three tests, including that the kernel's own migration pass does not drop it |
| 2. a row records repo, prompt, verify command, dependencies, expected artifacts | `TaskRowRecordsTheWholeSpec` — read back by direct query; the dependency is a real `task_links` edge and is shown to gate promotion |
| 3. two concurrent claimants, exactly one succeeds | `ConcurrentClaimIsExclusive` — two and four *separate processes* on a shared start barrier, so the mutex under test is SQLite's cross-process write lock; asserts one winner, one `task_runs` row |
| 4. a killed worker's task is reclaimable within the lease TTL; a live worker's is not | `DeadWorkerIsReclaimed` (reclaimed, run closed, claimable again) and `LiveWorkerDefersTheReclaim` (extended, not signalled, still unclaimable, exactly one in-flight run) |

### Where these tests are weaker than the criteria's wording

Recorded rather than papered over, because a gate whose limits are unstated is not a gate.

- **Criterion 3** is worded as `rowcount==1` / `rowcount==0`. The tests assert the observable
  equivalent — one `claim_task` returns a `Task`, the others return `None` — plus exactly one
  `task_runs` row. In the race the only `None` path *is* `rowcount==0` (the parents-undone early
  return cannot fire on a parentless task), so this is equivalent and arguably stronger, but it is
  not literally what the criterion says.
- **Criterion 4's second half** says the reclaim "defers". What the live-worker test actually
  exercises is the kernel's `claim_extended` branch: the PID is alive, so the claim's expiry is
  pushed forward. The distinct `reclaim_deferred` branch — a worker that survives SIGTERM — has no
  test and is probably unreachable on Windows, where `TerminateProcess` cannot be ignored. The
  behaviour the criterion exists to protect (no second worker spawned beside a live one) *is*
  asserted, twice.

## Defects found auditing this phase, and fixed

**`board.create_task` was not atomic.** The kernel's `create_task` commits its own transaction, so
setting the verify columns afterwards left a window in which the row was visible as `ready` with
`verify_command` NULL — an unverifiable task, claimable by any worker polling at that instant.
Exactly the failure this project exists to prevent, and it would have surfaced as a Phase 3
criterion-2 violation much later. Both statements now run inside one outer `kb.write_txn(conn)`;
the kernel's `create_task` opts into savepoint nesting for precisely this kind of composition.

Proven with failure injection rather than asserted:

```
OLD ordering: rows with verify_command NULL: 1   -> the new test WOULD FAIL
NEW ordering: rows with verify_command NULL: 0   -> the new test PASSES
```

**The board pin was a silent no-op under the Hermes dispatcher.** `os.environ.setdefault(
"HERMES_KANBAN_DB", …)` does nothing when the dispatcher has already injected that variable into a
worker's environment, which it always does. Harmless while `board.connect()` passes an explicit
path, but not once Phase 2's workers run under a dispatcher. Now an unconditional assignment.

## Notes for later phases

- The concurrency test spawns real processes and takes ~21s. It stays in the default suite; if it
  ever becomes the reason the suite is skipped, split it, don't delete it.
- Phase 2's worker must call `board.release_stale_claims`, never `kb.release_stale_claims` — going
  direct to the kernel reintroduces the Windows deferral. Worth a grep check in Phase 2's audit
  step, alongside the existing no-push/no-merge/no-credentials audit.
- The kernel exposes `signal_fn` on `reclaim_task` and `detect_stale_running` too. Neither is used
  yet; both need the same wrapper when they are.
