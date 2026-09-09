# Roadmap: Tri-AI Swarm

## Overview

The board substrate already exists and is production-tested — `hermes_cli/kanban_db.py` already
implements atomic claim, lease/heartbeat/reclaim, dependency-gated DAG promotion, and a blackboard.
This roadmap adds exactly the one thing it is missing (an exit-code verify oracle) and wires the
four layers on top of it in the order their own correctness depends on: first prove the board's
claim/reclaim mechanics are trustworthy in isolation; then prove a single worker will accept or
revert strictly by exit code; then let the strong model write real graphs and run several workers
concurrently at a cap taken from measurement, with failure isolated per branch; then make that state
observable from Telegram; only last, once every underlying primitive is proven, let Telegram mutate
the graph at all. Concurrent GPU-bound *fixing* work and cross-machine scale are explicitly deferred
past this milestone — this roadmap validates coordination and read-only CPU/IO-bound parallelism
only, per the measured ~16% (not Nx) concurrent-generation ceiling on this hardware.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Verified Board Substrate** - Add the verify_command column and prove atomic claim/lease/reclaim under real contention, reusing the existing kanban kernel
- [ ] **Phase 2: Verify-Gated Single-Worker Execution** - One worker claims, runs, and accepts/reverts subtasks strictly by exit code, assignable from CLI, queue file, or a durable schedule
- [ ] **Phase 3: Planner + Bounded Concurrent Execution** - The strong model writes a real graph and exits; several workers execute it concurrently at a measured cap with proven per-branch failure isolation
- [ ] **Phase 4: Read-Only Telegram Observability** - The board and full subtask output are inspectable from Telegram, no write capability yet
- [ ] **Phase 5: Telegram Control Actions** - Cancel, retry, and confirmation-gated task assignment from Telegram, routed through the same primitives every worker already uses

## Phase Details

### Phase 1: Verified Board Substrate
**Goal**: The kanban board can durably store a task graph with a verify_command oracle per node, and its atomic claim/lease/reclaim mechanics are proven correct under real concurrent contention — reusing the existing kernel, not rebuilding it.
**Depends on**: Nothing (first phase)
**Requirements**: GRAPH-02, GRAPH-04, BOARD-01, BOARD-02, BOARD-03, BOARD-04
**Success Criteria** (what must be TRUE):
  1. `kanban_db.py`'s `tasks` table has a `verify_command` column added via the existing `add_column_if_missing` helper, and re-running the migration is a no-op (idempotent, no error, no duplicate column)
  2. A task row created through the board API records target repo, prompt, verify_command, dependencies (via `task_links`), and expected artifacts — inspectable via a direct query against the board
  3. A test that spawns two concurrent claimants against the same ready task asserts exactly one claim succeeds (rowcount==1) and the other observes rowcount==0, run as an automated test with a pass/fail exit code
  4. A test that kills a worker mid-claim asserts the task becomes reclaimable within its lease TTL, and a second test where the "dead" worker is actually still alive asserts the reclaim defers instead of double-spawning a second worker on the same task
**Plans**: `.planning/phases/phase-1-plan.md` (complete — 22 tests, all criteria met, independently re-run by a reviewer)

### Phase 2: Verify-Gated Single-Worker Execution
**Goal**: One worker claims subtasks from the board, executes them, and accepts or reverts strictly by verify-command exit code — including cross-edge artifact checks and adversarially-tested verify commands — targeting only read-only CPU/IO-bound chores, assignable from the CLI, a queue file, or a durable schedule.
**Depends on**: Phase 1
**Requirements**: VERIFY-01, VERIFY-02, VERIFY-04, VERIFY-05, EXEC-02, EXEC-03, EXEC-04, TRIG-01, TRIG-02, TRIG-03
**Success Criteria** (what must be TRUE):
  1. Running the worker against a subtask with a passing verify command marks it done in the board and ledger; running it against one with a failing verify command reverts the repository's working tree to its pre-task state before any other subtask claims that repo
  2. A subtask configured to consume a deliberately-missing or empty upstream artifact fails its own verify step rather than proceeding, demonstrated by a test case
  3. Every verify command shipped in v1's chore set (test suite, typecheck, build, dependency audit) has a recorded run showing it FAILS against deliberately broken input, before being trusted for real work
  4. A task submitted via laptop CLI, one appended to the queue file, and one triggered by a Windows Scheduled Task all execute through the identical claim→verify→ledger path and each produces a ledger entry with worker identity, model, verify exit code, and duration
  5. Grep/audit of the worker codepath confirms no call to `git push`, `git merge` into a protected branch, deploy tooling, or credential file access exists anywhere in the execution path
