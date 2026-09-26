# Architecture Research

**Domain:** orchestrator/worker swarms — a strong model plans once and exits; small local models
execute the resulting task graph in parallel, coordinated through a shared task board only
**Researched:** 2026-09-02
**Confidence:** HIGH for board/claim/lease/reclaim mechanics (verified directly against a real,
running implementation of this exact pattern already installed on this machine —
`hermes_cli/kanban_db.py`, ~10K lines, in production use). MEDIUM for the general
orchestrator/worker literature (WebSearch verified against Anthropic, AWS, Kubernetes, Temporal,
Astronomer official docs). LOW/flagged individually where a claim rests on a single unverified
source.

**A note on sourcing.** This machine has Hermes Agent installed at
`~/AppData/Local/hermes/hermes-agent/`, and it already contains a fully-worked, tested
implementation of a blackboard task board with atomic claim, lease expiry, heartbeats, dead-worker
reclaim, dependency-gated DAG execution, and per-branch failure isolation
(`hermes_cli/kanban_db.py`, `hermes_cli/kanban_swarm.py`, `hermes_cli/kanban_decompose.py`).
`PROJECT.md` already flags this as "available and unused." Because it is real, running code for
the *exact* pattern this research question asks about, it is treated below as a primary source on
par with — and in places above — the general external literature. Line numbers cited refer to
`hermes_cli/kanban_db.py` as it exists on disk at research time; treat them as pointers, not a
frozen API.

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PLANNER  (strong model — Claude, runs once per assigned task, then exits)│
│    reads: one assigned task (chat / queue file / Telegram / cron)         │
│    writes: a persisted task graph — then EXITS. Nothing waits on it.      │
└───────────────────────────────┬────────────────────────────────────────--┘
                                 │ writes once
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TASK BOARD  (durable store — SQLite, single file, WAL mode)              │
│    tasks (id, status, deps via task_links, assignee, claim_lock,          │
│           claim_expires, worker_pid, verify_command*, result,             │
│           consecutive_failures, workspace_path)                           │
│    task_links   (parent_id, child_id)        — the DAG edges              │
│    task_runs    (per-attempt claim/lease/outcome history)                 │
│    task_events  (append-only audit log — claimed/completed/blocked/…)     │
│    task_comments(structured JSON "blackboard" posts — cross-worker notes) │
│  * verify_command is the one column this milestone must add — see below   │
└───────┬───────────────────────────┬───────────────────────────┬──────────┘
        │ atomic claim (CAS)        │ atomic claim (CAS)        │ read-only
        ▼                           ▼                           ▼
