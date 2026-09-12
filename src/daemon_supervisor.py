"""Foreground supervisor for the local worker and Telegram daemons.

This is operational tooling, not part of the worker execution path. It starts
only fixed Tri-AI daemon argv, writes their combined output to retained local
logs, and uses a stop-request file so a second terminal can ask for a clean
shutdown without killing a worker mid-tick.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import process_liveness
from interfaces import telegram_daemon

LOG_LIMIT_BYTES = 5 * 1024 * 1024
SHUTDOWN_SECONDS = 15.0
DEFAULT_INTAKE_POLICY_PATH = Path.home() / ".tri-ai" / "intake_policy.json"


@dataclass(frozen=True)
class DaemonCommands:
    worker: tuple[str, ...]
    telegram: tuple[str, ...]


def commands(
    *,
    root: Path,
    board_path: Path,
    ledger_path: Path,
    runs_root: Path,
    intake_policy: Optional[Path],
) -> DaemonCommands:
    """Build the only two child argv forms the supervisor may execute."""
    worker = (
        sys.executable, str(root / "src" / "worker_daemon.py"),
        "--board", str(board_path), "--ledger", str(ledger_path), "--runs-dir", str(runs_root),
    )
    telegram = [
        sys.executable, str(root / "src" / "interfaces" / "telegram_daemon.py"),
        "--board", str(board_path), "--ledger", str(ledger_path), "--runs-dir", str(runs_root),
    ]
    if intake_policy is not None:
        telegram.extend(("--intake-policy", str(intake_policy)))
    return DaemonCommands(worker, tuple(telegram))


def resolve_intake_policy(
    requested: Optional[Path], *, default_path: Path = DEFAULT_INTAKE_POLICY_PATH,
) -> Optional[Path]:
    """Use the operator-owned runtime policy only when it exists.

    An explicit path remains authoritative so a typo fails in the Telegram
    daemon rather than being silently replaced by the default policy.
    """
    if requested is not None:
        return requested
    return default_path if default_path.is_file() else None


def rotate_log(path: Path, *, limit_bytes: int = LOG_LIMIT_BYTES) -> None:
    """Preserve a large prior log under a timestamped name; never delete it."""
    if limit_bytes < 1:
        raise ValueError("log limit must be positive")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size < limit_bytes:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(path.stat().st_mtime))
    archived = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    if archived.exists():
        raise RuntimeError(f"refusing to overwrite retained log {archived}")
    path.replace(archived)


def _state_path(log_dir: Path) -> Path:
    return log_dir / "daemons.json"


def _write_state(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def validate_telegram_environment(
    env: Mapping[str, str],
    *,
    config_path: Optional[Path] = None,
    user_environment: Optional[Mapping[str, str]] = None,
) -> None:
    """Resolve the daemon's approved config sources before child launch.

    Resolution is shared with the daemon so the supervisor cannot accept a
    source that the child will reject. The resolved settings never reach argv,
    logs, or the retained supervisor state.
    """
    try:
        telegram_daemon.settings_from_sources(
            env, config_path=config_path, user_environment=user_environment,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def _is_alive(pid: object) -> bool:
    """Liveness for the "is another supervisor already running" guard.

    This used to call ``os.kill(pid, 0)`` directly, which is wrong on Windows
    in two separate ways that were both reproduced locally: an exited process
    whose handle is still open raises nothing and read as **alive**, and a PID
    that never existed raises a bare ``OSError`` (``WinError 87``) that this
    function did not catch, so it escaped ``main``'s startup check as a
    traceback rather than a diagnostic. Both are gone now that the shared
    ``GetExitCodeProcess``/``STILL_ACTIVE`` probe owns the question.
    """
    return process_liveness.pid_alive(pid)


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def _request_stop(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGTERM)


def _stop_children(children: Sequence[subprocess.Popen[object]], *, deadline_seconds: float = SHUTDOWN_SECONDS) -> bool:
    """Ask both daemons to stop, then force only surviving processes after evidence time."""
    for child in children:
        _request_stop(child)
    deadline = time.monotonic() + deadline_seconds
    while any(child.poll() is None for child in children) and time.monotonic() < deadline:
        time.sleep(0.1)
    survivors = [child for child in children if child.poll() is None]
    for child in survivors:
        child.terminate()
    for child in survivors:
        child.wait(timeout=5)
    return not survivors


@dataclass(frozen=True)
class RestartPolicy:
    """How hard the supervisor tries to keep one child daemon alive.

    The fleet died on 2026-09-12 because this supervisor returned on the first
    child exit: the Telegram daemon lost an HTTPS request, exited 1, and the
    worker was stopped underneath a run it had already claimed. Restarting is
    the fix, but restarting *without a budget* would be worse than stopping —
    a daemon that fails on a bad config fails identically every time, and an
    unbounded loop would spin, rewrite logs, and hide the cause.

    So: back off exponentially, cap the delay, cap the number of restarts in a
    rolling window, and treat a child that stays up past ``healthy_seconds`` as
    a fresh start whose backoff is reset.
    """

    base_seconds: float = 2.0
    max_seconds: float = 60.0
    max_restarts: int = 8
    window_seconds: float = 3600.0
    healthy_seconds: float = 120.0

    def delay_for(self, consecutive_failures: int) -> float:
        """Delay before restart number ``consecutive_failures`` (1-based)."""
        if consecutive_failures < 1:
            return 0.0
        return min(self.base_seconds * (2 ** (consecutive_failures - 1)), self.max_seconds)


@dataclass
class _Child:
    """One supervised daemon plus the evidence needed to decide a restart."""

    name: str
    command: tuple[str, ...]
    handle: Any
    process: Optional[subprocess.Popen[Any]] = None
    started_at: float = 0.0
    consecutive_failures: int = 0
    restart_times: list[float] = field(default_factory=list)
    next_start_at: float = 0.0
    total_restarts: int = 0
    last_exit_code: Optional[int] = None


def _note(handle: Any, message: str) -> None:
    """Write one supervisor line into the child's own retained log.

    The restart decision belongs beside the output that caused it; a reader
    tailing telegram.log should not have to correlate a second file to learn
    that the daemon was restarted, or why the supervisor gave up on it.
    """
    try:
        handle.write(f"supervisor: {message}\n")
        handle.flush()
    except (ValueError, OSError):
        pass


def supervise(
    daemon_commands: DaemonCommands,
    *,
    log_dir: Path,
    run_id: str,
    stop_path: Path,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    on_started: Optional[Callable[[Sequence[subprocess.Popen[Any]]], None]] = None,
    on_change: Optional[Callable[[Mapping[str, Optional[int]]], None]] = None,
    policy: RestartPolicy = RestartPolicy(),
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Keep both daemons alive until a stop request, or until one is unfixable.

    Returns 0 for a clean operator-requested shutdown and 1 when a child
    exhausted its restart budget, when a survivor had to be forced, or when a
    restart could not be spawned at all.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    worker_log, telegram_log = log_dir / "worker.log", log_dir / "telegram.log"
    rotate_log(worker_log)
    rotate_log(telegram_log)
    worker_handle = worker_log.open("a", encoding="utf-8")
    telegram_handle = telegram_log.open("a", encoding="utf-8")
    common = {"stdin": subprocess.DEVNULL, "creationflags": _creation_flags()}
    supervised = (
        _Child("worker", daemon_commands.worker, worker_handle),
        _Child("telegram", daemon_commands.telegram, telegram_handle),
    )

    def live() -> tuple[subprocess.Popen[Any], ...]:
        return tuple(c.process for c in supervised if c.process is not None)

    def pids() -> dict[str, Optional[int]]:
        """Current child PIDs by name; None for a child inside its backoff.

        Reported by name rather than by position because a restarting child is
        absent from the live sequence, and a positional reader would silently
        attribute the surviving daemon's PID to the dead one.
        """
        return {c.name: (c.process.pid if c.process is not None else None) for c in supervised}

    def start(child: _Child) -> None:
        child.process = popen(
            child.command, stdout=child.handle, stderr=subprocess.STDOUT, **common,
        )
        child.started_at = monotonic()

    try:
        for child in supervised:
            start(child)
        if on_started is not None:
            on_started(live())
        while True:
            if stop_path.exists():
                return 0 if _stop_children(live()) else 1

            changed = False
            for child in supervised:
                process = child.process
                if process is None:
                    # Awaiting its backoff window; respawn once it elapses.
                    if monotonic() >= child.next_start_at:
                        try:
                            start(child)
                        except OSError as exc:
                            _note(child.handle, f"{child.name} could not be restarted: {exc}")
                            _stop_children(live())
                            return 1
                        _note(child.handle, f"{child.name} restarted (attempt {child.consecutive_failures})")
                        changed = True
                    continue

                status = process.poll()
                if status is None:
                    if (
                        child.consecutive_failures
                        and monotonic() - child.started_at >= policy.healthy_seconds
                    ):
                        _note(
                            child.handle,
                            f"{child.name} healthy for {policy.healthy_seconds:.0f}s; backoff reset",
                        )
                        child.consecutive_failures = 0
                    continue

                # The child exited on its own. Decide restart from its record.
                child.last_exit_code = status
                now = monotonic()
                child.restart_times = [
                    t for t in child.restart_times if now - t <= policy.window_seconds
                ]
                if len(child.restart_times) >= policy.max_restarts:
                    _note(
                        child.handle,
                        f"{child.name} exited {status}; {len(child.restart_times)} restarts in the last "
                        f"{policy.window_seconds:.0f}s exhausts the budget - supervisor is stopping the fleet",
                    )
                    child.process = None
                    _stop_children(live())
                    return 1
                child.consecutive_failures += 1
                child.total_restarts += 1
                child.restart_times.append(now)
                delay = policy.delay_for(child.consecutive_failures)
                child.next_start_at = now + delay
                child.process = None
                _note(
                    child.handle,
                    f"{child.name} exited {status}; restarting in {delay:.0f}s "
                    f"(attempt {child.consecutive_failures} of {policy.max_restarts})",
                )
                changed = True

            if changed and on_change is not None:
                on_change(pids())
            time.sleep(0.25)
    finally:
        remaining = [c.process for c in supervised if c.process is not None and c.process.poll() is None]
        if remaining:
            _stop_children(remaining)
        worker_handle.close()
        telegram_handle.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run Tri-AI worker and Telegram daemons locally.")
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--log-dir", type=Path, default=Path.home() / ".tri-ai" / "logs")
    parser.add_argument("--intake-policy", type=Path)
    args = parser.parse_args(argv)
    intake_policy = resolve_intake_policy(args.intake_policy)

    try:
        validate_telegram_environment(os.environ)
    except RuntimeError as exc:
        print(f"daemon supervisor configuration error: {exc}", file=sys.stderr)
        return 2

    log_dir = args.log_dir.resolve()
    state_path = _state_path(log_dir)
    if state_path.exists():
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"state file is not valid JSON: {state_path}") from exc
        if prior.get("status") == "running" and _is_alive(prior.get("supervisor_pid")):
            raise RuntimeError(f"another Tri-AI daemon supervisor is still running: {prior.get('supervisor_pid')}")

    run_id = f"run-{os.getpid()}-{int(time.time())}"
    stop_path = log_dir / f"stop-{run_id}.request"
    state = {
        "run_id": run_id,
        "status": "running",
        "supervisor_pid": os.getpid(),
        "worker_pid": None,
        "telegram_pid": None,
        "stop_path": str(stop_path),
        "started_at": int(time.time()),
        "restarts": 0,
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    _write_state(state_path, state)
    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True
        stop_path.touch(exist_ok=True)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, request_stop)
        except (ValueError, OSError):
            pass
    try:
        def children_started(children: Sequence[subprocess.Popen[Any]]) -> None:
            state.update(worker_pid=children[0].pid, telegram_pid=children[1].pid)
            _write_state(state_path, state)

        def children_changed(current: Mapping[str, Optional[int]]) -> None:
            """Republish PIDs after a restart so readers never show a dead one.

            The dashboard reads these PIDs and probes them for liveness. A
            child inside its backoff window has no PID at all, and recording
            None is the honest answer - a stale PID would render as a daemon
            that is up.
            """
            state.update(
                worker_pid=current.get("worker"),
                telegram_pid=current.get("telegram"),
                restarts=int(state.get("restarts", 0) or 0) + 1,
                last_restart_at=int(time.time()),
            )
            _write_state(state_path, state)

        result = supervise(
            commands(
                root=root, board_path=args.board.resolve(), ledger_path=args.ledger.resolve(),
                runs_root=args.runs_dir.resolve(),
                intake_policy=intake_policy.resolve() if intake_policy else None,
            ),
            log_dir=log_dir, run_id=run_id, stop_path=stop_path,
            on_started=children_started, on_change=children_changed,
        )
    except Exception as exc:
        state.update(status="failed", stopped_at=int(time.time()), error=type(exc).__name__)
        _write_state(state_path, state)
        print(f"daemon supervisor stopped: {type(exc).__name__}", file=sys.stderr)
        return 1
    state.update(status="stopped", stopped_at=int(time.time()), exit_code=result, interrupted=stopping)
    _write_state(state_path, state)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
