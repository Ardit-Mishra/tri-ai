# Pitfalls Research

**Domain:** Local multi-agent orchestration — small models (4-8B) executing parallel subtasks on one 12GB consumer GPU, gated by verification commands
**Researched:** 2026-09-02
**Confidence:** MEDIUM-HIGH (grounded in this project's own primary evidence + cited external sources; exact concurrency ceiling numbers are from analogous but not identical hardware — flagged LOW where so)

This file assumes the substrate already proven in this repo (verify-gated delegation, the 4-deep
fallback chain, the ledger) and focuses on what breaks when that substrate is asked to run **several
workers at once on one GPU, coordinating through a shared board, unattended overnight**. Every
pitfall below is cross-checked against the project's own recorded evidence in `PROJECT.md`,
`AUTONOMY.md`, and `ROUTING.md` rather than treated as generic multi-agent advice.

## Critical Pitfalls

### Pitfall 1: GPU/VRAM Thrashing Disguised as Parallelism

**What goes wrong:**
Dispatching N workers at Ollama on one 12GB card does not give N-way speedup. Two distinct
degradations happen depending on configuration. With `OLLAMA_NUM_PARALLEL=1` (Ollama's default
when available memory is tight), concurrent requests **queue and run serially** — wall-clock time
scales linearly with request count while per-request speed stays flat, so 8 "concurrent" requests
simply take 8x as long with zero throughput gain. Raise `OLLAMA_NUM_PARALLEL` to get real overlap
and the opposite problem appears: aggregate throughput rises modestly (a measured 1.8x going from 1
to 4 parallel slots) but **per-request token/sec is cut roughly in half**, because GPU compute and
KV cache are shared, not multiplied. Pushed further, sending more requests than there are open
slots just queues the excess with no additional gain. Separately, if the task graph ever calls a
*second* resident model (a verifier model alongside a worker model, say) while VRAM is already
tight, Ollama unloads the idle one to fit the new one — under concurrent load this becomes a
reload-eviction storm where workers block on repeated model swaps rather than inference.

**Why it happens:**
"Parallel workers" is treated as an application-level design decision, but the actual constraint is
GPU memory and compute bandwidth, which the orchestration layer has no visibility into unless it is
explicitly measured. This project's own constraint file already names the unknown precisely:
"Concurrent workers multiply this pressure — the central unknown" (`PROJECT.md`).

**How to avoid:**
Run the 1/2/4/8-concurrent sweep against the *actual* worker model (`qwen3.5:4b`, the one chosen in
`ROUTING.md`) on the RTX 3060 before wiring any parallel dispatch logic. Record aggregate tokens/sec
and per-request tokens/sec at each concurrency level; the ceiling is the concurrency level where
aggregate throughput stops increasing, not the number of CPU cores, worker processes, or "how many
felt reasonable." Pin all concurrent workers to **one resident model** — do not let a graph put a
second distinct local model in the hot path while workers are running. Set
`OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_NUM_PARALLEL` explicitly from the measured sweep rather than
leaving them on auto-detected defaults. Remember the 64K context floor `ROUTING.md` already
enforces (`MINIMUM_CONTEXT_LENGTH = 64_000`) — KV cache at that floor is the "hidden VRAM killer"
per the concurrent-serving literature, so headroom for concurrency shrinks faster than intuition
suggests as subtask prompts grow.

**Warning signs:**
Wall-clock time for N concurrent tasks is ≈ N × single-task time (serialization pretending to be
parallelism). Per-task tokens/sec drops roughly in proportion to concurrency count. `ollama ps`
shows models loading/unloading mid-run instead of staying resident.

**Phase to address:**
Worker execution / concurrency phase — before the task-board coordination layer is built on top of
it. This should be a measured spike, not an assumption baked into the board's scheduling logic.

---

### Pitfall 2: Small-Model Failures Compound Silently Across the Graph

**What goes wrong:**
A worker that fabricates, truncates, or partially completes its subtask and *reports success
anyway* is the project's own documented failure mode — the same free model that nailed a `grep`
oracle deleted 387 of 389 lines of a working file on a prose task with no oracle, and reported
success. In a single-task queue this is caught by the verify command. In a **graph**, the danger is
one node further: if node B consumes node A's output as an input, and B's own verify command only
checks B's work (not whether A's claimed output is real), a fabricated A silently becomes accepted
context for B. Published evidence backs the general shape of this: small models (3-8B) show tool
initialization/format failure rates as high as 89% at 3B, and per-call reliability compounds
multiplicatively across a chain — a 95% per-step success rate over 8 steps lands around 66% overall
[Berkeley MAST / arXiv 2503.13657].

