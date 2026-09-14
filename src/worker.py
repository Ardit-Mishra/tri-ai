"""Tri-AI worker — the loop around the single execution path.

    board.release_stale_claims(conn)              # THE wrapper, never the kernel's
    select a ready task                           # verify_command NOT NULL
    claimed = kb.claim_task(conn, task_id, claimer="host:pid")
    executor.precheck(conn, repo)                 # expected_branch=None — see note
    executor.check_upstream_artifacts(conn, task_id)
    executor.run_agent(repo, prompt, timeout=..., usage_path=...)
    if agent tree_survived: quarantine + STOP the whole worker (durable)
    executor.run_verify(verify_command, cwd=repo, timeout=verify_timeout)
    if passed: kb.complete_task(...)
    else: executor.revert(...); re-assert clean; record the failed attempt
          via the kernel's sanctioned path; ledger
    ledger entry; release; repeat

A delegated task is accepted or rejected by the verify command's exit code,
never by the agent's own report — `src/executor.py` is the single execution
path, and this module is the loop around it plus the auditable record.

Non-negotiables (the safety-boundary test asserts the first two structurally):

1. **`board.release_stale_claims`, never `kanban_db.release_stale_claims`.**
   Going direct reintroduces the Phase 1 Windows reclaim defect: the kernel
   infers "worker already gone" from a ``ProcessLookupError`` that Windows'
   ``os.kill`` never raises, so a dead worker's task stays ``running`` forever
   — a hang wearing an error's clothes. The adapter routes through the
   kernel's own ``signal_fn`` hook; going around it throws that away.
2. **Every kernel access goes through `board.kanban()`**, which *assigns*
   ``HERMES_KANBAN_DB`` rather than ``setdefault``-ing it, so a
   dispatcher-spawned worker can never silently keep the dispatcher's board.
3. **Claimer lock is `host:pid`** (``ledger.worker_id()``) and **run_id is
   the claimed Task's ``current_run_id``**, created by ``claim_task`` (the
   kernel owns run-id generation). The run_id feeds the ledger and
   ``executor.stash_message(task_id, run_id)``, so a retry can't match its
   predecessor's stash.
4. **The upstream-artifact gate runs before the agent is invoked at all** —
   a fabricated upstream result must not get a chance to propagate.
5. **A timeout is an outcome, not a string.** ``run_agent``/``run_verify``
   return discriminated results; on ``tree_survived`` the worker quarantines
   the workspace *durably* (on the board, where reclaim cannot reach it) and
   STOPS — the quarantine must outlive the worker, because Phase 1's reclaim
   would otherwise correctly return the task to ``ready`` and the next worker
   would run against the same repo with the same unaccounted-for writer.
6. **A verify command is never run over a turn that did not happen.** The
   agent runtime records in its usage file whether it completed; when it says
   it failed, the workspace is restored and the task backs off as an
   environment fault without verify being invoked. Exit status cannot stand in
   for this — hermes prints a provider error as its final response and exits
   0 — and a verifier handed an unchanged workspace will pass on the previous
   run's output.
7. **The claim is held only while the agent is demonstrably working.** The
   kernel renews an expired claim whenever the worker PID is alive, and its
   staleness backstop reads ``hb is not None and now - hb > MAX_STALE`` — so a
   worker that never writes ``last_heartbeat_at`` holds its claim forever,
   wedged or not, because NULL can never go stale. A heartbeat on a *timer*
   would be worse than none: it would assert health for a run hung on a dead
   socket, which is the exact case the backstop exists to catch. So the beat is
   earned: it is written only when the run produced *observable* progress
   since the last one — agent output, or a changed file in the workspace.
   Both are needed. Hermes one-shot writes its stdout once, at exit, so the
   output signal alone fires after the run is already over (measured: two
   100s+ runs, zero beats); the workspace is what actually moves while an
   agent works. Silence on both stops the beat, it goes stale, and the kernel
   stops renewing.
8. **Only a passed verify completes.** Everything else reverts —
   ``executor.revert`` (stash, never delete), then a post-revert
   ``git status --porcelain`` re-assertion, then the failure is recorded
   through the kernel's own bookkeeping so the row is neither stranded
   ``running`` nor left to spin. If the revert itself fails, or the tree is
   still dirty afterwards, the workspace is quarantined and the worker stops:
   the repo is in an unknown state.

Expected-branch note: the kernel's ``branch_name`` column is only valid for
``worktree`` workspaces. The worker enforces it for task-owned worktrees; dir
workspaces continue to precheck with no expected branch. The quarantine and
clean-tree guards remain active for both, and the observed branch is recorded
in the ledger for provenance.

What this module does NOT claim: that an agent it launches cannot push. A YOLO
child can reach anything this user can. The safety boundary is about *this
module's* execution path — its own code creates no process and runs no shell;
see `src/executor.py` for the two process-creation gateways and the one
deliberate verify-shell exemption.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import board  # noqa: F401  (closure module; all kernel access via board.kanban())
import executor
import failure_class
import ledger
import worktrees

# Exit codes. 3 is reserved for a quarantine stop so a Windows Scheduled Task
# can tell "the worker found the workspace unsafe" apart from "all clear".
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_QUARANTINE = 3

# The agent's own runtime budget when the task carries no max_runtime_seconds.
# Matches run_queue.py's default. The verify command's budget is the task's
# verify_timeout, enforced by the worker before execution.
DEFAULT_AGENT_TIMEOUT = 30 * 60

# Environment failures are delayed rather than treated as evidence that the
# task's logic is broken. The cap belongs to the environment lane, not the
# kernel's logic circuit breaker, and prevents an unattended worker loop.
ENVIRONMENT_BACKOFF_SECONDS = 5
ENVIRONMENT_RETRY_LIMIT = 3

# How often the heartbeat thread checks whether the agent produced output.
# Well under the kernel's 15-minute claim TTL so a working agent's claim is
# refreshed long before it expires, and far under the 1-hour staleness
# backstop so a wedged one is still caught promptly once beats stop.
HEARTBEAT_INTERVAL_SECONDS = 60

# Block kind for a quarantined workspace. `needs_input` is the "truly blocked /
# human must look" bucket in the kernel's VALID_BLOCK_KINDS; only an operator
# clears a quarantine, so nothing automated may decide the workspace is fine.
QUARANTINE_BLOCK_KIND = "needs_input"


@dataclass
class Attempt:
    """The result of one claim-and-execute pass over a single task."""

    outcome: str                     # the ledger outcome vocabulary term
    stop: bool = False               # True → the whole worker must stop now
    entry: Optional[dict[str, Any]] = None   # the ledger line that was written

    @property
    def quarantined(self) -> bool:
        return self.outcome == "quarantined"


@dataclass
class WorkerSummary:
    """Aggregate of one worker invocation: every attempt's outcome in order."""

    outcomes: list[str] = field(default_factory=list)
    stopped: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o] = out.get(o, 0) + 1
        return out


