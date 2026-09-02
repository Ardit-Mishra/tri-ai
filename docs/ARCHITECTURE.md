# Architecture

Five components. Each does one thing, and each can fail without taking the others down.

```
                       ┌──────────────────────────────────────────┐
   phone               │  DESKTOP  (always on, USA)               │
   ┌────────┐          │                                          │
   │Telegram│──────────▶  Hermes gateway ("Bob")                  │
   └────────┘          │    └─ qwen3.5:4b  → gemma4 fallback      │
                       │  Ollama · 17 models                      │
                       │  RTX 3060, 12 GB VRAM                    │
                       │  Windows Task Scheduler ── durability    │
                       └───────────────▲──────────────────────────┘
                                       │ Tailscale (SSH, private net)
                       ┌───────────────┴──────────────────────────┐
                       │  LAPTOP  (build machine, travels)        │
                       │                                          │
                       │  Claude Code ──── judgment, orchestration│
                       │  OmniRoute :20128 ── model router        │
                       │  Ollama · 11 models                      │
                       │  Hermes CLI ── the delegated worker      │
                       │  the git repositories                    │
                       └──────────────────────────────────────────┘
```

---

## 1. Hermes — the worker

[Hermes Agent](https://hermes-agent.nousresearch.com) is a CLI coding agent. In this system it is
the thing that *does* delegated work. Two modes matter:

**One-shot.** `hermes -z "<prompt>"` runs a single task and exits. This is what the continuity
queue drives. It is not a daemon; nothing to keep alive, nothing to restart.

**Gateway.** `hermes gateway run` is a long-lived process that answers a chat surface — here,
Telegram. On this system it is called *Bob*. It is a completely separate thing from `-z`, and the
two never interact.

Hermes also ships `cron`, `kanban` (with a `swarm` verb) and `webhook` subcommands. They exist and
work; this system does not currently use them (see [AUTONOMY.md](AUTONOMY.md) for why).

### Two Hermes facts that cost real time to learn

1. **`cd` does not persist between commands** in Hermes' terminal tool. Its shell starts in the
   home directory each time. Every command it runs must carry its own `cd <repo> &&`, or the agent
   reports "not a git repository" from the wrong directory and the task *looks* broken when it is
   merely lost. The queue runner prepends this instruction to every prompt automatically.

2. **Hermes requires a model with ≥ 64,000 context.** This silently disqualifies otherwise good
   local models — `qwen3:14b` (40,960) and `qwen2.5-coder:14b` (32,768) are both out, despite
   fitting comfortably in 12 GB of VRAM. Check `ollama show <model>` before assuming a model is
   usable.

---

## 2. OmniRoute — the router

An LLM router (`npx omniroute`, config in `~/.omniroute`) serving an OpenAI-compatible API on
`localhost:20128`. It aggregates many providers behind one endpoint and, importantly, exposes
**semantic lanes** rather than only model names:

| lane | meaning |
|---|---|
| `auto/best-free` | best currently-working free model |
| `auto/coding:free` | free model biased toward code |
| `auto/cheap` | cheapest paid option |
| `auto/offline` | local only |

**Prefer lanes to model names.** Free hosted models are renamed and retired constantly — one
provider model in this system's chain went to `410 Gone` without warning, which meant the fallback
had silently not existed for weeks. A lane survives that churn; a hardcoded name does not.

Two operational notes: it answers `/models` but **404s on `/health`**, so a naive health probe
reports it down when it is fine; and `/` redirects (307) to `/dashboard`. Probe `/models`.

---

## 3. Ollama — local inference

Runs on both machines, listening on `0.0.0.0:11434` so each is reachable from the other over
Tailscale. The desktop carries the larger set (17 models) because it has the GPU.

**The VRAM ceiling is the real constraint.** The desktop's RTX 3060 has 12 GB, and Ollama keeps a
loaded chat model resident. Models above roughly 12 GB (`gemma4:31b` at 19 GB, `gpt-oss:20b` at
13 GB) partially offload to CPU and become unusably slow — they are not "a bit slower", they are a
different order of magnitude. Before dispatching a GPU training job, check what Ollama is holding:

```
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
```

---

## 4. Tailscale — the private network

A WireGuard mesh giving each machine a stable private address regardless of which network it is
on. This is what makes "laptop in another country" a non-event: `ssh desktop` works from a hotel
as it does from home, with no port forwarding and nothing exposed to the public internet.

**SSH-launched processes die with the session.** This is the single most expensive lesson in the
system's history: a long training run started over SSH is killed the moment the connection drops,
which — travelling — is constantly. Long work is registered as a **Windows scheduled task** and
triggered, never launched directly. See [AUTONOMY.md](AUTONOMY.md).

Note that the desktop's SSH shell is **PowerShell**, not `cmd` or `bash`. Commands with `&`, `>nul`
or bash conditionals will fail in confusing ways.

---

## 5. The continuity queue — the part that makes it a system

`src/run_queue.py`. A queue of tasks, each carrying a `verify` command whose exit code decides
pass or fail. It is the only component that is bespoke, and it is under 200 lines.

```
task ──▶ branch guard ──▶ hermes -z ──▶ VERIFY COMMAND ──▶ pass → ledger
                │                              │
                │ wrong branch                 │ non-zero
                ▼                              ▼
              skip                     git checkout -- .   (revert)
                                              │
                                              ▼
                                            ledger
```

Four properties, in order of importance:

1. **The verify command decides.** The agent's report is never evidence.
2. **A failed task is reverted** before the next one runs, so a broken attempt cannot contaminate
   what follows.
3. **The branch is checked first.** Wrong branch means skip, not edit-blind.
4. **Everything lands in `ledger.jsonl`** — agent exit, verify exit, duration, output tail.

It needs no daemon, no server and no session. `python run_queue.py` and walk away.

---

## Why not a "real" agent framework

Because the failure this system is designed around is not *insufficient orchestration*, it is
*plausible wrong answers accepted as correct*. A more elaborate graph of agents does not fix that;
an exit code does. Every piece here is chosen to keep the verification surface small enough to
trust: one bespoke file, and four off-the-shelf components each doing one job.