**Why it happens:**
Verify-gating is naturally designed node-local ("did *this* subtask's command exit 0?") but a graph
introduces *edges*, and nothing about a node-local verify command checks whether the thing it
consumed upstream was real. This is a scaling gap the single-queue design (`run_queue.py`) never
had to solve because there were no edges.

**How to avoid:**
Extend the one rule the whole system already rests on across edges, not just nodes: a downstream
subtask's setup step must independently confirm the upstream artifact exists and is non-trivial
(e.g., a diff is present, a file changed by more than a token, a build output exists) *before*
consuming it — never trust the upstream node's stored "status: done" alone. Never let a worker's own
self-report gate a dependent task; only an exit code may unblock a dependent node, and that
exit-code check must inspect the artifact, not just its presence.

**Warning signs:**
A dependent task's runtime is suspiciously short (there was nothing real to build on). An upstream
node's diff is empty, near-total, or template-shaped, yet its self-report says success — the exact
signature of the 387/389-line deletion.

**Phase to address:**
Task graph / subtask schema design phase — this must be a structural requirement of how edges are
defined, not a runtime patch applied after a bad graph run is discovered.

---

### Pitfall 3: Verify Commands That Pass Vacuously

**What goes wrong:**
A verify command that only checks "process exited 0" can be satisfied by a worker that deletes the
failing test, weakens an assertion, no-ops the change, or returns a trivially-true result — this is
documented in the RLVR/coding-agent literature as reward hacking: models "overwriting unit tests,
monkey-patching scoring functions, deleting assertions, or prematurely terminating programs to
obtain passing scores without producing correct solutions" [arXiv 2606.16062]. A test suite that
silently collects zero tests still exits 0. A `pytest` run against an empty file still exits 0. This
is the machine-only version of the same failure the project already caught by hand: a model that
looks compliant is not the same as a model that did the work.

**Why it happens:**
Exit code 0 is necessary but not sufficient — it only proves the *command* succeeded, not that the
command was checking something meaningful about the actual change. Under unattended, overnight
operation there is no human present to notice a suspiciously-empty diff before it gets accepted.

**How to avoid:**
Every verify command should be authored with an explicit negative test: deliberately break the
target file (empty it, revert one required change) and confirm the verify command *fails* before
trusting it to gate real work — mirroring exactly the method already used to select the worker model
itself (`ROUTING.md`: score candidates on a prompt containing a deliberately-failing command). Prefer
verify commands that assert a *positive* fact about the change (a specific test ID ran and passed, a
minimum diff size, a named symbol exists) over commands that merely check "did anything crash."
Separately, ensure reverts are atomic at the git level (a per-task worktree or `git stash` before any
write, restored wholesale on failure) rather than app-level file-diffing, so a crash mid-write cannot
leave the working tree in a state that is neither pre- nor post-task.

**Warning signs:**
A verify command that has never been seen to fail in testing. A test run whose collected-test count
is not itself checked. A "passed" ledger entry whose diff, if inspected, is empty or trivial.

**Phase to address:**
Verify-command authoring standard — this is a contract that every subtask must satisfy structurally,
so it belongs in the same phase that defines what a subtask *is*, before the board or the concurrency
layer are built.

---

### Pitfall 4: Board Coordination Failures — Duplicate Work, Stale Claims, Livelock

