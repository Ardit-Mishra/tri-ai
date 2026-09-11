# Proposed Gaps — blueprint review capture

**Status:** capture — no slice or probe started
**Origin:** 2026-09-10 independent Phase 3 review + a blueprint evaluation against Tri-AI's
architecture. The blueprint's "honest gaps" list and the partial control-plane entry,
each mapped to where it lives today and what would have to happen before it ships.
**Rule:** nothing here changes `STATE.md`'s active milestone: Phase 4. This file records decisions;
the phase-4 plan (`phases/phase-4-plan.md`) and its successors are where slice work happens.

## How to read this

Each entry: **current state** (exact file/line refs, only what I verified by reading),
**the gap**, **decision** (slice-worthy → which phase, hypothesis → what probe gates it, or
design decision → recorded, no slice),
**why**. A probe must be a named command that can fail for a named deliberate break, per the
project's methodology — a "we should look into this" sentence is not a probe.

---

## 1. Error classification: environment vs. logic failure

**Current state:** every non-zero verify exit follows the same state path — bounded retry,
then the kernel circuit breaker (2 consecutive failures) → `blocked` (`src/worker.py:485`).
`executor` already lexically separates spawn-error vs timeout vs exit ≠ 0, but none of that
reclassifies state or adjusts retry policy.
`.planning/research/FEATURES.md` actively argues against exponential backoff: "solves a shared-
external-service problem this system doesn't have". That is a *different* claim than the gap here —
backoff for a shared API is not env-vs-logic bucketing of mine.
`PITFALLS.md:152-168` caps retries (2–3) and wants backoff so an unattended overnight run is never
burned on an unfixable node; that already shipped as the circuit breaker.

**The gap:** an OOM, package-mirror time-out, or network blip is not the same failure as a test
assertion. Today both trip the circuit breaker identically. The blueprint's sensible half: bucket
the non-zero exits, give environment-class failures an exponential backoff that does **not** touch
graph status, and let only logic-class failures record against the node.

**Decision:** **slice-worthy → Phase 4 companion slice.** Re-visit `.planning/research/FEATURES.md`'s
anti-backoff stance when writing it — it is about protecting a shared service, not about not confusing two
failure classes. Probe-free: the classifier is deterministic (exit code + stderr patterns +
outcome kind already in `executor`), so a proving command is a deliberate-break test matrix, not a
measurement.

## 2. Worktree creation for same-repo concurrency

**Current state:** the declared Phase 3 intent is *not* shipped. `AUTONOMOUS-RUNBOOK.md:52` sets
the boundary ("per-subtask worktrees or another proven repo partition before it starts a second
worker on one repository"); what shipped is **serialization + partition** (`src/dispatcher.py`
`partition_groups`, same workspace key → one serial group). `board.create_task` accepts
`workspace_kind='worktree'` + `branch_name` (`src/board.py:219-233`) but nothing creates the
worktree — the task carries a path that must already exist. `PITFALLS.md:338,414` recommend
per-subtask worktrees so recovery is `git worktree remove --force`, never a manual hand-fix.

**The gap:** same-repo tasks are serially scheduled today. The blueprint's "concurrent siblings via
distinct worktrees" is our own plan's stated ambition, cut at Slice 2.

**Decision:** **slice-worthy, highest-leverage execution-side gap → Phase 4 first non-Telegram
slice, or the first slice of whatever the next execution phase is.** This also carries review
finding F1's sibling: the real `--once` subprocess launcher isn't wired and
`src/dispatcher.py:128-145` `filter_running` is dead code — see entry 7. Boundary to hold from the
Phase 3 plan: when isolation cannot be proven, leave the task ready and report a skip; never reuse
a `dir` workspace or delete one.

## 3. Multi-channel ingestion (Telegram execute side)

**Current state:** CLI + queue file + scheduler exist (Phase 2 `src/assign.py`, `src/chores.py`).
Telegram is *read-only observability only*, and that is not even built yet — Phase 4 is watch-only;
write actions (cancel/retry) are Phase 5, a pinned ordering in `STATE.md`. The execute side is the
"documented gap" of `PROJECT.md:77-78` (messaging Bob works, nothing reads the inbox back out and
executes it), tied to `PROJECT.md:117` "control from Telegram".