def worker_id() -> str:
    """This worker's ``host:pid`` identity — see ``ledger.worker_id``."""
    return ledger.worker_id()


# ---------------------------------------------------------------------------
# Ready-task selection
# ---------------------------------------------------------------------------


def ready_tasks(conn) -> list[dict[str, Any]]:
    """Every currently reclaimable ready task, oldest-first within priority.

    Delegates to ``board.ready_tasks`` — the single source of truth for
    ready-task selection.
    """
    return board.ready_tasks(conn)


def pick_ready_task(conn) -> Optional[str]:
    """One ready task id to claim, or None when there is nothing to do."""
    tasks = board.ready_tasks(conn)
    return str(tasks[0]["id"]) if tasks else None


# ---------------------------------------------------------------------------
# One task, end to end
# ---------------------------------------------------------------------------


def execute_task(
    conn,
    claimed,
    *,
    ledger_path: Optional[Path | str] = None,
    runs_root: Optional[Path | str] = None,
) -> Attempt:
    """Claimed task, through precheck → gate → agent → verify → accept/revert.

    ``claimed`` is the ``Task`` returned by ``kb.claim_task`` — its
    ``current_run_id`` IS the run id, owned by the kernel, used for the
    ledger, the quarantine record, and the stash message.
    """
    kb = board.kanban()
    run_id = claimed.current_run_id
    lp = Path(ledger_path) if ledger_path is not None else None
    root = Path(runs_root) if runs_root is not None else None
    started = time.time()

    task_id = claimed.id
    title = claimed.title
    repo = claimed.workspace_path
    branch: Optional[str] = None

    agent_log, verify_log = ledger.run_output_paths(task_id, run_id, root=root)
    usage_path = ledger.usage_path(task_id, run_id, root=root)

    if not repo:
        return _skip(
            conn, claimed, run_id, branch,
            reason="no workspace recorded for this task",
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=time.time() - started,
        )

    # --- precheck: quarantine, then clean tree (branch guard is off — see note)
    pre = executor.precheck(conn, repo, expected_branch=claimed.branch_name or None)
    branch = pre.branch
    if not pre.ok:
        if pre.reason.startswith("workspace quarantined"):
            # Blocked, not failed and not left ready — a quarantined task must
            # not spin: claim → refuse → reclaim → claim again. block_task
            # closes the run and returns the row to `blocked` for an operator.
            kb.block_task(
                conn, task_id,
                reason=pre.reason,
                kind=QUARANTINE_BLOCK_KIND,
                expected_run_id=run_id,
            )
            entry = _entry(
                claimed, run_id=run_id, repo=repo, branch=branch,
                outcome="blocked", reason=pre.reason, seconds=time.time() - started,
                agent_log=agent_log, verify_log=verify_log,
            )
            ledger.record(entry, path=lp)
            return Attempt("blocked", entry=entry)

        # Dirty tree, wrong branch, or an unreadable repo: the worker NEVER
        # touches a repo it did not dirty. reclaim_task is the kernel's public
        # mechanism for un-claiming without completing — it restores the
        # source phase (ready for our runs), closes the run as 'reclaimed',
        # and lets the task be claimed again by the next tick.
        return _skip(
            conn, claimed, run_id, branch, reason=pre.reason,
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=time.time() - started,
        )

    oracle = board.verify_spec(conn, task_id)
    if oracle is None or not oracle.get("verify_command"):
        _write_log(agent_log, "worker: agent never invoked — verify_command missing\n")
        _write_log(verify_log, "worker: verify never invoked — verify_command missing\n")
        return _fail(
            conn, claimed, run_id, repo, branch,
            outcome="failed", verify_outcome=None, verify_exit=None,
            agent=None,
            reason="verify_command is NULL at run time — will not execute",
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=time.time() - started,
        )
    verify_command = oracle["verify_command"]
    verify_timeout = oracle["verify_timeout"]
    if not verify_timeout:
        _write_log(agent_log, "worker: agent never invoked — verify_timeout missing\n")
        _write_log(verify_log, "worker: verify never invoked — verify_timeout missing\n")
        return _fail(
            conn, claimed, run_id, repo, branch,
            outcome="failed", verify_outcome=None, verify_exit=None,
            agent=None,
            reason="verify_timeout is NULL at run time — will not execute",
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=time.time() - started,
        )

    # --- VERIFY-04: the upstream gate, BEFORE the agent gets a chance.
    artifacts_ok, problems = executor.check_upstream_artifacts(conn, task_id)
    if not artifacts_ok:
        _write_log(agent_log, f"worker: agent never invoked — {problems}\n")
        _write_log(verify_log, "worker: verify never invoked — upstream gate failed\n")
        return _fail(
            conn, claimed, run_id, repo, branch,
            outcome="failed", verify_outcome=None, verify_exit=None,
            agent=None,
            reason=problems,
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=time.time() - started,
        )

    agent_timeout = claimed.max_runtime_seconds or DEFAULT_AGENT_TIMEOUT
    # The claim outlives its 15-minute TTL only while the agent is demonstrably
    # working. See _heartbeat_while_active: output earns the beat, elapsed time
    # does not.
    activity = _AgentActivity()
    with _heartbeat_while_active(
        task_id, run_id, activity,
        repo=repo, db_path=_database_file(conn),
    ):
        agent = executor.run_agent(
            repo, oracle.get("prompt") or "",
            timeout=agent_timeout, usage_path=usage_path,
            on_activity=activity.touch,
        )
    _write_log(agent_log, agent.output)

    # The agent's own report is NOT evidence; but a live process tree in the
    # workspace is not a state to keep working in. Quarantine durably, then
    # stop the WHOLE worker. No revert, no verify, no next claim.
    if agent.tree_survived:
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=lp, started=started,
            cause="agent_tree_survived",
            detail={"survivors": agent.survivors, "agent_exit": agent.exit_code},
        )

    # --- VERIFY-05: the agent runtime never got a turn. ------------------
    # This is NOT the agent reporting on its own work, which is never evidence.
    # It is the runtime's record — written by hermes to the usage file — that
    # the turn did not complete. The distinction matters because a provider
    # error is printed as the final response and the process still exits 0, so
    # exit status cannot see it. Letting verify run anyway is how a run that
    # never happened gets accepted: the command is given a workspace it did not
    # change, and any verifier that inspects state rather than a diff passes on
    # the previous run's output. The task is retried, not blamed — a model the
    # provider does not serve is an environment fault, not a logic one.
    #
    # Two shapes reach this gate, and the second was missed on the first pass:
    #
    #   runtime_failed is True  — a turn ran and the runtime recorded failure.
    #   runtime_failed is None  — no usage record exists at all. On its own that
    #       means nothing (a runtime that completed may simply not write one, so
    #       None must stay compatible), but paired with a NON-ZERO exit it means
    #       the process died before it could write one: hermes missing, spawn
    #       refused, killed at the timeout. `run_agent` returns exactly that on
    #       its containment-failure path — AgentResult(1, "FileNotFoundError:
    #       hermes.exe not found") with no usage file — and without this clause
    #       such a launch failure walks straight into verify and a
    #       state-inspecting verifier accepts the *previous* run's output.
    #       Same hollow gate as run 26, reached through a different door.
    launch_failed = agent.runtime_failed is None and agent.exit_code != 0
    if agent.runtime_failed or launch_failed:
        last = next(
            (ln for ln in reversed(agent.output.strip().splitlines()) if ln.strip()),
            "",
        )
        why = (
            "agent runtime exited %d without recording a turn" % agent.exit_code
            if launch_failed else "agent runtime reported failure"
        )
        _write_log(verify_log, f"worker: verify never invoked — {why}\n")
        unrestored = _restore_workspace(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=lp, started=started,
            verify_outcome=None,
        )
        if unrestored is not None:
            return unrestored
        return _environment_backoff(
            conn, claimed, run_id, repo, branch,
            verify_outcome=None, verify_exit=None, agent=agent,
            reason=f"{why}: {last[:250]}" if last else why,
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=agent.seconds,
        )

    # Read the workspace the moment the agent stops. Everything present now is
    # the agent's doing; anything that appears later belongs to the verify
    # command or to a writer this worker does not control, and calling that the
    # agent's output would be a claim the worker cannot support.
    agent_artifacts = _porcelain_artifacts(repo)

    verifier = executor.run_verify(verify_command, cwd=repo, timeout=verify_timeout)
    _write_log(verify_log, verifier.output)

    if verifier.tree_survived:
        # Same hard rule as the agent: an unknown writer is still alive. Do
        # not stash, do not claim anything else — quarantine, then stop.
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=lp, started=started,
            cause="verify_tree_survived",
            detail={
                "survivors": verifier.survivors,
                "verify_outcome": verifier.outcome,
            },
        )

    seconds = round(agent.seconds + verifier.seconds, 2)

    # A verify command proves the command. It says nothing about whether the
    # deliverable the task promised exists, so a declared artifact is checked
    # on its own terms and can fail a task whose command exited zero.
    declared_ok, declared_problem = _declared_artifacts_present(oracle, repo)
    if verifier.outcome == "passed" and not declared_ok:
        return _fail(
            conn, claimed, run_id, repo, branch,
            outcome="failed", verify_outcome="passed", verify_exit=0,
            agent=agent,
            reason=f"declared artifact check failed: {declared_problem}",
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=seconds,
        )

    if verifier.outcome == "passed":
        ok = kb.complete_task(
            conn, task_id,
            expected_run_id=run_id,
            result="verified",
            summary=f"verify exit 0 in {verifier.seconds}s",
            metadata={
                "verify_exit": 0,
                "seconds": seconds,
                "model": agent.model,
                "provider": agent.provider,
                "model_source": agent.model_source,
            },
        )
        if not ok:
            # complete refuses when a parent was reopened mid-run or the
            # expected_run_id no longer matches (ownership moved). Either way
            # the board did not accept the pass; record it as a hard failure
            # with the truth preserved, never as an unearned success.
            return _fail(
                conn, claimed, run_id, repo, branch,
                outcome="failed", verify_outcome="passed", verify_exit=0,
                agent=agent,
                reason="complete_task refused (expected_run_id mismatch or "
                       "parent reopened) — board did NOT go done",
                ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
                seconds=seconds, skip_board=True,
            )
        _record_artifacts(conn, task_id, run_id, repo, agent_artifacts)
        entry = _entry(
            claimed, run_id=run_id, repo=repo, branch=branch,
            outcome="passed", verify_exit=0, verify_outcome="passed",
            agent_exit=agent.exit_code,
            model=agent.model, provider=agent.provider,
            model_source=agent.model_source,
            seconds=seconds,
            agent_log=agent_log, verify_log=verify_log,
        )
        ledger.record(entry, path=lp)
        _record_outcome_proposal(conn, claimed, run_id, "passed")
        return Attempt("passed", entry=entry)

    # --- everything else reverts -----------------------------------------
    outcome = verifier.outcome                       # failed | timeout | spawn_error
    unrestored = _restore_workspace(
        conn, claimed, run_id, repo, branch, agent_log=agent_log,
        verify_log=verify_log, ledger_path=lp, started=started,
        verify_outcome=verifier.outcome,
    )
    if unrestored is not None:
        return unrestored

    classification = failure_class.classify_failure(
        verify_outcome=verifier.outcome,
        verify_exit=verifier.exit_code,
        verify_output=verifier.output,
    )
    if classification is failure_class.FailureClass.ENVIRONMENT:
        return _environment_backoff(
            conn, claimed, run_id, repo, branch,
            verify_outcome=verifier.outcome,
            verify_exit=verifier.exit_code, agent=agent,
            reason=verifier.output.strip().splitlines()[-1][:300]
            if verifier.output.strip() else None,
            ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
            seconds=seconds,
        )

    return _fail(
        conn, claimed, run_id, repo, branch,
        outcome=outcome, verify_outcome=verifier.outcome,
        verify_exit=verifier.exit_code, agent=agent,
        reason=verifier.output.strip().splitlines()[-1][:300] if verifier.output.strip() else None,
        ledger_path=lp, agent_log=agent_log, verify_log=verify_log,
        seconds=seconds,
    )


