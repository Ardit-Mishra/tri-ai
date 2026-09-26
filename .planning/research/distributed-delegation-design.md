# Distributed Hardware Delegation & Offloading Protocol — Design Specification

**Status:** design — not started
**Author:** Claude (this session)
**Date:** 2026-09-10
**Constraint:** $0 marginal cost, Tailscale mesh already provisioned, board lives on desktop
**Depends on:** Phase 3 (board substrate, dispatcher, worker pipeline), Phase 4 (read-only Telegram)
**Drives:** Phases 7+ (multi-device execution)

---

## 1. Why This Exists

Tri-AI currently runs on one machine — the desktop. The laptop writes task graphs and monitors,
but all execution happens on the desktop's RTX 3060. This is fine for the current chore workload,
but it leaves the laptop's 11 Ollama models idle and creates a single point of failure: if the
desktop is off or under load, nothing runs.

The Distributed Delegation Protocol extends the execution loop across the Tailscale mesh so that:

- **Lightweight tasks** (markdown processing, parsing, simple verification) run on the laptop,
  freeing the desktop for heavy inference.
- **Heavy tasks** (large code generation, multi-file refactoring, VRAM-hungry models) route
  directly to the desktop.
- **Failure-triggered offload** catches a laptop worker that exceeds its resource ceiling and
  forwards the task to the desktop automatically.
- **The phone remains a controller only** — it monitors via Telegram (Phase 4) and can trigger
  task creation (Phase 5), but never executes.

The board stays on the desktop. Everything else moves.

---

## 2. Architectural Topology

```
                    ┌──────────────────────────────────┐
                    │         DESKTOP (tri-desktop)     │
                    │  RTX 3060 · 12 GB VRAM · 17 models│
                    │                                   │
                    │  ┌─────────────┐  ┌────────────┐ │
                    │  │ Hermes Board │  │  Workers   │ │
                    │  │ (kanban DB)  │  │  (4 max)   │ │
                    │  └──────┬──────┘  └─────┬──────┘ │
                    │         │               │         │
                    │  ┌──────┴──────┐  ┌─────┴──────┐ │
                    │  │  tri-agent  │  │  Ledger    │ │
                    │  │  daemon     │  │  (JSONL)   │ │
                    │  └──────┬──────┘  └────────────┘ │
                    └─────────┼────────────────────────┘
                              │ Tailscale mesh
                    ┌─────────┼────────────────────────┐
                    │         │                        │
          ┌─────────┴──────┐  │            ┌───────────┴─────┐
          │   LAPTOP        │  │            │   PHONE          │
          │ 11 Ollama models│  │            │ Telegram only    │
          │                 │  │            │ (controller)      │
          │ ┌─────────────┐ │  │            └──────────────────┘
          │ │  tri-agent  │◄┼──┘
          │ │  daemon     │ │
          │ └──────┬──────┘ │
          │        │        │
          │ ┌──────┴──────┐ │
          │ │  Workers    │ │
          │ │  (2 max)    │ │
          │ └─────────────┘ │
          └─────────────────┘
```

### Node Roles

| Node | Hardware | Role | Max Workers | Models |
|------|----------|------|-------------|--------|
| `tri-desktop` | RTX 3060, 12 GB VRAM | Board host + heavy execution | 4 | 17 Ollama |
| `tri-laptop` | Variable, battery-limited | Lightweight execution | 2 | 11 Ollama |
| `tri-phone` | Mobile | Controller only (Telegram) | 0 | None |

### The Central Constraint

**The board is one file on the desktop.** `C:\Users\ardit\AppData\Local\hermes\hermes-agent` is the
Hermes kanban database. It is not replicated. It is not designed for concurrent multi-process
writes from different machines. Every task claim, completion, and failure must go through the
desktop's board.

This means:
- Remote workers (laptop) **cannot** call `kb.claim_task()` directly — the board file is not
  accessible over Tailscale in a safe way (file locking over network mounts is unreliable on
  Windows).
- Instead, the desktop runs a **tri-agent daemon** that owns the board and exposes a thin HTTP
  API for remote workers.
- The laptop's workers call the daemon, never the board directly.

---

## 3. The tri-agent Daemon (`tri-agent-d`)

A lightweight Python HTTP server that runs on each device. On the desktop, it owns the board. On
the laptop, it proxies to the desktop.

### Desktop Daemon — the board owner

Runs on `tri-desktop:9800` (Tailscale IP). Endpoints:

```
GET  /health                    → {"status": "ok", "node": "tri-desktop", "workers": 2}
POST /task/claim                → claims a task for a remote worker
POST /task/complete             → records completion + ledger entry
POST /task/fail                 → records failure + ledger entry
POST /task/heartbeat            → keeps a claim alive (replaces worker_pid check)
GET  /graph/{graph_id}          → returns the full graph state
GET  /runs?task_id=...          → returns run history for a task
GET  /runs?recent=...           → returns recent runs (for the dashboard)
POST /workspace/sync            → accepts a git diff bundle for workspace sync
GET  /workspace/fetch           → returns the current workspace state (git archive)
```

