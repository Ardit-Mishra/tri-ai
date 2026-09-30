"""Agent-role and capability contracts for governed Hermes specialists."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402


class CapabilityContracts(unittest.TestCase):
    def _fixture_root(self, *names: str) -> Path:
        """A skill tree that exists, because the brief now reads the files.

        These two tests used to pass `C:/skills`, a path with nothing behind
        it, and assert that the string came back out. That worked while the
        brief was a list of citations. It stopped being a useful check the
        moment the brief started inlining what it finds: a root with no
        skills in it is now correctly silent, so asserting on a made-up path
        was asserting that a dead reference is still printed.
        """
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name in names:
            folder = root / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: what {name} is for\n---\n\n"
                f"BODY OF {name}\n",
                encoding="utf-8")
        return root

    def test_researcher_gets_research_tools_and_citation_requirements(self):
        root = self._fixture_root("science-literature-review",
                                  "science-bioservices", "science-biopython")
        contract = capabilities.resolve_contract(
            "researcher", ["web_research", "scientific_research"]
        )
        brief = capabilities.brief_block(contract, skill_root=root)

        self.assertEqual(contract.role, "researcher")
        self.assertEqual(contract.capabilities, ("web_research", "scientific_research"))
        self.assertIn("Specialist role: Researcher", brief)
        self.assertIn("source URL", brief)
        self.assertIn("Do not invent citations", brief)
        self.assertIn("BODY OF science-literature-review", brief)

    def test_designer_gets_taste_diagram_and_motion_skill_sources(self):
        root = self._fixture_root("taste-skill", "diagram-design",
                                  "design-motion-principles")
        contract = capabilities.resolve_contract(
            "designer", ["taste", "diagram_design", "motion_design"]
        )
        brief = capabilities.brief_block(contract, skill_root=root)

        self.assertIn("form an evidence-backed visual direction", brief.lower())
        self.assertIn("BODY OF taste-skill", brief)
        self.assertIn("BODY OF diagram-design", brief)
        self.assertIn("BODY OF design-motion-principles", brief)

    def test_unknown_or_role_forbidden_capabilities_fail_closed(self):
        with self.assertRaisesRegex(capabilities.CapabilityError, "unknown capability"):
            capabilities.resolve_contract("builder", ["unlimited_shell"])
        with self.assertRaisesRegex(capabilities.CapabilityError, "not allowed for role"):
            capabilities.resolve_contract("researcher", ["deployment_prepare"])

    def test_default_builder_contract_preserves_legacy_tasks(self):
        contract = capabilities.resolve_contract(None, None)
        self.assertEqual(contract.role, "builder")
        self.assertEqual(contract.capabilities, ())

    def test_recommends_methods_and_matches_the_full_catalog_for_a_task(self):
        resource = capabilities.capability_catalog.CapabilityResource(
            resource_id="skill:ui-ux-pro-max",
            name="ui-ux-pro-max",
            kind="skill",
            origin="installed",
            description="Design and audit polished frontend user interfaces and dashboards.",
            path=Path("C:/skills/ui-ux-pro-max/SKILL.md"),
            availability="active",
            instruction_ready=True,
        )
        recommended = capabilities.recommend_capabilities(
            "Research and build a polished 3D dashboard website", "builder"
        )
        contract = capabilities.resolve_contract("builder", recommended)

        brief = capabilities.brief_block(
            contract,
            task_prompt="Research and build a polished 3D dashboard website",
            catalog_resources=[resource],
        )

        self.assertIn("web_research", contract.capabilities)
        self.assertIn("taste", contract.capabilities)
        self.assertIn("motion_design", contract.capabilities)
        self.assertIn("Automatically matched resources", brief)
        self.assertIn("C:\\skills\\ui-ux-pro-max\\SKILL.md", brief)


if __name__ == "__main__":
    unittest.main()
