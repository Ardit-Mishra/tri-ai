# Read this before touching Tri-AI

`C:\Users\ardit\AGENTS.md` still applies in full. This file is the current
state of play, so two agents do not build two systems.

**Full handoff: `C:\Users\ardit\tri-ai\docs\HANDOFF_2026-09-28.md`** — read it
before starting work. Written by Claude on 2026-09-28 from the live machine.

## Do not start work until the branch question is settled

Two active branches have diverged from base `4c8e454` (2026-09-22):

| branch | worktree | commits | holds |
|---|---|---|---|
| `codex/freellm-router` | `~/worktrees/tri-ai-freellm-router` | 194 | routing chain, capability catalog, radar, `decomposer.py`, semantic edges, learning loop, `cartographer.py` |
| `public-main` | `~/worktrees/tri-ai-public-release` | 35 | the Cortex (`src/dashboard/private_index.py`), `brain.capture_run`, the session boundary |

`private_index.py` does not exist on the first. `decomposer.py` does not
exist on the second. Both lines touch `src/dashboard/` and `src/worker.py`.
An integration order has to be agreed before either grows further.

## Five things that were true and are not any more

1. **"Loopback is the operator" is gone, and it was wrong in deployment.**
   `tailscale serve` proxies the tailnet to `http://127.0.0.1:3026`, so every
   remote visitor arrived as loopback and the sealed boundary authorized the
   whole tailnet — with 1,260 tests green. Fixed in `f279dc9`/`966cf83`/
   `bb10e08` on `public-main`. **The same trap applies to Cloudflare Tunnel**,
   which is the next thing planned in front of this dashboard.
2. **The dashboard requires a session.** Anything hitting `/api/snapshot`,
   `/events`, `/api/file-graph/scene` or `/artifact/...` must present the key
   when `KAYA_SESSION_TOKEN` is set. Loopback is no longer exempt then.
   Token comes from the environment, never an argument — a command line is
   readable by any local process here.
3. **`main` is stale** (84 commits, diverged 2026-09-02), and `origin/main`
   is a squashed 4-commit public release, not project history.
4. **Do not re-add `PRIVATE_MARKERS`** to
   `tests/test_public_release_boundary.py`. That list was the leak: it held
   the real hostname, home directory and both Tailnet addresses in the public
   repo it existed to protect. Identity is matched by shape now. The `tests/`
   exemption it forced is also gone, because the fixtures no longer identify
   anyone.
5. **There is no live channel to the desktop.** The `desktop/...` ref came
   from `git fetch C:/Users/ardit/desktop-tri-ai.bundle` — a hand-carried
   bundle, never a network remote. No SSH has ever been established.

## Standing rules that decide arguments

- **Run it before building for it.** Ardit's directive; it cancelled four
  queued work items. Prefer evidence over anticipation.
- Acceptance is a verifier's exit code. An agent's report is never evidence.
- Mutation-test every guard: break the production line, confirm a test fails.
  Four modules here were complete, tested, and called by nothing.
- Nothing is pushed without Ardit's explicit approval. `origin` is public.
- Do not disable SSH host-key checking to reach the desktop.

## Open questions awaiting Codex

In §7 of the handoff: merge direction; whether the desktop adapter runs *on*
the desktop or is pulled over SSH; and whether any Codex work sits
uncommitted from the session that hit the usage limit.

---

# CORRECTION — 2026-09-28, after surveying the desktop over SSH

Several claims above, inherited from `.planning/STATE.md` and repeated by
Claude without testing, are **false**. The desktop was surveyed directly on
2026-09-28. What is actually true:

| claim made above | reality |
|---|---|
| "no SSH has ever been established" | `~/.ssh/config` defines host `triai-desktop`; key auth works non-interactively. The account is **`Ardit II`** (with a space) — Claude guessed `ardit` and misread the failure. |
| "the desktop has never executed a task" | Its own ledger holds **83 entries, ~56 of them `DESKTOP-JHQ7HJM`**. It is the *primary* worker. |
| "the desktop contributes 0 items / the Cortex overstates itself" | The live Cortex reports **Desktop: 486,925 nodes, authorized** — the largest single source. |
| "796,684 is Drive + laptop only" | It is desktop 486,925 + laptop 307,151 + Drive 2,606 + Ollama 2. |
| "the file index agent is not authorized yet" | That string comes from `remote-desktop.json`, a **337-byte stale stub** written 2026-09-27 13:01, ten hours *before* the real index landed at 23:32. |
| "runtime relocation is pending" | The desktop already runs `Tri-AI Daemons` and `Tri-AI Dashboard` as scheduled tasks, and serves a dashboard on **:8081**. |

**Root cause of the errors:** Claude audited the *laptop's*
`~/.tri-ai/ledger.jsonl` and generalised to "the system". Each machine has its
own `board.db`, `ledger.jsonl`, `memory.db` and `runs/`. There is no shared
state.

## What the survey actually found

- **Two independent Tri-AI instances**, not one system with a misplaced
  runtime. Separate boards, ledgers, memories, run directories.
- **Both idle for two days.** The desktop's last ledger entry is 2026-09-26
  21:38 and its daemon logs stop at 21:34. The 15-minute scheduled task only
  re-detects "already running" (last result `0x800710E0`) against python
  processes started 2026-09-26 21:34. Nothing has flowed since.
- **Three-way branch divergence.** The desktop has its *own*
  `codex/freellm-router` at `6335105` ("Route planned tasks through admitted
  model pins", 273 commits). The laptop's tip `06c5e4e` **does not exist on
  the desktop at all** — `fatal: Not a valid object name`. Same branch name,
  no shared tip.
- **`private_index.py` is deployed loose**, not in git: the desktop runs it
  from `~/.tri-ai/cortex-agent/` (18,674 bytes, with
  `private_index_agent.py`). It produced a 289,762,711-byte `desktop.json` on
  2026-09-27 23:30, which reached the laptop at 23:32 — **by hand, not by
  automation.** That index will go stale with nothing to refresh it.
- **Seven sources genuinely empty**: Phone, Obsidian, GitHub, Vercel and
  Render, Claude and Codex, OmniRoute, FreeLLMAPI. The Cortex says so itself:
  *"Awaiting metadata index from 7 authorized source(s)."* This — not the
  desktop — is the real gap.
- The desktop's `:3001`, `:8080` and `:20128` are **netsh portproxies into
  WSL** (`172.23.251.44`), not native services.
- The desktop's `git worktree list` registers paths under `C:\Users\ardit`,
  which is the *laptop's* profile — the repo was copied across, carrying
  stale worktree metadata. Six entries are `prunable`.
- The laptop's dashboard is **not resilient** — it died when the desktop app
  quit. The desktop's survives, because it is a scheduled task.

## The real question, restated

Not "relocate the runtime to the desktop". It is already there, and has been
doing most of the work. The question is **which instance is canonical, and
what happens to the other one's board, ledger and memory** — plus which of
the three branch tips survives. Nothing should be merged or moved until that
is decided.
