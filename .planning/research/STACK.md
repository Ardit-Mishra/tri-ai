# Stack Research: Local-First Parallel Multi-Agent Orchestration

**Domain:** orchestrating several small local models to execute subtasks of one decomposed task,
in parallel, on one Windows GPU box, at $0 marginal cost, for a single operator
**Researched:** 2026-09-02
**Confidence:** MEDIUM-HIGH (framework claims verified against PyPI/official docs/GitHub; the
concurrent-throughput question in §4 is the one area where evidence is thin and is flagged as such)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|---|---|---|---|
| **Nothing new for orchestration** | — | task decomposition + parallel dispatch | See §5 — every surveyed framework either violates an existing project decision (no agent-to-agent messaging) or duplicates verification machinery that already exists and is trusted. The gap is wiring, not a framework. |
| `sqlite3` (Python stdlib) | Python 3.11+ ships it | live task board — claim/in-flight/done/failed state for concurrent workers on one machine | Zero new dependency, WAL mode gives crash-safe concurrent reads with serialized writes, single file is trivially inspectable and Telegram-reportable. See §3. |
| `asyncio` + `httpx.AsyncClient` (or `aiohttp`) | stdlib + httpx `0.28.x` | bounded concurrent dispatch of subtasks to Hermes/Ollama HTTP endpoints | Subtasks are I/O-bound (waiting on an HTTP call to Hermes/Ollama), not CPU-bound — this is precisely asyncio's design case, at near-zero dependency weight. See §1, §5. |
| Plain JSON per task-graph, one file per graph | — | persisted DAG of subtasks + dependencies + verify commands | Matches the existing ledger's git-diffable, human-readable, greppable ethos. Not a "real" workflow engine — see §2 for why that's the right call here. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---|---|---|---|
| `httpx` | 0.28.x | async HTTP client to Ollama's OpenAI-compatible endpoint and Hermes | If you don't already have a preferred async HTTP client. `aiohttp` is an equally valid, slightly heavier alternative — pick whichever the laptop environment already has installed to avoid adding a new dependency for its own sake. |
| Ollama's native Python client (`ollama` pkg) | — | if calling Ollama directly rather than through Hermes for a subtask | Only for subtasks that bypass Hermes entirely (e.g. a raw classification/extraction call) — most subtasks in this system go through `hermes -z`, not raw Ollama. |

### What To Investigate Before Writing New Code

| Item | Why | Notes |
|---|---|---|
| `hermes kanban swarm` | Already inside the trusted dependency (Hermes), described in the project's own docs as "parallel workers → verifier → synthesizer" — this is a description of exactly this milestone's target. The board "exists and is empty." | Not researched here (out of scope per milestone brief — it already exists). But it should be the first thing inspected before any of the above is built, because it may already implement the worker/verify loop this research is scoping a replacement for. |

## Installation

```bash
# Nothing framework-level to install. If httpx isn't already present on the laptop:
pip install httpx==0.28.1

# sqlite3 and asyncio are Python stdlib — no install needed.
```

## Alternatives Considered

### 1. Agent orchestration frameworks