┌───────────────┐           ┌───────────────┐          ┌──────────────────┐
│ WORKER LOOP 1  │           │ WORKER LOOP 2  │          │  CONTROL SURFACE  │
│ hermes -z ──▶  │           │ hermes -z ──▶  │          │  (Telegram / Bob) │
│ VERIFY cmd ──▶ │           │ VERIFY cmd ──▶ │          │  read: status,    │
│ pass→result    │           │ pass→result    │          │        ledger     │
│ fail→revert    │           │ fail→revert    │          │  act: cancel,     │
│ heartbeat while│           │ heartbeat while│          │        retry,     │
│ running        │           │ running        │          │        pause      │
└───────────────┘           └───────────────┘          └──────────────────┘
        │                           │
        ▼                           ▼
   ledger.jsonl  ◀───────────── append-only, shared by every worker
   (existing — extend, don't replace)
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|-------------------------|
| **Planner** | Decompose ONE assigned task into a DAG of subtasks, each with its own verify command; persist it; exit. Never blocks on execution. | The strong model (Claude Code / Claude in this system) invoked once, writing rows via the board's API — not a daemon, not a session that stays open. |
| **Task board** | Single source of truth for what exists, what's claimed, what's done, what's failed, and the dependency graph between them. The *only* channel workers use to see each other. | SQLite file, WAL mode, `BEGIN IMMEDIATE` write transactions, CAS `UPDATE ... WHERE status=? AND claim_lock IS NULL` for claims. One file per board/repo-set. |
| **Worker loop** | Poll for claimable (`ready`, no unmet deps) tasks, claim one atomically, run it, verify it by exit code, revert on failure, record the outcome, release or advance the claim. | A thin process per concurrent worker slot — this project already has the verify-gate half of this in `src/run_queue.py`; the missing half is claim-from-board instead of iterate-a-static-list. |
| **Reclaimer** | Notice a claim whose lease expired or whose owning process died, and return that task to `ready` (or terminal-fail it) so the graph doesn't stall on a dead worker. | A periodic sweep (`detect_crashed_workers` / `release_stale_claims` equivalent) — can run inside every worker loop's poll cycle; doesn't need its own process. |
| **Dependency gate** | Promote a task from waiting to claimable only when every parent is terminal; keep a task's descendants un-promoted (not blocked-forever, just never-ready) when the task itself fails permanently. | A function that re-derives `ready` from `task_links` + parent status on every state change (`recompute_ready`), not a value anyone sets by hand. |
| **Blackboard / artifact store** | Where a worker leaves something another worker (or the human) needs to read: structured facts, not prose. | Structured JSON entries scoped to a shared root/goal task (`task_comments`), plus a `result` field on the task row itself, plus git worktree paths for file artifacts. |
| **Control surface** | Thin remote client (phone chat bot) that can *read* board state and issue a narrow, safe set of *write* actions — never raw graph edits. | A handful of board-API calls behind Telegram commands: `status`, `list`, `cancel <id>`, `retry <id>`, `pause`/`resume`. No arbitrary SQL, no new-graph creation, no push/merge. |
| **Ledger** | Cross-cutting, append-only record of every attempt across every worker — the thing that makes the "$0, verified, autonomous" claim citable. | Already exists (`ledger.jsonl`); every worker appends to the same file/table regardless of which task or branch it ran. |

## Recommended Project Structure

```
src/
├── run_queue.py          # EXISTING — becomes the single-worker reference
│                          #   implementation; its verify→pass/revert loop is
│                          #   correct and should not be rewritten, only
│                          #   retargeted to claim from the board instead of
│                          #   iterating queue.jsonl in order.
├── board.py               # NEW — task board API. Either (a) a thin wrapper
│                          #   around hermes_cli.kanban_db reused directly, or
│                          #   (b) a from-scratch SQLite module that copies its
│                          #   CAS/lease/reclaim shape. See "Reuse vs rebuild".
│                          #   Exposes: create_task, link (deps), claim_task,
│                          #   heartbeat, complete_task, fail_task,
│                          #   reclaim_stale, list_tasks, post_note, get_notes.
├── planner.py             # NEW — invoked once per assigned task. Calls the
│                          #   strong model, gets back a task graph, writes it
│                          #   via board.py, exits. No loop, no daemon.
├── worker.py               # NEW — the claim→run→verify→record loop. Several
│                          #   instances run concurrently (subprocess or
│                          #   asyncio tasks per STACK.md). Wraps run_queue.py's
│                          #   verify-gate logic around board.claim_task().
├── control.py             # NEW — Telegram command handlers: status/list are
│                          #   pure reads; cancel/retry/pause are the only
│                          #   writes, each mapped to one board.py call.
├── queue.jsonl            # EXISTING — becomes optional: still valid as a
│                          #   manually-authored task source for the planner
│                          #   or as a bypass for single-task work.
└── ledger.jsonl           # EXISTING — unchanged in shape; every worker
                           #   instance appends to it, board.py does not
                           #   replace it, only feeds it.
```

### Structure Rationale

- **`board.py` is the only new file that owns concurrency-sensitive code.** Everything else
  (`planner.py`, `worker.py`, `control.py`) is a thin client of it. This keeps the "one bespoke file
  the whole trust model rests on" property the current architecture already has for
  `run_queue.py` — you get exactly one place where a race condition could hide, and one place to
  audit before trusting the swarm claim.
- **`worker.py` extends `run_queue.py`, it does not replace it.** The verify-then-revert logic in
  the existing runner is already correct and battle-tested (9/9 ledger). The only change a worker
  needs is *where its next task comes from* — `board.claim_task()` instead of the next line of
  `queue.jsonl` — and *what happens on claim failure* (skip to the next ready task) versus the
  current *what happens on verify failure* (revert, which is unchanged).
- **`planner.py` has no loop.** It is invoked, it writes, it exits — this is the literal contract
  from `PROJECT.md` ("plans ONCE... then exits") and it should be enforced structurally: this file
  should have no `while True`, no persistent connection, nothing that could accidentally turn into
  a second orchestrator competing with the workers.

## Reuse vs. rebuild: the task-board substrate

This is the single most important architectural call this research surfaces, and it wasn't
obvious until the actual Hermes install was inspected rather than assumed.

`STACK.md` (written by a parallel researcher without filesystem access to the Hermes install)
recommends "plain JSON per task-graph, one file per graph" for the persisted DAG, with a note that
`hermes kanban swarm` should be "inspected before any of the above is built." Having now inspected
it directly: **plain JSON files cannot provide the atomic claim this system needs under concurrent
workers without re-implementing file locking badly**, and `hermes_cli/kanban_db.py` already
implements — correctly, with edge cases this project hasn't hit yet — every mechanic this
milestone's active requirements ask for:

| Requirement in `PROJECT.md` | Already implemented in `kanban_db.py` |
|---|---|
| Task graph persisted to disk, survives planner exit / dropped connection / compaction | SQLite file + WAL; `task_events` append-only log gives full replay history per task |
| Workers claim atomically, no double-claim | `claim_task()` — CAS `UPDATE ... WHERE status='ready' AND claim_lock IS NULL`, rowcount checked (line ~4678) |
| Dead worker's task reclaimed | `detect_crashed_workers` / `release_stale_claims` — PID liveness check + claim-expiry check + host-local SIGTERM→SIGKILL termination (line ~8253 `_terminate_reclaimed_worker`) |
| Coordination through a shared board only, no direct messaging | `task_comments` as a structured JSON blackboard, scoped to a shared root card (`kanban_swarm.py`'s `post_blackboard_update` / `latest_blackboard`) — this is *literally* the pattern this milestone asks for, already shipped |
| Failed subtask does not block unrelated branches | `recompute_ready()` only promotes a task whose *direct* parents are `done`/`archived`; a failed branch's descendants simply never promote — siblings with no shared parent are unaffected (line ~4510) |
| Board observable/controllable | `task_events`, `task_runs`, and task `status` are all plain SQLite rows — trivially queryable from any client, including a Telegram handler |

**What is genuinely missing** from `kanban_db.py`, and is this milestone's real, load-bearing
delta: **there is no `verify_command` column, and no verify-then-revert semantics.** Kanban's
notion of "did this succeed" is a human/verifier-card judgment (`kanban_swarm.py`'s verifier task,
gated by metadata `{"gate": "pass"}`) — exactly the "agent's report as evidence" pattern this
project's whole design explicitly rejects (`PROJECT.md`: "The agent's report is never evidence").

**Recommendation:** treat `kanban_db.py` as the board substrate (reuse the file directly, or vendor
a stripped-down copy of just the `tasks`/`task_links`/`task_runs`/`task_events` tables and the
`write_txn`/`claim_task`/`recompute_ready`/`detect_crashed_workers` functions), and add exactly one
thing to it: a `verify_command` (+ optional `verify_timeout`) column on `tasks`, added the same way
`worker_pid` was added historically — an additive migration via
`_add_column_if_missing(conn, "tasks", "verify_command", "verify_command TEXT")` (this exact helper
already exists in the codebase, line ~2583, and is the established pattern for extending this
schema without a breaking migration). Then `worker.py`'s claim loop runs the claimed task's prompt
through `hermes -z`, and — instead of trusting the result — runs `verify_command` and calls
`complete_task()` only on exit 0, `fail_task()`/revert otherwise. This is a small, precise, additive
change to a proven kernel instead of a rewrite of atomic-claim logic that has already had its race
conditions found and fixed in production (see the "invariant recovery on re-claim" comment at line
~4640, which exists because a *specific* race was observed and fixed — exactly the class of bug a
from-scratch implementation would rediscover the hard way).

If reusing the Hermes kanban kernel directly turns out to be impractical (e.g. version coupling to
a Hermes install that updates independently of this project), the fallback is to **copy its schema
and CAS shape**, not its dependency — the four tables and the `UPDATE ... WHERE status=? AND
claim_lock IS NULL` claim pattern are the reusable asset, not the Hermes package itself.

## Architectural Patterns

### Pattern 1: Plan-once, persist, execute-later (durable decomposition)

**What:** The strong model's output is not a conversation — it is rows in a database. Once
written and committed, the planner's process can die, the connection can drop, the context can
compact, and the graph is unaffected, because nothing about execution depends on the planner still
being reachable.

**When to use:** Any time the thing making the decomposition decision is expensive, quota-limited,
or unreliable-to-keep-alive, and the things executing it are cheap and disposable. This is exactly
this project's stated reason for the split (`PROJECT.md`: "keeps the expensive model on judgment
only and lets work continue after quota is gone").

**Trade-offs:** The planner cannot adapt mid-execution — if a subtask reveals the plan was wrong,
no one is listening. The mitigation used by real systems (Temporal's durable workflows, this
project's own kanban root-card pattern) is a **root/parent task that reawakens** once its children
complete, giving the orchestrating profile one more chance to judge and extend the graph — not a
live conversation, a new planning pass triggered by board state (`kanban_decompose.py`'s comment:
"the root task stays alive... so when the whole graph completes the root wakes back up").

**Example (schema, not code — what must be IN a task-graph record):**
```
task:
  id                 unique, stable — referenced by deps, claims, events, ledger
  title / body        the actual prompt content for the worker
  deps (parent_ids)   edges into the DAG — task_links table, not a field on the row
  verify_command      the exit-code oracle — THE decision-maker, never optional
  verify_timeout      bounds a hung verify from hanging the worker forever
  status              ready | running | blocked | done | archived | (this project's) failed
  claim_lock           who currently owns it (host:pid or similar), NULL when unclaimed
  claim_expires        lease expiry — the reclaim trigger
  worker_pid           for host-local liveness checks (kill(pid, 0))
  consecutive_failures circuit breaker input — stop retrying forever
  max_retries          per-task override of the circuit-breaker threshold
  result               the artifact / final output, once done
  workspace_path       where on disk the work happened (git worktree) — see below
```

### Pattern 2: Blackboard coordination, not agent-to-agent messaging

**What:** Workers never address each other. They post structured facts to a shared location
scoped to the goal (a root/parent task, in kanban's model) and read from the same location before
acting. This is the classic blackboard architecture (originating in AI systems like Hearsay-II in
the 1970s–80s, and re-emerging as the recommended pattern for LLM multi-agent systems specifically
*because* it avoids the N² conversational coupling of direct messaging — confirmed independently by
current multi-agent-architecture writeups, MEDIUM confidence, WebSearch-verified).

**When to use:** Exactly this project's constraint — "small models negotiating with small models is
where these systems reliably produce expensive nonsense" (`PROJECT.md`). Blackboard coordination
structurally cannot deadlock two workers waiting on each other's messages, because there are no
messages, only reads and writes of shared state.

**In practice, "workers are aware of each other" means a worker can read, on demand:**
1. **Sibling status** — is a task this one depends on `done`, still `running`, or `blocked`? (a
   `SELECT` against `tasks`/`task_links`, not a message from that sibling)
2. **Completed artifacts** — the `result` field and any blackboard notes a finished sibling left
   on the shared root (`task_comments`, filtered to structured entries)
3. **The global goal** — the root/parent task's title+body, so a worker three levels deep in the
   graph still knows *why*, not just *what*

A worker does **not** need, and should not be given, another worker's live reasoning, its
in-progress scratch state, or a way to interrupt it. Those would require messaging, which is
explicitly out of scope.

**Trade-offs:** Everything meaningful must be written down in a form another worker (or the
verify command) can parse — "put machine-readable facts in completion metadata... put cross-worker
notes on the root task using structured comments" is kanban_swarm.py's own design note, and it maps
directly onto this project's own hard-won corollary: "never ask a model to derive a fact from raw
output; have the command emit the fact" (`PROJECT.md`). The blackboard pattern and this project's
verification philosophy are the same idea applied at two different layers.

### Pattern 3: Lease-based atomic claim with heartbeat-extended, PID-verified reclaim

**What:** A task is claimed with a single atomic `UPDATE ... WHERE status='ready' AND
claim_lock IS NULL` (or the SQL-appropriate equivalent). Exactly one concurrent writer can succeed;
every loser sees `rowcount == 0` and moves on to the next candidate — no retry storm, no
distributed lock service needed, because the database's own write serialization (SQLite's WAL
writer lock; `SELECT ... FOR UPDATE SKIP LOCKED` in Postgres/MySQL) is the mutex. This is the same
mechanism as SQS's visibility timeout (a temporary lease, not a lock, not an acknowledgment — a
second consumer can claim the message the instant the timeout lapses) and Kubernetes's `Lease`
objects for leader election and node heartbeats (a `renewTime` the holder must keep touching, or a
challenger takes over). All three are the same pattern: **possession is time-bounded and must be
renewed, not held indefinitely.**

**When to use:** Any time more than one process can see the same pool of claimable work and only
one may act on each item — which is exactly "several concurrent workers, one shared board."

**Concrete claim → heartbeat → reclaim lifecycle (as implemented in `kanban_db.py`, and the
shape this project should copy):**
1. **Claim:** `claim_task()` sets `status='running'`, `claim_lock=<owner>`,
   `claim_expires=now+ttl`, inside one write transaction with the CAS `WHERE` clause above. Also
   re-verifies parents are still terminal (a defensive re-check — see Pattern 4) and records a new
   `task_runs` row so retry history survives across claims.
2. **Heartbeat:** while running, the worker periodically extends `claim_expires` (`heartbeat_worker`
   / `heartbeat_claim`) — this is *orthogonal to PID liveness*: a worker's process can be alive
   while the actual work is hung (a stuck subprocess, a stalled model call), so the lease must be
   proven renewed, not just "process still exists." Missing a heartbeat is what actually means dead,
   not the PID.
3. **Reclaim (dead worker):** a periodic sweep looks for `status='running' AND claim_expires <
   now`. Before releasing, it attempts to confirm the claim is truly abandoned: is the `claim_lock`
   host-local to this reclaiming process (only this project's own host can safely SIGTERM/SIGKILL a
   PID it owns — see `_terminate_reclaimed_worker`)? If the presumed-dead worker is actually still
   alive and survives termination, the reclaim **defers** (extends the lease briefly) instead of
   releasing the claim out from under a live worker — this is the exact double-spawn bug this
   mechanic exists to prevent (`_worker_survived_termination` / `_defer_reclaim_for_live_worker`).
   Only once the worker is confirmed gone does the task return to `ready` (or advance a failure
   counter and go to a retry/blocked state).

**Trade-offs:** Lease TTL is a tuning knob with real consequences both directions — too short and a
slow-but-healthy worker gets reclaimed out from under itself (Kubernetes's own guidance: lease
duration ~60s, renew deadline ~15s, retry period ~5s, for a comparable "don't flap, but don't hang
forever" balance); too long and a genuinely dead worker's task sits unavailable for the whole TTL
window. Pick the TTL from the *slowest expected single subtask*, not an arbitrary default — for
this project's local-model subtasks that means checking actual `hermes -z` wall-clock time on the
target hardware, the same measurement `PITFALLS.md` already flags as needed for concurrency
generally.

### Pattern 4: Dependency-gated DAG execution with per-branch failure isolation

**What:** A task becomes claimable only when every direct parent is terminal-success
(`done`/`archived`). This single rule, re-evaluated after every state change rather than pushed
imperatively, is what makes independent branches run in parallel and failed branches stay
contained *for free* — no special-case "if sibling failed, still proceed" logic is needed, because
siblings were never gated on each other in the first place. Only genuine descendants of a failed
task are affected, and even they are not permanently stuck: they simply never satisfy
`_parents_satisfied()`, sitting in `todo` rather than cascading into a `failed` state of their own.

**When to use:** Any DAG where "one branch fails" should not mean "the whole graph stalls" — which
is this project's explicit requirement ("A failed subtask reverts and does not block unrelated
branches of the graph").

**Comparison to other orchestrators' failure semantics** (WebSearch-verified against Astronomer's
Airflow docs): Airflow's default `trigger_rule=all_success` would propagate a failure downstream as
`upstream_failed` unless explicitly relaxed with `none_failed_min_one_success` or similar; Airflow
also supports a DAG-wide `fail_fast` flag that kills everything still running the moment anything
fails. **This project's target behavior is the opposite of `fail_fast` by default** — partial
success is the normal case, not an opt-in — which matches kanban's default posture (parent-gating
only, no global stop) far more closely than Airflow's default posture. If a genuine "stop
everything, something is badly wrong" kill switch is ever needed, it should be a distinct, rarely
used **control-surface action**, not the default failure behavior of the graph.

**Trade-offs:** Because failure containment is structural (an ungated fact about the DAG shape) and
not something the executor decides at failure time, the planner's decomposition quality directly
determines blast radius — two subtasks that shouldn't depend on each other but are given a
spurious parent link will falsely couple their failure. This makes the planner's dependency choices
the single highest-leverage correctness decision in the whole system, worth extra scrutiny before
the graph is committed.

### Pattern 5: Least-privilege remote control surface

**What:** A thin client (a chat bot on a phone) is given read access to everything and write
access to almost nothing. The GitLab ChatOps model (WebSearch-verified) treats a chat command as
"the same functionality as a job run from the platform" — i.e., it does not invent a separate,
looser permission model for chat; it routes through the same authorization the platform already
enforces. This project's own stop-condition list already enumerates the boundary
(`CLAUDE.md`/`PROJECT.md`: no push, merge, deploy, or credential access from automated work) — the
Telegram surface must respect the *same* boundary, not a chat-specific relaxation of it.

**What must be queryable (read, always safe):**
- Per-task status, assignee/owner, claim state, retry count, last heartbeat age
- The DAG shape (what depends on what) and where the graph currently is in it
- The verify command and its last result (pass/fail/exit code) — the actual evidence, not a
  paraphrase of it
- The ledger — cross-run history, the same data `run_queue.py` already writes

**What is safe to expose as a write action, and why each is bounded:**
| Action | Maps to | Why it's safe |
|---|---|---|
| `cancel <id>` | force-reclaim: release the claim, set status to a terminal/blocked state | Equivalent to a lease simply expiring — a mechanic the system already tolerates by design; does not touch git, credentials, or other tasks |
| `retry <id>` | reset `consecutive_failures` (or just re-promote via `recompute_ready`-style logic) | Re-enters the exact same claim→verify path every other attempt went through; cannot bypass verification |
| `pause` / `resume` | stop/start new claims being issued (a board-level flag the worker loop checks before claiming) | In-flight work finishes or reclaims normally; nothing is force-killed, nothing new starts |

**What must NOT be exposed remotely, regardless of convenience:** creating a new task graph from a
chat message (that's the planner's job, and it should stay a deliberate, reviewable act — not a
one-line Telegram command that silently spawns a swarm); editing a verify command from chat (that's
editing the oracle itself, the one thing this whole system refuses to let anything touch casually);
anything that reaches push/merge/deploy/credentials, per the project's own standing rule.

## Data Flow

### Planning → Execution Handoff

```
[Assigned task: CLI / queue file / Telegram / cron]
    ↓
[Planner: strong model, one invocation]
    ↓ writes DAG (tasks + task_links + verify_command per task)
    ↓ EXITS — no further involvement
[Task board: durable, on disk, WAL-mode SQLite]
    ↓ (parents terminal?) → recompute_ready → status=ready
[Worker loop N]                              [Worker loop M]
    ↓ claim_task (CAS)                            ↓ claim_task (CAS)
    ↓ hermes -z <prompt>                          ↓ hermes -z <prompt>
    ↓ verify_command → exit code                  ↓ verify_command → exit code
    ↓ pass: complete_task, write result           ↓ pass: complete_task, write result
    ↓ fail: revert, record failure, maybe retry   ↓ fail: revert, record failure, maybe retry
    ↓ append to ledger.jsonl ←──────────────────────┘ (shared, append-only)
    ↓
[recompute_ready fires again → next layer of the DAG becomes claimable]
    ↓ ... repeats until graph is exhausted or every remaining branch is blocked ...
[Root/parent task reawakens on full completion — optional next planning pass]
```

### Control-Surface Read/Write Flow

```
[Telegram message] → [control.py handler]
    read path:  board.list_tasks() / board.get_task() / ledger tail  → format → reply
    write path: board.cancel(id) | board.retry(id) | board.set_paused(bool)
                    ↓ each is ONE bounded board.py call — no raw SQL from control.py,
                      no path that reaches git/credentials/push/merge/deploy
```

### Key Data Flows

1. **Plan-then-abandon:** the planner's only interaction with execution is the one write. It never
   polls, never blocks, never needs to still be running for the graph to progress — verified against
   this project's own already-decided requirement, and structurally identical to how Temporal
   workflows resume from durable event history after a crash rather than from a live process
   (WebSearch-verified against Temporal's docs: event history persists independently of any worker
   process, and replay reconstructs state from it, not from memory).
2. **Claim races resolve to exactly one winner:** every worker loop runs the identical claim query;
   SQLite's write serialization (or Postgres/MySQL `SKIP LOCKED`, if this ever needs to scale past
   one machine) guarantees at most one `UPDATE` affects the row, so "two workers took the same task"
   is structurally impossible rather than merely unlikely.
