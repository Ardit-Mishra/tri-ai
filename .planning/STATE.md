# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-09-02)

**Core value:** A task assigned once gets decomposed, executed in parallel by free local models, and verified by exit codes — without the expensive model staying in the loop.
**Current focus:** Phase 3 — Concurrency (next)

## Current Position

Phase: 3 of 5 (Planner + Bounded Concurrent Execution)
Plan: `.planning/phases/phase-3-plan.md` (Slices 1–2 complete, Slice 3 pending)
Status: Phase 2 complete; Phase 3 Slices 1–2 verified locally
Last activity: 2026-09-10 — Phase 3 Slice 2 is complete in local commit (uncommitted yet,
to be committed after STATE.md update). Adds `src/dispatcher.py` (~290 lines):
cap-derived concurrent dispatcher with workspace partitioning, `board.ready_tasks()` as
a board-level API, parallel wave dispatch via threading, and 28 tests covering cap
derivation, group scheduling, workspace conflict detection, parallel-over-serial timing
proof, same-repo serialization, unique-worktree concurrency, and subprocess PID
verification. Safety audit extended with `DispatcherCannotBypassOrPush` class (no
process creation, no raw git, no push/merge/deploy/credential in dispatcher). Full suite:
**130 tests, exit 0, 117.646s** (`python tests/run.py`).

Slice 1 (`a86f331`): planner graph writer — validate-first, transactional, workspace-aware.
98 tests at commit. Slice 2: dispatcher + concurrency tests — the cap-derived concurrent
executor with workspace partitioning. 130 tests at commit. Next atomic step is Phase 3
Slice 3 only: linked-graph failure isolation and full ledger assertions.

Progress: [█████░░░░░] 50%

## Performance Metrics

**Velocity:**
- Total plans completed: 2
- Average duration: 1 session
- Total execution time: 2 sessions

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Verified Board Substrate | 1 | 1 session | 1 session |
| 2. Verify-Gated Single-Worker Execution | 1 | 1 session | 1 session |

**Recent Trend:**
- Phase 1 (22 tests) → Phase 2 (89 tests cumulative)
- Trend: ↑

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: reuse `hermes_cli/kanban_db.py` directly as the board substrate; the only board-level build work is the additive `verify_command` column, not a new board or a bespoke JSON graph file (STACK.md's JSON recommendation is superseded by ARCHITECTURE.md's direct source read)
- Roadmap: "prove coordination works" (Phases 1-3) is deliberately kept separate from any GPU-bound concurrent-fixing claim — v1 targets read-only CPU/IO-bound chores only; concurrent generation only buys ~16% on this hardware (`concurrency_results.json`), not Nx
- Roadmap: Telegram read-only observability (Phase 4) ships before any Telegram write action (Phase 5) — the control surface must not mutate a live graph before its underlying primitives (reclaim, retry, pause) are proven trustworthy
- Phase 1: the kanban kernel is *used, never edited*. The Hermes install replaces whole package trees when it updates (every package has a `*.hermes-update-staging` sibling), so an in-place patch to `kanban_db.py` would be silently reverted — and the failure mode is invisible: the board keeps working while verification quietly stops. The additive migration runs from `src/board.py` against Tri-AI's own board file, through the kernel's own `add_column_if_missing`. Safe because the kernel's migration pass is purely additive and `tasks` is not in its `_REBUILD_SPECS` drift-rebuild list — both asserted by tests, not assumed
- Phase 1: three columns added, not one — `verify_command`, `verify_timeout`, `expected_artifacts` (JSON). Target repo needed no column: the kernel's `workspace_kind='dir'` + `workspace_path` already means exactly that
- Phase 1: `board.create_task` refuses a task with no verify command at write time, before any row exists, AND writes the row and its verify columns in one transaction. Rejecting early is only half the property: the kernel's `create_task` commits on its own, so a second transaction for the verify columns left a window where the row was visible as `ready` with `verify_command` NULL and a polling worker could claim an unverifiable task. Proven by failure injection, not assumed. This is Phase 3's criterion 2 landing early because it is the natural shape of the write API, not a separate feature
- Phase 2: **idempotency-key re-submission is a full no-op, never a partial update.** The kernel's `create_task` returns the existing row id for a duplicate key; `board.create_task` previously re-wrote only the three verify columns onto it, so a re-run of a queue line whose verify/timeout/artifacts changed refreshed the gate while title, prompt, workspace and `max_runtime_seconds` stayed stale — a fresh oracle bolted onto an old workspace, reported as success. Caught by the assign/chores cross-reviewer and confirmed by reproduction. The fix detects a pre-existing key inside the same `write_txn` (IMMEDIATE, so no interleaving writer) and skips the verify-column UPDATE. To change a task, delete and re-assign.
- Phase 2: **a claim must carry the worker's own pid, or the 15-minute TTL reclaims a live run.** Tri-AI claims with `host:pid` but never set the kernel's `worker_pid`; the kernel's live-worker extension branch (`release_stale_claims`: truthy `worker_pid` + `_pid_alive`) therefore never fired, and once `DEFAULT_CLAIM_TTL_SECONDS` (15m) elapsed a claim whose agent was still running (default 30m) was reclaimed to `ready` and a second worker spawned a second agent on the same repo. Caught by the worker/ledger cross-reviewer with a precise reproduction. Fixed by registering the worker's own pid after claim (`kb._set_worker_pid`, the same private-seam precedent as `board.posix_semantics_signal`); the dead/crashed/quarantine paths still reclaim because the pid is genuinely gone after exit.