**Claim flow (remote worker → daemon):**

```
1. Worker POST /task/claim {"task_id": "...", "worker_host": "tri-laptop", "worker_pid": 1234}
2. Daemon calls kb.claim_task(task_id) — the real board claim
3. Daemon records worker_pid via kb._set_worker_pid (the existing Phase 2 fix)
4. Daemon returns {"status": "claimed", "task": {...}, "workspace_url": "http://tri-desktop:9800/workspace/fetch?branch=..."}
5. Worker fetches the workspace via the URL (git archive over HTTP)
6. Worker executes locally
7. Worker POST /task/complete or /task/fail with the result
8. Daemon calls board.accept_task() or board.release_stale_claims() and writes the ledger entry
```

**Security:** the daemon binds to the Tailscale interface only (100.x.x.x), never to
0.0.0.0. No authentication beyond Tailscale's wireguard tunnel. The daemon refuses any request
from a non-Tailscale IP.

### Laptop Daemon — the proxy

Runs on `tri-laptop:9800`. Proxies all board operations to the desktop daemon. Adds:

- **Local worker management:** claims tasks from the desktop, distributes to local workers
- **Offload detection:** monitors local worker health, triggers offload if thresholds are breached
- **Workspace caching:** caches git archives locally to avoid re-fetching unchanged workspaces

---

## 4. Failure Detection & Offloading Triggers

### 4.1 Pre-Flight Hardware Tag (planner-side)

The planner can tag graph nodes with hardware requirements:

```json
{
  "id": "heavy-inference-node",
  "prompt": "Refactor the authentication module...",
  "hardware_tier": "heavy",
  "reason": "requires >8GB VRAM for context window"
}
```

The dispatcher reads `hardware_tier` and routes:
- `"heavy"` → desktop only
- `"light"` → laptop preferred, desktop fallback
- `"any"` → wherever capacity is available (default)

This is a **planner hint**, not a hard constraint. The planner's `validate_graph` function checks
that `hardware_tier` is one of the allowed values.

### 4.2 Runtime Offload Triggers (worker-side)

When a laptop worker is executing a task, it monitors:

| Trigger | Threshold | Action |
|---------|-----------|--------|
| VRAM saturation | >90% of available VRAM for >5s | Offload to desktop |
| Generation timeout | >120s per token batch | Offload to desktop |
| OOM exception | Any `torch.cuda.OutOfMemoryError` | Offload to desktop |
| Process crash | Exit code 137 (SIGKILL) or 139 (SIGSEGV) | Offload to desktop |
| Disk space | <1 GB free in workspace | Offload to desktop |

**Offload sequence:**

```
1. Worker detects trigger
2. Worker calls POST /task/fail {
     "task_id": "...",
     "error_class": "environment",
     "offload_requested": true,
     "offload_reason": "oom",
     "partial_results": "..."  # any work done so far
   }
3. Laptop daemon forwards to desktop daemon
4. Desktop daemon:
   a. Records the failed attempt in the ledger (error_class: "environment")
   b. Does NOT count against the node's circuit breaker (per Phase 4 Slice 3)
   c. Immediately re-claims the task for a desktop worker
   d. Fetches the workspace from the laptop's partial state (or re-fetches from git)
   e. Dispatches to a local desktop worker
5. Desktop daemon returns {"status": "offloaded", "new_worker": "tri-desktop:5678"}
6. Laptop daemon logs the offload event
```

### 4.3 The Offload Ledger Entry

Every offload gets a dedicated ledger entry:

```json
{
  "run_id": "offload-abc",
  "task_id": "heavy-inference-node",
  "worker_host": "tri-laptop",
  "worker_pid": 1234,
  "verify_outcome": "offloaded",
  "offload_reason": "oom",
  "offload_to": "tri-desktop",
  "error_class": "environment",
  "wall_seconds": 45.2
}
```

This is distinct from a failure — it's an environment-class event that doesn't trip the breaker.

---

## 5. Workspace Synchronization

The hardest part of distributed execution is ensuring both machines work on the same code state.

### 5.1 Git-Based Sync (primary)

The desktop is the git remote. The laptop fetches via HTTP:

```
Desktop daemon: GET /workspace/fetch?branch=phase-2/worker-assign
  → runs: git archive --format=tar branch | gzip
  → returns the tarball over HTTP

Laptop worker:
  1. Receives tarball
  2. Extracts to a local temp directory
  3. Executes the task
  4. If the task modifies files, generates a diff
  5. POSTs the diff back to the desktop via /workspace/sync

Desktop daemon:
  1. Receives the diff
  2. Applies it to the workspace: git apply <diff>
  3. Commits if the verify command passes
  4. Records the commit hash in the ledger
```

