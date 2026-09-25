"""Rich terminal dashboard over authoritative Tri-AI evidence.

This module is intentionally a reader. It opens the board through SQLite's
read-only URI mode, tails the append-only ledger, and reads the supervisor's
retained state file. It neither imports the board API nor has network,
credential, task-control, or process-spawn capability.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


DEFAULT_RUNTIME_ROOT = Path.home() / ".tri-ai"


def runtime_paths(root: Path | str = DEFAULT_RUNTIME_ROOT) -> dict[str, Path]:
    """Where the authoritative runtime state actually lives.

    The layout was previously spelled out at each call site. That is fine until
    a new reader is written from memory: the beacon guessed `board.sqlite3` and
    a `daemons.json` at the root, so it raised DashboardSourceError before it
    could post anything, and the node board it feeds was never reachable. One
    definition, so a second reader cannot disagree with the first.
    """
    base = Path(root)
    return {
        "board_path": base / "board.db",
        "ledger_path": base / "ledger.jsonl",
        "daemon_state_path": base / "logs" / "daemons.json",
        "runs_root": base / "runs",
        "brain_path": base / "brain" / "brain.db",
        "capability_catalog_path": base / "capabilities" / "catalog.json",
        "radar_path": base / "radar" / "latest.json",
    }
PidAlive = Callable[[int], bool]
WINDOWS_STILL_ACTIVE = 259
LOG_TAIL_LINES = 40
LOG_TAIL_BYTES = 65536
LOG_LINE_CHARS = 240


class DashboardSourceError(RuntimeError):
    """An authoritative source cannot be read as required for a snapshot."""


PHASE_ORDER = ("claimed", "worktree_prep", "agent_active", "verify_gate")


@dataclass(frozen=True)
class PhaseView:
    """One lifecycle stage and the evidence that decided its state."""
    key: str
    state: str  # done | active | pending | skipped
    evidence: str


@dataclass(frozen=True)
class RunLogView:
    """A bounded tail of one retained run log."""
    name: str
    path: str
    lines: tuple[str, ...]
    truncated: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class TaskTelemetry:
    """Run facts read from the board and the retained run directory."""
    run_id: Optional[int] = None
    run_status: Optional[str] = None
    run_outcome: Optional[str] = None
    started_at: Optional[int] = None
    ended_at: Optional[int] = None
    heartbeat_at: Optional[int] = None
    worker_pid: Optional[int] = None
    claim_lock: Optional[str] = None
    claim_expires: Optional[int] = None
    step_key: Optional[str] = None
    branch_name: Optional[str] = None
    worktree_path: Optional[str] = None
    workspace_kind: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    summary: Optional[str] = None
    error: Optional[str] = None
    phases: tuple[PhaseView, ...] = ()
    logs: tuple[RunLogView, ...] = ()
    artifacts: tuple[dict, ...] = ()


@dataclass(frozen=True)
class TaskView:
    task_id: str
    title: str
    status: str
    run_id: Optional[int]
    workspace_path: Optional[str] = None
    telemetry: Optional[TaskTelemetry] = None
    prompt: Optional[str] = None


@dataclass(frozen=True)
class TaskEdge:
    parent_id: str
    child_id: str


@dataclass(frozen=True)
class RuleCitationView:
    source_path: str
    source_line: int
    line_digest: str


@dataclass(frozen=True)
class RuleView:
    proposal_id: str
    rule_id: str
    workspace_path: str
    task_kind: str
    checks: tuple[str, ...]
    citations: tuple[RuleCitationView, ...]


@dataclass(frozen=True)
class LedgerEvent:
    timestamp: object
    task_id: str
    outcome: str
    verify_exit: object
    seconds: object


@dataclass(frozen=True)
class DaemonHealth:
    status: str
    processes: tuple[tuple[str, bool], ...]
    diagnostic: Optional[str]


@dataclass(frozen=True)
class BrainItemView:
    item_id: str
    title: str
    kind: str
    source: str
    project: Optional[str]
    trust: str
    created_at: float


@dataclass(frozen=True)
class BrainEdgeView:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    evidence_item_id: str


@dataclass(frozen=True)
class BrainView:
    status: str = "uninitialized"
    item_count: int = 0
    inbox_count: int = 0
    edge_count: int = 0
    items: tuple[BrainItemView, ...] = ()
    edges: tuple[BrainEdgeView, ...] = ()
    diagnostic: Optional[str] = None


@dataclass(frozen=True)
class CapabilityItemView:
    resource_id: str
    name: str
    kind: str
    availability: str
    adapter_status: str
    health_status: str
    risk_status: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityView:
    status: str = "uninitialized"
    total: int = 0
    active: int = 0
    archived: int = 0
    routable: int = 0
    gated: int = 0
    candidates: int = 0
    items: tuple[CapabilityItemView, ...] = ()
    diagnostic: Optional[str] = None


@dataclass(frozen=True)
class RadarCandidateView:
    name: str
    source: str
    url: str
    disposition: str
    stars: int = 0
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class RadarEvaluationView:
    name: str
    disposition: str
    static_verdict: str
    dynamic_status: str


@dataclass(frozen=True)
class RadarView:
    status: str = "uninitialized"
    generated_at: Optional[str] = None
    candidate_count: int = 0
    evaluated_count: int = 0
    error_count: int = 0
    candidates: tuple[RadarCandidateView, ...] = ()
    evaluations: tuple[RadarEvaluationView, ...] = ()
    diagnostic: Optional[str] = None


@dataclass(frozen=True)
class DashboardSnapshot:
    tasks: tuple[TaskView, ...]
    edges: tuple[TaskEdge, ...]
    ledger_events: tuple[LedgerEvent, ...]
    ledger_entry_count: int
    ledger_errors: tuple[str, ...]
    activated_rule_count: int
    daemons: DaemonHealth
    rules: tuple[RuleView, ...] = ()
    memory_errors: tuple[str, ...] = ()
    brain: BrainView = BrainView()
    capabilities: CapabilityView = CapabilityView()
    radar: RadarView = RadarView()


def _readonly_board(board_path: Path | str) -> sqlite3.Connection:
    path = Path(board_path).resolve()
    if not path.is_file():
        raise DashboardSourceError(f"board database does not exist: {path}")
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise DashboardSourceError(f"board database is unreadable: {path}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
    ).fetchone() is not None


def _read_active_rules(conn: sqlite3.Connection) -> tuple[tuple[RuleView, ...], tuple[str, ...]]:
    """Project accepted rules without importing or invoking the memory engine."""
    if not _table_exists(conn, "triai_activated_procedural_rules"):
        return (), ()
    rules: list[RuleView] = []
    errors: list[str] = []
    rows = conn.execute(
        "SELECT proposal_id, rule_json FROM triai_activated_procedural_rules ORDER BY proposal_id"
    ).fetchall()
    for row in rows:
        proposal_id = str(row["proposal_id"])
        try:
            raw = json.loads(row["rule_json"])
            if not isinstance(raw, Mapping):
                raise ValueError("rule is not an object")
            rule_id, workspace, task_kind = raw.get("id"), raw.get("workspace"), raw.get("task_kind")
            checks, raw_citations = raw.get("checks"), raw.get("citations")
            if not all(isinstance(value, str) and value.strip() for value in (rule_id, workspace, task_kind)):
                raise ValueError("rule identity is incomplete")
            if not isinstance(checks, list) or not checks or not all(isinstance(item, str) and item for item in checks):
                raise ValueError("rule checks are invalid")
            if not isinstance(raw_citations, list) or not raw_citations:
                raise ValueError("rule has no provenance citations")
            citations: list[RuleCitationView] = []
            for citation in raw_citations:
                if not isinstance(citation, Mapping):
                    raise ValueError("rule citation is invalid")
                path, line, digest = citation.get("source_path"), citation.get("source_line"), citation.get("line_digest")
                if not isinstance(path, str) or not path or isinstance(line, bool) or not isinstance(line, int) or line < 1:
                    raise ValueError("rule citation location is invalid")
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError("rule citation digest is invalid")
                citations.append(RuleCitationView(path, line, digest))
            rules.append(RuleView(
                proposal_id, rule_id.strip(), workspace.strip(), task_kind.strip(),
                tuple(checks), tuple(citations),
            ))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"accepted rule {proposal_id} is unreadable: {exc}")
    return tuple(rules), tuple(errors)


def _optional_int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_text(value: object) -> Optional[str]:
    return value.strip() or None if isinstance(value, str) else None


def _read_run_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """Project run records without importing the board API."""
    if not _table_exists(conn, "task_runs"):
        return {}
    rows = conn.execute(
        "SELECT id, task_id, step_key, status, claim_lock, claim_expires, worker_pid, "
        "last_heartbeat_at, started_at, ended_at, outcome, summary, metadata, error "
        "FROM task_runs ORDER BY id"
    ).fetchall()
    return {int(row["id"]): row for row in rows if _optional_int(row["id"]) is not None}


def _read_worktrees(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    if not _table_exists(conn, "triai_worktrees"):
        return {}
    rows = conn.execute(
        "SELECT task_id, target_path, branch_name FROM triai_worktrees"
    ).fetchall()
    return {str(row["task_id"]): row for row in rows}


def _read_run_event_kinds(conn: sqlite3.Connection) -> dict[int, set[str]]:
    """Collect the lifecycle event kinds already recorded against each run."""
    if not _table_exists(conn, "task_events"):
        return {}
    kinds: dict[int, set[str]] = {}
    for row in conn.execute(
        "SELECT run_id, kind FROM task_events WHERE run_id IS NOT NULL"
    ).fetchall():
        run_id = _optional_int(row["run_id"])
        kind = _optional_text(row["kind"])
        if run_id is not None and kind is not None:
            kinds.setdefault(run_id, set()).add(kind)
    return kinds


def _verify_exit(raw: object) -> Optional[int]:
    """The exit code a finished run recorded, if it recorded one."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, Mapping):
        return None
    return _optional_int(parsed.get("verify_exit"))


