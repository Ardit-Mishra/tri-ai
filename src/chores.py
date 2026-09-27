"""Tri-AI v1 chore catalog — the repository-level verify commands (VERIFY-05).

WHAT THIS MODULE IS
-------------------
A typed table of reusable "chores": read-only, CPU/IO-bound repository tasks
(EXEC-02) that a cheap free lane can both execute and self-verify. A catalog
row is a TEMPLATE, not a task. Nothing here names a real repository, hostname,
path or credential — this repository deliberately carries none of those, so
per-repo assignment (which chore applies to which repo, with what repo-specific
notes) is operator data, assembled from these rows. `src/chores.example.jsonl`
shows that assembly in the legacy queue format.

Each row carries the four things a verify gate needs:

* ``prompt_template`` — the task handed to the agent. Scoped to the read-only
  CPU/IO chores EXEC-02 permits; there is no feature-work template here on
  purpose. A feature task needs a human and review, not a free lane.
* ``verify_command`` — the fixed shell command string run with ``cwd=repo``.
  Its exit code decides pass/fail; the agent's own report never does.
* ``verify_timeout_seconds`` — the value that ships, set *from measurement* and
  validated against the calibration record, not guessed (see below).
* ``deliberate_break`` — one line describing how calibration breaks this chore,
  so a gate that cannot fail is not a gate.

WHAT IT SHIPS
-------------
Exactly the five rows in the plan's calibration table: test suite (pytest),
typecheck (tsc), build (npm), JS dependency advisories (npm audit), dependency
compatibility (uv pip check).

WHAT IT DOES NOT SHIP, AND WHY
------------------------------
Python advisory auditing (pip-audit) is deliberately absent. Its calibration
failed: ``uv tool run --from pip-audit==2.10.1 pip-audit --help`` runs, but a
real audit of a ``django==2.2.0`` fixture returned no verdict and no exit code
within the review window. A verify command that can hang is worse than one that
is missing — the worker stalls holding a claim, and Phase 1's reclaim machinery
has to rescue what should have been a clean failure. The command can return
once someone establishes a bounded, offline-capable invocation and calibrates
it. Until then Python repos get the compatibility check only, and the gap is
stated rather than papered over.

Also note: ``uv pip check`` and ``npm audit`` are two different checks with two
different breaks. ``uv pip check`` validates that installed packages' dependency
constraints are mutually satisfiable; it does NOT consult any advisory database,
so an advisory-bearing pin exits 0 and proves nothing against it. Each chore
here is paired with the break its own command can actually detect.

HOW THE TIMEOUTS ARE WIRED
--------------------------
``verify_timeout_seconds`` for a shipped chore comes from, or is validated
against, ``docs/verify-calibration.json`` — the record written by
``scripts/calibrate_verify.py``. ``verify_timeout_for(name)`` returns the
calibrated value straight from the record; ``assert_calibration_matches()``
refuses a catalog whose shipped timeouts disagree with the record. A chore that
is not calibrated does not ship: a verifier that cannot be calibrated is not a
gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_FILE = REPO_ROOT / "docs" / "verify-calibration.json"


class CalibrationError(RuntimeError):
    """A chore cannot ship because its calibration record is missing or wrong."""


def calibration_path() -> Path:
    """Where scripts/calibrate_verify.py writes its record."""
    return CALIBRATION_FILE


def _calibration_records() -> dict:
    """The record's ``chores`` mapping, name -> record. Raises if absent."""
    if not CALIBRATION_FILE.exists():
        raise CalibrationError(
            f"calibration record not found at {CALIBRATION_FILE}. "
            "Run scripts/calibrate_verify.py before shipping chores; a verify "
            "command whose gate is unproven does not ship."
        )
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))["chores"]
    except (KeyError, json.JSONDecodeError) as exc:
        raise CalibrationError(
            f"calibration record at {CALIBRATION_FILE} is unreadable: {exc}"
        ) from None


@dataclass(frozen=True)
class Chore:
    """One catalog row. Immutable; a row changes only by review, not at runtime."""

    name: str
    repo_kinds: tuple[str, ...]   # "js" and/or "python" — which repos the chore suits
    prompt_template: str          # the body given to the agent (EXEC-02 scoped)
    verify_command: str           # run with cwd=repo; exit code decides pass/fail
    verify_timeout_seconds: int   # shipped value, validated against calibration
    deliberate_break: str         # one line: how calibration proves the gate can fail