# ---------------------------------------------------------------------------
# Attempt endings
# ---------------------------------------------------------------------------


def _skip(
    conn, claimed, run_id, branch, *,
    reason, ledger_path, agent_log, verify_log, seconds,
) -> Attempt:
    """Claimed then released without touching the repo. Task returns to ready."""
    kb = board.kanban()
    _write_log(agent_log, f"worker: skipped — {reason}\n")
    _write_log(verify_log, f"worker: skipped — {reason}\n")
    # Public kernel mechanism for un-claiming without completing. Returns False
    # only if ownership already moved; the skip is real either way, so the
    # ledger line is written regardless.
    kb.reclaim_task(conn, claimed.id, reason=f"worker_skip: {reason}")
    entry = _entry(
        claimed, run_id=run_id, repo=claimed.workspace_path, branch=branch,
        outcome="skipped", reason=reason, seconds=seconds,
        agent_log=agent_log, verify_log=verify_log,
    )
    ledger.record(entry, path=ledger_path)
    return Attempt("skipped", entry=entry)


# Directory names never worth walking for progress evidence. `.git` churns on
# its own; the rest are dependency and build trees an agent does not hand-edit.
UNWALKED_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".pytest_cache", ".mypy_cache", "target",
})
# Ceiling on entries examined per scan, so one heartbeat tick cannot turn into a
# full walk of a large repository every minute.
WORKSPACE_SCAN_LIMIT = 4000