### 5.2 When Sync Fails

If the diff doesn't apply cleanly (merge conflict, stale workspace):
- The desktop daemon returns `{"status": "sync_failed", "reason": "conflict"}`
- The task is re-dispatched to a desktop worker with a fresh workspace
- The laptop's partial work is logged but not committed

### 5.3 Workspace Caching

The laptop daemon caches the last N git archives (default: 5). Before fetching, it checks if the
branch has changed:

```
GET /workspace/check?branch=phase-2/worker-assign
  → {"cached": true, "commit": "abc123", "age_minutes": 12}
  → if cached and fresh: skip fetch, use local cache
  → if stale or missing: fetch new archive
```

---

## 6. Heartbeat & Stale Claim Recovery

Remote workers have the same TTL problem as local workers — the desktop's board has a 15-minute
claim TTL. The daemon handles this:

### 6.1 Heartbeat

Every 5 minutes, the laptop daemon sends:

```
POST /task/heartbeat {
  "task_id": "...",
  "worker_host": "tri-laptop",
  "worker_pid": 1234
}
```

The desktop daemon calls `kb._set_worker_pid(task_id, pid)` to refresh the claim. This is the
same mechanism as the Phase 2 fix (`src/worker.py` registering worker_pid after claim), but
driven by the daemon instead of the worker process.

### 6.2 Stale Detection

If a heartbeat fails (laptop disconnected, process crashed):
- The desktop daemon detects the missing heartbeat after 2× the heartbeat interval (10 minutes)
- It calls `board.release_stale_claims()` — the existing Phase 1 mechanism
- The task returns to `ready` and can be re-dispatched to a desktop worker

---

## 7. Integration with Existing Code

### 7.1 Board (`src/board.py`)

No changes to the board itself. The daemon wraps the board's existing API:
- `board.create_task()` — called by the daemon when a planner writes a graph
- `board.ready_tasks()` — called by the daemon when a worker requests a claim
- `kb.claim_task()` — called by the daemon on behalf of remote workers
- `board.accept_task()` / `board.release_stale_claims()` — called by the daemon on completion/failure

The `hardware_tier` field is stored as part of the task's JSON payload, not as a board column.
The daemon reads it when routing.

### 7.2 Dispatcher (`src/dispatcher.py`)

The dispatcher gains a `target` parameter:

```python
def dispatch_one(task, launcher, target="any"):
    """
    target: "any" | "desktop" | "laptop"
    - "any": dispatch to whichever daemon has capacity
    - "desktop": force to desktop (heavy tasks)
    - "laptop": force to laptop (light tasks, desktop overloaded)
    """
```

The dispatcher reads `task.get("hardware_tier", "any")` and maps it to a target. If the target
daemon is unreachable, it falls back to the other.

### 7.3 Worker (`src/worker.py`)

No changes to the worker itself. Remote workers are just workers that connect to a daemon
instead of reading the board directly. The daemon handles the board interaction; the worker
handles execution.

### 7.4 Ledger (`src/ledger.py`)

No changes. The daemon writes ledger entries in the same JSONL format. The `worker_host` field
distinguishes which device executed the task.

### 7.5 Memory System (`src/memory/`)

The episodic memory's `runs.db` gains a `source_node` column:
- `"tri-desktop"` — executed locally
- `"tri-laptop"` — executed remotely
- `"offloaded"` — started on laptop, finished on desktop

This lets the memory system track which hardware tier is best for which task kind.

---

## 8. Safety Boundaries

All Phase 3 boundaries carry forward. Add:

1. **The daemon is the only board access point for remote workers.** No remote worker calls
   `kb.claim_task()` directly. The daemon owns the board; workers own execution.

2. **Offloaded tasks are environment-class events.** An OOM or timeout that triggers an offload
   does NOT count against the node's circuit breaker. The task is retried on a different machine,
   not blocked.

3. **The daemon refuses non-Tailscale connections.** It binds to the Tailscale interface only.
   No authentication beyond the wireguard tunnel.

4. **Workspace sync is diff-based, not full-repo transfer.** Only changed files are synced. The
   desktop never sends the entire repo to the laptop — it sends a git archive of the working
   tree, which is compressed.

5. **The phone never executes.** It monitors (Telegram read-only) and can trigger task creation
   (Phase 5), but the daemon rejects any `POST /task/claim` from a phone node.

6. **The daemon is auditable.** Every claim, completion, failure, offload, and heartbeat is
   logged to a daemon-specific log file (`.tri-ai/daemon/{node}.log`). The JARVIS dashboard
   can display daemon status.

