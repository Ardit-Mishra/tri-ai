# Tri-AI Swarm — orchestrator and workers

## What This Is

An orchestration layer for Tri-AI, the three-node build system (travelling laptop, always-on GPU
desktop, phone). A strong model decomposes one assigned task into an explicit task graph, writes it
to disk, and exits. Several small local models then execute that graph **simultaneously and
unattended**, coordinating through a shared task board, and no result is accepted except by a
verification command's exit code.

It is for one operator — Ardit — running production software solo from another country, at $0
marginal cost.

## Core Value

**A task assigned once gets decomposed, executed in parallel by free local models, and verified by
exit codes — without the expensive model staying in the loop.**

If everything else fails, that must work. The point is not parallelism for its own sake; it is that
the expensive, quota-limited model spends its turns on judgment and then leaves, while the work
continues without it.

## Requirements

### Validated

Shipped and proven before this milestone — the substrate this builds on.

- ✓ Verify-gated delegation — a task is accepted only by a verify command's exit code; failures
  auto-revert (`src/run_queue.py`, ledger: 9/9 across 6 repos, ~1,216s, $0)
- ✓ 4-deep fallback chain so a model lane never dead-ends
- ✓ GPU job dispatch from laptop to desktop, surviving laptop disconnection (Windows scheduled tasks)
- ✓ Private mesh networking between nodes (Tailscale)
- ✓ Chat reaches the desktop from a phone (Hermes gateway, "Bob")
- ✓ Continuity when premium quota runs out (`claude-free` / `cf`, route registry keyed by resolved
  model rather than route name)
- ✓ Phase 1 board substrate: verify schema, atomic claim contention, and Windows-safe stale reclaim
  adapter (22 tests; published ledger evidence)
- ✓ Phase 2 single-worker path: exit-code acceptance, artifact gate, recovery/quarantine, ledger,
  and CLI/queue/schedule triggers (89 tests, independently reviewed)
- ✓ Phase 3 planner and bounded CPU/IO dispatch: transactional graph creation, cap-derived parallel
  workers, and branch-local failure isolation (132 tests; independent review passed)

### Active

Hypotheses until shipped.

- [ ] The model-backed planner is wired to a live premium-model assignment; its persisted-graph
      validator is shipped, but the live model ingress is deliberately still separate
- [ ] Tasks can be assigned from Telegram, with a confirmation gate
- [ ] The board is observable from a real Telegram transport and returns full captured logs
- [ ] The board is controllable from Telegram only through proven cancel/retry primitives
- [ ] Passive memory may inform a worker only after citation validation and integration proof
- [ ] Remote execution may be accepted only from an independently checkable verification result,
      never a remote worker's completion report
- [ ] First working target: run the verifiable chores of all five repos in parallel rather than
      serially (pending research — see `.planning/research/`)

### Out of Scope

- **Direct agent-to-agent messaging** — small models negotiating with small models is where these
  systems reliably produce expensive nonsense. Coordination goes through the board.
- **Judgment work on the free lane** — anything a command cannot judge waits for the human. This is
  the boundary that keeps the ledger meaningful; widening it would make the whole system's central
  claim untrue.
- **Mandatory paid/cloud routing or autonomous account creation** — banned. Optional
  operator-configured cloud/free routes may be used through the local route broker, but local
  fallback must remain viable and Tri-AI never creates accounts, changes plans, or raises limits.
- **Autonomous push, remote/protected-branch merge, deploy, or credential access** — banned.
  A separate local promotion process may merge a reviewed immutable source SHA only into a
  dedicated local integration branch after a fresh operator-owned verifier passes; its contract
  and evidence are defined in `research/capability-expansion-design.md`.
- **A general agent framework** — this serves one operator's actual workflow. Generality is not a
  goal and would enlarge the surface that has to be trusted.

## Context

The substrate is built and verified; this milestone wires the missing layer.

**Available and unused:** `hermes kanban` (has a `swarm` verb — parallel workers → verifier →
synthesizer), `hermes cron`, `hermes webhook`. The kanban board exists and is empty.

**The documented gap this closes:** messaging Bob works and `/capture` writes to an inbox file, but
nothing reads that inbox back out and executes it. Today it is a chat interface to the desktop, not
a remote execution loop.

**Hardware:** desktop RTX 3060, 12 GB VRAM, 17 Ollama models, always on. Laptop: 11 models, Claude
Code, OmniRoute router on `:20128`, and the git repositories.

**The rule everything descends from,** learned by observation rather than preference: given a task
with a `grep` oracle a free model performed perfectly; given a prose rewrite with no oracle the
same model deleted 387 of 389 lines of a working file and reported success. The variable was never
difficulty — it was whether a command could judge the result.

**Corollary that governs subtask design:** never ask a model to derive a fact from raw output; have
the command emit the fact. Asked to count a 17-row list by eye a model answered 15, then 18; given
`ollama list | tail -n +2 | wc -l` it answered 17.

## Constraints

- **Budget**: local execution must remain viable at $0 marginal cost. An operator may explicitly
  configure an optional free, prepaid, or quota-backed cloud route; Tri-AI never creates accounts,
  changes plans, raises limits, or makes a cloud route mandatory for completion.
- **Model**: Hermes rejects any model below `MINIMUM_CONTEXT_LENGTH = 64_000`
  (`agent/model_metadata.py:405`, enforced at `agent/agent_init.py:2772`). This disqualifies
  `qwen3:14b` (40,960) and `qwen2.5-coder:14b` (32,768) **as Ollama currently serves them** —
  stronger coders than the 4.7B model in use, excluded by one integer comparison. Raising served
  context is possible in principle and costs KV-cache VRAM; unmeasured, so not assumed.
- **Hardware**: 12 GB VRAM ceiling. Models above it CPU-offload and become an order of magnitude
  slower, not slightly slower. Concurrent workers multiply this pressure — the central unknown.
- **Safety**: no push, remote/protected merge, deploy, or credential access from worker code.
  An infrastructure-owned route broker may use only credentials injected by the operator at its own
  startup; workers neither receive nor read provider credentials.
- **Environment**: the desktop's SSH shell is PowerShell; SSH-launched processes die with the
  session, so durable work is registered as a Windows scheduled task and triggered.
- **Honesty**: the system's public description must not exceed what the ledger shows.

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| Premium model plans, local models execute | Keeps the expensive model on judgment only and lets work continue after quota is gone — the exact failure this system exists to survive | — Pending |
| Coordination via a shared task board, not agent-to-agent messages | Cannot deadlock, is inspectable, and matches what `hermes kanban` already models | — Pending |
| The task graph is persisted to disk, not held in a conversation | A graph on disk survives a dropped connection, a compaction, and a quota wall; a conversation does not | — Pending |
| Every subtask carries its own verify command | Extends the one rule the whole system rests on down to the subtask level | — Pending |
| First target is parallel verifiable chores | Every subtask has an obvious oracle, it extends proven ground, and it makes the nightly sweep real | — Pending (research first) |
| Board observable and controllable from Telegram | Requested explicitly; also closes the documented inbox→execute→report gap | — Pending |

---
*Last updated: 2026-09-10 after phase and branch audit*
