# Phase 4 Plan — Read-Only Telegram Observability (+ captured-gap companion slices)

**Status:** in progress. Slice 1 is accepted (`2369613`). Slice 2 is implemented
(`a71ff9e`, corrected by `02b90f8`) but awaits adversarial regression proof for
the review corrections; Slices 3-4 have unaccepted candidate worktrees. See
`.planning/reviews/phase-audit-2026-09-10.md`. This plan captures the Phase 4
milestone and the companion work from `research/proposed-gaps.md` that attaches
to it.
**Requirement:** the roadmap's "board observable from Telegram" — read-only view first
(`.planning/research/FEATURES.md` v1 list, Telegram read-only entry).
**Depends on:** Phase 3 (`12288a7`, 132 tests, exit 0 — review passed)
**Drives:** Phase 5 (Telegram write control + inbox→execute ingestion).

## Goal

Let the operator watch the running graph from a phone: which nodes are unclaimed, claimed,
in-flight, done, failed; what each attempt's verify exit was; what its logs said. **Read-only.**
No mutating action from Telegram in this phase. The dispatcher's `--ledger`/`--runs-dir` outputs
and the ledger rows the failure-isolation tests assert are the raw material this consumes.

The control-plane rule stays pinned (`STATE.md`): do not mutate a live graph before reclaim/retry/
pause primitives are proven. Read-only Phase 4 proves the *view*; Phase 5 proves the *primitives
people will actually trust* before any mutation is reachable remotely.

## Non-Negotiable Boundaries (carried from previous phases)

- Canonical checkout `C:\Users\ardit\tri-ai` only; never the Omniroute scratch clone.
- Read `C:\Users\ardit\AppData\Local\hermes\hermes-agent`, never edit it.
- `board.release_stale_claims`, never the kernel directly.
- No automated push, merge, deploy, remote creation, or credential access. The Telegram read
  surface must not acquire any write capability in this phase — the safety boundary test for the
  phase must assert the read surface ships **no** mutation path (analogue of the planner/dispatcher
  bypass audits).
- $0 marginal cost: local models only, no metered API. Telegram read must not create accounts
  (`PROJECT.md`, Out of Scope).
- A verifier is accepted only on exit 0. The claim → precheck → gate → agent → verify → accept/
  revert path and the ledger *shape* are unchanged in this phase. Slice 3 is the one deliberate
  refinement: it adjusts *which failure classes mutate graph status* — every attempt still gets
  its ledger line (criterion 3, "every attempt represented in the ledger"), only the
  breaker/status effect is reclassified.

## Slice 1 — `dispatch_one` env contract + `filter_running` (gap #7)

**Why first:** a correctness-of-contract fix, no new surface; unblocks the real `--once` launcher;
tiny. `src/dispatcher.py:205-211` builds an env dict that never reaches the launcher; its docstring
promises a wiring nothing honors. `filter_running` (`src/dispatcher.py:128`) is dead code
(definition + tests only, never called by `dispatch()`).

**Proof:** delete the dead env dict and the misleading docstring — the launcher is a subprocess and
inherits `os.environ` already, so the explicit dict is redundant. Remove `filter_running` (tested but
never called by `dispatch()`; wiring it in adds complexity for no proven benefit). Add a
deliberately-wrong launcher assertion so the contract is proven able to fail (the
safety-boundary "a gate that cannot fail is not a gate" lesson). Full suite stays green.

## Slice 2 — Worktree creation for same-repo concurrency (gap #2)

**Why this phase:** it completes the Phase 3 promise (`AUTONOMOUS-RUNBOOK.md:52`) that was cut to
serialization + partition at Slice 2, and it is the execution-side gap with the largest payoff.

`board.create_task` already accepts `workspace_kind='worktree'` + `branch_name` (`src/board.py:
219-233`), but nothing creates the worktree — a task carries a path that must already exist. This
slice resolves the task's worktree before the worker claims the task:
- the worker's precheck/revert semantics stay intact; Tri-AI does **not** remove
  a failed worktree automatically because it is failure evidence. An operator
  alone may decide whether to remove it later;
- when isolation cannot be proven, leave the task ready and report a skip — never reuse a `dir`
  workspace, never delete one (Phase 3 plan boundary);
- worktree creation goes through a narrow, audited gateway — not a new bypass. The AST safety
  audit (`tests/test_safety_boundary.py`) must cover the new creator, and it must not touch
  `executor.git`'s allowlist in a way that widens it.

**Proof:** a same-repo test where two ready siblings pointed at one repository are given distinct
worktrees; assert resolved paths and branches differ before simultaneous starts, per the Phase 3
plan's second same-repo case. The readiness claim must survive a deliberate "worktree create
fails" break (task stays ready, skipped, not mis-serialized).

## Slice 3 — Error classification: environment vs. logic (gap #1)

