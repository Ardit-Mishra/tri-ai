"""Dynamic discovery and task matching for Tri-AI's installed tool estate."""

from __future__ import annotations

import sys
import tempfile
import unittest
import json
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capability_catalog  # noqa: E402


class CapabilityCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _skill(self, name: str, description: str, *, origin: str = "ECC") -> Path:
        folder = self.root / "skills" / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(
            f"---\nname: {name}\ndescription: \"{description}\"\n"
            f"metadata:\n  origin: {origin}\n---\n\n# {name}\n",
            encoding="utf-8",
        )
        return path

    def test_discovers_skill_metadata_instead_of_a_hard_coded_shortlist(self):
        path = self._skill(
            "accessibility-audit",
            "Audit a complete web product for accessibility and usability defects.",
        )

        resources = capability_catalog.discover_skills([self.root / "skills"])

        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0].resource_id, "skill:accessibility-audit")
        self.assertEqual(resources[0].origin, "ECC")
        self.assertEqual(resources[0].path, path)
        self.assertTrue(resources[0].instruction_ready)

    def test_task_matcher_selects_relevant_resources_and_keeps_new_tools_eligible(self):
        self._skill("taste-skill", "Design a distinctive polished frontend interface.")
        self._skill("browser-qa", "Test a website in desktop and mobile browsers.")
        self._skill("bulk-rnaseq", "Build reproducible RNA sequencing workflows.")
        resources = capability_catalog.discover_skills([self.root / "skills"])

        selected = capability_catalog.match_resources(
            "Research competing products, then design and browser-test a polished website UI",
            resources,
            limit=5,
        )

        ids = [item.resource.resource_id for item in selected]
        self.assertIn("skill:taste-skill", ids)
        self.assertIn("skill:browser-qa", ids)
        self.assertNotIn("skill:bulk-rnaseq", ids)
        self.assertTrue(all(item.reason for item in selected))

    def test_archived_specialist_remains_selectable_when_no_active_copy_exists(self):
        archived = self.root / "skills-archive" / "rare-hardware-profiler"
        archived.mkdir(parents=True)
        (archived / "SKILL.md").write_text(
            "---\nname: rare-hardware-profiler\n"
            "description: Profile unusual accelerator hardware bottlenecks.\n---\n",
            encoding="utf-8",
        )
        resources = capability_catalog.discover_skills([archived.parent])

        selected = capability_catalog.match_resources(
            "Profile accelerator hardware bottlenecks", resources
        )

        self.assertEqual(selected[0].resource.availability, "archived-reference")

    def test_source_repositories_are_visible_but_not_misrepresented_as_adapters(self):
        repo = self.root / "sources" / "fresh-agent-tool"
        repo.mkdir(parents=True)
        (repo / "README.md").write_text(
            "# Fresh Agent Tool\nExperimental multi-agent browser research runtime.",
            encoding="utf-8",
        )
        (repo / "LICENSE").write_text("MIT License", encoding="utf-8")

        resources = capability_catalog.discover_source_repositories(repo.parent)

        self.assertEqual(resources[0].resource_id, "source:fresh-agent-tool")
        self.assertFalse(resources[0].instruction_ready)
        self.assertEqual(resources[0].license_hint, "MIT")

    def test_discovers_configured_mcp_servers_without_exposing_environment_values(self):
        config = self.root / "config.toml"
        config.write_text(
            "[mcp_servers.context7]\ncommand = 'npx'\nargs = ['-y', '@upstash/context7-mcp']\n"
            "[mcp_servers.context7.env]\nSECRET = 'do-not-leak'\n",
            encoding="utf-8",
        )

        resources = capability_catalog.discover_mcp_servers(config)

        self.assertEqual([item.resource_id for item in resources], ["mcp:context7"])
        rendered = repr(resources[0])
        self.assertNotIn("do-not-leak", rendered)
        self.assertIn("npx", resources[0].description)

    def test_adapter_manifest_records_readiness_permissions_and_risk_separately(self):
        manifest = self.root / "adapters.json"
        manifest.write_text(json.dumps({
            "schema": "triai.capability-adapters.v1",
            "adapters": [{
                "id": "browser-use",
                "name": "Browser Use",
                "description": "Browser automation for declared web workflows.",
                "kind": "command",
                "entrypoint": "definitely-not-an-installed-triai-command",
                "availability": "executable",
                "adapter_status": "gated",
                "upstream_url": "https://github.com/browser-use/browser-use",
                "permission_scopes": ["browser", "network"],
                "risk_status": "reviewed-with-boundaries",
                "tags": ["browser", "automation"],
            }],
        }), encoding="utf-8")

        resources = capability_catalog.discover_adapters(manifest)

        self.assertEqual(resources[0].resource_id, "adapter:browser-use")
        self.assertEqual(resources[0].entrypoint, "definitely-not-an-installed-triai-command")
        self.assertEqual(resources[0].adapter_status, "gated")
        self.assertEqual(resources[0].upstream_url, "https://github.com/browser-use/browser-use")
        self.assertEqual(resources[0].permission_scopes, ("browser", "network"))
        self.assertEqual(resources[0].risk_status, "reviewed-with-boundaries")
        self.assertEqual(resources[0].health_status, "entrypoint-missing")

    def test_executable_adapter_wins_over_same_named_instruction_copy(self):
        skill = capability_catalog.CapabilityResource(
            "skill:browser-use", "Browser Use", "skill", "installed",
            "Browser automation", self.root / "SKILL.md", "active", True,
        )
        adapter = capability_catalog.CapabilityResource(
            "adapter:browser-use", "Browser Use", "command", "operator-manifest",
            "Browser automation", None, "executable", False,
            adapter_status="gated", entrypoint="browser-use",
        )

        matches = capability_catalog.match_resources(
            "automate this browser workflow", [skill, adapter], limit=5
        )

        self.assertEqual(matches[0].resource.resource_id, "adapter:browser-use")

    def test_loopback_service_readiness_is_checked_without_external_network_access(self):
        manifest = self.root / "adapters.json"
        manifest.write_text(json.dumps({
            "schema": "triai.capability-adapters.v1",
            "adapters": [{
                "id": "router", "name": "Router", "kind": "service",
                "entrypoint": "http://127.0.0.1:3001", "availability": "gated",
            }],
        }), encoding="utf-8")

        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, timeout))
            return Response(b"ok")

        resources = capability_catalog.discover_adapters(manifest, opener=opener)

        self.assertEqual(resources[0].health_status, "loopback-service-ready")
        self.assertEqual(seen, [("http://127.0.0.1:3001", 2)])

    def test_external_service_is_cataloged_but_not_probed(self):
        manifest = self.root / "adapters.json"
        manifest.write_text(json.dumps({
            "schema": "triai.capability-adapters.v1",
            "adapters": [{
                "id": "remote", "name": "Remote", "kind": "service",
                "entrypoint": "https://example.com/health", "availability": "gated",
            }],
        }), encoding="utf-8")

        resources = capability_catalog.discover_adapters(
            manifest, opener=lambda *_args, **_kwargs: self.fail("external probe attempted")
        )

        self.assertEqual(resources[0].health_status, "external-service-not-probed")


if __name__ == "__main__":
    unittest.main()
