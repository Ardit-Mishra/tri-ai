# Phase and Plan Audit — 2026-09-10

**Scope:** canonical checkout `C:\Users\ardit\tri-ai` at `00671d9`, plus every
registered Tri-AI worktree. Commit messages were treated as claims; the commands
listed below are the evidence.

## Accepted History

| phase | status | evidence |
|---|---|---|
| 1 — verified board substrate | complete | published Phase 1 history; plan records 22 tests |
| 2 — verify-gated worker | complete | Phase 2 plan records independent review and 89 tests, exit 0 |
| 3 — planner and bounded dispatch | complete | `76dc2c6` plus independent review `12288a7`; 132 tests, exit 0 |
| 4 / Slice 1 — launcher contract | accepted | `2369613`; independent review passed |

The canonical full suite was run during this audit: `python tests/run.py` at
`00671d9` completed **137 tests, exit 0, 149.886s**.

## Phase 4 Status

**Slice 2 is implemented but not accepted.** `a71ff9e` created task-owned
worktrees. Review found two P1 state gaps, and `02b90f8` changed the code to
re-prove a worktree and adopt an exact deterministic leftover under an outer
write transaction. The code change has no adversarial regression test for
either condition: a crash-style pre-existing deterministic target, or a board
record pointing to the wrong branch. The 137-test suite therefore proves no
general regression, not that either correction fires. Add those two tests and
obtain review before Slice 3.

**Slice 3 is not implemented.** Worktree `slice3/error-class` contains
uncommitted `src/failure_class.py` and an uncommitted test file. Its full suite
failed: **149 tests, exit 1, 149.868s**. The immediate failure is that
`ConnectionResetError: [WinError 10054]` is asserted as environment class but
does not match the classifier. More importantly, the file stops after table
tests: it does not wire classification into `worker.execute_task`, prove
environment failures preserve graph status while ledgering the attempt, or
prove logic failures still trip the circuit breaker.

**Slice 4 is not implemented.** `slice4/telegram-read` has committed renderer
candidate `f2b18dd`, but its full suite failed: **157 tests, exit 1, 154.066s**
(two test/code contract mismatches). Its `/logs` command returns log *paths*,
not the full captured stdout/stderr required by ROADMAP criterion 2; no actual
Telegram transport is implemented. Do not call it a Telegram surface yet.

## Later-Phase Candidates

These branches are experiments, not completed or merge-ready phases. They all
forked from `00671d9`, so none contains later Phase 4 work.

| branch | observed state | audit decision |
|---|---|---|
| `phase5/memory-core` | committed episodic (`7ba614a`) and semantic (`f889391`) modules; uncommitted procedural module/tests; full current suite **191 tests, exit 0, 149.880s** | partial only. 5D evolution, worker integration, and the required self-evolution proofs are absent. `EpisodicMemory` enables SQLite WAL despite this runtime reporting SQLite `3.50.4`, a version already guarded elsewhere for the WAL-reset issue. Semantic clustering knowingly substitutes different fields for the documented signature. Resolve those before considering a Phase 5 merge. |
| `phase6/dashboard` | committed reader/web candidate; uncommitted TUI module/tests; full current suite **168 tests, exit 1, 150.317s** | not accepted. The current failure is a stale rendering assertion. It also runs before its Phase 5 memory dependency is complete, so it can only remain an isolated prototype. |
| `phase7/daemon` | committed candidate `33b4a28`; full suite **152 tests, exit 0, 150.221s** | rejected as unsafe. `POST /task/complete` marks a running task done and manufactures `verify_exit=0` without executing or independently receiving a verifiable gate result. It violates the project’s central acceptance rule. The branch also includes direct `git archive`/`git apply` subprocess calls outside the existing audited executor boundary, and workspace sync applies an arbitrary remote diff before verification. |

## Planning Corrections

1. The roadmap, project charter, phase plans, state file, and runbook had
   stale Phase 3/4 status. Their source of truth is now this audit plus git
   history and executable evidence.
2. Phase 4 remains the active milestone. The next atomic action is to add the
   two adversarial Slice 2 correction tests and review `02b90f8`; do not start
   Slice 3 implementation until that passes.
3. Phase 5–8 designs remain useful, but their branch work stays explicitly
   experimental until it is rebased onto the accepted predecessor, has a
   passing full suite, satisfies its planned oracle, and passes review.
4. A remote daemon may coordinate claims, but it cannot accept work from a
   remote worker’s report. Any revised Phase 7 plan must make the trusted
   verifier run on the authority that accepts the task, or otherwise provide
   an independently checkable exit-code artifact. A claimed result alone is
   not evidence.
