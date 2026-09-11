# Phase 5A-C Plan - Confirmed Telegram Control, Worker Daemon, Routing Probe

**Status:** 5A-5C implemented and locally verified (2026-09-11).

This plan intentionally separates three local slices. Each one has an
exit-code-backed test gate and a local commit. The Telegram transport remains a
network/auth adapter only; it never receives a board connection or a process
handle.

## 5A - Confirmed Intake and Control

### Contract

- A local JSON policy maps a workspace alias to a canonical, preconfigured
  workspace and named verifier profile. A profile owns a fixed verify command
  and positive timeout. Telegram text never supplies a shell command.
- /run <workspace-alias> <prompt> creates a durable pending intake record, not
  a task. It returns a request id.
- /confirm <request-id> creates one normal verify-gated task through
  board.create_task; a second confirmation is a no-op reporting the same task
  id. A pending request expires without creating a task.
- /retry <task-id> creates a pending retry confirmation. Confirmation only
  requeues eligible failed/blocked tasks, preserves every task specification,
  refuses a quarantined workspace, and lets a worker's ordinary claim create
  the new task run.
- /cancel <task-id> is immediate, but only through a board-owned
  compare-and-swap primitive. It first terminates the recorded worker PID with
  Windows-correct liveness semantics, then changes the exact still-claimed run
  to terminal cancelled. If the run completed first, cancellation reports that
  fact and changes nothing.

### Proofs

1. Unconfirmed or expired intake creates zero task rows.
2. Alias/path escape, unknown profile, and blank/unsafe policy values are
   refused before a pending record is written.
3. Confirmation creates exactly one task with the policy-owned command and
   timeout; duplicate confirmation does not alter it.
4. Retry preserves the original verify fields and reaches a new task run only
   through worker.run_once.
5. Cancellation races a real claimed worker; it never overwrites a completed
   task and leaves no running claim or open task run.
6. AST tests prove the daemon still imports neither board nor subprocess, and
   the control surface has no arbitrary shell or raw tasks SQL path.

## 5B - Continuous Local Worker Daemon

The daemon calls worker.run_once, so every tick retains stale-claim release,
precheck, verify, ledger, quarantine, and circuit-breaker behavior. Empty
queues use capped idle sleep only; they do not create task retries. Unexpected
exceptions stop the daemon and return a non-zero exit. SIGINT/SIGTERM-equivalent
stop requests finish the current boundary and return cleanly.

Proofs: deterministic fake clock/sleeper proves capped empty-queue backoff,
one non-empty tick resets the delay, graceful stop does not claim a new task,
and stale-claim release remains part of each tick.

## 5C - OmniRoute Routing Probe

The initial slice is an evidence-producing local probe, not a claim that
Hermes was silently reconfigured. It uses an injected HTTP transport against a
configured local OpenAI-compatible endpoint and a declared desktop-local
fallback endpoint. A 429, connection failure, or timeout creates one route
attempt record with the classified reason and one fallback attempt. Neither
route outcome can complete a board task; the normal worker verifier remains
the sole acceptance authority.

Actual executor adoption is permitted only after the probe proves the Hermes
launcher's documented endpoint configuration can be applied without exposing
credentials to the worker or bypassing the existing contained agent gateway.
If that contract cannot be demonstrated from local Hermes source/tests, the
slice stops at diagnostic evidence rather than guessing an environment
variable.

**Implementation record:** Hermes source proves that one-shot accepts
`--model`/`--provider` (and corresponding launch-scoped model/provider
environment overrides), while named endpoint resolution comes from Hermes
`config.yaml`; its general `OPENAI_BASE_URL` override is explicitly retired.
The worker therefore remains unchanged. `routing_probe.py` measures only a
policy-owned, loopback OpenAI-compatible primary/fallback pair through an
injected transport and writes a separate diagnostic JSONL record. It never
reads Hermes configuration, credentials, or board state, and it cannot accept
a task. A later executor-adoption plan must bind a credential-free,
operator-configured Hermes profile to a measured admission gate before altering
`executor.run_agent`.

Proofs: unknown route is refused before HTTP; 429, timeout, and connection
error select fallback exactly once; fallback result and policy version are
recorded; and an adversarial route cannot turn a successful HTTP response into
a verified board completion.

## Boundaries

- No push, merge, deploy, remote creation, or credential inspection.
- Hermes remains read-only.
- The existing read adapter remains read-only.
- The existing worker/executor safety closure must be extended for every new
  execution-path module before it is used.
- python tests/run.py is required before each local commit. Failed command
  outputs and existing user files remain evidence.