| Framework | Latest (checked 2026-09-02) | Works against local Ollama/OpenAI-compatible endpoint at $0? | Verdict for this project |
|---|---|---|---|
| **LangGraph** | 1.2.11 (Aug 11, 2026) | YES — `langchain-ollama`'s `ChatOllama` points at any local base URL, no API key. MEDIUM-HIGH confidence, multiple 2026 sources confirm this is a common pattern. | **Do not adopt.** LangGraph brings its own graph/state/checkpointer abstraction whose entire job is to persist and resume execution state — which is precisely what the existing ledger + a task-graph JSON file already do, more simply and more inspectably. It also assumes tool-calling-capable models for anything beyond a trivial chain, which stacks a *second* compatibility filter on top of the project's already-documented 64K-context floor (some models that clear the context bar don't reliably emit tool-call JSON). Worth it only if the graph gets genuinely complex (conditional branching, human-in-the-loop interrupts, cyclic replanning) — this milestone's first target (parallel verifiable chores across five repos) is not that. |
| **CrewAI** | 1.14.6 (May 28, 2026) | YES — `LLM(model="ollama/<model>", base_url=...)`, no key required. HIGH confidence, this is CrewAI's documented standard local setup. | **Do not use — hard disqualifier, not a weight judgment.** CrewAI's `Process.hierarchical` gives agents "delegate work to coworker" / "ask question of coworker" tools by default: agents call each other directly. The project's own PROJECT.md rules this out explicitly: *"Direct agent-to-agent messaging — small models negotiating with small models is where these systems reliably produce expensive nonsense."* CrewAI's core value proposition is the exact thing this project decided not to build. (MEDIUM confidence on the delegation-tool specifics — well-documented CrewAI architecture, not independently re-verified against current CrewAI docs this session.) |
| **AutoGen (classic, Microsoft)** | Maintenance mode since Oct 2025, no new features, last major Python release Sep 2024 | YES technically (local model providers supported), but frozen. | **Do not use.** Same objection as CrewAI — `ConversableAgent` is literally agents exchanging messages with each other; that's the framework's defining primitive. Also unmaintained. |
| **AG2** (community fork of AutoGen) | v0.12.2 (May 1, 2026), actively developed, v1.0 path announced | YES | **Do not use.** AG2 explicitly continues "the familiar conversation-based API of the original AutoGen line" — same agent-to-agent objection. Being actively maintained doesn't change the architectural mismatch. |
| **OpenAI Agents SDK** (successor to Swarm) | Provider-agnostic as of April 2026 update ("100+ third-party LLMs"), Swarm itself now explicitly a "reference design," not for production | YES — point `OpenAIChatCompletionsModel` at `http://localhost:11434/v1/` with a placeholder API key. HIGH confidence, this is Ollama's own documented OpenAI-compatibility surface. | **Do not adopt.** Lighter violation than CrewAI/AutoGen — its unit is a "handoff" (one agent explicitly transfers control to another) rather than open negotiation — but it still routes coordination through agent-to-agent handoff rather than the shared board, and it carries tracing/guardrails/sessions infrastructure this system has no use for. Its value proposition (production hardening around handoffs) doesn't map onto "N stateless workers claim rows off one board." |
| **Letta** (formerly MemGPT) | Actively developed through 2026, supports Ollama and vLLM backends, has a paid tier (Pro $20/mo) alongside a free/local tier | YES for the free/local/BYOK path | **Do not use.** Letta's core contribution is long-term, cross-session agent memory — a problem this system does not have (task state is explicitly meant to live in a persisted graph on disk, not in an agent's memory). Letta also runs its own agent server with its own backing store, which is parallel infrastructure to the SQLite board and ledger this project already trusts, not a replacement for either. |
| **smolagents** (Hugging Face) | 1.26.0 (May 29, 2026) | YES — model-agnostic, well-documented Ollama integration, no API key | **Do not use.** It's a single/manager-agent "writes and executes code" pattern — a different worker abstraction than the one this system already has (Hermes as the code-executing worker). Adding it would mean running two different code-execution agents side by side for no gain; Hermes already fills the role smolagents would fill. |
| **Ray** | actively maintained, has a `ray.util.multiprocessing.Pool` shim for single-machine use | YES trivially — Ray doesn't care what HTTP endpoint your task functions call | **Do not use.** Ray is built to scale a compute graph across many machines; on one box it brings a GCS (global control store), a dashboard, an object store and its own serialization layer along for the ride. For dispatching <10 short HTTP calls to Ollama, this is the textbook case where a dependency's weight is disproportionate to the problem — Ray's own community guidance (found in this research) is to reach for `loky` or plain `multiprocessing` for single-machine tasks and reserve Ray for multi-machine scale. This system explicitly stays on one desktop for inference. |
| **Prefect** | actively developed, Python-native, self-hosted open source is genuinely free | YES — flows are just Python functions, can call Ollama/Hermes directly | **Do not adopt for this milestone.** Prefect is a real scheduler with retries, a UI, and — if self-hosted — a server process (`prefect server start`) plus its own metadata database to keep alive. This is a second durability layer sitting next to Windows Task Scheduler, which this project already uses successfully and has documented reasons to trust (survives laptop disconnects, no SSH-session-death problem). Prefect's sweet spot is 20-50 *recurring* flows; this milestone is ad hoc decomposition of one assigned task a few times a day. If the swarm concept later grows into genuinely recurring, multi-step data pipelines, revisit — not now. |
| **Dagster** | Dagster+ (hosted) removed included credits from Solo/Starter plans as of May 2026, now $0.035-0.040/asset-materialization with no free allowance; self-hosted Dagster core remains open source | Self-hosted: YES at $0. Hosted: NO — violates the $0 constraint outright. | **Do not use.** Even setting the hosted-tier pricing change aside, Dagster's software-defined-asset model (`@asset`, IO managers) is built for typed data flowing between steps in a pipeline — a mismatch for heterogeneous shell-verify chores (lint, format, dep bump) that don't produce a meaningful "asset." Self-hosting still means a webserver + daemon + backing DB to operate, for no fit gained over Prefect's simpler model, which itself isn't recommended either. |
| **Plain `asyncio` / `multiprocessing`** (stdlib) | — | YES, trivially, no dependency at all | **Recommended.** See Core Technologies above. This is the correct concurrency primitive for I/O-bound HTTP calls to local model servers, costs nothing, and matches the existing runner's "one bespoke ~200-line file" philosophy instead of fighting it. |

