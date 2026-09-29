"""The seven pending Cortex sources, indexed or honestly reported as down.

Two rules these collectors share with the filesystem indexer:

**Metadata only.** Names, sizes and times. No file contents, no repository
contents, no transcript bodies, no absolute paths - `private_index`
validates the last one and refuses a payload carrying it.

**An unreachable source is reported, never omitted.** A Cortex that silently
drops a source it was told to display looks complete when it is not. Every
collector therefore returns a *valid* payload even when it fails, carrying
`status: unavailable` and a diagnostic that says why.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard import private_index, source_collectors  # noqa: E402


class CatalogTest(unittest.TestCase):
    """OmniRoute and FreeLLMAPI both speak /v1/models."""

    def _answer(self, body):
        payload = json.dumps(body).encode("utf-8")

        class _Response:
            def read(self_inner):
                return payload

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_exc):
                return False

        return mock.patch.object(source_collectors.request, "urlopen",
                                 return_value=_Response())

    def test_a_catalog_without_sizes_still_produces_a_valid_index(self):
        """An OpenAI-compatible catalog reports no byte size. A file item with
        a null size is refused by the validator, so the collector has to say
        something legal - and this is the one path that only runs when a
        router is actually up, which is why it went unnoticed."""
        with self._answer({"data": [{"id": "qwen2.5-coder:7b"}, {"id": "devstral:24b"}]}):
            payload = source_collectors.openai_catalog(
                "http://example.invalid", source_id="omniroute", label="OmniRoute")
        self.assertEqual(payload["status"], "indexed")
        self.assertEqual(payload["item_count"], 3)          # root + 2 models
        private_index.validate_private_index(payload)       # must not raise

    def test_a_router_that_does_not_answer_is_reported_not_omitted(self):
        with mock.patch.object(source_collectors.request, "urlopen",
                               side_effect=OSError("refused")):
            payload = source_collectors.openai_catalog(
                "http://127.0.0.1:1/", source_id="omniroute", label="OmniRoute")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["item_count"], 0)
        self.assertIn("OmniRoute", str(payload["diagnostic"]))
        self.assertEqual(payload["sources"][0]["id"], "omniroute")
        private_index.validate_private_index(payload)

    def test_a_reachable_router_serving_nothing_is_partial_not_indexed(self):
        with self._answer({"data": []}):
            payload = source_collectors.openai_catalog(
                "http://example.invalid", source_id="freellmapi", label="FreeLLMAPI")
        self.assertEqual(payload["status"], "partial")

    def test_a_nonsense_answer_does_not_become_an_index(self):
        with self._answer({"unexpected": True}):
            payload = source_collectors.openai_catalog(
                "http://example.invalid", source_id="omniroute", label="OmniRoute")
        self.assertEqual(payload["status"], "unavailable")


class LocalStoreTest(unittest.TestCase):
    def test_a_missing_obsidian_config_is_unavailable_not_a_crash(self):
        payload = source_collectors.obsidian_vaults(
            Path("C:/nowhere/obsidian.json"))
        self.assertEqual(payload["status"], "unavailable")
        private_index.validate_private_index(payload)

    def test_a_home_with_no_agent_stores_is_unavailable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty:
            payload = source_collectors.agent_sessions(Path(empty))
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["sources"][0]["id"], "claude-codex")

    def test_agent_session_roots_fold_into_one_declared_region(self):
        """Two stores, one declared source. If the roots do not fold, the
        region reports zero while holding every node."""
        from dashboard import kaya_web
        for root in ("claude-codex-claude", "claude-codex-codex"):
            self.assertEqual(kaya_web._source_region_id(root), "claude-codex")


class HostingTest(unittest.TestCase):
    def test_neither_provider_signed_in_is_unavailable_with_both_reasons(self):
        with mock.patch.object(source_collectors, "_run",
                               return_value=(127, "not found")):
            payload = source_collectors.hosting_projects()
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("Vercel", str(payload["diagnostic"]))
        self.assertIn("Render", str(payload["diagnostic"]))

    def test_one_provider_signed_in_still_indexes(self):
        def _run(command, timeout=45.0):
            if command[0] == "render":
                return 0, json.dumps([{"service": {"name": "kaya-demo"}}])
            return 127, "not found"

        with mock.patch.object(source_collectors, "_run", _run):
            payload = source_collectors.hosting_projects()
        self.assertEqual(payload["status"], "indexed")
        names = [item["name"] for item in payload["items"]]
        self.assertIn("render/kaya-demo", names)
        private_index.validate_private_index(payload)


class GithubTest(unittest.TestCase):
    def test_repository_names_only_never_contents(self):
        listing = json.dumps([
            {"nameWithOwner": "Ardit-Mishra/tri-ai", "diskUsage": 12,
             "pushedAt": "2026-09-28T10:00:00Z", "isPrivate": False},
            {"nameWithOwner": "Ardit-Mishra/secret", "diskUsage": 3,
             "pushedAt": "bad-timestamp", "isPrivate": True},
        ])
        with mock.patch.object(source_collectors, "_run", return_value=(0, listing)):
            payload = source_collectors.github_repositories()
        self.assertEqual(payload["status"], "indexed")
        self.assertEqual(payload["item_count"], 3)          # root + 2 repos
        self.assertIn("1 private", str(payload["diagnostic"]))
        private_index.validate_private_index(payload)

    def test_an_unparsable_push_time_does_not_fail_the_whole_index(self):
        listing = json.dumps([{"nameWithOwner": "a/b", "diskUsage": 1,
                               "pushedAt": "nonsense", "isPrivate": False}])
        with mock.patch.object(source_collectors, "_run", return_value=(0, listing)):
            payload = source_collectors.github_repositories()
        self.assertEqual(payload["status"], "indexed")

    def test_an_unauthenticated_cli_is_unavailable(self):
        with mock.patch.object(source_collectors, "_run", return_value=(1, "")):
            payload = source_collectors.github_repositories()
        self.assertEqual(payload["status"], "unavailable")


class EveryCollectorTest(unittest.TestCase):
    def test_all_seven_declared_ids_are_covered_or_explained(self):
        """`phone` has no collector because nothing runs on the phone yet.
        This test exists so adding one is not forgotten silently."""
        covered = set(source_collectors.COLLECTORS)
        self.assertEqual(
            covered,
            {"obsidian", "claude-codex", "github", "vercel-render",
             "omniroute", "freellmapi"},
        )
        self.assertNotIn("phone", covered,
                         "a phone collector needs a phone-side agent first")


if __name__ == "__main__":
    unittest.main()


class CredentialHandlingTest(unittest.TestCase):
    """A key may be presented; it must never be kept or shown."""

    def test_a_refused_catalog_says_so_without_echoing_the_key(self):
        from urllib import error as urlerror
        refusal = urlerror.HTTPError(
            "http://example.invalid/v1/models", 401, "Unauthorized", {}, None)
        with mock.patch.object(source_collectors.request, "urlopen",
                               side_effect=refusal):
            payload = source_collectors.openai_catalog(
                "http://example.invalid", source_id="freellmapi",
                label="FreeLLMAPI", api_key="sk-do-not-leak-me")
        blob = json.dumps(payload)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("401", str(payload["diagnostic"]))
        self.assertNotIn("sk-do-not-leak-me", blob,
                         "a diagnostic ends up on a dashboard")

    def test_the_key_is_sent_as_a_bearer_token(self):
        captured = {}

        def _urlopen(probe, timeout=None):
            captured["auth"] = probe.get_header("Authorization")
            raise OSError("stop here")

        with mock.patch.object(source_collectors.request, "urlopen", _urlopen):
            source_collectors.openai_catalog(
                "http://example.invalid", source_id="omniroute",
                label="OmniRoute", api_key="abc123")
        self.assertEqual(captured["auth"], "Bearer abc123")

    def test_no_key_means_no_authorization_header(self):
        captured = {}

        def _urlopen(probe, timeout=None):
            captured["auth"] = probe.get_header("Authorization")
            raise OSError("stop here")

        with mock.patch.object(source_collectors.request, "urlopen", _urlopen):
            source_collectors.openai_catalog(
                "http://example.invalid", source_id="omniroute", label="OmniRoute")
        self.assertIsNone(captured["auth"])

    def test_a_server_error_is_distinguished_from_no_answer(self):
        from urllib import error as urlerror
        failure = urlerror.HTTPError(
            "http://example.invalid/v1/models", 500, "Server Error", {}, None)
        with mock.patch.object(source_collectors.request, "urlopen",
                               side_effect=failure):
            payload = source_collectors.openai_catalog(
                "http://example.invalid", source_id="omniroute", label="OmniRoute")
        self.assertIn("500", str(payload["diagnostic"]))


class OmniRouteCliTest(unittest.TestCase):
    """The CLI lists models without the HTTP key the server demands.

    `omniroute serve` answers /v1/models with 401 unless a key is presented,
    but `omniroute models --output json` reads the same catalog locally and
    is already trusted on this machine. Preferring it means the catalog can
    be indexed without Claude ever handling a credential.
    """

    LISTING = json.dumps([
        {"id": "Claude Opus 5", "provider": "anthropic", "contextWindow": 200000},
        {"id": "qwen2.5-coder:7b", "provider": "deskollama", "contextWindow": 32768},
    ])

    def test_the_cli_catalog_is_indexed(self):
        with mock.patch.object(source_collectors, "_run",
                               return_value=(0, self.LISTING)):
            payload = source_collectors.omniroute_catalog()
        self.assertEqual(payload["status"], "indexed")
        self.assertEqual(payload["item_count"], 3)          # root + 2 models
        names = [item["name"] for item in payload["items"]]
        self.assertIn("Claude Opus 5", names)
        private_index.validate_private_index(payload)

    def test_ansi_colour_codes_do_not_break_the_parse(self):
        """The CLI writes a banner and colour escapes before the JSON."""
        noisy = "\x1b[2m loaded env \x1b[0m\n" + self.LISTING + "\n\x1b[2m done \x1b[0m"
        with mock.patch.object(source_collectors, "_run", return_value=(0, noisy)):
            payload = source_collectors.omniroute_catalog()
        self.assertEqual(payload["status"], "indexed")

    def test_trailing_output_after_the_json_is_ignored(self):
        with mock.patch.object(source_collectors, "_run",
                               return_value=(0, self.LISTING + "\n... and 57 more.\n")):
            payload = source_collectors.omniroute_catalog()
        self.assertEqual(payload["status"], "indexed")

    def test_the_provider_is_kept_so_a_lane_is_identifiable(self):
        with mock.patch.object(source_collectors, "_run",
                               return_value=(0, self.LISTING)):
            payload = source_collectors.omniroute_catalog()
        paths = [item["relative_path"] for item in payload["items"]]
        self.assertIn("models/deskollama/qwen2.5-coder:7b", paths)

    def test_no_cli_falls_back_to_the_http_catalog(self):
        with mock.patch.object(source_collectors, "_run", return_value=(127, "")):
            with mock.patch.object(source_collectors, "openai_catalog",
                                   return_value={"fell_back": True}) as http:
                result = source_collectors.omniroute_catalog()
        self.assertTrue(result.get("fell_back"))
        self.assertTrue(http.called)

    def test_a_cli_that_prints_no_json_falls_back_too(self):
        with mock.patch.object(source_collectors, "_run",
                               return_value=(0, "a table, not json")):
            with mock.patch.object(source_collectors, "openai_catalog",
                                   return_value={"fell_back": True}):
                result = source_collectors.omniroute_catalog()
        self.assertTrue(result.get("fell_back"))


class WindowsShimTest(unittest.TestCase):
    """An npm-installed CLI is a .cmd shim, which CreateProcess cannot run.

    `gh` and `render` are real executables and worked immediately. `omniroute`
    is installed by npm as `omniroute.cmd`, and a bare `["omniroute", ...]`
    argv fails with FileNotFoundError on Windows - so the collector silently
    fell back to HTTP and reported 401, hiding a catalog it could have read.
    """

    def test_a_cmd_shim_is_invoked_through_the_interpreter(self):
        captured = {}

        def _fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["shell"] = kwargs.get("shell")
            raise OSError("stop here")

        with mock.patch.object(source_collectors.shutil, "which",
                               return_value=r"C:\npm\omniroute.cmd"):
            with mock.patch.object(source_collectors.subprocess, "run", _fake_run):
                source_collectors._run(["omniroute", "models"])

        self.assertTrue(captured["argv"][0].lower().endswith("cmd.exe"),
                        captured["argv"][0])
        self.assertIn("/c", captured["argv"])
        self.assertIn(r"C:\npm\omniroute.cmd", captured["argv"])
        self.assertFalse(captured["shell"], "never hand the argv to a shell")

    def test_a_real_executable_is_run_directly(self):
        captured = {}

        def _fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            raise OSError("stop here")

        with mock.patch.object(source_collectors.shutil, "which",
                               return_value=r"C:\Program Files\gh\gh.exe"):
            with mock.patch.object(source_collectors.subprocess, "run", _fake_run):
                source_collectors._run(["gh", "repo", "list"])

        self.assertEqual(captured["argv"][0], r"C:\Program Files\gh\gh.exe")
        self.assertNotIn("/c", captured["argv"])

    def test_a_missing_command_is_reported_not_raised(self):
        with mock.patch.object(source_collectors.shutil, "which", return_value=None):
            code, output = source_collectors._run(["nonexistent-tool"])
        self.assertNotEqual(code, 0)