3. **Failure is local, not global:** a failed subtask's effect is bounded to (a) itself — reverted,
   retried, or eventually blocked — and (b) its actual descendants in `task_links`, never its
   unrelated siblings.

## Scaling Considerations

This system's real ceiling is not "how many users" — it's how many concurrent local-model workers
one 12 GB GPU can usefully run, which is a hardware question `PITFALLS.md` already researches in
depth. The architecture-level scaling table below is about *board* and *coordination* limits, which
are not the bottleneck but should not be assumed infinite either.

| Scale | Architecture Adjustments |
|-------|---------------------------|
| 1-4 concurrent workers, single machine (this project's actual target) | SQLite WAL + CAS is more than sufficient — this is exactly the scale `kanban_db.py` is built and proven for. No changes needed. |
| Workers split across the laptop and the desktop (both machines this project already uses) | The board file must live somewhere both can reach with correct locking — SQLite over Tailscale-mounted storage is fragile (network filesystems and SQLite's file-locking assumptions do not mix well); prefer one machine hosting the board and the other's workers connecting over a small HTTP shim, OR run two independent boards (one per machine) with no cross-board dependencies, since this project's task graphs (repo chores) are naturally partitionable that way. |
| Beyond ~8-10 concurrent claimers on one board | SQLite's single-writer model starts to matter (writes serialize even though reads don't); Postgres/MySQL with `SELECT ... FOR UPDATE SKIP LOCKED` is the documented next step (Prisma/Vlad Mihalcea, WebSearch-verified) — almost certainly unnecessary here given the GPU ceiling is far lower than the SQLite ceiling. |

### Scaling Priorities

1. **First bottleneck: GPU/VRAM throughput, not the board.** `PITFALLS.md` has already researched
   this in depth (concurrent Ollama requests halving per-request tokens/sec well before 8-way
   parallelism is useful). The board architecture above will comfortably outrun this bottleneck —
   do not over-invest in board scalability before the concurrency sweep in `PITFALLS.md` is done.
2. **Second bottleneck: git worktree contention**, if multiple workers touch the same repository
   concurrently. The board's `workspace_kind`/`workspace_path`/`branch_name` fields exist
   specifically to give each claimed task its own isolated working directory — this mirrors the
   git-worktree-per-agent pattern now standard for parallel AI coding agents (WebSearch-verified:
   Anthropic's own Claude Code docs recommend worktrees for exactly this; Cursor's "Parallel Agents"
   feature is built on the same primitive). **Do not skip this** — running two workers against the
   same checkout is the concrete, observable failure mode (silent overwrites, `.git/index.lock`
   contention) that worktree isolation exists to prevent, and this project's five target repos make
   it a near-certain occurrence the first time two chores land on the same repo in one run.

## Anti-Patterns

### Anti-Pattern 1: The planner stays "on call"

**What people do:** Keep the strong model's session open, waiting to answer worker questions or
re-plan on the fly, because it feels safer than fully committing to the written graph.

**Why it's wrong:** This silently reintroduces exactly the dependency this milestone exists to
remove — "the expensive, quota-limited model spends its turns on judgment and then leaves" becomes
"the expensive model never actually leaves." It also reintroduces conversation-as-unit-of-work,
which does not survive a dropped connection, defeating the entire point of persisting the graph.

**Do this instead:** The planner writes, exits, and is genuinely gone. If the graph turns out to be
wrong, that surfaces as a `blocked`/failed task the human (or a fresh planner invocation) reviews
later — not as a live back-channel.

### Anti-Pattern 2: Trusting the worker's self-report as completion

**What people do:** Mark a task `done` because the agent said it finished, especially tempting for
small/cheap models where re-verifying feels like overhead.

**Why it's wrong:** This is the single most concretely-proven failure mode *in this project's own
history* — the exact free-lane model that handled a `grep`-verifiable delete perfectly deleted 387
of 389 lines of a working file on a no-oracle prose task and reported success (`PROJECT.md`). This
is not a hypothetical anti-pattern; it is an observed one, on this hardware, with these models.

**Do this instead:** Every subtask's completion is decided by its `verify_command`'s exit code, full
stop — the one rule this project's whole architecture already rests on, extended down to the
subtask level without exception.

### Anti-Pattern 3: Letting the control surface create or edit graphs

**What people do:** Add a chat command that's "just a small convenience" — spin up a new swarm from
a Telegram message, or tweak a verify command because the phone is what's in hand at 11pm.

**Why it's wrong:** It collapses the deliberate planning step into an impulsive one-liner from a
thin client with no review step, and — worse — it makes the verify command (the one thing this
system trusts unconditionally) editable from the least-scrutinized surface in the whole
architecture.

**Do this instead:** Keep the control surface to observe + narrow, board-native actions (cancel,
retry, pause) that all route through the same primitives every worker already uses. New graphs and
verify-command changes stay a deliberate act on the build machine.

### Anti-Pattern 4: Reclaiming a task whose worker is actually still alive

**What people do:** Treat "lease expired" as equivalent to "worker is dead" and immediately hand
the task to a second worker.

**Why it's wrong:** A slow-but-healthy worker (a big model on a loaded GPU, a subprocess that
legitimately takes longer than the TTL) gets its work duplicated — now two workers are doing the
same task, one of which will finish and "succeed" while the other's revert-on-fail or late-completion
races against it. This is a real, previously-hit bug class in the reference implementation, which is
*why* it has an explicit defer-and-recheck step rather than an unconditional release.

