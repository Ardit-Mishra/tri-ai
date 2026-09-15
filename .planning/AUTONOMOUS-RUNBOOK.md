# Autonomous Delivery Runbook

This is the resumable operating contract for Codex or Claude. Follow it before
any implementation work. It exists so a usage-limit handoff changes the model,
not the standards, state, or checkout.

## Canonical Checkout Gate

The only canonical checkout is `C:\Users\ardit\tri-ai`.

```powershell
Set-Location C:\Users\ardit\tri-ai
git rev-parse --show-toplevel
git status --short --branch
Get-Content .planning\STATE.md
```

The first command must resolve to `C:/Users/ardit/tri-ai`. Stop if it does not.
Do not use or merge the Omniroute temp clone under
`C:\Users\ardit\AppData\Local\Temp\claude\...\scratchpad\p0a\tri-ai`; it is unrelated history.

## Standing Boundaries

- Never push, merge, deploy, create a remote, or read credentials.
- Never edit the Hermes kernel. Read `C:\Users\ardit\AppData\Local\hermes\hermes-agent` only.
- Acceptance is the verify command's exit code, never an agent report.
- Preserve evidence. A cleanly reverted verifier failure is a bounded fresh
  claim; unknown workspace state (surviving process tree, failed revert, or
  dirty post-revert assertion) quarantines and hard-stops.
- Run `python tests/run.py` before every local commit. Keep failures and logs;
  do not use `git reset --hard`, delete evidence, or conceal a failed attempt.

## Phase 2 Baseline

Phase 2 is complete locally on `phase-2/worker-assign`. The post-review suite
passed 89 tests, exit 0, in 93.951 seconds on 2026-09-10. It is deliberately
unpushed. Do not revisit it except for a concrete regression.

## Phase 4 Objective

Build **Read-Only Telegram Observability** in four reviewed slices:

1. Correct the `dispatch_one` launcher contract and remove dead dispatcher
   code; an invalid launcher result must abort public concurrent dispatch.
2. Create isolated worktrees for same-repository concurrent tasks.
3. Classify environment failures separately from logic failures without
   weakening ledger evidence or the circuit breaker.
4. Render board and ledger evidence through a Telegram read surface with no
   remote mutation path.

## Current Checkpoint

Phase 3 is COMPLETE and independently reviewed at
`.planning/reviews/phase-3-review.md`. Phase 4 Slices 1 and 2 are accepted
(`2369613`, `8ea0d03`). Slice 2's correction in `02b90f8` now has adversarial
proof for exact-source/branch adoption and corrupt-record refusal before claim.
The canonical suite passed: `python tests/run.py` — 139 tests, exit 0,
109.003s.

Slice 4's read-only adapter scaffold is verified locally. It has no transport,
credential, account, network, process, or mutation capability. The overnight
hard stop is now active: do not begin Phase 5, add a live Telegram transport,
or take external action without new operator direction.

## Safe Fan-Out

Fan out only read-only investigation first. Do not let multiple agents edit the
same checkout.

### Coordination Protocol

Subagents do not share transient chat context. The lead is their coordinator:
give each subagent one read-only question, collect its evidence, and record the
decision, file/line references, command result, and next atomic step in
`STATE.md` before assigning the next writable slice. Every subagent must read
`STATE.md`, this runbook, and the active phase plan before it starts; every
later subagent therefore sees the earlier findings through the repository, not
through an assumed live conversation. Only the lead edits production code.

1. **Kernel mapper:** identify the exact read-only Hermes board APIs for graph
   creation, parent links, worktree lifecycle, claim, completion, failure, and
   cancellation. Return file/line evidence and no code.
2. **Concurrency designer:** turn `research/concurrency_results.json` into a
   reproducible CPU/IO worker-cap experiment. Define the measurement command,
   success threshold, and deliberate branch-failure case. Return a test matrix
   and no code.
3. **Planner contract reviewer:** define the strict planner output schema and
   how every generated child is routed through `board.create_task` with a
   verify command. Identify injection and missing-oracle failure cases. Return
   findings and no code.
4. **Adversarial reviewer:** attack the planned isolation, stale-claim, and
   ledger assertions. Return only reproducible findings.

One lead agent synthesizes those reports into
`.planning/phases/phase-3-plan.md`. Every criterion needs a named command that
can fail for a named deliberate break before implementation begins.

