# Phase 5 Proposal Review Plan

**Status:** implemented and verified locally (2026-09-11).

The board persists two fixed proposal kinds: terminal task outcomes and
candidate procedural advice. Task proposals can only archive an already-done
task or request the existing retry primitive. Candidate proposals can only
activate a previously bounded procedural rule after its citations are
revalidated. Neither path can merge, push, deploy, invoke a process, change a
verify command, change a task schema, inject a worker prompt, or change routing.

The Telegram daemon is an HTTPS/auth transport. It receives only authorized
messages and fixed `prop:approve:<id>` / `prop:reject:<id>` callback tokens;
the local control adapter opens the board and calls its compare-and-swap
decision primitive. The daemon sends a card once per authorized chat, records
the message receipt, and edits a terminal decision to remove its buttons.

Exit gate: proposal, Telegram, and worker focused tests, followed by
`python tests/run.py`, must exit 0 before the local commit.

## Evidence

- `python -m unittest tests.test_proposal_review tests.test_telegram_control
  tests.test_telegram_daemon tests.test_worker_verify_gate` — 36 tests, exit
  0, 22.178s.
- `python tests/run.py` — 224 tests, exit 0, 177.333s.
- The callback, duplicate decision, citation-drift, notification-receipt, and
  adapter boundary tests all run against temporary board files and fake HTTPS
  transport only. No credential or live Telegram request was used.