**Do this instead:** Before releasing an expired claim, confirm the worker is actually gone (PID
check; attempt termination; if it survives, extend the lease briefly and recheck next cycle rather
than double-spawning).

## Integration Points

### External Services / Substrate

| Service | Integration Pattern | Notes |
|---------|----------------------|-------|
| `hermes -z <prompt>` | Worker loop invokes it as a one-shot subprocess per claimed task, exactly as `run_queue.py` already does | No change to how the agent itself is invoked — only to how the *next task to invoke it on* is selected (claim vs. static list) |
| Hermes kanban kernel (`kanban_db.py`) | Reused directly (preferred) or its schema/CAS shape vendored | See "Reuse vs. rebuild" above — this is the load-bearing integration decision for the whole milestone |
| Ollama (both machines) | Unchanged — workers call it exactly as they do today, through Hermes | Concurrency ceiling is a hardware question, covered in `PITFALLS.md`, not an architecture question |
| Telegram / Hermes gateway ("Bob") | Control surface's transport layer | Already proven laptop-independent; the new piece is the handler logic behind it (`control.py`), not the transport |
| `ledger.jsonl` | Every worker instance appends; format unchanged | This is the artifact that keeps the autonomy claim citable — do not fragment it per-worker |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|----------------|-------|
| Planner ↔ Board | One-directional write, then nothing | Enforce structurally (no loop in `planner.py`) so this boundary can't accidentally grow into a second orchestrator |
| Worker ↔ Board | Claim (write), heartbeat (write), complete/fail (write), read (deps/status/blackboard) | The *only* channel between workers — no worker-to-worker calls, sockets, or shared memory of any kind |
| Worker ↔ `hermes -z` subprocess | Spawn, capture stdout/exit, then run `verify_command` independently | The worker never trusts the subprocess's own claim of success — mirrors `run_queue.py`'s existing discipline exactly |
| Control surface ↔ Board | Read: unrestricted. Write: three named actions only (cancel/retry/pause) | This is a permissions boundary, not just a code boundary — treat it as the one place in the system where "convenient" and "safe" are in tension, and safe wins |