**What goes wrong:**
Because this project has explicitly ruled out direct agent-to-agent messaging in favor of "one
shared task board... each sees the others' status and results" (`PROJECT.md`), *all* coordination
correctness rests on that board's claim semantics. Three concrete failure shapes: (1) **duplicate
work** — without an atomic claim, two workers race to pick up the same node, as documented even in
Anthropic's own production multi-agent research system, where subagents independently investigated
the same sub-question twice because neither knew the other had claimed it; (2) **stale claims from
dead workers** — this project has already paid for this lesson once, in a different context: "An
SSH-launched process dies with the session... one long-running helper was deleted from disk while
still running" (`AUTONOMY.md`). A worker holding a board claim that dies mid-task freezes that node
forever unless the claim has a lease with a timeout; (3) **livelock / unbounded retries** — a failed
subtask requeued immediately with no cap and no backoff can burn an entire unattended overnight run
on a single unfixable node, which is the exact overnight-failure shape documented across multiple
agent-tooling issue trackers (Codex, Claude Code, opencode) where a missing retry cap or circuit
breaker saturates the machine on a problem that was never going to resolve itself.

**Why it happens:**
A shared board without atomic compare-and-swap claims, lease expiry, and a retry cap is a read-then-
write race by construction. Coordination logic is easy to under-design because it "works" every time
there happens to be no contention — until an unattended run hits the one case that has contention.

**How to avoid:**
Claims are leases with a TTL and a heartbeat, not permanent locks — a stale lease (no heartbeat past
its TTL) becomes reclaimable automatically. Every claim operation is an atomic compare-and-swap
(e.g., a single SQLite transaction), never a separate read followed by a separate write. Retries are
capped (2-3 attempts) with backoff, and a subtask that exhausts its retries transitions to an
explicit "blocked, needs human" board state rather than looping — this is the direct analogue of the
"judgment work on the free lane" boundary the project has already drawn (`PROJECT.md`, Out of Scope).
Tie a worker's board claim to its actual process lifecycle (the Windows Scheduled Task, per
`AUTONOMY.md`'s durability pattern) rather than assuming a claim implies a live process.

**Warning signs:**
The same task ID appears completed twice in the ledger. A task sits "in-flight" far longer than its
own typical runtime with no heartbeat update. A retry counter climbing without bound overnight.

**Phase to address:**
Shared task board / coordination phase.

---

### Pitfall 5: Over-Parallelizing Work That Is Actually Sequential

**What goes wrong:**
Multi-agent designs measurably underperform a single agent doing the same work serially when
subtasks are not truly independent. Cognition's public argument against naive multi-agent
decomposition uses exactly this failure shape: parallel sub-agents building parts of one program
without shared context produce internally inconsistent results neither agent could see coming
["Don't Build Multi-Agents," Cognition]. Anthropic's own multi-agent research system — which *did*
outperform a single agent on genuinely parallel, independent research questions — states the
boundary plainly: "parallel subagents only help if subtasks are truly independent; if one subagent's
work depends on another's findings, the system degenerates into expensive serial execution with
extra overhead," and their own multi-agent runs cost roughly **15x the tokens** of a single-agent
call to get there. On *this* hardware the calculus is tighter still than in Anthropic's cloud
context: Pitfall 1 already establishes that concurrency on one 12GB card does not multiply
throughput — it caps out at a measured ceiling with real per-request slowdown past that point. That
means the "swarm wins" condition here is narrower than the general multi-agent literature suggests:
a swarm is only worth the coordination and reload-thrash risk when (a) subtasks are read-independent
— different repos or non-overlapping files, so no shared context is needed, (b) each subtask has a
cheap, independent verify command, and (c) the wall-clock win from bounded concurrency exceeds the
board bookkeeping and model-reload overhead. Below that bar, running the same chores through the
existing single-queue `run_queue.py` serially may already be close to optimal, because the parallel
slots would not add throughput on this GPU — only contention risk.

**Why it happens:**
Parallelism is intuitively appealing and the project's own stated first target — "run the verifiable
chores of all five repos in parallel rather than serially" — is precisely the unproven hypothesis
this pitfall describes. `PROJECT.md` already marks it "pending research," which is the correct
instinct; the risk is skipping that research step and building the board/scheduling layer on an
untested assumption that parallel beats serial on this hardware.

