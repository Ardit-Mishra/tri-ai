# Phase 4.5 Plan - Telegram Long-Poll Transport

**Status:** authorized implementation in progress. This is a narrowly scoped
extension of Phase 4's accepted local read-only adapter, not a Phase 5 control
surface.

## Goal

Make /status, /task <id>, and /logs <id> reachable from the operator's phone
through Telegram long polling over HTTPS, without opening an inbound port,
adding a webhook, or adding any board mutation.

## Boundary

- The local read surface stays pure: no token lookup, transport, network,
  subprocess, or board mutation.
- The daemon is the sole external-I/O caller. It reads
  TRI_AI_TELEGRAM_BOT_TOKEN only when the operator starts it, reads an explicit
  one-or-many chat-id allowlist, and passes authorized text to the adapter.
- The daemon owns neither board writes nor process creation. It neither pushes,
  merges, deploys, reads other credentials, nor starts itself.
- An unauthorized chat is silently dropped before adapter dispatch. A
  transport/API/configuration failure hard-stops the daemon; it is not retried
  invisibly.
- /logs retains the adapter's realpath containment rule. Long output is chunked
  for Telegram without truncation.

## Implementation and Proof

1. Extend /task to report the board-recorded workspace and declared expected
   artifacts as target files.
2. Implement a small HTTPS Bot API client for getUpdates and sendMessage,
   using JSON POST requests and an injected transport seam for tests.
3. Implement a long-poll loop with offset advancement, command normalization
   for addressed commands, authorization before dispatch, and
   4,000-character response chunking.
4. Prove with fake transport that missing startup configuration is rejected; an
   authorized update invokes the adapter and returns its response; an
   unauthorized update invokes neither adapter nor reply; addressed commands
   normalize; long logs are fully retained; the API client uses HTTPS JSON POST
   and rejects a Bot API failure; and the daemon has no board/ledger import or
   subprocess spawn.
5. Run python tests/run.py before a local commit, record the exact result in
   STATE.md, and stop. No live bot credential or Telegram request is used
   during implementation or tests.

## Operator Invocation (after review)

    $env:TRI_AI_TELEGRAM_BOT_TOKEN = "<bot token>"
    $env:TRI_AI_TELEGRAM_AUTHORIZED_CHAT_ID = "<numeric chat id>"
    python src/interfaces/telegram_daemon.py --board <board.db> --ledger <ledger.jsonl> --runs-dir <runs>

TRI_AI_TELEGRAM_AUTHORIZED_CHAT_IDS may instead contain a comma-separated
allowlist. The process is intentionally foreground-only; service installation,
auto-start, and Telegram write commands remain out of scope.
