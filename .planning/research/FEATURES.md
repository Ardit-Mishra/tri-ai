# Feature Research

**Domain:** Personal multi-agent task orchestration (parallel local-model workers, shared task board, verification-gated, single operator)
**Researched:** 2026-09-02
**Confidence:** MEDIUM-HIGH — table-stakes job-queue mechanics are well-established (Context7-adjacent industry consensus via job-queue literature); multi-agent-framework anti-pattern claims are WebSearch-verified across multiple independent sources; the first-version bottleneck analysis is derived from this project's own documented, measured constraints (`.planning/PROJECT.md`, `docs/AUTONOMY.md`) rather than external sources, so treat that part as HIGH confidence for *this* system specifically, not as a general claim.

## Feature Landscape

### Table Stakes (Users Expect These)

This "user" is one operator trusting unattended runs while asleep or abroad. Table stakes here means: missing it makes the ledger untrustworthy or lets a run hang forever, not "looks unprofessional."

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Hard concurrency limit | The 12GB VRAM ceiling is a documented hard constraint (`PROJECT.md`): models above it CPU-offload and become an order-of-magnitude slower, not slightly slower. An unbounded worker pool doesn't fail gracefully here, it silently degrades every worker at once. Genuinely non-negotiable, not cosmetic. | LOW | A single integer cap enforced by the dispatcher. The *right* number is unmeasured — start conservative (2) and raise only after measuring VRAM headroom under load. |
| Per-subtask timeout (already exists) | `queue.jsonl` already has `timeout`/`verify_timeout` fields. Without a timeout, one stuck worker (infinite loop, model repeating itself) blocks its slot forever in a scheme with only 2-3 concurrent slots — proportionally worse than in the serial system, where a hang just delays the rest of the queue instead of starving the whole board. | LOW | Extend, don't redesign — this is proven ground per `docs/OPERATING.md`. |
| Retry — not backoff | Genuinely useful: transient model flakiness (malformed output, a tool call that races a file lock) is real and free to retry locally. | LOW | **Backoff is the part that "merely looks professional" here.** Backoff-with-jitter exists to protect a *shared external service* from a thundering herd (see job-queue literature below). This system's "service" is the operator's own single GPU — there is no external rate limit to protect, and the actual scarce resource (VRAM) is already governed by the concurrency limit above, not by retry timing. A flat short delay before retry is enough; exponential-backoff-with-jitter is solving a multi-tenant problem this system doesn't have. |
| Cancellation (in-flight) | Explicitly requested (`PROJECT.md`: board must support "cancel or retry" from Telegram). Without it, a bad task consumes a concurrency slot until its timeout fires, which could be 30 minutes on a system with only 2 slots. | MEDIUM | On Windows, "cancel" means killing a registered scheduled-task process (`schtasks /End`) or its child, then writing a `cancelled` result to the ledger so the board doesn't show it as stuck `in-flight` forever. Needs the durability pattern from `docs/AUTONOMY.md` (schtasks-registered, not SSH-launched) to even have something killable. |
| Structured logging / run history (already exists) | `ledger.jsonl` already captures id, result, agent exit code, verify exit code, duration, and output tails. This is the entire reason the autonomy claim is citable rather than asserted (`docs/AUTONOMY.md`). Extending it to parallel runs is non-negotiable — the moment two workers can be in-flight at once, a single interleaved log stream is actively misleading, not just untidy. | LOW-MEDIUM | The only real work is isolating per-subtask log capture (one file/stream per subtask) so concurrent output doesn't interleave — see Dependencies below. |
| Task-board state machine (claimed / in-flight / done / failed) | This is the actual mechanism the parallel model requires — without a shared, inspectable state, "coordination without direct messaging" (the already-chosen design) has nothing to coordinate through. `hermes kanban` already models exactly this shape and is currently unused. | MEDIUM | Reuse `hermes kanban`'s existing state model rather than inventing a new one — it already matches PROJECT.md's decision to coordinate via board, not messages. |