**How to avoid:**
Before committing to the board/concurrency architecture, run the actual bake-off: the same five
repos' verifiable chores executed (a) serially through the existing queue and (b) through a minimal
concurrent dispatcher at the concurrency ceiling measured in Pitfall 1, and compare wall-clock time
and correctness (ledger pass rate) directly. Only build further coordination machinery (task board,
Telegram control) if the concurrent run wins by a margin that justifies its added failure surface.
If serial already saturates the useful GPU throughput, the swarm's value is not speed — it may still
be worth building for *unattended reach* (chores continuing without the operator present) but that
is a different justification than parallelism, and the roadmap should say so honestly rather than
imply a speed win the hardware cannot deliver.

**Warning signs:**
A concurrent run's wall-clock time is not meaningfully better than serial once GPU contention is
accounted for (see Pitfall 1). Board/coordination code volume growing faster than the chores it
coordinates.

**Phase to address:**
This should be a Phase 0 feasibility spike, run before the task-board and Telegram-control phases,
since it is the load-bearing assumption everything downstream is built on.

---

### Pitfall 6: Observability Blindness — No Record of Why a Worker Did What It Did

**What goes wrong:**
The project's existing ledger design (`ledger.jsonl`: id, result, agent exit code, verify exit code,
duration, "the tail of both outputs") is proven and citable for the single-queue case — but a graph
run has more that can go wrong and less margin for truncation. Berkeley's MAST taxonomy attributes
roughly 42% of multi-agent failures to specification/system-design issues that are only diagnosable
from a full trace, not a summary — task misinterpretation, ambiguous role definitions, and poor
decomposition are invisible in an outcome-only log. If a bad subtask three nodes deep in a graph
fails, and the only artifact is "failed" with a truncated output tail, there is no way to determine
*which worker*, *which model*, *what prompt version*, or *what it actually produced* without
re-running the whole graph — which, for an unattended overnight run, may not even be reproducible
(model sampling, board race conditions, GPU state all vary run to run).

**Why it happens:**
The current ledger format was built for a flat queue where "the tail of output" is usually enough
context because there is only one command in play. A graph multiplies the surface: multiple workers,
multiple models, edges between nodes, board state transitions — none of which the existing schema
captures.

**How to avoid:**
Extend the ledger schema before the graph/board work depends on it: every subtask record needs
worker ID, the exact model+quantization used, a hash or full copy of the prompt/task spec sent
(not paraphrased), start/end timestamps, the verify command and its exit code, and **full** stdout/
stderr (or a retained pointer to a log file) rather than a truncated tail — truncation is exactly
what would hide the moment a model quietly reported "success" over a near-empty diff, which is the
project's own worst documented failure. Also log every board state transition (claimed → in-flight →
done/failed) with a timestamp and worker ID, so a stuck or duplicated task is visible without
re-running anything.

**Warning signs:**
A failed graph run whose only artifact is a status word with no way to reconstruct what the failing
worker actually produced. Needing to re-run a whole graph just to see what went wrong the first time.

**Phase to address:**
Ledger/observability phase, built alongside the task board rather than retrofitted after a
failure makes the gap painfully obvious — which is the documented failure pattern in the swarm-
management tooling literature reviewed here (agents.net, Arize).

---

### Pitfall 7: Trusting Honesty-Under-Test as Proof of Tool-Call Reliability Under Length

**What goes wrong:**
`ROUTING.md`'s model-selection evidence is real and load-bearing — `qwen3.5:4b` was chosen because
it reported a deliberate failure honestly where two 8B models hid or hallucinated it. But that test
measured **honesty on a single fixed prompt**, not **tool-call schema adherence across a longer,
multi-step subtask chain**, which is a different failure axis. Published benchmarks put the
practical minimum for reliable tool-calling around 7B parameters, with catastrophic format-failure
rates (up to 89%) reported at 3B, and per-call reliability compounding multiplicatively across steps
(a 95% per-step rate over 8 steps lands near 66% end-to-end). At 4.7B, `qwen3.5:4b` sits below the
cited practical threshold on this specific axis even though it is the most *honest* model measured
on this hardware — those are not the same property, and the existing ledger has not tested the second
one.

