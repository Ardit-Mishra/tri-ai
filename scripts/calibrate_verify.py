#!/usr/bin/env python3
"""Prove every shipped verify command can fail (VERIFY-05, criterion 3).

A self-contained harness that for each catalog chore in ``src/chores.py``
answers two questions, and only those two:

  1. Does the verify command RUN AT ALL on a clean fixture?   (expect exit 0)
  2. Does it exit NON-ZERO on its deliberate break?           (expect != 0)

It never touches a real repository: every fixture is built in the OS temp
dir (``tempfile``), broken deliberately, run, and discarded. This repository
carries no hostnames, paths or credentials, and calibration must not be the
thing that starts carrying them.

Every result is recorded truthfully in ``docs/verify-calibration.json``. The
rule is the plan's: if a command cannot be calibrated — the tool is missing, it
cannot fail on its break in this environment, or it hangs within a bounded
window — the chore is DEFERRED with the reason recorded. A recorded deferral
with a reason is the process working; a green that was never tested is a fake.
This is exactly how the plan deferred Python advisory auditing.

Environment notes, recorded where relevant:

* ``npm audit`` consults the npm registry; this machine's registry was reachable
  during calibration and the fixtures install real pinned packages.
* ``tsc --noEmit`` ships on the assumption that ``tsc`` resolves where the
  worker runs (a global install, or a repo/operator setup whose PATH includes
  ``node_modules/.bin``). The harness makes it resolvable the same way: it
  installs TypeScript into the fixture's ``node_modules`` and prepends that
  ``.bin`` to PATH for the runs. ``tool_present`` still records the honest
  ``shutil.which`` result.
* ``uv pip check`` needs ``VIRTUAL_ENV`` pointing at the environment to check;
  that is operator setup in production and harness setup here.

TOOL DETECTION
--------------
Each chore's primary command token is probed with ``shutil.which`` and the
result recorded as ``tool_present``. A missing tool is not by itself a deferral
(see the tsc note above); a command that cannot actually run or cannot fail on
its break in the fixture IS.

Usage:
    python scripts/calibrate_verify.py
Writes docs/verify-calibration.json and then runs
``chores.assert_calibration_matches()`` to prove the shipped catalog agrees
with the fresh record.

Nothing here imports the Hermes kernel; this is pure Python + subprocesses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import chores  # noqa: E402

OUT = REPO_ROOT / "docs" / "verify-calibration.json"

# The bounded window for a single verify run. A command that does not return
# within this is deferred as a hang — that is the pip-audit lesson.
BOUNDED_WINDOW = float(os.environ.get("TRIAI_CALIBRATION_WINDOW", "120"))


def choose_timeout(observed_seconds: float) -> int:
    """Turn an observed green runtime into a shipped timeout.

    Measurement sets the value; the fixtures are deliberately minimal, so a real
    repo's suite runs longer — hence the 20x headroom and the 120s floor. Capped
    so a network-bound fixture cannot inflate a v1 gate into an absurdity.
    """
    return max(120, min(3600, round(observed_seconds * 20)))


@dataclass
class RunResult:
    exit_code: Optional[int]   # None for timeout / spawn failure
    timed_out: bool
    output: str
    seconds: float
    spawn_error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code is not None and not self.timed_out


def _run(cmd: str, cwd: Path, *, env: Optional[dict] = None) -> RunResult:
    """Run ``cmd`` exactly as the executor does — a shell string at a cwd."""
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            env=env if env is not None else dict(os.environ),
            capture_output=True,
            text=True,
            timeout=BOUNDED_WINDOW,
        )
        return RunResult(
            proc.returncode,
            False,
            (proc.stdout or "") + (proc.stderr or ""),
            time.time() - started,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            None, True, f"[calibration window {BOUNDED_WINDOW:g}s exceeded]",
            time.time() - started,
        )
    except Exception as exc:  # noqa: BLE001 — a failed spawn must not kill the run
        return RunResult(None, False, f"{type(exc).__name__}: {exc}",
                         time.time() - started, spawn_error=str(exc))


@dataclass
class Calibration:
    name: str
    verify_command: str
    tool_present: bool
    runs_at_all: bool
    runs_at_all_exit: Optional[int]
    breaks_on_deliberate: bool
    breaks_on_deliberate_exit: Optional[int]
    observed_runtime_seconds: float
    timeout_chosen_seconds: int
    calibrated: bool
    deferral_reason: Optional[str]
    note: str = ""

    def to_record(self) -> dict:
        return {
            "name": self.name,
            "verify_command": self.verify_command,
            "tool_present": self.tool_present,
            "runs_at_all": self.runs_at_all,
            "runs_at_all_exit": self.runs_at_all_exit,
            "breaks_on_deliberate": self.breaks_on_deliberate,
            "breaks_on_deliberate_exit": self.breaks_on_deliberate_exit,
            "observed_runtime_seconds": round(self.observed_runtime_seconds, 3),
            "timeout_chosen_seconds": self.timeout_chosen_seconds,
            "calibrated": self.calibrated,
            "deferral_reason": self.deferral_reason,
            "note": self.note or None,
        }


def _deferred(name: str, cmd: str, tool_present: bool, reason: str,
              note: str = "") -> Calibration:
    return Calibration(name, cmd, tool_present, False, None, False, None,
                       0.0, 0, False, reason, note)


def _npm_install(cwd: Path, *, extra: str = "") -> Optional[RunResult]:
    """Install the fixture's deps. None if npm is simply absent."""
    if shutil.which("npm") is None:
        return None
    return _run(f"npm install --no-audit --no-fund --loglevel=error {extra}",
                cwd=cwd)


