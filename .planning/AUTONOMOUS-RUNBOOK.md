# Autonomous Delivery Runbook

This is the resumable operating contract for Codex or Claude. Follow it before
any implementation work. It exists so a usage-limit handoff changes the model,
not the standards, state, or checkout.

## Canonical Checkout Gate

The only canonical checkout is `C:\Users\ardit\tri-ai`.

```powershell
Set-Location C:\Users\ardit\tri-ai
git rev-parse --show-toplevel
git status --short --branch
Get-Content .planning\STATE.md
```

The first command must resolve to `C:/Users/ardit/tri-ai`. Stop if it does not.
Do not use or merge the Omniroute temp clone under
`C:\Users\ardit\AppData\Local\Temp\claude\...\scratchpad\p0a\tri-ai`; it is unrelated history.

## Standing Boundaries

- Never push, merge, deploy, create a remote, or read credentials.
- Never edit the Hermes kernel. Read `C:\Users\ardit\AppData\Local\hermes\hermes-agent` only.
- Acceptance is the verify command's exit code, never an agent report.
- Preserve evidence. A cleanly reverted verifier failure is a bounded fresh
  claim; unknown workspace state (surviving process tree, failed revert, or
  dirty post-revert assertion) quarantines and hard-stops.
- Run `python tests/run.py` before every local commit. Keep failures and logs;
  do not use `git reset --hard`, delete evidence, or conceal a failed attempt.

## Phase 2 Baseline

Phase 2 is complete locally on `phase-2/worker-assign`. The post-review suite
passed 89 tests, exit 0, in 93.951 seconds on 2026-09-10. It is deliberately
unpushed. Do not revisit it except for a concrete regression.

## Phase 3 Objective

Build **Planner + Bounded Concurrent Execution** against the four roadmap
criteria:

1. The planner persists a task graph through the board API and exits.
2. The board rejects planner-written tasks without `verify_command` at write
   time.
3. Independent CPU/IO chores run faster with a measured worker cap, with every
   attempt represented in the ledger.
4. A failed branch does not stall unrelated branches.

The Phase 2 `dir` workspace design is single-worker only. Phase 3 must use
per-subtask worktrees or another proven repo partition before it starts a
second worker on one repository.

## Current Checkpoint

Phase 3 is COMPLETE locally on `phase-2/worker-assign`, verified by
`python tests/run.py`: 132 tests, exit 0, 117.560s on 2026-09-10.
Slice 1 (`a86f331`): planner graph writer, 98 tests. Slice 2 (`fa2bb52`):
cap-derived concurrent dispatcher with workspace partitioning,
`board.ready_tasks()`, `tests/test_phase3_concurrency.py` (28 tests), and
`DispatcherCannotBypassOrPush` safety audit, 130 tests. Slice 3 (`76dc2c6`):
linked-graph failure isolation — `src/dispatcher.py` dispatch loop
simplified (re-read the board each wave; no `dispatched_ids`; `max_waves`
bound) and `tests/test_phase3_failure_isolation.py` (2 tests) driving the
real worker path through the kernel's own circuit breaker, parent gate, and
ledger. The next atomic step is **independent commit-level review of the three
Phase 3 commits** against the Completion Gate before Phase 4 (read-only
Telegram observability). Do not begin Phase 4 until that review passes.

## Safe Fan-Out

Fan out only read-only investigation first. Do not let multiple agents edit the
same checkout.

### Coordination Protocol

Subagents do not share transient chat context. The lead is their coordinator:
give each subagent one read-only question, collect its evidence, and record the
decision, file/line references, command result, and next atomic step in
`STATE.md` before assigning the next writable slice. Every subagent must read
`STATE.md`, this runbook, and the active phase plan before it starts; every
later subagent therefore sees the earlier findings through the repository, not
through an assumed live conversation. Only the lead edits production code.

1. **Kernel mapper:** identify the exact read-only Hermes board APIs for graph
   creation, parent links, worktree lifecycle, claim, completion, failure, and
   cancellation. Return file/line evidence and no code.
2. **Concurrency designer:** turn `research/concurrency_results.json` into a
   reproducible CPU/IO worker-cap experiment. Define the measurement command,
   success threshold, and deliberate branch-failure case. Return a test matrix
   and no code.
3. **Planner contract reviewer:** define the strict planner output schema and
   how every generated child is routed through `board.create_task` with a
   verify command. Identify injection and missing-oracle failure cases. Return
   findings and no code.
4. **Adversarial reviewer:** attack the planned isolation, stale-claim, and
   ledger assertions. Return only reproducible findings.

One lead agent synthesizes those reports into
`.planning/phases/phase-3-plan.md`. Every criterion needs a named command that
can fail for a named deliberate break before implementation begins.

## Implementation Order

1. Commit the Phase 2 baseline locally if it is not already clean.
2. Write and review the Phase 3 plan from the four reports.
3. Implement planner persistence and its reject-at-write-time tests.
4. Implement worktree/partitioned worker execution and the measured-cap
   harness. Do not guess the cap.
5. Add the failed-branch isolation test and full ledger assertions.
6. Run the full suite, update `STATE.md` and this runbook checkpoint, then
   commit locally. Stop for review before moving to Phase 4.

## Usage-Limit Handoff Prompt

Start a new Claude session from `C:\Users\ardit\tri-ai` with:

> Read `.planning/AUTONOMOUS-RUNBOOK.md`, `.planning/STATE.md`, and
> `.planning/phases/phase-4-plan.md` (if present, else the roadmap in
> `.planning/PROJECT.md`) in full. You are the Phase 4 lead. First run the
> canonical-checkout gate, then independently review the three Phase 3 commits
> (`a86f331`, `fa2bb52`, and the Slice 3 commit) against the plan's Completion
> Gate and the four roadmap criteria. Do not begin Phase 4 (read-only Telegram
> observability) until that review passes. Work locally;
> never push, merge, deploy, create remotes, or read credentials. Update state
> after every verified slice. If context or usage runs low, write the exact
> completed evidence, current commit, failing command, and next atomic step to
> `.planning/STATE.md`, then stop.

## Checkpoint Format

At every verified slice, append to `STATE.md`:

- current commit and branch;
- files changed;
- exact test command, count, exit code, and duration;
- any failed command and retained evidence path;
- next single atomic step.

Do not mark a phase complete until every roadmap success criterion has its
proving artifact and the full suite exits 0.