def _run_metadata(raw: object) -> tuple[Optional[str], Optional[str]]:
    """Read the model/provider the run actually recorded, never a guess."""
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, Mapping):
        return None, None
    return _optional_text(parsed.get("model")), _optional_text(parsed.get("provider"))


def _read_log_tail(path: Path, name: str) -> RunLogView:
    """Tail one retained run log under a fixed byte and line bound."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > LOG_TAIL_BYTES:
                handle.seek(size - LOG_TAIL_BYTES)
            payload = handle.read()
    except OSError as exc:
        return RunLogView(name, str(path), (), False, f"log is unreadable ({type(exc).__name__})")
    text = payload.decode("utf-8", errors="replace")
    lines = [line[:LOG_LINE_CHARS] for line in text.splitlines() if line.strip()]
    truncated = size > LOG_TAIL_BYTES or len(lines) > LOG_TAIL_LINES
    return RunLogView(name, str(path), tuple(lines[-LOG_TAIL_LINES:]), truncated, None)


def _read_run_logs(runs_root: Optional[Path], task_id: str, run_id: Optional[int]) -> tuple[RunLogView, ...]:
    """Read the active run's retained logs from a path the reader derived itself.

    The directory is built only from identifiers already read out of the board,
    never from caller-supplied text, so there is no traversable path surface.
    """
    if runs_root is None or run_id is None:
        return ()
    directory = Path(runs_root) / task_id / str(run_id)
    logs: list[RunLogView] = []
    for name in ("agent", "verify"):
        candidate = directory / f"{name}.log"
        if candidate.is_file():
            logs.append(_read_log_tail(candidate, name))
    return tuple(logs)


def _derive_phases(
    *,
    workspace_kind: Optional[str],
    event_kinds: set[str],
    worktree: Optional[sqlite3.Row],
    logs: Sequence[RunLogView],
    run_status: Optional[str],
    run_active: bool,
    verify_evidence: Optional[str] = None,
) -> tuple[PhaseView, ...]:
    """Decide each lifecycle stage from recorded evidence only."""
    verify_log = next((log for log in logs if log.name == "verify" and log.lines), None)
    reached: dict[str, tuple[bool, str]] = {
        "claimed": ("claimed" in event_kinds, "board recorded a claimed event"),
        "worktree_prep": (worktree is not None, "worktree row recorded for this task"),
        "agent_active": ("spawned" in event_kinds, "board recorded a spawned worker child"),
        "verify_gate": (
            (verify_log is not None, "verify log has retained output")
            if verify_log is not None or verify_evidence is None
            else (True, verify_evidence)
        ),
    }
    skipped: dict[str, str] = {}
    if workspace_kind is not None and workspace_kind != "worktree" and worktree is None:
        skipped["worktree_prep"] = f"{workspace_kind} workspace has no worktree stage"

    phases: list[PhaseView] = []
    last_reached = -1
    for index, key in enumerate(PHASE_ORDER):
        if reached[key][0]:
            last_reached = index
    for index, key in enumerate(PHASE_ORDER):
        hit, evidence = reached[key]
        if hit:
            state = "active" if (run_active and index == last_reached) else "done"
        elif key in skipped:
            state, evidence = "skipped", skipped[key]
        elif index < last_reached:
            state, evidence = "skipped", "no evidence recorded for this stage"
        elif run_active:
            state, evidence = "pending", "not reached yet"
        else:
            state, evidence = "skipped", f"run ended {run_status or 'without this stage'}"
        phases.append(PhaseView(key, state, evidence))
    return tuple(phases)


def _read_artifact_rows(conn: sqlite3.Connection) -> dict[tuple[str, int], tuple[dict, ...]]:
    """Group recorded artifacts by the run that produced them."""
    if not _table_exists(conn, "triai_run_artifacts"):
        return {}
    grouped: dict[tuple[str, int], list[dict]] = {}
    for row in conn.execute(
        "SELECT task_id, run_id, path, change, size_bytes FROM triai_run_artifacts ORDER BY path"
    ).fetchall():
        key = (str(row["task_id"]), int(row["run_id"]))
        grouped.setdefault(key, []).append({
            "path": str(row["path"]), "change": str(row["change"]),
            "size_bytes": _optional_int(row["size_bytes"]),
        })
    return {key: tuple(value) for key, value in grouped.items()}


def _task_telemetry(
    row: sqlite3.Row,
    *,
    runs: Mapping[int, sqlite3.Row],
    worktrees: Mapping[str, sqlite3.Row],
    event_kinds: Mapping[int, set[str]],
    runs_root: Optional[Path],
    artifacts: Mapping[tuple[str, int], tuple[dict, ...]] = {},
) -> TaskTelemetry:
    """Assemble one task's run facts from already-read board evidence."""
    task_id = str(row["id"])
    run_id = _optional_int(row["current_run_id"])
    if run_id is None:
        candidates = [rid for rid, run in runs.items() if str(run["task_id"]) == task_id]
        run_id = max(candidates) if candidates else None
    run = runs.get(run_id) if run_id is not None else None
    run_status = _optional_text(run["status"]) if run is not None else None
    run_active = run_status == "running"
    logs = _read_run_logs(runs_root, task_id, run_id) if run_active else ()
    worktree = worktrees.get(task_id)
    workspace_kind = _optional_text(row["workspace_kind"])
    model, provider = _run_metadata(run["metadata"]) if run is not None else (None, None)
    verify_evidence = None
    if run is not None and not run_active:
        exit_code = _verify_exit(run["metadata"])
        if exit_code is not None:
            verify_evidence = f"run recorded verify exit {exit_code}"
        elif _optional_text(run["summary"]):
            verify_evidence = f"run summary: {_optional_text(run['summary'])}"
    return TaskTelemetry(
        run_id=run_id,
        run_status=run_status,
        run_outcome=_optional_text(run["outcome"]) if run is not None else None,
        started_at=(
            _optional_int(run["started_at"]) if run is not None
            else _optional_int(row["started_at"])
        ),
        ended_at=_optional_int(run["ended_at"]) if run is not None else None,
        heartbeat_at=(
            _optional_int(run["last_heartbeat_at"]) if run is not None
            else _optional_int(row["last_heartbeat_at"])
        ),
        worker_pid=_optional_int(row["worker_pid"]),
        claim_lock=_optional_text(row["claim_lock"]),
        claim_expires=_optional_int(row["claim_expires"]),
        step_key=(
            _optional_text(row["current_step_key"])
            or (_optional_text(run["step_key"]) if run is not None else None)
        ),
        branch_name=(
            _optional_text(row["branch_name"])
            or (_optional_text(worktree["branch_name"]) if worktree is not None else None)
        ),
        worktree_path=_optional_text(worktree["target_path"]) if worktree is not None else None,
        workspace_kind=workspace_kind,
        model=model or _optional_text(row["model_override"]),
        provider=provider or _optional_text(row["provider_override"]),
        summary=_optional_text(run["summary"]) if run is not None else None,
        error=_optional_text(run["error"]) if run is not None else None,
        phases=_derive_phases(
            workspace_kind=workspace_kind,
            event_kinds=event_kinds.get(run_id, set()) if run_id is not None else set(),
            worktree=worktree,
            logs=logs,
            run_status=run_status,
            run_active=run_active,
            verify_evidence=verify_evidence,
        ),
        logs=logs,
        artifacts=artifacts.get((task_id, run_id), ()) if run_id is not None else (),
    )