class _AgentActivity:
    """Records that the agent emitted output. Cheap, thread-safe, no I/O.

    Touched from ``run_agent``'s reader thread, read by the heartbeat thread.
    Deliberately holds nothing but a clock reading: the pipe-draining thread
    must never block, and anything it says about the agent beyond "bytes
    arrived" would be the agent's own account of itself.
    """

    __slots__ = ("_at", "_lock")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._at = time.monotonic()

    @property
    def at(self) -> float:
        with self._lock:
            return self._at


def _workspace_mtime(repo: Path | str, *, limit: int = WORKSPACE_SCAN_LIMIT) -> float:
    """Newest modification time under ``repo``, as evidence the agent is working.

    Output alone is not a usable liveness signal for this runtime, and finding
    that out cost a wasted fix. Hermes one-shot (`-z`) writes its response to
    stdout exactly once, immediately before exiting — see `run_oneshot`. So a
    heartbeat driven only by output fires once, after the run is already over,
    and two real 100s+ runs recorded zero beats.

    An agent's job here is to change files, so the workspace itself is the
    progress signal that actually exists: a run that is editing is advancing
    some mtime, and one wedged on a dead socket is not. Like output, this is raw
    evidence rather than the agent's account of itself.

    Bounded on purpose — dependency and build trees are skipped, and the walk
    stops at ``limit`` entries. A heartbeat that made the machine slower every
    minute would be its own defect.
    """
    newest = 0.0
    seen = 0
    try:
        root = Path(repo)
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in UNWALKED_DIRS]
            for name in files:
                seen += 1
                if seen > limit:
                    return newest
                try:
                    stamp = os.stat(os.path.join(current, name)).st_mtime
                except OSError:
                    continue
                if stamp > newest:
                    newest = stamp
    except OSError:
        pass
    return newest


def _database_file(conn) -> Optional[str]:
    """The file this connection is actually open on.

    Asked of the connection rather than re-resolved from configuration. A
    second thread that opens "the board" by global lookup can open a different
    board than the run is executing against — which is exactly what a
    dispatcher-spawned worker exists to prevent (non-negotiable 2), and what a
    test harness pinning a temp board would hit.
    """
    try:
        for _, name, path in conn.execute("PRAGMA database_list"):
            if name == "main":
                return path or None
    except sqlite3.Error:
        pass
    return None


@contextmanager
def _heartbeat_while_active(
    task_id: str,
    run_id: int,
    activity: _AgentActivity,
    *,
    repo: Optional[Path | str] = None,
    db_path: Optional[str] = None,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
):
    """Beat the claim only while the agent is actually producing output.

    The kernel extends an expired claim whenever the worker PID is alive, and
    treats a NULL ``last_heartbeat_at`` as never-stale — so a worker that never
    heartbeats holds its claim forever, wedged or not. Its documented backstop
    (``DEFAULT_CLAIM_HEARTBEAT_MAX_STALE_SECONDS``) only engages once a
    heartbeat exists to go stale.

    So the heartbeat has to *mean* something. A thread that beats on a timer
    would satisfy the kernel while proving nothing: it would report a healthy
    worker for a run hung on a dead socket, which is precisely the case the
    backstop exists to catch, and would leave the system worse than the NULL it
    replaced. This beats only when ``activity`` advanced since the last beat.
    A wedged agent stops emitting, the heartbeat stops, it goes stale, and the
    kernel reclaims.

    The thread owns its own connection: SQLite objects belong to the thread
    that made them, and the worker's connection is busy with the run. A failed
    beat is logged into the event stream by the kernel and otherwise ignored —
    losing one is a missed extension, never a reason to fail the task.
    """
    stop = threading.Event()
    seen = activity.at
    seen_mtime = _workspace_mtime(repo) if repo is not None else 0.0

    def _beat() -> None:
        nonlocal seen, seen_mtime
        conn = None
        try:
            conn = board.connect(Path(db_path)) if db_path else board.connect()
            kb = board.kanban()
            while not stop.wait(interval):
                current = activity.at
                mtime = _workspace_mtime(repo) if repo is not None else 0.0
                progressed = current != seen or mtime > seen_mtime
                if not progressed:
                    # Neither output nor a file change since the last beat. Say
                    # nothing rather than asserting liveness we cannot see.
                    continue
                seen, seen_mtime = current, max(mtime, seen_mtime)
                try:
                    kb.heartbeat_worker(
                        conn, task_id,
                        note="agent output observed",
                        expected_run_id=run_id,
                    )
                except sqlite3.Error:
                    # Ownership moved, or the board is momentarily busy. The
                    # claim simply does not get extended this tick.
                    pass
        except Exception:
            # A liveness helper must never be able to fail a run.
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    thread = threading.Thread(target=_beat, name="claim-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def _restore_workspace(
    conn, claimed, run_id, repo, branch, *,
    agent_log, verify_log, ledger_path, started, verify_outcome,
) -> Optional[Attempt]:
    """Return the workspace to its pre-run state, or quarantine and stop.

    Returns None when the repo is provably clean afterwards, and a terminal
    ``Attempt`` when it is not. Both non-pass exits go through here so the
    revert and the post-revert assertion cannot drift apart.
    """
    rv = executor.revert(repo, task_id=claimed.id, run_id=run_id)
    if rv.outcome == "failed" or not rv.clean:
        # The revert itself could not return the repo to a known state. That
        # is a serious event, not a retryable failure: quarantine and stop.
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=ledger_path, started=started,
            cause="revert_failed",
            detail={
                "revert_outcome": rv.outcome,
                "revert_output": rv.output[-500:],
                "verify_outcome": verify_outcome,
            },
        )

    code, status_out = executor.git(["status", "--porcelain"], cwd=repo)
    if code != 0 or status_out.strip():
        # Post-revert assertion failed: the repo is dirty after our own revert.
        # Unknown state again — quarantine and stop rather than proceed.
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=ledger_path, started=started,
            cause="post_revert_dirty",
            detail={
                "git_exit": code,
                "status_output": status_out[-500:],
                "verify_outcome": verify_outcome,
            },
        )
    return None


