# Phase 6 Plan - Read-Only JARVIS Dashboard

**Status:** Slices 1 and 2 accepted locally (2026-09-11)

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

## Slice 2 - Local JARVIS Web Surface

- `src/dashboard/jarvis_web.py` binds only `127.0.0.1` and serves one
  self-contained dark UI. It imports the terminal snapshot reader rather than
  opening a separate database path.
- `/api/snapshot` and `/events` serialize the same immutable snapshot. SSE
  sends a fresh snapshot every two seconds; there is no mutation endpoint.
- Tests prove HTML delivery, JSON/SSE serialization, loopback-only binding,
  and the absence of board mutation, credential, external-network, or process
  spawning capability.

### Evidence

- `python -m unittest tests.test_dashboard tests.test_dashboard_web
  tests.test_telegram_control tests.test_telegram_daemon tests.test_worker_daemon`
  -> 47 tests, exit 0, 16.484s.
- `python tests/run.py` -> 259 tests, exit 0, 180.472s.
- The web server suppresses only ordinary Windows client disconnect exceptions;
  all other request-handler exceptions continue through the standard server
  error path.
- Windows daemon liveness uses a query-only process handle, not `os.kill(pid,
  0)`, which gave false-down readings for live processes in local validation.
- At medium/mobile widths the header and metrics reflow without clipping; the
  four specified task lanes remain the only lane categories, with terminal
  non-success states retained in Failed.
- Live acceptance: `http://127.0.0.1:8080` served a JSON snapshot and SSE
  event while its displayed supervisor, worker, and Telegram indicators agreed
  with the retained supervisor state and direct PID checks.
- The concurrent daemon incident was recovered separately through
  `board.abort_dead_worker_claim`: it refuses a live worker and closes a proven
  dead claim with a durable board event. It is not a retry mechanism.