## Implementation Order

1. Add and verify the two Slice 2 correction tests.
2. Obtain independent review of the correction; do not start Slice 3 until it
   passes.
3. Implement one remaining Phase 4 slice at a time in plan order.
4. Run the full suite, update `STATE.md` and this runbook checkpoint, then
   commit locally and stop for independent review before the next slice.

## Usage-Limit Handoff Prompt

Start a new Claude session from `C:\Users\ardit\tri-ai` with:

> Read `.planning/AUTONOMOUS-RUNBOOK.md`, `.planning/STATE.md`, and
> `.planning/phases/phase-4-plan.md`, `.planning/research/proposed-gaps.md`, and
> `.planning/reviews/phase-audit-2026-09-10.md` in full. You are the Phase 4
> lead. First run the canonical-checkout gate. Slice 1 is accepted; Slice 2's
> code correction in `02b90f8` lacks two adversarial tests. Implement only
> those tests and any minimal correction they expose; do not start Slice 3 or
> merge any feature branch. Work locally;
> never push, merge, deploy, create remotes, or read credentials. Update state
> after every verified slice. If context or usage runs low, write the exact
> completed evidence, current commit, failing command, and next atomic step to
> `.planning/STATE.md`, then stop.

## Checkpoint Format

At every verified slice, append to `STATE.md`:

- current commit and branch;
- files changed;
- exact test command, count, exit code, and duration;
- any failed command and retained evidence path;
- next single atomic step.

Do not mark a phase complete until every roadmap success criterion has its
proving artifact and the full suite exits 0.

---

## Operating Tri-AI on the desktop (added 2026-09-14)

**Where everything runs.** `DESKTOP-JHQ7HJM`, account `Ardit II` (the space
defeats naive quoting — see below), reachable over Tailscale at `100.67.149.86`
with `~/.ssh/triai_desktop`. The laptop is where code is written. Nothing is
true until it is on the desktop and committed there.

| service | port | scheduled task |
|---|---|---|
| OmniRoute router | 127.0.0.1:20129 | `OmniRoute Router` |
| worker + telegram daemons | — | `Tri-AI Daemons` |
| read-only dashboard | 8081, loopback + Tailscale | `Tri-AI Dashboard` |

All three carry AtStartup + AtLogOn + a 15-minute repetition, S4U, with
`MultipleInstances = IgnoreNew`. Ports 20128 and 8080 are both held by
`svchost.exe` on this machine; that is why the router is on 20129 and the
dashboard on 8081.

**Restarting the daemons after a code change.** The running processes hold the
old modules in memory, so a deploy changes nothing until they reload:

1. `stop_path` from `~/.tri-ai/logs/daemons.json`, `New-Item` it — the sentinel,
   never a kill, so no worker is cut off mid-tick.
2. Wait for all three PIDs to disappear.
3. `Start-ScheduledTask -TaskName 'Tri-AI Daemons'`.
4. **Compare the PIDs before and after.** A previous session read back the same
   PIDs and reported the restart as done; the old handler was still running and
   ate the user's first message.

**Running a shell on the desktop.** The account is `Ardit II`. The space breaks
bash → ssh → cmd → powershell quoting in every naive form, and `scp` cannot take
it as a *destination*. Two patterns that work:

- Script: UTF-16 + base64 through `powershell -EncodedCommand`
  (`scratchpad/rps.py`). Command lines cap near 32 KB, and the encoding triples
  the payload, so anything over ~8 KB must go by file.
- Files: `scp` to `C:/Windows/Temp/` (no space), then `Move-Item` into place
  from PowerShell. **Hash both ends** — an earlier chunked-base64 transfer
  silently kept only the final chunk and every file arrived truncated.

**Calling the router from a script.** POST to
`http://127.0.0.1:20129/v1/chat/completions` with curl. `omniroute chat --file`
prints its banner and exits silently on a ~24 KB prompt. Build the JSON body in
Python: PowerShell's `ConvertTo-Json` turned a 24 KB string into a 461 KB body
whose `content` read back empty. `omniroute simulate -m <model> --explain` is
how to find real routing ids — the display names in `omniroute models` are not
routable.

**Never run `verify.py` by hand.** It archives and commits a run's output. It
now refuses unless `TRIAI_RUN_ID` is set and exits 2 if it is not.
