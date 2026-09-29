# Read this before touching Tri-AI

`C:\Users\ardit\AGENTS.md` still applies in full. This file is the current
state of play, and it is tracked on `trunk`, so it travels with the code.

## There is one line of development: `trunk`

Both machines are on it. Check before you start:

```
git rev-parse --abbrev-ref HEAD    # expect: trunk
```

It was three lines until 2026-09-29, and reconciling them is done:

* The laptop and desktop each had a `codex/freellm-router`. They shared 273
  commits, differed by six, and touched **no file in common**, so they were
  merged (`b8089c8`).
* `private/cortex` (once misleadingly named `public-main`) had an
  **unrelated history** - the scrubbed public release began a fresh root -
  so it could never be merged. It was instead found to be a strict superset:
  every source and test file the engine had, plus 31 more, and more correct
  in at least one place. It became `trunk`.

`codex/freellm-router` and the `desktop/*` branches are kept on the private
remote as history. Do not develop on them.

## Two repositories, and the split is enforced

| | |
|---|---|
| `Ardit-Mishra/tri-ai` | **public** - the sealed demo, and nothing else |
| `Ardit-Mishra/tri-ai-private` | **private** - real history, Cortex indexes, operator notes |

`RELEASE_SCOPE` declares a branch's audience and
`scripts/release_guard.py` enforces it at push time, installed at
`core.hooksPath` outside the repo so it applies on every branch. It fails
closed: an unrecognised scope reads as private, an unrecognised remote as
public, and a machine with no declared private remote can push nothing
private anywhere.

Never push to the public remote without checking what is on the branch.

## Things that are true and easy to get wrong

1. **A long-running daemon does not reload Python.** Patching a module
   changes nothing until the process restarts. A whole evening's fixes
   appeared not to work because of this; the agent logs still quoted the
   old prompt. After changing `src/`, restart the daemons:
   `Start-ScheduledTask -TaskName 'Tri-AI Daemons'` (stop the python
   processes first - the task will not replace a running instance).
2. **The desktop account is `Ardit II`, with a space.** Any path handed to
   a shell must go through `executor.shell_path()` and sit inside quotes.
   An unquoted `cd C:\Users\Ardit II\... &&` is split by bash into "too
   many arguments", every command fails, and the agent stops to ask a
   question nobody can answer.
3. **OmniRoute's port differs per machine** - 20128 on the laptop, 20129 on
   the desktop - and it runs as two processes, only one of which binds.
   Ask the process what it bound; do not assert a number.
4. **`git diff --quiet` cannot see new files.** A verify command built on
   it fails every task whose job is to create something. Use
   `python verify.py`, which attributes deliverables to *this run*.
5. **A cancelled parent strands its children forever.** `standstill.py`
   reports it. The readiness rule belongs to the Hermes kanban kernel,
   which this project uses and does not edit.

## Standing rules that decide arguments

- **Run it before building for it.** Ardit's directive; it cancelled four
  queued work items. Prefer evidence over anticipation.
- Acceptance is a verifier's exit code. An agent's report is never
  evidence.
- Mutation-test every guard: break the production line, confirm a test
  fails. Five modules here were complete, tested, and called by nothing.
- Nothing reaches the public remote without Ardit's explicit approval.
- Every task must be able to go from ideation to deployment. That is the
  bar for all work, not an aspiration.

## Open, and needing Ardit rather than an agent

- **FreeLLMAPI has no API key.** Create one in its UI at
  `http://127.0.0.1:3001` and set `FREELLMAPI_API_KEY`. Do not put it in
  this repository, a task prompt, or a chat message.
- **Two tasks wait on a cancelled parent** and can never run. Cancelling
  them or recreating the parent is a product decision.
- **Phone** is declared as a Cortex source with no collector, because
  nothing runs on the phone yet.