### 2. Task-graph representation

**Recommendation: plain JSON, one file per task-graph, mirroring the existing `ledger.jsonl` pattern.**

- **What people actually use for this scale:** for a solo operator's ad hoc DAGs (a handful of nodes,
  created a few times a day, not a recurring production pipeline), the field consistently converges
  on flat files — JSON or JSONL — not a workflow engine's internal database. Workflow engines
  (Airflow/Prefect/Dagster) exist to solve *recurring, scheduled, observable-at-scale* pipelines with
  many stakeholders; a DAG that one person's premium model writes once and several free local
  workers consume once does not need a scheduler's metadata store.
- **Why not a real workflow engine:** every one surveyed (Prefect, Dagster) requires a live server
  process to get its UI/observability/retry benefits — which then becomes new infrastructure this
  one-operator system has to keep alive, alongside Windows Task Scheduler, Tailscale, Ollama, and
  OmniRoute. The project already treats "another daemon that can silently die" as a real cost — see
  its own documented lesson about the OmniRoute `/health` 404 that looked like an outage. Don't
  create a second version of that risk for the task graph.
- **Why not raw SQLite for the graph definition itself:** SQLite is the right tool for *live,
  mutating, concurrently-claimed state* (§3) but a worse tool for *the graph's static definition* —
  a human (or Bob, via Telegram) benefits from being able to `cat`/`git diff`/eyeball a task graph
  the way the existing ledger already is. A JSON file per graph (`tasks/<task-id>/graph.json`, one
  node per subtask: `id`, `deps`, `prompt`, `verify`, `status`, timestamps) keeps that property.
  Mutating per-node `status` fields in that same file works fine at this concurrency scale (single
  digit workers, single machine) but if that starts to race, the fix is not "add a framework," it's
  "move the *status* column into the SQLite board and leave the *graph shape* in JSON" — i.e. split
  static definition from live state rather than reaching for a heavier persistence layer.
- **Confidence:** MEDIUM. This is a synthesis of general practitioner convention (flat-file DAGs for
  small/personal automation) rather than one single authoritative source; the project's own existing
  ledger pattern is the stronger piece of evidence and was already proven at "9/9 across 6 repos."

### 3. Shared-state / task-board substrate

**Recommendation: SQLite in WAL mode, one `.db` file on local disk, as the live claim/status board —
additive to the existing `ledger.jsonl`, not a replacement for it.**

| Option | Verdict | Reasoning |
|---|---|---|
| **SQLite (WAL mode)** | **Recommended** | Single file, no server process, ACID, readers don't block the writer in WAL mode. Confirmed via research: SQLite still enforces one writer at a time even in WAL mode (`SQLITE_BUSY` if two writers collide) — so the safe pattern is either (a) a single dedicated writer thread that serializes claim/status updates, or (b) short retrying transactions with `busy_timeout` set, which is standard practice and cheap to implement. Crash safety is real: WAL mode guarantees only an uncommitted transaction is lost on a crash, not database integrity — this matters directly for this system given SSH-launched processes are documented to die mid-flight. A claim is a single `UPDATE tasks SET status='claimed', worker=?, claimed_at=? WHERE id=? AND status='pending'` — the affected-row-count is the atomic compare-and-swap. A stale claim (worker died) is recovered with a lease/heartbeat timestamp column and a sweep, the same shape as the existing ledger's revert-on-failure logic, just one level earlier. |
| **Directory of claim files (atomic rename)** | Viable but strictly worse here | `os.replace()` is atomic on Windows since Python 3.3+ (confirmed), so a Maildir-style "rename to claim" pattern is dependency-free and correct. But answering "what's claimed/in-flight/done/failed right now" — an explicit requirement (*"observable and controllable from Telegram"*) — means scanning a directory and reconstructing state from filenames/mtimes, which is exactly the query SQLite answers with one `SELECT`. You'd end up hand-rolling an index on top of the directory, at which point you've built a worse SQLite. |
| **Redis** | Reject | Requires a running server process on Windows (native Windows support is community-maintained/WSL-dependent, not first-party) — a new daemon to keep alive, on a system that has already been burned once by an invisible dead dependency (OmniRoute's `/health` 404 looking like "down" when it wasn't). Redis's actual advantage — throughput at high concurrency across machines — isn't needed: this is one machine, single-digit concurrent workers, low request volume. Pure overhead here. |
| **Plain file locks (`msvcrt.locking` / a `.lock` file)** | Reject as the primary mechanism | Works for mutual exclusion but gives you none of SQLite's query surface (status board, history, per-node metadata) — you'd be building a worse, bespoke SQLite by hand. Fine as a belt-and-suspenders guard around the one dedicated writer thread if you want extra insurance, not as the board itself. |

