# Tri-AI

A three-node build system that lets one person ship production software from anywhere, at
near-zero marginal cost, by routing every task to the cheapest machine and model that can
actually do it — and by refusing to accept any delegated work on the agent's word alone.

It is not an agent framework. It is a set of small, boring pieces wired so that a laptop in one
country, a GPU desktop in another, and a phone can behave as one workshop.

```
   phone ──Telegram──▶  desktop (always-on)  ◀──Tailscale SSH──  laptop
                        · Hermes gateway "Bob"                   · Claude Code (judgment)
                        · Ollama, 17 models                      · Ollama, 11 models
                        · RTX 3060 12 GB                         · OmniRoute (router)
                        · Windows Task Scheduler                 · the repos
```

## The one idea

> **A delegated task is accepted or rejected by a verification command's exit code, never by the
> agent's own report.**

This is not caution for its own sake. Observed on the same free model, the same day:

| task | oracle | result |
|---|---|---|
| Delete two project entries | `grep` returns empty | perfect |
| Rewrite three prose strings | *none* | **deleted 387 of 389 lines and reported success** |

The difference was not difficulty. It was whether a command could judge the result. So the rule
that falls out of it:

> If you cannot write a command that proves the task worked, the task does not belong in the
> queue. Leave it for the expensive model.

Everything else in this repo is downstream of that sentence.

## What is actually automated

Being precise about this matters more than the architecture diagram, because the interesting
claim is not "I built an agent system" — it is "I know exactly which parts of it I can trust."

| capability | status |
|---|---|
| Queued work runs unattended and self-verifies | **working** — `src/run_queue.py` |
| A failed task reverts itself before the next one starts | **working** |
| Every attempt is written to an auditable ledger | **working** |
| Long jobs survive the laptop going offline | **working** — Windows scheduled tasks on the desktop |
| GPU training dispatched from the laptop, run on the desktop | **working** |
| A model lane never dead-ends | **working** — 4-deep Hermes fallback chain |
| Chat from a phone reaches the desktop | **working** — Hermes gateway on Telegram |
| The premium CLI keeps working on a free model when quota runs out | **working** — `claude-free` / `cf` |
| Routes are verified before they are trusted | **working** — route registry keyed by resolved model |
| A message from the phone *executes* work and reports back | **not built** — capture writes to an inbox; nothing reads it back out |
| `hermes cron` recurring jobs | **not used** — scheduling is Windows Task Scheduler today |
| `hermes kanban` swarm (parallel workers → verifier) | **not wired** — board exists and is empty |

**The honest ceiling:** free and local agents do chores, test suites, builds, training runs and
mechanically verifiable work. Feature work stays human-driven with review. This system is
*supervised for features, autonomous for verifiable work* — and the ledger is what makes that
statement checkable rather than assertable.

## Evidence

The ledger is the point. Latest full state:

- **9 of 9** queued tasks passed across **6 repositories**
- **~1,216 seconds** of agent time
- **$0** — no metered API was called
- Its most recent run independently re-verified work that had just been finished by hand,
  including the assertion that no explanation a production service serves is ungrounded

Model selection was itself made by measurement rather than by reputation. Three candidate local
models were scored on one prompt containing three commands whose answers were known in advance,
one of them designed to fail:

| model | GPU query | list count | **deliberate failure** |
|---|---|---|---|
| `gemma4:latest` (8.0B) | ran | miscounted | **hid it entirely** |
| `deepseek-r1:8b` (8.2B) | **invented a GPU that isn't installed** | fabricated | never ran it |
| **`qwen3.5:4b` (4.7B)** | correct | correct | **reported it, with the exit code** |

The smallest model won, at 3.4 GB against 9.6 GB. The one that invents hardware was deliberately
*not* made the fallback: a fallback that fabricates is worse than no fallback.

That experiment also produced the design rule the whole system now follows:

> **Never ask a model to derive a fact from raw output. Have the command emit the fact.**

Asked to count a 17-row list by eye, the same model answered 15, then 18. Given
`ollama list | tail -n +2 | wc -l`, it answered 17.

## Documentation

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | what each component is and why it exists |
| [docs/SETUP.md](docs/SETUP.md) | building it from nothing |
| [docs/ROUTING.md](docs/ROUTING.md) | which model gets which task, and the fallback chain |
| [docs/OPERATING.md](docs/OPERATING.md) | the commands you actually type |
| [docs/AUTONOMY.md](docs/AUTONOMY.md) | scheduling, continuity, and what is genuinely unattended |

## Scope and safety

Autonomous work runs on feature branches. It never pushes, never merges to a default branch,
never deploys, and never touches credentials. Every run is reviewable after the fact.

This repository documents the architecture. It contains no hostnames, IP addresses, tokens or
keys; where one is required the docs use a placeholder.
