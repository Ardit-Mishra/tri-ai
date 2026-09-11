# Project Research Summary

**Project:** Tri-AI Swarm — orchestrator and workers
**Domain:** Local-first, single-operator multi-agent orchestration — a premium model plans once and exits, small local models execute the resulting task graph in parallel on one consumer GPU, coordinated through a shared task board, verified only by exit codes.
**Researched:** 2026-09-02
**Confidence:** MEDIUM-HIGH overall — architecture is HIGH (verified against real, running, already-installed code); the central throughput question that all four research passes flagged as open has since been closed by an on-machine measurement, not left as a guess.

> **Historical numbering notice (2026-09-10):** the "Implications for
> Roadmap" phase labels below are the pre-implementation research sequence,
> not the canonical execution roadmap. Phase 3's planner/concurrency work was
> later combined and completed; the active Phase 1-8 order and status live in
> `../ROADMAP.md` and `.planning/reviews/phase-audit-2026-09-10.md`.

## Executive Summary

This is a personal job-orchestration system, not a general agent framework, and the research converges hard on that framing: every mainstream multi-agent framework surveyed (CrewAI, AutoGen/AG2, LangGraph, OpenAI Agents SDK, Letta, smolagents, Ray, Prefect, Dagster) was rejected, either because its core primitive is agent-to-agent messaging (explicitly banned in PROJECT.md) or because it duplicates persistence/verification machinery this project already owns and trusts. The right shape is small and boring: a persisted task graph, a shared claim/lease board, worker processes that pull work and are gated by nothing but a verify command's exit code, and a thin Telegram control surface layered on top last. Two things happened after the four research passes were briefed that materially change what the roadmap can assume, and both are treated as authoritative over the individual research files where they conflict.

First, ARCHITECTURE.md was written with actual filesystem access to the installed Hermes Agent and found that `hermes_cli/kanban_db.py` (~10K lines, already in production use) already implements essentially the entire board substrate this milestone needs: atomic CAS claim, lease expiry with heartbeat renewal, PID-verified dead-worker reclaim, dependency-gated DAG promotion with per-branch failure isolation, and a blackboard via structured `task_comments`. STACK.md, written without that filesystem access, recommended building a new plain-JSON task-graph representation instead — a reasonable inference from general practice, but wrong here because the thing it was inferring toward already exists, tested, with race conditions already found and fixed in production. Where the two disagree, ARCHITECTURE.md's direct source reading wins: reuse `kanban_db.py` as the board, not JSON files. The one thing genuinely missing from it — no `verify_command` column, no verify-then-revert semantics — is the entire scope of new work this milestone actually requires at the persistence layer.

Second, the open empirical question every one of the four research passes flagged — "does dispatching N workers at the local Ollama model actually buy parallel throughput on this specific RTX 3060?" — has now been measured directly, and the answer is no, not currently. Wall-clock time scales almost linearly with concurrency (2.88s → 5.33s → 7.52s → 9.93s for N=1..4), aggregate throughput speedup tops out around 1.16x at N=4, and peak VRAM is flat at 5278 MiB regardless of N — the flat VRAM is the tell: no additional parallel slots are being allocated, requests are serializing behind one slot because `OLLAMA_NUM_PARALLEL` is unset on this machine. This forbids the roadmap from treating concurrent local-model generation as a speed win. It does not forbid concurrency generally — CPU/IO-bound verify work (pytest, tsc, npm audit, git status) never touches this ceiling because it doesn't call Ollama at all, and multi-repo unattended reach (the operator asleep while five repos work through their chore queues) is a real value proposition independent of per-call speed. The roadmap should build v1 on that distinction explicitly, not blur it.

## Key Findings

### Recommended Stack

