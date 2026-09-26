# Project State

## Project Reference

See: `.planning/PROJECT.md` (audited 2026-09-10)

**Core value:** A task assigned once gets decomposed, executed in parallel by free local models, and verified by exit codes — without the expensive model staying in the loop.
**Current focus:** **Phase 6 is complete and accepted (2026-09-12).** All four
slices - terminal snapshot, loopback web surface, accepted-memory panels, and the
spatial HUD with its surface audit - are accepted on exit-code and live evidence.
Phase 5 remains operator-accepted, including the live phone path and the
confirmation of mobile draft `p_52ad147623592edf` into ready task `t_a17464db`;
the configured-task intake contract is the accepted product decision and does not
invoke the planner. The next phase is 7 (Planner Integration and Trusted
Distributed Delegation), whose existing candidate violates the verify gate and
must not be merged.

`genclarus` is now a registered governed workspace, and the five-project portfolio
is documented in `.planning/PORTFOLIO_STRATEGY.md`.
Route admission is complete, but executor routing remains intentionally disabled
until an operator-configured Hermes profile has real measured evidence.

**Approved future direction:** `.planning/research/capability-expansion-design.md`
defines local verified promotion, RAG, measured routing with optional
operator-configured cloud/free lanes and desktop-local failover, semantic memory,
and constrained self-evolution. These features are approved. The operator
authorized sequential implementation of Phase 5A-C on 2026-09-11. The active
plan is .planning/phases/phase-5-control-worker-routing-plan.md. Each slice
remains separately verified and committed; no live credential is inspected and
no external proxy request is made during implementation.

## Current Position

Phase: 6 of 9 COMPLETE (JARVIS Dashboard & Spatial HUD)
Next: Phase 7 (GenClarus Orchestration under Tri-AI) - prove the kernel
governs a real child project end to end, with honest per-task verification
Plan: .planning/phases/phase-6-jarvis-dashboard-plan.md
Status: Phases 1-6 are complete. Phase 6 closed out on 2026-09-13 with all five
slices accepted - Slice 5 added artifact capture, completion delivery, and
artifact serving, closing the gap where a finished task told nobody. The
canonical verification is `python tests/run.py` -> **359 tests, exit 0,
204.436s**. The fleet is live and healthy, the stranded claim from
the 2026-09-12 incident is closed, and its root cause is fixed rather than worked
around.

**Phase 7 planning update (2026-09-12):**
`.planning/phases/phase-7-plan.md` is a new draft, not implementation progress.
It replaces the unsafe remote-daemon candidate with four reviewable slices:
read-only planner assistance, fenced remote leases, desktop-owned candidate
result verification, and a local two-node evidence exercise. A remote report or
diff cannot complete a task; the desktop must materialize the task worktree and
run the existing board-recorded verifier. No live Tailscale transport, remote
credential, auto-commit, merge, push, or offload work is authorized by this
plan. Next atomic action: independent plan review, then Slice 1 only.

## 2026-09-12 Master Directive - Completed

**Part 1, fleet recovery - root cause found in the logs, not inferred.** The
Telegram child lost an HTTPS request and exited 1. `daemon_supervisor.supervise`
was written to return on the first child exit and never retry, so it stopped the
worker underneath run 5 of `t_69cc6245`, which had already claimed the task.
`supervisor.stderr.log` was empty because that path is a plain `return`, not an
exception - which is exactly why the earlier session could not diagnose it.

- The stranded claim was closed with `board.abort_dead_worker_claim`: task and run
  5 are `cancelled`, claim fields clear, `aborted` operator-recovery event id 16,
  plus an appended diagnostic ledger line naming the clean-tree precheck, the
  supervisor's child-exit return, and the dead PID 38148.
- `supervise` now restarts a dead child with exponential backoff (2s doubling to
  60s), a rolling restart budget (8/hour) after which it stops the fleet rather
  than spinning on an unfixable child, and a healthy-uptime backoff reset. PIDs are
  republished **by name**, so a child inside its backoff reports no PID rather than
  a stale one - a positional reader would have attributed the surviving daemon's
  PID to the dead one, and the dashboard would have shown a dead daemon as up.
- `TelegramDaemon.run_forever` no longer treats one lost HTTPS request as fatal:
  transport failures retry with the same backoff shape under a bounded budget, so a
  network blip costs no process restart while a revoked token still exits non-zero.
- **`src/process_liveness.py` now owns Windows liveness for both the supervisor and
  the dashboard.** The supervisor's own `_is_alive` used `os.kill(pid, 0)`, which
  was wrong in two opposite ways, both reproduced locally before being fixed: an
  exited process whose handle is still open raises nothing and read as **alive**,
  and a PID that never existed raises a bare `OSError` (WinError 87) that the guard
  did not catch, so it escaped `main()`'s startup check as a traceback rather than a
  diagnostic. This is the third and fourth Windows process-lifetime assumption to be
  wrong in this project.
- Fleet restarted and verified: supervisor 37492, worker 25108, telegram 3124, all
  alive by direct probe, `restarts: 0`.

**Parts 2 and 3, dashboard audit and semantic nodes.** The duplicate `render()` is
deleted. SSE reconnects on exponential backoff (1s doubling to 30s) behind a
`STREAM LIVE` / `RECONNECTING` badge carrying a complete atomic status message, kept
outside the container `render()` clears each snapshot. Raw epoch timestamps became
relative timers with the absolute value retained as the element `title`. Nodes lead
with intent - task title primary, hex id demoted, workspace as a path-derived tag
and tint, so a project added later is tagged without a palette edit - and hover or
tap raises a micro-card with prompt, workspace, branch, phase and runtime.

**The lesson worth keeping from this slice:** a green suite is not a rendered
surface. Three defects here were invisible to the tests. An edit produced two `else`
clauses in one `if` and the page served an empty HUD while every substring assertion
still passed; name plates drawn rightward hid a left-hand node's plate under its
neighbour; and two workspace hull labels landed on the same line at phone width. The
first is now structurally impossible to ship - `tests/test_dashboard_template_syntax.py`
parses the embedded script with `node --check` (skipping explicitly, never quietly,
when Node is absent). The other two were found only by opening the page.

**Part 4, portfolio.** All five projects located and their git state read. One
correction that matters: **genclarus's checkout is `C:\Users\ardit\projects\genelens`**
- the directory was never renamed. It is now registered in
`~/.tri-ai/intake_policy.json` as alias `genclarus` with verify profile
`genclarus-vitest` (`npm test`, 600s), measured before registration at 36 files /
333 tests / exit 0 / 4.17s. `default_workspace` remains `tri-ai`.
`.planning/PORTFOLIO_STRATEGY.md` records each project's path, branch, verify gate
and remaining work, and marks which gates were executed here versus read from CI.

**Part 5, network reach.** The dashboard binds `127.0.0.1` **and** the Tailscale
address `100.118.189.88`, as two servers rather than one wildcard bind - `0.0.0.0`
would have published the HUD on the home Wi-Fi as well. Proven by probe: loopback
reachable, Tailscale reachable, `192.168.0.36:8080` refused. Non-loopback binding
requires `--allow-non-loopback`, so a typo or a port argument can never widen it.

**Known follow-up, not a blocker:** the `genclarus` working tree has one
uncommitted entry, so Tri-AI runs against that workspace will fail the clean-tree
precheck until it is staged or stashed - the same precheck that stranded
`t_69cc6245`.

**Daemon incident and evidence-preserving recovery:** task `t_a17464db` entered
`running`, then its clean-tree precheck retained `agent.log` and `verify.log`
showing `working tree not clean (4 entries)`. The supervisor's retained state
recorded exit code 1 and the recorded worker PID was dead. The old Telegram
line established only a generic HTTPS failure, so it cannot prove the first
child's exact failure. The worker had no Windows `SIGBREAK` handler, leaving a
shutdown hole if the supervisor sent CTRL_BREAK while cleanup was in flight.
`board.abort_dead_worker_claim` now refuses a live or raced worker, and only
then atomically cancels the stranded task/run and appends an `aborted`
operator-recovery board event. The live recovery used that primitive: task and
run 4 are `cancelled`, the claim fields are clear, and retained run logs remain
in place. `worker_daemon` now registers `SIGBREAK`; future Telegram HTTPS
diagnostics retain the exception class without exposing response content.

**Phase 6 Slice 1 accepted locally:** `src/dashboard/jarvis_terminal.py` reads
the board through SQLite `mode=ro` plus `query_only`, tails bounded ledger
evidence, and renders task/dependency state, activated-rule count, and
supervisor health with Rich. It has no board API, network, credential, or
process-spawn path. Focused proof: `python -m unittest tests.test_dashboard`
→ **7 tests, exit 0, 3.026s**. Full suite: `python tests/run.py` -> **253
tests, exit 0, 150.571s**.

**The agent could not act, and the model was not why, 2026-09-14.** Two tasks
blocked on the desktop with the agent printing HTML to stdout instead of writing
a file - the same shape that blocked `t_c5374def` on the laptop. The obvious
reading was model capability: the desktop's primary was **`qwen3.5:4b`**, far
too small for reliable tool use, with `gemma4:latest` behind it.

Setting `devstral:24b` as primary did **not** fix it. `t_51ce35ea` blocked
twice more, and the usage file named the real cause:

    model: devstral:24b   completed: true   failed: false   api_calls: 1

**One API call.** A tool-using turn makes many - each tool call is a round trip,
and the laptop's working runs show 13, 26 and 49. One means the model answered
once and invoked nothing, which is exactly what the log says: *"I'm executing
the necessary steps now"*, a code block, and no file. The agent was not failing
to use its tools; it had none.

The laptop declares `toolsets: [hermes-cli, web]`. The desktop had **no
toolsets key at all**, and whatever hermes defaults to does not include the
terminal. Adding it fixed the run on the first attempt:

    model devstral:24b · api_calls 2 · verify: devstral-proof.html parses
    (566 bytes) · filed under runs/2026-09-14-devstral-proof-.../ · done/verified

*What this cost, and the lesson.* Roughly an hour went into model selection -
pulling devstral, comparing local models, writing a probe - for a problem that
was one missing config key. The `api_calls: 1` was in the usage file from the
first failed run and would have pointed straight at it. **The evidence was
there before the theory was.**

The probe also mismeasured: it passed `-m <model>` to override the model per
run, and hermes ignored it - Ollama had `qwen3.5:4b` loaded throughout, so it
was testing one model five times. `-m` does not override the configured model
in this setup; only the config does. Worth knowing before trusting any future
per-run model comparison.

*Desktop model chain, local only* (the laptop's router times out from here, and
depending on it would undo the migration): `devstral:24b` ->
`qwen2.5-coder:14b` -> `gemma4:31b`.

*Separately, a documentation claim that outran its evidence - and then did not.*
Three modules - `executor.py`, `ledger.py`, `worker.py` - state the agent "runs
under `HERMES_YOLO_MODE=1` with every shell approval auto-granted", and nothing
in Tri-AI sets it: not the worker, not `agent_env()`, and it is unset in
process, user and machine scope. **RESOLVED 2026-09-14: the claim is correct and
the search was looking in the wrong repository.** `hermes_cli/oneshot.py:255-256`
sets both `HERMES_YOLO_MODE=1` and `HERMES_ACCEPT_HOOKS=1` itself, for every
`-z` call, with the reason in the comment above it: "Auto-approve any shell /
tool approvals. Non-interactive by definition - a prompt would hang forever."
Tri-AI uses `-z` exclusively, so the docstrings describe what actually happens.
The residual risk they discuss is real and unchanged: a YOLO agent on this
account can reach anything this user can.

**Tri-AI moved to the desktop, 2026-09-14.** The laptop was always the wrong
host: `VIVO-S14` is a notebook, so closing the lid killed the bot. It now runs on
`DESKTOP-JHQ7HJM` / `100.67.149.86` - 16 cores, 32 GB, uptime 8 days.

*Access.* Tailscale supplies the network path but not authentication, and
Tailscale SSH's server side is not supported on Windows, so a key was needed.
`~/.ssh/triai_desktop` (private half stays on the laptop). Two mistakes on the
way: the key first went to `administrators_authorized_keys`, which only applies
to members of the Administrators group, and the account is **`Ardit II`** - with
a space - which is why every username guess failed.

*What moved.* `tri-ai`, `.tri-ai` (board, ledger, runs, config) and
`tri-ai-sandbox`, ~5.3 MB packed. **`career-ops` was deliberately excluded** -
133 MB of personal job-search data that stays local.

*What broke, and why it is worth writing down.*

