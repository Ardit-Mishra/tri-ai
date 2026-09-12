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
- Phase 0: completed feasibility input, not a delivery phase
- Integer phases (1-8): delivery phases
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 0: Concurrency Feasibility Measurement** - Measure this machine's
  default local-generation ceiling before promising speedup from concurrent
  model calls; this is a completed input, not a branch or deliverable
- [x] **Phase 1: Verified Board Substrate** - Add the verify_command column and prove atomic claim/lease/reclaim under real contention, reusing the existing kanban kernel
- [x] **Phase 2: Verify-Gated Single-Worker Execution** - One worker claims, runs, and accepts/reverts subtasks strictly by exit code, assignable from CLI, queue file, or a durable schedule
- [x] **Phase 3: Planner + Bounded Concurrent Execution** - The strong model writes a real graph and exits; several workers execute it concurrently at a measured cap with proven per-branch failure isolation
- [x] **Phase 4: Read-Only Telegram Observability** - The board and full subtask output are inspectable from Telegram, no write capability yet
- [x] **Phase 5: Memory, Telegram Control, and Intake** - Passive memory foundation plus cancel, retry, and confirmation-gated task assignment from Telegram
- [x] **Phase 6: JARVIS Dashboard & Spatial HUD** - Read-only terminal/web dashboard over board, ledger, accepted memory evidence, and per-run lifecycle telemetry
- [ ] **Phase 7: Planner Integration and Trusted Distributed Delegation** - Learned-pattern planner assistance plus a remote-worker protocol that preserves verify-gated acceptance
- [ ] **Phase 8: Remote Offload and Phone Control** - Hardware-aware offload and confirmation-gated multi-node control

## Phase Details

### Phase 0: Concurrency Feasibility Measurement
**Purpose**: Establish what the actual RTX 3060 configuration can do before
designing around assumed model parallelism. `concurrency_results.json` records
the measured default configuration: generation-bound requests reached about
1.16x aggregate speedup at N=4 with flat VRAM, so they serialize rather than
provide N-way speedup. This does not constrain CPU/IO verification chores.
**Status**: Complete research input. The unrelated `phase0a/safe-execution`
scratch worktree is not this phase and must not be merged.

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
**Plans**: `.planning/phases/phase-2-plan.md` (complete — 89 tests, exit 0, independently cross-reviewed)

### Phase 3: Planner + Bounded Concurrent Execution
**Goal**: A strong model decomposes one assigned task into a persisted graph via the board API and exits; several workers then claim and execute independent subtasks concurrently at a cap set from the measured concurrency data, and a failure in one branch is proven not to stall unrelated branches.
**Depends on**: Phase 2
**Requirements**: GRAPH-01, GRAPH-03, EXEC-01, VERIFY-03
**Success Criteria** (what must be TRUE):
  1. Invoking the planner on one assigned task results in a persisted graph (task rows + task_links) on the board, and the planner process exits immediately after writing — no planner process remains alive or polling
  2. A planner-authored subtask with no verify_command is rejected by the board's write API at insertion (a non-zero exit / raised error at write time), never discovered later at claim or run time
  3. A batch of independent CPU/IO-bound chores run through N concurrent workers (N set from `concurrency_results.json`, not guessed) completes in measurably less wall-clock time than the same batch run through one worker, with the ledger showing every worker's entries
  4. A deliberately-failed subtask (bad verify command or killed worker) in one branch of a concurrent run shows as failed in the ledger/board, while sibling subtasks with no dependency on it reach `done` in the same run
**Plans**: `.planning/phases/phase-3-plan.md` (complete — `76dc2c6`; independent review `12288a7`, 132 tests, exit 0)

### Phase 4: Read-Only Telegram Observability
**Goal**: The board and ledger are inspectable from Telegram — what is queued, claimed, running, passed, or failed — including full output on any subtask, with no write capability yet.
**Depends on**: Phase 3
**Requirements**: OBS-01, OBS-04
**Success Criteria** (what must be TRUE):
  1. A Telegram status query returns the current board state (counts/list of queued, claimed, running, passed, failed) matching a direct board query taken at the same moment
  2. A Telegram query for a specific failed subtask returns its full captured stdout/stderr (not a truncated tail), sufficient to diagnose the failure without re-running the task
**Plans**: `.planning/phases/phase-4-plan.md` and
`.planning/phases/phase-4.5-telegram-transport-plan.md` (complete; both
criteria independently exercised against the live Telegram polling daemon on
2026-09-11)

### Phase 5: Memory, Telegram Control, and Intake
**Goal**: Build the passive episodic/procedural memory foundation, then let a
running subtask be cancelled, a failed subtask retried, and a new task assigned
from Telegram. Cancellation is ungated; every other remote mutation is
confirmation-gated and routes through the same board primitives every worker
already uses.
**Depends on**: Phase 4
**Requirements**: OBS-02, OBS-03, TRIG-04
**Success Criteria** (what must be TRUE):
  1. Sending a cancel command for a running subtask from Telegram immediately releases its claim and marks it in a terminal/cancelled state, verified by a follow-up board query, with no confirmation step required
  2. Sending a retry command for a failed subtask from Telegram re-enters it into the exact claim→verify path (same verify_command, no bypass), verified by a follow-up board query showing a new task_run
  3. Assigning a new task from Telegram requires an explicit confirmation step before the planner is invoked; an unconfirmed message creates zero board rows
  4. No Telegram handler contains a path to arbitrary shell execution, verify-command editing, or push/merge/deploy/credential access (grep/audit-verifiable)