def _fail(
    conn, claimed, run_id, repo, branch, *,
    outcome, verify_outcome, verify_exit, agent, reason,
    ledger_path, agent_log, verify_log, seconds, skip_board=False,
) -> Attempt:
    """A non-pass attempt after the workspace was returned to a known state.

    Records the failure through the kernel's own bookkeeping, then writes the
    ledger entry. ``skip_board`` is set when the board was already left in a
    state we must not overwrite (e.g. complete_task refused); the ledger is
    still authoritative about the attempt.
    """
    if not skip_board:
        owned = _board_failure(
            conn, claimed, run_id, agent_log=str(agent_log),
            verify_outcome=verify_outcome, verify_exit=verify_exit,
            reason=reason,
        )
    else:
        owned = False
    model = provider = None
    model_source = "unavailable"
    if agent is not None:
        model, provider, model_source = agent.model, agent.provider, agent.model_source
    # The agent.log / verify.log files are NOT touched here: whatever ran
    # before this point already wrote its full, untruncated output, and the
    # ledger claim is that these files hold exactly that. When the failure
    # happened before anything ran (missing verify_command/timeout, upstream
    # gate), execute_task wrote the "never invoked" notes before calling _fail.
    entry = _entry(
        claimed, run_id=run_id, repo=repo, branch=branch,
        outcome=outcome, verify_exit=verify_exit, verify_outcome=verify_outcome,
        agent_exit=agent.exit_code if agent is not None else None,
        model=model, provider=provider, model_source=model_source,
        seconds=seconds, reason=reason, failure_class="logic",
        agent_log=agent_log, verify_log=verify_log,
    )
    if not owned and not skip_board:
        # We lost ownership of the claim before recording the board failure
        # (single worker: only a racy external reclaim can do this). The run
        # may already be closed by the reclaim; do not touch it. Say so.
        entry = dict(entry)
        entry["reason"] = (
            f"{entry.get('reason') or ''}; claim ownership lost before failure "
            "was recorded on the board".strip()
        )
    ledger.record(entry, path=ledger_path)
    if outcome == "failed":
        _record_outcome_proposal(conn, claimed, run_id, "failed")
    return Attempt(outcome, entry=entry)


def _record_outcome_proposal(conn, claimed, run_id: int, outcome: str) -> None:
    """Durably surface a terminal outcome without changing worker execution."""
    board.create_task_outcome_proposal(
        conn,
        task_id=claimed.id,
        run_id=int(run_id),
        title=claimed.title,
        outcome=outcome,
    )


def _environment_backoff(
    conn, claimed, run_id, repo, branch, *, verify_outcome,
    verify_exit, agent, reason, ledger_path, agent_log, verify_log, seconds,
) -> Attempt:
    """Ledger an environment failure without invoking the logic breaker.

    The task itself remains ``ready`` so its graph status is not rewritten, but
    the durable board record hides it from both worker and dispatcher until its
    due time. After three such retries it is explicitly blocked for an operator
    without creating a kernel ``gave_up`` logic-failure event.
    """
    kb = board.kanban()
    now = int(time.time())
    prior = board.environment_backoff(conn, claimed.id)
    attempts = int(prior["attempts"]) + 1 if prior is not None else 1
    exhausted = attempts > ENVIRONMENT_RETRY_LIMIT
    event_kind = "environment_retry_exhausted" if exhausted else "environment_backoff"
    retry_status = kb._retry_status_for_run(conn, claimed.id, run_id)
    run_outcome = (
        "timed_out" if verify_outcome == "timeout" else
        "spawn_failed" if verify_outcome == "spawn_error" else "failed"
    )
    owned = False
    with kb.write_txn(conn):
        if exhausted:
            cur = conn.execute(
                "UPDATE tasks SET status = 'blocked', claim_lock = NULL, claim_expires = NULL, "
                "worker_pid = NULL, last_heartbeat_at = NULL "
                "WHERE id = ? AND status = 'running' AND claim_lock = ? AND current_run_id = ?",
                (claimed.id, claimed.claim_lock, int(run_id)),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, "
                "worker_pid = NULL, last_heartbeat_at = NULL "
                "WHERE id = ? AND status = 'running' AND claim_lock = ? AND current_run_id = ?",
                (retry_status, claimed.id, claimed.claim_lock, int(run_id)),
            )
        if cur.rowcount == 1:
            owned = True
            closed = kb._end_run(
                conn, claimed.id, outcome=run_outcome, status=run_outcome,
                error=(reason or "environment failure")[:500],
                metadata={"failure_class": "environment", "attempts": attempts},
            )
            payload = {
                "verify_outcome": verify_outcome,
                "verify_exit": verify_exit,
                "attempts": attempts,
                "reason": reason,
            }
            if exhausted:
                kb._append_event(conn, claimed.id, event_kind, payload, run_id=closed)
            else:
                eligible_at = now + ENVIRONMENT_BACKOFF_SECONDS
                conn.execute(
                    "INSERT INTO triai_environment_backoff "
                    "(task_id, attempts, eligible_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(task_id) DO UPDATE SET attempts = excluded.attempts, "
                    "eligible_at = excluded.eligible_at, updated_at = excluded.updated_at",
                    (claimed.id, attempts, eligible_at, now, now),
                )
                kb._append_event(
                    conn, claimed.id, event_kind, {**payload, "eligible_at": eligible_at}, run_id=closed,
                )
    entry = _entry(
        claimed, run_id=run_id, repo=repo, branch=branch,
        outcome="environment_exhausted" if exhausted else "environment_backoff",
        verify_exit=verify_exit, verify_outcome=verify_outcome,
        agent_exit=agent.exit_code if agent is not None else None,
        model=agent.model if agent is not None else None,
        provider=agent.provider if agent is not None else None,
        model_source=agent.model_source if agent is not None else "unavailable",
        seconds=seconds, reason=reason, agent_log=agent_log, verify_log=verify_log,
        failure_class="environment",
    )
    if not owned:
        entry["reason"] = f"{entry.get('reason') or ''}; claim ownership lost before environment backoff".strip()
    ledger.record(entry, path=ledger_path)
    return Attempt(entry["outcome"], entry=entry)


