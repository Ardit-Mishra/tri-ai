# Phase 2 Plan — Verify-Gated Single-Worker Execution

**Status:** complete — 89 tests, exit 0
**Requirements:** VERIFY-01, VERIFY-02, VERIFY-04, VERIFY-05, EXEC-02, EXEC-03, EXEC-04,
TRIG-01, TRIG-02, TRIG-03
**Depends on:** Phase 1 (`23dbfb4`)

## Goal

One worker claims a subtask from the board, executes it, and accepts or reverts **strictly by the
verify command's exit code**. Phase 1 built somewhere to put that command; Phase 2 is the first
phase where Tri-AI actually does work.

Decisions already taken: build all five criteria in one pass; the worker runs on the **laptop**,
deferring the move to the always-on desktop to explicit later work rather than assuming it now.

## Approach: port the proven core, do not rewrite it

`src/run_queue.py` already implements verify-gated execution, revert-on-failure, a ledger, and the
prompt scaffolding Hermes needs. It has a 9/9 record (`evidence/ledger.jsonl`). Phase 2 moves that
onto the board; it does not reinvent it. Lift verbatim where noted.

### `src/executor.py` — the single execution path

Everything that runs a subtask goes through one function, so the three trigger paths cannot drift
apart. That identity is criterion 4 and it is a structural property, not a test.

| piece | source |
|---|---|
| `git()`, `run_agent()`, `run_verify()` | the only three process gateways — see the safety section |
| `CD_PREAMBLE`, `HARD_RULES` | lift verbatim from `run_queue.py:56-72` |
| `precheck(repo, expected_branch)` | branch guard from `run_queue.py:126-133`, **plus a clean-tree guard** |
| `check_upstream_artifacts(conn, task)` | new — VERIFY-04 |
| `run_agent(task)` | `hermes -z <prompt>`, timeout from the task |
| `run_verify(task)` | the task's `verify_command` in the repo; **its exit code is the only thing that decides** |
| `revert(repo)` | `git stash push --include-untracked` — see below |

The `cd` preamble is load-bearing, not decoration: Hermes' terminal starts in the user's home
directory and `cd` does not persist between its commands, so an un-prefixed command reports "not a
git repository" from the wrong directory and a working task looks broken.

`check_upstream_artifacts` runs **before the agent is invoked at all**. For every parent, each
declared `expected_artifacts` path must exist and be non-empty; otherwise the subtask fails its own
verify step immediately. A fabricated upstream result must not get a chance to propagate. It needs
no git at all — it is a filesystem check.

**Existence and non-emptiness are not sufficient on their own.** An artifact path that is absolute,
contains `..`, or resolves through a symlink could point at an unrelated non-empty file and satisfy
the check while proving nothing about the upstream task. So each path is resolved with
`os.path.realpath` and asserted to be **contained within the parent task's workspace root**, itself
realpathed; absolute paths and traversal are rejected at assignment time as well. Otherwise VERIFY-04
is satisfiable by any non-empty file on the machine, which is a gate that cannot fail.

### Revert: stash, never delete

`run_queue.py:152` reverts with `git checkout -- .` alone, which restores tracked files and leaves
untracked ones behind — so a task that *creates* a bad file survives its own revert.

The obvious fix, `git clean -fd`, is wrong, and the reasoning that made it look safe does not hold.
The clean-tree precondition proves the tree was clean **at claim time**; it says nothing about who
wrote to it afterwards. "Single worker" excludes another Tri-AI worker — not an open editor, a file
watcher, a formatter, an unrelated scheduled job, or a child process that outlived the agent's
timeout. Any of those can drop a non-ignored file into the repo during the run, and at revert time
that file is indistinguishable from one the task created. `git clean -fd` would delete it.

So the revert never deletes:

```
git stash push --include-untracked --message "triai-revert:<task_id>:<run_id>"
```