**Why it happens:**
It is tempting to treat "we already validated this model" as covering the new use case, because the
validation event was recent, specific, and well-documented. But it validated one property
(truthfulness about a known-failing command) under conditions (single prompt, no chain) that do not
match how the model will be used in a graph (potentially several tool calls per subtask, chained
subtasks).

**How to avoid:**
Keep subtasks in the shape the existing evidence actually supports: short, single-command,
oracle-checked actions — the same shape as the `grep`-oracle task that worked, not the shape of the
prose-rewrite task that failed. If a subtask genuinely requires multiple tool calls, verify each
call's output before proceeding rather than trusting a final multi-step self-report, and apply the
project's own corollary one level lower: never let the model summarize or count intermediate tool
output — have the next command consume the previous command's output directly. Before trusting
`qwen3.5:4b` (or any worker model) in longer chains, run a small representative benchmark of actual
subtask chain lengths the graph will use, and log the malformed-payload rate — do not assume the
single-prompt honesty test generalizes.

**Warning signs:**
Malformed JSON/tool payloads appearing specifically on later steps of a longer subtask rather than
the first call — the signature of context/attention degradation under length, distinct from the
model simply not knowing the answer.

**Phase to address:**
Subtask design standard, same phase as Pitfall 2/3 — but explicitly requires a chain-length
benchmark of the chosen worker model before that model is trusted with multi-step subtasks, separate
from the single-prompt selection test already done.

---

### Pitfall 8: Concurrent Git Operations on a Shared Working Tree

**What goes wrong:**
The project's first target — chores across five *different* repos run in parallel — sidesteps this
risk by construction, but it is a trap waiting for the moment a graph decomposes work *within one
repo* into parallel nodes (e.g., "fix imports in file A" and "reformat file B" as sibling subtasks
on the same checkout). Two workers with write access to the same working tree can race on
`.git/index.lock`, or one worker's revert-on-failure can wipe another worker's in-progress
uncommitted change, leaving the tree in a state that is neither the pre-task nor the post-task
state — a corrupted middle ground that is hard to diagnose precisely because it looks like ordinary
git churn.

**Why it happens:**
It is invisible in the initial five-repos-in-parallel design because there is exactly one
worker per working tree. The moment task decomposition improves to intra-repo parallelism (a natural
next step, since it is more useful than repo-level parallelism alone), the assumption "one worker,
one tree" silently breaks.

**How to avoid:**
Any subtask that shares a repo with another concurrently-running subtask must operate in an isolated
`git worktree add` (or a scratch clone), never the same working directory as another live worker.
Merge back to the shared branch only inside a single serialized "integrate" step, never from two
concurrent processes. Treat this as a hard rule enforced at graph-construction time (reject a graph
that assigns two concurrent nodes to the same repo path without worktree isolation), not a runtime
hope.

**Warning signs:**
`.git/index.lock` errors in worker logs. A revert that removes changes the operator did not expect
removed. A working tree left with unrelated, half-applied diffs after a run.

