# Phase 5 Procedural Memory Plan

**Status:** implemented and locally verified (2026-09-11).

## Purpose

Provide the first safe procedural-memory layer: operator-authored,
citation-backed preflight advice selected from validated episodic evidence. It
does not create rules, alter tasks, or inject anything into a worker yet.

## Contract

- Rules are strict JSON records scoped to one canonical workspace and one task
  kind, with a future expiry and at least one valid episodic citation.
- Advice is a closed checklist vocabulary, not free text or shell commands.
  No field can hold a verifier, route, model, task mutation, or process command.
- Only `preflight_advice` activation exists. Selection is deterministic,
  exact-scope, expiry-aware, citation-aware, and bounded by a character budget.
- A stale, expired, or mismatched rule is excluded but retained in its
  operator-owned source file for inspection.
- The module imports no board, worker, executor, routing, or process module.

## Proofs

1. A valid cited rule returns its exact checklist only in matching scope.
2. Citation alteration, expiry, wrong workspace, or wrong task kind excludes it.
3. Free text, command-shaped fields, unknown keys, unsupported checklist items,
   and non-preflight activation are refused at load time.
4. Selection order and budget are deterministic.
5. AST audit proves this layer cannot execute, route, or accept work.

## Exit Gate

`python -m unittest tests.test_procedural_memory`, then
`python tests/run.py`, must exit 0 before the local commit.

**Implementation record:** `memory/procedural.py` loads operator-owned
historical rule files without mutating them. Only a closed, non-executable
preflight checklist can be selected, and every selection re-validates its
episodic citations, workspace, task kind, expiry, and character budget.