def _read_board(
    board_path: Path | str,
    *,
    runs_root: Optional[Path] = None,
) -> tuple[tuple[TaskView, ...], tuple[TaskEdge, ...], int, tuple[RuleView, ...], tuple[str, ...]]:
    conn = _readonly_board(board_path)
    try:
        try:
            task_rows = conn.execute(
                "SELECT id, title, body, status, current_run_id, workspace_path, workspace_kind, "
                "branch_name, current_step_key, worker_pid, claim_lock, claim_expires, "
                "started_at, last_heartbeat_at, model_override, provider_override FROM tasks "
                "ORDER BY priority DESC, created_at ASC"
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT parent_id, child_id FROM task_links ORDER BY parent_id, child_id"
            ).fetchall()
            runs = _read_run_rows(conn)
            worktrees = _read_worktrees(conn)
            event_kinds = _read_run_event_kinds(conn)
            artifact_rows = _read_artifact_rows(conn)
        except sqlite3.Error as exc:
            raise DashboardSourceError("board schema cannot supply dashboard evidence") from exc
        rules, rule_errors = _read_active_rules(conn)
        return (
            tuple(
                TaskView(
                    row["id"], row["title"], row["status"], row["current_run_id"],
                    str(row["workspace_path"]) if row["workspace_path"] is not None else None,
                    _task_telemetry(
                        row, runs=runs, worktrees=worktrees,
                        event_kinds=event_kinds, runs_root=runs_root,
                        artifacts=artifact_rows,
                    ),
                    _optional_text(row["body"]),
                )
                for row in task_rows
            ),
            tuple(TaskEdge(row["parent_id"], row["child_id"]) for row in edge_rows),
            len(rules), rules, rule_errors,
        )
    finally:
        conn.close()