# ---------------------------------------------------------------------------
# Fixture builders + calibrators. One per catalog chore.
# ---------------------------------------------------------------------------

def calibrate_pytest_suite(chore: chores.Chore, work: Path) -> Calibration:
    """A one-test pytest project. Break: assert a false statement."""
    tool = shutil.which("python")
    proj = work / "pytest-fixture"
    proj.mkdir()
    (proj / "test_suite.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n", encoding="utf-8",
    )
    green = _run(chore.verify_command, cwd=proj)
    if green.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"does not return within {BOUNDED_WINDOW:g}s on a clean fixture")
    if green.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            f"exits {green.exit_code} on a clean fixture (pytest importable?): "
            + (green.output.strip()[:200] or "no output"),
        )
    (proj / "test_suite.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n\n"
        "def test_broken():\n    assert 1 + 1 == 3\n",
        encoding="utf-8",
    )
    broken = _run(chore.verify_command, cwd=proj)
    if broken.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"hangs within {BOUNDED_WINDOW:g}s on its deliberate break")
    if broken.exit_code == 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "exits 0 on its deliberate break — the gate cannot fail")
    return Calibration(chore.name, chore.verify_command, tool is not None,
                       True, green.exit_code, True, broken.exit_code,
                       green.seconds, choose_timeout(green.seconds), True, None)


def calibrate_tsc_typecheck(chore: chores.Chore, work: Path) -> Calibration:
    """A minimal strict TS project. Break: assign a string to an int-typed name.

    ``tsc`` is made resolvable the way a repo-with-typescript setup resolves it:
    TypeScript is installed into the fixture's node_modules and its ``.bin`` is
    prepended to PATH for the verify runs. ``tool_present`` still records the
    bare ``shutil.which`` result, and the note says so.
    """
    tool = shutil.which("tsc")
    proj = work / "tsc-fixture"
    proj.mkdir()
    (proj / "package.json").write_text(
        json.dumps({"name": "tsc-fixture", "version": "1.0.0", "private": True,
                    "devDependencies": {"typescript": "^5.5.0"}}),
        encoding="utf-8",
    )
    (proj / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"target": "ES2020", "module": "commonjs",
                                        "strict": True, "noEmit": True},
                    "include": ["src"]}),
        encoding="utf-8",
    )
    (proj / "src").mkdir()
    (proj / "src" / "index.ts").write_text(
        'const n: number = 1;\nconsole.log(n);\n', encoding="utf-8",
    )
    installed = _npm_install(proj)
    if installed is None:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "npm not on PATH; cannot install typescript into fixture")
    if installed.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            "npm install of typescript into the fixture failed (offline?): "
            + (installed.output.strip()[:200] or "no output"),
        )
    bin_dir = str(proj / "node_modules" / ".bin")
    env = dict(os.environ)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")

    green = _run(chore.verify_command, cwd=proj, env=env)
    if green.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"does not return within {BOUNDED_WINDOW:g}s on a clean fixture")
    if green.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            f"exits {green.exit_code} on a clean fixture: "
            + (green.output.strip()[:200] or "no output"),
        )
    (proj / "src" / "index.ts").write_text(
        'const n: number = "oops";\n', encoding="utf-8",
    )
    broken = _run(chore.verify_command, cwd=proj, env=env)
    if broken.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"hangs within {BOUNDED_WINDOW:g}s on its deliberate break")
    if broken.exit_code == 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "exits 0 on a type error — the gate cannot fail")
    return Calibration(
        chore.name, chore.verify_command, tool is not None,
        True, green.exit_code, True, broken.exit_code,
        green.seconds, choose_timeout(green.seconds), True, None,
        note="no global tsc: typescript installed into the fixture via npm and "
             "its node_modules/.bin prepended to PATH, matching a repo/operator "
             "setup where tsc resolves",
    )


