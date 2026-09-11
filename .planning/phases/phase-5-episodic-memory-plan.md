# Phase 5 Episodic Memory Plan

**Status:** implemented and locally verified (2026-09-11).

## Purpose

Build the first memory tier as a queryable, provenance-preserving index of
existing ledger lines. The ledger remains the source of truth. The index
contains no acceptance authority, no model call, and no board mutation.

## Contract

- Input is a JSONL ledger supplied by the operator. Each nonblank line must be
  a JSON object; a malformed line hard-stops before index mutation.
- Each indexed event retains its canonical source path, one-based ledger line,
  and SHA-256 digest of that exact source line. The citation validates only if
  the same line still exists with the same digest.
- A sync is transactional: it parses the whole ledger first, then marks only
  the source's no-longer-observed rows stale and upserts observed rows.
  Historical rows are retained as invalidated evidence, never deleted.
- Queries return only current records by default and may filter on task id,
  outcome, or failure class. Every returned record carries a citation.
- SQLite uses journal_mode=DELETE because the installed SQLite is in the
  documented WAL-reset vulnerable range.

## Proofs

1. A valid ledger creates queryable outcome/failure facts with exact citations.
2. An appended ledger preserves prior citations; changing or removing a cited
   line invalidates its old citation and makes it non-current after sync.
3. A malformed ledger fails before any current-row state changes.
4. Outcome/task/failure filters produce only matching current facts.
5. AST audit proves the memory module cannot import the board, worker, executor,
   routing broker, or process-spawn modules.

## Exit Gate

`python -m unittest tests.test_episodic_memory`, then
`python tests/run.py`, must exit 0 before the local commit.

**Implementation record:** `memory/episodic.py` parses the full input before
opening a write transaction, indexes source-line digests under SQLite
`journal_mode=DELETE`, and marks replaced source rows non-current without
deleting them. It is a derived read model only.
