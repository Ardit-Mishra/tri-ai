# Phase 5 Semantic Memory Plan

**Status:** implemented and locally verified (2026-09-11).

## Contract

Semantic memory is a deterministic graph derived from current episodic facts and
currently valid procedural rules. It creates only closed-set
`depends_on`, `recorded`, `produced`, `retries_from`, and `cites`
edges. Every edge retains one or more source citations; stale citations exclude
the derived fact. It imports no board, worker, executor, router, or process
module and cannot mutate acceptance.

## Exit Gate

`python -m unittest tests.test_semantic_memory`, then `python tests/run.py`,
must exit 0 before the local commit.

**Implementation record:** `memory/semantic.py` deterministically derives
task/run/outcome/dependency/retry/rule-citation edges only while their original
episodic citations validate. It does not persist an independent truth store.