**Confidence:** MEDIUM-HIGH on the SQLite WAL mechanics (multiple corroborating sources, including a
direct, well-known SQLite semantics fact — single-writer-at-a-time even in WAL — that is easy to get
wrong). LOW-to-unverified on Windows-filesystem-specific WAL edge cases specifically — the research
did not surface Windows-specific caveats beyond the general "don't put the DB file on a network
share" rule, which does not apply here since everything runs on local disk on the desktop.

### 4. Concurrency for local inference (Ollama on the RTX 3060, 12 GB)

**Verified against Ollama's official FAQ (docs.ollama.com/faq), HIGH confidence:**

- `OLLAMA_NUM_PARALLEL` — max parallel requests handled **per loaded model**. Default **1**.
  Increasing it scales required memory by `OLLAMA_NUM_PARALLEL × context length`, because *each*
  parallel slot gets its own KV cache. This composes badly with a constraint this project already
  has: Hermes requires ≥64K context. Running even 2 parallel slots against a 64K-context model
  roughly doubles the KV-cache VRAM cost of an already-large context window — likely to eat most of
  the remaining headroom on a 12 GB card before generation even starts. **Do not assume
  `OLLAMA_NUM_PARALLEL` is a cheap lever here; the context floor makes it an expensive one.**
- `OLLAMA_MAX_LOADED_MODELS` — max models resident at once, default `3 × GPU count` (so 3 on this
  single-GPU desktop) *provided they all fit in available VRAM simultaneously*. For GPU inference, a
  new model must fit **entirely** in whatever VRAM is left, or it doesn't get a concurrent GPU slot.
- **What this means concretely on this hardware:** Bob's resident chat model (`qwen3.5:4b`, ~3.4 GB)
  already occupies part of the 12 GB. That leaves roughly 8-9 GB of headroom — enough for maybe one
  or two more small (3-4 GB, Q4) models to be co-resident before CPU offload risk starts, not
  "several." This is a hard ceiling, not a tuning problem.

**Not verified by official docs — flagged explicitly as the weakest part of this research:**

- Ollama's own FAQ does **not** state whether several small models running concurrently
  out-throughput one larger model run serially, or vice versa. This is a real gap, not a smoothed-over
  one.
- Community reports (LOW confidence — forum/blog sources, not independently reproduced here) describe
  **non-linear, sometimes degraded** scaling when running multiple concurrent Ollama workloads on one
  consumer GPU — e.g. reports of uneven GPU utilization across cards, and one report of 3 concurrent
  Ollama instances being slower in aggregate than 1. A benchmarking paper comparing Ollama and vLLM
  under concurrency (MEDIUM confidence — appears to be a peer-reviewed benchmark, not independently
  re-run here) found Ollama's throughput fell ~40% at just 4 concurrent requests, versus vLLM's ~15%
  drop, attributed to vLLM's continuous batching versus Ollama's simpler request handling — i.e.
  **Ollama specifically (not GPU inference in general) is not built to scale gracefully under
  concurrency**, because it lacks the batching machinery that makes concurrent GPU inference actually
  pay off.
- **Practical read for this project, stated as a hypothesis to test, not a fact:** whether concurrent
  small workers beat serial execution likely depends on whether the subtask is *latency-bound* (short
  prompt, short output, dominated by per-call overhead — e.g. `git status`, lint, format, the
  project's own stated first target of "verifiable chores") versus *generation-bound* (long output,
  GPU stays busy the whole call). For short, mechanical, verify-gated chores — exactly this
  milestone's stated first target — the GPU is plausibly idle between tokens often enough that modest
  concurrency (2 workers, not N) helps more than it hurts. For anything closer to a large rewrite,
  serial execution on the single resident model is the safer default.
