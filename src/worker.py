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
6. **Only a passed verify completes.** Everything else reverts —
   ``executor.revert`` (stash, never delete), then a post-revert
   ``git status --porcelain`` re-assertion, then the failure is recorded
   through the kernel's own bookkeeping so the row is neither stranded
   ``running`` nor left to spin. If the revert itself fails, or the tree is
   still dirty afterwards, the workspace is quarantined and the worker stops:
   the repo is in an unknown state.

Expected-branch note (a pinned decision, not an open question): the kernel's
``branch_name`` column is only valid for ``worktree`` workspaces, so the board
cannot hold an expected branch for Tri-AI's ``dir`` workspaces in this wave.
The worker therefore calls ``executor.precheck(conn, repo)`` with
``expected_branch=None``; the quarantine guard and the clean-tree guard are
still active. The branch guard is a documented gap. The repo's actual branch
at precheck time is recorded in the ledger for provenance.

What this module does NOT claim: that an agent it launches cannot push. A YOLO
child can reach anything this user can. The safety boundary is about *this
module's* execution path — its own code creates no process and runs no shell;
see `src/executor.py` for the two process-creation gateways and the one
deliberate verify-shell exemption.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import board  # noqa: F401  (closure module; all kernel access via board.kanban())
import executor
import ledger

# Exit codes. 3 is reserved for a quarantine stop so a Windows Scheduled Task
# can tell "the worker found the workspace unsafe" apart from "all clear".
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_QUARANTINE = 3

# The agent's own runtime budget when the task carries no max_runtime_seconds.
# Matches run_queue.py's default. The verify command's budget is the task's
# verify_timeout, enforced by the worker before execution.
DEFAULT_AGENT_TIMEOUT = 30 * 60

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

    Used by ``--dry-run`` (lists without touching anything) and by
    ``pick_ready_task``. ``verify_command`` must be present: an unverifiable
    task does not belong on the board (board.create_task enforces that at
    write time; this re-checks rows written through lower-level APIs).
    """
    rows = conn.execute(
        "SELECT id, title, workspace_path, verify_command, verify_timeout "
        "FROM tasks "
        "WHERE status = 'ready' AND claim_lock IS NULL "
        "  AND verify_command IS NOT NULL "
        "ORDER BY priority DESC, created_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def pick_ready_task(conn) -> Optional[str]:
    """One ready task id to claim, or None when there is nothing to do."""
    tasks = conn.execute(
        "SELECT id FROM tasks "
        "WHERE status = 'ready' AND claim_lock IS NULL "
        "  AND verify_command IS NOT NULL "
        "ORDER BY priority DESC, created_at ASC LIMIT 1"
    ).fetchone()
    return str(tasks["id"]) if tasks else None


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
    pre = executor.precheck(conn, repo, expected_branch=None)
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
    agent = executor.run_agent(
        repo, oracle.get("prompt") or "",
        timeout=agent_timeout, usage_path=usage_path,
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
        return Attempt("passed", entry=entry)

    # --- everything else reverts -----------------------------------------
    outcome = verifier.outcome                       # failed | timeout | spawn_error
    rv = executor.revert(repo, task_id=task_id, run_id=run_id)
    if rv.outcome == "failed" or not rv.clean:
        # The revert itself could not return the repo to a known state. That
        # is a serious event, not a retryable failure: quarantine and stop.
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=lp, started=started,
            cause="revert_failed",
            detail={
                "revert_outcome": rv.outcome,
                "revert_output": rv.output[-500:],
                "verify_outcome": verifier.outcome,
            },
        )

    code, status_out = executor.git(["status", "--porcelain"], cwd=repo)
    if code != 0 or status_out.strip():
        # Post-revert assertion failed: the repo is dirty after our own revert.
        # Unknown state again — quarantine and stop rather than proceed.
        return _quarantine_stop(
            conn, claimed, run_id, repo, branch, agent_log=agent_log,
            verify_log=verify_log, ledger_path=lp, started=started,
            cause="post_revert_dirty",
            detail={
                "git_exit": code,
                "status_output": status_out[-500:],
                "verify_outcome": verifier.outcome,
            },
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
        seconds=seconds, reason=reason,
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
    return Attempt(outcome, entry=entry)


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
    seconds=0.0, reason=None, agent_log=None, verify_log=None,
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
    ledger_path: Optional[Path | str] = None,
    runs_root: Optional[Path | str] = None,
) -> Optional[Attempt]:
    """One tick: release stale claims, claim one ready task, execute it.

    Returns None when there is nothing to do this tick (nothing ready, or the
    claim raced another worker). The release is deliberately FIRST and always
    goes through ``board.release_stale_claims``.
    """
    board.release_stale_claims(conn)              # THE wrapper, never the kernel's
    task_id = pick_ready_task(conn)
    if task_id is None:
        return None
    kb = board.kanban()
    claimed = kb.claim_task(conn, task_id, claimer=ledger.worker_id())
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
        attempt = run_once(conn, ledger_path=ledger_path, runs_root=runs_root)
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