# The verify timeouts here ship from measurement: each row must equal the
# timeout_chosen_seconds that scripts/calibrate_verify.py recorded for it
# (assert_calibration_matches() enforces exactly that). The `120`s below are
# the formula output for the observed fixture runtimes — see the calibration
# record's timeout_policy note.
CHORES: tuple[Chore, ...] = (
    Chore(
        name="pytest-suite",
        repo_kinds=("python",),
        prompt_template=(
            "Run this repository's Python test suite and fix what fails.\n\n"
            "Command (verify gate): python -m pytest -q\n"
            "Run it from the repo root. If any test fails, read the failure and "
            "fix the CODE — not the test, unless the test is provably asserting "
            "something false. Re-run until the suite is green. If you cannot fix "
            "it, say so plainly and change nothing. Report the exact final pytest "
            "summary line verbatim."
        ),
        verify_command="python -m pytest -q",
        verify_timeout_seconds=120,
        deliberate_break="assert a false statement in one test",
    ),
    Chore(
        name="tsc-typecheck",
        repo_kinds=("js",),
        prompt_template=(
            "Run this repository's TypeScript typecheck and fix what fails.\n\n"
            "Command (verify gate): tsc --noEmit\n"
            "Run it from the repo root. If the typecheck reports errors, read the "
            "failure and fix the CODE — not the tsconfig, unless the tsconfig is "
            "provably wrong. Re-run until tsc --noEmit exits 0. If you cannot fix "
            "it, say so plainly and change nothing. Report the error count before "
            "and after."
        ),
        verify_command="tsc --noEmit",
        verify_timeout_seconds=120,
        deliberate_break="assign a string to an int-typed name",
    ),
    Chore(
        name="build",
        repo_kinds=("js",),
        prompt_template=(
            "Run this repository's build and fix what fails.\n\n"
            "Command (verify gate): npm run build\n"
            "Run it from the repo root. If the build fails, read the error and fix "
            "the CODE — not the build script, unless the script is provably wrong. "
            "Re-run until the build exits 0. If you cannot fix it, say so plainly "
            "and change nothing. Report the build's final output line verbatim."
        ),
        verify_command="npm run build",
        verify_timeout_seconds=120,
        deliberate_break="introduce a syntax error in an entry module",
    ),
    Chore(
        name="npm-audit-advisory",
        repo_kinds=("js",),
        prompt_template=(
            "Audit this repository's npm dependencies for high-severity "
            "advisories and fix what fails.\n\n"
            "Command (verify gate): npm audit --audit-level=high\n"
            "Run it from the repo root. If the audit reports high- or "
            "critical-severity vulnerabilities, upgrade the affected dependency to "
            "a fixed version — changing package.json / package-lock.json minimally "
            "and nothing else — and re-run until the audit exits 0. If a dependency "
            "cannot be fixed without a change you cannot verify, say so plainly and "
            "change nothing. Report the final audit summary line verbatim."
        ),
        verify_command="npm audit --audit-level=high",
        verify_timeout_seconds=120,
        deliberate_break="pin a version with a known advisory",
    ),
    Chore(
        name="uv-pip-check-compat",
        repo_kinds=("python",),
        prompt_template=(
            "Check this repository's Python dependency pins and fix what fails.\n\n"
            "Command (verify gate): uv pip check\n"
            "Run it from the repo root against the environment the repo pins "
            "(VIRTUAL_ENV must point at it). uv pip check validates that installed "
            "packages' dependency constraints are mutually satisfiable; it does NOT "
            "consult any advisory database. If it reports incompatibilities, fix "
            "the offending pins minimally, reinstall if needed, and re-run until it "
            "exits 0. If you cannot fix it, say so plainly and change nothing. "
            "Report the final check output verbatim."
        ),
        verify_command="uv pip check",
        verify_timeout_seconds=120,
        deliberate_break="install two packages with conflicting pins",
    ),
)

_BY_NAME = {c.name: c for c in CHORES}


def by_name(name: str) -> Chore:
    """The catalog row for ``name``, or raise with the available set."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise KeyError(
            f"no chore named {name!r}; catalog has {sorted(_BY_NAME)}"
        ) from None


def verify_timeout_for(name: str) -> int:
    """The calibrated timeout for ``name``, straight from the calibration record.

    Timeouts are set from measurement rather than guessed; the record is the
    measure. Raises ``CalibrationError`` for a chore that is not calibrated —
    such a chore must not ship, so there is intentionally no fallback.
    """
    record = _calibration_records().get(name)
    if record is None or not record.get("calibrated"):
        raise CalibrationError(
            f"chore {name!r} is not calibrated in {CALIBRATION_FILE} — "
            "it must not ship."
        )
    return int(record["timeout_chosen_seconds"])


def assert_calibration_matches() -> None:
    """Fail if any shipped chore's timeout disagrees with the calibrated record.

    This is the wire between catalog and measurement: the committed JSON is the
    only authority on how long a verify command may run, and a catalog row whose
    timeout was hand-edited without a re-calibration is caught here rather than
    shipped.
    """
    records = _calibration_records()
    for chore in CHORES:
        record = records.get(chore.name)
        if record is None:
            raise CalibrationError(
                f"chore {chore.name!r} has no calibration record — it must not ship."
            )
        if not record.get("calibrated"):
            raise CalibrationError(
                f"chore {chore.name!r} is not calibrated — it must not ship."
            )
        chosen = int(record["timeout_chosen_seconds"])
        if chosen != chore.verify_timeout_seconds:
            raise CalibrationError(
                f"catalog timeout for {chore.name!r} is "
                f"{chore.verify_timeout_seconds}s but calibration chose {chosen}s. "
                "Shipped timeouts must come from the record — re-run "
                "scripts/calibrate_verify.py and set the catalog from it."
            )