# Requirements: Tri-AI Swarm

**Defined:** 2026-09-02
**Core Value:** A task assigned once gets decomposed, executed in parallel by free local models, and verified by exit codes — without the expensive model staying in the loop.

## What research changed

Three findings reshaped this scope before a line was written. They are recorded here because the
requirements below only make sense against them.

1. **Most of the board already exists.** `hermes_cli/kanban_db.py` (12,139 lines, installed and
   production-tested) implements atomic claim, lease expiry with heartbeat renewal, PID-verified
   dead-worker reclaim with a defer-if-alive guard, dependency-gated DAG promotion, and a
   blackboard via `task_comments`. Verified by direct source reading, not by report.
   **The single genuine gap:** `verify_command` appears **zero times** in that file. The board
   trusts a judgment; this project trusts an exit code.

2. **Concurrent generation buys ~16%, not Nx.** Measured on the actual RTX 3060: wall-clock scales
   linearly with worker count and VRAM stays flat at 5,278 MiB whether one request is in flight or
   four — meaning requests are serialized behind one slot. Any requirement that assumed N
   concurrent model calls run N times faster has been removed.
   **Parallelism over CPU/IO-bound work is unaffected** and is where v1 lives.

3. **Verify-gating is node-local.** It has no answer for graph edges: a fabricated upstream
   artifact is consumed downstream as valid input. And a verify command can pass vacuously —
   deleted assertions and empty test collection both exit 0.

## v1 Requirements

### Task graph

- [ ] **GRAPH-01**: A strong model decomposes an assigned task into a persisted graph of subtasks and then exits; execution continues without it
- [ ] **GRAPH-02**: The graph lives in the kanban SQLite board, not in a bespoke JSON file — atomic claim under concurrency is the reason
- [ ] **GRAPH-03**: A planner that emits a subtask with no verify command is rejected at write time, not discovered at run time
- [ ] **GRAPH-04**: Each subtask records its target repo, prompt, verify command, dependencies, and expected artifacts

### Board

- [ ] **BOARD-01**: Reuse the existing kanban claim/lease/heartbeat/reclaim kernel rather than reimplementing it
- [ ] **BOARD-02**: Add `verify_command` to the task schema using the codebase's own `add_column_if_missing` migration helper — additive, not a fork
- [ ] **BOARD-03**: Two workers can never hold the same subtask; proven by a deliberate race test, not by inspection
- [ ] **BOARD-04**: A subtask whose worker dies is reclaimed and becomes runnable again, without double-spawning a worker that is merely slow

### Verification

- [ ] **VERIFY-01**: A subtask is accepted only when its verify command exits 0; the worker's own report is never evidence
- [ ] **VERIFY-02**: A failed subtask reverts its repository working tree before any other subtask runs there
- [ ] **VERIFY-03**: A failed subtask does not block branches that do not depend on it
- [ ] **VERIFY-04**: A subtask checks its upstream artifacts exist and are non-empty before consuming them — closes the node-local gap that lets a fabricated result propagate
- [ ] **VERIFY-05**: Every verify command is proven to FAIL against a deliberately broken input before it is trusted; a command that cannot fail is not a gate

### Execution

- [ ] **EXEC-01**: Multiple workers execute ready subtasks concurrently, bounded by a cap derived from measurement rather than chosen
- [ ] **EXEC-02**: v1 targets read-only, CPU/IO-bound repository chores — test suites, typechecks, builds, dependency audits — where the parallelism is real
- [ ] **EXEC-03**: Every run appends to the existing ledger, recording worker identity, model, verify exit code and duration
- [ ] **EXEC-04**: Nothing autonomous pushes, merges, deploys, or reads credentials

### Triggers

- [ ] **TRIG-01**: A task can be assigned from the laptop CLI
- [ ] **TRIG-02**: A task can be assigned by appending to a queue file
- [ ] **TRIG-03**: A task can run on a schedule, durable across reboots and independent of any agent being alive
- [ ] **TRIG-04**: A task can be assigned from Telegram, behind an explicit confirmation

### Observability and control