1. **Hermes version drift.** The desktop had v0.18.2, whose `write_txn` lacks
   `allow_nested`: 143 errors. `hermes update` took it to **v0.21.2**, which was
   worse - that release removes `_record_task_failure`, `_pid_alive` and
   `_set_worker_pid`. **Tri-AI calls six private kernel functions** (`kb._*`),
   and three vanished in one minor release. The desktop is now pinned to the
   laptop's exact commit `23a64a97`, where all six exist. That commit is carried
   locally by hermes' updater and `_record_task_failure` is **absent from
   upstream `ee35a462`** - so the kernel depends on a locally-patched function
   that upstream does not have. That is a real fragility, not a migration
   artifact, and adapting to the public API is work that needs its own review.
2. **Python environment.** Tri-AI imports `hermes_cli` under the *system*
   interpreter, so the desktop needed the laptop's dependency set. Both are
   Python 3.14.3. `pip install -r` is all-or-nothing and aborted on a package
   needing MSVC, so installs run per-package with `--only-binary=:all:`:
   **167 of 174**, the 7 skipped all career-ops scrapers Tri-AI never imports.
3. **My own repath script reported a false negative.** It guarded with
   `if OLD in raw_text` using the unescaped path while JSON stores
   `C:\Users\ardit`, so it skipped `intake_policy.json` and said "no laptop
   paths" - worse than failing. The telegram daemon then refused to start every
   few seconds until the supervisor exhausted its restart budget. Fixed by
   parsing the JSON rather than pattern-matching its text.
