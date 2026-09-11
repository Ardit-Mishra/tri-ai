# Project State

## Project Reference

See: `.planning/PROJECT.md` (audited 2026-09-10)

**Core value:** A task assigned once gets decomposed, executed in parallel by free local models, and verified by exit codes — without the expensive model staying in the loop.
**Current focus:** Phase 4 — Read-Only Telegram Observability (Slice 2
adversarial correction proof)

**Approved future direction:** `.planning/research/capability-expansion-design.md`
defines local verified promotion, local RAG/model-routing probes, semantic
memory, and constrained self-evolution. These features are approved, but Phase
4 Slice 2 remains the next atomic implementation step. The design has not yet
received independent subagent review because the service returned `Transport
closed`; the reviewer packets are embedded in the design.

## Current Position

Phase: 4 of 8 (Read-Only Telegram Observability)
Plan: `.planning/phases/phase-4-plan.md`
Status: Phases 1-3 are complete; Phase 4 Slice 1 is accepted. Slice 2's
implementation and review correction are committed on the canonical branch,
but the two P1 review fixes lack adversarial regression tests and are not
accepted. Do not start Slice 3 until they exist and review passes.

**Canonical branch:** `phase-2/worker-assign`. The audited implementation base
was `00671d9`; the phase/plan audit record was committed as `75e188f`.

**Latest canonical verification:** `python tests/run.py` → **137 tests, exit
0, 146.674s** (2026-09-11). This establishes the current base is green; it
does not prove Slice 2's two correction cases because no test drives them.

**Phase 4 Slice 2 correction:** independent review of `a71ff9e` found that a
crash after `git worktree add` could strand a deterministic unowned target and
that a recorded target was not proven to be the expected branch. `02b90f8`
adds `executor.verify_worktree` and holds a board write transaction across
adoption/materialization/recording. Required next proof: (1) pre-create the
deterministic target and prove it is adopted only when linked to the exact
source and branch; (2) corrupt a recorded target/branch and prove dispatch
skips it unclaimed. Keep every target as evidence; do not auto-remove it.

**Branch audit:** `.planning/reviews/phase-audit-2026-09-10.md` records every
registered branch and its command result. Slice 3 (`slice3/error-class`) and
Slice 4 (`slice4/telegram-read`) fail their current full suites. Phase 5,
Phase 6, and Phase 7 worktrees are partial experiments, not merged progress;
the Phase 7 candidate is rejected because it can mark a task done without a
trusted verify result.

## Performance Metrics

**Velocity:**
- Completed phases: 3
- Average duration: 1 session
- Total execution time: 2 sessions

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Verified Board Substrate | 1 | 1 session | 1 session |
| 2. Verify-Gated Single-Worker Execution | 1 | 1 session | 1 session |
| 3. Planner + Bounded Concurrent Execution | 1 | independently reviewed | — |

**Recent Trend:**
- Phase 1 (22 tests) → Phase 2 (89) → Phase 3 (132) → current Phase 4 base (137)
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
- Phase 3 Slice 3: **the kernel owns the parent gate — the dispatcher/board never re-checks it.** `kanban_db.claim_task` is the single enforcement point: a claim on a task with an undone parent demotes it `ready -> todo` with a `claim_rejected` event and returns `None`, and unparented tasks land `ready` while parented ones land `todo` until `recompute_ready` promotes them when every parent is `done`. So a linked-graph failure test builds the DAG through `task_links` and asserts on the real claim/complete/reclaim lifecycle rather than re-implementing scheduling. Verified against the kernel source, not assumed.
- Phase 3 Slice 3: **the failure-isolation test drives the REAL worker path, not a bespoke failure simulator.** The launcher claims the dispatched task via `kb.claim_task`, then runs `worker.execute_task` (claim -> precheck -> upstream gate -> agent -> verify -> accept/revert -> ledger) with only `executor.run_agent` swapped, so the "deliberate non-zero verify command" genuinely exits 7 through `executor.run_verify` and the retry/circuit-breaker is the kernel's own `_record_task_failure` (limit 2 -> `blocked` + `gave_up`). This is why the test is credible where a mock-outcome launcher would not be.
- Phase 3 Slice 3: **the dispatcher must re-read the board between waves, not track dispatched IDs** — a reclaimed (failed-then-retried) task reappears in `ready_tasks()` and would be invisible to a `dispatched_ids` set. The Slice 3 test's `5 results (root + 2 failing-parent + 2 siblings)` in one run is proof the re-read drives the retry. This is the second scheduling-loop correction (after `run_batch` threading) that Slice 3 surfaced; both were needed only because the earlier slices did not exercise failure.

### Pending Todos

- Phase 4 Slice 2: add the two missing adversarial tests for `02b90f8` before
  accepting its review correction. This is the next atomic step. The task-owned
  worktree is never automatically removed; a failed or pre-existing target is
  evidence for an operator, not worker cleanup.
