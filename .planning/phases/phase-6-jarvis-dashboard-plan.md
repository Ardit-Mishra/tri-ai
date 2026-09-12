# Phase 6 Plan - Read-Only JARVIS Dashboard

**Status:** Slice 1 accepted locally (2026-09-11)

## Purpose

Give the operator one local terminal view of authoritative board lifecycle
state, recent ledger evidence, accepted procedural-memory count, and daemon
supervisor health. JARVIS is an observability surface, never a control plane.

## Slice 1 - Terminal Snapshot and Watch Loop

### Contract

- `src/dashboard/jarvis_terminal.py` opens the board with SQLite `mode=ro` and
  `PRAGMA query_only=ON`; it does not import `board`, so it cannot run a
  migration or call a board mutator.
- The dashboard reads the final bounded set of JSONL ledger entries on each
  refresh. Malformed trailing lines are reported as source evidence rather
  than silently becoming a valid event.
- Supervisor health comes only from the retained `daemons.json` state file and
  an injected PID-liveness probe. The dashboard launches no process, contacts
  no network, and never reads configuration or credentials.
- Rich renders a stable snapshot containing task status counts, task rows and
  dependency edges, recent ledger outcomes, activated-rule count, and daemon
  status. `--watch` repeats snapshot reads at a positive operator-selected
  interval; it has no write/retry behavior.

### Proving Artifacts

1. A fixture board containing done, ready, and running tasks plus a dependency
   renders those exact IDs, statuses, and edge direction.
2. Known ledger lines render in newest-first bounded order; a malformed line
   yields an explicit diagnostic rather than fabricated evidence.
3. A supervisor state fixture with an alive and a dead child renders the
   respective health facts without starting or killing either process.
4. An AST boundary test rejects board imports, write SQL, process spawning,
   network imports, and credential/config access in the dashboard module.
5. A CLI snapshot exits zero and writes the expected Rich text to an injected
   terminal. A missing board fails non-zero with an honest diagnostic.

### Out of Scope

No web server, callbacks, task mutation, rule activation, planner integration,
or daemon changes. The existing supervisor and its child daemons are neither
started, stopped, nor reconfigured by this slice.

### Evidence

- `python -m unittest tests.test_dashboard` -> 7 tests, exit 0, 3.026s.
- `python tests/run.py` -> 253 tests, exit 0, 150.571s.
- One live one-shot snapshot opened the existing board read-only and exposed a
  stopped supervisor with no recorded child processes. It performed no recovery
  action and left the retained state/logs unchanged.

## Later Slices

- Slice 2: local web visualization over the same read-only snapshot contract.
- Slice 3: accepted semantic/procedural evidence panels, still observational.
