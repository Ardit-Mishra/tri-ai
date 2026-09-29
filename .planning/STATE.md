# Tri-AI — state, 2026-09-29

This file is **tracked on `trunk`**, so it travels with the code. It used
to be untracked, which is how 178 KB of working notes came to exist on
exactly one laptop.

## One line, two repositories

`trunk` is the only line of development. Both machines are on it.

| | |
|---|---|
| `Ardit-Mishra/tri-ai` | **public** — the sealed demo, nothing else |
| `Ardit-Mishra/tri-ai-private` | **private** — real history, Cortex indexes, operator notes |

`RELEASE_SCOPE` declares a branch's audience; `scripts/release_guard.py`
enforces it at push time from `core.hooksPath` outside the repo, so it
applies on every branch. Fails closed in all three directions: unknown
scope reads private, unknown remote reads public, no declared private
remote means nothing private moves.

The three earlier lines are reconciled. The two `codex/freellm-router`
branches shared 273 commits and no files in common, so they merged
(`b8089c8`). `private/cortex` had an **unrelated history** — the scrubbed
release began a fresh root — but was a strict superset, so it became
`trunk`. Old branches are kept on the private remote as history.

## What runs, and what keeps it running

| | desktop `DESKTOP-JHQ7HJM` | laptop `Vivo-S14` |
|---|---|---|
| worker, supervisor, Telegram | scheduled task, boot + logon + 15 min | — |
| dashboard | `:8081`, scheduled task | Cortex `:3026`, scheduled task |
| OmniRoute | `:20129` | `:20128` |
| FreeLLMAPI | `:3001` | `:3001` |

Every service is loopback-only. The Cortex reaches a phone through
`tailscale serve`, behind a session token.

**A long-running daemon does not reload Python.** After changing `src/`,
stop the python processes and `Start-ScheduledTask`. A full evening's
fixes appeared not to work because of this.

## Cortex

870,777 indexed items across 9 reporting sources — desktop 486,925,
laptop 307,151, Obsidian 73,802, Drive 2,606, OmniRoute 51, Claude and
Codex 227, GitHub 10, Vercel and Render 3, Ollama 2. Sealed without a
session, verified over the real Tailnet URL.

## The build that proved it end to end

A stalled 17-task e-commerce graph on the desktop went from 3 done to
**15 done**, unattended, producing real files (`cart.html` 12,485 bytes,
`checkout.html` 14,467 bytes, backends, admin views) each past a
verifier's exit code.

Four defects had to be fixed to get there, and none were visible to any
test:

1. **A space in an account name.** `Ardit II` made the prompt's unquoted
   `cd` split in bash; every command failed and the agent stopped to ask a
   question nobody could answer. Fixed by `executor.shell_path()`.
2. **A stale daemon** running the pre-fix module from memory.
3. **`git diff --quiet` cannot see new files**, so any task whose job was
   to create something failed and had its work stashed away. Replaced with
   `python verify.py`, which attributes deliverables to *this run*.
4. **A cancelled parent strands its children silently.** `standstill.py`
   now reports it, and repeats while it persists.

## Open, needing Ardit rather than an agent

- **FreeLLMAPI has no API key.** Create one at `http://127.0.0.1:3001`,
  set `FREELLMAPI_API_KEY`. Not in the repo, a task prompt, or a chat
  message — that is the project's own rule.
- **Two tasks wait on a cancelled parent** and can never run. Cancel them
  or recreate the parent; it is a product decision, and they are now
  visible rather than silently parked.
- **Phone** is a declared Cortex source with no collector, because
  nothing runs on the phone yet.

## Deliberately not wired

`lane_select` is complete, tested, and off the execution path on purpose.
Measured selection compares lanes, and all 110 ledger runs used a single
lane, so there is nothing to compare. `model_routes` picks models today.
A test names the condition that reverses this: two or more lanes with
`MIN_SAMPLES` decided, non-environment runs for one role.

Environment failures no longer count against a lane — a lane is not bad
because the harness was broken.