@dataclass(frozen=True)
class ArtifactView:
    """One produced file, already proven to sit inside its task's workspace."""
    task_id: str
    run_id: int
    index: int
    path: str
    absolute: str
    change: str
    size_bytes: Optional[int]


def read_task_artifacts(board_path: Path | str, task_id: str) -> tuple[ArtifactView, ...]:
    """Read a task's produced artifacts from the board, newest run only.

    The workspace and the relative path both come from the board. A recorded
    path that escapes its own workspace is dropped rather than served - the
    containment check is the security boundary, so it fails closed.
    """
    conn = _readonly_board(board_path)
    try:
        if not _table_exists(conn, "triai_run_artifacts"):
            return ()
        task = conn.execute(
            "SELECT workspace_path FROM tasks WHERE id = ?", (str(task_id),)
        ).fetchone()
        if task is None or not task["workspace_path"]:
            return ()
        rows = conn.execute(
            "SELECT task_id, run_id, path, change, size_bytes FROM triai_run_artifacts "
            "WHERE task_id = ? AND run_id = ("
            "  SELECT MAX(run_id) FROM triai_run_artifacts WHERE task_id = ?"
            ") ORDER BY path",
            (str(task_id), str(task_id)),
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        conn.close()

    try:
        workspace = Path(str(task["workspace_path"])).resolve()
    except OSError:
        return ()

    views: list[ArtifactView] = []
    for index, row in enumerate(rows):
        relative = str(row["path"])
        try:
            resolved = (workspace / relative).resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(workspace):
            continue
        views.append(ArtifactView(
            task_id=str(row["task_id"]),
            run_id=int(row["run_id"]),
            index=index,
            path=relative,
            absolute=str(resolved),
            change=str(row["change"]),
            size_bytes=_optional_int(row["size_bytes"]),
        ))
    return tuple(views)


def _read_ledger(path: Path | str, *, limit: int) -> tuple[tuple[LedgerEvent, ...], int, tuple[str, ...]]:
    if limit < 1:
        raise ValueError("ledger_limit must be positive")
    target = Path(path)
    if not target.is_file():
        return (), 0, (f"ledger does not exist: {target}",)
    events: list[LedgerEvent] = []
    errors: list[str] = []
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return (), 0, (f"ledger is unreadable: {target} ({type(exc).__name__})",)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"ledger line {line_number} is not valid JSON")
            continue
        if not isinstance(raw, dict):
            errors.append(f"ledger line {line_number} is not an object")
            continue
        task_id = raw.get("task_id")
        outcome = raw.get("outcome")
        if not isinstance(task_id, str) or not isinstance(outcome, str):
            errors.append(f"ledger line {line_number} lacks task_id or outcome")
            continue
        events.append(LedgerEvent(
            raw.get("ts"), task_id, outcome, raw.get("verify_exit"), raw.get("seconds"),
        ))
    return tuple(reversed(events[-limit:])), len(events), tuple(errors)