def _board_failure(
    conn, claimed, run_id, *,
    agent_log, verify_outcome, verify_exit, reason,
) -> bool:
    """The kernel-sanctioned bookkeeping for a non-success attempt.

    This follows the kernel's own timeout path (`enforce_max_runtime`) exactly:

      1. inside one ``write_txn``:
           UPDATE tasks SET status=<retry_status>, claim_lock=NULL,
             claim_expires=NULL, worker_pid=NULL, last_heartbeat_at=NULL
             WHERE id=? AND status='running' AND claim_lock=? AND current_run_id=?
           _end_run(outcome=..., status=..., error=..., metadata=...)
           _append_event(...)
      2. after the txn commits:
           _record_task_failure(..., release_claim=False, end_run=False)

    ``_retry_status_for_run`` resolves the phase the task resumes from — for a
    Tri-AI run that is ``ready`` (we only ever claim from ``ready``), so a
    retry is a fresh claim of the same task. ``_record_task_failure``
    increments ``consecutive_failures`` and, when the counter reaches the
    effective limit (per-task ``max_retries`` else ``DEFAULT_FAILURE_LIMIT``
    = 2), trips the circuit breaker: the task becomes ``blocked`` with a
    ``gave_up`` event, so a permanently failing task cannot spin forever.

    The run-row outcome/status use the kernel's own documented vocabulary
    (`task_runs` schema comment: ``timed_out``/``spawn_failed``, and
    ``failed``). The ledger carries the authoritative per-attempt record with
    its own discriminated vocabulary.

    Returns True when the board accepted the record (we still owned the run).
    """
    kb = board.kanban()
    task_id = claimed.id
    retry_status = kb._retry_status_for_run(conn, task_id, run_id)

    error = reason or "no reason recorded"
    if verify_exit is not None:
        error = f"verify exit {verify_exit}"
    elif verify_outcome == "spawn_error":
        error = f"verify spawn error: {reason or ''}".strip()

    if verify_outcome == "timeout":
        run_outcome, event_kind = "timed_out", "verify_timeout"
    elif verify_outcome == "spawn_error":
        run_outcome, event_kind = "spawn_failed", "verify_spawn_error"
    else:
        run_outcome, event_kind = "failed", "verify_failed"

    payload = {
        "worker": ledger.worker_id(),
        "run_id": run_id,
        "verify_outcome": verify_outcome,
        "verify_exit": verify_exit,
        "agent_log": str(agent_log),
        "reason": reason,
    }

    owned = False
    with kb.write_txn(conn):
        cur = conn.execute(
            "UPDATE tasks SET status = ?, claim_lock = NULL, claim_expires = NULL, "
            "worker_pid = NULL, last_heartbeat_at = NULL "
            "WHERE id = ? AND status = 'running' AND claim_lock = ? "
            "  AND current_run_id = ?",
            (retry_status, task_id, claimed.claim_lock, int(run_id)),
        )
        if cur.rowcount == 1:
            owned = True
            # The environment series is consecutive. A real logic failure
            # begins the kernel's separate breaker series from this point.
            conn.execute("DELETE FROM triai_environment_backoff WHERE task_id = ?", (task_id,))
            closed = kb._end_run(
                conn, task_id,
                outcome=run_outcome, status=run_outcome,
                error=error[:500],
                metadata={"verify_outcome": verify_outcome, "worker": ledger.worker_id()},
            )
            kb._append_event(
                conn, task_id, event_kind,
                {**payload, "retry_status": retry_status},
                run_id=closed,
            )
    if owned:
        kb._record_task_failure(
            conn, task_id, error[:500],
            outcome=run_outcome,
            release_claim=False,
            end_run=False,
        )
    return owned


def _porcelain_artifacts(repo: Path | str) -> list[dict[str, Any]]:
    """Parse `git status --porcelain` into produced-artifact records.

    Observation only: the tree is read, never modified. A rename reports its
    destination, which is the path that now exists.

    ``--untracked-files=all`` is not optional. Plain porcelain collapses a new
    untracked directory into one `?? clock/` line, so an agent that builds a
    small site records as a single entry naming a directory rather than the
    files it wrote — and anything downstream that expects files (delivery, the
    declared-artifact gate, the operator reading the board) sees nothing it can
    use.
    """
    try:
        code, out = executor.git(
            ["status", "--porcelain", "--untracked-files=all"], cwd=repo,
        )
    except (executor.DisallowedGitCommand, OSError):
        return []
    if code != 0:
        return []
    artifacts: list[dict[str, Any]] = []
    for line in out.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        change = line[:2].strip() or "?"
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if not path:
            continue
        size: Optional[int] = None
        try:
            candidate = Path(repo) / path
            if candidate.is_file():
                size = candidate.stat().st_size
        except OSError:
            size = None
        artifacts.append({"path": path, "change": change, "size_bytes": size})
    return artifacts


