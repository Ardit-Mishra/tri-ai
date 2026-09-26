# Independent Phase 3 Review

**Reviewer:** Claude Code (self-review, no second-seat reviewer available)
**Date:** 2026-09-10
**Commits reviewed:** `a86f331` (Slice 1), `fa2bb52` (Slice 2), `76dc2c6` (Slice 3)
**Baseline:** 132 tests, exit 0, 109.816s (`python tests/run.py`)

## Method

1. Read the Completion Gate (phase-3-plan.md lines 232-247) and the four roadmap criteria.
2. Ran the full test suite: 132 tests, exit 0.
3. Read every source file changed by the three slices: `src/planner.py`, `src/dispatcher.py`, `src/board.py`, `src/worker.py`, `tests/test_planner.py`, `tests/test_phase3_concurrency.py`, `tests/test_phase3_failure_isolation.py`, `tests/test_safety_boundary.py`.
4. Examined the Slice 3 dispatcher diff to verify the `dispatched_ids` removal.
5. Checked each roadmap criterion against the evidence.

## Completion Gate Verification

| Command | Exit | Evidence |
|---------|------|----------|
| `python -m unittest tests.test_planner` | 0 | 7 tests, all pass |
| `python -m unittest tests.test_phase3_concurrency` | 0 | 28 tests, all pass |
| `python -m unittest tests.test_phase3_failure_isolation` | 0 | 2 tests, all pass |
| `python -m unittest tests.test_safety_boundary` | 0 | 30 tests, all pass |
| `python tests/run.py` | 0 | 132 tests, 109.816s |

All five Completion Gate commands pass.

## Roadmap Criteria

### Criterion 1: The planner persists a task graph through the board API and exits.

**PASS.** `planner.write_graph` (line 176) calls `board.create_task` inside a `kb.write_txn` for every node, returns a `node_key -> task_id` map, and the module exits. The CLI (`main`, line 198) reads one JSON file, writes the graph, prints the ID map, and returns 0. The subprocess test (`test_cli_exits_after_persisting_the_graph`, test_planner.py:186) launches the planner as a real child process with a 20-second timeout, asserts exit 0, and verifies 3 rows exist. The topological sort (`_topological_order`, planner.py:76) detects cycles and parents-before-children ordering. The mid-graph write failure test (test_planner.py:102) proves atomic rollback: a RuntimeError on the second `create_task` leaves zero rows and zero links.

### Criterion 2: The board rejects planner-written tasks without verify_command at write time.

**PASS.** `board.create_task` (board.py:203) raises `ValueError` when `verify_command` is blank, before any row is written. The planner's `validate_graph` (planner.py:117) also rejects blank verify_command during validation. The test (`test_missing_oracle_rejects_the_whole_graph_before_any_write`, test_planner.py:87) proves zero rows and zero links after rejection. The safety audit (`PlannerCannotBypassTheVerifyWriter`, test_safety_boundary.py:186) confirms the planner source contains no direct kernel create_task, raw INSERT, or decompose calls.

### Criterion 3: Independent CPU/IO chores run faster with a measured worker cap, with every attempt represented in the ledger.

**PASS.** Cap derivation (`dispatcher.read_cap`, dispatcher.py:41) reads `concurrency_results.json` and extracts the largest successful concurrency. The timing test (`test_parallel_run_faster_than_serial`, test_phase3_concurrency.py:437) creates 8 tasks, runs dispatch at cap=1 (serial) and cap=4 (parallel), and asserts `parallel_wall <= serial_wall * 0.70`. The test proves real parallelism via `_assert_intervals_overlap` (overlapping start/end windows) and the negative control (`test_broken_dispatcher_does_not_overlap`, line 514) proves sequential execution produces no overlap. Ledger entries: the failure isolation test (`test_failing_branch_blocks_only_its_own_descendant`, test_phase3_failure_isolation.py:215) asserts both failed attempts are ledgered with `verify_outcome='failed'`, `verify_exit=7`, valid `worker` (host:pid regex), and `run_id`. All 8 nodes in the timing test reach `done` with one entry each (asserted via `result.all_passed` and `len(results) == 8`).

### Criterion 4: A failed branch does not stall unrelated branches.