- Phase 2's worker must call `board.release_stale_claims`, never `kb.release_stale_claims` directly — going straight to the kernel reintroduces the Windows reclaim deferral (see Blockers). Add a grep check to Phase 2's criterion-5 audit step, beside the existing no-push/no-merge/no-credentials audit
- Any new Tri-AI entry point must go through `board.kanban()`, which now *assigns* `HERMES_KANBAN_DB` rather than `setdefault`-ing it. A dispatcher-spawned worker inherits that variable pointing at the Hermes board, so `setdefault` silently kept the wrong board
- Review is a separate seat: Claude writes, a second model reviews at the commit/branch level. Brief at `~/CODEX-REVIEWER-BRIEF.md`. It earns its keep — the first pass caught a test whose *name* claimed it proved a schema collision was refused while its body only inspected a throwaway table and never called `migrate()`. A test that asserts less than its name is the same class of failure as an agent reporting success it did not achieve, and self-review does not reliably catch it
- The kernel exposes `signal_fn` on `reclaim_task` and `detect_stale_running` as well. Neither is used yet; both need the same wrapper when a phase reaches for them
- Phase 3 review: **DONE 2026-09-10** — passed, 4 non-blocking findings, recorded at `.planning/reviews/phase-3-review.md` (commit `12288a7`).
- Slice 3's uncommitted classifier is not integrated with the worker and fails
  its current test matrix. Slice 4's candidate returns log paths rather than
  full captured logs and has no real transport. Neither has started in the
  sense that matters for roadmap acceptance.
- Phase 5's memory candidate is partial (episodic and semantic committed,
  procedural uncommitted, evolution absent) and uses WAL on SQLite 3.50.4.
  Phase 6's dashboard candidate fails. Phase 7's daemon candidate can fabricate
  a passing verification result and directly applies remote diffs; do not merge
  any of these branches. Full evidence is in the audit record.
- Before Phase 5 work starts, reconcile the coarse preview (semantic memory and
  self-evolution in Phase 6) with the detailed memory design (5C/5D). The
  Phase 5 plan, not an experimental branch, must choose the order and proofs.

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
Phase/plan audit complete on canonical `phase-2/worker-assign`; audit record
commit `75e188f`.
Read `.planning/reviews/phase-audit-2026-09-10.md` before touching any feature
worktree. It separates accepted history from candidate code and preserves each
failure result.

**Accepted Phase 3 evidence:**
- `src/dispatcher.py` — MODIFIED. Dispatch loop simplified for retry support: drop `dispatched_ids`;
  re-read `board.ready_tasks()` fresh each wave so reclaimed (failed-then-retried) tasks reappear
  naturally; add `max_waves` test bound. No new process/git surface (safety audit unchanged).
- `tests/test_phase3_failure_isolation.py` — NEW. 2 tests. Builds the Slice 3 DAG through real
  `task_links`, dispatches one run (`root` -> failing-parent/blocked-descendant + two siblings), and
  proves the failing parent's deliberate non-zero verify command is ledgered `verify_outcome='failed'`
  and trips the kernel circuit breaker (`blocked` + `gave_up` after 2), the descendant stays
  unclaimable (kernel demotes ready child with undone parent to `todo` at claim), siblings reach
  `done` in the same run with valid worker/run ledger identities, and a deliberately broken
  dispatcher (stops at first failed child) makes the sibling-completion assertion fail.

**Current canonical test result:** `python tests/run.py` → **137 tests, exit 0,
146.674s** (2026-09-11).

**Key design decisions (Slice 3):**
- The failure test drives `worker.execute_task` (the real claim -> precheck -> gate -> agent -> verify
  -> accept/revert -> ledger path) with only `executor.run_agent` swapped; the verify command is a real
  `python -c "import sys; sys.exit(7)"`, so the ledger genuinely records `verify_exit=7`.
- `board.create_task(parents=[...])` creates the real `task_links`; the kernel's `claim_task` is the
  single parent-gate enforcement point (demote ready child with undone parents to `todo` + return
  `None`) and `complete_task` runs `recompute_ready` to promote siblings when root finishes — the
  dispatcher's wave re-read picks them up, which is how one dispatch run does root, children, and the
  failing-parent retry (`5 results`).
- `failing_parent` gets `priority=100` in the negative test so `ready_tasks()` orders it first and the
  broken dispatcher deterministically stops at it before any sibling is launched.

**Slice 2 commit:** `fa2bb52` — cap-derived concurrent dispatcher + workspace partitioning. 130 tests.
**Slice 1 commit:** `a86f331` — planner graph writer. 98 tests, exit 0, 104.808s.
**Phase 2 commit:** `ea44258` — worker/ledger/assign/chores. 89 tests, exit 0, 93.951s.

Next: Phase 4 Slice 2 adversarial correction tests, then independent review.
Do not merge any branch or start Slice 3 before that gate passes.

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

Next: execute `.planning/AUTONOMOUS-RUNBOOK.md` for Phase 4.

Note: `~/.claude/skills/` was destroyed in the 2026-09-06 incident and is NOT in the `S5-claude-r3`
archive — that archive stopped at `./profiles/`, before reaching `./skills/`. So the whole GSD suite
(`gsd-plan-phase`, `gsd-execute-phase`, ~60 skills) and the custom `research-repo-grade` skill are
gone. GSD is a marketplace plugin and can be reinstalled; `research-repo-grade` was custom and is not
in `skills-lock.json`, so it is lost. Until GSD is reinstalled, phases are planned directly against
the roadmap's success criteria rather than via `/gsd:plan-phase`.
Resume file: None