---

## 9. Implementation Phases

### Phase 7A — Daemon Foundation
- `src/daemon/__init__.py`, `src/daemon/server.py` (HTTP server)
- Desktop daemon: health endpoint, claim/complete/fail endpoints
- Tests: `test_daemon.py` (smoke test — server starts, health returns OK, claim returns a task)
- **Proof:** a test that starts the daemon, claims a task via HTTP, executes it locally, and
  completes it via HTTP. The board state matches.

### Phase 7B — Remote Worker Protocol
- Laptop daemon: proxy to desktop, local worker management
- Workspace sync: git archive over HTTP, diff-based return
- Tests: `test_daemon.py` (integration — two daemons, one claim across network)
- **Proof:** a test that starts two daemons (simulated), claims on the desktop from the laptop,
  fetches workspace, executes, and completes. The ledger has the correct `worker_host`.

### Phase 7C — Offload Detection & Trigger
- Offload triggers: VRAM monitoring, timeout detection, OOM catch
- Offload sequence: fail → re-claim on desktop → re-dispatch
- Tests: `test_daemon.py` (offload — trigger detected, task re-dispatched)
- **Proof:** a test that simulates an OOM on the laptop (stubbed), asserts the task is offloaded
  to the desktop, and the ledger has the offload entry with `error_class: "environment"`.

### Phase 7D — Planner Integration
- `hardware_tier` field in graph nodes
- `validate_graph` checks allowed values
- Dispatcher reads `hardware_tier` and routes
- Tests: `test_planner.py` (hardware_tier validation), `test_daemon.py` (routing)
- **Proof:** a test that creates a graph with `hardware_tier: "heavy"` nodes, dispatches, and
  asserts they land on the desktop daemon.

### Phase 8 — Phone Controller (Phase 5 companion)
- Telegram commands for task creation targeting specific hardware tiers
- Dashboard shows multi-node status
- Tests: integration tests with Telegram mock

---

## 10. VRAM Budget

| Component | VRAM | Notes |
|-----------|------|-------|
| Daemon (HTTP server) | 0 | CPU-only, `http.server` or `flask` |
| Git archive/sync | 0 | File I/O over HTTP |
| Offload monitoring | 0 | Reads `/proc` or `nvidia-smi` periodically |
| Workspace caching | 0 | Disk cache, not GPU |

**Total distributed delegation VRAM: 0 GB.** The daemon is pure CPU/network. The worker's VRAM
cost is unchanged — it's the model inference, not the delegation.

---

## 11. Data Flow: End-to-End Distributed Execution

```
1. Planner writes graph to desktop board
   (hardware_tier tags on nodes)

2. Dispatcher reads ready tasks
   (desktop daemon serves them)

3. For each task:
   a. Read hardware_tier
   b. If "heavy" → dispatch to desktop worker
   c. If "light" → dispatch to laptop daemon (if available)
   d. If "any" → dispatch to whichever has capacity

4. Laptop daemon receives dispatch:
   a. POST /task/claim to desktop daemon
   b. Desktop daemon claims on the board
   c. Desktop returns workspace archive URL
   d. Laptop fetches workspace (git archive over HTTP)
   e. Laptop worker executes locally

5. During execution, laptop monitors:
   a. VRAM usage (nvidia-smi or torch.cuda)
   b. Generation latency
   c. Process health

6. If trigger detected:
   a. POST /task/fail with offload_requested=true
   b. Desktop re-claims, re-dispatches locally
   c. Ledger records offload event (environment class)

7. If execution succeeds:
   a. Worker generates diff of changes
   b. POST /task/complete with diff
   c. Desktop applies diff, commits, records ledger
   d. Workspace synced back to git

8. Heartbeat every 5 minutes keeps claim alive
9. If heartbeat fails → stale claim recovery
```

---

## 12. Open Questions (design review items)

1. **Git archive vs. rsync:** `git archive` sends a snapshot but doesn't handle incremental
   changes well. For large repos, rsync or `git diff --stat`-based selective sync might be faster.
   The design uses git archive for simplicity; a future optimization can switch to incremental.

2. **Daemon process management:** How does the daemon start? Windows scheduled task (like the
   existing worker registration)? Or manual start? The design defers this to implementation —
   the daemon is a Python script that can be started by any method.

3. **Multiple laptops:** The design assumes one laptop. If multiple laptops join the mesh, the
   daemon needs to track which laptop has which task. The current design handles this via the
   `worker_host` field — each laptop is a distinct host.

4. **Phone execution:** The design explicitly excludes phone execution. If a future phone has
   sufficient hardware (e.g., a flagship Android with 8+ GB RAM), the architecture supports
   adding it as a lightweight node — but that's a future decision, not a current requirement.