`STACK.md`'s framework survey stands: nothing external should be adopted for orchestration — every framework surveyed either violates the no-agent-messaging decision or duplicates trusted machinery. Its `asyncio` + `httpx` recommendation for I/O-bound dispatch is sound and unaffected by the measurement below. **Its one recommendation that is superseded: "plain JSON, one file per task-graph" for the persisted DAG.** ARCHITECTURE.md's direct read of the installed Hermes codebase found this isn't needed — `kanban_db.py` already stores the graph shape (`task_links`), live claim state, run history, and a blackboard, all in one WAL-mode SQLite file with atomic CAS semantics JSON-plus-file-locking cannot cheaply replicate. STACK.md's own section 3 (SQLite WAL for live claim/status board, additive to the ledger) was actually pointed in the right direction — the disagreement is narrowly about where the graph *definition* lives (JSON vs. reusing the DB that already models it), and it resolves in favor of reuse.

**Core technologies, adjudicated:**
- `kanban_db.py`'s SQLite WAL board (reused directly, or its schema/CAS shape vendored) — the task graph, live claim state, and blackboard. Supersedes STACK.md's plain-JSON-graph recommendation; ARCHITECTURE.md wins per direct source verification.
- `asyncio` + `httpx.AsyncClient` — bounded concurrent dispatch of CPU/IO-bound subtasks to Hermes. Still correct; unaffected by the concurrency measurement, which was specifically about GPU generation, not dispatch mechanics.
- Nothing new for orchestration frameworks — confirmed independently by both STACK.md's survey and ARCHITECTURE.md's finding that the gap is wiring (one missing DB column), not a missing framework.

### Expected Features

