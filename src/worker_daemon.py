"""Foreground local polling daemon around the existing one-tick worker path."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import board
import ledger
import worker


@dataclass(frozen=True)
class DaemonSummary:
    ticks: int
    idle_sleeps: int
    stopped: bool


def startup_diagnostic(exc: Exception) -> str:
    """Preserve the failure class and reason in retained worker output."""
    return f"worker daemon stopped: {type(exc).__name__}: {exc}"


def serve(
    tick: Callable[[], object | None],
    *,
    sleep: Callable[[float], None],
    stop_requested: Callable[[], bool],
    idle_min_seconds: float = 1.0,
    idle_max_seconds: float = 30.0,
    max_ticks: Optional[int] = None,
    on_idle: Optional[Callable[[], object]] = None,
) -> DaemonSummary:
    """Run ticks until stopped; empty queues back off but never retry tasks.

    `on_idle` runs only when a tick found nothing to do, so the learning pass
    never sits between a claim and its agent. It is called for its effect and
    its result is ignored, and an exception from it is swallowed: a worker
    that stops running tasks because reflection failed has traded the thing
    that works for the thing that might.
    """
    if idle_min_seconds <= 0 or idle_max_seconds < idle_min_seconds:
        raise ValueError("idle backoff bounds are invalid")
    delay = float(idle_min_seconds)
    ticks = idle_sleeps = 0
    while not stop_requested():
        if max_ticks is not None and ticks >= max_ticks:
            break
        attempt = tick()
        ticks += 1
        if attempt is None:
            if on_idle is not None:
                try:
                    on_idle()
                except Exception:
                    pass
            if max_ticks is not None and ticks >= max_ticks:
                break
            sleep(delay)
            idle_sleeps += 1
            delay = min(delay * 2, float(idle_max_seconds))
            continue
        delay = float(idle_min_seconds)
        if bool(getattr(attempt, "stop", False)):
            return DaemonSummary(ticks, idle_sleeps, True)
    return DaemonSummary(ticks, idle_sleeps, True)


def _learning_pass(conn, *, ledger_path, memory_path, workspace):
    """The idle-tick hook: index the ledger and draft rules for repeats.

    Built here rather than inside `serve` so the loop stays a pure scheduler
    with no opinion about what idle time is for, and so a test can drive
    `serve` without touching a ledger.

    `ledger_path` falls back to the same default `worker.run_once` writes to.
    An earlier version returned None here, so a daemon started without
    `--ledger` ledgered its runs and never learned from them - the two halves
    disagreed about what "no ledger given" means. The deployed supervisor does
    pass `--ledger`, so this was the default path rather than production, and
    exactly the asymmetry that survives until someone changes the supervisor.
    Found by Codex reviewing this change set.
    """
    source = Path(ledger_path) if ledger_path is not None else Path(ledger.ledger_path())
    memory = memory_path or source.with_name("memory.db")
    target = workspace or source.parent
    ledger_path = source

    def run() -> None:
        # `refresh_from_ledger` already swallows its own failures; this is the
        # second belt, because an import error here would still reach `serve`.
        try:
            import proposals
            proposals.refresh_from_ledger(
                conn, ledger_path=ledger_path, memory_path=memory,
                workspace=target,
            )
        except Exception:
            pass

    return run


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local verify-gated worker continuously.")
    parser.add_argument("--board", default=None)
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--idle-min", type=float, default=1.0)
    parser.add_argument("--idle-max", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="run one worker tick, then exit")
    parser.add_argument("--memory", default=None,
                        help="episodic memory database (default: beside the ledger)")
    parser.add_argument("--no-learning", action="store_true",
                        help="skip the idle pass that drafts candidate rules")
    args = parser.parse_args(argv)

    if args.board:
        import os
        os.environ["TRIAI_BOARD_DB"] = str(Path(args.board).resolve())
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    stop_signals = [signal.SIGINT, signal.SIGTERM]
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        stop_signals.append(sigbreak)
    for sig in stop_signals:
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass

    conn = None
    try:
        conn = board.connect(Path(args.board) if args.board else None)
        serve(
            lambda: worker.run_once(
                conn,
                ledger_path=Path(args.ledger) if args.ledger else None,
                runs_root=Path(args.runs_dir) if args.runs_dir else None,
            ),
            sleep=time.sleep,
            stop_requested=stopping.is_set,
            idle_min_seconds=args.idle_min,
            idle_max_seconds=args.idle_max,
            max_ticks=1 if args.once else None,
            on_idle=_learning_pass(
                conn,
                ledger_path=Path(args.ledger) if args.ledger else None,
                memory_path=Path(args.memory) if args.memory else None,
                workspace=Path(args.runs_dir) if args.runs_dir else None,
            ) if not args.no_learning else None,
        )
    except Exception as exc:
        print(startup_diagnostic(exc), file=sys.stderr)
        return worker.EXIT_ERROR
    finally:
        if conn is not None:
            conn.close()
    return worker.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