The circuit breaker (2 consecutive failures → `blocked`, `src/worker.py:485`) exists and stays.
This is the first change to what counts as a "failure" against a node since the circuit breaker
shipped in Phase 2. This slice adds the bucket: an environment-class failure (OOM, package-mirror
time-out, network) backs off **without** recording against the node; a logic-class failure
(assertion, syntax) is the only thing that records and trips the breaker. Re-visit
`.planning/research/FEATURES.md`'s anti-backoff note — it concerns protecting a shared external
service, which is a different claim from not confusing two failure classes, and the plan must say
so explicitly rather than silently override a documented decision.

**Proof:** a deliberate-break matrix, not a measurement: for each environment failure (spawn error,
timeout, a stubbed mirror that returns non-zero with a retryable stderr pattern) assert the graph
status is untouched and a bounded backoff elapses; for each logic failure assert the node records
and the breaker still trips at its limit. The classifier is deterministic — exit code + already-
discriminated `executor` outcome + stderr pattern.

## Slice 4 — Read-only Telegram observability (the Phase 4 milestone)

The roadmap's actual ask. Consume the ledger/board state the earlier phases produce and render it
to a phone channel. The content already exists as ledger rows (`host:pid`, `run_id`, `verify_exit`,
`verify_outcome`, timings, log paths) and board status; this slice surfaces it read-only.

- The read surface ships **no** mutation path — asserted structurally (the phase's own safety-boundary
  test), and no account creation, no credential read.
- What a reader can learn: per-graph node status, per-attempt verify outcome + exit, logs.
  Command layout must be reviewed against the honest-claims rule: it reports exactly what the ledger
  shows, nothing more.
- Deliberately out of scope in this slice: any cancel/retry/skip verb, and any inbox→execute of a
  Telegram message. Those are Phase 5.

**Proof:** a repeatable read command that, against a board in a known state (a test-built graph with
a failure-isolation shape), renders the graph exactly as the ledger/board say it is. A deliberately
"one node ahead of the ledger" fixture must render that gap, so the read is proven to reflect
evidence rather than a summary.

## Phase 5 (preview, not started)

- **Memory subsystem — episodic + procedural (gap #9):** SQLite run index + markdown summaries
  (compiled truth + timeline), rules engine with citation validation, skill files. Foundation for
  everything after Phase 4. Design at `.planning/research/memory-subsystem-design.md`.
- **Telegram write control (#8's control half):** retry/cancel/pause, reachable and deliberately
  gated on the primitives being proven in Phase 4/5.
- **Telegram inbox→execute (#3):** closes the `PROJECT.md:77-78` documented gap. After read-only
  proves the view.

## Phase 6 (preview, not started)

- **Memory subsystem — semantic + self-evolution:** knowledge graph extraction (zero LLM calls),
  post-mortem reflection, failure-driven rule creation, pattern crystallization.
- **JARVIS dashboard:** terminal TUI + local web visualization — the neural network view of the
  running graph. Reads board + ledger + memory system status. Never writes.

## Phase 7 (preview, not started)

- **Planner integration:** planner loads learned patterns and suggests macro templates. Gated on
  the pattern crystallization being proven reliable.
- **Distributed delegation — daemon foundation (gap #10):** tri-agent daemon on the desktop,
  HTTP API for remote workers, workspace sync via git archive. Design at
  `.planning/research/distributed-delegation-design.md`.

## Phase 8 (preview, not started)

- **Distributed delegation — remote workers + offload:** laptop daemon (proxy), offload detection
  (VRAM/timeout/OOM triggers), heartbeat protocol, offload ledger entries.
- **Phone controller:** Telegram commands for multi-node task creation targeting hardware tiers.

## Completion Gate (Phase 4)

Each command below exits 0 from the canonical checkout and its output is recorded in `STATE.md`,
alongside the model's filters for the read surface:

```powershell
python -m unittest tests.test_phase4_observability   # name TBD when the slice lands
python -m unittest tests.test_safety_boundary
python tests/run.py
```

Keep failed outputs and any test ledger/logs. Update the Phase 4 checkpoint after each verified
slice, commit locally, stop for independent review before Phase 5. Never push automatically.

## Handoff Prompt

> Work only in `C:\Users\ardit\tri-ai`. Read `.planning/AUTONOMOUS-RUNBOOK.md`, `.planning/STATE.md`,
> `.planning/phases/phase-4-plan.md`, and `.planning/research/proposed-gaps.md` in full. Implement
> one verified Phase 4 slice at a time, in order: the `dispatch_one` contract fix, worktree
> creation, error classification, then read-only Telegram observability. Do not edit Hermes, use
> the temp Omniroute clone, push, merge, deploy, create a remote, or read credentials. Run
> `python tests/run.py` before every local commit. If context or usage is low, update `STATE.md`
> with branch, commit, exact command output, retained evidence paths, and the next single atomic
> step, then stop.
