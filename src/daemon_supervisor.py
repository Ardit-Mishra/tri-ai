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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

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
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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


def supervise(
    daemon_commands: DaemonCommands,
    *,
    log_dir: Path,
    run_id: str,
    stop_path: Path,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    on_started: Optional[Callable[[Sequence[subprocess.Popen[Any]]], None]] = None,
) -> int:
    """Run until a stop request or one daemon exits; never retry a failed child."""
    log_dir.mkdir(parents=True, exist_ok=True)
    worker_log, telegram_log = log_dir / "worker.log", log_dir / "telegram.log"
    rotate_log(worker_log)
    rotate_log(telegram_log)
    worker_handle = worker_log.open("a", encoding="utf-8")
    telegram_handle = telegram_log.open("a", encoding="utf-8")
    children: tuple[subprocess.Popen[Any], ...] = ()
    try:
        common = {"stdin": subprocess.DEVNULL, "creationflags": _creation_flags()}
        worker = popen(daemon_commands.worker, stdout=worker_handle, stderr=subprocess.STDOUT, **common)
        children = (worker,)
        telegram = popen(
            daemon_commands.telegram, stdout=telegram_handle, stderr=subprocess.STDOUT, **common,
        )
        children = (worker, telegram)
        if on_started is not None:
            on_started(children)
        while True:
            if stop_path.exists():
                return 0 if _stop_children(children) else 1
            exited = [child for child in children if child.poll() is not None]
            if exited:
                _stop_children(children)
                return 1
            time.sleep(0.25)
    finally:
        if children and any(child.poll() is None for child in children):
            _stop_children(children)
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

        result = supervise(
            commands(
                root=root, board_path=args.board.resolve(), ledger_path=args.ledger.resolve(),
                runs_root=args.runs_dir.resolve(),
                intake_policy=intake_policy.resolve() if intake_policy else None,
            ),
            log_dir=log_dir, run_id=run_id, stop_path=stop_path, on_started=children_started,
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
