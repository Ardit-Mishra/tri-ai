"""Phase 2, criterion 5: the worker's own reach is provably narrow.

This does NOT prove a YOLO agent cannot push — it cannot, and the plan withdraws
that claim. What it proves is that Tri-AI's own execution path creates processes
in a small, *qualified* set of places, that git is reachable only through an
exact-form allowlist, and that `shell=True` appears in exactly one deliberately
exempt function.

The audit parses ASTs rather than grepping text, because a grep for "git push"
neither sees `subprocess.run(["git", "push"])` nor a subcommand assembled at
runtime — and it *does* match the prohibition text in `run_queue.py` that exists
to forbid the thing, so a text audit passes on a file that merely mentions it.

Two properties matter as much as the detection itself, both added after review
found them missing:

* **Identity is qualified.** A gateway is a ``(module, function)`` pair. A bare
  function name would let any other module define ``def git()`` and inherit the
  exemption.
* **Aliased imports are refused outright.** ``import subprocess as sp`` and
  ``from subprocess import run`` both defeat attribute-based detection, so the
  closure may not contain them at all. Rejecting the shape is stronger than
  trying to resolve it.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import executor  # noqa: E402

# The audited closure: every module on the execution path.
CLOSURE_MODULES = (
    "executor.py", "board.py", "worker.py", "ledger.py", "assign.py", "chores.py",
)

# Modules not yet written. Enumerated rather than filtered, so an unaudited hole
# is visible in source instead of hidden behind a silent skip. This must be
# empty when Phase 2 is complete; `test_pending_modules_are_declared` fails if a
# module appears here that has since been written.
PENDING_MODULES = frozenset({"worker.py", "ledger.py", "assign.py", "chores.py"})

# Process creation is confined to these (module, function) pairs.
#
# Only two: `git()` runs the allowlisted forms, and `spawn_contained()` is the
# single place a child is started — `run_agent` and `run_verify` both delegate
# to it, so they create nothing directly. That is narrower than the three the
# plan specified, and it removes the earlier five-gateway divergence: process
# containment is now ctypes against kernel32, not `taskkill`/`tasklist`
# subprocesses, so terminating a tree is no longer itself process creation.
PROCESS_GATEWAYS = frozenset({
    ("executor", "git"),
    ("executor", "spawn_contained"),
})

# Only this one may pass shell=True: the operator authored that command.
SHELL_EXEMPT = frozenset({("executor", "run_verify")})

PROCESS_ATTRS = frozenset({
    "run", "Popen", "call", "check_call", "check_output", "system", "popen",
    "spawn", "spawnl", "spawnle", "spawnv", "spawnve", "execv", "execve",
    "posix_spawn", "fork", "forkpty", "startfile",
})
PROCESS_MODULES = frozenset({"subprocess", "os"})


def _audited() -> list[Path]:
    """Closure modules present on disk."""
    return [SRC / n for n in CLOSURE_MODULES if (SRC / n).exists()]


def _owners(tree: ast.AST, module: str) -> dict[int, tuple[str, str]]:
    """Map every node to a lexical, rather than bare-name, function identity."""
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.owner: dict[int, tuple[str, str]] = {}
            self.scope: list[str] = []

        def visit(self, node):
            if self.scope:
                self.owner[id(node)] = (module, ".".join(self.scope))
            return super().visit(node)

        def _scoped(self, node, name: str):
            self.scope.append(name)
            for child in ast.iter_child_nodes(node):
                self.visit(child)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            self._scoped(node, node.name)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            self._scoped(node, node.name)

        def visit_Lambda(self, node):
            self._scoped(node, "<lambda>")

    visitor = Visitor()
    visitor.visit(tree)
    return visitor.owner


def _parsed():
    for path in _audited():
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class ClosureIsComplete(unittest.TestCase):
    def test_every_closure_module_is_audited_or_declared_pending(self):
        missing = [
            n for n in CLOSURE_MODULES
            if not (SRC / n).exists() and n not in PENDING_MODULES
        ]
        self.assertEqual(
            missing, [],
            f"closure modules absent and not declared pending: {missing}. "
            "A module on the execution path that the audit cannot see is a hole.",
        )

    def test_pending_modules_are_declared(self):
        # A module that now exists must be removed from PENDING, or it stays
        # excused while actually being audited — a stale exemption.
        stale = [n for n in PENDING_MODULES if (SRC / n).exists()]
        self.assertEqual(
            stale, [],
            f"written but still listed as pending: {stale} — remove from PENDING_MODULES",
        )

    def test_the_audit_covers_something(self):
        self.assertTrue(_audited(), "no closure module was audited at all")

    def test_local_execution_imports_are_in_the_closure(self):
        missing = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [item.name.split(".", 1)[0] for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module.split(".", 1)[0]]
                else:
                    continue
                for module in modules:
                    filename = f"{module}.py"
                    if (SRC / filename).exists() and filename not in CLOSURE_MODULES:
                        missing.append(f"{path.name} imports unaudited {filename}")
        self.assertEqual(missing, [], "local execution imports outside closure: " + repr(missing))


class ImportsCannotHideProcessCalls(unittest.TestCase):
    """Aliased imports defeat attribute detection, so the shape is refused."""

    def test_no_aliased_import_of_a_process_module(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name in PROCESS_MODULES and a.asname:
                            bad.append(f"{p.name}:{n.lineno} import {a.name} as {a.asname}")
        self.assertEqual(bad, [], f"aliased process-module import: {bad}")

    def test_no_from_import_of_a_process_function(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if isinstance(n, ast.ImportFrom) and n.module in PROCESS_MODULES:
                    for a in n.names:
                        if a.name in PROCESS_ATTRS:
                            bad.append(f"{p.name}:{n.lineno} from {n.module} import {a.name}")
        self.assertEqual(bad, [], f"from-import of a process function: {bad}")

    def test_no_star_imports(self):
        bad = [
            f"{p.name}:{n.lineno}"
            for p, t in _parsed()
            for n in ast.walk(t)
            if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
        ]
        self.assertEqual(bad, [], f"star import hides what is in scope: {bad}")

    def test_no_dynamic_import(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Name) and f.id == "__import__":
                        bad.append(f"{p.name}:{n.lineno} __import__")
                    if (
                        isinstance(f, ast.Attribute)
                        and isinstance(f.value, ast.Name)
                        and f.value.id == "importlib"
                    ):
                        bad.append(f"{p.name}:{n.lineno} importlib.{f.attr}")
        self.assertEqual(bad, [], f"dynamic import defeats static audit: {bad}")

    def test_importlib_cannot_be_aliased_or_imported_by_name(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if isinstance(n, ast.Import):
                    for a in n.names:
                        if a.name == "importlib" and a.asname:
                            bad.append(f"{p.name}:{n.lineno} import importlib as {a.asname}")
                if isinstance(n, ast.ImportFrom) and n.module == "importlib":
                    for a in n.names:
                        if a.name in ("*", "import_module"):
                            bad.append(f"{p.name}:{n.lineno} from importlib import {a.name}")
        self.assertEqual(bad, [], f"importlib alias defeats static audit: {bad}")


class ProcessCreationIsConfined(unittest.TestCase):
    def test_every_process_call_is_inside_a_qualified_gateway(self):
        offenders = []
        for path, tree in _parsed():
            module = path.stem
            owner = _owners(tree, module)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if not (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id in PROCESS_MODULES
                    and f.attr in PROCESS_ATTRS
                ):
                    continue
                where = owner.get(id(node))
                if where not in PROCESS_GATEWAYS:
                    offenders.append(
                        f"{path.name}:{node.lineno} {f.value.id}.{f.attr} in {where}"
                    )
        self.assertEqual(
            offenders, [],
            "process creation outside a qualified gateway:\n  " + "\n  ".join(offenders),
        )

    def test_shell_true_appears_only_in_the_exempt_gateway(self):
        offenders = []
        for path, tree in _parsed():
            owner = _owners(tree, path.stem)
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or node.arg != "shell":
                    continue
                v = node.value
                if isinstance(v, ast.Constant) and v.value is True:
                    where = owner.get(id(node))
                    if where not in SHELL_EXEMPT:
                        offenders.append(f"{path.name}:{v.lineno} in {where}")
        self.assertEqual(
            offenders, [], "shell=True outside run_verify:\n  " + "\n  ".join(offenders)
        )


class EvasionShapesAreRejected(unittest.TestCase):
    def test_no_getattr_on_a_process_module(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr"
                    and n.args
                    and isinstance(n.args[0], ast.Name)
                    and n.args[0].id in PROCESS_MODULES
                ):
                    bad.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bad, [], f"getattr dispatch on a process module: {bad}")

    def test_no_process_function_aliased_to_a_name(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if not isinstance(n, ast.Assign):
                    continue
                v = n.value
                if (
                    isinstance(v, ast.Attribute)
                    and isinstance(v.value, ast.Name)
                    and v.value.id in PROCESS_MODULES
                    and v.attr in PROCESS_ATTRS
                ):
                    bad.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bad, [], f"process function aliased: {bad}")

    def test_no_partial_over_a_process_function(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if not isinstance(n, ast.Call):
                    continue
                f = n.func
                is_partial = (isinstance(f, ast.Name) and f.id == "partial") or (
                    isinstance(f, ast.Attribute) and f.attr == "partial"
                )
                if is_partial and n.args:
                    a = n.args[0]
                    if (
                        isinstance(a, ast.Attribute)
                        and isinstance(a.value, ast.Name)
                        and a.value.id in PROCESS_MODULES
                    ):
                        bad.append(f"{p.name}:{n.lineno}")
        self.assertEqual(bad, [], f"partial over a process function: {bad}")

    def test_no_dict_dispatch_of_a_process_function(self):
        bad = []
        for p, t in _parsed():
            for n in ast.walk(t):
                if not isinstance(n, ast.Dict):
                    continue
                for value in n.values:
                    if (
                        isinstance(value, ast.Attribute)
                        and isinstance(value.value, ast.Name)
                        and value.value.id in PROCESS_MODULES
                        and value.attr in PROCESS_ATTRS
                    ):
                        bad.append(f"{p.name}:{value.lineno}")
        self.assertEqual(bad, [], f"dict-dispatched process function: {bad}")


class TheAuditItselfCanFail(unittest.TestCase):
    """A gate that cannot fail is not a gate — criterion 3, applied to the audit.

    Every check runs against deliberately bad source and must report it. Review
    found the earlier version exercised only the unaliased happy path.
    """

    def _offenders(self, source: str, module: str = "probe") -> list[str]:
        tree = ast.parse(source)
        owner = _owners(tree, module)
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                if (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id in PROCESS_MODULES
                    and f.attr in PROCESS_ATTRS
                    and owner.get(id(n)) not in PROCESS_GATEWAYS
                ):
                    out.append(f"{f.value.id}.{f.attr}")
        return out

    def _aliased_imports(self, source: str) -> list[str]:
        tree = ast.parse(source)
        out = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                out += [
                    f"{a.name} as {a.asname}"
                    for a in n.names
                    if a.name in PROCESS_MODULES and a.asname
                ]
            if isinstance(n, ast.ImportFrom) and n.module in PROCESS_MODULES:
                out += [a.name for a in n.names if a.name in PROCESS_ATTRS]
            if isinstance(n, ast.Import):
                out += [
                    f"importlib as {a.asname}"
                    for a in n.names
                    if a.name == "importlib" and a.asname
                ]
            if isinstance(n, ast.ImportFrom) and n.module == "importlib":
                out += [a.name for a in n.names if a.name in ("*", "import_module")]
        return out

    def test_detects_a_push_hidden_in_an_argv_list(self):
        bad = "import subprocess\ndef sneaky():\n    subprocess.run(['git', 'push'])\n"
        self.assertEqual(self._offenders(bad), ["subprocess.run"])

    def test_detects_a_runtime_assembled_command(self):
        bad = "import subprocess\ndef sneaky(verb):\n    subprocess.run(['git', verb])\n"
        self.assertEqual(self._offenders(bad), ["subprocess.run"])

    def test_detects_an_aliased_module_import(self):
        self.assertEqual(
            self._aliased_imports("import subprocess as sp\nsp.run(['git','push'])\n"),
            ["subprocess as sp"],
        )

    def test_detects_a_from_import_of_a_process_function(self):
        self.assertEqual(
            self._aliased_imports("from subprocess import run\nrun(['git','push'])\n"),
            ["run"],
        )

    def test_a_gateway_name_in_another_module_is_not_exempt(self):
        # The qualified-identity property: `def git()` elsewhere must not
        # inherit executor.git's exemption.
        src = "import subprocess\ndef git(argv):\n    subprocess.run(['git', *argv])\n"
        self.assertEqual(self._offenders(src, module="worker"), ["subprocess.run"])
        self.assertEqual(self._offenders(src, module="executor"), [])

    def test_a_nested_gateway_name_is_not_exempt(self):
        src = (
            "import subprocess\n"
            "def outer():\n"
            "    def spawn_contained():\n"
            "        subprocess.Popen(['git', 'push'])\n"
        )
        self.assertEqual(self._offenders(src, module="executor"), ["subprocess.Popen"])

    def test_dict_dispatch_is_detected(self):
        src = "import subprocess\ncalls = {'go': subprocess.run}\ncalls['go'](['git', 'push'])\n"
        tree = ast.parse(src)
        hits = [
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for node in node.values
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "subprocess"
        ]
        self.assertEqual(hits, ["run"])

    def test_aliased_importlib_is_detected(self):
        self.assertEqual(
            self._aliased_imports("import importlib as loader\nloader.import_module('subprocess')\n"),
            ["importlib as loader"],
        )


class GitAllowlist(unittest.TestCase):
    def test_the_five_needed_forms_are_allowed(self):
        for form in (
            ["status", "--porcelain"],
            ["branch", "--show-current"],
            ["rev-parse", "--show-toplevel"],
            ["stash", "list"],
            ["stash", "push", "--include-untracked", "--message", "triai-revert:t:1"],
        ):
            self.assertTrue(executor._git_form_allowed(form), form)

    def test_destructive_and_networked_forms_are_refused(self):
        for form in (
            ["push"], ["push", "origin", "main"],
            ["merge", "main"], ["rebase", "main"],
            ["remote", "add", "origin", "http://example.invalid/x.git"],
            ["config", "credential.helper", "store"],
            ["credential", "fill"],
            ["clean", "-fd"],
            ["checkout", "--", "."],
            ["stash", "clear"], ["stash", "drop"],
            ["branch", "-D", "main"],
            ["status"], ["status", "--porcelain", "-uall"],
        ):
            self.assertFalse(executor._git_form_allowed(form), form)

    def test_a_refused_form_raises_before_spawning(self):
        with self.assertRaises(executor.DisallowedGitCommand):
            executor.git(["push", "origin", "main"], cwd=".")


if __name__ == "__main__":
    unittest.main()
