# Phase 5 Operational Daemons Plan

**Status:** implemented and verified locally (2026-09-11).

`scripts/run_daemons.ps1` is the operator entrypoint. It starts the foreground
`src/daemon_supervisor.py`, which launches exactly the local worker and
Telegram daemons with fixed argv. It never reads credential values; the
Telegram child consumes its documented environment variables only after the
operator starts the runner.

The supervisor preserves child stdout/stderr in `~/.tri-ai/logs/worker.log`
and `telegram.log`. At five MiB it timestamp-rotates an old log without
deleting retained evidence. `daemons.json` records the supervisor and child
PIDs plus a per-run stop-request path. The stop script creates that request;
the supervisor signals both child process groups, waits fifteen seconds, and
only then terminates a survivor while retaining its logs and stopped state.
If either child exits unexpectedly, its sibling is stopped and the supervisor
returns non-zero. No child is retried.

## Commands

From `C:\Users\ardit\tri-ai`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daemons.ps1
python tests/run.py
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_daemons.ps1
```

Append `-IntakePolicy <path>` to the first command only when the local,
operator-owned intake profile should enable `/run`. Telegram proposal polling,
outbound cards, and callbacks do not require that profile.

## Evidence

- `python -m unittest tests.test_daemon_supervisor` — 6 tests, exit 0,
  0.046s.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daemons.ps1
  -WhatIf` — exit 0; printed the two-daemon supervisor invocation without
  starting it.
- `python tests/run.py` — 230 tests, exit 0, 147.921s.
