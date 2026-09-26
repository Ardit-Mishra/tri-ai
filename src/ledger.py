"""Tri-AI audit ledger — the append-only, on-disk record of every attempt.

One JSON object per line, each line ``\\n`` terminated, appended with ``"a"``.
The ledger is the **authoritative per-attempt record**: the board row is
lifecycle state (what a task is doing now), the ledger is the evidence trail
(what actually happened, in order, with the full output). They join on
``task_id`` and ``run_id``, and on ``worker`` matching the board's
``claim_lock`` (``host:pid``).

What this module claims:

* Every attempt produces exactly one ledger line, including attempts that
  never ran an agent or a verifier — a skipped claim is a real event.
* Full, untruncated agent and verify output is written to
  ``runs/<task_id>/<run_id>/{agent,verify}.log`` (Phase 4's OBS-04 needs it),
  and the ledger entry carries those paths.
* The ``model``/``provider`` fields come from the run's ``--usage-file``, via
  ``executor.run_agent`` — the ledger never contains a modelled or guessed
  model name. A configured model name is an assertion, and this is the one
  file that must contain none. When the usage file is absent or lacks the
  field, ``model_source`` is ``"unavailable"`` and ``model`` is ``None``.

What this module does NOT claim:

* **Tamper-evidence.** The ledger lives on the same host and user as the agent
  it records, and that agent runs under ``HERMES_YOLO_MODE=1``; it can edit or
  delete the file. Entries reliably explain cooperative runs and catch an
  accident; they would not survive deliberate tampering. See
  `.planning/phases/phase-2-plan.md` ("Diagnostic, but not tamper-evident").
* **The whole truth.** The ledger is a file on the worker host; it is not the
  board and does not substitute for the kernel's ``task_runs`` closure.

Entry schema — every field is always present (``None`` where not applicable), and
the field names below are stable; the integration tests assert on them:

    ts            float       unix epoch seconds (``time.time()``)
    worker        str         "host:pid" — the SAME string the worker passes
                              as ``claimer=`` to ``claim_task``, so a ledger
                              line joins to a board row and a claim lock
    task_id       str
    run_id        int or None the claimed Task's ``current_run_id``
    title         str
    repo          str         workspace_path exactly as recorded on the board
    branch        str or None actual repo branch at precheck (provenance; see
                              the expected-branch note in worker.py)
    outcome       str         one of: passed | failed | timeout | spawn_error |
                              environment_backoff | environment_exhausted |
                              skipped | blocked | quarantined | dry_run
    verify_exit   int or None verify command exit code (only when it ran and
                              returned an exit; ``None`` for timeout — a
                              timeout must not borrow an exit code)
    verify_outcome str or None one of passed | failed | timeout | spawn_error,
                              or None when no verify command ran
    agent_exit    int or None the agent's exit code (``None`` when never run)
    model         str or None from the run's ``--usage-file``
    provider      str or None from the run's ``--usage-file``
    model_source  str         "usage_file" | "unavailable"
    seconds       float       agent + verify elapsed; for skips/blocks/quarantine
                              stops it is the claim-to-decision wall time;
                              0.0 for nothing-ran failures
    reason        str or None short, human-readable cause for a non-pass
                              outcome (verify exit, precheck detail, problems
                              from the upstream-artifact gate, quarantine cause)
    failure_class str or None "environment" when a deterministic verifier
                              signal delayed the task, "logic" for a recorded
                              logic failure, otherwise None
    agent_log     str or None absolute path to the full agent output
    verify_log    str or None absolute path to the full verify output
    api_calls     int or None how many model calls the turn took, from the
                              run's ``--usage-file``
    answered_without_tools
                  bool or None ``api_calls <= 1``: the model produced a final
                              answer without ever receiving a tool result, so
                              nothing it described reached the filesystem.
                              None when the runtime recorded no count - a
                              missing field is not an accusation. Measured
                              over the first 82 entries: 19 of 52 failures
                              against 1 of 21 passes, and `devstral:24b` in 10
                              of its 11 runs.

Outcome vocabulary (worker.py decides, ledger.py only records):

    passed        verify ran and exited 0; the board row was completed
    failed        the attempt was executed and not accepted: verify exit != 0,
                  OR the upstream-artifact gate failed, OR the verify command
                  was missing at run time ("fails its own verify step",
                  per the Phase 2 plan). ``verify_outcome`` / ``reason``
                  distinguish the cause.
    timeout       the verify command timed out and its process tree was
                  terminated; ``verify_exit`` is None
    spawn_error   the verify command could not be started
    environment_backoff
                  a verifier environment failure was recorded and the still-
                  ready task is hidden from board consumers until its durable
                  eligibility time; it did not increment the logic breaker.
    environment_exhausted
                  more than the bounded number of consecutive environment
                  retries occurred; the task is blocked for an operator,
                  without a kernel ``gave_up`` logic-failure event.
    skipped       the task was claimed, then released WITHOUT touching the
                  repo (dirty tree, branch mismatch, no workspace, unreadable
                  repo). ``reason`` carries the precheck detail. A dirty tree
                  the worker did not dirty is never fixed by the worker.
    blocked       the workspace was already quarantined at claim time; the
                  task was ``block_task``-blocked so it cannot spin.
                  ``reason`` carries the quarantine detail.
    quarantined   THIS worker observed a surviving process tree (agent or
                  verify), or a failed revert / dirty post-revert state, and
                  durably quarantined the workspace before stopping.
    dry_run       reserved for a listing that writes a ledger line. The stock
                  ``worker --dry-run`` touches nothing and writes nothing, so
                  no ``dry_run`` line is produced by the worker itself.
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
#
# Both the ledger file and the per-run output tree are gitignored (the default
# live under ~/.tri-ai, outside the repo entirely) and are overridable by
# environment variable and, in worker.py, by CLI flag, so tests use a temp path
# and never touch the operator's audit trail.


def ledger_path() -> Path:
    """The ledger file. ``$TRIAI_LEDGER`` overrides ``~/.tri-ai/ledger.jsonl``."""
    override = os.environ.get("TRIAI_LEDGER", "").strip()
    return Path(override) if override else Path.home() / ".tri-ai" / "ledger.jsonl"


def runs_root() -> Path:
    """Where ``runs/<task_id>/<run_id>/`` lives.
    ``$TRIAI_RUNS_DIR`` overrides ``~/.tri-ai/runs``."""
    override = os.environ.get("TRIAI_RUNS_DIR", "").strip()
    return Path(override) if override else Path.home() / ".tri-ai" / "runs"


def worker_id() -> str:
    """The worker's identity: ``host:pid``.

    This is the SAME string worker.py passes as ``claimer=`` to
    ``claim_task``, so a ledger line joins to a board row and a claim lock.
    It has to be computed the same way in both places; the kernel's own
    default claimer uses exactly ``socket.gethostname()`` + ``os.getpid()``.
    """
    try:
        host = socket.gethostname() or "unknown"
    except Exception:
        host = "unknown"
    return f"{host}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Per-run output layout
# ---------------------------------------------------------------------------


def run_output_paths(
    task_id: str,
    run_id: Optional[int],
    *,
    root: Optional[Path] = None,
) -> tuple[Path, Path]:
    """The agent and verify log paths for one attempt, creating the directory.

    ``runs/<task_id>/<run_id>/{agent,verify}.log`` — full, untruncated. The
    ledger entry carries these paths so the raw evidence is findable from the
    record.
    """
    directory = (Path(root) if root is not None else runs_root()) / str(task_id)
    if run_id is not None:
        directory = directory / str(int(run_id))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "agent.log", directory / "verify.log"


def usage_path(
    task_id: str,
    run_id: Optional[int],
    *,
    root: Optional[Path] = None,
) -> Path:
    """Where the agent's ``--usage-file`` lands for this attempt.

    ``runs/<task_id>/<run_id>/usage.json``, co-located with the output logs.
    ``executor.run_agent`` reads the model/provider back from it; the file
    itself is kept as an audit artifact.
    """
    directory = (Path(root) if root is not None else runs_root()) / str(task_id)
    if run_id is not None:
        directory = directory / str(int(run_id))
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "usage.json"


# ---------------------------------------------------------------------------
# Append / read
# ---------------------------------------------------------------------------


def record(entry: Mapping[str, Any], *, path: Optional[Path] = None) -> Path:
    """Append ``entry`` as one JSON line to the ledger. Returns the path.

    Creates the ledger's directory if needed. The write is done with ``"a"``
    and is never rewritten in place — append-only is the point.

    A serialization failure raises: a ledger that silently drops an attempt
    is worse than none, so a non-serializable entry is a hard error rather
    than a skipped line.
    """
    target = Path(path) if path is not None else ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(entry), ensure_ascii=False)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return target


def read_entries(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Read every entry back, in order. Blank lines are skipped; a line that
    is not JSON raises ``ValueError`` (a corrupt ledger is a serious event,
    not something to paper over with a skip)."""
    target = Path(path) if path is not None else ledger_path()
    entries: list[dict[str, Any]] = []
    if not target.exists():
        return entries
    for lineno, line in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{target}:{lineno}: ledger line is not JSON — {exc}"
            ) from exc
        entries.append(parsed)
    return entries
