"""Detect a workspace's stack and give each node a gate that can actually fail.

`decomposer` marks every node `verify_generic: true` because a stack-blind
command only proves the workspace changed. A Django node should be gated on
Django's own checks, and the gate has to come from repository evidence rather
than from what the request claims to be.

Two consumers, deliberately separate: the `-verification` skill is an
instruction document that teaches the *agent* how to verify, while the command
here is what the *gate* runs. Conflating them would mean parsing prose into a
shell command, which is the kind of cleverness that fails silently.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import stack_profile  # noqa: E402


class _Workspace(unittest.TestCase):
    """A throwaway directory to plant evidence in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def plant(self, *relpaths: str, body: str = "") -> None:
        for rel in relpaths:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")


class DetectionTest(_Workspace):
    def test_an_empty_directory_is_unknown_not_a_guess(self):
        self.assertEqual(stack_profile.detect(self.root).stack, "unknown")

    def test_pyproject_alone_is_plain_python(self):
        self.plant("pyproject.toml", body="[project]\nname='x'\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "python")

    def test_manage_py_and_a_django_dependency_beat_plain_python(self):
        """A Django repo is also a Python repo; the specific stack must win."""
        self.plant("manage.py")
        self.plant("requirements.txt", body="Django==5.0\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "django")

    def test_manage_py_without_django_is_not_django(self):
        """manage.py alone is weak evidence - plenty of projects have one."""
        self.plant("manage.py")
        self.plant("pyproject.toml", body="[project]\nname='x'\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "python")

    def test_artisan_and_composer_is_laravel(self):
        self.plant("artisan")
        self.plant("composer.json", body='{"require":{"laravel/framework":"^11"}}')
        self.assertEqual(stack_profile.detect(self.root).stack, "laravel")

    def test_package_json_alone_is_node(self):
        self.plant("package.json", body='{"name":"x","scripts":{"test":"vitest"}}')
        self.assertEqual(stack_profile.detect(self.root).stack, "node")

    def test_cargo_is_rust_and_go_mod_is_go(self):
        self.plant("Cargo.toml", body="[package]\nname='x'\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "rust")
        (self.root / "Cargo.toml").unlink()
        self.plant("go.mod", body="module x\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "go")

    def test_a_repo_that_ships_its_own_runner_is_gated_on_that_runner(self):
        """Tri-AI itself: stdlib-only, no manifest, but tests/run.py is the gate."""
        self.plant("tests/run.py", body="import unittest\n")
        self.plant("src/thing.py")
        profile = stack_profile.detect(self.root)
        self.assertEqual(profile.stack, "python-runner")
        self.assertIn("tests/run.py", profile.verify_command)

    def test_a_manifest_beats_a_bare_runner(self):
        """pytest is the better gate when the project declares itself."""
        self.plant("tests/run.py", body="import unittest\n")
        self.plant("pyproject.toml", body="[project]\nname='x'\n")
        self.assertEqual(stack_profile.detect(self.root).stack, "python")

    def test_detection_records_the_evidence_it_used(self):
        """A wrong gate must be traceable to the file that caused it."""
        self.plant("go.mod", body="module x\n")
        self.assertIn("go.mod", stack_profile.detect(self.root).evidence)


class VerifyCommandTest(_Workspace):
    def test_a_known_stack_gets_its_own_runner_and_is_not_generic(self):
        self.plant("go.mod", body="module x\n")
        profile = stack_profile.detect(self.root)
        self.assertIn("go test", profile.verify_command)
        self.assertFalse(profile.generic)

    def test_an_unknown_stack_falls_back_and_says_so(self):
        """The fallback must be labelled, or a blind gate looks considered."""
        profile = stack_profile.detect(self.root)
        self.assertTrue(profile.generic)
        self.assertTrue(profile.verify_command.strip())

    def test_django_is_gated_on_missing_migrations_not_only_tests(self):
        """A model change with no migration passes the tests and breaks deploy."""
        self.plant("manage.py")
        self.plant("requirements.txt", body="Django==5.0\n")
        self.assertIn("makemigrations --check", stack_profile.detect(self.root).verify_command)

    def test_every_known_stack_defines_a_non_empty_command(self):
        for name, spec in stack_profile.STACKS.items():
            self.assertTrue(spec.verify_command.strip(), name)


class SkillReferenceTest(_Workspace):
    def test_a_stack_with_a_verification_skill_names_it(self):
        self.plant("manage.py")
        self.plant("requirements.txt", body="Django==5.0\n")
        self.assertIn("django-verification", stack_profile.detect(self.root).skills)

    def test_named_skills_exist_on_this_machine(self):
        """A dangling skill reference resolves to nothing and teaches nobody.

        This asserts a fact about *this machine's* skill estate, not about the
        code, so it skips where there is no estate to check. An earlier version
        called `.iterdir()` unconditionally and failed on the desktop, which has
        no `~/.claude/skills` at all - a test that fails for having nothing to
        test is reporting the wrong thing.
        """
        root = Path.home() / ".claude" / "skills"
        if not root.is_dir():
            self.skipTest(f"no skill estate at {root}")
        installed = {p.name for p in root.iterdir() if p.is_dir()}
        missing = [
            (name, skill)
            for name, spec in stack_profile.STACKS.items()
            for skill in spec.skills
            if skill not in installed
        ]
        self.assertEqual(missing, [])


class ApplyToGraphTest(_Workspace):
    def test_nodes_get_the_detected_command_and_lose_the_generic_flag(self):
        self.plant("go.mod", body="module x\n")
        graph = {"nodes": [
            {"node_key": "n01", "verify_command": "placeholder", "verify_generic": True},
            {"node_key": "n02", "verify_command": "placeholder", "verify_generic": True},
        ]}
        stack_profile.apply(graph, self.root)
        for node in graph["nodes"]:
            self.assertIn("go test", node["verify_command"])
            self.assertFalse(node["verify_generic"])
            self.assertEqual(node["verify_stack"], "go")

    def test_an_unknown_workspace_leaves_the_generic_flag_set(self):
        graph = {"nodes": [{"node_key": "n01", "verify_command": "x",
                            "verify_generic": True}]}
        stack_profile.apply(graph, self.root)
        self.assertTrue(graph["nodes"][0]["verify_generic"])
        self.assertEqual(graph["nodes"][0]["verify_stack"], "unknown")


if __name__ == "__main__":
    unittest.main()