One command. It restores tracked modifications and removes untracked files in the same operation,
leaving a clean tree — and everything it took is recoverable with `git stash pop`. If an external
writer's file gets caught, it is retrievable rather than gone.

Limits, stated rather than discovered later. The last two were measured in a disposable repo during
review, not reasoned about:

- **Ignored files are untouched**, exactly as with `clean -fd` — `.env`, `node_modules`, `venv` and
  build caches survive, because `--include-untracked` does not imply `--all`. A task that writes a
  *gitignored* file leaves it behind. Accepted; not a total rollback, and not claimed as one.
- **Empty untracked directories are removed and cannot be restored.** Git does not track
  directories, so a stash has nothing to recover. "Never deletes" is true of file *content*, not of
  empty directory structure. Rare in practice; stated because the claim would otherwise be false.
- **A no-change revert creates no stash.** `git stash push --include-untracked` prints
  `No local changes to save` and exits **0** with no new entry. So the worker must not assume a
  stash exists: it checks whether one was created and records either the stash message or the
  outcome `no_changes_to_revert`. Recording a stash reference that does not exist would be a
  fabricated recovery path in the one file that must contain none.
- **Stashes accumulate** when there *is* something to save. Each is traceable by its ledger entry. A
  documented `git stash list | grep triai-revert` cleanup is an operator action, never automatic —
  the point is that nothing is destroyed without a human deciding.
- **A dirty tree at claim time is a skip, not a fix.** The worker refuses the task and records the
  skip. It never touches a repo it did not dirty.
- **Phase 3 needs more.** Concurrent workers on one repo break the precondition entirely and must
  partition repos per worker or move to per-subtask worktrees — `kanban_db` already models
  `workspace_kind='worktree'`, so the substrate is there.

### `src/ledger.py` — the auditable record

Append-only JSONL, extending `run_queue.py:record()` with the two fields EXEC-03 requires that it
lacks today: **worker identity** (`host:pid` — the same string the board uses as its claim lock, so
a ledger line joins to a board row) and **model**.

Full agent and verify output goes to `runs/<task_id>/<run_id>/{agent,verify}.log`, with the ledger
carrying the paths. `run_queue.py` truncates to the last 800 characters; Phase 4's OBS-04 requires
untruncated stdout/stderr, so capture it now rather than re-plumbing later.

**The model field comes from the run, not from config.** `hermes -z --usage-file <path>` writes a
JSON record containing `model` and `provider` for the run that actually executed
(`hermes_cli/oneshot.py:181-182`), alongside token counts and `session_id`. The worker passes
`--usage-file`, reads it back, and records `model` and `provider` from it.

This closes what was an open question in the first draft of this plan, which proposed recording the
*configured* model labelled as configured. That was defensible but weak — the route registry's own
rule is that identity is the resolved model plus adapter path, never the route name, and a dynamic
route resolves differently run to run. A configured-model field would have been an assertion in a
file whose entire purpose is that it contains none. If the usage file is missing or lacks `model`,
the ledger records `null` and a `model_source` of `"unavailable"` — never a guess.

### `src/worker.py` — the loop

`board.release_stale_claims(conn)` → select a ready task → `kb.claim_task` → `executor` →
`kb.complete_task` on verify exit 0, else revert and record the failed attempt → ledger → release
→ repeat. A cleanly reverted ordinary verifier failure returns to `ready` for a **bounded fresh
claim**. The kernel records every run and trips its per-task failure circuit breaker (default: two
failed attempts), leaving the task `blocked` rather than allowing an unbounded loop. A hard-stop is
reserved for an unsafe or unknown workspace state: surviving process tree, failed revert, or dirty
post-revert assertion.
Flags: `--once`, `--max-tasks N`, `--dry-run`.

