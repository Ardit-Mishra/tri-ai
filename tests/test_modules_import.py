"""Every module has to load under the interpreter the suite actually runs on.

This exists because eight test files were reported as passing-by-absence for an
unknown length of time. `tests/run.ps1` runs under the Hermes venv's Python
3.11, and `src/telegram_control.py` carried one f-string with a unicode escape
inside its expression - legal from 3.12, a SyntaxError before it. Every module
that imported it failed to load, and unittest reported each as a single
`unittest.loader._FailedTest` error rather than as the ~150 tests that did not
run. The suite said "463 tests, 8 errors" while the entire Telegram surface -
intake, attachments, the daemon, the transport, the revision loop - was being
checked by nothing at all.

A loader error is not a failing test. It is the absence of tests wearing one
test's clothes, and on a board that reports its own health that is the worst
shape a defect can take.

So: import every module in src/, by name, under whatever interpreter is running
the suite. It is a shallow check and it is meant to be - the depth belongs in
the other files, and none of them run if this one does not pass.
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# Modules with a side effect on import, or that need an environment the suite
# does not provide. Enumerated rather than pattern-matched, so adding one is a
# deliberate act that shows up in review.
NOT_IMPORTED: frozenset[str] = frozenset()


def _module_names() -> list[str]:
    names = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        relative = path.relative_to(SRC).with_suffix("")
        names.append(".".join(relative.parts))
    return names


class EveryModuleLoads(unittest.TestCase):
    def test_src_is_not_empty(self) -> None:
        # A glob that silently matches nothing would make this whole file pass
        # while proving nothing - the exact failure it was written to catch.
        self.assertGreater(len(_module_names()), 20)

    def test_every_module_imports(self) -> None:
        failures: list[str] = []
        for name in _module_names():
            if name in NOT_IMPORTED:
                continue
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - the point is what broke
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(
            failures, [],
            "modules that do not load under "
            f"Python {sys.version_info.major}.{sys.version_info.minor}:\n  "
            + "\n  ".join(failures),
        )

    def test_the_telegram_surface_specifically_loads(self) -> None:
        # Named explicitly because this is the surface the user reaches the
        # system through, and the one that was silently untested.
        for name in ("telegram_control", "attachments", "intake_preflight",
                     "completion_report", "interfaces.telegram_daemon"):
            with self.subTest(module=name):
                importlib.import_module(name)


class EveryTestFileLoads(unittest.TestCase):
    """The suite's own files, for the same reason."""

    def test_no_test_module_fails_to_import(self) -> None:
        here = Path(__file__).resolve().parent
        sys.path.insert(0, str(here))
        failures: list[str] = []
        for path in sorted(here.glob("test_*.py")):
            if path.name == Path(__file__).name:
                continue
            try:
                importlib.import_module(path.stem)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "test modules that do not load:\n  "
                         + "\n  ".join(failures))


if __name__ == "__main__":
    unittest.main()