**What merely looks professional here, worth naming explicitly so it doesn't get built by default:**
- Exponential backoff with jitter (see above — solves a shared-external-service problem this system doesn't have).
- Dead-letter queues / priority queues (built for multi-tenant, high-volume systems balancing many producers; five repos' chores is not that volume).
- Distributed locking across machines (this is one GPU box plus a laptop, not a cluster — a file-based board with atomic writes is sufficient, a lock service is not).
- Horizontal autoscaling of workers (there is no "more capacity" to scale to — the ceiling is one GPU's VRAM, which is fixed).

### Differentiators (Competitive Advantage)

Where this system should actually spend build effort beyond "table stakes job queue," because it maps to the Core Value in PROJECT.md: work continues, verified, after the expensive model leaves.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Board observable *and controllable* from Telegram (view + cancel + retry) | Closes the documented gap directly: today Bob is a chat interface, not an execution loop (`docs/AUTONOMY.md`). This is the single feature that turns the existing substrate into what was actually promised — remote control from a phone, not just remote chat. | MEDIUM | Read side (status) is cheap — it's a formatted query over the board/ledger. Write side (cancel/retry) needs the same kill-and-mark-cancelled mechanism as the table-stakes cancellation feature; Telegram is just a thin client on top of it, not a separate control plane. |
| Per-subtask verify command, extended to the graph level | Extends the one rule the whole system already rests on (verify-gated, exit-code-only acceptance) down into parallel branches. This is what keeps "several free models working unattended" from becoming "several free models making things up unattended in parallel," which is strictly worse than one doing it serially. | LOW (mechanically — it's the same field, just per-node in a graph instead of per-row in a flat queue) | Already true in spirit for the flat queue; the differentiator is applying it without exception to every node the premium model's decomposition produces, including nodes a human didn't hand-write. |
| Failure isolation per graph branch (a failed subtask reverts, does not block unrelated branches) | This is what makes "parallel" actually pay for itself over serial: in the serial system today, one failing repo doesn't block the next repo's queue rows, but a failing *step within* a repo's chore chain can. Making that isolation explicit at the graph level is the actual point of moving to a graph instead of a flat list. | MEDIUM | Requires the graph to encode real dependency edges, not just a flat list re-run in parallel — otherwise "graph" is a rebrand with no new behavior. |
| Time/duration accounting surfaced as a CPU-offload signal, not just bookkeeping | Duration per subtask is already captured in the ledger for free. The differentiator is *using* it: a subtask that suddenly takes 10x longer under concurrency is the direct, cheap, already-available signal for "this worker got pushed onto CPU" — exactly the unmeasured unknown PROJECT.md flags as the central risk of concurrency. No new instrumentation needed, just a comparison against the same subtask's serial-run baseline. | LOW | High value for near-zero marginal cost — this is the cheapest possible answer to "is concurrency actually helping or silently hurting." |

### Anti-Features (Commonly Requested, Often Problematic)

Evaluated bluntly against a one-operator system where the ledger's honesty is the entire point.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| Agent "personalities" / role-play (CrewAI-style named personas: "Senior QA Engineer," "Build Specialist") | Looks like it adds structure; popular pattern in CrewAI-style frameworks, makes demos read nicely. | A 4–7B local model does not reason better because it has a name and backstory — it burns prompt tokens and context headroom that is already scarce (64K-context floor, 12GB VRAM), and it creates the *appearance* of specialization when the only thing that actually distinguishes a good result from a bad one is the verify command. This directly contradicts the system's own founding lesson: a command must judge the result, not the model's self-report — and a persona is pure self-report theater. | Name workers by function only if needed for logs ("gs-tests-worker"), never by personality. Spend the saved context on the actual task prompt and verify instructions. |
| Unbounded autonomy (let workers decide what to run next, self-queue new tasks) | Feels like "real" autonomy, and is the direction agent-framework marketing pushes. | Independently confirmed by current multi-agent-framework research: a large share of multi-agent deployments fail specifically because scope exceeded what could be verified — the orchestrator becomes a single point of failure and misclassification compounds. This project already drew this line correctly (`PROJECT.md` out-of-scope: "judgment work on the free lane" is explicitly banned) — this entry exists to flag it as a temptation to resist as the system grows, not a gap to fill. | Every new task the graph produces still needs a human- or premium-model-authored node with its own verify command. The graph can widen; the judgment boundary should not. |
| Self-modifying prompts (a worker rewrites its own instructions based on past runs) | Sounds like learning / self-improvement. | Makes the ledger's central claim ("quote the ledger, never a recollection," `docs/OPERATING.md`) unverifiable — if the prompt that ran yesterday isn't the prompt that runs today, "why did this pass yesterday and fail today" has no answer. This is actively harmful specifically *because* this system's credibility rests on being able to say exactly what ran. | If a prompt needs to change, that's a human edit with a new task `id` (the existing convention for "the contract changed," per `docs/OPERATING.md`) — never a silent runtime mutation. |
| Agent-to-agent chat / negotiation between workers | Common in AutoGen-style frameworks; feels more "agentic." | Already explicitly out of scope in `PROJECT.md`, and independently confirmed by current research: conversational multi-agent negotiation is the pattern requiring the most custom engineering to make reliable and the highest risk for cost/context blowup — small models negotiating with small models is exactly where these systems produce expensive nonsense. Nothing found in current research contradicts the project's existing call here. | Board-based coordination (claim / in-flight / done / failed), already decided. This entry exists only to confirm the decision holds and should not be revisited as a "nice to have" later. |
| A dashboard that duplicates the ledger | Feels more "real" / demo-able than reading a file. | A hosted or even local web UI mirroring `ledger.jsonl` adds a new thing that must stay running, a new attack surface, and (if hosted) a new dependency — all for zero new information over the existing ledger query plus a Telegram status command. It also risks quietly reintroducing metered infrastructure or a service that must be "up," which conflicts with the $0 and no-new-accounts constraints. | A one-glance formatted Telegram summary (`N running / N passed / N failed / N queued`) is the entire dashboard this system needs. Build UI only if a specific question can't be answered by grep-ing the ledger or querying the board — not preemptively. |
| Generality for imagined future users (plugin system, multi-tenant task ownership, a configurable workflow DSL beyond `queue.jsonl`) | Feels like good engineering practice / "doing it right." | Explicitly named as an anti-goal already: `PROJECT.md` states generality is not a goal and would enlarge the surface that has to be trusted. There is exactly one operator; every generality knob is a knob only a hypothetical other operator will ever turn, and each one is attack surface and maintenance burden paid by the one person who exists. | Extend the existing flat/graph JSON schema minimally, in the direction this specific operator's actual repos need, and stop. |
| Full hosted agent-observability platform (Langfuse/LangSmith-style tracing SaaS) | Popular 2026 tooling category; "everyone doing agents in production has one." | New external dependency, likely metered or account-gated, for a single operator whose file-based ledger already answers "what happened, did it pass, how long did it take" — the questions that actually matter for a verify-gated system. Full LLM-conversation tracing platforms exist to debug *why* a non-deterministic agent chose a wrong tool call across a large fleet; at five repos and a handful of subtasks, that's solvable by reading a captured stdout tail. | Extend the existing ledger's output-tail capture per subtask (already proven pattern) before reaching for a hosted tracing product. Revisit only if failures become genuinely opaque without step-level traces — see Observability ranking below. |

## Feature Dependencies

```
Task-board state machine (claim/in-flight/done/failed)
    └──requires──> Per-subtask isolated log capture (one file/stream per subtask, not one shared stream)
                       └──requires──> Structured logging schema extended from flat ledger to graph-node ledger rows

Telegram board observability (view status)
    └──requires──> Task-board state machine

Telegram board control (cancel / retry)
    └──requires──> In-flight cancellation mechanism (kill registered process + mark ledger row "cancelled")
                       └──requires──> Durable process registration (Windows scheduled task per subtask, per existing
                                       docs/AUTONOMY.md pattern — an SSH-launched process cannot be reliably killed
                                       or tracked after a dropped connection)

Concurrency limit tuning (raising above a conservative default)
    └──requires──> A measured VRAM/CPU-offload threshold under N concurrent local-model workers
                       (currently unmeasured — PROJECT.md names this explicitly as "the central unknown")

Graph-level failure isolation (failed branch doesn't block siblings)
    └──requires──> Real dependency edges in the task graph, not a flat list re-run concurrently
                       └──requires──> Premium-model decomposition step already ships a graph, not a queue row

Approval gate before running a phone-initiated task
    └──requires──> Task-board state machine (needs a "pending confirmation" state distinct from "queued")

Time/duration-as-CPU-offload-signal
    └──requires──> Nothing new — already available from existing per-row duration field; enhances trust in
                    concurrency limit tuning by giving it real data instead of guesswork

GPU-bound "fix and reverify" chores running concurrently
    └──conflicts with──> Concurrency limit tuned only against CPU/IO-bound chores
                    (fixing chores are token-generation-bound and hit the VRAM ceiling directly; running them
                    at the same concurrency number validated for pure-verification chores is not a safe assumption)
```

### Dependency Notes

- **Per-subtask isolated log capture must exist before parallel run history is trustworthy.** The current ledger pattern assumes one attempt at a time; the moment two subtasks run concurrently, a shared stdout capture interleaves and the "tail of both outputs" field (the thing that makes failures debuggable, per `docs/OPERATING.md`) stops meaning anything. This is the one piece of "boring plumbing" that has to land before anything else in the parallel model is trustworthy — it is not optional infrastructure, it is a precondition for the ledger continuing to be citable.
- **Telegram control requires the cancellation mechanism to exist first, not the other way around.** Telegram is explicitly a thin client here — it should not become a second, parallel implementation of "how to stop a running task." Build cancel-by-id once (CLI or board-level), then expose it through Telegram.
- **Concurrency-limit tuning depends on a measurement that hasn't been taken.** This is the one dependency that blocks scaling from a conservative default to whatever number the hardware can actually sustain — and per PROJECT.md it is explicitly unmeasured, not merely undocumented. Treat "measure VRAM under 2, then 3, then 4 concurrent local workers" as a required step before raising the default, not an optional nice-to-have.
- **GPU-bound fixing chores conflict with a concurrency number validated only against CPU/IO-bound verification chores.** This is the most important dependency conflict in the whole feature set (see First-Version Evaluation below) — treating "N concurrent workers is safe" as a single fact rather than two separate facts (safe for read-only verify vs. safe for model-driven fixing) is the most likely way this milestone quietly fails its own goal.

## MVP Definition

### Launch With (v1)

Minimum to validate the actual hypothesis under test: parallel execution + board coordination + Telegram observability, without also betting on unmeasured GPU concurrency behavior.

- [ ] Task-board state machine (claim/in-flight/done/failed) — reuse `hermes kanban`'s existing model rather than inventing one; this is the substrate everything else sits on
- [ ] Hard concurrency limit, conservative default (e.g. 2) — non-negotiable given the documented VRAM ceiling
- [ ] Per-subtask timeout/verify_timeout, extended from the existing flat-queue fields to graph nodes — already proven, just needs to survive the schema change
- [ ] Per-subtask isolated log capture (no interleaved output) — precondition for parallel ledger rows meaning anything
- [ ] In-flight cancellation (kill + mark cancelled in ledger), reachable from the laptop CLI first
- [ ] Telegram: read-only board status (running/passed/failed/queued) + cancel + retry — the documented gap this milestone exists to close
- [ ] Ledger extended to graph-node rows, still append-only, still exit-code-gated — no change to the acceptance rule, only to what's being accepted
- [ ] First target scoped to **2–3 repos' read-only verification chores only** (test suite on a currently-green branch, typecheck, dependency audit) — explicitly *not* chores that expect the worker to fix failing code, so the first measurement of "is parallelism real" isn't contaminated by unmeasured GPU contention

### Add After Validation (v1.x)

Trigger: v1's 2–3 repo run has passed, the ledger shows real concurrent durations, and a CPU-offload threshold has actually been measured (not assumed).

- [ ] Scale from 2–3 to the full five repos, using the measured concurrency ceiling rather than the v1 conservative default
- [ ] Graph-level failure isolation with real dependency edges (failed branch doesn't block siblings) — trigger: the graph is no longer trivially flat, i.e. some repos have multi-step chore chains worth branching
- [ ] Approval gate before running a phone-initiated task (pending-confirmation state) — trigger: Telegram assignment is used enough that a fat-fingered message is a real risk, not a hypothetical one
- [ ] Pause/resume the whole queue from Telegram — trigger: a false start needs stopping without individually cancelling every in-flight task
- [ ] Time/duration surfaced explicitly as a CPU-offload alarm (flag when a subtask's duration deviates sharply from its serial baseline) — trigger: concurrency is running routinely enough that manual duration-eyeballing stops scaling

### Future Consideration (v2+)

Defer until the CPU/IO-bound path above is proven reliable and measured.

- [ ] GPU-bound "fix and reverify" chores running concurrently — defer until VRAM contention under N concurrent local-model workers is measured; this is a different bottleneck than the v1 target and should not be assumed safe at the same concurrency number
- [ ] Full replayable per-subtask conversation/tool-call trace — defer until a failure is actually opaque without one; the verify command's exit code plus a captured output tail has been sufficient so far, per the system's own doctrine that a command's output is ground truth and a model's self-report is not
- [ ] Task graph visualization (rendered dependency diagram) — defer while the graph is small enough (roughly five repos times a handful of chores) to read as a JSON list; revisit only if the graph grows branchy enough that a flat listing stops being legible
- [ ] Any configurable workflow DSL beyond the existing `queue.jsonl`/graph schema — explicitly named as an anti-goal (see Anti-Features); do not build ahead of an actual second use case

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Task-board state machine | HIGH | MEDIUM (reuses `hermes kanban`) | P1 |
| Hard concurrency limit | HIGH | LOW | P1 |
| Per-subtask isolated log capture | HIGH | MEDIUM | P1 |
| In-flight cancellation | HIGH | MEDIUM | P1 |
| Telegram board view (status) | HIGH | LOW (once board exists) | P1 |
| Telegram cancel/retry | HIGH | LOW (once cancellation exists) | P1 |
| Ledger extended to graph nodes | HIGH | LOW | P1 |
| Scope v1 to CPU/IO-bound chores only, defer GPU-bound fixing | HIGH (risk reduction) | LOW (it's a scoping decision, not code) | P1 |
| Scale to 5 repos | MEDIUM | LOW (once concurrency is measured) | P2 |
| Graph-level failure isolation with real edges | MEDIUM | MEDIUM | P2 |
| Approval gate for phone-initiated tasks | MEDIUM | LOW-MEDIUM | P2 |
| Pause/resume whole queue | MEDIUM | LOW | P2 |
| Duration-as-CPU-offload signal | MEDIUM-HIGH | LOW | P2 (cheap enough to consider pulling into P1) |
| GPU-bound fix-and-reverify chores, concurrent | LOW (unproven) | HIGH (unmeasured VRAM contention) | P3 |
| Full replayable conversation trace | LOW (until proven needed) | HIGH | P3 |
| Task graph visualization | LOW | MEDIUM | P3 |
| Hosted observability SaaS | LOW (duplicates ledger) | MEDIUM-HIGH + ongoing dependency | Rejected |
| Agent personalities | NEGATIVE | LOW to build, ongoing cost in context/trust | Rejected |
| Agent-to-agent chat | NEGATIVE | HIGH, already ruled out | Rejected |

## Observability Ranking (value per unit of build effort)

Answering "what does the operator actually need to see to trust it," ranked highest ROI first:

1. **Live task-board status (claimed/in-flight/done/failed).** Highest value, low-medium effort — the board already has a model to reuse (`hermes kanban`). This is the single artifact that answers "is it working right now."
2. **Ledger extended to graph-node run history.** Near-zero marginal effort (the mechanism exists), and it's what makes the whole system's claims citable rather than asserted — already the project's stated standard.
3. **Per-subtask log tail on failure.** Near-zero marginal effort once isolated log capture exists (a precondition anyway, see Dependencies) — this is the actual debugging tool, more useful than a full trace for the failure modes this system has actually hit (a command failed, here's its output) versus the failure modes a trace is built for (a model's reasoning went wrong across many tool calls).
4. **Duration/time accounting as a CPU-offload signal.** Already captured for free; the only new work is *using* the number as an alarm rather than a bookkeeping fact. Extremely cheap, and it directly answers the project's own named central unknown.
5. **Task graph visualization.** Medium effort, low value at current scale — five repos times a handful of chores is legible as a list. Revisit only if the graph gets deep enough that a flat view stops working.
6. **Full replayable conversation/tool-call trace.** Highest effort of the group, and lowest current value — this system's own doctrine holds that a command's output is the fact and a model's narration is not, so the debugging payoff of a full trace is lower here than in frameworks where the model's reasoning path is itself the thing being audited. Build only if failures start recurring in a way the verify output and log tail can't explain.

## Human-in-the-Loop / Remote Thin Client — What's Safe on a Phone

Given the project's own out-of-scope list already forbids autonomous push/merge/deploy/credential access, the phone-safety question narrows to: what should Telegram be allowed to *trigger*, versus only *show*.

**Safe to expose to Telegram, no gate needed:**
- Read board/ledger status (what's running, queued, passed, failed)
- Read a failed task's captured output tail
- Cancel a specific in-flight or queued task (cancellation only ever removes work, never adds risk)
- Retry a task that already has a verify command on file (re-running an already-vetted contract is not a new risk)

**Safe, but should be gated (confirm-before-execute) rather than instant:**
- Enqueuing a *new* task from a phone message — a fat-fingered or auto-complete-mangled message triggering a real GPU job is a plausible failure mode a laptop CLI doesn't have (typing on a phone is more error-prone, and there's no `--dry-run` review step in a chat message the way there is reading a `queue.jsonl` diff before running it)
- Pausing/resuming the entire queue (low risk, but affects everything currently in flight, so a confirm step costs little and prevents an accidental blanket action)

**Should never be exposed to Telegram, full stop:**
- Arbitrary shell command execution ("just run this command on the desktop") — this collapses the entire verify-gated design into an unrestricted remote shell, which is a different and far more dangerous system than the one described
- Editing or approving a verify command remotely — a verify command is a security-relevant contract (per `docs/OPERATING.md`, "the whole safety mechanism"); changing it should happen in the repo, reviewed like code, never as a chat message
- Triggering push, merge, deploy, or any credentialed action — already out of scope project-wide, and a phone is the worst place to relax that rule, not the best (least review context, least ability to see a diff)
- Adding or rotating credentials of any kind

## Competitor Feature Analysis

Framed against three reference categories rather than named products, since this system's actual competitors are (a) general job-queue tooling and (b) general multi-agent frameworks, and it should borrow selectively from both while adopting neither's full model.

| Feature | Job-queue tools (Celery/Airflow-style) | Multi-agent frameworks (CrewAI/AutoGen-style) | Our approach |
|---------|------------------------------------------|------------------------------------------------|--------------|
| Concurrency control | Per-worker concurrency limits, autoscaling pools | Rarely bounded explicitly; framework assumes API-hosted models with elastic capacity | Hard cap tied to a *measured* local VRAM ceiling — the opposite assumption (fixed, scarce capacity), so autoscaling patterns don't transfer |
| Retry/backoff | Exponential backoff + jitter as default, tuned for shared external services | Often just re-prompt-and-retry, no formal backoff | Retry yes, backoff-with-jitter no — no shared external service to protect (see Table Stakes) |
| Coordination model | Central broker/queue, workers pull independently, no worker-to-worker chat | AutoGen: workers converse directly; CrewAI: role-based hierarchy with orchestrator | Shared board only, already decided against direct agent chat (PROJECT.md) — closer to the job-queue model than the agent-framework model |
| Identity/roles | Workers are anonymous, interchangeable pool members | Workers have named personas/roles as a first-class concept | Workers are named by function for logs only, never by persona — rejected the personality pattern outright (see Anti-Features) |
| Result acceptance | Job succeeds if it exits 0 / doesn't raise | Framework-dependent; often accepts a model's own "I'm done" self-report | Exit-code-of-a-verify-command only, never model self-report — stricter than either reference category by design |
| Observability | Mature: dashboards (Flower, Airflow UI), often over-built for solo use | Emerging hosted tracing SaaS (Langfuse/LangSmith-style) as the 2026 default answer | File-based ledger + Telegram status query; explicitly rejects both a hosted UI and a hosted tracing SaaS as duplicating what a log already gives one operator |
| Human control surface | Usually a web admin UI (pause queue, requeue, inspect) | Emerging pattern: chat-platform approval gates (Slack/Telegram buttons) for destructive actions | Telegram as the control surface (already decided), scoped per the safe/gated/never breakdown above — narrower than either reference category, deliberately |

## Sources

- [Job Queues Explained: Workers, Retries, and Scheduling](https://blog.openreplay.com/job-queues-explained-workers-retries-scheduling/) — concurrency limits, exponential backoff + jitter rationale, idempotency
- [The Job Queue Problem: How to Stop Workers from Stepping on Each Other](https://medium.com/@puneet_kumar_agarwal/the-job-queue-problem-how-to-stop-workers-from-stepping-on-each-other-4b832c77d02d) — exactly-once processing, graceful shutdown
- [Background Jobs and Queues: 2026 Engineering Reference](https://www.digitalapplied.com/blog/background-job-queue-patterns-2026-engineering-reference) — dead-letter queues, retry patterns
- [AI Agent Anti-Patterns (Part 1): Architectural Pitfalls That Break Enterprise Agents](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-agents-before-they-32d211dded43) — orchestrator single point of failure, context overflow, cost escalation
- [Multi-Agent AI Orchestration Guide & 2026 Updates](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier) — pattern-mismatch failure rate, swarm requiring custom engineering
- [6 Multi-Agent Orchestration Patterns for Production (2026)](https://beam.ai/agentic-insights/multi-agent-orchestration-patterns-production)
- [CrewAI vs AutoGen: Which One Is the Best Framework](https://www.zenml.io/blog/crewai-vs-autogen) — role-based personas vs conversational negotiation, overengineering comparison
- [CrewAI vs AutoGen — Role-Based Teams or Conversation Agents? (2026)](https://myengineeringpath.dev/tools/crewai-vs-autogen/)
- [Human-in-the-Loop Approval Workflow: Slack/Telegram Approval Gates for Agent Workflows](https://www.nnode.ai/blog/2026-02-05-human-in-the-loop-approval-gates) — default-deny for destructive actions, checkpoint/resume pattern
- [Building a Telegram Bot for Your Kubernetes Cluster with kagent and A2A](https://maniak.io/articles/2026-03-13-telegram-bot-kagent-a2a-kubernetes/) — Approve/Reject button pattern for destructive ops
- [Human-in-the-Loop AI Agents: How to Design Approval Workflows](https://www.stackai.com/insights/human-in-the-loop-ai-agents-how-to-design-approval-workflows-for-safe-and-scalable-automation) — gate placement: irreversibility, privilege expansion, boundary-exceeding
- [Agent observability: The complete guide for 2026 (Braintrust)](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026) — step-level tracing vs binary health checks, why traditional logging is insufficient for non-deterministic agents
- [14 best AI agent observability tools in 2026 (Arize)](https://arize.com/blog/best-ai-observability-tools-for-autonomous-agents-in-2026/)
- Project-internal, HIGH confidence for this system specifically: `.planning/PROJECT.md`, `docs/OPERATING.md`, `docs/AUTONOMY.md` — the 12GB VRAM ceiling, the unmeasured CPU-offload threshold, the existing ledger schema, the already-decided out-of-scope list (agent-to-agent messaging, judgment work on the free lane, generality)

---
*Feature research for: personal multi-agent task orchestration, single operator, verification-gated*
*Researched: 2026-09-02*
