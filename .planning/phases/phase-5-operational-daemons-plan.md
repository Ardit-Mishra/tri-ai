# Phase 5 Operational Daemons Plan

**Status:** implemented and verified locally (2026-09-11).

`scripts/run_daemons.ps1` is the operator entrypoint. It starts the foreground
`src/daemon_supervisor.py`, which launches exactly the local worker and
Telegram daemons with fixed argv. Its defaults use the canonical local runtime
paths `~/.tri-ai/board.db`, `~/.tri-ai/ledger.jsonl`, and `~/.tri-ai/runs`.
Before creating state or spawning either child, the supervisor checks only for
the presence of `TRI_AI_TELEGRAM_BOT_TOKEN` and either
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID` or
`TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS`; it never writes their values to command
lines, logs, or state. The supervisor and Telegram child use the same resolver,
so preflight cannot accept a source the child will reject.

## Telegram Configuration

Settings resolve in this order: the current process environment,
`~/.tri-ai/config.json`, then the Windows User environment registry. This
means a one-off shell setting wins, but a new PowerShell session can start the
daemons without manually re-exporting the bot settings. Only the `TRI_AI_*`
names above are supported; unprefixed `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_AUTHORIZED_CHAT_ID` are not aliases.

The optional user-profile configuration file has this shape:

```json
{
  "telegram": {
    "bot_token": "<bot token>",
    "authorized_chat_id": "<numeric chat id>"
  }
}
```

`authorized_chat_ids` may instead be a comma-separated string or a JSON array.
The file is deliberately outside the repository and its values never enter git,
command lines, daemon state, or logs. It is plaintext local configuration, not
a vault; keep it within the Windows user profile and do not copy it into the
workspace.

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

`-WhatIf` prints the fixed supervisor invocation without spawning a process and
does not require Telegram configuration. A normal start with missing Telegram
settings exits with a console message naming the required environment variables
before it creates `daemons.json` or child logs.

The daemon writes the actual safe failure class and reason to `telegram.log`
(for example a missing configuration field or an HTTPS failure), rather than a
generic configuration/transport message. Unexpected exceptions retain their
normal traceback in the retained log.

## Evidence

- `python -m unittest tests.test_daemon_supervisor` — 8 tests, exit 0,
  0.466s. This includes a no-spawn missing-environment preflight and an actual
  PowerShell `-WhatIf` assertion for canonical runtime defaults.
- `python -m unittest tests.test_telegram_daemon tests.test_daemon_supervisor`
  — 24 tests, exit 0, 0.517s. This proves the process/config/registry source
  precedence, config-backed supervisor preflight, and reason-preserving daemon
  startup diagnostics.
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_daemons.ps1
  -WhatIf` — exit 0; printed the two-daemon supervisor invocation without
  starting it.
- `python tests/run.py` — 238 tests, exit 0, 148.043s.