**The gap:** inbox → execute → report does not exist.

**Decision:** **slice-worthy → Phase 5 write-side slice.** Deliberately after read-only Phase 4:
do not mutate a live graph before reclaim/retry/pause primitives are proven (pinned in `STATE.md`).

## 4. Model routing by subtask type

**Current state:** one fixed worker-model path. The 4-deep fallback chain + route registry exist
from the pre-board milestone (`PROJECT.md:31`, validated) but route by *resolved model / quota*,
not by *subtask kind*. `STACK.md:186` floats a hypothesis: long generation-heavy subtasks "likely"
should run serially — explicitly a hypothesis, not measured.

**The gap:** nothing maps code vs prose vs markup subtasks to the best-fit local model.

**Decision:** **hypothesis → probe before a slice.** The project's own methodology: this is exactly
the kind of claim to verify empirically, not infer. Gate: a per-task-kind accuracy/VRAM probe (a
small deliberate dataset of code / prose / markup chores with binary oracles, run across a short
list of candidate local models) before any routher ships. Until then the fixed configuration stands.

## 5. RAG / vector retrieval for context-injected workers

**Current state:** nothing. No mention in any `.planning` file. The architecture was deliberately
chosen to *avoid* stuffing context into restricted windows.

**The gap:** the blueprint wants a local vector store so workers pull only the precise function
definitions they need instead of a whole repo.

**Decision:** **hypothesis → probe before a slice, and cost-probe first of all.** ChromaDB /
FAISS + sentence-transformers on a 12 GB VRAM box that already runs 17 Ollama models is a real
memory bet. The probe is a measurement in the style of `concurrency_results.json`: peak VRAM and
wall time of a small retrieval+inference run at a working concurrency, compared with the current
no-retrieval path, on the actual desktop. If the probe shows negative or flat value, drop it. $0
constraint holds (local embedders only).

## 6. Localized dynamic graph mutation ("graph healing")

**Current state:** nothing in any plan. Appears only in the review conversation.

**The gap / the pushback:** a small model proposing sub-graph patches (add an install step, insert a
data-cleaning pre-task) is the "small models judge" failure mode `PROJECT.md:84-91` documents — the
same model that deleted 387/389 lines and reported success. **Auto-merge of a local model's patch is
rejected.** The safe shape: a worker that hits a missing-dependency blocker proposes a patch to the
**operator or the premium planner lane**, never auto-merges.

**Decision:** **design decision, not a slice.** Recorded here as: human-in-loop or reject. Any
future slice is gated on a concrete "propose only, never merge" contract and a proving test that a
local model's proposed patch cannot land without an explicit operator/premium-lane approval.

## 7. `dispatch_one` env-var contract (review F1)

**Current state:** `src/dispatcher.py:205-211` builds a local `env` dict setting
`TRIAI_BOARD_DB` / `TRIAI_LEDGER` / `TRIAI_RUNS_DIR`, then calls `launcher(task_id)` — the dict is
never delivered to any subprocess. The docstring promises an env contract nothing honors. Dead
sibling: `filter_running` (`src/dispatcher.py:128`, import-only in tests) is never called by
`dispatch()`.

**The gap:** a future production launcher that relies on `dispatch_one`'s documented env wiring
would silently inherit the parent's board instead of the intended one.

**Decision:** **slice-worthy defect → Phase 4, first slice.** Either pass the env to the launcher
explicitly (signature change / context manager) or delete the dead code and the misleading
docstring. Remove or wire `filter_running`. This is a correctness-of-contract fix, not a feature.

## 8. Observable ledger & control plane (telemetry)

**Current state:** ledger is real — every attempt writes rows with worker `host:pid`, `run_id`,
`verify_exit`, `verify_outcome`, timings, and log paths (`src/worker.py` `_ledger_entry`). No
dashboard of any kind exists. Telegram read-only is Phase 4; write actions are Phase 5; a
"real-time visual execution grid" is not in any plan.

**The gap:** the blueprint wants a live dashboard (WebSocket grid, pause/resume/retry hooks, an
artifact inspector). Much of its *content* already exists as ledger rows; the UI does not.