- **This is squarely the kind of claim the project's own methodology says not to trust on reputation.**
  The model-selection experiment in ROUTING.md/README.md is explicit: score candidates by
  measurement, not parameter count or benchmark. The same discipline applies here — before
  committing to a worker count, run the same short verify-gated chore (e.g. `git status` piped
  through a subtask prompt) at concurrency 1, 2, and 3 against the desktop and record wall-clock time
  and `nvidia-smi` memory, exactly as the fallback chain was tested deliberately rather than assumed
  to work. **Start the first parallel-chores milestone at a hardcoded concurrency of 2** (the level
  the VRAM math above supports without guessing), and treat any higher number as something to earn
  with a measurement, not assume from a framework default.

## What NOT to Use

| Avoid | Why | Use Instead |
|---|---|---|
| CrewAI | Core feature is agents delegating to and messaging each other — the exact pattern PROJECT.md rules out as producing "expensive nonsense." | Board-mediated coordination via SQLite (§3) — a worker claims a row, never talks to another worker. |
| AutoGen (classic) or AG2 | Both are built around `ConversableAgent`-style direct message exchange between agents; AutoGen classic is additionally frozen (maintenance mode, no new features since late 2025). | Same as above. |
| OpenAI Agents SDK / Swarm | Coordination unit is an agent-to-agent "handoff"; brings tracing/guardrails/session infrastructure irrelevant to a stateless-worker-claims-a-board model; Swarm itself is now explicitly a non-production reference design. | Same as above. |
| LangGraph | Duplicates the verify-gated persistence this system already trusts, with its own graph/checkpointer state machine; assumes tool-calling-capable local models, adding a second compatibility filter beyond the existing 64K-context floor. | The existing ledger pattern + a plain JSON task-graph file (§2). |
| Letta | Solves cross-session agent memory, a problem this system doesn't have (task state belongs in a persisted graph, not an agent's memory); runs its own agent server as parallel infrastructure. | The task-graph JSON + SQLite board already covers "what does the system remember about this task." |
| smolagents | A single/manager-agent code-executing pattern that duplicates the role Hermes already fills as the worker. | Hermes one-shot (`hermes -z`), already proven. |
| Ray | Built to scale compute across machines; brings a dashboard, GCS, and object store for a single-box, single-digit-worker workload. | `asyncio` + a bounded semaphore. |
| Prefect | A real scheduler with a server process and its own metadata DB — a second durability layer next to the Windows Task Scheduler this project already trusts and has verified survives disconnection. | Windows Task Scheduler (already proven) + the SQLite board for live state. |
| Dagster | Hosted tier now charges per-asset-materialization with no free allowance as of May 2026 (violates $0 outright if used hosted); self-hosted core still requires a webserver+daemon and models work as typed "assets," a poor fit for heterogeneous shell-verify chores. | Same as Prefect's replacement — Task Scheduler + SQLite board. |
| Redis | Requires a running server daemon on Windows with no first-party native support — one more thing that can silently die, for throughput this system's scale doesn't need. | SQLite in WAL mode. |
| `OLLAMA_NUM_PARALLEL` raised aggressively without measurement | Each parallel slot multiplies KV-cache cost by the full context length; combined with the existing 64K-context floor for Hermes-compatible models, this can consume most of the 12 GB ceiling per additional slot. | Measure wall-clock and `nvidia-smi` VRAM at concurrency 1/2/3 before setting it above the default; start any first implementation at a hardcoded worker cap of 2. |

## Stack Patterns by Variant

**If the swarm milestone stays at "a handful of short, mechanical, verify-gated chores per repo,
run a few times a day":**
- Use `asyncio` + `httpx` + SQLite board + JSON task-graph, exactly as recommended above.
- Because the workload is small, ad hoc, and I/O-bound — every heavier option surveyed is scoped for
  a problem this milestone doesn't have (recurring pipelines, cross-machine scale, or agent
  negotiation).

**If subtasks later grow into long, generation-heavy rewrites (not the current first target):**
- Prefer serial execution on the single resident model over concurrent small-model dispatch for
  those specific subtask types, per the throughput findings in §4 — concurrency likely helps short
  chores and likely hurts long-generation ones on this hardware.
