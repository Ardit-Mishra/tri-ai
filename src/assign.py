"""Tri-AI assignment layer — three triggers, one write path (TRIG-01/02/03).

Every task that reaches the board goes through exactly one function,
``assign()``, which writes via ``board.create_task``. The claim, execution and
ledger path downstream is therefore identical by construction, not by
convention — that identity is criterion 4 and it is a structural property.

THE THREE TRIGGERS
------------------
* TRIG-01  laptop CLI:
      python src\\assign.py --repo <path> --prompt <text> --verify <cmd>
                            --verify-timeout <seconds> [--title <name>]
                            [--expected-artifacts <path>]... [--board <path>]
* TRIG-02  queue file (legacy run_queue format, incl. ``//`` comments):
      python src\\assign.py --from-queue <queue.jsonl> [--board <path>]
      Each line is JSON; ``id`` becomes the board's idempotency key (re-running
      a nightly file cannot pile up duplicates), ``title``/``repo``/``prompt``/
      ``verify``/``verify_timeout`` map to the row, ``expected_artifacts`` is
      stored when present, and ``timeout`` maps to the kernel's
      ``max_runtime_seconds``. ``branch`` is accepted and logged but NOT passed
      to create_task: the kernel's ``branch_name`` is worktree-only and dir
      workspaces have no branch column this wave.
* TRIG-03  schedule — scripts/register-triai-task.ps1 registers a Windows
      Scheduled Task whose first action is ``assign --from-queue`` and whose
      second is ``worker --max-tasks N``. Assignment on a schedule is the
      requirement; a worker only claims work that already exists.

WHAT THIS MODULE DOES NOT CLAIM
-------------------------------
It does not run anything and it creates nothing itself — ``board.create_task``
is the only writer and its single-transaction guarantee is what keeps a row
from ever being visible as ``ready`` without its verify columns. ``assign()``
adds one guard the board does not have: a task without a ``verify_timeout`` is
REJECTED here at write time (``board.create_task`` accepts ``None``), and the
worker re-checks before execution, so the plan's "enforced twice" rule holds for
rows written through either surface.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

# Imported for the kernel, exactly like the board's own users. This module
# spawns no processes; assign() is pure board-write.
import board  # noqa: E402

# Every queue entry MUST carry these. verify_timeout is the plan's second
# enforcement point — a line without one is rejected loudly, never defaulted.
_REQUIRED_QUEUE_FIELDS = ("id", "title", "repo", "prompt", "verify")


class AssignError(ValueError):
    """A task was refused at assignment time. Nothing was written."""


def load_queue(path: str | os.PathLike) -> list[tuple[int, dict]]:
    """Parse a legacy run_queue.jsonl file: one JSON object per line.

    ``//`` comment lines are skipped (run_queue.py:88's convention, lifted
    verbatim), and every other line must parse. Returns ``(line_number,
    entry)`` pairs so a rejection can name the offending line — a malformed or
    incomplete queue must fail loudly, never silently.
    """
    text = Path(path).read_text(encoding="utf-8")
    entries: list[tuple[int, dict]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            entries.append((line_no, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise AssignError(
                f"{path}:{line_no}: malformed JSON line — {exc}"
            ) from None
    return entries


def assign(
    conn,
    *,
    title: str,
    prompt: str,
    verify_command: str,
    repo: Optional[str | Path] = None,
    verify_timeout: Optional[int] = None,
    expected_artifacts: Iterable[str] = (),
    parents: Iterable[str] = (),
    **kernel_kwargs,
) -> str:
    """The one write path. Creates one verify-gated board row; returns its id.

    Required: ``title``, ``prompt``, ``verify_command``, ``verify_timeout``.
    ``board.create_task`` already refuses a missing/blank ``verify_command`` at
    write time; this function adds the rule the board does not enforce: a task
    without a ``verify_timeout`` is rejected here, loudly, and ``verify_timeout``
    is coerced to a positive int so a non-numeric or non-positive value fails
    assignment rather than reaching the worker. ``repo`` is recorded through the
    kernel's own dir-workspace fields. Extra ``kernel_kwargs`` pass straight to
    the kernel (used by the queue trigger for idempotency_key and
    max_runtime_seconds).

    Raises ``ValueError`` (none of it writes a row) when ``verify_timeout`` is
    missing, non-integer, or non-positive, or when ``board.create_task`` rejects
    the task (e.g. no verify command).
    """
    if verify_timeout is None:
        raise AssignError(
            f"verify_timeout is required (task {title!r}). A task without one "
            "is claimable in the window before the worker re-checks — nothing "
            "with an unmeasured gate belongs on the board. Rejecting here at "
            "write time is the plan's first enforcement."
        )
    try:
        timeout = int(verify_timeout)
    except (TypeError, ValueError):
        raise AssignError(
            f"verify_timeout must be an integer number of seconds (task "
            f"{title!r}), got {verify_timeout!r}"
        ) from None
    if timeout <= 0:
        raise AssignError(
            f"verify_timeout must be a positive number of seconds (task "
            f"{title!r}), got {timeout}"
        )

    return board.create_task(
        conn,
        title=title,
        prompt=prompt,
        verify_command=verify_command,
        repo=repo,
        verify_timeout=timeout,
        expected_artifacts=list(expected_artifacts),
        parents=list(parents),
        **kernel_kwargs,
    )


def assign_queue(conn, queue_path: str | os.PathLike) -> list[tuple[str, str]]:
    """TRIG-02: every line of a legacy queue file becomes one board row.

    Returns ``[(queue_id, task_id), ...]`` in file order, one per line. Each
    line is validated against the required fields and the fields map as:

        id       -> idempotency_key   (running a nightly file twice returns the
                                       same task, never a duplicate pile)
        title    -> title
        repo     -> workspace         (dir workspace)
        prompt   -> prompt
        verify   -> verify_command
        verify_timeout -> verify_timeout   (REQUIRED — a line lacking it fails)
        expected_artifacts -> expected_artifacts   (stored when present)
        timeout  -> max_runtime_seconds            (agent run cap, when present)
        branch   -> accepted, logged, NOT stored   (worktree-only this wave)

    A line missing ``verify_timeout`` (or any required field) raises
    ``AssignError`` naming the file and line, and no row is written for it — a
    silently-defaulted timeout would be the exact assertion-without-oracle this
    project rejects.
    """
    created: list[tuple[str, str]] = []
    for line_no, entry in load_queue(queue_path):
        missing = [
            field for field in _REQUIRED_QUEUE_FIELDS
            if not str(entry.get(field) or "").strip()
        ]
        if "verify_timeout" not in entry or entry["verify_timeout"] is None:
            missing.append("verify_timeout")
        if missing:
            raise AssignError(
                f"{queue_path}:{line_no}: missing required field(s): "
                + ", ".join(sorted(set(missing)))
                + " — nothing was written for this line."
            )

        branch = entry.get("branch")
        if branch:
            print(f"  {queue_path}:{line_no}: branch {branch!r} accepted and "
                  "ignored — the kernel's branch_name is worktree-only; dir "
                  "workspaces have no branch column this wave")

        task_id = assign(
            conn,
            title=entry["title"],
            prompt=entry["prompt"],
            verify_command=entry["verify"],
            repo=entry["repo"],
            verify_timeout=entry["verify_timeout"],
            expected_artifacts=entry.get("expected_artifacts") or (),
            idempotency_key=entry["id"],
            max_runtime_seconds=entry.get("timeout"),
        )
        created.append((entry["id"], task_id))
    return created


def _banner() -> str:
    return (
        "Assign tasks to the verify-gated board. Exactly one of --from-queue "
        "or --repo (with --prompt/--verify/--verify-timeout) is required."
    )


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Exit 0 on success; errors exit 1 (argparse misuse: 2)."""
    ap = argparse.ArgumentParser(prog="assign.py", description=_banner())
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from-queue", metavar="QUEUE.JSONL",
                   help="TRIG-02: read tasks from a legacy run_queue-format "
                        "JSONL file (// comments skipped)")
    g.add_argument("--repo", metavar="PATH",
                   help="TRIG-01: workspace the task runs in (dir workspace)")
    ap.add_argument("--prompt", metavar="TEXT",
                    help="the task given to the agent (required with --repo)")
    ap.add_argument("--verify", dest="verify_command", metavar="CMD",
                    help="verify command run with cwd=repo; its exit code "
                         "decides pass/fail (required with --repo)")
    ap.add_argument("--verify-timeout", dest="verify_timeout", type=int,
                    metavar="SECONDS",
                    help="verify timeout in seconds; REQUIRED — an unmeasured "
                         "gate does not belong on the board")
    ap.add_argument("--title", metavar="NAME",
                    help="task title (default: derived from repo + verify)")
    ap.add_argument("--expected-artifacts", action="append", default=[],
                    metavar="PATH",
                    help="repeatable: a path a parent task must produce")
    ap.add_argument("--board", metavar="PATH",
                    help="board DB file (default: the board's TRIAI_BOARD_DB "
                         "env var, else ~/.tri-ai/board.db)")
    args = ap.parse_args(argv)

    if args.from_queue:
        if any((args.prompt, args.verify_command, args.verify_timeout is not None,
                args.expected_artifacts)):
            ap.error("--from-queue takes no --prompt/--verify/--verify-timeout/"
                     "--expected-artifacts; those come from the queue lines")
    else:
        missing = []
        if not args.prompt:
            missing.append("--prompt")
        if not args.verify_command:
            missing.append("--verify")
        if args.verify_timeout is None:
            missing.append("--verify-timeout")
        if missing:
            ap.error("with --repo, required: " + ", ".join(missing))

    # --board on every trigger so tests and operators get an isolated board;
    # when absent, the board's own TRIAI_BOARD_DB env var (or default) applies.
    if args.board:
        os.environ["TRIAI_BOARD_DB"] = str(Path(args.board).expanduser())
    conn = None
    try:
        conn = board.connect(Path(args.board).expanduser() if args.board else None)
        if args.from_queue:
            for queue_id, task_id in assign_queue(conn, args.from_queue):
                print(f"{queue_id} -> {task_id}")
        else:
            title = args.title or f"{Path(args.repo).name}: {args.verify_command}"
            task_id = assign(
                conn,
                title=title,
                prompt=args.prompt,
                verify_command=args.verify_command,
                repo=args.repo,
                verify_timeout=args.verify_timeout,
                expected_artifacts=args.expected_artifacts,
            )
            print(task_id)
    except ValueError as exc:
        print(f"assign: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:  # e.g. Hermes kernel not found
        print(f"assign: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())