def _pid_alive(pid: int) -> bool:
    """Read one process-liveness fact without signalling or controlling it."""
    if pid < 1:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a reliable existence probe on Windows.
        # A query-only process handle is read-only and does not start, signal,
        # or otherwise control the daemon being observed.
        #
        # Opening the handle is not itself liveness: a terminated process keeps
        # its process object while any handle to it remains open, so OpenProcess
        # still succeeds for a daemon that has already exited. Only the recorded
        # exit code separates the two. STILL_ACTIVE is the documented sentinel;
        # a process that genuinely exits with code 259 is indistinguishable, and
        # that ambiguity is inherent to the Win32 contract.
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
            open_process.restype = ctypes.c_void_p
            get_exit_code = kernel32.GetExitCodeProcess
            get_exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
            get_exit_code.restype = ctypes.c_bool
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (ctypes.c_void_p,)
            close_handle.restype = ctypes.c_bool
            handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        except OSError:
            return False
        if not handle:
            return False
        try:
            code = ctypes.c_uint32(0)
            if not get_exit_code(handle, ctypes.byref(code)):
                return False
            return code.value == WINDOWS_STILL_ACTIVE
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _read_daemon_health(
    state_path: Path | str, *, pid_alive: PidAlive,
) -> DaemonHealth:
    path = Path(state_path)
    if not path.is_file():
        return DaemonHealth("unknown", (), f"supervisor state does not exist: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return DaemonHealth("unknown", (), f"supervisor state is unreadable: {type(exc).__name__}")
    if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
        return DaemonHealth("unknown", (), "supervisor state has no status")
    processes: list[tuple[str, bool]] = []
    for label, field in (("supervisor", "supervisor_pid"), ("worker", "worker_pid"), ("telegram", "telegram_pid")):
        pid = raw.get(field)
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            processes.append((label, pid_alive(pid)))
        else:
            processes.append((label, False))
    return DaemonHealth(raw["status"], tuple(processes), None)


def _read_brain(path: Path | str, *, limit: int = 24) -> BrainView:
    source = Path(path).resolve()
    if not source.is_file():
        return BrainView(diagnostic=f"Brain database does not exist: {source}")
    try:
        conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("brain_items", "brain_inbox", "brain_edges")
        }
        rows = conn.execute(
            "SELECT item_id, title, kind, source, project, trust, created_at "
            "FROM brain_items WHERE trust != 'rejected' "
            "ORDER BY created_at DESC, item_id LIMIT ?",
            (limit,),
        ).fetchall()
        item_ids = tuple(row["item_id"] for row in rows)
        edge_rows: Sequence[sqlite3.Row] = ()
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            edge_rows = conn.execute(
                "SELECT edge_id, source_id, target_id, relation, evidence_item_id "
                f"FROM brain_edges WHERE source_id IN ({placeholders}) "
                f"AND target_id IN ({placeholders}) ORDER BY relation, edge_id",
                (*item_ids, *item_ids),
            ).fetchall()
        return BrainView(
            status="ready",
            item_count=counts["brain_items"],
            inbox_count=counts["brain_inbox"],
            edge_count=counts["brain_edges"],
            items=tuple(
                BrainItemView(
                    row["item_id"], row["title"], row["kind"], row["source"],
                    row["project"], row["trust"], float(row["created_at"]),
                )
                for row in rows
            ),
            edges=tuple(
                BrainEdgeView(
                    row["edge_id"], row["source_id"], row["target_id"],
                    row["relation"], row["evidence_item_id"],
                )
                for row in edge_rows
            ),
        )
    except sqlite3.Error as exc:
        return BrainView(status="error", diagnostic=f"Brain database is unreadable: {type(exc).__name__}")
    finally:
        if "conn" in locals():
            conn.close()