## Suggested Build Order

Each step is listed with *why it has to come before the next one* — not just a preference order.

1. **Task-graph schema + board API (`board.py`), including the `verify_command` column.**
   Nothing else is testable without a place to durably write and read a graph. This is also where
   the reuse-vs-rebuild decision gets made concrete — resolve it here, first, because every
   subsequent component's shape depends on which persistence layer it's talking to.

2. **Claim / lease / heartbeat / reclaim, proven with a single fake worker.**
   Before any real work runs, prove the atomic-claim and dead-worker-reclaim mechanics in
   isolation — e.g. two processes racing to claim the same row, one process claiming and then being
   killed to verify reclaim fires. This has to come before real workers exist because a claim bug
   discovered *underneath* real concurrent `hermes -z` calls is nearly undebuggable; discovered here,
   it's a unit test.

3. **Single-worker verify-gated execution loop (`worker.py`), one worker, no concurrency.**
   Retarget `run_queue.py`'s proven verify→pass/revert logic to pull its next task from
   `board.claim_task()` instead of iterating `queue.jsonl`. Prove this end-to-end — including
   git-worktree isolation per task — with exactly one worker before adding a second. This has to
   precede parallelism because if the single-worker path has a bug, running four of them in
   parallel just makes the bug's blast radius four times worse and four times harder to isolate.