**It must call `board.release_stale_claims`, never `kanban_db.release_stale_claims`.** Going direct
reintroduces the Windows defect from Phase 1: the kernel infers "worker already gone" from a
`ProcessLookupError` that Windows' `os.kill` never raises, so every tick defers the reclaim forever
and a dead worker's task stays `running` — presenting as a hang, not an error. Asserted by a test
in criterion 5's audit, not left to memory.

### `src/assign.py` — three triggers, one write path

All three **assign** through one `assign()` that writes via `board.create_task`, so the
claim→verify→ledger path is identical by construction rather than by convention.

- **TRIG-01** laptop CLI — `python -m assign --repo … --prompt … --verify …`
- **TRIG-02** queue file — `--from-queue queue.jsonl`, reusing `run_queue.py`'s existing JSONL
  format and its `load_queue()` parser (`run_queue.py:88`), including the `//` comment convention
- **TRIG-03** schedule — `scripts/register-triai-task.ps1` registers one Windows Scheduled Task
  action, `scripts/run-triai-scheduled.ps1`, which runs **`assign --from-queue chores/nightly.jsonl`
  and only then `worker --max-tasks N` when assignment exits 0**.

TRIG-03 needs that first step spelled out, because "run the worker on a schedule" is *not* a
trigger: a worker claims work that already exists, it never creates any. Scheduling only the worker
would mean the scheduled path never calls `assign()` at all, and criterion 4's identical-path claim
would be false for one of its three cases. Assignment on a schedule is the requirement; running the
worker afterwards is the operational convenience that makes it useful unattended. Task Scheduler
guarantees ordering for multiple actions, not this required failure gate, so the runner makes the
exit-code condition explicit.

### `src/chores.py` + `scripts/calibrate_verify.py` — VERIFY-05

The v1 chore set with its verify commands per repo. EXEC-02 holds v1 to read-only CPU/IO-bound
chores: test suite, typecheck, build, dependency audit.

`calibrate_verify.py` proves each verify command can actually fail: clone the target repo into the
scratchpad, break it deliberately, run the verify, assert non-zero, record to
`docs/verify-calibration.json`. **It never touches a real repo.** A gate that cannot fail is not a
gate — but proving that must not cost a working checkout.

Four calibrations, each with its own deliberate break:

| chore | verify command | deliberate break |
|---|---|---|
| test suite | `pytest -q` / `npm test` | assert a false statement in one test |
| typecheck | `tsc --noEmit` / `mypy` | assign a string to an int-typed name |
| build | `npm run build` | introduce a syntax error in an entry module |
| dependency **advisories** (JS) | `npm audit --audit-level=high` | pin a version with a known advisory |
| dependency **compatibility** | `uv pip check` | install two packages with conflicting pins |

**Python advisory auditing does not ship in v1.** It failed calibration and is deferred, which is
the process working rather than a gap: `uv tool run --from pip-audit==2.10.1 pip-audit --help` runs,
but the real audit of a `django==2.2.0` fixture returned no verdict and no exit code within the
review window. A verify command that can hang is worse than one that is missing — the worker stalls
holding a claim, and Phase 1's reclaim machinery has to rescue what should have been a clean
failure. Two further problems found alongside it: the "no install step" claim was wrong (uv built a
28-package ephemeral environment, with downloads), and this repo has no `requirements.txt`, so the
command was target-repo dependent in a way the table hid. It can return once someone establishes a
bounded, offline-capable invocation and calibrates it. Until then, Python repos get the
compatibility check only, and the gap is stated rather than papered over.

The audit row was originally one chore pairing `npm audit` with `uv pip check` against a single
"pin a version with a known advisory" break. That is wrong: `uv pip check` validates that installed
packages' dependency constraints are mutually satisfiable — it does not consult any advisory
database, so an advisory-bearing pin exits 0 and the gate proves nothing. They are two different
checks and each needs the break that its own command can actually detect. This is precisely what
criterion 3 exists to catch, and it was caught before any code was written rather than by a chore
silently passing in production.