def _read_capabilities(path: Path | str, *, limit: int = 40) -> CapabilityView:
    source = Path(path).resolve()
    if not source.is_file():
        return CapabilityView(diagnostic=f"Capability catalog does not exist: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CapabilityView(status="error", diagnostic=f"Capability catalog is unreadable: {type(exc).__name__}")
    if document.get("schema") != "triai.capability-catalog.v1":
        return CapabilityView(status="error", diagnostic="Capability catalog schema is unsupported")
    raw_resources = document.get("resources")
    if not isinstance(raw_resources, list):
        return CapabilityView(status="error", diagnostic="Capability catalog has no resource list")
    availability: dict[str, int] = {}
    items: list[CapabilityItemView] = []
    for raw in raw_resources:
        if not isinstance(raw, Mapping):
            continue
        state = str(raw.get("availability") or "unknown")
        availability[state] = availability.get(state, 0) + 1
        # The showcase list is adapter-focused. The complete skill estate is
        # represented by counts and remains searchable in the catalog itself.
        if not str(raw.get("resource_id") or "").startswith("adapter:"):
            continue
        items.append(CapabilityItemView(
            str(raw.get("resource_id") or "unknown"),
            str(raw.get("name") or "Unnamed capability"),
            str(raw.get("kind") or "unknown"),
            state,
            str(raw.get("adapter_status") or "unadapted"),
            str(raw.get("health_status") or "not-checked"),
            str(raw.get("risk_status") or "unreviewed"),
            tuple(str(tag) for tag in raw.get("tags", []) if isinstance(tag, str)),
        ))
    items.sort(key=lambda item: (
        {"executable": 0, "registered": 1, "active": 2, "gated": 3, "source-only": 4, "missing": 5}.get(item.availability, 9),
        item.name.casefold(),
    ))
    routable = sum(availability.get(key, 0) for key in ("executable", "registered", "active"))
    return CapabilityView(
        status="ready",
        total=len(raw_resources),
        active=availability.get("active", 0),
        archived=availability.get("archived-reference", 0),
        routable=routable,
        gated=availability.get("gated", 0) + availability.get("source-only", 0),
        candidates=availability.get("missing", 0) + availability.get("candidate-only", 0),
        items=tuple(items[:limit]),
    )


def _read_radar(path: Path | str, *, limit: int = 16) -> RadarView:
    source = Path(path).resolve()
    if not source.is_file():
        return RadarView(diagnostic=f"Technology radar report does not exist: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return RadarView(status="error", diagnostic=f"Technology radar report is unreadable: {type(exc).__name__}")
    if document.get("schema") != "triai.technology-radar.v1":
        return RadarView(status="error", diagnostic="Technology radar schema is unsupported")
    candidates: list[RadarCandidateView] = []
    for raw in document.get("candidates", []):
        if not isinstance(raw, Mapping):
            continue
        candidates.append(RadarCandidateView(
            str(raw.get("name") or "unknown"), str(raw.get("source") or "unknown"),
            str(raw.get("url") or ""), str(raw.get("disposition") or "evaluate"),
            int(raw.get("stars") or 0),
            tuple(str(signal) for signal in raw.get("signals", []) if isinstance(signal, str)),
        ))
    evaluations: list[RadarEvaluationView] = []
    for raw in document.get("evaluations", []):
        if not isinstance(raw, Mapping):
            continue
        candidate = raw.get("candidate") if isinstance(raw.get("candidate"), Mapping) else {}
        static = raw.get("static") if isinstance(raw.get("static"), Mapping) else {}
        dynamic = raw.get("dynamic") if isinstance(raw.get("dynamic"), Mapping) else {}
        evaluations.append(RadarEvaluationView(
            str(candidate.get("name") or "unknown"),
            str(raw.get("disposition") or "unknown"),
            str(static.get("verdict") or "not-run"),
            str(dynamic.get("status") or "not-run"),
        ))
    errors = document.get("errors", [])
    return RadarView(
        status="ready",
        generated_at=str(document.get("generated_at")) if document.get("generated_at") else None,
        candidate_count=len(candidates), evaluated_count=len(evaluations),
        error_count=len(errors) if isinstance(errors, list) else 0,
        candidates=tuple(candidates[:limit]), evaluations=tuple(evaluations[:limit]),
    )


def read_snapshot(
    *,
    board_path: Path | str,
    ledger_path: Path | str,
    daemon_state_path: Path | str,
    ledger_limit: int = 12,
    pid_alive: PidAlive = _pid_alive,
    runs_root: Optional[Path | str] = None,
    brain_path: Optional[Path | str] = None,
    capability_catalog_path: Optional[Path | str] = None,
    radar_path: Optional[Path | str] = None,
) -> DashboardSnapshot:
    """Read one immutable dashboard snapshot from the authoritative sources."""
    tasks, edges, activated_rule_count, rules, memory_errors = _read_board(
        board_path, runs_root=Path(runs_root) if runs_root is not None else None,
    )
    events, ledger_entry_count, ledger_errors = _read_ledger(ledger_path, limit=ledger_limit)
    return DashboardSnapshot(
        tasks=tasks,
        edges=edges,
        ledger_events=events,
        ledger_entry_count=ledger_entry_count,
        ledger_errors=ledger_errors,
        activated_rule_count=activated_rule_count,
        daemons=_read_daemon_health(daemon_state_path, pid_alive=pid_alive),
        rules=rules,
        memory_errors=memory_errors,
        brain=_read_brain(
            brain_path if brain_path is not None else Path(board_path).resolve().parent / "brain" / "brain.db"
        ),
        capabilities=_read_capabilities(
            capability_catalog_path if capability_catalog_path is not None
            else Path(board_path).resolve().parent / "capabilities" / "catalog.json"
        ),
        radar=_read_radar(
            radar_path if radar_path is not None
            else Path(board_path).resolve().parent / "radar" / "latest.json"
        ),
    )


def _status_counts(tasks: Sequence[TaskView]) -> str:
    counts: dict[str, int] = {}
    for task in tasks:
        counts[task.status] = counts.get(task.status, 0) + 1
    return ", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "none"


def _renderable(snapshot: DashboardSnapshot) -> Group:
    heading = Panel(
        Text("TRI-AI JARVIS | Read-only evidence dashboard", style="bold cyan"),
        subtitle=(
            f"tasks={len(snapshot.tasks)} ({_status_counts(snapshot.tasks)}) | "
            f"ledger={snapshot.ledger_entry_count} | accepted rules={snapshot.activated_rule_count}"
        ),
    )
    tasks = Table(title="Board", expand=True)
    tasks.add_column("Task", style="cyan", no_wrap=True)
    tasks.add_column("Status")
    tasks.add_column("Run", justify="right")
    tasks.add_column("Title", overflow="fold")
    for task in snapshot.tasks:
        tasks.add_row(task.task_id, task.status, "-" if task.run_id is None else str(task.run_id), task.title)

    edges = Table(title="Dependencies", expand=True)
    edges.add_column("Parent -> child")
    if snapshot.edges:
        for edge in snapshot.edges:
            edges.add_row(f"{edge.parent_id} -> {edge.child_id}")
    else:
        edges.add_row("none")

    events = Table(title="Recent ledger events (newest first)", expand=True)
    events.add_column("Time")
    events.add_column("Task")
    events.add_column("Outcome")
    events.add_column("Verify exit")
    if snapshot.ledger_events:
        for event in snapshot.ledger_events:
            events.add_row(str(event.timestamp), event.task_id, event.outcome, str(event.verify_exit))
    else:
        events.add_row("-", "-", "no valid entries", "-")

    health = ", ".join(
        f"{label}: {'up' if alive else 'down'}" for label, alive in snapshot.daemons.processes
    ) or "no recorded processes"
    health_panel = Panel(
        f"state: {snapshot.daemons.status}\n{health}"
        + (f"\n{snapshot.daemons.diagnostic}" if snapshot.daemons.diagnostic else ""),
        title="Daemon supervisor",
    )
    diagnostics = Panel(
        "\n".join(snapshot.ledger_errors) if snapshot.ledger_errors else "none",
        title="Ledger diagnostics",
    )
    return Group(heading, tasks, edges, events, health_panel, diagnostics)


def render_snapshot(snapshot: DashboardSnapshot, *, console: Console) -> None:
    """Render a snapshot through an injected Rich console for testability."""
    console.print(_renderable(snapshot))


def _arguments(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the read-only Tri-AI JARVIS terminal dashboard.")
    parser.add_argument("--board", type=Path, default=DEFAULT_RUNTIME_ROOT / "board.db")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_RUNTIME_ROOT / "ledger.jsonl")
    parser.add_argument("--daemon-state", type=Path, default=DEFAULT_RUNTIME_ROOT / "logs" / "daemons.json")
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNTIME_ROOT / "runs")
    parser.add_argument("--ledger-limit", type=int, default=12)
    parser.add_argument("--watch", action="store_true", help="Refresh without modifying any source.")
    parser.add_argument("--refresh-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if args.ledger_limit < 1:
        parser.error("--ledger-limit must be positive")
    if args.refresh_seconds <= 0:
        parser.error("--refresh-seconds must be positive")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _arguments(argv)
    console = Console()

    def snapshot() -> DashboardSnapshot:
        return read_snapshot(
            board_path=args.board,
            ledger_path=args.ledger,
            daemon_state_path=args.daemon_state,
            ledger_limit=args.ledger_limit,
            runs_root=args.runs_root,
        )

    try:
        if not args.watch:
            render_snapshot(snapshot(), console=console)
            return 0
        with Live(console=console, refresh_per_second=4, transient=True) as live:
            while True:
                live.update(_renderable(snapshot()))
                time.sleep(args.refresh_seconds)
    except KeyboardInterrupt:
        return 0
    except (DashboardSourceError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"jarvis dashboard stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