**PASS.** The Slice 3 DAG test (test_phase3_failure_isolation.py:215) builds the exact DAG from the plan, completes root, dispatches failing-parent (which fails twice → `blocked` + `gave_up`), and proves both independent siblings reach `done` in the same dispatcher run. The blocked descendant stays `todo` (the kernel's `claim_task` demotes it). The negative control (`test_broken_dispatcher_that_stops_on_first_failure_leaves_siblings_ready`, line 277) uses a deliberately broken dispatcher that stops at the first failure and asserts the sibling-completion assertion FAILS — proving the positive result does not come from a test artifact.

## Findings

### F1 — Dead environment variables in `dispatch_one` (design inconsistency, not a test failure)

**File:** `src/dispatcher.py:205-211`

`dispatch_one` sets `TRIAI_BOARD_DB`, `TRIAI_LEDGER`, and `TRIAI_RUNS_DIR` on a local `env` dict, but then calls `launcher(task_id)` — the `env` dict is never passed to any subprocess or used by the launcher. The launcher callable receives only `task_id` and must resolve the board path independently.

**Impact:** In the test suite, every launcher that needs the board receives `board_path` as a constructor parameter (e.g., `_MockLauncher(board_path=...)`, the failure-isolation `_make_launcher(self.db_path)`), so the dead env vars cause no test failure. The Slice 2 subprocess test (`test_workers_run_with_distinct_pids`, test_phase3_concurrency.py:788) sets its own env vars when launching worker stubs via `subprocess.Popen`.

**Failure scenario:** If a future production launcher relies on `dispatch_one` to set `TRIAI_BOARD_DB` (the natural expectation given the docstring "Sets `TRIAI_BOARD_DB` so the worker subprocess resolves to the same board"), it would silently get the parent process's value instead. This is a latent defect in the dispatch_one contract, not a current test gap.

**Verdict:** Not a Completion Gate blocker. Clean up by either removing the env-var code or making `dispatch_one` actually pass the env to the launcher (e.g., via a signature change or `os.environ` context manager).

### F2 — `filter_running` is defined but never called in the dispatch loop (dead code)

**File:** `src/dispatcher.py:128-145` (definition), `src/dispatcher.py:278-290` (inline conflict detection in dispatch loop)

The `filter_running` function is defined and tested (`FilterRunningTest`, test_phase3_concurrency.py:296), but the `dispatch()` function does its own inline workspace-conflict detection via `running_keys` instead of calling `filter_running`. This was true in Slice 2 as well — the function was dead code from its first commit.

**Impact:** None — the inline detection is correct and equivalent. The tests exercise the function in isolation, which is fine. But the function is unreachable production code.

**Verdict:** Not a Completion Gate blocker. Remove the function or replace the inline detection with a `filter_running` call.

### F3 — Safety boundary test for dispatcher process detection is overly broad (test design, not a false negative)

**File:** `tests/test_safety_boundary.py:669-692`

`test_no_direct_process_creation_in_dispatcher` checks every `ast.Call` node whose attribute name matches any `PROCESS_ATTRS` entry (e.g., `run`, `Popen`, `call`, `spawn`, etc.) — regardless of which object the method is called on. A call to `dict.update()` would not match (different attribute name), but a hypothetical call to `config.run()` or `result.spawn()` in the dispatcher would be flagged even though it is not a process-creation call.

**Current behavior:** The test passes today because `dispatcher.py` contains no calls whose attribute name matches any `PROCESS_ATTRS` entry. The false-positive risk is theoretical.

**Verdict:** Not a Completion Gate blocker. The test is conservative (over-reports, never under-reports), which is the correct direction for a safety audit. It could be tightened by checking the receiver object's module, but that is a refinement, not a defect.

### F4 — Slice 2 commit message documents `dispatched_ids` that was removed in Slice 3 (historical inconsistency, no functional impact)

**Evidence:** Slice 2 commit message (fa2bb52) says "dispatch() tracks dispatched_ids to prevent re-dispatch on re-read." Slice 3 (76dc2c6) removes `dispatched_ids` entirely and the new design re-reads the board each wave so reclaimed tasks reappear.

**Impact:** None. The Slice 3 commit message correctly documents the removal. This is a normal design evolution within the same phase.

**Verdict:** Not a defect. Noted for completeness.

## Summary

The three Phase 3 commits satisfy all four roadmap criteria and all five Completion Gate commands. The test suite is 132 tests, exit 0.

Four findings, none blocking:
- **F1** (design inconsistency): dead env vars in `dispatch_one` — latent risk for future integration, not a current test failure.
- **F2** (dead code): `filter_running` defined but never called.
- **F3** (test design): safety boundary process-detection test is overly broad but correctly conservative.
- **F4** (historical): Slice 2 commit message references `dispatched_ids` removed in Slice 3.

**Recommendation:** Phase 3 passes the review. Proceed to Phase 4 (read-only Telegram observability). F1 should be addressed before the real dispatcher-to-worker subprocess integration (likely Phase 4 or 5), when a production launcher will actually depend on `dispatch_one`'s env-var contract.