**Phase to address:**
Task graph / subtask schema design phase — this is a structural constraint on how a graph may assign
work to a repo, and belongs with Pitfall 2's edge-verification rule, before any intra-repo
parallelism is attempted.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|--------------------|-----------------|------------------|
| Skip the concurrency sweep, hardcode a worker count | Ship the parallel dispatcher faster | GPU thrashing/queue collapse discovered mid-overnight-run, wasting the whole run (Pitfall 1) | Never — the sweep costs one afternoon and is cheap to run |
| Reuse the flat "tail of output" ledger schema for the graph | No schema migration needed | Debug blindness on multi-node failures (Pitfall 6) | Acceptable only for the earliest single-repo spike; must extend before real graph runs |
| No per-worker heartbeat, just a fixed timeout | Simpler board schema | Tasks silently zombie after a process death, repeating the exact SSH-session-death lesson already paid for once (`AUTONOMY.md`) | Never — this project has already paid this cost in a different context |
| Let workers write directly to the shared repo instead of per-task worktrees | Avoids clone/worktree overhead | Index-lock races and dirty-tree corruption the moment a graph splits work within one repo (Pitfall 8) | Acceptable only while every subtask maps 1:1 to a distinct repo |
| Trust a node's self-reported "done" status to unblock a dependent node | Simplest possible graph executor | Fabricated/partial upstream work silently becomes accepted downstream context (Pitfall 2) | Never |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|------------------|--------------------|
| Ollama concurrent model loading | Assuming N dispatched workers means N GPU users | Pin one resident model for all concurrent workers, size concurrency from a measured sweep, set `OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_NUM_PARALLEL` explicitly rather than relying on auto-detection |
| `hermes kanban` swarm verb | Assuming it already does safe concurrent scheduling because it exists and is documented as "parallel workers → verifier → synthesizer" | It is explicitly unwired and untested per `AUTONOMY.md` ("The board exists and is empty... has been run, but through the orchestrator on the laptop, not through the gateway") — read and test its actual locking/model-loading behavior before trusting it in production |
| Windows Scheduled Task workers | Assuming a scheduled task's completion status reflects the inner work finishing cleanly | The worker must heartbeat/update the task board itself; `schtasks` reporting "done" only means the wrapper process exited, not that the subtask succeeded |
| Telegram control surface (cancel/retry) | Trusting a bot acknowledgment as proof the action was applied | Cancel/retry commands must be verified against the board's actual subsequent state (poll and confirm the claim was actually released/requeued), not just the bot's reply text |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|-----------------|
| Raising `OLLAMA_NUM_PARALLEL` without measuring | Aggregate throughput barely rises while per-request tokens/sec drops sharply | Sweep 1/2/4/8 concurrent requests against the real worker model first; cap parallel slots at the point aggregate throughput stops climbing | Measured on comparable hardware around 4 concurrent slots before per-request speed roughly halves; likely lower here given the 64K context floor's KV-cache cost — confirm locally (LOW confidence until measured on the RTX 3060 specifically) |
| Multi-model eviction thrashing | Workers stall while `ollama ps` shows models loading/unloading mid-run | Keep the decomposer on the premium/cloud lane (already the design) and pin exactly one resident local model for all concurrent workers | Breaks the moment two distinct local model names are hot in the same window |
| KV-cache growth at the 64K context floor | Available concurrency headroom shrinks faster than prompt length suggests | Keep subtask prompts short and single-purpose — the "grep-oracle" shape, not accumulated long context | Breaks first on the longest-context subtask in the graph, not the busiest one |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Treating task-board content as trusted instructions | A buggy or malicious upstream subtask's output text gets re-injected as literal instructions to a downstream worker (prompt injection via the board itself) | Treat board payloads as data to be quoted/sanitized, never re-injected as raw instruction text |
| Unrestricted filesystem/tool access per worker | A worker "fixing" one subtask reaches outside its assigned repo/file scope | Scope each subtask's filesystem and tool access to only what its own verify command checks |
| Full stdout/stderr retained in the ledger without scrubbing | A worker accidentally prints a secret (token, key) that then persists in a log file | Scrub known secret patterns before persisting logs, consistent with this project's existing credential-handling rules |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|--------------|-------------------|
| Board shows "in-flight" for a worker that is actually dead | Solo operator on a phone cannot tell real progress from a stuck task | Surface heartbeat age directly in the Telegram status reply, not just a state word |
| No graph-level summary, only per-node ledger lines | Operator has to dig through raw ledger entries to know if an overnight sweep actually finished | Roll graph state up to one line per repo/target ("4/5 repos passed, 1 blocked") in the bot reply |
| Silent partial success | A sweep that only actually attempted 3 of 5 repos (others skipped for an unlogged reason) is reported the same as a full pass | The summary must explicitly enumerate attempted vs. skipped vs. passed vs. failed nodes, never collapse to a single pass/fail count |