### Pending Todos

- Phase 2's worker must call `board.release_stale_claims`, never `kb.release_stale_claims` directly — going straight to the kernel reintroduces the Windows reclaim deferral (see Blockers). Add a grep check to Phase 2's criterion-5 audit step, beside the existing no-push/no-merge/no-credentials audit
- Any new Tri-AI entry point must go through `board.kanban()`, which now *assigns* `HERMES_KANBAN_DB` rather than `setdefault`-ing it. A dispatcher-spawned worker inherits that variable pointing at the Hermes board, so `setdefault` silently kept the wrong board
- Review is a separate seat: Claude writes, a second model reviews at the commit/branch level. Brief at `~/CODEX-REVIEWER-BRIEF.md`. It earns its keep — the first pass caught a test whose *name* claimed it proved a schema collision was refused while its body only inspected a throwaway table and never called `migrate()`. A test that asserts less than its name is the same class of failure as an agent reporting success it did not achieve, and self-review does not reliably catch it
- The kernel exposes `signal_fn` on `reclaim_task` and `detect_stale_running` as well. Neither is used yet; both need the same wrapper when a phase reaches for them

### Blockers/Concerns

- **Independent verification caught a false green on 2026-09-09 (Phase 2 slice 1).** The author ran
  the suite and reported 51 tests passing; the reviewer ran the same suite on the same machine and
  got exit 1 — `test_a_delayed_writer_spawned_by_the_verifier_never_writes` failed with
  `tree_survived=True`. The defect was real and the author's green run was the misleading one:
  `_kill_tree` returned success whenever the shell parent had already exited, and verified only that
  the *root* pid was gone rather than the tree. `taskkill /F /T` walks a parent-child map that a
  detached grandchild is not on. Reproduced directly: the old logic reports "tree gone: True" while
  the orphan writes 8s later. Replaced with a Windows **Job Object** (ctypes/kernel32) — children
  join at creation, `TerminateJobObject` kills the set atomically, and the job is queried afterwards
  for survivors, so "we killed it" becomes evidence rather than an assertion. Two lessons worth
  keeping: a timing-dependent test can pass for the author and fail for a reviewer on the same
  machine, so a single green run is not verification; and this is the second Windows
  process-lifetime assumption to be wrong here, after Phase 1's reclaim defect.

- **Phase 1 publication resolved on 2026-09-09.** The URL and GitHub permissions were correct; `Ardit-Mishra/tri-ai` simply had never been created. `gh repo create Ardit-Mishra/tri-ai --public` created an empty repository, then `git push -u origin main` published `23dbfb4`. GitHub API verification proved `evidence/ledger.jsonl` is public, detected `license=MIT`, and reported `main` as the default branch at `23dbfb4`. The two earlier hard-stops were correct: pushing cannot create a GitHub repository, and no URL or credential change was needed.

- REQUIREMENTS.md's own summary line originally stated "21 total" v1 requirements; the actual itemized list contains 25. Corrected during roadmap creation — verify this doesn't indicate a requirement was silently dropped somewhere upstream if it resurfaces.
- **On Windows the kanban kernel never reclaims a task whose worker is dead** (found and worked around in Phase 1). `_terminate_reclaimed_worker` reads "already gone" from a `ProcessLookupError`, which Windows' `os.kill` never raises — a dead PID gives `PermissionError` (WinError 5), one that never existed gives `OSError` (WinError 87), and both are read as "still alive", so every tick defers the reclaim forever. Measured against the kernel default: `0 reclaimed; status = running; events [..., 'reclaim_deferred']`. Load-bearing, because the always-on node is the Windows desktop and a stranded task looks busy rather than broken. Worked around in the adapter via the kernel's own `signal_fn` hook (`board.posix_semantics_signal`), not by editing the kernel; the guard is not weakened — a genuinely live worker is still signalled and still defers
- EXEC-01's concurrency cap (Phase 3) must be set from `concurrency_results.json`, which measured concurrent *generation* only (up to n=4, ~16% aggregate gain, VRAM flat at 5,278 MiB). CPU/IO-bound chore parallelism is a different, unmeasured-but-likely-favorable case per PITFALLS.md/FEATURES.md reasoning — do not conflate the two when Phase 3 sets its actual worker count.

