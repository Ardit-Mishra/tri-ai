# Tri-AI — working agreement for Claude sessions

Tri-AI is an autonomous agentic framework: a background supervisor
(`src/daemon_supervisor.py`) runs a worker (`src/worker_daemon.py`) and a
Telegram control daemon (`src/interfaces/telegram_daemon.py`); tasks are
verify-gated by exit code; a strictly read-only dashboard
(`src/dashboard/jarvis_web.py`, `jarvis_terminal.py`) observes the system.

It is also the kernel that governs a five-project portfolio: `peptidemhc`,
`genomesight`, `biostudio`, `tri-ai` itself, and `genclarus`.

## Read this first, every session

`.planning/` is the project's durable memory — the transcript is not.

1. `.planning/STATE.md` — current position, what is accepted, what was just
   learned. Read before doing anything.
2. `.planning/ROADMAP.md` — phase order and success criteria.
3. `.planning/phases/phase-<n>-*.md` — the active phase's contract.

**Always write session findings back into `.planning/STATE.md`** before ending
a session or handing over. Record what changed, the exact verification command
and its result (test count, exit code, duration), and any root cause a fresh
session would otherwise re-derive. This is a standing instruction from the
operator, not a nicety.

## Non-negotiable boundaries

- **The dashboard is read-only.** Board access is SQLite `mode=ro` plus
  `PRAGMA query_only=ON`. Never add a mutating endpoint, never import `board`
  from a dashboard module. AST boundary tests enforce this — keep them passing.
- **Evidence over assertion.** Every claim of completion carries an exit code.
  "Tests pass" without the command and its output is not acceptable here.
- **Live daemons are not yours to restart casually.** The supervisor, worker,
  and Telegram daemons may be running detached. Read their state from
  `~/.tri-ai/logs/daemons.json`; do not kill or restart them unless the task
  says to. The dashboard server is the exception — it is safe to restart.
- **Never fabricate telemetry.** If a column is NULL or a stage did not run,
  render "not recorded" or "skipped" with the reason. A lifecycle stage that a
  `dir` workspace never reaches is `skipped`, not `pending`.

## Verification gate

```
python tests/run.py
```

Must exit 0. Current baseline: **324 tests**. A change that does not keep this
green is not done.

## Runtime layout

| Path | What |
|---|---|
| `~/.tri-ai/board.db` | authoritative task board (SQLite) |
| `~/.tri-ai/ledger.jsonl` | append-only verify evidence |
| `~/.tri-ai/logs/daemons.json` | supervisor state + child PIDs |
| `~/.tri-ai/runs/<task>/<run>/` | retained `agent.log`, `verify.log` |
| `~/.tri-ai/intake_policy.json` | Telegram intake workspace aliases |

## Choosing skills for work in this repo

Skills live in `~/.claude/skills`. Do not guess names — search the directory
and read the frontmatter `description`, which states when a skill applies.

Match to the work at hand:

- **Planning a phase or slice** → `writing-plans`, `brainstorming`
- **Implementing against a written plan** → `executing-plans`,
  `test-driven-development`
- **A bug, test failure, or surprising behaviour** → `systematic-debugging`
  (form a hypothesis and test it; do not shotgun fixes)
- **Before claiming anything is done** → `verification-before-completion`
- **Dashboard / HUD / canvas work** → `ui-ux-pro-max`, `ui-styling`,
  `emil-design-eng`, `improve-animations`, and the `htmlx-*` family for
  self-contained HTML deliverables
- **Agent, supervisor, or harness architecture** → `agentic-os`,
  `autonomous-loops`, `agent-harness-construction`, `agent-introspection-debugging`
- **Reviewing a diff** → the built-in `/code-review`, plus `receiving-code-review`

A skill is worth loading when it changes *how* you would do the work. If it
would only restate what you already intend, skip it.

## Things that have already bitten us

- Windows liveness cannot be probed with `os.kill(pid, 0)` (false down) **or**
  with a bare `OpenProcess` handle (false up — a terminated process keeps its
  process object while any handle is open). Use `GetExitCodeProcess` and
  require `STILL_ACTIVE` (259). See `_pid_alive` in `jarvis_terminal.py`.
- Windows `allow_reuse_address` lets a second process bind port 8080 silently
  while the first keeps serving. Always stop the old dashboard before starting
  a new one, or you will debug a stale template.
- The embedded dashboard template is a Python r-string. Editing it via shell
  heredocs mangles `\n` escapes — patch it with a Python script instead.
- A worker that dies mid-run leaves a stranded claim. `board.abort_dead_worker_claim`
  refuses a live worker and closes a proven-dead one. It is not a retry.
- Windows liveness has exactly one correct implementation here: `src/process_liveness.py`.
  Do not reach for `os.kill(pid, 0)` or a bare `OpenProcess` - both have already shipped
  wrong answers, in opposite directions.
- The dashboard template is JavaScript embedded in a Python r-string, and substring
  assertions pass on a script that does not parse. `tests/test_dashboard_template_syntax.py`
  runs `node --check` over it. Keep that gate, and open the page after a UI change -
  geometry defects (overlapping labels, colliding plates) are invisible to the suite.