**Decision:** **slice-worthy but later than the control primitives it surfaces.** Phase 4 read-only
state is the minimum viable observability; Phase 5 write hooks (retry/cancel) are the minimum
viable control; only after both are proven does a live dashboard earn a slot — and even then it is
a display over the existing ledger/board, not a new state store. Pending until Phase 4/5 evidence
exists.

---

## 9. Agent Memory & Self-Evolution Subsystem

**Current state:** nothing. Workers execute tasks with no memory of past runs. The ledger
captures what happened but nothing learns from it. The planner builds graphs from scratch every
time. No episodic index, no knowledge graph, no procedural rules, no self-evolution.

**The gap:** Tri-AI's execution loop is stateless across runs. A worker that failed 5 minutes
ago on a task with exit 7 will attempt the same thing again with no awareness of the failure. The
planner has no access to historical patterns. The operator has no way to inject learned lessons
into worker execution.

**Decision:** **slice-worthy → Phase 5 companion (episodic + procedural), Phase 5C (semantic),
Phase 6 (JARVIS dashboard + evolution loop), Phase 7 (planner integration).** Full design at
`.planning/research/memory-subsystem-design.md`. 3-tier memory stack (episodic/semantic/
procedural) with zero LLM calls, zero VRAM overhead, all CPU file I/O. JARVIS dashboard is the
visual neural network view the operator requested. Self-evolution is failure-driven rule creation
with citation validation.

**Why:** the memory system is the foundation for everything after Phase 4. Without it, the
dashboard shows data but nothing learns from it, the planner repeats patterns that already failed,
and the operator cannot inject lessons without editing code. With it, every failure produces a
rule, every successful pattern crystallizes into a macro, and the dashboard shows the brain
learning in real time.


## 10. Distributed Hardware Delegation & Offloading Protocol

**Current state:** all execution happens on the desktop. The laptop writes task graphs and
monitors via Telegram but never executes. The desktop's RTX 3060 is the sole compute node.
Tailscale mesh is provisioned (PROJECT.md) but unused for task execution.

**The gap:** the laptop's 11 Ollama models sit idle. Lightweight tasks (markdown, parsing,
simple verification) could run on the laptop, freeing the desktop for heavy inference. If the
laptop hits a resource ceiling (OOM, timeout), the task should automatically offload to the
desktop.

**Decision:** **slice-worthy → Phase 7 (daemon foundation + remote protocol + offload
detection).** Full design at `.planning/research/distributed-delegation-design.md`. The board
stays on the desktop; a thin HTTP daemon exposes board operations to remote workers. Workspace
sync via git archive over HTTP. Offload triggers: VRAM saturation, generation timeout, OOM.
The phone remains controller-only (Telegram).

**Why:** single-point-of-failure is the gap. If the desktop is off, nothing runs. With
delegation, the laptop can execute lightweight tasks independently and offload heavy ones when
the desktop is available. The daemon pattern also opens the door to future nodes (another
desktop, a cloud instance) without changing the board.

---

## Ranked next actions

Slice 1 is accepted. Slice 2 is implemented but awaits the adversarial
correction tests recorded in `.planning/reviews/phase-audit-2026-09-10.md`.
Slices 3-4 and every later phase remain unaccepted candidates.

1. **#7 `dispatch_one` contract + `filter_running`** — fix first: removes a latent lie in the docstring and dead code, tiny.
2. **#2 worktree creation** — the biggest unshipped promise; unblocks same-repo concurrency.
3. **#1 error classification** — re-examine `.planning/research/FEATURES.md`'s anti-backoff note; deterministic classifier, no probe.
4. **#9 memory subsystem (episodic + procedural)** — foundation for everything after Phase 4; design at `.planning/research/memory-subsystem-design.md`.
5. **#3 Telegram execute side** — Phase 5, after read-only Phase 4 proves the primitives.
6. **#8 JARVIS dashboard + #9 semantic memory** — visual neural network view + knowledge graph; depends on episodic memory.
7. **#9 self-evolution loop + planner integration** — failure-driven rules + macro crystallization; depends on procedural + semantic memory.
8. **#10 distributed delegation** — daemon foundation + remote protocol + offload detection; design at `.planning/research/distributed-delegation-design.md`.
9. **#4 model routing, #5 RAG** — probes gate each; #5's is the more expensive/doubtful bet.
10. **#6 graph healing** — design rejected as auto-merge; human-in-loop only, no current slate.
