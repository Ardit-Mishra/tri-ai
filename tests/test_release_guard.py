"""A private branch must not be pushable to the public remote.

There are now two repositories: a public demo and a private repository that
holds the real history, the Cortex indexes and the operator's notes. Keeping
them apart cannot rest on remembering which is which - the branch holding the
Cortex is currently named `public-main`, which is exactly the kind of detail
that gets acted on at speed and regretted.

So the separation is declared in the tree and enforced at the only moment it
can actually go wrong: the push.

`RELEASE_SCOPE` states who a branch's content is for. The guard refuses a
private-scope push to any remote not known to be private, and it **fails
closed** - an unrecognised remote is treated as public, and a machine that
has not declared its private remote can push nothing private anywhere. An
unconfigured setup must never be a permissive one; that rule already caught
a real leak in the session boundary.

The private remote is read from local git config rather than a tracked file,
so the public repository never names it.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import release_guard  # noqa: E402


PUBLIC = "https://github.com/Ardit-Mishra/tri-ai.git"
PRIVATE = "https://github.com/Ardit-Mishra/tri-ai-private.git"


class ScopeFileTest(unittest.TestCase):
    def test_this_branch_declares_a_scope(self):
        scope = release_guard.read_scope(Path(__file__).resolve().parents[1])
        self.assertIn(scope, ("public", "private"))

    def test_an_absent_scope_file_is_treated_as_private(self):
        """Fail closed: a branch that has not said it is publishable is not."""
        self.assertEqual(release_guard.read_scope(Path("C:/nowhere-at-all")), "private")

    def test_an_unreadable_scope_is_private_not_public(self):
        self.assertEqual(release_guard.parse_scope(""), "private")
        self.assertEqual(release_guard.parse_scope("   "), "private")
        self.assertEqual(release_guard.parse_scope("banana"), "private")

    def test_scope_is_case_and_whitespace_tolerant(self):
        for text in ("public", "PUBLIC\n", "  Public  \n"):
            self.assertEqual(release_guard.parse_scope(text), "public")


class RefusalTest(unittest.TestCase):
    def _refusal(self, *, scope, destination, private_remotes=(PRIVATE,)):
        return release_guard.refusal(
            scope=scope, destination=destination, private_remotes=private_remotes)

    def test_private_content_to_the_public_remote_is_refused(self):
        reason = self._refusal(scope="private", destination=PUBLIC)
        self.assertIsNotNone(reason)
        self.assertIn("private", reason.lower())

    def test_private_content_to_the_private_remote_is_allowed(self):
        self.assertIsNone(self._refusal(scope="private", destination=PRIVATE))

    def test_public_content_may_go_anywhere(self):
        self.assertIsNone(self._refusal(scope="public", destination=PUBLIC))
        self.assertIsNone(self._refusal(scope="public", destination=PRIVATE))

    def test_an_unknown_remote_is_treated_as_public(self):
        """Fail closed. A new remote nobody has classified is not trusted."""
        self.assertIsNotNone(
            self._refusal(scope="private", destination="https://example.com/x.git"))

    def test_a_machine_with_no_private_remote_configured_refuses_everything_private(self):
        reason = self._refusal(scope="private", destination=PRIVATE, private_remotes=())
        self.assertIsNotNone(reason)
        # Refusing is not enough: an unconfigured machine must be told the
        # exact key to set. A refusal that only says "no" gets worked around
        # with --no-verify, which is the one outcome this guard cannot allow.
        self.assertIn(release_guard.PRIVATE_REMOTE_CONFIG, reason)
        self.assertIn("git config", reason)

    def test_matching_ignores_the_git_suffix_and_case(self):
        for variant in (
            "https://github.com/Ardit-Mishra/tri-ai-private",
            "https://github.com/Ardit-Mishra/tri-ai-private.git",
            "HTTPS://GITHUB.COM/Ardit-Mishra/tri-ai-private.git",
            "git@github.com:Ardit-Mishra/tri-ai-private.git",
        ):
            with self.subTest(variant=variant):
                self.assertIsNone(self._refusal(scope="private", destination=variant))

    def test_a_public_url_that_merely_prefixes_the_private_one_is_not_private(self):
        """`tri-ai` is a prefix of `tri-ai-private`. A substring match would
        make the public remote look private and wave everything through."""
        self.assertIsNotNone(self._refusal(scope="private", destination=PUBLIC))

    def test_the_refusal_names_what_to_do(self):
        reason = self._refusal(scope="private", destination=PUBLIC)
        self.assertTrue(len(reason) > 40, "a refusal nobody understands gets bypassed")


class HookIsInstalledTest(unittest.TestCase):
    """A guard nobody calls is the fifth module in this repo to be written,
    tested, and wired to nothing."""

    def test_the_hook_exists_and_invokes_the_guard(self):
        hook = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "pre-push"
        self.assertTrue(hook.exists(), "no pre-push hook")
        body = hook.read_text(encoding="utf-8")
        self.assertIn("release_guard", body)

    def test_the_hook_fails_the_push_on_a_nonzero_guard(self):
        hook = (Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "pre-push"
                ).read_text(encoding="utf-8")
        self.assertIn("exit", hook)


if __name__ == "__main__":
    unittest.main()


class BoundaryChecksAreScopeAwareTest(unittest.TestCase):
    """The public-release checks must consult the scope, not assume it.

    They exist to keep the public repository clean. Applied unconditionally
    they also forbid the operator's own notes from being committed to the
    private one - which is how 178 KB of working state came to sit on a
    single laptop with no remote at all.
    """

    def test_the_boundary_test_reads_the_declared_scope(self):
        source = (Path(__file__).resolve().parents[1]
                  / "tests" / "test_public_release_boundary.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("release_guard", source)
        self.assertIn("skipTest", source)

    def test_the_checks_follow_the_scope_in_whichever_direction_it_points(self):
        """Both directions, because the branch this runs on is not fixed.

        An earlier version of this asserted the checks are *skipped*, full
        stop - true on a private branch and false the moment the scrubbed
        branch ran the same suite, where it failed with "expected the
        public checks to be skipped here" while the boundary checks it was
        guarding had all passed. Asserting a branch fact where the property
        is a linkage is how a test ends up failing on the one branch it
        most needs to pass on.

        The property is that the scope decides. So read the scope and
        assert the matching outcome.
        """
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import release_guard

        scope = release_guard.read_scope(Path(__file__).resolve().parents[1])

        import unittest as _unittest
        loader = _unittest.TestLoader()
        suite = loader.discover(
            str(Path(__file__).resolve().parents[1] / "tests"),
            pattern="test_public_release_boundary.py")
        result = _unittest.TextTestRunner(
            stream=open(os.devnull, "w", encoding="utf-8"), verbosity=0).run(suite)
        self.assertEqual(result.failures, [])
        self.assertEqual(result.errors, [])

        if scope == "public":
            self.assertFalse(
                result.skipped,
                "RELEASE_SCOPE says public, so the boundary checks must "
                "actually run - a skipped check on the published branch is "
                "the exact hole this file exists to close")
            self.assertTrue(result.testsRun, "no boundary checks were found")
        else:
            self.assertTrue(
                result.skipped,
                f"RELEASE_SCOPE reads {scope!r}, so the public checks should "
                "have skipped rather than forbidding the operator's own notes")


class ScopeFileExplainsItselfTest(unittest.TestCase):
    """The marker sits in a repo strangers read; it should say what it means.

    A bare word in a file called RELEASE_SCOPE is cryptic, and a cryptic
    marker gets deleted by someone tidying up. Comment lines are ignored so
    the file can carry its own explanation.
    """

    def test_comment_lines_are_ignored(self):
        self.assertEqual(release_guard.parse_scope(
            "# what this means\n# more prose\npublic\n"), "public")

    def test_the_first_meaningful_line_decides(self):
        self.assertEqual(release_guard.parse_scope("\n\n  private  \n# trailing"),
                         "private")

    def test_a_file_of_only_comments_is_private(self):
        self.assertEqual(release_guard.parse_scope("# nothing declared\n"), "private")

    def test_a_stray_word_after_the_scope_does_not_flip_it(self):
        self.assertEqual(release_guard.parse_scope("private\npublic\n"), "private")