4. **Planner (`planner.py`) — writes a real graph via the board API, then exits.**
   This can be built and tested in parallel with steps 2-3 (it only needs step 1's schema to write
   into), but should not be wired to trigger real execution until step 3 is trustworthy — otherwise
   a planner bug and a worker bug become indistinguishable when something goes wrong. Test the
   planner by inspecting the graph it writes, independent of execution.

5. **Multiple concurrent workers.**
   Only once 2 and 3 are independently proven correct should a second (and third, fourth) worker
   loop instance run against the same board. This is the step that actually exercises the
   claim-race and reclaim mechanics under real contention, and it needs the GPU concurrency sweep
   from `PITFALLS.md` done first to know how many workers are even worth running.

6. **Failure isolation validation.**
   Deliberately fail one branch (bad verify command, killed worker mid-task) and confirm unrelated
   branches keep progressing and only true descendants stall. This needs step 5 running for real,
   since failure isolation is a property of the live graph under concurrent execution, not
   something unit-testable in isolation.

7. **Read-only Telegram observability (`control.py`, status/list only).**
   Needs steps 1-6 producing real state to display; ship this before any write actions so the
   control surface is useful (and its read path battle-tested) before it's given any power to
   mutate the graph.

8. **Telegram control actions — cancel, retry, pause.**
   Last, deliberately. Each of these mutates a live graph remotely from the least-scrutinized
   surface in the system; they should only be exposed once the underlying primitives they call
   (reclaim, retry-via-recompute, pause-gating) have been proven trustworthy through steps 1-6, not
   before.

