# Project State

## Project Reference

See: `.planning/PROJECT.md` (audited 2026-09-10)

**Core value:** A task assigned once gets decomposed, executed in parallel by free local models, and verified by exit codes — without the expensive model staying in the loop.
**Current focus:** operational worker/Telegram daemon tooling is verified
locally. The next step is an operator-started live phone exercise; no live
Telegram request was made during implementation. Route admission is complete,
but executor routing remains intentionally disabled until an
operator-configured Hermes profile has real measured evidence.

**Approved future direction:** `.planning/research/capability-expansion-design.md`
defines local verified promotion, RAG, measured routing with optional
operator-configured cloud/free lanes and desktop-local failover, semantic memory,
and constrained self-evolution. These features are approved. The operator
authorized sequential implementation of Phase 5A-C on 2026-09-11. The active
plan is .planning/phases/phase-5-control-worker-routing-plan.md. Each slice
remains separately verified and committed; no live credential is inspected and
no external proxy request is made during implementation.

## Current Position

Phase: 5 of 8 (Memory, Telegram Control, and Intake)
Plan: .planning/phases/phase-5-control-worker-routing-plan.md
Status: Phases 1-3 are complete. Phase 4's local adapter and HTTPS transport
are verified; an operator independently exercised the read-only phone path on
2026-09-11 against task t_baa70e9a, which reached done after two retained
reclaims. Phase 5A-C is complete local implementation work. Slice 2's
implementation/review correction (`a71ff9e` / `02b90f8`) is now covered by
adversarial regression tests in `8ea0d03`. Slice 3 is accepted on local,
exit-code evidence under the operator's explicit continuation instruction; the
separate review service was unavailable and is recorded as such.

**Canonical branch:** `phase-2/worker-assign`. The audited implementation base
was `00671d9`; the phase/plan audit record was committed as `75e188f`.

**Latest canonical verification:** `python tests/run.py` → **230 tests, exit
0, 147.921s** (2026-09-11). This verifies Phase 5A confirmed Telegram
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

**Current implementation:** operator authorized Phase 4.5 Telegram long-poll
transport. Plan: `.planning/phases/phase-4.5-telegram-transport-plan.md`.
The daemon alone will read `TRI_AI_TELEGRAM_BOT_TOKEN` at operator-start time,
will require an explicit chat-id allowlist before invoking the adapter, and
will hard-stop on transport failure. No live credential or Telegram request is
used during implementation or tests.

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

Last session: 2026-09-10
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

**Current canonical test result:** `python tests/run.py` → **137 tests, exit 0,
136.158s** (2026-09-11).

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

Next: Phase 4 Slice 2 adversarial correction tests, then independent review.
Do not merge any branch or start Slice 3 before that gate passes.

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

Next: execute `.planning/AUTONOMOUS-RUNBOOK.md` for Phase 4.

Note: `~/.claude/skills/` was destroyed in the 2026-09-06 incident and is NOT in the `S5-claude-r3`
archive — that archive stopped at `./profiles/`, before reaching `./skills/`. So the whole GSD suite
(`gsd-plan-phase`, `gsd-execute-phase`, ~60 skills) and the custom `research-repo-grade` skill are
gone. GSD is a marketplace plugin and can be reinstalled; `research-repo-grade` was custom and is not
in `skills-lock.json`, so it is lost. Until GSD is reinstalled, phases are planned directly against
the roadmap's success criteria rather than via `/gsd:plan-phase`.
Resume file: None