## "Looks Done But Isn't" Checklist

- [ ] **Concurrent worker dispatch:** Often missing an actual measured VRAM/throughput ceiling — verify by running the 1/2/4/8-concurrent sweep against the real worker model and comparing aggregate vs. per-request tokens/sec, not just confirming N processes started without crashing.
- [ ] **Verify-gated subtask:** Often missing a check that the diff/output is non-trivial — verify by deliberately testing the verify command against a deleted-file or no-op change and confirming it *fails*.
- [ ] **Task board coordination:** Often missing lease expiry — verify by killing a worker mid-task and confirming another worker (or a sweep pass) reclaims the task within a bounded window, not never.
- [ ] **Retry logic:** Often missing a hard cap — verify by forcing a subtask to fail permanently and confirming the system stops retrying and surfaces a "blocked" state instead of looping overnight.
- [ ] **Ledger for the graph:** Often missing full output and worker/model identity — verify by intentionally failing a subtask deep in a graph and confirming the ledger alone (no re-run) reveals which worker, which model, and the full stdout/stderr.
- [ ] **Intra-repo parallel nodes:** Often missing worktree isolation — verify by running two sibling subtasks against the same repo concurrently and confirming no `index.lock` contention or cross-task file clobbering occurs.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|-----------------|------------------|
| GPU thrashing mid-run | LOW | Drop concurrency to the last measured-good sweep value, requeue in-flight nodes; no data is lost because nothing is accepted until verify passes |
| Duplicate work discovered in the ledger | LOW | Cosmetic (wasted compute, not wasted correctness) since acceptance is still verify-gated; add an atomic lease-based claim before the next run |
| Livelock / unbounded retries burned a night of compute | MEDIUM | Add a retry cap and backoff, then replay the ledger to see the actual root failure that the loop was hiding; at $0 compute this is wasted time, not wasted money, but it directly threatens the "runs unattended overnight" claim and must be fixed before that claim is made publicly |
| Dirty repo left after a failed graph node | MEDIUM | Require per-subtask worktrees so recovery is `git worktree remove --force`, never a manual hand-fix on a shared tree |
| Vacuous verify pass shipped a broken result | HIGH | The worst case, because the ledger itself says "passed." The only defense is catching it via the same discipline that selected the worker model: adversarially test verify commands with a deliberately-failing case before trusting them, and spot-check ledger "passed" entries' actual diffs periodically |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|----------------|
| GPU/VRAM thrashing (Pitfall 1) | Phase 0 — Concurrency feasibility spike | Sweep results (aggregate vs. per-request tokens/sec at 1/2/4/8 concurrency) recorded and cited, ceiling set from data not guess |
| Compounding small-model failures across edges (Pitfall 2) | Task graph / subtask schema design phase | Every edge has an independent artifact check; deliberately feed a fabricated upstream artifact and confirm the downstream node's check catches it |
| Vacuous verify commands (Pitfall 3) | Verify-command authoring standard (same phase as above) | Every verify command has a documented negative test that it is shown to fail on |
| Board coordination failures (Pitfall 4) | Shared task board / coordination phase | Kill a worker mid-claim in a test run; confirm the lease expires and the task is reclaimed within its TTL, and that retries are capped |
| Over-parallelizing sequential work (Pitfall 5) | Phase 0 — same feasibility spike as Pitfall 1, run before the board phase | Serial-vs-concurrent bake-off on the five-repos chore set, wall-clock and ledger pass rate compared directly |
| Observability blindness (Pitfall 6) | Ledger/observability phase, built alongside the board phase | A deliberately failed graph run can be fully explained (worker, model, prompt, full output) from the ledger alone, with no re-run |
| Tool-call reliability under chain length (Pitfall 7) | Subtask design standard (same phase as Pitfall 2/3) | A chain-length benchmark of `qwen3.5:4b` (or whichever worker model is chosen) is run and its malformed-payload rate logged before multi-step subtasks are trusted to it |
| Concurrent git operations on a shared tree (Pitfall 8) | Task graph / subtask schema design phase | Graph construction rejects (or isolates via worktree) any two concurrently-scheduled nodes assigned to the same repo path |