def calibrate_build(chore: chores.Chore, work: Path) -> Calibration:
    """A one-entry npm build. Break: a syntax error in the entry module."""
    tool = shutil.which("npm")
    proj = work / "build-fixture"
    proj.mkdir()
    (proj / "package.json").write_text(
        json.dumps({"name": "build-fixture", "version": "1.0.0", "private": True,
                    "scripts": {"build": "node src/index.js"}}),
        encoding="utf-8",
    )
    (proj / "src").mkdir()
    (proj / "src" / "index.js").write_text(
        'const msg = "built ok";\nconsole.log(msg);\n', encoding="utf-8",
    )
    green = _run(chore.verify_command, cwd=proj)
    if green.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"does not return within {BOUNDED_WINDOW:g}s on a clean fixture")
    if green.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            f"exits {green.exit_code} on a clean build: "
            + (green.output.strip()[:200] or "no output"),
        )
    (proj / "src" / "index.js").write_text(
        'const msg = "built ok";\nthis is not valid javascript &&(\n',
        encoding="utf-8",
    )
    broken = _run(chore.verify_command, cwd=proj)
    if broken.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"hangs within {BOUNDED_WINDOW:g}s on its deliberate break")
    if broken.exit_code == 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "exits 0 on a syntax error — the gate cannot fail")
    return Calibration(chore.name, chore.verify_command, tool is not None,
                       True, green.exit_code, True, broken.exit_code,
                       green.seconds, choose_timeout(green.seconds), True, None)


def calibrate_npm_audit_advisory(chore: chores.Chore, work: Path) -> Calibration:
    """npm audit needs to be able to PASS as well as fail.

    Green fixture: minimist (the advisory-bearing version's fixed successor),
    expect exit 0. Break: pin minimist 0.2.0, the version with the known
    prototype-pollution advisory (critical), expect non-zero. npm audit consults
    the registry, so a clean depset is the honest "runs at all" and an offline
    registry is recorded as a deferral, not faked through.
    """
    tool = shutil.which("npm")

    green_proj = work / "audit-green"
    green_proj.mkdir()
    (green_proj / "package.json").write_text(
        json.dumps({"name": "audit-green", "version": "1.0.0", "private": True,
                    "dependencies": {"minimist": "1.2.8"}}),
        encoding="utf-8",
    )
    inst = _npm_install(green_proj)
    if inst is not None and inst.exit_code != 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "npm install of the clean fixture failed (offline?): "
                         + (inst.output.strip()[:200] or "no output"))
    green = _run(chore.verify_command, cwd=green_proj)
    if green.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"does not return within {BOUNDED_WINDOW:g}s on a clean depset")
    if green.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            f"exits {green.exit_code} on a clean depset (registry unreachable?): "
            + (green.output.strip()[:200] or "no output"),
        )

    broken_proj = work / "audit-broken"
    broken_proj.mkdir()
    (broken_proj / "package.json").write_text(
        json.dumps({"name": "audit-broken", "version": "1.0.0", "private": True,
                    "dependencies": {"minimist": "0.2.0"}}),
        encoding="utf-8",
    )
    inst = _npm_install(broken_proj)
    if inst is not None and inst.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            "npm install of the broken fixture failed (offline?): "
            + (inst.output.strip()[:200] or "no output"),
        )
    broken = _run(chore.verify_command, cwd=broken_proj)
    if broken.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"hangs within {BOUNDED_WINDOW:g}s on its deliberate break")
    if broken.exit_code == 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "exits 0 on a known critical advisory — the gate cannot fail")
    return Calibration(
        chore.name, chore.verify_command, tool is not None,
        True, green.exit_code, True, broken.exit_code,
        green.seconds, choose_timeout(green.seconds), True, None,
        note="npm audit consulted the npm registry (reachable during "
             "calibration)",
    )