**Plans**: `.planning/phases/phase-2-plan.md` (planned, not started)

### Phase 3: Planner + Bounded Concurrent Execution
**Goal**: A strong model decomposes one assigned task into a persisted graph via the board API and exits; several workers then claim and execute independent subtasks concurrently at a cap set from the measured concurrency data, and a failure in one branch is proven not to stall unrelated branches.
**Depends on**: Phase 2
**Requirements**: GRAPH-01, GRAPH-03, EXEC-01, VERIFY-03
**Success Criteria** (what must be TRUE):
  1. Invoking the planner on one assigned task results in a persisted graph (task rows + task_links) on the board, and the planner process exits immediately after writing — no planner process remains alive or polling
  2. A planner-authored subtask with no verify_command is rejected by the board's write API at insertion (a non-zero exit / raised error at write time), never discovered later at claim or run time
  3. A batch of independent CPU/IO-bound chores run through N concurrent workers (N set from `concurrency_results.json`, not guessed) completes in measurably less wall-clock time than the same batch run through one worker, with the ledger showing every worker's entries
  4. A deliberately-failed subtask (bad verify command or killed worker) in one branch of a concurrent run shows as failed in the ledger/board, while sibling subtasks with no dependency on it reach `done` in the same run
**Plans**: TBD

### Phase 4: Read-Only Telegram Observability
**Goal**: The board and ledger are inspectable from Telegram — what is queued, claimed, running, passed, or failed — including full output on any subtask, with no write capability yet.
**Depends on**: Phase 3
**Requirements**: OBS-01, OBS-04
**Success Criteria** (what must be TRUE):
  1. A Telegram status query returns the current board state (counts/list of queued, claimed, running, passed, failed) matching a direct board query taken at the same moment
  2. A Telegram query for a specific failed subtask returns its full captured stdout/stderr (not a truncated tail), sufficient to diagnose the failure without re-running the task
**Plans**: TBD

### Phase 5: Telegram Control Actions
**Goal**: A running subtask can be cancelled, a failed subtask can be retried, and a new task can be assigned — all from Telegram, with cancellation ungated and everything else confirmation-gated, routing through the exact same board primitives every worker already uses.
**Depends on**: Phase 4
**Requirements**: OBS-02, OBS-03, TRIG-04
**Success Criteria** (what must be TRUE):
  1. Sending a cancel command for a running subtask from Telegram immediately releases its claim and marks it in a terminal/cancelled state, verified by a follow-up board query, with no confirmation step required
  2. Sending a retry command for a failed subtask from Telegram re-enters it into the exact claim→verify path (same verify_command, no bypass), verified by a follow-up board query showing a new task_run
  3. Assigning a new task from Telegram requires an explicit confirmation step before the planner is invoked; an unconfirmed message creates zero board rows
  4. No Telegram handler contains a path to arbitrary shell execution, verify-command editing, or push/merge/deploy/credential access (grep/audit-verifiable)
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Verified Board Substrate | 1/1 | Complete | 2026-09-08 |
| 2. Verify-Gated Single-Worker Execution | 0/TBD | Not started | - |
| 3. Planner + Bounded Concurrent Execution | 0/TBD | Not started | - |
| 4. Read-Only Telegram Observability | 0/TBD | Not started | - |
| 5. Telegram Control Actions | 0/TBD | Not started | - |
