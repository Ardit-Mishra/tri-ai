# Phase 7 Plan - Planner Assistance and Trusted Remote Delegation

**Status:** draft - requires independent plan review before implementation
**Depends on:** Phase 6 acceptance (`python tests/run.py`, 324 tests, exit 0)
**Goal:** Let the planner use accepted procedural memory and let a registered
remote executor produce candidate work, while only the desktop board owner can
accept a task after its own verify command exits 0.

## Non-Negotiable Contract

- The canonical checkout is `C:\Users\ardit\tri-ai`; Hermes is read-only.
- The desktop remains the sole board owner. A remote process never opens the
  board database, imports the kernel, or writes ledger completion evidence.
- A remote `passed` report, remote exit code, remote log, and remote diff are
  diagnostic inputs, never acceptance evidence.
- The desktop applies a bounded candidate patch only in the task-owned isolated
  worktree, runs the board-recorded verifier through `executor.run_verify`, and
  accepts only that local exit-0 result. It never auto-commits, merges, pushes,
  deploys, creates remotes, or reads Git/hosting credentials.
- A submitted patch that fails validation, application, verification, cleanup,
  or provenance checks hard-stops that attempt and retains its bundle, logs, and
  worktree as evidence. No fallback workspace, rename, deletion, or retry loop.
- The phone remains a confirmation-gated controller only. It never receives a
  worker credential, claims a task, or executes a task.

## Rejected Design Elements

The 2026-09-10 hardware sketch is not implementation authority where it says a
remote worker can call `complete`, a desktop daemon can automatically commit a
remote diff, or Tailscale source IP alone is sufficient authorization. Those
forms can turn an untrusted report into an accepted task or mutate a repository
without a local oracle.

## Slice 1: Planner Assistance Is Data-Only

### Surfaces

- `src/planner_assist.py`
- `src/planner.py`
- `tests/test_phase7_planner_assist.py`

The planner may select only active, cited procedural rules through the existing
read-only memory selector. It receives a bounded checklist and citation ids; it
does not receive free-form command text, route configuration, board handles, or
execution callbacks. Its graph output remains Phase 3 JSON and every node still
requires its own nonblank, board-owned verify command and positive timeout.

### Proofs

```powershell
python -m unittest tests.test_phase7_planner_assist
python -m unittest tests.test_planner
```

Tests must prove a matching activated rule is represented as bounded planner
advice, stale/scope-mismatched rules disappear, and no advice can supply or
replace a node's verifier. Deliberately inject a command-shaped rule payload and
prove the planner rejects it before any board write. A structural audit must
prove `planner_assist` cannot import worker, executor, dispatcher, routing, or
board mutation surfaces.

## Slice 2: Fenced Remote Lease Protocol

### Surfaces

- `src/remote_protocol.py`
- `src/remote_gateway.py`
- `tests/test_phase7_remote_protocol.py`

The desktop gateway exposes a narrow protocol adapter; it is not a general
remote board API. A registered executor requests a specific ready task. The
desktop uses `board.release_stale_claims`, claims through the existing board
path, and returns an immutable lease containing task id, opaque lease/fence id,
source commit, workspace descriptor, prompt, expected artifacts, and the
board-recorded verifier spec.

Every subsequent heartbeat or result submission must carry that exact lease id.
The gateway refuses a stale, duplicate, wrong-node, expired, or task-mismatched
lease before any board/worktree mutation. Lease renewal extends only the matching
remote lease; it cannot resurrect a reclaimed claim.

Live transport is deferred until an operator provisions a Tailscale ACL limited
to the registered node identities and a separate operator-owned application
credential. Tests inject a registry/authenticator; source IP, hostname, and a
phone identifier are never authorization proofs. The daemon binds only to an
explicit Tailscale address when live transport is later enabled, never `0.0.0.0`.

### Proofs

```powershell
python -m unittest tests.test_phase7_remote_protocol
```

The adversarial suite starts an in-process desktop gateway over loopback only.
It proves a registered simulated remote receives one lease, an unregistered
node is refused, duplicate claim has one winner, stale result cannot complete a
reclaimed task, and a phone-role identity cannot claim. The test must use a
deliberately broken gateway that ignores the fence id and demonstrate that the
stale-result assertion fails.

## Slice 3: Desktop-Owned Candidate Result Gate

### Surfaces

- `src/remote_result_gate.py`
- narrow exact-argv patch gateway in `src/executor.py`
- `tests/test_phase7_remote_result_gate.py`

A remote result is a sealed, size-bounded bundle: declared source commit,
lease id, patch bytes/hash, optional diagnostic logs, and no executable command.
The gateway rejects absolute/traversal paths, malformed/unexpected bundle keys,
oversized payloads, hash mismatch, source-commit mismatch, and a result whose
task/lease/node do not match the durable claim.

Only after those checks may the desktop materialize the task-owned worktree and
apply the patch through a new exact-argv, no-shell executor gateway. It runs
`git apply --check` before `git apply`; both forms are individually allowlisted
and AST-audited. The gateway may not run `commit`, `merge`, `push`, `fetch`,
`pull`, `remote`, `config`, or credential commands.

The desktop then runs the existing contained verifier with the board-recorded
command and timeout. Exit 0 plus artifact checks is the only path to `done`.
The accepted work remains in its task worktree for operator review; `done` means
the candidate passed its oracle, not that it was integrated into a branch.

### Proofs

```powershell
python -m unittest tests.test_phase7_remote_result_gate
python -m unittest tests.test_safety_boundary
```

Tests must prove all of the following on a temporary Git repository:

1. A valid remote patch changes the task worktree, the desktop verifier exits 0,
   and the ledger records `acceptor_node=tri-desktop`, not a remote success claim.
2. A remote report claiming success with a deliberately failing desktop verifier
   leaves the task unaccepted and retains the candidate bundle/log evidence.
3. A stale/replayed lease and a mismatched source commit make zero worktree or
   board writes.
4. A patch escape, invalid patch, and verifier timeout each hard-stop without
   committing, merging, pushing, or falling back to the source checkout.
5. The AST audit fails against deliberately bad `subprocess`/dynamic-dispatch
   source and against an allowlist containing `git commit` or `git push`.

## Slice 4: Local Two-Node Evidence Exercise

No real remote device or credential is required. Start a desktop gateway and a
registered simulated remote in separate processes against temp repositories.
The remote receives a lease and returns a candidate patch; the desktop verifies
it locally and retains the isolated worktree. Then repeat with a nonzero desktop
verifier and prove the remote's reported pass cannot complete the task.

```powershell
python -m unittest tests.test_phase7_remote_protocol tests.test_phase7_remote_result_gate
python tests/run.py
```

The exercise is accepted only if both success and deliberately failed verifier
paths leave durable, queryable board and ledger evidence. It is not a benchmark,
hardware-routing claim, live Tailscale exposure, or automatic offload feature.

## Completion Gate

Before each local commit run the slice command plus `python tests/run.py`. Update
`STATE.md` after every accepted slice with branch, commit, exact exit-code
evidence, retained paths, and the next atomic step. Stop for independent
commit-level review after every slice. Phase 8 begins only after Phase 7's
desktop acceptance and remote-fence proofs are accepted.