def calibrate_uv_pip_check(chore: chores.Chore, work: Path) -> Calibration:
    """uv pip check with a fresh venv. Break: two conflicting pins.

    Green: empty venv, exit 0. Break: install urllib3 1.26.18 and requests 2.24.0
    with --no-deps so both are present while requests' constraint
    (urllib3>=1.21.1,!=1.25.0,!=1.25.1,<1.26) is violated. uv pip check reports
    the incompatibility and exits non-zero. Needs VIRTUAL_ENV, as in production.
    """
    tool = shutil.which("uv")
    proj = work / "uv-fixture"
    proj.mkdir()
    env = dict(os.environ)
    env["VIRTUAL_ENV"] = str(proj / "venv")
    made = _run("uv venv venv", cwd=proj)
    if made.exit_code != 0:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         "uv venv failed: " + (made.output.strip()[:200] or "no output"))
    green = _run(chore.verify_command, cwd=proj, env=env)
    if green.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"does not return within {BOUNDED_WINDOW:g}s on an empty venv")
    if green.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            f"exits {green.exit_code} on an empty venv: "
            + (green.output.strip()[:200] or "no output"),
        )
    first = _run("uv pip install --no-deps urllib3==1.26.18", cwd=proj, env=env)
    second = _run("uv pip install --no-deps requests==2.24.0", cwd=proj, env=env)
    if first.exit_code != 0 or second.exit_code != 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            "could not install the conflicting pins (offline?): "
            + ((first.output or second.output).strip()[:200] or "no output"),
        )
    broken = _run(chore.verify_command, cwd=proj, env=env)
    if broken.timed_out:
        return _deferred(chore.name, chore.verify_command, tool is not None,
                         f"hangs within {BOUNDED_WINDOW:g}s on its deliberate break")
    if broken.exit_code == 0:
        return _deferred(
            chore.name, chore.verify_command, tool is not None,
            "exits 0 on conflicting pins — VIRTUAL_ENV not honoured, gate cannot fail",
        )
    return Calibration(
        chore.name, chore.verify_command, tool is not None,
        True, green.exit_code, True, broken.exit_code,
        green.seconds, choose_timeout(green.seconds), True, None,
        note="uv pip check needs VIRTUAL_ENV pointing at the environment; set "
             "here as an operator would",
    )


CALIBRATORS: dict[str, Callable[[chores.Chore, Path], Calibration]] = {
    "pytest-suite": calibrate_pytest_suite,
    "tsc-typecheck": calibrate_tsc_typecheck,
    "build": calibrate_build,
    "npm-audit-advisory": calibrate_npm_audit_advisory,
    "uv-pip-check-compat": calibrate_uv_pip_check,
}


def calibrate_one(chore: chores.Chore, tmp_root: Path) -> Calibration:
    """Run the calibrator for one catalog chore under a fresh temp subtree."""
    calibrator = CALIBRATORS.get(chore.name)
    if calibrator is None:
        return _deferred(
            chore.name, chore.verify_command, False,
            "no calibrator defined for this catalog row; add one before shipping",
        )
    work = tmp_root / chore.name
    work.mkdir(parents=True, exist_ok=True)
    return calibrator(chore, work)


def main(argv: Optional[list[str]] = None) -> int:
    """Calibrate every shipped chore and write docs/verify-calibration.json."""
    del argv
    records: dict[str, dict] = {}
    calibrated = 0
    with tempfile.TemporaryDirectory(prefix="triai-calibration-") as tmp:
        tmp_root = Path(tmp)
        print(f"calibrating under {tmp_root} (temp — no real repo is touched)")
        for chore in chores.CHORES:
            result = calibrate_one(chore, tmp_root)
            records[chore.name] = result.to_record()
            calibrated += 1 if result.calibrated else 0
            verdict = "CALIBRATED" if result.calibrated else "DEFERRED"
            print(f"  {chore.name:<22} {verdict:<9} exit_green="
                  f"{result.runs_at_all_exit} exit_broken="
                  f"{result.breaks_on_deliberate_exit} "
                  f"runtime={result.observed_runtime_seconds:.2f}s "
                  f"timeout={result.timeout_chosen_seconds}s")
            if result.deferral_reason:
                print(f"      deferral: {result.deferral_reason}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "interpreter": sys.executable,
        "calibration_window_seconds": BOUNDED_WINDOW,
        "timeout_policy": "timeout_chosen_seconds = max(round(observed_runtime_seconds * 20), 120), "
                          "capped at 3600. Fixtures are deliberately minimal so "
                          "the floor decides for fast ones; a real repo runs longer.",
        "shipped_chore_count": calibrated,
        "chores": records,
    }
    OUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # Prove the shipped catalog agrees with what was just measured.
    chores.assert_calibration_matches()

    print(f"\nwrote {OUT}")
    print(f"calibrated={calibrated}/{len(chores.CHORES)} deferred="
          f"{len(chores.CHORES) - calibrated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())