Calibration must confirm two things for every shipped command: that it runs at all, and that it
exits non-zero on the deliberate break. If either fails, the chore does not ship — a verifier that
cannot be calibrated is not a gate. That rule is what removed Python advisory auditing above.

### Timeout is an outcome, not a hang

Phase 1 added a `verify_timeout` column; this plan originally never said what firing it means.
Stated now, because the `pip-audit` calibration demonstrated a verify command can simply not return:

**"Killed" must mean the whole process tree stopped.** `subprocess.run(..., shell=True, timeout=…)`
kills the shell, not its descendants — on Windows the grandchildren survive. A verify command that
spawns a writer can therefore "time out", keep writing, and race the revert and the next claim,
producing corruption that looks like nothing went wrong. So the timeout path is:

1. Spawn the verify command in a Windows **Job Object** before it can execute (the primary process
   starts suspended, joins the job, then resumes), not as a bare shell child.
2. On timeout, terminate the Job Object atomically and enumerate it for survivors.
3. Confirm the tree is actually gone. **If it is not, quarantine the workspace durably, then stop**
   — do not stash, do not claim anything else. A repo with a live unknown writer in it is not a
   state to keep working in.
4. Only then stash, then re-assert `status --porcelain` is empty.

**The quarantine must outlive the worker, because Phase 1 is designed to undo an in-memory stop.**
A worker that merely exits achieves nothing durable: its lease expires, `board.release_stale_claims`
correctly reclaims the task to `ready` (`src/board.py:282`), and the next worker runs against the
same repo with the same live writer in it. The reclaim mechanism is not wrong — it is doing exactly
what Phase 1 built it to do — which is why the stop has to be recorded somewhere reclaim cannot
reach:

- Before exiting, the worker writes a **workspace-scoped quarantine record** keyed by the resolved
  repo path, carrying task id, run id, timestamp, the surviving PIDs, and the reason. It goes on the
  board, since coordination belongs there and Phase 4 must be able to see it.
- `precheck()` refuses any task whose workspace is quarantined, in **every** worker, not just the
  one that set it.
- A refused task is **blocked**, not failed and not left ready — `kanban_db.block_task` with a block
  kind exists for this. Failing it would let it spin: claim, refuse, reclaim, claim again.
- Only an operator clears a quarantine. That is the point; nothing automated may decide a repo with
  an unaccounted-for writer is fine now.
- The test is the **restart path**, not the timeout path: time out a verifier whose tree survives,
  let the worker stop, let the claim be reclaimed, start a second worker, and assert it refuses the
  workspace and blocks the task. Testing only the immediate timeout would have passed against the
  broken design.