4. **PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM**, which the
   daemon's plain-UTF-8 read rejects: `cannot read intake policy:
   JSONDecodeError`. Rewritten from Python.

*Verified.* `python tests/run.py` on the desktop -> **471 tests, exit 0** - the
same count as the laptop, which is how environment parity was confirmed rather
than assumed. Scheduled task **"Tri-AI Daemons"**, boot + logon triggers,
highest run level, S4U so it needs no interactive session. All three daemons
alive; the laptop's are stopped, so there is exactly one Telegram poller.

*Open.* `genclarus` is dropped from the intake policy - genelens is not on the
desktop yet, and a workspace alias pointing at a missing directory blocks the
telegram daemon outright. Restore it after copying genelens and running
`npm install` there. And the proving task `t_3879ea0f` **blocked**: the agent
printed HTML to stdout instead of writing the file, twice - the same
`auto/best-coding` failure that blocked `t_c5374def` on the laptop. The kernel,
verifier, gates and daemons are all correct on the desktop; the model is not.

**Artifacts filed, and a third review pass, 2026-09-14.** The operator's
complaint: *"i don't have time to sift through the files... what if the tasks
required multiple artifacts"*. Ad-hoc work landed flat in the workspace root, so
the sandbox held `celestial.html`, `clock/`, `clock.html` and
`telegram-loop-test.html` side by side with nothing saying which run made what.

Each run now gets `runs/YYYY-MM-DD-slug/`, named from the operator's own words,
holding exactly what it produced with nesting intact plus a `_run.html` landing
page; `deliverables.html` is the gallery. The slug comes from `TRIAI_TASK_PROMPT`,
which `executor.verify_env` now passes to the verify command. Existing files were
backfilled from the board's artifact records. **This is workspace policy, not
kernel policy** - right for a scratch workspace, wrong for genelens.

*Building it caused three defects, each caught only after it shipped.*

1. **It destroyed a deliverable.** The gallery lived at root `index.html` - the
   likeliest filename for "make me a web page" - so an agent's own `index.html`
   was excluded from its run as furniture and then overwritten. Task
   `t_fd232d1c` lost a file. Naming the per-run page `index.html` repeated the
   collision one level down. Both names are now reserved.
2. **It stranded every artifact record.** Artifacts are captured at agent exit;
   the verifier then moves them. Eight of eight rows pointed at nothing, which
   breaks artifact serving *and* Telegram delivery - the operator's main
   channel. `_relocate_moved_artifacts` reconciles location only, refusing
   ambiguity (5 of 8 repaired; the 3 refused genuinely do not exist).
3. **It let a rejected run be archived.** The declared-artifact gate ran after
   verify, so run 38 of `t_80dab89f` had its scattered output filed, committed
   and listed in the gallery before the gate failed it - and run 39 *failed
   despite producing all three declared files*, because the verifier had moved
   them before the gate looked. The gate now runs before verify (`8d25f26`).

**Codex third pass - four findings, all fixed and re-verified against its own
repros.**

- *BLOCKER*: the verifier staged and committed before its cleanliness check, so
  a run about to be rejected was archived first. Refusal now precedes any move
  or commit; a rejected run leaves no commit and no half-filed directory.
- *HIGH*: the heartbeat's workspace scan capped at 4000 entries with `os.walk`,
  whose order is undefined - 4001 stale files ahead of the agent's directory and
  a **healthy** run earns no beat, then gets reclaimed mid-flight. Repro:
  `fresh_seen False`. Replaced with "did anything change since T", exiting on
  the first newer file and bounded by time. Same repro: `True` in 0.165s; full
  walk of genelens 0.499s.
- *HIGH*: an artifact beyond unambiguous repair was dropped from delivery
  silently. The card now names how many could not be attached.
- *MEDIUM*: `TRIAI_TASK_PROMPT` could change an operator verifier's behaviour -
  `safe & echo x > file` created the file. Shell-active characters are stripped.
  Verified by side effect, not string match.

**The gate held; the model did not.** `t_c5374def` asked for three named files
and is **blocked** after two failures: run 40 scattered its output, run 41
produced nothing and emitted a truncated `"Understood your"`. Both were rejected
before verify ran, the workspace stayed clean and the gallery uncontaminated.
That is the system working - and `auto/best-coding` failing a plain
three-file request twice is its own finding.

**Latest verification:** `python tests/run.py` -> **471 tests, exit 0**.

**Still open:** Tri-AI runs on the laptop (`VIVO-S14`, chassis 10, battery
present), so closing the lid kills the bot. The always-on host is
`desktop-jhq7hjm` / `100.67.149.86`, already serving Ollama. SSH is open there
but keyless and SMB is closed, so the migration is blocked on one manual step:
installing `~/.ssh/triai_desktop.pub` into `administrators_authorized_keys`.

**The Telegram loop is proven end to end, and it found two defects doing it,
2026-09-14.** The operator sent a task from their phone: *"Build a single home
page that shows a live analog clock with a sweeping second hand, dark theme, no
external libraries."*

*Inbound works.* Staged `p_ca8cc015d7e63374` -> workspace `tri-ai-sandbox` ->
confirmed 7s later -> task `t_a180b31e`. Intake, workspace quick-select and
`/confirm` all fired. Outbound was proven separately: message 112 delivered, and
the completion card for `t_c13940be` landed as message 115.

*Then it failed, and the failure was mine.* The agent read "home page" as a site
and wrote `clock/index.html`, `clock/script.js`, `clock/style.css`.
`git status --porcelain` reports that as a single line - `?? clock/` - because
git collapses untracked directories unless asked not to. The verifier skipped
the entry (it names no file), concluded the run produced nothing, and exited 1.
The worker duly reverted; `git stash -u` saved the files but could not remove
the directory on Windows (`failed to remove clock/: Permission denied`); the
post-revert tree read dirty; the workspace was quarantined and the worker
stopped. **One missing flag turned a correct-looking run into a halted kernel.**

Fixed in tri-ai `864b39e` and sandbox `98535b0` with
`--untracked-files=all`, added to `GIT_ALLOWED_FORMS` as its own exact form so
nothing can fall back to the collapsing one. Cleanliness assertions keep plain
porcelain - they test for dirt, not enumeration.

*A second gate was hollow underneath it.* With enumeration fixed, the same run
**passed** - on three EMPTY files. The agent had created placeholders and given
up (*"I'm unable to create the analog clock for you right now... The commands
are failing due to syntax errors"*), and an empty string is valid input to
`HTMLParser`, so existence and parseability both passed on nothing. The verifier
now requires content. Note the correct outcome for run 30 was always FAIL; the
verifier was reaching it for the wrong reason.

*A diagnosis of mine was wrong.* I told the operator the claim lease had expired
and killed the agent mid-run. Codex disproved it: `run_once` registers
`worker_pid` after claim and the kernel *extends* an expired claim when the
host-local PID is alive (`reclaimed 0 / expires_extended True`). Run 30 was
reclaimed because the worker had already stopped itself after quarantining. The
real gap is the inverse: `last_heartbeat_at` is only ever set to NULL, and the
kernel treats NULL as never-stale, so a live-but-wedged worker is extendable
forever. **Unfixed, and the right fix is a heartbeat that represents real
progress rather than a blind timer.**

**Codex review of the batch - four findings, every one with a repro.**

1. *BLOCKER, now closed (`f37020c`).* The runtime gate from `cf4ddbc` read
   `runtime_failed` truthily, so it only caught a runtime that ran and recorded
   failure. When hermes cannot spawn at all, `run_agent` returns
   `AgentResult(1, msg)` with no usage file and `runtime_failed=None` - which
   walked into verify and was accepted on the previous run's output
   (`attempt passed / task_status done / verify_calls 1`). None alone still
   means nothing; None **plus a non-zero exit** now fires the gate
   (`environment_backoff / ready / verify_calls 0`).
   Codex also caught that my own test proved the wrong thing:
   `test_no_usage_record_leaves_the_verify_command_as_the_only_gate` passed
   `agent_writes=[...]`, demonstrating compatibility for a completed run and
   never exercising the dangerous case. Four tests added for what it missed.
2. *HIGH, now closed (`f37020c`).* Artifact enumeration could fan out into
   unbounded uploads: the card truncated what it *named* at 6, but
   `deliverable_documents` returned every match, so 250 small pages queued 250
   `sendDocument` calls. Capped at `MAX_DOCUMENTS_SENT`.
3. *MEDIUM, now closed (sandbox `f854d25`).* `MIN_DELIVERABLE_BYTES` applied per
   file would reject `body{margin:0}` (14 bytes) beside a real `index.html`. The
   bar belongs to the run: at least one produced file must carry content.
4. *MEDIUM, open.* The heartbeat gap above.

Codex confirmed the allowlist widening is safe (only the exact form is
admitted), that remaining plain-porcelain uses are all cleanliness assertions,
and that environment backoff is bounded when the signal exists
(`backoff, backoff, backoff, exhausted` -> `blocked`).

*The re-run succeeded.* `t_a180b31e` run 33: `clock.html`, 5,569 bytes, one
artifact recorded, delivered as message 121. Verified independently of the
agent's report - served and driven in a browser, the digital readout advanced
`1:02:58` -> `1:03:02` and the second hand swept with it. Zero external
dependencies.

**Latest verification:** `python tests/run.py` -> **437 tests, exit 0**.

**GitHub is not a model source for this project, 2026-09-13.** Checked directly
rather than assumed, because the config still carried `github/*` aliases.

- **GitHub Models is gone.** `models.github.ai/inference` returns HTTP 410
  `github_models_retirement_brownout`. GitHub retired the service on
  **2026-07-30** - catalog, playground, inference API and BYOK endpoints - with
  no grace period and all keys invalidated. Every "free LLM API" list still
  recommending it is stale.
- **Copilot Pro via the Student Pack caps at 300 premium requests/month.**
  Tri-AI tasks take up to 49 API calls each (1-49 observed, most substantial
  tasks 8-26), so that budget is **tens of tasks a month at best**. Fine in an
  IDE; useless for an unattended worker. Note the 300-request framing is
  itself dated - GitHub's current Copilot docs describe credits rather than a
  flat premium-request count, so treat the number as an order of magnitude.
- The `github/*` router aliases do not resolve: `github/claude-sonnet-5` fell
  through to `qwen3.5:4b` when probed.

**`devstral:24b` pulled to the Tailscale box** (14.3 GB, 307s) and verified
answering. It is now fallback 2, above `qwen2.5-coder:14b`, because it is the
only local coder with a published agentic score (46.8% SWE-Bench Verified) and
tool-call formatting an agent loop can rely on. Chain is now
`auto/best-coding` -> `auto/smart` -> `devstral:24b` -> `qwen2.5-coder:14b` ->
local GGUF, re-verified serving `auto/best-coding` after the edit.

**PAT exposure assessment.** The token is **live** (HTTP 200 as `Ardit-Mishra`,
fine-grained). It does not appear in `tri-ai` or `genelens` git history, and
`AppData\Local` is outside OneDrive - so the disclosure is local-only. Its sole
consumer is hermes' `github_copilot` MCP entry (`api.githubcopilot.com/mcp/` -
repo tools, not inference). `gh` and git use a separate `gho_` keyring token,
and `executor.agent_env()` strips `GH_TOKEN`/`GITHUB_TOKEN` from agent children,
so no Tri-AI execution path touches it. Revoking costs only the GitHub MCP tools
inside hermes sessions.

**The model chain was broken end to end, 2026-09-13.** Investigating the run-26
404 turned up a larger finding: **every Tri-AI task ever run executed on the
fallback chain, never on the configured model.**

*What was wrong, in `~/AppData/Local/hermes/config.yaml`.*

| Slot | Was | Status |
|---|---|---|
| primary | `glm-5.3-flash:cloud` @ `127.0.0.1:11434` | **dead** - Ollama Cloud replies "this model requires a subscription or usage credits" |
| fallback 1 | `auto/best-free` @ `:20128` | worked, but is explicitly the *cheapest free* routing pool |
| fallback 2 | `qwen3.5:4b` @ `100.67.149.86` | a 4B model, on a box that also serves 14B/20B/31B |
| fallback 3 | `gemma4:e4b` @ `localhost:11434` | **wrong host** - that model lives on the Tailscale box; local Ollama serves one GGUF. This is the entry that 404'd and produced run 26 |

Every usage file written *before the repair* records `auto/best-free`, which is
fallback 1 - direct evidence that the primary failed on every turn and nobody
noticed, because failover is silent by design. Stated as a present-tense claim
about the whole directory this is now false and review caught it: a scan of
`~/.tri-ai/runs/*/*/usage.json` today gives `auto/best-free` 7,
`auto/best-coding` 4, one failed run with no model. The four are runs 28 and
later, which is the repair showing up in the evidence.

*Replacement, each probed before it was written in.* Primary
`auto/best-coding` @ `:20128`, then `auto/smart`, `qwen2.5-coder:14b` and
`gemma4:31b` on the Tailscale box, then the one GGUF that is guaranteed present
locally - so the last resort can never 404 again. Probing also showed the
`github/*` direct routes are dead: `github/claude-sonnet-5` fell through to
`qwen3.5:4b`, consistent with the stale Copilot PAT.

*Verified.* A one-shot probe served `auto/best-coding` with `completed=true`,
and real task `t_628344fa` (run 28, 395.57s) carries
`model=auto/best-coding, provider=custom, model_source=usage_file` in the
ledger - the first run in the project's history not served by `auto/best-free`.

**The PAT is more exposed than the scrub suggested, 2026-09-14.** Review flagged
remaining literals; a content scan (fingerprinting each match rather than
grepping paths) sharpened it:

| File | Holds the live token? |
|---|---|
| `.env` | yes - **by design**, this is where it now lives |
| `.hermes_history` | **yes** - shell history |
| `state.db` | **yes**, plus a second, different PAT-shaped literal |
| `config.yaml` and every `config.yaml.bak*` | clean |

Two of the three backups review named (`.env.bak.20260821_214141`,
`config.yaml.bak.20260708_031721`) are in fact clean - a path-level scan cannot
tell a live token from an old one. But `.hermes_history` and `state.db` do hold
it, `hermes backup` zips the whole profile directory, and the token is confirmed
live (HTTP 200 as `Ardit-Mishra`). **This upgrades the recommendation from
"rotate when convenient" to "rotate": scrubbing a SQLite session store and a
history file is not worth attempting in place.** Interpolation itself was
verified working, so a new token dropped into `.env` needs no config change.

*Credential moved out of plaintext.* The GitHub PAT sat literally in
`config.yaml` under `mcp_servers.github_copilot.headers.Authorization`. It is
now `Bearer ${MCP_GITHUB_COPILOT_API_KEY}` with the value in the profile `.env`,
which is hermes' own convention (`hermes_cli/mcp_config.py:_env_key_for_server`).
Three backup copies of `config.yaml` also carried it and were scrubbed. **The
token still needs revoking and reissuing on GitHub** - it sat in plaintext and
in backups, so it must be treated as disclosed. That is an operator action.

*Workload note for any future model decision.* Real Tri-AI tasks consume
**40K to 2.27M tokens each**, across 1-49 API calls; the substantial ones sit
between ~200K and 2.3M. That range disqualifies
most free API tiers outright (Groq's free lane is ~200K tokens/day - less than a
single task). Only Mistral's Experiment tier (1B tokens/month) and Google AI
Studio (no daily token cap, 1M TPM, 1,500 RPD) can carry this volume, and both
use free-tier traffic for training - which matters because tasks send the
contents of `~/projects/genelens` and `~/tri-ai`. The Tailscale Ollama box is
the only option where private repo contents never leave the network.

**A task reached `done` having built nothing, 2026-09-13.** Run 26 of
`t_669fec6c` (the sandbox toggle) is the worst failure this project has produced,
because it is the exact failure the project exists to prevent, and it passed
through two gates that were each supposed to catch it.

*What happened.* Hermes' `fallback_model` chain fell through to its third entry,
`gemma4:e4b` on the local Ollama at `localhost:11434`. That model is not
installed there - `/api/tags` lists exactly one model, a gemma-4-12B GGUF - so
the provider returned 404. Hermes printed `API call failed after 3 retries:
HTTP 404` as its **final response** and exited **0**. The worker read the exit
code, saw success, and ran the verify command. The sandbox verifier of the day
asked only whether *a* deliverable existed anywhere in the workspace;
`celestial.html` did, left by run 24, so it exited 0 and the board recorded
`status=done, result=verified`. Zero artifacts were captured for the run - the
only honest signal anywhere in the record - and nothing consumed it.

*Why the verifier was hollow.* It had been rewritten hours earlier, in this same
session, to archive deliverables so the next task would not be stranded behind a
dirty tree. That fix committed `celestial.html`, and from that moment the
existence check was satisfied forever, by any run, including one where no agent
ran. The gate was a property of the repository, not of the run. This is the same
class of defect the review seat was set up to catch, introduced while fixing
something else and not re-examined.

*Both halves are closed.*

1. `src/executor.py` - `run_agent` now reads `failed` from the runtime's usage
   file into `AgentResult.runtime_failed`. `None` when no such field exists:
   absence of a record is not a record of success. Run 26's usage file carried
   `"failed": true, "model": null`; run 27's carried `"failed": false,
   "model": "auto/best-free"`, 26 api_calls, 1.5M tokens.
2. `src/worker.py` - invariant 6: **a verify command is never run over a turn
   that did not happen.** When the runtime reports failure the workspace is
   restored and the task backs off as an *environment* fault, without verify
   being invoked. Environment, not logic: a router selecting a model the
   provider does not serve is an outage, and routing it through the logic
   breaker would blame a task for an infrastructure failure. Both non-pass
   exits now share `_restore_workspace` so the revert and its post-revert
   assertion cannot drift.
3. `~/tri-ai-sandbox/verify.py` (b9e2ef6) - gates on `git status --porcelain`
   instead of file existence. The worker guarantees a clean tree at claim, so
   the dirty set at verify time is exactly this run's work. A clean tree now
   fails, however many files sit in the repo.

*Verification.* `tests/run.py` -> **424 tests, exit 0, 194.289s** (was 412).
New suite `tests/test_agent_runtime_gate.py`, 12 tests, mutation-checked:
disabling the gate fails 5 and errors 1, including the one asserting verify was
never invoked. The sandbox verifier was exercised four ways - clean tree with
`celestial.html` present (the run-26 scenario) exits 1; new file exits 0 and
archives; modified file exits 0 and archives; immediate re-run exits 1.

*The re-run.* Task re-filed as `t_3d03f995`; `t_669fec6c` carries a comment
retracting its acceptance. Run 27: `auto/best-free`, 636.14s, agent_exit 0,
verify exit 0, **one artifact recorded** (`celestial.html` M 36,361 bytes, up
from 27,408) against run 26's zero. The verify log reads `verify: this run
produced celestial.html` - the run-scoped gate naming the file the run changed.
Confirmed independently of the agent's report: `role="switch"` present, and
driving the page in a browser flips the label DARK SPACE -> NEBULA VIOLET with
the scene animating to violet.

*Still open, and operator-owned.* `~/AppData/Local/hermes/config.yaml` still
lists `gemma4:e4b` as the last fallback, pointing at a local Ollama that does
not serve it, so every fall-through to that entry 404s. The gate now turns that
into a retryable environment backoff instead of a false pass, but the entry is
still dead. Same file exposes a GitHub PAT in plaintext under
`mcp_servers.github_copilot.headers.Authorization`; it should be rotated and
moved to an env reference.

**Review seat exercised on tonight's batch, 2026-09-13.** Codex reviewed the
17-commit batch under `~/CODEX-REVIEWER-BRIEF.md`. Two findings, both with repro
evidence, both acted on.

1. *Delivery was documented as exactly-once and is not.* `publish_completions`
   and `publish_progress` send first and record the receipt second, so a crash
   between the two re-sends on the next poll. The ordering is kept deliberately
   - recording first loses a completion outright when the send fails, and a
   duplicate notice is a better failure than a finished task nobody hears about
   - so what changed is the claim. The docstrings now state at-least-once and
   why, and a test pins behaviour and wording together.
2. *`revise:` and `log:` callbacks did not bind to the chat.* `confirm`/`cancel`
   route through board functions that check `chat_id`; these two did not. The
   practical escalation is nil - both are read-only, and an authorized chat can
   already type `/logs` for any task - so the invariant as written in the brief
   was the thing that was wrong. `board.chat_was_notified` now gates both.

**Phase 7 criteria 3 and 4 complete.** Artifacts are read the moment the agent
exits, before the verify command can add its own output (mutation-proven:
reverting the capture point attributes a verifier-written `coverage.xml` to the
agent). A task declaring `expected_artifacts` now fails when a declared file is
missing or empty even on verify exit 0 - the command proves the command, not the
deliverable. That gate caught `test_triggers` passing three tasks which declared
artifacts they never produced, in a shared workspace where the second and third
could not have passed a clean-tree precheck either.

**Criteria 1 and 2 remain open** and need real runs against `~/projects/genelens`
with `npm test` as the gate. No task has ever been executed against GenClarus.

**Two seats, not one.** `~/CODEX-REVIEWER-BRIEF.md` records the arrangement:
Claude writes, Codex reviews. Of 86 commits, 17 carry a `Claude Opus 5` trailer
and 9 a `Claude Code` trailer; the remaining 60 are interleaved throughout, not
an early era - the first Claude trailer is `f964973` (2026-09-09) and the last
untrailered commit is `540e5fd` (2026-09-12). Codex also reaches for
`~/.codex/skills/gsd-code-review/SKILL.md` and `docs/CODEX-NAVIGATION-GUIDE.md`,
which are review apparatus this session did not account for.

**Publication is pending an operator decision.** `origin` holds only `main`;
`phase-2/worker-assign` has no upstream and is 79 commits ahead. The GitHub repo
`Ardit-Mishra/tri-ai` is **public**. A scrub was agreed before pushing: the
Tailscale address appears in 3 tracked files and the operator's home path in 11
planning documents. Scrubbing the tip does not clean history - those strings
remain in the 79 commits unless the branch is squashed.

**Latest verification:** `python tests/run.py` -> **412 tests, exit 0,
227.755s** (2026-09-13).

**Mobile ergonomics and the neural lattice, 2026-09-13.** Confirming a task
meant copying a hex id on a phone; knowing whether anything was happening meant
typing `/status` until it changed. Staged requests now carry Confirm/Cancel
buttons (the id stays in the text, so tapping is the convenience and typing the
fallback), bare `/confirm` resolves when exactly one request is outstanding and
lists them when ambiguous, and `/run` with no alias offers the workspaces as
buttons. A single progress card per task per chat is edited in place through
claimed -> building -> testing and closed on a terminal phase; an unchanged
phase costs no edit, so an idle poll makes no Telegram calls, and a failed edit
leaves the phase unrecorded so the next poll retries. Small self-contained text
artifacts upload as documents beside the link - a link needs the tailnet, a file
does not. `StagedReply` subclasses `str` so every existing caller is unaffected.

The canvas became a firing neural lattice: axons are quadratic curves bowed
perpendicular to their own run, action potentials travel an axon only while its
upstream task is running (a still lattice means nothing is executing, which is
the point), clusters render as layered volumetric fields, and the soma reads its
state - running pulses, done settles, cancelled dims to a dormant amber trace.
A second finger becomes a pinch rather than fighting the drag. Artifacts open in
a sandboxed in-HUD frame reset to `about:blank` on close. HiDPI scaling and
`touch-action:none` were already in place and were left unchanged.

**Intake pre-flight closes the workspace trap.** A plain-text edit aimed at
`celestial.html` went to the default workspace, which did not contain it, so the
agent wrote a new page instead of editing the intended one - and nothing said so
until the run finished. Staging now names its target workspace and reports files
a prompt references that the workspace does not hold. It warns only when the
workspace was actually read and held none of them: a prompt naming one present
and one new file is ordinary work, and a workspace that could not be read proves
nothing about absence. A test caught the first version warning about "missing"
files in a directory it had never opened.

**Live verification (2026-09-13):** the daemon fleet runs on this desktop
(`VIVO-S14`, the same host as tailscale `vivo-s14`); `daemons.json` reports
`running` with all three PIDs alive, and `process_liveness.pid_alive` -
`GetExitCodeProcess == STILL_ACTIVE` - returns True for each and False for an
unused PID. `daemon_supervisor._is_alive` routes through it.

**Still unproven:** the reply-to-revise path has passed its tests but has never
been exercised on a real phone - `task_links` is still empty, so the lattice has
no axon to fire along and the linked-follow-up flow has no live evidence. The
inline keyboards likewise have never been tapped. Artifact capture still
attributes a concurrent writer's edits to the run; a tree snapshot at claim does
not fix it, because the clean-tree precheck already guarantees an empty baseline
and git cannot say who wrote a file. GenClarus remains registered but undriven.

**Latest verification:** `python tests/run.py` -> **395 tests, exit 0,
163.906s** (2026-09-13).

**Phase 6 complete and accepted (2026-09-13).** Slice 5 closed the gap where a
task could finish, write a real file, and tell nobody. Produced files are
captured from the workspace on the verified-pass path (`git status --porcelain`
against the clean tree the precheck guarantees at claim); completions are pushed
to each authorized chat exactly once through a notification ledger, leading with
the operator's own prompt rather than the generic intake title; and
`/artifact/<task_id>/<index>` serves board-recorded paths resolved inside the
task's own workspace, addressed by index so there is no path to traverse.
Replying to a completion message creates a follow-up linked through
`task_links` and inheriting the original's workspace. `/files` lists everything
produced, grouped under the prompt that asked for it.

**Live acceptance:** task `t_cf8111f2` ("build me a landing page ... colossal
celestial structures") ran to done with `verify exit 0 in 163.64s`, captured
five artifacts unattended, and delivered a working Tailscale artifact link to
the operator's phone. A later road-trip itinerary task repeated it.

**Dashboard made legible.** Every Telegram task rendered as `Telegram: tri-ai`
because plates used the intake title; the operator's prompt was not even in the
payload. Prompts now travel from `tasks.body` through the reader, a
"happening now" panel answers what is being worked on without a tap, and the
jargon is gone (Task map, Finished runs, Learned rules, Picked up / Workspace
ready / Building / Testing).

**Defects found and fixed this session:**
- Class names passed as the `text` argument of `make(tag, text, cls)` printed
  literal `dot` in status badges and `inspect-grid` under the inspector title.
- Windows liveness reported a dead daemon as alive. `OpenProcess` succeeds for a
  terminated process whose object still holds an open handle; liveness now
  requires `GetExitCodeProcess == STILL_ACTIVE`. This is the inverse of the
  earlier `os.kill(pid, 0)` false-down defect - both directions are now covered.
- The verify stage lit only from a live `verify.log`, so every finished task
  claimed it had never been tested. It now reads the run's recorded exit code.
- `drawLabelPill` clamped only the right edge while `pillRect` clamped both, so
  a label measured as fitting drew off the left edge; hull labels also drew
  before the node pass and were painted over. Labels are now deferred past the
  node pass and every label claims its rect.

**Workspace separation (2026-09-13).** Ad-hoc Telegram work ran in the kernel
repo, so every delivered file left the tree dirty and the next task skipped at
the clean-tree precheck - this stranded runs 4 and 5 earlier. A `sandbox`
workspace (`~/tri-ai-sandbox`, `python verify.py`) is now the intake default.
Its verifier fails when a run produced no file and when produced HTML does not
parse, which is a real gate; the previous `python tests/run.py` proved only that
the kernel works, so a page could be marked `verified` on evidence unrelated to
it. Historical rows carry that weaker meaning. `intake_policy.json.bak` holds
the prior policy.

**Known limitation carried into Phase 7:** artifact capture diffs the working
tree, so a concurrent writer's edits are attributed to the run - observed live
when two of five captured artifacts were a parallel editor's changes. A tree
snapshot taken at claim would fix it.

**Portfolio audit (2026-09-13):** `.planning/PORTFOLIO_STRATEGY.md` records what
exists. `peptidemhc`, `genomesight`, and `biostudio` were named as flagship
projects but a scan of every `.md` and `.json` under the home directory's source
roots returned zero mentions and no directory by those names. The portfolio is
two projects - Tri-AI and GenClarus (`~/projects/genelens`, live at
genclarus.com) - not five. GenClarus is registered as a governed workspace but
no task has yet run against it, which is now Phase 7.

**Latest verification:** `python tests/run.py` -> **359 tests, exit 0,
204.436s** (2026-09-13).

**Phase 6 Slice 4 (spatial HUD) implemented locally, 2026-09-12:** the web
dashboard gained hierarchical "site inspection" visuals over the same read-only
snapshot contract. `jarvis_terminal` now projects `TaskTelemetry`, `PhaseView`,
and `RunLogView` by reading `task_runs`, `triai_worktrees`, `task_events`, and
eight further `tasks` columns - all `SELECT`, still `mode=ro` + `query_only=ON`,
and the AST boundary test passes unchanged. Lifecycle phases
(`CLAIMED -> WORKTREE_PREP -> AGENT_ACTIVE -> VERIFY_GATE`) are derived from
recorded evidence only; a stage a `dir` workspace never reaches renders
`skipped` with its reason rather than `pending`. Active-run `agent.log` and
`verify.log` tails ride inside the snapshot (40 lines / 64KB / 240 chars per
line, running runs only) rather than behind a `?task=` endpoint, so no
caller-supplied text ever becomes a path under `~/.tri-ai/runs`. Canvas adds
workspace convex hulls, room/stage node tiers, an arc-reactor core and
four-segment phase ring on running nodes, and pan / wheel / double-tap zoom; the
inspector becomes a bottom sheet below 768px. Verified live against the board:
phases read `claimed=done worktree_prep=skipped agent_active=done
verify_gate=active` for run 5 of `t_69cc6245`, with the real log line surfaced.

**Operator-reported visual defects fixed:** the status-badge indicator carried
the literal text `dot` and the node inspector carried the literal text
`inspect-grid` - both were class names passed as the `text` argument of
`make(tag, text, cls)`. Evidence outcomes are now tinted micro-badges. Without
dependency edges the layout places nodes deterministically on the core orbit;
the previous mutual-repulsion physics pinned them against the canvas walls,
which was the "floating in deep space" complaint.

**Windows liveness probe corrected (false-up defect):** `_pid_alive` reported a
dead daemon as alive. Opening a process handle is not liveness - Windows keeps
the process object while any handle to it remains open, so `OpenProcess`
succeeds for an exited process. Confirmed live: PID 16436 (telegram) returned
`OpenProcess OK, exit_code=1 (EXITED)` while the panel showed TELEGRAM UP. The
probe now calls `GetExitCodeProcess` and requires `STILL_ACTIVE` (259); the
residual ambiguity (a process genuinely exiting with code 259) is inherent to
the Win32 contract and is documented in the code. Regression test spawns a
child, waits for it, and asserts it reports down while `Popen` still holds the
handle. This is the inverse of the earlier `os.kill(pid, 0)` false-down defect.

**Known latent trap, not yet fixed:** `jarvis_web.py` declares `function
render(data)` twice at the same scope. The second shadows the first, so the
first is dead - but it calls `clear(byId('matrix'))` against an element that no
longer exists and would throw if declaration order ever changed.

**Live incident, unresolved at time of writing (2026-09-12):** the daemon fleet
stopped during the session. `daemons.json` reports `status: stopped`; supervisor
5544, worker 38148, and telegram 16436 are all dead, telegram having exited with
code 1 (`supervisor.stderr.log` is empty). Task `t_69cc6245` remains `running`
on the board with run 5 holding claim `Vivo-S14:38148` - a stranded claim
against a dead worker. Its `agent.log` records `worker: skipped - working tree
not clean (8 entries) - skipping`. No recovery action was taken: closing the
claim is `board.abort_dead_worker_claim`, which refuses a live worker and is not
a retry. The working tree of `C:\Users\ardit\tri-ai` is dirty and will keep
failing the clean-tree precheck until staged or stashed.

**Operating environment note:** Windows `allow_reuse_address` lets a second
process bind port 8080 while the first keeps serving, so the dashboard must be
stopped before restart or a stale template is served. The embedded HTML template
is a Python r-string; shell heredocs mangle its escapes, so it must be patched
with a Python script.

**Latest verification:** `python tests/run.py` -> **276 tests, exit 0,
173.931s** (2026-09-12).

**Phase 6 Slice 2 accepted locally:** `src/dashboard/jarvis_web.py` is a
loopback-only (`127.0.0.1`) standard-library web server. It reuses the terminal
reader, exposes only immutable `/api/snapshot` and SSE `/events`, and serves a
self-contained dark dashboard. Structural tests reject board/ledger mutators,
credential access, external-network clients, and process spawning; endpoint
tests prove HTML, JSON, SSE, and non-loopback refusal. Windows client
disconnects are quiet without suppressing real server errors. The terminal
reader now reports the complete valid ledger-entry count alongside its bounded
evidence tail. A live read caught `os.kill(pid, 0)` reporting Windows daemon
PIDs as down; the reader now uses a query-only Windows process handle and has
a current-PID regression test. Medium and mobile widths stack the header,
metrics, and four requested task lanes without clipping; cancelled tasks stay
visible in the Failed lane. Full suite: `python tests/run.py` -> **259 tests,
exit 0, 180.472s**. Live verification: the local server is listening at
`http://127.0.0.1:8080`; `/api/snapshot` and SSE both responded; its status
indicators report the supervisor, worker, and Telegram daemon alive, matching
the retained supervisor state and direct PID checks. Next: Phase 6 Slice 3
accepted-memory evidence panels; do not enqueue a retry for the cancelled task.
Slice 2's
implementation/review correction (`a71ff9e` / `02b90f8`) is now covered by
adversarial regression tests in `8ea0d03`. Slice 3 is accepted on local,
exit-code evidence under the operator's explicit continuation instruction; the
separate review service was unavailable and is recorded as such.

**Canonical branch:** `phase-2/worker-assign`. The audited implementation base
was `00671d9`; the phase/plan audit record was committed as `75e188f`.

**Latest canonical verification:** `python tests/run.py` -> **259 tests, exit
0, 180.472s** (2026-09-11). This verifies Phase 5A confirmed Telegram
control, the Phase 5B local polling daemon, the Phase 5C routing probe, and
the route-admission, episodic-memory, procedural-memory, semantic-memory, and
candidate-evolution and proposal-review gates alongside every prior phase.

**Phase 5A accepted locally:** confirmed Telegram control is implemented in
`telegram_control.py` and `board.py`, with the HTTPS daemon selecting it
only when an operator supplies an intake-policy JSON file. Run and retry create
durable pending actions; confirm is chat-bound and idempotent; policy owns
workspace, verify command, and timeout; cancel is compare-and-swap and refuses
an unregistered or surviving worker PID. Focused control/transport tests: 22,
exit 0, 5.175s.

**Phase 5 live acceptance:** superseded by the operator's 2026-09-11 mobile
acceptance recorded above. It proved the confirmation path creates one
configured verify-gated task. Cancel/retry retain their automated CAS and
ordinary-run proofs; they were not misrepresented as part of that single live
intake exercise.

**Telegram intake ergonomics and worker diagnostics accepted locally:** the live
`/run` report exposed a misleading reply, not a whitespace parser defect:
`split(maxsplit=2)` already preserves multi-word prompts, but missing policy
or unknown aliases were reported as generic usage. `/run` now distinguishes
missing intake policy, malformed syntax, and unknown aliases; `/workspaces`
and `/help` list the bounded, policy-owned aliases. Plain text creates only a
confirmation-required draft in a policy default workspace (a single workspace
is default automatically; multi-workspace policies may set
`default_workspace`). Exact status phrases remain read-only. `worker_daemon`
now retains an exception type and reason in `worker.log`, matching Telegram
diagnostics. Focused tests: `python -m unittest tests.test_telegram_control
tests.test_telegram_daemon tests.test_worker_daemon` → **34 tests, exit 0,
8.464s**. The launcher/supervisor use `~/.tri-ai/intake_policy.json`
automatically when it exists; an explicit policy path still takes precedence.
The local default currently maps alias `tri-ai` to the canonical repository
with the fixed `python tests/run.py` verifier and a 300-second timeout. Full
suite after that operational change: `python tests/run.py` → **245 tests,
exit 0, 149.093s**.

**Live-startup correction accepted:** the operator-owned config file was valid
JSON saved with a Windows UTF-8 BOM. The daemon read it as bare UTF-8 and
refused it before child launch. Config decoding now uses `utf-8-sig`, with a
regression test; no credential value was printed or committed. Full suite:
`python tests/run.py` → **246 tests, exit 0, 179.950s**. The prior supervisor
was stopped through its sentinel, then a detached supervisor launched with
the default policy. Its state is `running`, all three recorded PIDs are alive,
the Telegram child argv contains the policy, and the supervisor error log is
empty. Direct Bot API `getMe` succeeded; `sendMessage` to the authorized chat
was accepted as message 47 with `System verified and online by Codex. Intake
policy active.` The default `tri-ai` workspace currently has retained
untracked runtime files, so a confirmed task aimed at that repository will
correctly skip at the worker's clean-tree precheck until a clean workspace is
selected.

**Phase 5B accepted locally:** `src/worker_daemon.py` is a foreground,
resilient loop around exactly one existing `worker.run_once` tick. Empty
queues use capped exponential backoff; a real attempt resets the delay; a
worker-requested hard stop or SIGINT/SIGTERM exits cleanly. The daemon does not
create a retry path, so the existing worker/board retry and circuit-breaker
rules remain authoritative. Focused daemon/safety tests: 42, exit 0, 3.097s.

**Phase 5C accepted locally:** `src/routing_probe.py` is an injected-
transport, diagnostic-only policy probe. It rejects unknown task kinds and
non-loopback/non-`/v1` endpoints before a transport call; it records
policy-versioned primary evidence and exactly one distinct loopback fallback
for 429, timeout, or connection failure. It is structurally unable to import
the board, worker, or executor or complete a task. Hermes source inspection
verified launch-scoped `--model`/`--provider` selection, but endpoint
selection is Hermes `config.yaml` state and general `OPENAI_BASE_URL` is
retired. No executor route was invented, no Hermes configuration was read, and
no live proxy call was made. Focused probe tests: 7, exit 0, 0.035s. Next:
plan/review a credential-free, operator-configured Hermes profile plus measured
route-admission gate before enabling executor routing.

**Phase 5A-C commits:** `f72ea6f` (confirmed control), `37fe26d`
(continuous daemon), and `ea026a1` (local routing probe).

**Route admission accepted locally:** `src/route_admission.py` consumes
only operator-owned policy plus verifier/resource evidence. It requires the
exact policy route and model, every baseline and candidate verifier exit to be
zero, one approved resident model, and compliance with the policy VRAM cap.
Agent success narration is never consulted. Focused tests: 8, exit 0, 0.038s.
No Hermes configuration, credential, endpoint, executor, worker, or board
path is reachable. Next: a fresh episodic-memory plan using accepted
board/ledger records, not the rejected candidate branch.

**Episodic memory accepted locally:** `src/memory/episodic.py` is a
read-only derived SQLite index over full JSONL ledger snapshots. Every fact
keeps its original source path, line number, and SHA-256 line digest; malformed
input leaves the current index untouched, while changed source lines invalidate
the old citation and preserve it as stale evidence. Focused tests: 5, exit 0,
0.196s. Next: procedural rules that cite only validated episodic facts and
cannot alter task acceptance or execution policy.

**Procedural memory accepted locally:** `src/memory/procedural.py` loads
strict, operator-owned rules whose only selectable output is a fixed
preflight-checklist vocabulary. Selection revalidates exact workspace/task-kind
scope, expiry, citation digests, deterministic order, and a character budget;
free text, command-shaped fields, routing/model fields, and any activation
other than `preflight_advice` are refused. Focused tests: 6, exit 0, 0.086s.
No rule is injected into the worker yet. Next: deterministic semantic facts
derived only from the episodic and procedural records.

**Semantic memory accepted locally:** `src/memory/semantic.py` derives a
deterministic, in-memory graph of task/run/outcome/dependency/retry/rule
citations from supplied episodic facts and procedural rules. Every edge carries
source citation(s); any changed or missing source makes the corresponding fact
disappear on the next derivation. Focused tests: 4, exit 0, 0.152s. It has no
board, worker, executor, router, process, or persistence capability. Next:
candidate-only constrained evolution, with replay and citation validation
before any possible activation.

**Candidate-only evolution accepted locally:** `src/memory/evolution.py`
derives deterministic `draft` candidates only from at least two currently valid
episodic citations for the same `logic` failure signature and non-zero verifier
exit. A candidate is a frozen data record with a fixed inspection checklist and
`operator_review_required` label; it has no board, worker, executor, routing,
process, file-mutation, task-schema, verifier, prompt, or activation path.
Citation drift invalidates both the candidate and any held-out replay result;
replay reports supporting evidence only. Focused tests: 8, exit 0, 0.280s.
Next: separately design explicit operator review and activation, with no
automatic execution or configuration mutation.

**Proposal review and activation accepted locally:** `src/board.py` owns
durable `pending` / `approved` / `rejected` / `expired` proposals, per-chat
Telegram notification receipts, and compare-and-swap decisions. A passed task
can only be archived when it is still `done`; a failed task uses the existing
retry gate; an approved candidate becomes a citation-revalidated, fixed
procedural checklist record only. Approval cannot merge, push, deploy, run a
process, alter prompts/verifiers/task schemas/routes, or inject a worker. The
Telegram daemon authenticates both callback actor and chat before calling the
local control seam, emits fixed inline decision data, and edits terminal
choices to remove buttons. Citation drift expires the proposal and writes no
activated rule. Focused tests: 36, exit 0, 22.178s. Next: operator-started
mobile exercise with the existing daemon; no live network request was made in
this implementation slice.

**Operational daemon tooling accepted locally:** `scripts/run_daemons.ps1`
starts the fixed-argv Python supervisor in the foreground. The supervisor owns
only `worker_daemon.py` and `telegram_daemon.py`, preserves stdout/stderr in
`~/.tri-ai/logs/worker.log` and `telegram.log`, timestamp-rotates large logs
without deletion, records supervisor/child PIDs in `~/.tri-ai/logs/daemons.json`,
and notices the Telegram daemon's normal pending-proposal push on every poll.
`scripts/stop_daemons.ps1` creates the recorded stop-request file; the
supervisor asks both child process groups to stop cleanly before a bounded
forced-stop fallback. A partial launch stops the first child and retains logs.
Focused operational tests: 6, exit 0, 0.046s; PowerShell `-WhatIf` exited 0.

Start from `C:\Users\ardit\tri-ai` in the same PowerShell session that has
the Telegram token and authorized chat environment variables:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daemons.ps1
```

Add `-IntakePolicy <operator-owned-policy.json>` only to enable `/run` intake;
proposal notifications and callback review work without it. Test with:

```powershell
python tests/run.py
```

Request a clean stop from another PowerShell session with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_daemons.ps1
```

The operational slice performed no live start, credential inspection, or
Telegram network request. Next: operator-started foreground exercise and
inspection of retained logs/state.

**Operational startup correction accepted locally:** `run_daemons.ps1`
now defaults board, ledger, and retained run paths to `~/.tri-ai/` rather than
the repository's `.planning/` fixtures. Before creating daemon state or
spawning either child, the supervisor will require
`TRI_AI_TELEGRAM_BOT_TOKEN` and either
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID` or
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS`; it checks presence only and never logs
their values. `-WhatIf` remains a no-spawn preview and does not require
Telegram settings. Focused operational verification: `python -m unittest
tests.test_daemon_supervisor` → **8 tests, exit 0, 0.466s**. Full verification:
`python tests/run.py` → **238 tests, exit 0, 148.043s**. Next: local commit,
then operator-started foreground exercise using the canonical runtime paths.

**Telegram startup diagnosis and persistent configuration accepted locally:** the
generic `telegram daemon stopped: configuration or transport failure` log line
was caused by `telegram_daemon.main()` replacing all caught `ValueError` and
`TelegramTransportError` messages. It also resolved only the current process
environment, so a PowerShell opened before a Windows User environment update
could not see it. The shared resolver now uses this precedence: process
environment, `~/.tri-ai/config.json`, then Windows User environment registry.
It accepts only `TRI_AI_TELEGRAM_BOT_TOKEN` and either
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID` or
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS`; unprefixed `TELEGRAM_*` names are not
supported. The supervisor and child use the same resolver before spawn, and
safe failure type/reason reaches `telegram.log` without credential values.
Focused tests are green: `python -m unittest tests.test_telegram_daemon
tests.test_daemon_supervisor` → **24 tests, exit 0, 0.517s**. Full suite
passed: `python tests/run.py` → **238 tests, exit 0, 148.043s**. Next: provide
the token/chat ID through one approved source, then retry the normal runner
command.

**Current implementation:** operator authorized Phase 4.5 Telegram long-poll
transport. Plan: `.planning/phases/phase-4.5-telegram-transport-plan.md`.
The supervisor validates the presence of the Telegram settings at
operator-start time; the daemon alone consumes their values, requires an
explicit chat-id allowlist before invoking the adapter, and hard-stops on
transport failure. No live credential or Telegram request is used during
implementation or tests.

**Phase 4.5 verification:** focused adapter/transport proof
`python -m unittest tests.test_phase4_observability tests.test_telegram_daemon`
→ **14 tests, exit 0, 2.858s**. The fake transport proves authorization happens
before adapter dispatch, all supported command routes remain read-only, long
logs are chunked without truncation, and the live client shapes HTTPS JSON POST
without making a request. Direct daemon CLI help also exits 0. Files added:
`src/interfaces/telegram_daemon.py`, `src/interfaces/__init__.py`,
`tests/test_telegram_daemon.py`, and the transport plan; files extended:
`src/telegram_read_surface.py`, `tests/test_phase4_observability.py`.
The next atomic step is local commit-level review, then operator configuration
and foreground start of the daemon. Do not add service installation, polling
automation, or Telegram mutation.

**Phase 4 Slice 2 accepted:** independent review of `a71ff9e` found that a
crash after `git worktree add` could strand a deterministic unowned target and
that a recorded target was not proven to be the expected branch. `02b90f8`
adds `executor.verify_worktree` and holds a board write transaction across
adoption/materialization/recording. `8ea0d03` proves exact-source and
exact-branch adoption/refusal, then proves a corrupted on-disk recorded target
skips before launcher/claim. Every rejected target remains evidence. Full
record: `.planning/reviews/phase-4-slice-2-review-2026-09-11.md`.

**Branch audit:** `.planning/reviews/phase-audit-2026-09-10.md` retains the
then-current branch evidence. Its old observations about Slice 3 and Slice 4
candidate suites are superseded on the canonical branch by commits `a0ab9fc`
and `1695ec4`, and by the Phase 4.5 verifier above. Phase 5, Phase 6, and
Phase 7 worktrees remain partial experiments, not merged progress; the Phase 7
candidate remains rejected because it can mark a task done without a trusted
verify result.

## Performance Metrics

**Velocity:**
- Completed phases: 3
- Average duration: 1 session
- Total execution time: 2 sessions

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Verified Board Substrate | 1 | 1 session | 1 session |
| 2. Verify-Gated Single-Worker Execution | 1 | 1 session | 1 session |
| 3. Planner + Bounded Concurrent Execution | 1 | independently reviewed | — |

**Recent Trend:**
- Phase 1 (22 tests) → Phase 2 (89) → Phase 3 (132) → current Phase 4 base (137)
- Trend: ↑

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: reuse `hermes_cli/kanban_db.py` directly as the board substrate; the only board-level build work is the additive `verify_command` column, not a new board or a bespoke JSON graph file (STACK.md's JSON recommendation is superseded by ARCHITECTURE.md's direct source read)
- Roadmap: "prove coordination works" (Phases 1-3) is deliberately kept separate from any GPU-bound concurrent-fixing claim — v1 targets read-only CPU/IO-bound chores only; concurrent generation only buys ~16% on this hardware (`concurrency_results.json`), not Nx
- Roadmap: Telegram read-only observability (Phase 4) ships before any Telegram write action (Phase 5) — the control surface must not mutate a live graph before its underlying primitives (reclaim, retry, pause) are proven trustworthy
- Phase 1: the kanban kernel is *used, never edited*. The Hermes install replaces whole package trees when it updates (every package has a `*.hermes-update-staging` sibling), so an in-place patch to `kanban_db.py` would be silently reverted — and the failure mode is invisible: the board keeps working while verification quietly stops. The additive migration runs from `src/board.py` against Tri-AI's own board file, through the kernel's own `add_column_if_missing`. Safe because the kernel's migration pass is purely additive and `tasks` is not in its `_REBUILD_SPECS` drift-rebuild list — both asserted by tests, not assumed
- Phase 1: three columns added, not one — `verify_command`, `verify_timeout`, `expected_artifacts` (JSON). Target repo needed no column: the kernel's `workspace_kind='dir'` + `workspace_path` already means exactly that
- Phase 1: `board.create_task` refuses a task with no verify command at write time, before any row exists, AND writes the row and its verify columns in one transaction. Rejecting early is only half the property: the kernel's `create_task` commits on its own, so a second transaction for the verify columns left a window where the row was visible as `ready` with `verify_command` NULL and a polling worker could claim an unverifiable task. Proven by failure injection, not assumed. This is Phase 3's criterion 2 landing early because it is the natural shape of the write API, not a separate feature
- Phase 2: **idempotency-key re-submission is a full no-op, never a partial update.** The kernel's `create_task` returns the existing row id for a duplicate key; `board.create_task` previously re-wrote only the three verify columns onto it, so a re-run of a queue line whose verify/timeout/artifacts changed refreshed the gate while title, prompt, workspace and `max_runtime_seconds` stayed stale — a fresh oracle bolted onto an old workspace, reported as success. Caught by the assign/chores cross-reviewer and confirmed by reproduction. The fix detects a pre-existing key inside the same `write_txn` (IMMEDIATE, so no interleaving writer) and skips the verify-column UPDATE. To change a task, delete and re-assign.
- Phase 2: **a claim must carry the worker's own pid, or the 15-minute TTL reclaims a live run.** Tri-AI claims with `host:pid` but never set the kernel's `worker_pid`; the kernel's live-worker extension branch (`release_stale_claims`: truthy `worker_pid` + `_pid_alive`) therefore never fired, and once `DEFAULT_CLAIM_TTL_SECONDS` (15m) elapsed a claim whose agent was still running (default 30m) was reclaimed to `ready` and a second worker spawned a second agent on the same repo. Caught by the worker/ledger cross-reviewer with a precise reproduction. Fixed by registering the worker's own pid after claim (`kb._set_worker_pid`, the same private-seam precedent as `board.posix_semantics_signal`); the dead/crashed/quarantine paths still reclaim because the pid is genuinely gone after exit.
- Phase 3 Slice 3: **the kernel owns the parent gate — the dispatcher/board never re-checks it.** `kanban_db.claim_task` is the single enforcement point: a claim on a task with an undone parent demotes it `ready -> todo` with a `claim_rejected` event and returns `None`, and unparented tasks land `ready` while parented ones land `todo` until `recompute_ready` promotes them when every parent is `done`. So a linked-graph failure test builds the DAG through `task_links` and asserts on the real claim/complete/reclaim lifecycle rather than re-implementing scheduling. Verified against the kernel source, not assumed.
- Phase 3 Slice 3: **the failure-isolation test drives the REAL worker path, not a bespoke failure simulator.** The launcher claims the dispatched task via `kb.claim_task`, then runs `worker.execute_task` (claim -> precheck -> upstream gate -> agent -> verify -> accept/revert -> ledger) with only `executor.run_agent` swapped, so the "deliberate non-zero verify command" genuinely exits 7 through `executor.run_verify` and the retry/circuit-breaker is the kernel's own `_record_task_failure` (limit 2 -> `blocked` + `gave_up`). This is why the test is credible where a mock-outcome launcher would not be.
- Phase 3 Slice 3: **the dispatcher must re-read the board between waves, not track dispatched IDs** — a reclaimed (failed-then-retried) task reappears in `ready_tasks()` and would be invisible to a `dispatched_ids` set. The Slice 3 test's `5 results (root + 2 failing-parent + 2 siblings)` in one run is proof the re-read drives the retry. This is the second scheduling-loop correction (after `run_batch` threading) that Slice 3 surfaced; both were needed only because the earlier slices did not exercise failure.

### Pending Todos

- Phase 4 Slice 4 scaffold is verified locally: `telegram_read_surface.py`
  accepts only `/status`, `/task <id>`, and `/logs <id>`; reads board/ledger;
  returns full contained retained logs; and has no token, transport, network,
  process, or board-mutation capability. It is not a live Telegram deployment.
  Operator direction on 2026-09-11 explicitly authorizes the bounded live
  transport plan at `.planning/phases/phase-4.5-telegram-transport-plan.md`.
  The implementation has local exit-code evidence but has not been connected
  to a live account in this session. Do not begin Phase 5 or add any Telegram
  mutation.
- Phase 2's worker must call `board.release_stale_claims`, never `kb.release_stale_claims` directly — going straight to the kernel reintroduces the Windows reclaim deferral (see Blockers). Add a grep check to Phase 2's criterion-5 audit step, beside the existing no-push/no-merge/no-credentials audit
- Any new Tri-AI entry point must go through `board.kanban()`, which now *assigns* `HERMES_KANBAN_DB` rather than `setdefault`-ing it. A dispatcher-spawned worker inherits that variable pointing at the Hermes board, so `setdefault` silently kept the wrong board
- Review is a separate seat: Claude writes, a second model reviews at the commit/branch level. Brief at `~/CODEX-REVIEWER-BRIEF.md`. It earns its keep — the first pass caught a test whose *name* claimed it proved a schema collision was refused while its body only inspected a throwaway table and never called `migrate()`. A test that asserts less than its name is the same class of failure as an agent reporting success it did not achieve, and self-review does not reliably catch it
- The kernel exposes `signal_fn` on `reclaim_task` and `detect_stale_running` as well. Neither is used yet; both need the same wrapper when a phase reaches for them
- Phase 3 review: **DONE 2026-09-10** — passed, 4 non-blocking findings, recorded at `.planning/reviews/phase-3-review.md` (commit `12288a7`).
- Slice 3's uncommitted classifier is not integrated with the worker and fails
  its current test matrix. Slice 4's candidate returns log paths rather than
  full captured logs and has no real transport. Neither has started in the
  sense that matters for roadmap acceptance.
- Phase 5's memory candidate is partial (episodic and semantic committed,
  procedural uncommitted, evolution absent) and uses WAL on SQLite 3.50.4.
  Phase 6's dashboard candidate fails. Phase 7's daemon candidate can fabricate
  a passing verification result and directly applies remote diffs; do not merge
  any of these branches. Full evidence is in the audit record.
- Before Phase 5 work starts, turn the approved capability-expansion design
  into reviewed atomic implementation plans; semantic memory/evolution are
  Phase 5C/5D, with the choice and proof obligations now recorded there.

### Blockers/Concerns

- **Independent verification caught a false green on 2026-09-09 (Phase 2 slice 1).** The author ran
  the suite and reported 51 tests passing; the reviewer ran the same suite on the same machine and
  got exit 1 — `test_a_delayed_writer_spawned_by_the_verifier_never_writes` failed with
  `tree_survived=True`. The defect was real and the author's green run was the misleading one:
  `_kill_tree` returned success whenever the shell parent had already exited, and verified only that
  the *root* pid was gone rather than the tree. `taskkill /F /T` walks a parent-child map that a
  detached grandchild is not on. Reproduced directly: the old logic reports "tree gone: True" while
  the orphan writes 8s later. Replaced with a Windows **Job Object** (ctypes/kernel32) — children
  join at creation, `TerminateJobObject` kills the set atomically, and the job is queried afterwards
  for survivors, so "we killed it" becomes evidence rather than an assertion. Two lessons worth
  keeping: a timing-dependent test can pass for the author and fail for a reviewer on the same
  machine, so a single green run is not verification; and this is the second Windows
  process-lifetime assumption to be wrong here, after Phase 1's reclaim defect.

- **Phase 1 publication resolved on 2026-09-09.** The URL and GitHub permissions were correct; `Ardit-Mishra/tri-ai` simply had never been created. `gh repo create Ardit-Mishra/tri-ai --public` created an empty repository, then `git push -u origin main` published `23dbfb4`. GitHub API verification proved `evidence/ledger.jsonl` is public, detected `license=MIT`, and reported `main` as the default branch at `23dbfb4`. The two earlier hard-stops were correct: pushing cannot create a GitHub repository, and no URL or credential change was needed.

- REQUIREMENTS.md's own summary line originally stated "21 total" v1 requirements; the actual itemized list contains 25. Corrected during roadmap creation — verify this doesn't indicate a requirement was silently dropped somewhere upstream if it resurfaces.
- **On Windows the kanban kernel never reclaims a task whose worker is dead** (found and worked around in Phase 1). `_terminate_reclaimed_worker` reads "already gone" from a `ProcessLookupError`, which Windows' `os.kill` never raises — a dead PID gives `PermissionError` (WinError 5), one that never existed gives `OSError` (WinError 87), and both are read as "still alive", so every tick defers the reclaim forever. Measured against the kernel default: `0 reclaimed; status = running; events [..., 'reclaim_deferred']`. Load-bearing, because the always-on node is the Windows desktop and a stranded task looks busy rather than broken. Worked around in the adapter via the kernel's own `signal_fn` hook (`board.posix_semantics_signal`), not by editing the kernel; the guard is not weakened — a genuinely live worker is still signalled and still defers
- EXEC-01's concurrency cap (Phase 3) must be set from `concurrency_results.json`, which measured concurrent *generation* only (up to n=4, ~16% aggregate gain, VRAM flat at 5,278 MiB). CPU/IO-bound chore parallelism is a different, unmeasured-but-likely-favorable case per PITFALLS.md/FEATURES.md reasoning — do not conflate the two when Phase 3 sets its actual worker count.

## Session Continuity

Last session: 2026-09-12 (master directive: fleet recovery, dashboard audit,
portfolio alignment, Phase 6 closeout)
Phase/plan audit complete on canonical `phase-2/worker-assign`; audit record
commit `75e188f`.
Read `.planning/reviews/phase-audit-2026-09-10.md` before touching any feature
worktree. It separates accepted history from candidate code and preserves each
failure result.

**Accepted Phase 3 evidence:**
- `src/dispatcher.py` — MODIFIED. Dispatch loop simplified for retry support: drop `dispatched_ids`;
  re-read `board.ready_tasks()` fresh each wave so reclaimed (failed-then-retried) tasks reappear
  naturally; add `max_waves` test bound. No new process/git surface (safety audit unchanged).
- `tests/test_phase3_failure_isolation.py` — NEW. 2 tests. Builds the Slice 3 DAG through real
  `task_links`, dispatches one run (`root` -> failing-parent/blocked-descendant + two siblings), and
  proves the failing parent's deliberate non-zero verify command is ledgered `verify_outcome='failed'`
  and trips the kernel circuit breaker (`blocked` + `gave_up` after 2), the descendant stays
  unclaimable (kernel demotes ready child with undone parent to `todo` at claim), siblings reach
  `done` in the same run with valid worker/run ledger identities, and a deliberately broken
  dispatcher (stops at first failed child) makes the sibling-completion assertion fail.

**Current canonical test result:** `python tests/run.py` -> **324 tests, exit 0,
159.988s** (2026-09-12). The Phase 3 figure below is historical.

**Key design decisions (Slice 3):**
- The failure test drives `worker.execute_task` (the real claim -> precheck -> gate -> agent -> verify
  -> accept/revert -> ledger path) with only `executor.run_agent` swapped; the verify command is a real
  `python -c "import sys; sys.exit(7)"`, so the ledger genuinely records `verify_exit=7`.
- `board.create_task(parents=[...])` creates the real `task_links`; the kernel's `claim_task` is the
  single parent-gate enforcement point (demote ready child with undone parents to `todo` + return
  `None`) and `complete_task` runs `recompute_ready` to promote siblings when root finishes — the
  dispatcher's wave re-read picks them up, which is how one dispatch run does root, children, and the
  failing-parent retry (`5 results`).
- `failing_parent` gets `priority=100` in the negative test so `ready_tasks()` orders it first and the
  broken dispatcher deterministically stops at it before any sibling is launched.

**Slice 2 commit:** `fa2bb52` — cap-derived concurrent dispatcher + workspace partitioning. 130 tests.
**Slice 1 commit:** `a86f331` — planner graph writer. 98 tests, exit 0, 104.808s.
**Phase 2 commit:** `ea44258` — worker/ledger/assign/chores. 89 tests, exit 0, 93.951s.

Superseded: Phase 4 completed and was operator-accepted on 2026-09-11.

Phase 1 carries two defects found by self-audit and fixed (non-atomic `create_task`; the `setdefault`
board pin), one found by review and fixed (`migrate` silently accepting a same-named column of a
different type).

Phase 2 landed: worker (`src/worker.py`), assign (`src/assign.py`), ledger (`src/ledger.py`),
chores (`src/chores.py`), three criterion test files, calibration infrastructure, and two BLOCKER
fixes in `src/board.py` (idempotency no-op) and `src/worker.py` (worker_pid registration).

**Canonical-checkout guard:** `C:\Users\ardit\tri-ai` is the authoritative Tri-AI checkout. An
Omniroute Claude session created a separate temporary clone under
`C:\Users\ardit\AppData\Local\Temp\claude\...\scratchpad\p0a\tri-ai` on unrelated branch
`phase0a/safe-execution`, with untracked execution-backend files. Do not merge, delete, or use that
clone as a handoff source. Every autonomous session must pass the path gate in
`.planning/AUTONOMOUS-RUNBOOK.md` before modifying anything.

Next: Phase 7 (Planner Integration and Trusted Distributed Delegation). Its
existing candidate can fabricate a passing verification result and applies remote
diffs directly - do not merge it. Before any Phase 7 work, turn the approved
delegation design into a reviewed atomic plan, and read
`.planning/reviews/phase-audit-2026-09-10.md` first.

Note: `~/.claude/skills/` was destroyed in the 2026-09-06 incident and is NOT in the `S5-claude-r3`
archive — that archive stopped at `./profiles/`, before reaching `./skills/`. So the whole GSD suite
(`gsd-plan-phase`, `gsd-execute-phase`, ~60 skills) and the custom `research-repo-grade` skill are
gone. GSD is a marketplace plugin and can be reinstalled; `research-repo-grade` was custom and is not
in `skills-lock.json`, so it is lost. Until GSD is reinstalled, phases are planned directly against
the roadmap's success criteria rather than via `/gsd:plan-phase`.
Resume file: None

---

## 2026-09-14 — the system moved machines, and learned to look before it builds

**Where Tri-AI runs now.** The worker, the Telegram bot and the router all live on the desktop
(`DESKTOP-JHQ7HJM`, account `Ardit II`, reachable over Tailscale at `100.67.149.86` with
`~/.ssh/triai_desktop`). The laptop is where code is written; nothing is true until it is on the
desktop. `career-ops` stays on the laptop only — it is the operator's real job-search data.

**Root cause of everything that failed after the move.** OmniRoute binds `127.0.0.1`. Moving Tri-AI
to the desktop silently severed it from all ten providers and dropped it to raw Ollama, which
narrates instead of acting. The tell was in the first failed run's usage file: `api_calls: 1` means
the model answered once and invoked no tool. Hours were spent swapping local models before reading
it. **When an agent stops acting, read `api_calls` in the usage file before touching the model.**

**OmniRoute on the desktop.** `omniroute@3.8.50`, serving on `127.0.0.1:20129` (20128 is held by a
Windows service). The store moved via SQLite's `backup()` API, not a file copy — the WAL held 4 MB
that a copy would have dropped. Both `.env` files travelled; credentials are encrypted with
`STORAGE_ENCRYPTION_KEY` and decrypt correctly on the new machine (proved by a live call returning
`gpt-4o-mini-2024-07-18`). All 10 provider connections active. Autostart is the Scheduled Task
`OmniRoute Router`, AtStartup + AtLogOn, S4U. **Not yet proven across an actual reboot.**
Verify with: `omniroute providers list` and `curl http://127.0.0.1:20129/v1/models`.

Hermes chain: `auto/best-coding` @ router → `auto/smart` → `devstral:24b` → `qwen2.5-coder:14b`.
The local models cannot do complex work; they are the difference between degraded and stopped.

**The taste pass** (`66e3cf6` here, `c0d3ed3` on the desktop; sandbox `a827b87`).
Run 58 of `t_036e27e3` produced a masala-brand landing page that passed every gate and was still
slop: no FSSAI licence, no net weights, no MRP, no batch date, the framework's default system font.
No check could be written in advance to know that spices need a licence number, so the standard is
not hard-coded — the agent researches the field with its web tools, writes `BRIEF.md` with a
"Must appear" list of concrete strings real examples carry, and the verifier holds the finished file
to that list. `~/.tri-ai/taste.json` holds the thresholds; a malformed file logs and falls back.
`TRIAI_TASTE_REQUIRED` carries the worker's decision to the verifier so the two cannot drift.
Verify with: `python -m unittest discover -s tests -p test_taste.py -t tests` (31 tests).

**Honest limit:** the mechanical floor finds exactly one thing on the page the user rejected (the
missing typeface). Taste is not mechanically detectable. The brief is the only part with teeth, and
it has teeth only because the agent went and looked first.

**Known failures, both pre-existing and neither in this path:**
- `test_worktree_materialization.test_named_worker_materializes_before_claiming` fails identically
  with `src/worker.py` reverted to HEAD. A locked `tmprkxevjap/` sits in the repo root that the OS
  will not let `rm` or `git` open; `git worktree prune` finds nothing. Not yet diagnosed.
- 8 loader errors (`test_telegram_*`, `test_daemon_*`, `test_mobile_ergonomics`,
  `test_reply_revision_loop`, `test_attachment_intake`): `tests/run.ps1` picks the Hermes venv's
  Python 3.11, which rejects `telegram_control.py:96`'s backslash-in-f-string. The daemons run
  under 3.12+, so the code is fine and the *runner* is wrong — the whole Telegram surface is
  therefore untested by `tests/run.ps1`. This is worth fixing before trusting that suite.

**Open, operator-owned:** rotate the GitHub PAT (confirmed live as `Ardit-Mishra`, present in
`.hermes_history` and `state.db`, both of which `hermes backup` zips).

---

## 2026-09-14 (later) — the gate got teeth, then got audited

Two commits: `4af2584` (the taste gate rewritten) and the test-infrastructure work
folded into it. Deployed to the desktop as `46a1f9e`; sandbox verifier `4138955`.

**The suite was lying, and by more than it looked.** `tests/run.ps1` runs under the
Hermes venv's Python 3.11. `src/telegram_control.py:96` had one f-string with a unicode
escape inside its expression — legal from 3.12, a SyntaxError before it. Eight test
files failed to *load*, and unittest reported each as a single
`unittest.loader._FailedTest` error. "463 tests, 8 errors" actually meant **102 tests
were not running**, and the entire Telegram surface — intake, attachments, the daemon,
the transport, the revision loop — was checked by nothing. A loader error is not a
failing test; it is the absence of tests wearing one test's clothes.

Fixed by hoisting the escape to a module constant, and pinned by
`tests/test_modules_import.py`, which imports every module in `src/` under whatever
interpreter is running the suite. **565 → 575 tests, all passing, on both machines.**

**Two flaky tests, both real races in the tests, not the code.**
`test_a_beat_stops_when_the_output_stops` read the heartbeat row the instant touching
stopped, racing the beat still in flight for the final touch — that beat is earned, it
just lands after the read. Now takes two readings inside the silence.
`test_touch_advances_the_reading` slept 10 ms against a monotonic clock whose
resolution on Windows is 15.625 ms, so the two readings could compare equal; it now
sleeps past `time.get_clock_info("monotonic").resolution`. 24 consecutive clean runs
after both fixes, one unexplained failure in the 8 runs before that.

**The kernel deletes a task-owned worktree on completion.** `test_worktree_materialization`
had been failing for an unknown length of time and was assumed stale. It was not.
`kanban_db.complete_task` → `_cleanup_workspace` → `_cleanup_worktree_workspace` runs
`git worktree remove`, so a *passing* worktree task ends with its workspace gone —
while `src/worktrees.py` said in its first line that worktrees are never removed. The
kernel's guard is careful and worth knowing: no `--force`, removal refused if the tree
is dirty or holds unpushed commits, never the main checkout, and only an
auto-generated `wt/<task-id>` branch is deleted with it. Tri-AI names its branches, so
commits survive. **What does not survive is an absolute artifact path into a removed
worktree** — artifacts are recorded before verify runs. Directory workspaces (every
workspace the Telegram intake offers) are unaffected. Pinned by
`test_work_left_in_a_worktree_survives_the_kernels_cleanup`.

**Codex review, and its limit.** The first taste gate went to Codex (`cx/gpt-5.5`
through the router) and came back with seven findings, every one real — all of them
ways the gate could reject work that should have passed. That is the quieter failure
and the worse one: nobody sees a page that was never delivered. The rewrite closed all
seven. Then the account hit its quota: *"All codex accounts reached configured quota
threshold (reset after 617h 46m 30s)"* — 26 days, from 2026-09-14.

Substitutes were tried and are worse. The `nvidia` connection's live catalog does not
contain the models the router lists for it (`z-ai/glm-5.2`, `qwen/qwen3.5-397b-a17b`
both 400). `gemini-3.1-pro-preview` never clears its cooldown. GitHub Copilot refuses
`claude-opus-5` and `gpt-5.3-codex` for this account. `auto/best-coding` currently
resolves to `gpt-4o-2024-08-06` / `gemini-flash-lite-latest`, which answered a real
concurrency question correctly and returned NO FINDINGS on a 25 KB review — not a
substitute for Codex, and any report quoting one should say which it was.

**Two findings the review did not get to, both worse than anything in its list.**
Found by auditing the rewrite by hand:

  - The repair signal used `intake_preflight.check().present`, which matches bare
    filenames *anywhere* under the workspace. The sandbox files every finished run
    under `runs/<slug>/`, so one past run containing `index.html` would have made every
    later "create index.html" look like a repair — **the research pass would have
    switched itself off permanently after the first task, silently, with every test
    green.** `worker._is_repair` now asks whether the named file is where the agent
    will actually work.
  - Promises were compared literally, so `MRP &#8377;185`, `Net wt.<br>100 g` and
    `200&nbsp;g` all read as broken promises. That is ordinary good markup.
    `taste.normalize` folds entities, whitespace and typographic quotes on both sides —
    which also stops a curly apostrophe hiding a banned phrase.

**Useful router facts learned.** `omniroute simulate -m <model> --explain` prints the
real routing ids and the per-model breaker/quota state; the display names in
`omniroute models` are not routable. `omniroute chat --file` prints its banner and
exits silently on a ~24 KB prompt, so scripted calls should POST to
`http://127.0.0.1:20129/v1/chat/completions` directly. Building the JSON body in
PowerShell turns a 24 KB string into a 461 KB body whose `content` reads back empty —
build it in Python and let PowerShell only POST the bytes.

**Nine blocked tasks** sit on the board, all from the pre-OmniRoute period when the
agent narrated instead of acting: four Mishwan attempts, two recipe-card sites, and
three of my own proof tasks (`heartbeat-proof`, `desktop-proof`, `devstral-proof`).
They are inert and were left alone — they are the user's requests and the evidence of
that period, and neither is mine to delete.

**The approval pause was deliberately NOT built.** The user asked for research, then
"tells me", then makes it. The brief is delivered *with* the result — `BRIEF.md` is a
produced artifact and `.md` is in `completion_report.DOCUMENT_SUFFIXES`, so it is
attached to the completion card. A *blocking* approval step would have been actively
harmful this week: it parks every visual task in `needs_input` waiting for a person who
is away. Worth building when they are back and can say how they want it.

---

## 2026-09-14 (night) — what a live stress test found that 593 unit tests could not

The taste gate was deployed and then put through the real thing: the same
Mishwan request that produced the page the user rejected, with the same logo,
filed on the desktop board and watched. Four attempts, six agent runs. Every
rejection was correct behaviour under a defective instruction, and each one
exposed something no unit test would have.

**Run 59 and run 63 — the agent ran the verifier.** `verify.py` is not a check,
it is a mutation: it moves a run's output into `runs/<slug>/` and commits it.
The agent researched, built the page, and then ran the verifier to check its own
work. Sandbox commit `d2a3c79` at 19:52:32 is the agent's own archive. Seconds
later the worker's real verify found a clean tree and recorded a complete,
researched page as *"this run changed nothing in the workspace"*. Run 63 did it
**after** `HARD_RULES` had been changed to forbid it explicitly.

  **A rule in a prompt is a request, not a control.** `verify.py` now refuses
  unless `TRIAI_RUN_ID` is set, and `executor.agent_env()` strips the whole
  `TRIAI_*` verify identity from the agent's environment, so an agent cannot
  hold one however the worker was started. Exit code 2, distinct from the 1 that
  means "this run failed".

**Run 60 — the brief landed in the home directory.** The agent researched
properly, built `index.html` with `styles.css`, `script.js` and the user's logo
copied in, and wrote `BRIEF.md` to `C:\Users\Ardit II\BRIEF.md`. "Write BRIEF.md
in this workspace" was read as "somewhere sensible", and good work was rejected
over a file one directory away. `taste.brief_block` now takes the workspace and
prints the absolute path.

**Run 62 — the gate threw away a good page over two strings.** A 25 KB page
carrying FSSAI, ISO 22000, Halal, Kosher, Meerut, Uttar Pradesh and a rupee sign
— ten of its twelve promises — failed on `"500 g"` (the page set `500g`) and
`"Rs 1240.00"` (a price invented before building and revised during it). Fixed
three ways: whitespace inside a promise is optional when matching; the page must
keep `min_must_appear` of its promises rather than all of them; and the
instruction asks for the marks of the field rather than invented values, and
says the brief may be edited while building.

**Run 64 — promises were read too literally.** It kept *none* of its ten
promises because it had written them the way models write lists:
`- "FSSAI Certified"`, `- "Rs" (currency symbol)`, `- **500gm**`. Taken at face
value those ask the page to contain the quotation marks. `taste._promise_text`
now reads a quoted span as the promise, otherwise cuts at the first gloss, and
strips markdown emphasis — a cut that only ever makes a promise shorter, because
the failure being guarded against is refusing work that was done.

**Runs 59 and 61 — the brief was treated as the finish line.** Each wrote a good
brief and stopped, spending an attempt and making the next agent redo the same
research. The instruction now says both beats happen in one turn.

### Operational findings from the same session

- **The desktop has 15.8 GB free of 931 GB.** Tri-AI's whole runtime footprint is
  0.6 MB (`.tri-ai/runs` 0.2 MB, `tri-ai-sandbox/runs` 0.3 MB, logs ~0), and
  `.omniroute` is 45 MB. Nothing here is the cause, and everything here stops if
  the disk fills. Enumerating the profile to find the cause timed out twice over
  SSH; worth a look locally.
- **A config error can stop the whole fleet permanently.** The supervisor's
  budget is 8 restarts per hour, after which it "stops the fleet". Earlier today
  `intake_policy.json` referenced `genclarus` — a laptop-only path — and the
  telegram daemon crash-looped until the budget was exhausted. Both scheduled
  tasks now carry a **15-minute repeating trigger** alongside AtStartup and
  AtLogOn; with `MultipleInstances = IgnoreNew` a running instance is untouched
  and a dead one is restarted within 15 minutes.
- **`run_daemons.ps1` waits for the router** (port 20129, up to 180 s) before
  starting the supervisor, then starts anyway. Both are AtStartup tasks with
  nothing sequencing them, and every model call resolves through the router.
- **The daemon logs have no timestamps**, which made two resolved incidents look
  current during triage. Worth fixing.
- **`HERMES_YOLO_MODE` is set by hermes itself**, in `oneshot.py:255`, for every
  `-z` call — see the correction earlier in this file.

### The dashboard every completion card links to was not running

`--dashboard-url http://100.67.149.86:8080` was configured on the daemons task
and **port 8080 on the desktop is held by `svchost.exe`** — the same squat that
pushed OmniRoute off 20128. No dashboard process was registered at all, so every
completion card the user received carried a link that could not resolve.

Now `scripts/run_dashboard.ps1` + the `Tri-AI Dashboard` scheduled task, on
**8081** (checked free), bound to loopback and the Tailscale address with
`--allow-non-loopback` passed deliberately — that flag exists to make exposing
task titles, workspace paths and log tails an explicit choice, and Tailscale is
how the phone reaches it. The dashboard stays strictly read-only: SQLite in
`mode=ro` with `PRAGMA query_only`, no mutating route. The daemons task's
`--dashboard-url` was moved to 8081 to match.

Verified: `http://127.0.0.1:8081/` and `http://100.67.149.86:8081/` both 200.

### Three tasks, one recovery story

`OmniRoute Router`, `Tri-AI Daemons` and `Tri-AI Dashboard` each now carry three
triggers — AtStartup, AtLogOn, and a **15-minute repetition** — with
`MultipleInstances = IgnoreNew`, so a running instance is untouched and a dead
one is restarted within 15 minutes. That covers the case the supervisor cannot:
its own budget is 8 restarts per hour, after which it deliberately "stops the
fleet", and before this that was permanent until a human noticed.

### Two more the gate would have got wrong, found by reading rather than running

**A typeface is a choice, not a network request.** The blocked recipe-card task
says *"Self-contained, no external assets"* in so many words, and the floor
demanded a font from `fonts.googleapis.com` or an `@font-face` rule — so a page
doing exactly what it was asked would have been rejected. The rule was aimed at
the right thing and measuring the wrong one: what was wrong with the page the
user rejected was not that its font came from no server, it was that **nobody
chose**. Its only `font-family` declaration, in full, was Tailwind's default.
`_has_chosen_type` now accepts a loaded webfont *or* a named face in CSS, and
still fails on the framework default and nothing else. Verified against both
real pages, not fixtures. A related bug: the pattern stopped at the first quote,
so `font-family: "Playfair Display", serif` — the common shape — captured
nothing at all.

**A quoted promise is now anchored to the start of its bullet.** Unanchored,
two apostrophes anywhere in a bullet read as a quoted span: `Don't miss the
chef's pick` became `t miss the chef`, a promise no page could keep.

### A mistake worth recording, because it is the session's own lesson

Re-filing the recipe-card request to test the gate on a second subject, the
patch script that rewrote the prompt **printed "request switched" and changed
only the title**. The `.replace()` found no match and said nothing, so a
Mishwan task was filed, ran twice, and its brief — FSSAI, Uttar Pradesh, masala
— looked at first like cross-task memory contamination. It was not. It was a
silent no-op wearing a success message, which is the same defect class as the
loader errors that hid 102 tests and the chunked transfer that kept only its
last fragment. **Assert the replacement matched.** Verified afterwards by
reading the prompt back off the board before watching the run.

### Second subject: the recipe cards, and where the gate is still weak

Your blocked recipe-card request — *"three files: index.html, style.css,
recipes.json ... warm cream-and-terracotta ... Self-contained, no external
assets"* — was re-filed to check the gate had not been tuned to Mishwan. It had
not: the agent's brief cited Google's JSON-LD recipe spec, FSSAI **menu
labelling** (calorific value per serving, serving size, veg/non-veg indicators),
and picked `#FDFBF7` cream with `#C85A32` terracotta, which is what was asked
for. Completely different research, appropriate to the subject.

Two more gate defects fell out of it, both the same shape as the typeface one —
a rule aimed at the right thing and measuring the wrong property:

- **Imagery meant "a file was fetched" when it means "is there any visual
  composition".** Run 68 answered "no external assets" with four CSS gradients,
  two box-shadows, five radii and a Playfair Display / Inter pairing, and was
  rejected for having no `<img>` — the one thing the request ruled out. Counted
  out of the revert stash before changing anything. Gradients now count.
- **Label-plus-value promises cannot be kept by any correct page.** Run 69
  promised `Calories per serving: 399.6` and `Servings: 4`. A page renders the
  label as a heading and the number elsewhere, so the joined string never
  appears contiguously. The instruction now names those exact shapes as what
  not to write, and the last thing it tells the agent is to search its own
  finished page for every bullet and fix whichever end is wrong.

**Still open, honestly.** The promise contract works well for categorical marks
(FSSAI, Uttar Pradesh, 500 g) and badly for value-shaped ones. Two Mishwan
tasks passed cleanly; the recipe-card task has not yet, and after the
instruction changes above one more attempt is running. If it fails again the
next thing to try is not another rule — it is whether `min_must_appear` should
scale with how many promises were made, rather than being a flat floor that a
five-promise brief must clear perfectly while a twelve-promise brief may miss
seven.

---

## 2026-09-25 — the Telegram daemon's real defect, found and fixed

**Root cause: every 4xx was classified permanent.** `HTTP 409` means *another
`getUpdates` is already polling this bot*. It is not a rejection — it is two
pollers contending, and it clears the moment one stops. The client raised
`TelegramPermanentError`, the daemon exited, and the supervisor restarted it.
Observed live on the desktop: **eight restarts in a row**, most of the budget.

Two machines polling one token — a laptop and a desktop, which is this
operator's actual setup — is therefore sufficient to produce an endless
die-and-restart loop. So is one stale process. This is the most likely
explanation for months of "Telegram keeps breaking".

`429` had the same flaw and is transient by definition; Telegram sends
`retry_after` with it.

**Fixed on both error paths.** Telegram reports these two ways: as an HTTP
status, and as `ok:false` with an `error_code` inside a `200` body. Only the
first was found initially; fixing it alone would have left the daemon dying on
409 whenever the API chose the second form. `401` and `404` remain permanent —
a bad token or bad method cannot be waited out and must stop loudly.

### How it was isolated

| check | result |
|---|---|
| `getMe` × 3 | **OK** — `@ardit_tri_ai_bot`, token valid |
| DNS / TCP 443 / HTTPS to api.telegram.org | all fine, no proxy, no exit node |
| `getUpdates` timeout=0 and 10 | **OK** |
| daemon (`--poll-timeout 30`) | 502, TimeoutError, then 409 → exit |

A valid token and a working network alongside a dying daemon is what pointed at
classification rather than connectivity.

### Verified after deploy

```
telegram pid 11036 -> 19036, restarts reset 8 -> 0
two minutes later: same pid, restarts 0
supervisor: telegram healthy for 120s; backoff reset
```

### Desktop now current

`8931f6b`, 771 tests pass on both machines (desktop reports `OK (skipped=1)` —
it has no `~/.claude/skills`, and that check now skips rather than failing).

Runbook facts confirmed in passing: OmniRoute is on **20129** and the dashboard
on **8081**, because svchost holds 20128 and 8080. The dashboard listens on
loopback *and* `100.67.149.86`, as intended.

### Still open

`~/.tri-ai/freellmapi` on the **desktop** listens on `:::3001` — all
interfaces, the same exposure fixed on the laptop today. It needs
`HOST=127.0.0.1` and a restart.

## Telegram UX — what is actually wrong, measured not assumed

Two of my own assessments here were wrong and are corrected below. Do not
re-derive them.

**WRONG 1: "the operator has never run the good version."** All of
`progress_card.py`, `completion_report.py`, `_deliver_documents` and
`send_document` already existed at the desktop's old head `cd37c3a`. The
complaints are about what these produce, not their absence.

**WRONG 2: "the completion card tells you almost nothing."** That verdict came
from feeding `render()` ledger-shaped keys (`reason`, `model`, `verify_exit`)
when it reads board-row keys (`error`, `metadata` as a JSON string, `summary`,
`artifacts`, `ended_at`). With the correct shape it is good:

```
PASSED — Build me a recipe card page for a masala chai
task t_c5374def · run 41
verify: exit 0
took 6m 23s
model deskollama/qwen2.5-coder:7b @ ollama
produced 2 files:
  • index.html   http://100.67.149.86:8081/artifact/t_c5374def/0
  • style.css    ...
```

Progress cards are equally terse — one message edited in place per task:
`⚡ Building · <prompt>` / `task … · workspace … · running 4m`, with phases
claimed → worktree_prep → agent_active → verify_gate → done/failed/cancelled.

### So the real gaps are these, and only these

1. **No conversation.** Plain text creates a task and waits for `/confirm`.
   There is no follow-up, no "change that", no "what's going on" in words. It is
   command-and-card, which matches `phase-4.5`'s read-only design and not how
   the operator wants to work.
2. **Artifacts arrive as tailnet links.** `_deliver_documents` uploads only
   "self-contained text types" under a size cap; everything else is a URL that
   needs Tailscale. Off the tailnet, a finished result is unreachable.
3. **14 commands** for a phone surface.
4. **`/confirm` on every task**, including ones that touch nothing external.

### Recommended shape (not yet built)

Auto-confirm anything that is not send/spend/publish; keep the gate only for
the three roles marked `external=True`. Collapse to natural language plus
`/confirm` and `/stop`. Attach artifacts as files wherever the size cap allows
rather than defaulting to a link. Reply-to-a-card already routes back to its
task (`board.task_for_notified_message`) — that is the seam a conversation
should be built on.

## Session handover — 2026-09-25, context exhausted at 93%

Branch `codex/freellm-router`, head pushed to GitHub, **771 tests pass on both
machines**. Desktop is current and its daemons run the merged code.

Done today: Step 0 (commit + clean-clone verify), decomposer, stack_profile,
lane_select, cartographer, roles 7→29, capability merge fix, Hermes junction
repair + 29 profiles, registry accuracy, FreeLLMAPI 13 vulns → 0, the
laptop's `HOST=127.0.0.1` bind fix, the two-machine history merge, and the
Telegram 409 fix.

**Next, in order:** (1) the Telegram UX shape above; (2) Step 5, the evolution
loop — intake verdict plus the ledger→proposal promotion ladder, extending
`src/proposals.py` and `src/memory/evolution.py`; (3) the 22 "produced nothing"
failures, the largest single failure mode and unrelated to Telegram.

**Not a bug:** the node process on desktop `:::3001` is an unrelated app of the
operator's (`node index.js` from cmd.exe), not FreeLLMAPI, which is not
installed there. An earlier note in this file inferred otherwise from an open
port.
