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

## Phase 4 Objective

Build **Read-Only Telegram Observability** in four reviewed slices:

1. Correct the `dispatch_one` launcher contract and remove dead dispatcher
   code; an invalid launcher result must abort public concurrent dispatch.
2. Create isolated worktrees for same-repository concurrent tasks.
3. Classify environment failures separately from logic failures without
   weakening ledger evidence or the circuit breaker.
4. Render board and ledger evidence through a Telegram read surface with no
   remote mutation path.

## Current Checkpoint

Phase 3 is COMPLETE and independently reviewed at
`.planning/reviews/phase-3-review.md`. Phase 4 Slices 1 and 2 are accepted
(`2369613`, `8ea0d03`). Slice 2's correction in `02b90f8` now has adversarial
proof for exact-source/branch adoption and corrupt-record refusal before claim.
The canonical suite passed: `python tests/run.py` — 139 tests, exit 0,
109.003s.

The next atomic step is Phase 4 Slice 3: implement deterministic environment
versus logic classification without weakening per-attempt ledger evidence or
the circuit breaker. Review the unaccepted `slice3/error-class` worktree as
evidence only; do not merge or copy it wholesale. Do not start Slice 4 until
Slice 3 passes focused/full tests and independent review. See
`.planning/reviews/phase-4-slice-2-review-2026-09-11.md` and
`.planning/reviews/phase-audit-2026-09-10.md`.

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

1. Add and verify the two Slice 2 correction tests.
2. Obtain independent review of the correction; do not start Slice 3 until it
   passes.
3. Implement one remaining Phase 4 slice at a time in plan order.
4. Run the full suite, update `STATE.md` and this runbook checkpoint, then
   commit locally and stop for independent review before the next slice.

## Usage-Limit Handoff Prompt

Start a new Claude session from `C:\Users\ardit\tri-ai` with:

> Read `.planning/AUTONOMOUS-RUNBOOK.md`, `.planning/STATE.md`, and
> `.planning/phases/phase-4-plan.md`, `.planning/research/proposed-gaps.md`, and
> `.planning/reviews/phase-audit-2026-09-10.md` in full. You are the Phase 4
> lead. First run the canonical-checkout gate. Slice 1 is accepted; Slice 2's
> code correction in `02b90f8` lacks two adversarial tests. Implement only
> those tests and any minimal correction they expose; do not start Slice 3 or
> merge any feature branch. Work locally;
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
