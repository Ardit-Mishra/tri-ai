"""Agent-role and capability contracts for governed Hermes specialists."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402


class CapabilityContracts(unittest.TestCase):
    def test_researcher_gets_research_tools_and_citation_requirements(self):
        contract = capabilities.resolve_contract(
            "researcher", ["web_research", "scientific_research"]
        )
        brief = capabilities.brief_block(contract, skill_root=Path("C:/skills"))

        self.assertEqual(contract.role, "researcher")
        self.assertEqual(contract.capabilities, ("web_research", "scientific_research"))
        self.assertIn("Specialist role: Researcher", brief)
        self.assertIn("source URL", brief)
        self.assertIn("Do not invent citations", brief)
        self.assertIn("C:\\skills\\science-literature-review\\SKILL.md", brief)

    def test_designer_gets_taste_diagram_and_motion_skill_sources(self):
        contract = capabilities.resolve_contract(
            "designer", ["taste", "diagram_design", "motion_design"]
        )
        brief = capabilities.brief_block(contract, skill_root=Path("C:/skills"))

        self.assertIn("form an evidence-backed visual direction", brief.lower())
        self.assertIn("C:\\skills\\taste-skill\\SKILL.md", brief)
        self.assertIn("C:\\skills\\diagram-design\\SKILL.md", brief)
        self.assertIn("C:\\skills\\design-motion-principles\\SKILL.md", brief)

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