## Session Continuity

Last session: 2026-09-10
Phase 3 Slice 2 complete. Branch `phase-2/worker-assign`. Commit pending (uncommitted changes).

**Slice 2 changed files:**
- `src/dispatcher.py` — NEW. Cap-derived concurrent dispatcher (~290 lines): `read_cap()`,
  `partition_groups()`, `filter_running()`, `dispatch_one()`, `dispatch()`, `run_batch()`,
  `WorkerResult`, `DispatchResult`.
- `src/board.py` — added `ready_tasks()` (board-level API for ready task selection with
  `workspace_kind` and `branch_name`).
- `src/worker.py` — `ready_tasks()` now delegates to `board.ready_tasks()`.
- `tests/test_phase3_concurrency.py` — NEW. 28 tests: `ReadCapTest` (9), `PartitionGroupsTest` (5),
  `FilterRunningTest` (3), `DispatchMockTest` (4), `TimingProofTest` (2), `SameRepoPartitionTest` (3),
  `SubprocessDispatchTest` (2).
- `tests/test_safety_boundary.py` — `dispatcher.py` added to `CLOSURE_MODULES`; new
  `DispatcherCannotBypassOrPush` class (4 tests: no process creation, no raw git, no push/merge/
  deploy/credential, launcher-only worker invocation).

**Test result:** `python tests/run.py` → **130 tests, exit 0, 117.646s** (2026-09-10).
Focused concurrency suite: **28 tests, exit 0, 16.92s**.

**Key design decisions (Slice 2):**
- Cap derived from `.planning/research/concurrency_results.json` (currently 4). Fail loudly on
  malformed/missing file or override exceeding measured cap.
- `run_batch()` uses threading for concurrent dispatch within a wave — tasks within a wave genuinely
  overlap in wall-clock time, proving the parallel-over-serial timing predicate.
- `dispatch()` tracks `dispatched_ids` to prevent re-dispatch when re-reading `ready_tasks()` between
  waves. `running_keys` cleared after each wave (workers have exited by then).
- Mock launchers mark tasks as done on the board after invocation, so the dispatch loop's inter-wave
  re-read sees correct state.
- Safety: dispatcher never calls subprocess, os.system, or any PROCESS_ATTRS directly. Process
  creation is the launcher's responsibility.

**Slice 1 commit:** `a86f331` — planner graph writer. 98 tests, exit 0, 104.808s.
**Phase 2 commit:** `ea44258` — worker/ledger/assign/chores. 89 tests, exit 0, 93.951s.

Next: Phase 3 Slice 3 only — linked-graph failure isolation and full ledger assertions. Do not begin
until Slice 2 is independently reviewed.

Phase 1 carries two defects found by self-audit and fixed (non-atomic `create_task`; the `setdefault`
board pin), one found by review and fixed (`migrate` silently accepting a same-named column of a
different type).

Phase 2 landed: worker (`src/worker.py`), assign (`src/assign.py`), ledger (`src/ledger.py`),
chores (`src/chores.py`), three criterion test files, calibration infrastructure, and two BLOCKER
fixes in `src/board.py` (idempotency no-op) and `src/worker.py` (worker_pid registration).

**Canonical-checkout guard:** `C:\Users\ardit\tri-ai` is the authoritative Tri-AI checkout. An
Omniroute Claude session created a separate temporary clone under
`C:\Users\ardit\AppData\Local\Temp\claude\...\scratchpad\p0a\tri-ai` on unrelated branch
`phase0a/safe-execution`, with untracked execution-backend files. Do not merge, delete, or use that
clone as a handoff source. Every autonomous session must pass the path gate in
`.planning/AUTONOMOUS-RUNBOOK.md` before modifying anything.

Next: execute `.planning/AUTONOMOUS-RUNBOOK.md` for Phase 3.

Note: `~/.claude/skills/` was destroyed in the 2026-09-06 incident and is NOT in the `S5-claude-r3`
archive — that archive stopped at `./profiles/`, before reaching `./skills/`. So the whole GSD suite
(`gsd-plan-phase`, `gsd-execute-phase`, ~60 skills) and the custom `research-repo-grade` skill are
gone. GSD is a marketplace plugin and can be reinstalled; `research-repo-grade` was custom and is not
in `skills-lock.json`, so it is lost. Until GSD is reinstalled, phases are planned directly against
the roadmap's success criteria rather than via `/gsd:plan-phase`.
Resume file: None