def _declared_artifacts_present(oracle: Mapping[str, Any], repo: Path | str) -> tuple[bool, str]:
    """Check a task's own declared artifacts exist and are non-empty.

    Declaring nothing is not a failure - most tasks declare nothing, and the
    verify command is their only gate. Declaring something and not producing it
    is a failure regardless of what the command returned.
    """
    declared = oracle.get("expected_artifacts") or []
    problems: list[str] = []
    for item in declared:
        if not isinstance(item, str) or not item.strip():
            problems.append("a declared artifact is not a usable path")
            continue
        try:
            path = executor.resolve_artifact(repo, item)
        except executor.ArtifactEscape as exc:
            problems.append(str(exc))
            continue
        if not path.exists():
            problems.append(f"missing {item!r}")
        elif path.stat().st_size == 0:
            problems.append(f"empty {item!r}")
    return (not problems), "; ".join(problems)


def _record_artifacts(
    conn, task_id: str, run_id: int, repo: Path | str,
    agent_artifacts: Optional[Sequence[Mapping[str, Any]]] = None,
) -> int:
    """Record produced artifacts; never fail the run over bookkeeping."""
    # Prefer what the workspace held when the agent stopped. Falling back to a
    # fresh read keeps older callers working, but then the verify command's own
    # output is indistinguishable from the agent's.
    artifacts = list(agent_artifacts) if agent_artifacts is not None else _porcelain_artifacts(repo)
    if not artifacts:
        return 0
    try:
        return board.record_run_artifacts(
            conn, task_id=task_id, run_id=run_id, artifacts=artifacts,
        )
    except (ValueError, sqlite3.Error):
        # Bookkeeping must never turn a verified pass into a failure.
        return 0


def _quarantine_stop(
    conn, claimed, run_id, repo, branch, *,
    agent_log, verify_log, ledger_path, started, cause, detail,
) -> Attempt:
    """Durably quarantine the workspace and stop the whole worker.

    The quarantine record is written to the board FIRST — it is the restart
    path's guarantee: the claim expires, ``board.release_stale_claims``
    reclaims the task to ``ready`` (Phase 1 working exactly as designed), and
    the NEXT worker's precheck refuses the quarantined workspace and blocks
    the task. Nothing automated clears it; only an operator does.

    This worker deliberately does NOT close the run or touch the claim: the
    task stays ``running`` until the TTL reclaim, exactly as the plan's
    restart-path test expects.
    """
    repo = str(repo)
    board.quarantine_workspace(
        conn, repo, task_id=claimed.id, run_id=run_id,
        reason=cause, detail=detail,
    )
    entry = _entry(
        claimed, run_id=run_id, repo=repo, branch=branch,
        outcome="quarantined", reason=f"{cause} (survivors {detail.get('survivors')})"
        if "survivors" in detail else cause,
        seconds=time.time() - started,
        agent_log=agent_log, verify_log=verify_log,
    )
    ledger.record(entry, path=ledger_path)
    return Attempt("quarantined", stop=True, entry=entry)


# ---------------------------------------------------------------------------
# Ledger entry construction
# ---------------------------------------------------------------------------


def _entry(
    claimed, *, run_id, repo, branch, outcome,
    verify_exit=None, verify_outcome=None, agent_exit=None,
    model=None, provider=None, model_source="unavailable",
    seconds=0.0, reason=None, agent_log=None, verify_log=None, failure_class=None,
) -> dict[str, Any]:
    """One ledger line. Field names are documented in ledger.py's schema."""
    return {
        "ts": time.time(),
        "worker": ledger.worker_id(),
        "task_id": claimed.id,
        "run_id": run_id if run_id is not None else None,
        "title": claimed.title,
        "repo": str(repo),
        "branch": branch,
        "outcome": outcome,
        "verify_exit": verify_exit,
        "verify_outcome": verify_outcome,
        "agent_exit": agent_exit,
        "model": model,
        "provider": provider,
        "model_source": model_source,
        "seconds": round(float(seconds), 2),
        "reason": reason,
        "failure_class": failure_class,
        "agent_log": str(agent_log) if agent_log is not None else None,
        "verify_log": str(verify_log) if verify_log is not None else None,
    }