**Plans**: `.planning/phases/phase-5-control-worker-routing-plan.md` records
the verified 5A-C implementation. The operator accepted Phase 5 on 2026-09-11
after the mobile confirmation path created ready task `t_a17464db` from draft
`p_52ad147623592edf`; the explicit product decision is that confirmation
creates one configured verify-gated task, not a planner invocation. The
cancel/retry contracts remain covered by their automated board/transport
proofs; this acceptance does not misrepresent them as separately exercised by
that mobile run.
**Planning gate**: Before Phase 5 implementation, reconcile the preview's
Phase 6 placement of semantic memory and self-evolution with the detailed
memory design's 5C/5D placement. The Phase 5 plan must choose one ordering,
state dependencies, and define exit-code-backed proofs before either branch is
treated as authoritative.
**Approved capability direction (2026-09-11)**: semantic memory and constrained
self-evolution are Phase 5C/5D; Phase 6 is dashboards. Local RAG and measured
routing/failover first ship as evidence-producing probes, and local verified
promotion is a separate post-Phase-4 gate. Optional operator-configured
cloud/free routes always have a desktop-local fallback. See
`.planning/research/capability-expansion-design.md`.

### Phase 6: JARVIS Dashboard & Spatial HUD
**Goal**: Present a read-only board/ledger/accepted-memory view. It does not
derive, activate, or mutate memory rules; those responsibilities are Phase 5D.
It depends on accepted Phase 5 memory evidence.

**Slice 1 acceptance criteria:** a local Rich terminal snapshot renders task
states and dependency edges from a SQLite read-only connection, newest bounded
ledger evidence, activated-rule count, and supervisor health from its retained
state file. A refresh loop reads fresh snapshots only. Structural tests reject
board mutation, process spawn, network, and credential/config access; an
unchanged board-file test proves the snapshot does not write.

**Slice 2 acceptance criteria:** a loopback-only local web surface reuses the
same immutable snapshot for a self-contained task matrix, daemon-health bar,
metrics, bounded evidence terminal, JSON endpoint, and live SSE feed. Tests
prove non-loopback binding is refused, endpoint serialization is faithful, and
the web module has no board mutation, credential, external-network, or process
spawn capability.

**Slice 3 acceptance criteria:** accepted procedural rules render with task kind,
checks, and provenance citations from the same read-only snapshot, linked to tasks
only by resolved workspace path. The panel derives and activates nothing.

**Slice 4 acceptance criteria:** per-run lifecycle telemetry (claim, worktree prep,
agent, verify gate) is derived from recorded evidence only, with a stage a `dir`
workspace never reaches rendering `skipped` and its reason rather than `pending`.
Nodes are identified by task title with the hex id demoted and the workspace tagged.
Bounded active-run log tails ride inside the snapshot, so no caller-supplied text
becomes a path. The SSE client reconnects on exponential backoff behind a stream
status badge, and the server binds loopback unless a wider interface is named
explicitly. The embedded client script is parsed by a real JavaScript parser, because
substring assertions pass on a script that does not parse.

**Status: COMPLETE, accepted 2026-09-12.** `python tests/run.py` -> 324 tests,
exit 0, 159.988s. Verified live against the real board, at desktop and 375px, with
the bind surface probed (loopback and Tailscale reachable; home Wi-Fi refused).

### Phase 7: Planner Integration and Trusted Distributed Delegation
**Goal**: Let the planner consume accepted crystallized patterns and coordinate
remote execution without allowing a remote agent report to mark work verified.
The accepting authority must possess an independently checkable verify result.

### Phase 8: Remote Offload and Phone Control
**Goal**: Add measured hardware routing and confirmation-gated phone controls
only after the trusted remote protocol exists.

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8.
Branch experiments do not change this order or count as completion.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 0. Concurrency Feasibility Measurement | measurement recorded | Complete research input | 2026-09-02 |
| 1. Verified Board Substrate | 1/1 | Complete | 2026-09-08 |
| 2. Verify-Gated Single-Worker Execution | 1/1 | Complete | 2026-09-10 |
| 3. Planner + Bounded Concurrent Execution | 1/1 | Complete | 2026-09-10 |
| 4. Read-Only Telegram Observability | 4/4 slices + transport implementation verified | Complete; live operator exercise passed | 2026-09-11 |
| 5. Memory, Telegram Control, and Intake | 5A-C + memory/review slices | Complete; operator accepted mobile intake and configured-task decision | 2026-09-11 |
| 6. JARVIS Dashboard & Spatial HUD | 4/4 slices | **Complete**; terminal, web, memory panels and spatial HUD accepted. Daemon incident recovered and its root cause fixed (supervisor now restarts children with a bounded budget) | 2026-09-12 |
| 7. Planner Integration and Trusted Distributed Delegation | 1 draft plan | Plan review pending; legacy candidate violates verify gate | - |
| 8. Remote Offload and Phone Control | 0/TBD | Planned | - |