**Must have (v1, table stakes):** task-board state machine (reuse `hermes kanban`'s model), a hard concurrency limit (start conservative — see measurement below, not a framework default), per-subtask timeout (extend existing `queue.jsonl` fields), per-subtask isolated log capture (a precondition — interleaved concurrent stdout makes the ledger meaningless the moment two workers run at once), in-flight cancellation reachable from the CLI first, Telegram read-only board status + cancel/retry, ledger extended to graph-node rows (still append-only, still exit-code-gated), and — critically — **v1 scoped to 2-3 repos' read-only verification chores only** (test suite on a green branch, typecheck, dependency audit), explicitly not chores that ask a worker to fix failing code. This scoping decision is now stronger than when FEATURES.md proposed it: it was proposed to avoid contaminating the first measurement with unmeasured GPU contention, and the contamination risk it warned about is exactly what the measurement below confirms is real for generation-bound work.

**Should have (differentiators):** Telegram board control (cancel/retry, not just view), per-subtask verify command extended to every graph node without exception, failure isolation per branch (real dependency edges, not a flat list rebranded as a graph), and duration-as-CPU-offload-signal — near-zero marginal cost since duration is already captured, and it's the cheapest available answer to "is concurrency helping or hurting."

**Defer to v2+:** GPU-bound "fix and reverify" chores running concurrently (defer until `OLLAMA_NUM_PARALLEL` behavior is actually measured — see Gaps below), full replayable per-subtask conversation trace, task graph visualization, any configurable workflow DSL beyond the existing schema. **Reject outright:** agent personalities/personas, agent-to-agent chat, a hosted observability SaaS, a dashboard duplicating the ledger, generality for imagined future users — all explicitly named anti-features that would burn context or trust for no gain a single operator needs.

### Architecture Approach

Plan-once/execute-many: a premium model decomposes one assigned task into a DAG, writes it to the board, and exits — it never stays "on call." Workers run a claim → `hermes -z` → verify_command → complete/revert loop against the board, coordinating only through reads and writes of shared board state (blackboard pattern), never through direct messages. A lease-based atomic claim (CAS `UPDATE ... WHERE status='ready' AND claim_lock IS NULL`) with heartbeat-extended, PID-verified reclaim is the mechanism that makes "several concurrent workers, one shared board" safe without a distributed lock service. Dependency-gated promotion (`recompute_ready`, re-derived after every state change) is what gives partial-success/failure-isolation behavior for free, structurally, without special-case logic. A thin Telegram control surface is least-privilege: unrestricted reads, three narrow writes (cancel/retry/pause), never graph creation or verify-command edits.

**Major components:**
1. **Board (`board.py`)** — thin wrapper around `kanban_db.py`, or a vendored copy of its four tables and CAS/lease/reclaim functions, plus the one new `verify_command` column. The only file that owns concurrency-sensitive code.
2. **Worker loop (`worker.py`)** — retargets `run_queue.py`'s already-proven verify→pass/revert logic to pull its next task from `board.claim_task()` instead of iterating `queue.jsonl`. Extends proven ground; does not rewrite it.
3. **Planner (`planner.py`)** — invoked once per assigned task, writes a graph via the board API, exits. No loop, no daemon — enforced structurally so it cannot silently become a second orchestrator.
4. **Control surface (`control.py`)** — Telegram command handlers; reads are unrestricted, writes are exactly `cancel`/`retry`/`pause`, each one bounded board-API call.

### Critical Pitfalls

1. **GPU/VRAM thrashing disguised as parallelism** — now confirmed empirically on this exact hardware (see below), not just theorized. Avoid by scoping v1 to CPU/IO-bound work and treating any generation-bound concurrency number as unproven until separately measured.
2. **Small-model failures compound silently across graph edges** — a fabricated/truncated upstream result can become trusted context for a downstream node if only node-local verify is checked. Avoid by having every downstream node's setup step independently confirm the upstream artifact is real (non-trivial diff, file changed, build output exists), never trusting a stored "done" status alone.
3. **Verify commands that pass vacuously** — exit 0 proves the command succeeded, not that it checked anything meaningful (a test suite that collects zero tests still exits 0). Avoid by authoring every verify command with a documented negative test (deliberately break the target, confirm the command fails) before trusting it to gate real work.
4. **Board coordination failures** — duplicate work, stale claims from dead workers, and unbounded retry livelock are all real failure shapes for any shared board without atomic CAS, lease TTLs, and a retry cap. This is exactly the machinery `kanban_db.py` already has and has already debugged in production — the reuse decision above is also the mitigation for this pitfall.
5. **Over-parallelizing work that is actually sequential** — the project's own first-target hypothesis ("run five repos' chores in parallel rather than serially") was explicitly flagged as unproven and requiring a bake-off before committing to board/scheduling machinery. The concurrency measurement below is that bake-off's first data point, and it says the speed case for generation-bound parallelism is weak; the case for unattended reach is untouched by it.

## What Is Already Built — Reuse, Do Not Rewrite

- **`hermes_cli/kanban_db.py`** (installed, ~10K lines, in production use): atomic CAS claim (`claim_task()`, CAS `UPDATE ... WHERE status='ready' AND claim_lock IS NULL`), lease expiry with heartbeat renewal (`heartbeat_worker`/`heartbeat_claim`), PID-verified dead-worker reclaim with defer-if-still-alive logic (`detect_crashed_workers`, `_terminate_reclaimed_worker`, `_defer_reclaim_for_live_worker` — a specific double-spawn bug already found and fixed), dependency-gated DAG promotion (`recompute_ready`, gates only on direct parents, so failed branches don't block unrelated siblings), and a blackboard via structured `task_comments` scoped to a shared root task. This is the board. Build `board.py` as a thin wrapper around it, or vendor its schema and CAS shape — not a rewrite.
- **`src/run_queue.py`**: the verify→pass/revert loop is already correct and proven (9/9 across 6 repos, ~1,216s, $0). `worker.py` should retarget its "next task" source from iterating `queue.jsonl` to `board.claim_task()` — the verify-gate logic itself does not change.
- **`ledger.jsonl`**: the append-only run history that makes the autonomy claim citable. Extend its schema to graph-node rows; do not replace or fragment it per worker.
- **Windows Scheduled Task durability pattern** (`docs/AUTONOMY.md`): already proven to survive SSH/laptop disconnection. Tie every worker's board claim to this process lifecycle, not an assumed-live process.

## The Single Genuine Gap

`kanban_db.py` has no `verify_command` column and no verify-then-revert semantics. Its existing notion of "did this succeed" is a human/verifier-card judgment gated by comment metadata — exactly the "agent's report as evidence" pattern this project's whole design already rejects. **The entire new-build scope at the persistence layer is: add one additive column** (`verify_command TEXT`, optionally `verify_timeout`) via the codebase's existing `_add_column_if_missing` migration helper (already used historically for `worker_pid`), then wire `worker.py` to run the claimed task's prompt through `hermes -z`, run `verify_command` independently, and call `complete_task()`/`fail_task()`+revert based on its exit code alone — never on the worker's self-report. Everything else in ARCHITECTURE.md's requirements table (persistence, atomic claim, dead-worker reclaim, blackboard, failure isolation, observability) is already implemented and should not be re-derived from scratch.

## What the Measurement Forbids the Roadmap From Assuming

On the actual RTX 3060, `qwen3.5:4b`, 160 tokens/request, best-of-2, with `OLLAMA_NUM_PARALLEL` unset (the default, and this machine's current state):

| Concurrency (N) | Wall-clock | Aggregate tok/s | Speedup vs. serial | Peak VRAM |
|---|---|---|---|---|
| 1 | 2.88s | 55.6 | 1.00x | 5278 MiB |
| 2 | 5.33s | 60.0 | 1.08x | 5278 MiB |
| 3 | 7.52s | 63.8 | 1.15x | 5278 MiB |
| 4 | 9.93s | 64.5 | 1.16x | 5278 MiB |

Wall-clock scales almost linearly with N; per-request latency degrades in step; **VRAM is flat regardless of N**, which is the direct evidence that no additional parallel slot is being allocated per request — requests are serializing behind a single generation slot, not running concurrently on the GPU at all. The roadmap must not assume that dispatching N workers at local generation buys anything close to Nx throughput; on current configuration it buys roughly 16% at N=4, plateauing. This is specific to **generation-bound** calls through Ollama. It says nothing about, and does not constrain, concurrency for CPU/IO-bound verify work (pytest, tsc, npm audit, git status, dependency scans) that never calls the local model at all — that work is genuinely parallel and unaffected by this ceiling. v1's scoping decision (read-only verification chores only, no fix-and-reverify) is therefore not just a risk-reduction convenience, it is the only way to build a first version whose concurrency claims are true given this data.

## What Remains Unmeasured, and What Measuring It Would Cost

Whether raising `OLLAMA_NUM_PARALLEL` above 1 changes this picture is genuinely unknown — it is the one variable the measurement did not vary, because doing so requires an Ollama restart. That restart interrupts the always-on Hermes/Ollama process the Telegram gateway ("Bob") depends on, and **consent for that interruption has not been given.** This is not an oversight; it is a deliberate, correctly-drawn line, and the roadmap should treat "measure `OLLAMA_NUM_PARALLEL>1`" as its own explicit, consent-gated step — not bundled silently into a concurrency phase — with the cost stated plainly: a brief outage of Bob/the Telegram gateway, requiring Ardit's explicit go-ahead before scheduling it. Until that measurement exists, any concurrency number above the conservative default for generation-bound (fix-and-reverify) work is a guess, not a fact, and PITFALLS.md's Pitfall 1 checklist item ("ceiling set from data, not guess") is not yet satisfied for that configuration — only for the default, unset configuration, which is now well measured.

Separately unmeasured and flagged by PITFALLS.md, independent of the GPU question: whether `qwen3.5:4b`'s demonstrated honesty (on a single fixed prompt) generalizes to reliable tool-call/JSON formatting across a longer multi-step subtask chain — published benchmarks put the practical reliable-tool-calling floor around 7B, well above this model's 4.7B, with per-call reliability compounding multiplicatively across steps. This argues for keeping v1 subtasks in the single-command, oracle-checked shape already proven, not multi-step chains, until a chain-length benchmark is separately run.

## Implications for Roadmap

### Phase 1: Board substrate — close the one genuine gap
**Rationale:** Nothing downstream is testable without a place to durably write and read a graph, and the reuse-vs-rebuild decision has to be made concrete first because every later component's shape depends on it (ARCHITECTURE.md's Suggested Build Order, step 1).
**Delivers:** `board.py` wrapping (or vendoring) `kanban_db.py`, plus the additive `verify_command`/`verify_timeout` migration. Proven with a single fake worker: two processes racing to claim the same row, one claiming then killed to verify reclaim fires.
**Addresses:** Task-board state machine (FEATURES P1), the differentiator "verify command extended to every graph node."
**Avoids:** Pitfall 4 (board coordination failures) — by construction, since the machinery being reused already had its races found and fixed in production.

### Phase 2: Single-worker verify-gated execution loop, scoped to CPU/IO-bound chores only
**Rationale:** A claim bug discovered underneath real concurrent `hermes -z` calls is nearly undebuggable; discovered with one worker, it's a unit test. This is also where the measurement's forbidden assumption is enforced structurally — v1's first target must be chores that don't call the GPU for generation at all (test suite on a green branch, typecheck, dependency audit), so the first "is this working" measurement isn't contaminated by unmeasured GPU contention.
**Delivers:** `worker.py`, retargeting `run_queue.py`'s proven verify→pass/revert loop onto `board.claim_task()`. Ledger extended to graph-node rows with full (not truncated) output and worker/model identity, built alongside this phase, not retrofitted after a failure exposes the gap.
**Addresses:** Per-subtask isolated log capture, ledger extension (FEATURES P1).
**Avoids:** Pitfall 6 (observability blindness), Pitfall 3 (vacuous verify — enforce the negative-test authoring standard here).

### Phase 3: Planner
**Rationale:** Can be built and tested in parallel with Phase 2 (only needs Phase 1's schema to write into), but should not be wired to trigger real execution until Phase 2 is trustworthy, or a planner bug and a worker bug become indistinguishable.
**Delivers:** `planner.py` — one invocation, writes a graph via the board API, exits. No `while True`, no persistent connection, structurally incapable of becoming a second orchestrator.
**Implements:** Plan-once-persist-execute-later pattern.
**Avoids:** Pitfall 2 (compounding failures across edges) at the schema level — every edge must carry an independent artifact check, not just a status read, and this is the phase where that requirement gets baked into the graph schema.

### Phase 4: Concurrency — multiple workers, at a hardcoded, measurement-justified cap
**Rationale:** Only once Phases 1-3 are independently proven correct should a second/third worker run against the same board — this is the step that actually exercises claim-race and reclaim mechanics under real contention. The measurement above supplies the number: for the current (`OLLAMA_NUM_PARALLEL` unset) configuration, generation-bound concurrency plateaus near 1.16x by N=4, so a cap above 2 buys negligible speed for generation-bound work and should not be raised without the separate, consent-gated `OLLAMA_NUM_PARALLEL` experiment. For the CPU/IO-bound chores Phase 2 actually scoped v1 to, this GPU ceiling does not apply at all — concurrency there is bounded by CPU/repo contention (git worktree isolation, Pitfall 8), not VRAM.
**Delivers:** N worker loop instances, failure isolation validated (deliberately fail one branch, confirm unrelated branches keep progressing).
**Avoids:** Pitfall 1 (GPU/VRAM thrashing — already measured for the default config, not a guess), Pitfall 5 (over-parallelizing sequential work — v1's chores are read-independent by construction), Pitfall 8 (concurrent git operations — enforce worktree isolation the moment two concurrent nodes could touch the same repo).

### Phase 5: Read-only Telegram observability
**Rationale:** Needs Phases 1-4 producing real state to display; ship before any write action so the read path is battle-tested first.
**Delivers:** `control.py` status/list handlers only — board state, ledger tail, heartbeat age (so a stuck task is distinguishable from progress, per FEATURES' UX pitfalls).
**Addresses:** "Board observable... from Telegram" (FEATURES differentiator, closes the documented inbox→execute→report gap in PROJECT.md).

### Phase 6: Telegram control actions — cancel, retry, pause
**Rationale:** Last, deliberately — each of these mutates a live graph remotely from the least-scrutinized surface in the system; only expose once the underlying primitives (reclaim, retry-via-recompute, pause-gating) are proven trustworthy through Phases 1-4.
**Delivers:** Three named, bounded board-API calls behind Telegram commands. No raw SQL, no new-graph creation, no verify-command edits from chat — enforced as a permissions boundary, not just a code boundary.
**Avoids:** Anti-Pattern 3 (letting the control surface create or edit graphs).

### Deferred (v1.x / v2+, not this roadmap)
Scale from 2-3 to all five repos (trigger: v1 passes and a real CPU-offload/duration baseline exists); graph-level failure isolation with real multi-step dependency edges (trigger: some repo's chore chain is genuinely branchy, not flat); GPU-bound "fix and reverify" chores running concurrently (trigger: the separate, consent-gated `OLLAMA_NUM_PARALLEL>1` measurement has actually been taken — do not assume the same concurrency cap validated for read-only verification is safe for generation-bound fixing, these are two different bottlenecks per FEATURES.md's dependency graph); full replayable conversation trace; task graph visualization.

### Phase Ordering Rationale

- Board before worker before planner before concurrency before Telegram: each step's correctness is a precondition for the next step being debuggable at all — a claim bug found under one fake worker is a unit test; the same bug found under four real concurrent `hermes -z` calls is a production incident.
- The measurement collapses what would otherwise have been "Phase 0: concurrency feasibility spike" into a completed input rather than a roadmap phase — but its scope is narrower than "concurrency is fine," so the phases above encode the narrower, true finding (fine for CPU/IO-bound, unproven-beyond-16% for generation-bound) rather than a blanket green light.
- Telegram is deliberately last on both the read and write side — it is a thin client with no primitives of its own; building it before the primitives exist would mean building against a moving target.

### Research Flags

Needs research or a further deliberate measurement before being trusted:
- **Phase 4 (concurrency), if the cap is ever raised for generation-bound work:** requires the separate `OLLAMA_NUM_PARALLEL>1` experiment, gated on Ardit's explicit consent to a brief Bob/Telegram outage. Do not fold this into Phase 4's default implementation.
- **Phase 3 (planner):** dependency-edge correctness in the decomposition is named in ARCHITECTURE.md as "the single highest-leverage correctness decision in the whole system" — a spurious parent link falsely couples two branches' failure. Worth a specific review/test pass, not just code review.
- **Verify-command authoring standard (cross-cutting, Phases 1-2):** each verify command needs a documented negative test (PITFALLS.md Pitfall 3) — this is an authoring discipline to establish explicitly, not something that falls out of the board/worker code automatically.

Phases with standard, already-proven patterns (skip `research-phase`):
- **Phase 1 (board):** `kanban_db.py`'s claim/lease/reclaim/blackboard mechanics are already running in production with known-fixed race conditions — implement the additive migration, don't re-research the pattern.
- **Phase 2 (single worker):** `run_queue.py`'s verify→pass/revert loop is already proven (9/9, 6 repos) — retarget its task source, don't redesign its verification discipline.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH, with one recommendation superseded | Framework-rejection survey is solid (verified against PyPI/official docs). Its plain-JSON-graph recommendation is superseded by ARCHITECTURE.md's direct source read — noted explicitly above, not silently dropped. |
| Features | MEDIUM-HIGH | Table-stakes job-queue mechanics are well-established industry consensus; anti-pattern claims (agent personas, agent-to-agent chat) are WebSearch-verified across multiple sources; the first-version bottleneck/scoping analysis is grounded in this project's own measured constraints. |
| Architecture | HIGH | Board/claim/lease/reclaim mechanics verified directly against real, running, already-installed code (`kanban_db.py`), not documentation about the pattern. General orchestrator/worker literature cross-checked against official docs (Anthropic, AWS, Kubernetes, Temporal, Astronomer). |
| Pitfalls | MEDIUM-HIGH, upgraded to HIGH on the concurrency claim specifically | The GPU-thrashing pitfall was flagged from community reports and one benchmark paper on non-identical hardware (LOW confidence at research time); the on-machine measurement has since confirmed the pitfall's core shape directly on the RTX 3060, so that specific claim is now HIGH confidence, not inferred. |

**Overall confidence:** MEDIUM-HIGH. The load-bearing architectural decision (reuse `kanban_db.py`) rests on direct code inspection, not inference, and the load-bearing throughput assumption has moved from "flagged as unverified by all four researchers" to "measured on the actual target hardware." The remaining gaps below are genuinely open, not glossed over.

### Gaps to Address

- **`OLLAMA_NUM_PARALLEL>1` behavior:** unmeasured; requires a consent-gated Ollama restart that briefly interrupts Bob/Telegram. Handle by making this its own explicit, opt-in step before any roadmap phase relies on a concurrency cap above 2 for generation-bound work — never assume it from the default-configuration measurement.
- **Tool-call/JSON reliability of `qwen3.5:4b` across multi-step chains:** the model's honesty was validated on a single fixed prompt, not chain length; published size-thresholds put reliable tool-calling around 7B. Handle by keeping v1 subtasks single-command and oracle-checked (the shape already proven), and treat any multi-step subtask design as needing its own small benchmark first.
- **Whether the git-worktree-per-task isolation pattern is actually needed for v1:** v1 is scoped to cross-repo chores (one worker per repo, no intra-repo parallel nodes), so Pitfall 8 doesn't bite yet — but the moment task decomposition improves to intra-repo parallelism, this becomes load-bearing and untested. Handle by treating worktree isolation as a hard graph-construction-time rule (reject two concurrent same-repo nodes without it) the first time it becomes relevant, not a runtime hope.
- **Dependency-edge correctness from the planner:** no automated check yet exists for "does this graph's dependency structure actually match reality." Handle during Phase 3 by inspecting planner-written graphs before wiring them to real execution (ARCHITECTURE.md's own build-order guidance).

## Sources

### Primary (HIGH confidence)
- `hermes_cli/kanban_db.py`, `kanban_swarm.py`, `kanban_decompose.py` — read directly from the installed Hermes Agent at `~/AppData/Local/hermes/hermes-agent/hermes_cli/`. Working, running production code implementing this exact pattern.
- `.planning/research/concurrency_results.json` — on-machine measurement, RTX 3060, `qwen3.5:4b`, N=1-4, best-of-2.
- `.planning/PROJECT.md`, `docs/AUTONOMY.md`, `docs/ROUTING.md`, `src/run_queue.py` — this project's own recorded evidence and already-proven substrate.
- [Ollama FAQ — docs.ollama.com/faq](https://docs.ollama.com/faq) — `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS` semantics.
- [Anthropic — Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents) — orchestrator-workers pattern.
- [Kubernetes Leases](https://kubernetes.io/docs/concepts/architecture/leases/), [AWS SQS visibility timeout](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html) — lease/heartbeat pattern cross-validation.

### Secondary (MEDIUM confidence)
- [Why Do Multi-Agent LLM Systems Fail? (MAST taxonomy, arXiv 2503.13657)](https://arxiv.org/abs/2503.13657) — peer-reviewed, 1,600+ annotated traces.
- [Don't Build Multi-Agents — Cognition](https://cognition.com/blog/dont-build-multi-agents); Anthropic's own multi-agent research system account (~15x token cost, duplicate-work incident).
- [LLMs Gaming Verifiers (arXiv 2604.15149)](https://arxiv.org/pdf/2604.15149), [Auditing Reward Hackability (arXiv 2606.16062)](https://arxiv.org/pdf/2606.16062) — vacuous-verify pitfall.
- Framework version/status checks (LangGraph, CrewAI, AutoGen/AG2, smolagents, Letta) — PyPI/GitHub releases, MEDIUM-HIGH confidence per STACK.md's own per-source notes.

### Tertiary (LOW confidence, superseded or flagged for re-verification)
- [I Pointed 8 Agents at One Local LLM](https://jangwook.net/en/blog/en/local-llm-concurrent-requests-num-parallel-experiment/) — M1/16GB, not this project's hardware; superseded for this project by the direct RTX 3060 measurement above, retained only as a directional cross-check.
- STACK.md section 2's "plain JSON per task-graph" recommendation — superseded by ARCHITECTURE.md's direct source verification; retained in this summary only to document the adjudication, not as guidance to follow.

---
*Research completed: 2026-09-02*
*Ready for roadmap: yes*
