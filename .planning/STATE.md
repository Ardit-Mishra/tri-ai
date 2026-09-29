# State — pointer, not a copy

This worktree (`public-main`) had no `.planning/` at all, so any agent
following `CLAUDE.md` into it found nothing and worked blind. That is how two
lines of work diverged without either noticing.

`.planning/` is untracked, so **every worktree has its own copy and they
drift.** Do not treat this file as the record.

## Read these, in this order

1. `C:\Users\ardit\tri-ai\docs\HANDOFF_2026-09-28.md` — current state of
   play across both branches, and the branch-integration question that
   blocks everything else.
2. `C:\Users\ardit\worktrees\tri-ai-freellm-router\.planning\STATE.md` — the
   working state document (178 KB, the real one). The copy at
   `C:\Users\ardit\tri-ai\.planning\STATE.md` is an older 37 KB subset.
3. `AGENTS.md` in this directory — the five things that were true and are
   not any more.

## What this worktree holds

`public-main`, 35 commits past base `4c8e454`: the Cortex
(`src/dashboard/private_index.py`), `brain.capture_run`, and the whole
2026-09-28 session boundary — `b8df28d`, `f279dc9`, `966cf83`, `bb10e08`.

**All four are unpushed and must stay that way without Ardit's explicit
approval.** `origin` is a public GitHub repo and this branch indexes real
Google Drive and laptop paths.

1,292 tests, exit 0, as of 2026-09-28.

## Running the dashboard from here

```powershell
$env:KAYA_SESSION_TOKEN = (Get-Content "$env:USERPROFILE\.tri-ai\kaya-session-key.txt" -Raw).Trim()
python -m dashboard.kaya_web --host 127.0.0.1 --port 3026   # cwd: .\src
```

Setting that variable turns loopback trust **off** — the operator's own
browser signs in at `/login` too. That is deliberate: `tailscale serve`
proxies the tailnet from `127.0.0.1`, so any rule that trusted loopback
authorized the entire tailnet.

> **Corrected 2026-09-28 after an SSH survey of the desktop.** The claims that the desktop has never run a task, that it contributes 0 items, and that no SSH channel exists are all FALSE. The desktop holds 83 ledger entries (~56 its own), reports 486,925 nodes in the live Cortex, and runs Tri-AI as scheduled tasks serving a dashboard on :8081. Each machine has its own board, ledger and memory; the laptop's ledger was mistaken for the system's. Full correction: the CORRECTION section of docs/HANDOFF_2026-09-28.md in the primary checkout.
