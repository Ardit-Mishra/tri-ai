# Phase 3 Plan - Planner + Bounded Concurrent Execution

**Status:** in progress - Slice 1 complete in `a86f331`; Slice 2 next
**Requirements:** GRAPH-01, GRAPH-03, EXEC-01, VERIFY-03
**Depends on:** Phase 2 (`4b37f4a`, 89 tests, exit 0)

## Goal

Persist a validate-first task graph, then execute its independent verified nodes
through a bounded local dispatcher. The planner is one-shot: it writes a graph
and exits. Workers, not the planner, own later execution. Every graph node is
created through Tri-AI's verify-gated board API, and every execution remains on
the Phase 2 claim -> agent -> verify -> ledger path.

This phase is limited to CPU/IO-bound chores. It does not claim that concurrent
local-model generation is fast. The existing measurement establishes `N=4` as
the highest tested local-model ceiling (`1.16x`, no failed requests), while the
new Phase 3 experiment must independently establish the CPU/IO dispatcher
speedup at that cap.

## Non-Negotiable Boundaries

- The canonical checkout is `C:\Users\ardit\tri-ai`; never use the unrelated
  Omniroute scratch clone.
- Read `C:\Users\ardit\AppData\Local\hermes\hermes-agent` only. Do not edit
  the Hermes kernel.
- A worker calls `board.release_stale_claims`, never the kernel directly.
- No automated push, merge, deploy, remote creation, or credential access.
- A verifier is accepted only on exit 0. Timeout, spawn error, failed exit,
  surviving process tree, failed revert, and dirty post-revert state retain the
  Phase 2 semantics.
- A cleanly reverted ordinary verify failure may receive the kernel's bounded
  fresh claim. Unsafe or unknown workspace state quarantines and stops.

## Slice 1: Transactional Planner Graph Writer

### New surfaces

- `src/planner.py`: a one-shot graph parser, validator, and writer.
- `src/board.py`: a narrow explicit workspace API needed by graph nodes. Do
  not let planner input flow through unrestricted `**kernel_kwargs`.
- `tests/test_planner.py`: persisted-graph, atomic-rejection, and planner-exit
  proof.

### Planner input contract

The strong model's output is a JSON document supplied to `planner.py`; it is
data, not executable instructions. A future model launcher may produce that
document, but this phase's correctness boundary is the validator and writer.
The planner accepts exactly one document, validates all of it in memory, then
writes it in one board transaction. It does not poll, dispatch, or stay alive.

Each node must contain:

```json
{
  "node_key": "stable-key",
  "title": "short task name",
  "prompt": "bounded task instruction",
  "workspace": {
    "kind": "dir-or-worktree",
    "path": "absolute workspace or repository anchor",
    "branch": "required only for worktree"
  },
  "verify_command": "operator-owned command",
  "verify_timeout": 120,
  "expected_artifacts": ["relative/path"],
  "parents": ["other-node-key"]
}
```

Before the first board write, reject duplicate or blank `node_key`, blank
title/prompt/verify command, missing or non-positive timeout, unknown parent,
self-parent, cycle, non-list artifacts, unsafe artifact path, unsupported
workspace kind, relative `dir` workspace, and a worktree without a repository
anchor and unique branch. Rejection returns non-zero and writes zero rows and
zero links for that graph.

Topologically sort the validated graph. Map `node_key` to the returned board
task id and call `board.create_task` for every node with already-mapped parent
ids. The planner may not call `kb.create_task`, Hermes decomposition helpers,
or raw `INSERT INTO tasks`; those bypass Tri-AI's verify columns. Its adapter
must explicitly persist `workspace_kind`, `workspace_path`, and `branch_name`
where valid, rather than relying on the Phase 2 `repo -> dir workspace`
default.

### Proving commands and deliberate breaks

```powershell
python -m unittest tests.test_planner
rg -n "kb\\.create_task|kanban_db\\.create_task|INSERT INTO tasks|decompose" src\planner.py
```

Tests must prove all of the following:

1. A two-root, one-child graph creates exactly three rows and the expected
   `task_links`; the child stays non-ready until both parents complete.
2. Invoke the planner CLI in a child process with a valid fixture. It exits
   within its bounded test timeout, has no surviving children, and leaves the
   persisted graph queryable after its process has exited.
3. Deliberately remove one node's `verify_command`. The planner exits non-zero,
   creates no nodes or links, and a direct SQL query finds no NULL verify field.
4. Deliberately add an unknown parent, duplicate key, cycle, non-positive
   timeout, and an artifact escape (`..`, absolute, symlink escape). Each is
   rejected before writing. A test with only a later worker failure is not
   sufficient: criterion 2 requires rejection at write time.
5. The structural audit above finds no bypass writer. Include a deliberately
   bad fixture using `kb.create_task` so the audit itself is proven able to
   fail.

## Slice 2: Bounded Dispatcher and Workspace Partitioning

### New surfaces

- `src/dispatcher.py`: bounded launcher for Phase 2 workers.
- `src/worker.py`: accept only the explicit, board-recorded workspace and
  expected branch supplied by the dispatcher; retain the same `run_once`
  execution path and ledger writer.
- `tests/test_phase3_concurrency.py`: measured-cap, per-worker-ledger, and
  same-repository partition tests.

The dispatcher reads `.planning/research/concurrency_results.json` and derives
the cap as the largest recorded successful concurrency: currently `4`. It must
fail loudly when the file is malformed, contains no successful positive cap,
or the requested cap exceeds the measured cap. Do not hardcode `4` in a second
place. Record the source path and selected cap in dispatcher output/evidence.

