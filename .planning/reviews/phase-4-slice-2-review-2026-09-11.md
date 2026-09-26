# Phase 4 Slice 2 Review — 2026-09-11

**Scope:** `8ea0d03 Prove worktree ownership correction`
**Verdict:** accepted after independent re-review and the canonical full-suite
gate.

## What the Tests Prove

`tests/test_worktree_materialization.py` adds the two outstanding adversarial
proofs for `02b90f8`:

1. A pre-existing deterministic target is adopted only when it is a linked
   checkout of the declared source on the declared branch. The test makes the
   source and branch dimensions fail independently: a foreign repository owns
   one target on the exact expected branch, and the declared source owns a
   second target on a different branch. Both are skipped, stay `ready`, retain
   no ownership record, and remain on disk as evidence.
2. A durable record that claims the expected branch but whose on-disk worktree
   is actually on another branch is skipped before `dispatcher` reaches its
   launcher. The board remains `ready` and unclaimed; its target path and the
   on-disk target remain intact as evidence.

The second proof deliberately makes the recorded branch and board workspace
path agree, so the resolver reaches `executor.verify_worktree` rather than
short-circuiting on metadata.

## Verification

- `python -m unittest tests.test_worktree_materialization` — 7 tests, exit 0,
  5.435s after the final source-linkage correction.
- `python tests/run.py` — 139 tests, exit 0, 107.568s.

## Independent Review

Two background Claude reviewer launches failed because Windows process argument
handoff truncated their prompts to `You`; they are not treated as reviews. A
subsequent read-only Claude review found that the first test draft did not make
the ownership gate fail. Its re-review then found the foreign-source fixture
also had the wrong branch, which failed to isolate source linkage. After the
fixture was corrected, the final scoped re-review returned **No blocking
findings**. The reviewer was permission-gated from running tests, so only this
checkout's command exit codes count as test evidence.

Two initial focused test failures were assertion-placement mistakes in the new
test, not production behavior: the recorded target correctly remained on the
task row after rejection. The final tests assert that retained target directly.

## Next Atomic Step

Phase 4 Slice 3: integrate deterministic error classification for environment
failures without weakening the ledger or circuit breaker. Start from the
accepted canonical branch; do not reuse the unaccepted `slice3/error-class`
candidate without a line-by-line review against the active plan.