- Re-measure before trusting either assumption; this is exactly the kind of claim the project's own
  methodology says to verify empirically rather than infer from framework defaults.

**If `hermes kanban swarm` turns out to already implement the claimed worker/verifier/synthesizer
loop:**
- Prefer wiring that over any of the above — it is inside an already-trusted dependency, and the
  milestone brief itself flags it as "unwired," not "untested" or "insufficient." This is out of
  scope for this research file (see milestone context) but should be the first thing checked before
  building the SQLite board or asyncio dispatcher described above.

## Version Compatibility

| Package A | Compatible With | Notes |
|---|---|---|
| `httpx` 0.28.x | Python 3.11+ | Standard async client; no known conflicts with Hermes or Ollama's HTTP surface (both are plain REST/OpenAI-compatible JSON over HTTP). |
| Ollama v0.33.2 (Aug 27, 2026) | OpenAI-compatible endpoint at `/v1/`, native endpoint at `/api/` | Confirm `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_LOADED_MODELS` behavior against whatever version is actually running on the desktop (`ollama --version`) before relying on the defaults stated in §4 — these have changed across Ollama's release history and this research reflects the current (2026-09-02) official FAQ, not necessarily the exact desktop-installed version's behavior. |

## Sources

- `/ollama/ollama` (Context7) — resolved but not queried this session; official FAQ fetched directly instead (below), sufficient for the concurrency questions asked.
- [Ollama FAQ — docs.ollama.com/faq](https://docs.ollama.com/faq) — `OLLAMA_NUM_PARALLEL`, `OLLAMA_MAX_LOADED_MODELS`, VRAM/context-scaling semantics. HIGH confidence, official docs, fetched 2026-09-02.
- [LangGraph PyPI](https://pypi.org/project/langgraph/) — version 1.2.11, Aug 11, 2026. HIGH confidence.
- [CrewAI PyPI / changelog](https://docs.crewai.com/en/changelog) — version 1.14.6, May 28, 2026. MEDIUM confidence (fetched via search summary, not direct page load).
- [smolagents PyPI](https://pypi.org/project/smolagents/) — version 1.26.0, May 29, 2026. HIGH confidence.
- [Ollama GitHub releases](https://github.com/ollama/ollama/releases) — v0.33.2, Aug 27, 2026. HIGH confidence.
- AG2/AutoGen status — multiple 2026 sources (AI DEV DAY, AgentMarketCap, Blck Alpaca) converging on: AutoGen classic in maintenance mode since Oct 2025; AG2 is the actively-maintained community fork (v0.12.2, May 2026); Microsoft's own consolidated path is Microsoft Agent Framework 1.0 (GA April 2026). MEDIUM confidence — multiple independent sources agree, none is Microsoft's own primary announcement page directly fetched.
- OpenAI Agents SDK / Swarm status — openai.github.io/openai-agents-python (existence confirmed) plus multiple 2026 secondary sources describing Swarm as now a "reference design" and the Agents SDK as the supported, provider-agnostic production path. MEDIUM confidence.
- Letta status — letta.com blog/docs, 2026 pricing and local/Ollama-backend support. MEDIUM-HIGH confidence (fetched from Letta's own site via search summaries).
- Dagster+ pricing change (May 2026, removed included credits from Solo/Starter) — single-source (secondary comparison article), not independently verified against Dagster's own pricing page. LOW-MEDIUM confidence on the specific pricing figures; flagged accordingly in the table above.
- SQLite WAL mode / single-writer semantics / crash safety — multiple corroborating sources (SkyPilot engineering blog, tenthousandmeters.com, general SQLite documentation consensus). HIGH confidence — this is well-established, frequently-documented SQLite behavior, not a fringe claim.
- `os.replace()` atomicity on Windows since Python 3.3 — Python standard library documentation convention, corroborated by multiple secondary sources. HIGH confidence.
- Multi-concurrent-Ollama throughput degradation — community reports (Reddit r/LocalLLaMA references, secondary blogs) plus one benchmarking-paper reference (`doi.org/10.3390/app16115435`) comparing Ollama vs vLLM under concurrency. LOW (community reports) to MEDIUM (benchmark paper) confidence — explicitly flagged in §4 as the weakest-evidenced finding in this document; recommend empirical validation on the actual desktop before depending on any specific concurrency number.

---
*Stack research for: local-first parallel multi-agent orchestration, Tri-AI Swarm milestone*
*Researched: 2026-09-02*