def _write_log(path: Path | str, text: str) -> None:
    """Write an output log, creating the directory if needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def run_once(
    conn,
    *,
    task_id: Optional[str] = None,
    ledger_path: Optional[Path | str] = None,
    runs_root: Optional[Path | str] = None,
) -> Optional[Attempt]:
    """One tick: release stale claims, claim one ready task, execute it.

    Returns None when there is nothing to do this tick (nothing ready, or the
    claim raced another worker). The release is deliberately FIRST and always
    goes through ``board.release_stale_claims``.
    """
    board.release_stale_claims(conn)              # THE wrapper, never the kernel's
    selected_id: Optional[str] = None
    for ready in board.ready_tasks(conn):
        if task_id is not None and ready["id"] != task_id:
            continue
        resolution = worktrees.resolve_task(conn, ready)
        if resolution.ready:
            selected_id = str(resolution.task["id"])
            break
        if task_id is not None:
            return None
    if selected_id is None:
        return None
    kb = board.kanban()
    claimed = kb.claim_task(conn, selected_id, claimer=ledger.worker_id())
    if claimed is None:
        return None
    # Register this worker's OWN pid as the claim's worker_pid (a private
    # kernel seam, the same precedent as board.posix_semantics_signal). The
    # kernel's release_stale_claims EXTENDS a claim whose host-local worker_pid
    # is still alive instead of reclaiming it (kanban_db.py:5000-5004, #23025) —
    # but only when worker_pid is truthy. Tri-AI workers never set it, so once
    # DEFAULT_CLAIM_TTL_SECONDS (15m) elapses a claim whose agent is still
    # running (default timeout 30m) is reclaimed to `ready` and a second worker
    # invocation spawns a second agent on the same repo: the double-spawn Phase
    # 1's reclaim exists to prevent. With the pid registered, an alive worker is
    # extended instead, and a genuinely dead/crashed worker is still reclaimed
    # (its pid is gone). The quarantine-restart path is unaffected: the pid is
    # really dead after the worker exits 3, so the left-open run is reclaimed to
    # `ready` and the next worker blocks on the quarantine, as designed.
    try:
        kb._set_worker_pid(conn, claimed.id, os.getpid())
    except Exception:
        # A claim without a registered pid re-creates the reclaim hazard. Do
        # not execute under it — hand it back so the next tick can retry.
        kb.reclaim_task(conn, claimed.id, reason="worker_pid_registration_failed")
        return None
    return execute_task(conn, claimed, ledger_path=ledger_path, runs_root=runs_root)


def run(
    conn,
    *,
    once: bool = False,
    task_id: Optional[str] = None,
    max_tasks: Optional[int] = None,
    ledger_path: Optional[Path | str] = None,
    runs_root: Optional[Path | str] = None,
) -> WorkerSummary:
    """Claim and execute tasks until the invocation's budget is exhausted.

    ``once`` stops after at most one attempt. ``max_tasks`` caps attempts per
    invocation (skips and quarantine stops count). A ``quarantined`` attempt
    stops the whole worker — the workspace is unsafe, so nothing further may
    run. Each tick starts with ``board.release_stale_claims``.
    """
    summary = WorkerSummary()
    while True:
        attempt = run_once(
            conn,
            task_id=task_id,
            ledger_path=ledger_path,
            runs_root=runs_root,
        )
        if attempt is None:
            break
        summary.outcomes.append(attempt.outcome)
        if attempt.stop:
            summary.stopped = True
            break
        if once:
            break
        if max_tasks is not None and len(summary.outcomes) >= int(max_tasks):
            break
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = """\
python src/worker.py [--once] [--max-tasks N] [--dry-run]
                     [--board PATH] [--ledger PATH] [--runs-dir PATH]

Runs verify-gated tasks from the Tri-AI board: claim → precheck → upstream
gate → agent → verify → accept or revert → ledger. Acceptance is decided ONLY
by the verify command's exit code.

--dry-run   lists ready tasks and their verify commands without claiming,
            running, writing the ledger, or touching any repo. Opening the
            board may still run the additive schema migration (verify columns,
            quarantine table) exactly as any connect would.
--ledger    where the JSONL ledger is appended (default $TRIAI_LEDGER or
            ~/.tri-ai/ledger.jsonl).
--runs-dir  where runs/<task_id>/<run_id>/{agent,verify}.log live
            (default $TRIAI_RUNS_DIR or ~/.tri-ai/runs).

Exit: 0 = ran cleanly (pass/fail are outcomes, recorded, not worker errors);
      1 = unexpected internal error; 3 = workspace quarantined, worker stopped
      so a restart-path run can refuse and block it.
"""


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Windows consoles default stdout/stderr to cp1252, which cannot encode the
    # — and → glyphs in the usage help (and in a "WORKSPACE QUARANTINED" stop
    # notice). Without this, `python src/worker.py --help` crashes on a stock
    # console before argparse even prints. Reconfigure to UTF-8 with
    # errors="replace" so an unprintable glyph degrades to ? instead of an
    # exception. Logs and the ledger are unaffected: they are written with an
    # explicit encoding, never through these streams.
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            try:
                _reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(
        prog="worker",
        description="Tri-AI verify-gated single worker.",
        epilog=USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--once", action="store_true",
                        help="run at most one task then exit")
    parser.add_argument("--task-id", default=None,
                        help="claim only this ready task, after workspace resolution")
    parser.add_argument("--max-tasks", type=int, default=None,
                        help="run at most N tasks this invocation")
    parser.add_argument("--dry-run", action="store_true",
                        help="list ready tasks and their verify commands; touch nothing")
    parser.add_argument("--board", default=None,
                        help="board DB path (default $TRIAI_BOARD_DB or ~/.tri-ai/board.db)")
    parser.add_argument("--ledger", default=None,
                        help="ledger path (default $TRIAI_LEDGER or ~/.tri-ai/ledger.jsonl)")
    parser.add_argument("--runs-dir", default=None,
                        help="per-run output dir (default $TRIAI_RUNS_DIR or ~/.tri-ai/runs)")
    args = parser.parse_args(argv)

    # Pin the board BEFORE the first kernel import/use. board.kanban() assigns
    # HERMES_KANBAN_DB from TRIAI_BOARD_DB; making the CLI flag feed that env
    # keeps every kernel path resolution on the same file the operator names.
    if args.board:
        os.environ["TRIAI_BOARD_DB"] = str(Path(args.board).resolve())

    ledger_path: Optional[Path] = Path(args.ledger) if args.ledger else None
    runs_root: Optional[Path] = Path(args.runs_dir) if args.runs_dir else None
    lp = Path(ledger.ledger_path()) if ledger_path is None else ledger_path
    rr = Path(ledger.runs_root()) if runs_root is None else runs_root

    conn = board.connect()
    try:
        if args.dry_run:
            tasks = ready_tasks(conn)
            print(f"{len(tasks)} ready task(s); everything listed below is "
                  "verified by exit code, nothing is touched by --dry-run.")
            for t in tasks:
                print(f"  {t['id']}  {t['title']!r}")
                print(f"      repo   : {t.get('workspace_path')}")
                print(f"      verify : {t.get('verify_command')}  "
                      f"(timeout {t.get('verify_timeout')}s)")
            return EXIT_OK

        summary = run(
            conn,
            once=args.once,
            task_id=args.task_id,
            max_tasks=args.max_tasks,
            ledger_path=lp,
            runs_root=rr,
        )
        counts = summary.counts
        print(f"\n{'=' * 60}")
        print(f"  attempts: {len(summary.outcomes)}  "
              f"{', '.join(f'{k}={v}' for k, v in counts.items()) or 'nothing ready'}")
        print(f"  ledger : {lp}")
        print(f"  runs   : {rr}")
        if summary.stopped:
            print("  WORKSPACE QUARANTINED — worker stopped. A restarted worker "
                  "will refuse the workspace and block its task; only an "
                  "operator clears the quarantine.")
            return EXIT_QUARANTINE
        return EXIT_OK
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