Use process workers, not threads, so each ledger entry has a distinct
`host:pid` worker identity. The dispatcher launches only Tri-AI's worker CLI or
a narrow internal target that calls the exact same `worker.run_once` path. It
does not use Hermes' dispatcher because Hermes-spawned workers bypass Tri-AI's
verify-gated execution and ledger path.

Before launch, partition workspaces:

- Independent `dir` workspaces may run together only when their canonical
  `board.workspace_key` values differ.
- A same-repository concurrent sibling must be `workspace_kind='worktree'`,
  with a distinct task-owned worktree and branch. Resolve and persist that
  workspace before the worker claims it.
- When isolation cannot be proven, leave the task ready and report a skip; do
  not serialize it invisibly, reuse a `dir` workspace, or delete a workspace.
- Every worker still prechecks clean state, checks quarantine, and performs
  `board.release_stale_claims` through the wrapper.

The dispatcher does not retry a failed worker itself. The Phase 2 worker and
kernel own failure recording, bounded retry, reclaim, quarantine, and ledger
evidence. The dispatcher only waits for its worker processes to exit and
reports their exit status.

### Measured CPU/IO proof

```powershell
python -m unittest tests.test_phase3_concurrency
python tests/run.py
```

The deterministic test creates eight independent tasks in eight isolated temp
Git repositories. It uses a test-only verifier that spends a fixed bounded
interval doing CPU/IO-like work and exits 0; the agent result is a controlled
successful fixture. Run the same graph with cap 1 and then with the cap read
from `concurrency_results.json` (currently 4).

The parallel run passes only when all are true:

- the selected cap is derived from the JSON evidence, not a literal;
- all eight nodes reach `done`;
- there are exactly eight ledger entries, one per task and run id;
- worker identities contain at least two distinct process ids;
- every entry has `verify_exit=0`, `verify_outcome='passed'`, positive seconds,
  and existing full agent and verifier log files;
- worker execution intervals overlap; and
- `parallel_wall <= serial_wall * 0.70`.

The test must deliberately run a broken dispatcher that launches one worker at
a time and assert that its timing/overlap predicate fails. A task-count-only
test is not a concurrency proof.

The same-repository test creates two ready siblings pointed at one `dir`
workspace and asserts the dispatcher never starts both concurrently. A second
case supplies two unique task worktrees and asserts their resolved paths and
branches differ before simultaneous starts. It must not use the live Tri-AI
checkout.

## Slice 3: Linked-Graph Failure Isolation

### New surface

- `tests/test_phase3_failure_isolation.py` with fixture helpers only as needed.

Build this actual DAG, using `task_links`, not a flat task list:

```text
root
|- failing-parent
|  `- blocked-descendant
|- passing-sibling-b
`- passing-sibling-c
```

Complete `root`, then dispatch with the measured cap. `failing-parent` must
have a deliberate non-zero verify command; both siblings have passing commands
and no dependency on the failing branch. The test proves:

- the failing attempt is ledgered with `verify_outcome='failed'`, then follows
  the existing bounded retry/circuit-breaker state machine;
- `blocked-descendant` remains unclaimable because its parent never becomes
  `done`;
- both independent siblings reach `done` during the same dispatcher run;
- their ledger entries exist with valid worker/run identities; and
- no graph-wide failure flag or dispatcher early-return prevented the siblings.

Run the test against a deliberately broken dispatcher that stops after the
first failed child and assert the sibling completion assertion fails. This
guards against a test whose success comes from completing siblings before the
failure path was introduced.

## Structural Safety Audit

Extend `tests/test_safety_boundary.py`'s named closure and AST checks for
`planner` and `dispatcher`. The audit must reject direct process creation or
raw Git calls outside the existing qualified gateways. `dispatcher` may launch
only its fixed Tri-AI worker argv with no shell; planner has no process gateway
in this slice. The fixed verifier remains the sole deliberately unconstrained
operator command through `executor.run_verify`.

Also assert all stale-claim references in runtime code route through
`board.release_stale_claims`, and that planner/dispatcher source contains none
of `push`, `merge`, deploy tooling, credential-file reads, or direct Hermes
task creation.

## Completion Gate

Phase 3 is complete only after each command below exits 0 from the canonical
checkout and its output is recorded in `STATE.md`:

```powershell
python -m unittest tests.test_planner
python -m unittest tests.test_phase3_concurrency
python -m unittest tests.test_phase3_failure_isolation
python -m unittest tests.test_safety_boundary
python tests/run.py
```

Keep failed outputs and any test ledger/run logs. Update the Phase 3 checkpoint
after each verified slice, commit locally, and stop for independent commit-level
review before Phase 4. Never push automatically.

## Handoff Prompt

> Work only in `C:\Users\ardit\tri-ai`. Read `.planning/STATE.md`,
> `.planning/AUTONOMOUS-RUNBOOK.md`, and `.planning/phases/phase-3-plan.md` in
> full. Implement exactly one verified Phase 3 slice at a time, beginning with
> the transactional planner and its adversarial tests. Do not edit Hermes, use
> the temp Omniroute clone, push, merge, deploy, create a remote, or read
> credentials. Run `python tests/run.py` before every local commit. If context
> or usage is low, update `STATE.md` with branch, commit, exact command output,
> retained evidence paths, and the next single atomic step, then stop.