**Why this order and not, say, planner-first:** the planner is the most "interesting" component
but the least load-bearing for *proving the system works* — a correct graph written to a broken
board is worthless, while a hand-written test graph run through a correct board/worker pair proves
the hard part (concurrent claim safety, failure isolation) without needing the strong model in the
loop at all. Build the part that has to be right before the part that's easy to get right.

## Sources

- `hermes_cli/kanban_db.py`, `hermes_cli/kanban_swarm.py`, `hermes_cli/kanban_decompose.py` —
  read directly from the local Hermes Agent install at
  `~/AppData/Local/hermes/hermes-agent/hermes_cli/`. Primary source; HIGH confidence — this is
  working, running code implementing this exact pattern, not documentation about the pattern.
- `.planning/PROJECT.md`, `docs/ARCHITECTURE.md`, `src/run_queue.py` (this repository) — the
  existing substrate and already-made decisions this research extends.
- [Anthropic — Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents)
  — orchestrator-workers pattern definition and cost/complexity trade-off framing. MEDIUM-HIGH
  confidence, official source.
- [Amazon SQS visibility timeout docs](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html)
  and [SQS heartbeat/watchdog pattern](https://www.tecracer.com/blog/2023/03/the-beating-heart-of-sqs-of-heartbeats-and-watchdogs.html)
  — lease-not-lock framing, heartbeat-extends-lease pattern. HIGH confidence, official + verified.
- [Kubernetes Leases](https://kubernetes.io/docs/concepts/architecture/leases/) — lease
  duration/renew-deadline/retry-period tuning guidance for heartbeat-based liveness and leader
  election. HIGH confidence, official docs.
- [Prisma — Postgres job queue with SKIP LOCKED](https://www.prisma.io/blog/you-dont-need-a-job-queue-postgres-already-has-skip-locked)
  and [Vlad Mihalcea — SKIP LOCKED job queue](https://vladmihalcea.com/database-job-queue-skip-locked/)
  — atomic claim pattern for SQL-backed queues, and why SQLite's single-writer model gets the same
  guarantee without needing `SKIP LOCKED` explicitly. MEDIUM-HIGH confidence.
- [Astronomer — Airflow trigger rules](https://www.astronomer.io/docs/learn/airflow-trigger-rules)
  — comparison point for DAG failure-propagation semantics (`all_success` vs
  `none_failed_min_one_success` vs `fail_fast`). MEDIUM confidence, official partner docs.
- [Temporal — Event History](https://docs.temporal.io/encyclopedia/event-history) and
  [Understanding Temporal](https://docs.temporal.io/evaluate/understanding-temporal) — durable
  execution / replay-from-persisted-history pattern, cited as the general-case validation of
  "persist the plan, not the conversation." MEDIUM-HIGH confidence, official docs.
- Git worktree isolation for parallel AI agents:
  [Augment Code guide](https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution),
  [Upsun](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents) —
  confirms `kanban_db.py`'s `workspace_kind`/`workspace_path`/`branch_name` fields implement a
  now-standard isolation primitive. MEDIUM confidence, multiple independent sources agreeing,
  WebSearch-verified.
- [GitLab ChatOps docs](https://docs.gitlab.com/ci/chatops/) — least-privilege framing for
  chat-triggered actions routing through existing platform authorization rather than a
  chat-specific permission model. MEDIUM confidence, official docs.
- Blackboard architecture pattern (Hearsay-II and descendants) — MEDIUM/LOW confidence: the
  historical origin (Hearsay-II, Carnegie Mellon, 1970s speech-understanding system) is stated from
  training-data knowledge and was not independently re-confirmed by a WebSearch source in this
  session; the *current* application of the pattern to multi-agent LLM systems (shared repository +
  specialized readers/writers + no direct messaging) was WebSearch-verified against multiple 2026
  sources (Medium/Denis Petelin, Medium/edoardo schepis) and matches `kanban_swarm.py`'s
  implementation closely enough to treat the mapping as reliable even where the historical citation
  is lower-confidence.
- `.planning/research/STACK.md`, `.planning/research/PITFALLS.md` (this milestone, parallel
  research) — cross-referenced for consistency; the JSON-vs-SQLite tension between STACK.md's
  recommendation and this file's finding is called out explicitly above rather than silently
  overridden.

---
*Architecture research for: orchestrator/worker swarm — plan-once/execute-many task graphs
coordinated through a shared task board*
*Researched: 2026-09-02*
