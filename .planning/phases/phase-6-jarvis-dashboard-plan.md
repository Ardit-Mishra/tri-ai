# Phase 6 Plan - Read-Only JARVIS Dashboard

**Status:** COMPLETE - Slices 1-4 accepted (2026-09-12). Full suite at closeout:
`python tests/run.py` -> 324 tests, exit 0.

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
- Slice 4: spatial HUD - lifecycle telemetry, workspace grouping, and mobile ergonomics
  over the same snapshot, plus the Part 2/3 audit of the surface itself.

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

## Slice 3 - Accepted Memory Evidence Panels

Accepted 2026-09-11 on local exit-code evidence under the operator's explicit
continuation instruction. The separate review service was unavailable and is
recorded as such rather than reported as a passed review.

- The Memory Bank panel renders accepted procedural rules with their task kind,
  checks, and provenance citations, read through the same `mode=ro` +
  `query_only=ON` snapshot. It derives nothing and activates nothing; rule
  derivation and activation remain Phase 5D responsibilities.
- Rules are linked to tasks only by resolved workspace path, so a rule governs a
  task because both resolve to the same workspace, never because a display
  heuristic guessed.
- Slice 2's implementation/review correction (`a71ff9e` / `02b90f8`) is covered by
  the adversarial regression tests in `8ea0d03`.

## Slice 4 - Spatial HUD, Surface Audit, and Network Reach

### Contract

- The terminal reader projects `TaskTelemetry`, `PhaseView` and `RunLogView` by
  reading `task_runs`, `triai_worktrees`, `task_events` and eight further `tasks`
  columns. Every statement is `SELECT`, the connection stays `mode=ro` plus
  `PRAGMA query_only=ON`, and the AST boundary test is unchanged.
- Lifecycle phases (`CLAIMED -> WORKTREE_PREP -> AGENT_ACTIVE -> VERIFY_GATE`) are
  derived only from recorded evidence. A stage a `dir` workspace never reaches
  renders `skipped` with its reason, never `pending`.
- Active-run `agent.log` and `verify.log` tails ride inside the snapshot (40 lines
  / 64KB / 240 chars per line, running runs only). No caller-supplied text ever
  becomes a path under `~/.tri-ai/runs`.
- Nodes lead with intent: task title primary, hex id demoted to a muted subtitle,
  workspace rendered as a path-derived tag and tint. Hover or tap raises a
  micro-card carrying prompt, workspace, branch, phase and runtime.
- The SSE client reconnects on exponential backoff (1s doubling to 30s) behind a
  `STREAM LIVE` / `RECONNECTING` status badge; timestamps render relative with the
  absolute value retained as the element title.
- `create_server` binds loopback unless `allow_non_loopback` is passed explicitly.
  `--host` repeats, so loopback and one named interface can both be bound without
  a wildcard bind. There is no mutating endpoint at any binding.

### Proving Artifacts

1. `tests/test_dashboard_template_syntax.py` parses the embedded script with
   `node --check`. Every other dashboard test asserts on substrings and therefore
   passes on a script that does not parse - a gap that was not hypothetical: an
   edit during this slice produced two `else` clauses in one `if` and served an
   empty HUD until this gate caught it. Node is not a project dependency, so its
   absence skips explicitly rather than passing quietly.
2. `tests/test_dashboard_hud.py` proves the removed duplicate `render()`, the
   HiDPI backing-store scale, SSE backoff and its atomic status badge, relative
   timers with retained absolute values, the semantic name plates and their
   outward anchoring, the micro-card fields, touch-target sizing, and the network
   binding contract including the refusal of an un-opted-in wider bind.
3. The dashboard's structural boundary tests are unchanged and still reject board
   mutation, credential access, external-network clients, and process spawning.

### Evidence

- `python -m unittest tests.test_dashboard tests.test_dashboard_web
  tests.test_dashboard_hud tests.test_dashboard_template_syntax` -> 51 tests, exit 0.
- `python tests/run.py` -> **324 tests, exit 0, 159.988s** (2026-09-12).
- Live, against the real board: the HUD rendered three semantic node plates with
  workspace tags and hulls, the `[TRI-AI CORE]` anchor, a `STREAM LIVE` badge, and
  three service pills that agreed with direct PID probes of supervisor 37492,
  worker 25108 and telegram 3124.
- Live at 375px: metrics reflow to two columns, plates and hull labels stay legible
  and non-overlapping, and the inspector becomes a bottom sheet.
- Binding proven by probe: `127.0.0.1:8080` reachable, `100.118.189.88:8080`
  (Tailscale) reachable, `192.168.0.36:8080` (home Wi-Fi) refused.

### Defects Found By Looking At The Running Page

Two were invisible to the test suite because they are geometry, not logic: name
plates drawn rightward put a left-hand node's plate underneath its neighbour, and
two workspace hull labels landed on the same line at phone width. Both are now
covered by tests, but both were found by opening the page. That is the standing
lesson of this slice - a green suite is not a rendered surface.

## Phase 6 Closeout

All four slices are accepted. The dashboard reads board, ledger, retained daemon
state and retained run logs, and has no path to mutate any of them. Phase 6 is
complete.