- [ ] **OBS-01**: The board is readable from Telegram — what is queued, claimed, running, passed, failed
- [ ] **OBS-02**: A running subtask can be cancelled from Telegram without a confirmation gate, because cancelling only removes risk
- [ ] **OBS-03**: A failed subtask can be retried from Telegram
- [ ] **OBS-04**: Each subtask's full output is retrievable after the fact, so a failure can be diagnosed without re-running it

## v2 Requirements

Deferred deliberately. Each has a stated reason.

### Concurrency

- **CONC-01**: Re-measure with `OLLAMA_NUM_PARALLEL > 1` — requires an Ollama restart that interrupts the Telegram gateway, so it needs consent
- **CONC-02**: GPU-bound fix-and-reverify chores running concurrently — a different bottleneck than v1 validates, and bundling them would let the milestone quietly fail its own goal

### Scale

- **SCALE-01**: Cross-machine board shared by laptop and desktop workers — SQLite over a network mount is fragile; needs its own design pass
- **SCALE-02**: Raising served context on `qwen3:14b` / `qwen2.5-coder:14b` above the 64,000 floor to unlock stronger coders — costs KV-cache VRAM, unmeasured

### Worker quality

- **WORK-01**: Benchmark malformed tool-call rate for the worker model across multi-step chains — its validated honesty is a different property from format reliability, and the ledger has never tested it

## Out of Scope

| Feature | Reason |
|---|---|
| Direct agent-to-agent messaging | Small models negotiating with small models is where these systems produce expensive nonsense. Coordination goes through the board. Independently confirmed by research. |
| Adopting an agent framework (CrewAI, AutoGen, AG2) | All are built around agents delegating to each other directly — the pattern already ruled out. Disqualified on fit, not weight. |
| LangGraph / Ray / Prefect / Dagster | Work at $0, but each duplicates infrastructure already built and trusted here. |
| Judgment work on the free lane | Anything a command cannot judge waits for a human. Widening this would make the system's central claim untrue. |
| Metered per-token APIs | Banned. Breaks the $0 rule. |
| Creating accounts | For any service, ever. |
| Autonomous push / merge / deploy / credentials | Work stays on feature branches and waits for review. |
| Shell execution from Telegram | The least-scrutinised surface must not reach the most dangerous capability. |
| Agent personalities or role-play | Burns scarce context for no reasoning benefit. |
| A dashboard duplicating the ledger | New infrastructure that must stay up, for no new information. |
| Generality for imagined future users | One operator. Generality enlarges the surface that has to be trusted. |

## Traceability

Populated during roadmap creation.

| Requirement | Phase | Status |
|---|---|---|
| GRAPH-01 | Phase 3 | Complete |
| GRAPH-02 | Phase 1 | Complete |
| GRAPH-03 | Phase 3 | Complete |
| GRAPH-04 | Phase 1 | Complete |
| BOARD-01 | Phase 1 | Complete |
| BOARD-02 | Phase 1 | Complete |
| BOARD-03 | Phase 1 | Complete |
| BOARD-04 | Phase 1 | Complete |
| VERIFY-01 | Phase 2 | Complete |
| VERIFY-02 | Phase 2 | Complete |
| VERIFY-03 | Phase 3 | Complete |
| VERIFY-04 | Phase 2 | Complete |
| VERIFY-05 | Phase 2 | Complete |
| EXEC-01 | Phase 3 | Complete |
| EXEC-02 | Phase 2 | Complete |
| EXEC-03 | Phase 2 | Complete |
| EXEC-04 | Phase 2 | Complete |
| TRIG-01 | Phase 2 | Complete |
| TRIG-02 | Phase 2 | Complete |
| TRIG-03 | Phase 2 | Complete |
| TRIG-04 | Phase 5 | Pending |
| OBS-01 | Phase 4 | Pending |
| OBS-02 | Phase 5 | Pending |
| OBS-03 | Phase 5 | Pending |
| OBS-04 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 25 total (corrected from the summary count of 21 recorded when this file was first drafted — the itemized list below the header always contained 25 entries; verified by direct count during roadmap creation)
- Mapped to phases: 25/25 ✓ (100% coverage, no orphans, no duplicates)

---
*Requirements defined: 2026-09-02; traceability audited 2026-09-10.*