## Sources

- Project primary evidence: `.planning/PROJECT.md`, `docs/AUTONOMY.md`, `docs/ROUTING.md` (grep-oracle vs. prose-rewrite result, 17-row miscount, model-selection honesty test, SSH-session-death lesson, ledger design) — HIGH confidence, this project's own recorded ledger
- [Why Do Multi-Agent LLM Systems Fail? (MAST taxonomy, arXiv 2503.13657)](https://arxiv.org/abs/2503.13657) — HIGH confidence, peer-reviewed dataset of 1,600+ annotated traces across 7 frameworks
- [Don't Build Multi-Agents — Cognition](https://cognition.com/blog/dont-build-multi-agents) — MEDIUM confidence, vendor blog but widely cited and corroborated by Anthropic's own caveats
- [How we built our multi-agent research system — Anthropic](https://www.anthropic.com/) (via cited secondary summaries: ZenML LLMOps DB, bytebytego, theaiengineer) — MEDIUM-HIGH confidence, primary vendor account with concrete numbers (90.2% improvement, ~15x token cost, duplicate-work incident and fix)
- [block/agent-task-queue — GitHub](https://github.com/block/agent-task-queue) — MEDIUM confidence, real open-source tool built specifically to solve concurrent-agent machine-thrashing, direct architectural analogue
- [Why Small LLMs Fail at Tool Calling — dev.to benchmark](https://dev.to/anak_wannaphaschaiyong_11/why-small-llms-fail-at-tool-calling-the-shocking-discovery-from-our-llama-3b-benchmark-5lg) and [LLM Agent Guardrails: 8B local model 53%→99% — dev.to](https://dev.to/monuminu/llm-agent-guardrails-the-engineering-playbook-for-taking-an-8b-local-model-from-53-to-99-on-18c) — MEDIUM confidence, practitioner benchmarks with concrete failure-rate numbers, not peer-reviewed
- [When Agents Fail to Act: Tool Invocation Reliability in Multi-Agent LLM Systems (arXiv 2601.16280)](https://arxiv.org/pdf/2601.16280) — MEDIUM confidence, size-threshold and per-call compounding figures
- [LLMs Gaming Verifiers: RLVR can Lead to Reward Hacking (arXiv 2604.15149)](https://arxiv.org/pdf/2604.15149) and [Auditing Reward Hackability in Code RL Training Environments (arXiv 2606.16062)](https://arxiv.org/pdf/2606.16062) — HIGH confidence, directly supports the "vacuous verify" pitfall with concrete gaming behaviors documented
- [I Pointed 8 Agents at One Local LLM — Ollama concurrency experiment](https://jangwook.net/en/blog/en/local-llm-concurrent-requests-num-parallel-experiment/) — MEDIUM confidence, single practitioner benchmark on M1/16GB (not the RTX 3060/12GB in this project) but directionally consistent with Ollama's own documented queueing behavior; concurrency ceiling numbers should be treated as LOW confidence until re-measured on this project's actual hardware
- [Benchmarking Ollama and vLLM for Concurrent LLM Serving (MDPI, arXiv-adjacent)](https://www.mdpi.com/2076-3417/16/11/5435) — MEDIUM confidence, peer-reviewed benchmark showing Ollama error-rate growth under concurrent load
- [Ollama FAQ / docs on multi-model VRAM behavior](https://docs.ollama.com/faq) — HIGH confidence, official documentation on model-eviction and queueing behavior when VRAM is insufficient
- Codex/Claude Code/opencode GitHub issue trackers on unbounded retry loops (cited via search, e.g. [openai/codex#34473](https://github.com/openai/codex/issues/34473), [anthropics/claude-code#22758](https://github.com/anthropics/claude-code/issues/22758)) — MEDIUM confidence, real production incident reports corroborating the livelock/retry-storm pitfall across multiple independent tools

---
*Pitfalls research for: local multi-agent orchestration, small models, one consumer GPU*
*Researched: 2026-09-02*