`tests/test_verify_timeout.py` proves it: a verify command that spawns a delayed writer and exits;
after the timeout fires and the revert completes, the file the writer would have created must not
exist. Without that test this is an assumption, and assumptions about process lifetimes on Windows
have already been wrong once in this project (Phase 1's reclaim defect).

**Timeout is a distinct outcome, not an exit code.** `run_queue.py:76` maps a timeout to exit `124`,
but `124` is a value a real verify command may legitimately return, so overloading it makes two
different results indistinguishable. `run_verify` returns a discriminated `VerifyResult` with
`outcome` in `passed | failed | timeout | spawn_error` plus an `exit_code` that is `None` for the
non-exit cases. The ledger records both. Only `passed` accepts; everything else reverts.

**`verify_timeout` is enforced twice, not documented once.** `board.create_task()` accepts
`verify_timeout=None` today (`src/board.py:177`), so "mandatory" would be a comment rather than a
rule. `assign()` rejects a task without one at write time, and the worker re-checks before
execution and refuses to run a task lacking it — covering rows written directly through the
lower-level board API. Calibration records each command's observed runtime so the value is set from
measurement rather than guessed, the same discipline Phase 3 applies to its concurrency cap.

## The safety boundary — mechanism, not a request

Criterion 5 originally read: the worker's source contains no `git push`, no `git merge`, no deploy
tooling, no credential access, proven by grep — with the agent additionally *instructed* not to do
those things by `HARD_RULES` in the prompt.

That is not a control, and the reason is the project's own thesis pointed at itself. `hermes -z`
sets `HERMES_YOLO_MODE=1` and `HERMES_ACCEPT_HOOKS=1` unconditionally
(`hermes_cli/oneshot.py:255-256`), auto-approving every shell and tool call. The agent can run any
command it likes, from anywhere, regardless of what our source contains. A prompt asking it not to
is **the agent's own report** — an assertion about its behaviour with no oracle behind it. Grepping
our own source proves only that *we* do not call `git push`; it says nothing about the subprocess
that can. Worse, a literal grep for the forbidden strings matches the prohibition text in
`run_queue.py:68` itself, so the test would pass on a file that merely mentions them.

The second draft of this plan then over-corrected, promising that "nothing autonomous can push,
merge, deploy or read credentials" on the strength of a scrubbed environment and a `pre-push` hook.
Review demolished that too, with evidence: `git push --no-verify` bypasses the hook outright
(measured against a disposable local remote), the agent can simply delete or rewrite the hook file,
`GIT_CONFIG_GLOBAL` leaves the **system** and **repository** config levels untouched, and Windows
Credential Manager plus any inherited SSH agent remain reachable to any process running as this
user. `ssh` and the GitHub REST API bypass `git` entirely.

**That containment claim is withdrawn.** It is not deliverable by in-repo means against an
unrestricted child process, and the roadmap never asked for it — criterion 5 is a claim about *our
execution path*, not about what a YOLO subprocess is capable of. Restated to exactly what is true:

**Preventive, and provable.** The worker's own code never invokes `git push`, `git merge`, deploy
tooling, or credential reads. That is criterion 5 and it is what the roadmap asks — but a text grep
cannot prove it. `subprocess.run(["git", "push"])` contains no substring `git push`, and a
subcommand assembled at runtime contains nothing to match at all. So the property is made
structural rather than textual:

**Exact argv forms, not subcommands.** A subcommand-level allowlist is the wrong granularity:
`stash` also spells `stash clear`, and `branch` also spells `branch -D`. `executor.git()` matches
the **complete argument vector** against a fixed table, and everything Phase 2 needs is on it:

| form | used by |
|---|---|
| `status --porcelain` | clean-tree precheck; post-revert assertion |
| `branch --show-current` | branch guard |
| `rev-parse --show-toplevel` | confirm the workspace really is the repo root |
| `stash push --include-untracked --message <msg>` | revert |
| `stash list` | confirming the revert actually stashed |

`stash list` is enough to prove a stash was created only if the message is unique per attempt and
matched exactly, so it is `triai-revert:<task_id>:<run_id>` — `run_id` is per-attempt, so a retry of
the same task cannot match its predecessor's stash and report a revert that did not happen.

Nothing else. `checkout` and `worktree` were dropped — the stash revert does not need `checkout`,
and worktrees are Phase 3. `clean` was dropped as a straight contradiction: an earlier draft used
`git clean -fd` and, when the revert changed to a stash, the capability was left behind. That is the
argument for an allowlist over prose in one line.

**Two process-creation gateways, and only two.** The audit rule "dynamically built commands fail"
cannot coexist with `sh(cmd, shell=True)` inherited from `run_queue.py` and with running an
operator-authored `verify_command`. The implementation narrows process creation to two functions:

| gateway | contract |
|---|---|
| `executor.git(argv)` | exact-form table above; argv list; **no shell** |
| `executor.spawn_contained(command, **kwargs)` | the only child-process creator; attaches every child to its Job Object before it runs |

`run_agent()` supplies fixed Hermes argv to `spawn_contained()` with no shell. `run_verify()` supplies
the operator-authored command with `shell=True`; it is the single shell exemption, named separately
because the verify command is deliberately unconstrained. The worker's own reach is provably narrow;
the verify command's is deliberately not.

**An AST audit over an explicit closure.** `tests/test_safety_boundary.py` parses `executor`,
`worker`, `ledger`, `assign`, `chores`, `board` and asserts that no `subprocess.*`, `os.system` or
`os.popen` call site exists anywhere in that set **outside the two process-creation gateways**. It also
rejects, within the closure, the shapes that would let a call escape the check: aliasing a process
function to another name, `getattr`-dispatch, `functools.partial` over one, dict-dispatched process
functions, star imports, and dynamic `__import__` / `importlib`. Without those rejections
"unprovable is unsafe" is a slogan rather than a property. A helper added later that is called from
the closure but not in it fails the audit.

The text grep survives only as a cheap secondary check, scoped to exclude the prompt constants so it
cannot pass on the prohibition text in `run_queue.py:68`.

**Defence in depth, honestly labelled.** The child environment is still built explicitly rather
than inherited (`GIT_TERMINAL_PROMPT=0`, no `GH_TOKEN` / `GITHUB_TOKEN` / `GIT_ASKPASS`,
`GIT_CONFIG_GLOBAL` with no credential helper), and the workspace still gets a `pre-push` hook.
These raise the cost of an accidental push. **They do not stop a determined or confused agent**, and
the plan must not be read as claiming otherwise.

**Diagnostic, but not tamper-evident.** Work happens on feature branches, every attempt is in the
ledger with full captured output, and `git reflog` is inspectable afterwards. These are genuinely
useful for understanding what a *cooperative* run did — which is every run in practice.

They are **not** a reliable detector of a hostile one, and the earlier draft of this plan called
them "the control that actually carries weight", which was the withdrawn containment claim wearing a
different hat. A YOLO child runs as this user and can edit the ledger file, run
`git reflog expire --expire=now --all`, or push a branch and then force-delete the remote ref — after
which the remote's visible history need retain nothing. Provider-side audit logs are not guaranteed
on a personal account and are not part of this plan.

Real tamper-evidence would require the ledger to be written where the child cannot reach it — an
append-only store on another host, or a signing step outside the worker's process. That is not built
and is not claimed. What is true today: these artifacts reliably explain cooperative runs and would
catch an *accident*; they would not survive deliberate tampering.

**The residual risk, stated plainly.** An agent running under `HERMES_YOLO_MODE=1` on this account
can in principle reach stored credentials and push. The only mechanisms that genuinely close it are
OS-level: a dedicated low-privilege Windows account with no stored git credentials and no SSH keys,
or a container/Windows Sandbox with no credential store and restricted egress. **Running the worker
under a credential-free account is therefore a documented operator prerequisite for unattended
use**, not something this phase's code can assert on its own. Building that containment is a
candidate for its own phase; it is explicitly not claimed here.

`tests/test_safety_boundary.py` still runs the adversarial push against a fixture repo with a fake
remote — it is worth having, because it proves the ordinary route is closed and would catch a
regression in the env scrubbing. It is labelled as covering the ordinary route only, and the
`--no-verify` and hook-deletion bypasses are recorded in the test file as known-uncovered.

## Success criteria → how each is proven

Every row is an exit code, not a judgement.

| # | criterion | proven by |
|---|---|---|
| 1 | pass → done; fail → revert before the next claim | `tests/test_worker_verify_gate.py`, both paths: a **passing** task reaches `done` on the board and writes a ledger entry with `verify_exit=0`; a **failing** task that edits a tracked file *and* creates an untracked one leaves `git status --porcelain` empty before the fresh retry claim, with the stash recoverable. A separate loop test proves the kernel records two failed attempts then blocks the task through its circuit breaker |
| 2 | missing/empty upstream artifact fails its own verify | `tests/test_upstream_artifacts.py`: missing case, empty-file case, and **escape cases** — absolute path, `..` traversal, and a symlink pointing outside the parent workspace at a real non-empty file — each asserting the agent was **never invoked** |
| 3 | every shipped verify command proven to fail on broken input | `scripts/calibrate_verify.py` run; `docs/verify-calibration.json` committed, showing a recorded non-zero exit per command in the table above — five commands, five distinct breaks |
| 4 | three triggers, identical path, complete ledger entry | `tests/test_triggers.py`: CLI, queue file, and the scheduled runner's queue action each produce a board row and execute it through `worker.run_once`; every resulting ledger entry carries worker identity, `model` + `provider`, verify exit code, and duration. The scheduled runner test also proves a non-zero assignment exit prevents worker startup |
| 5 | the **worker codepath** contains no push, merge, deploy or credential access | `tests/test_safety_boundary.py` — scoped source audit (excluding prompt constants), the ordinary-route adversarial push test, and an assertion that the worker calls `board.release_stale_claims` and never the kernel's. Scope limit recorded in the test file: this does **not** prove a YOLO agent cannot push by other means |

## Verification

```
tests\run.ps1        # full suite; exit 0
```

Plus a real end-to-end run against one repo's test-suite chore, assigned all three ways, producing
three ledger entries with complete fields. After a deliberately failing task,
`git status --porcelain` in the target repo must be empty **and**, when the task changed anything,
`git stash list` must show the matching `triai-revert:` entry — a clean tree with nothing stashed,
after a task that wrote files, would mean the revert deleted rather than preserved. A task that
changed nothing must instead record `no_changes_to_revert` and create no stash.

## Review record

This plan was reviewed before implementation and changed in five places as a result. Kept here
because the changes are the argument for having a separate review seat at all:

- Criterion 5 rested on a prompt instruction plus a grep of our own source. `hermes -z` auto-approves
  every shell call, so the prompt was an assertion with no oracle — the exact pattern this project
  rejects — and the grep would have matched its own prohibition text. Replaced with a
  credential-free child environment, a `pre-push` hook, and an adversarial test that tries to push.
- TRIG-03 scheduled the *worker*, which claims work rather than creating it, so the scheduled path
  would never have called `assign()` and criterion 4's identical-path claim would have been false
  for one of its three cases.
- The dependency-audit calibration paired `uv pip check` with an advisory-bearing break it cannot
  detect. Split into two chores with breaks each command can actually see.
- `git clean -fd` was justified by a precondition that only excludes other Tri-AI workers, not
  editors or watchers. Replaced with a recoverable stash.
- The `model` field was to be recorded as configuration; `--usage-file` reports the executed run's
  model and provider, so it is now provenance.

A second review round then broke the *replacement* safety design, with measurements:

- `git push --no-verify` bypasses the `pre-push` hook — demonstrated against a disposable local
  remote — and the agent can also delete the hook, use `ssh` or the GitHub API directly, or reach
  Windows Credential Manager, which `GIT_CONFIG_GLOBAL` does not cover. The containment claim was
  withdrawn rather than patched, criterion 5 restated to what the roadmap actually asks, and the
  residual risk plus its only real remedy (a credential-free account or a sandbox) written down.
- `pip-audit` is not installed here, so a bare invocation would exit non-zero as "not found" and
  read as a *failing gate* — worse than no gate. Pinned to an ephemeral `uv tool run`.
- `git stash push --include-untracked` removes empty untracked directories unrecoverably, and on a
  no-op exits 0 without creating a stash. Both limits now stated; the worker records
  `no_changes_to_revert` rather than a stash reference that does not exist.

A fifth round found the state-machine hole the previous fix created:

- The timeout hard-stop was in-memory, so it did not survive the worker. Phase 1's reclaim — working
  exactly as designed — would return the task to `ready` and a restarted worker would run against
  the same repo with the unaccounted-for writer still in it. Safety state must be at least as
  durable as the mechanism that undoes it. Now a board-recorded, workspace-scoped quarantine that
  every worker honours and only an operator clears, with the task **blocked** rather than failed so
  it cannot spin. The test is the restart path, not the timeout path.
- Noted in passing and equally real: the artifact check could be satisfied by an absolute path, a
  `..` traversal, or a symlink resolving to any non-empty file on the machine — a gate that cannot
  fail. Now realpath-contained within the parent's workspace.
- `stash list` proves a stash exists only against a per-attempt-unique message, so it carries
  `run_id`; otherwise a retry could match its predecessor's stash and report a revert that never ran.

A fourth round broke the replacements again:

- The `git()` allowlist still authorised `clean` — left behind when the revert changed to a stash,
  so the plan forbade the operation in prose while permitting the capability. `checkout` and
  `worktree` were unneeded. And subcommand granularity was wrong: `stash` also spells `stash clear`,
  `branch` also spells `branch -D`. Now an exact-argv table of five forms.
- The AST rule contradicted its own implementation: `sh(shell=True)` and the operator-authored
  verify command are both dynamic, so the audit would have failed `run_verify` or exempted `sh()`
  broadly enough to void the property. It first resolved to three named gateways, the verify
  launcher being the single deliberate exemption, plus rejection of the aliasing/`getattr`/`partial`/
  dict-dispatch shapes that would otherwise let a call slip the check. The final Job Object
  implementation narrowed actual process creation further to `git()` and `spawn_contained()`.
- "Killed" did not mean stopped: `shell=True` + `timeout` kills the shell, not the Windows child
  tree, so a timed-out verifier could keep writing during the revert. Now a process-tree kill with a
  hard-stop if the tree survives, and a test that spawns a delayed writer to prove it.
- Timeout was to be recorded as exit `124`, a value a real verifier may legitimately return.
  Replaced with a discriminated `VerifyResult`. And `verify_timeout` "mandatory" was documentation
  only, since `board.create_task` accepts `None`; now enforced at assignment and re-checked by the
  worker.

A third round before that broke three:

- A scoped *text* grep cannot see `subprocess.run(["git", "push"])` or a runtime-assembled
  subcommand. Replaced with a single allowlisted `git()` chokepoint plus an AST audit over a named
  module closure, so the property is structural rather than textual.
- "Detective, and real" was the withdrawn containment claim in different clothing: a YOLO child can
  edit the ledger, expire reflogs, and force-delete a pushed remote ref. Downgraded to diagnostic
  and explicitly not tamper-evident, with what real tamper-evidence would require written down.
- The Python advisory command failed calibration — no verdict, no exit code, inside the review
  window — so it does not ship. That in turn exposed that `verify_timeout` had no defined semantics
  anywhere in the plan; a timeout is now an explicit failure outcome, distinct from a non-zero exit.

Every item across all three rounds was found by review, not by the author. Several would have
shipped as silent failures rather than errors — a hanging gate, a grep that passes on prohibition
text, a recovery reference to a stash that was never created.

The commit-level review found one further operational failure and corrected it before handoff:

- A failed verifier returning to `ready` is intentional when the revert establishes a known-clean
  workspace: it is a new, fully recorded claim and is bounded by the kernel's circuit breaker.
  The review adds a loop test proving exactly two failed attempts occur at the default limit, then a
  `gave_up` event blocks the task. Unsafe or ambiguous states still quarantine and hard-stop.
- The scheduled task used two sequential actions and assumed a failed `assign` would suppress the
  worker action. The registration now schedules one runner that checks assignment's exit code before
  launching the worker; both its syntax and the control-flow ordering are tested.

## Explicitly not in this phase

The planner, concurrent workers, Telegram, and the move to the desktop. Phase 3 sets its worker
count from `concurrency_results.json` rather than a guess — and note that the measured ~16%
concurrent-generation ceiling applies to *generation*, not to the CPU/IO-bound chores v1 targets.
Those are different bottlenecks and must not be conflated.
