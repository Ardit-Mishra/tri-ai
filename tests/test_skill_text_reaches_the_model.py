"""A path is not guidance. The skill's words have to be in the prompt.

`brief_block` handed the agent a line like

    Skill instructions: C:/Users/Ardit II/.codex/skills/frontend-design/SKILL.md

and trusted it to go and open the file. Claude and Codex usually will.
They are not the point of this system: the standing constraint is `$0
marginal cost`, so the lanes that carry the volume are local Ollama models
and free API models routed through OmniRoute and FreeLLMAPI. A 7B model
handed a filesystem path and a build to finish does not stop and read it.

The evidence is on the desktop, in the brief run 135 wrote before it was
rejected: display face `Space Grotesk`, body face `JetBrains Mono`,
background `#09090b`, accent `#38bdf8`. That is the machine-made look
almost exactly - and `frontend-design`, the one skill whose job is to name
those defaults and forbid them, was sitting in the prompt as a path.

So the skill text itself goes in, up to a budget, and the budget is real:
these files run to 9-21 KB each and the prompt is not free. What does not
fit stays a path, but carries the skill's own one-line description, so
even a reference the model does not open tells it what it would find.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402


def _write_skill(root: Path, name: str, description: str, body: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class SkillTextIsInlinedTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_skill_s_own_words_appear_in_the_brief(self):
        _write_skill(self.root, "frontend-design", "How a page should look",
                     "Never reach for Space Grotesk as the safe face.")
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(contract, skill_root=self.root)
        self.assertIn("Never reach for Space Grotesk as the safe face.", block,
                      "the skill was named but its guidance never reached the model")

    def test_a_skill_that_does_not_exist_is_not_announced_as_readable(self):
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(contract, skill_root=self.root)
        self.assertNotIn("Skill instructions:", block,
                         "a dead path was handed to the agent as if it led somewhere")

    def test_the_budget_stops_the_prompt_running_away(self):
        big = "x" * 40_000
        for name in ("frontend-design", "web-design-guidelines", "ui-ux-pro-max"):
            _write_skill(self.root, name, f"{name} description", big)
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(contract, skill_root=self.root,
                                         skill_budget=10_000)
        self.assertLess(len(block), 20_000,
                        "three 40 KB skills were pasted in whole")

    def test_what_did_not_fit_still_says_what_it_is(self):
        """A reference the model may open should say what it would find."""
        big = "x" * 40_000
        for name in ("frontend-design", "web-design-guidelines", "ui-ux-pro-max"):
            _write_skill(self.root, name, f"the {name} description line", big)
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(contract, skill_root=self.root,
                                         skill_budget=10_000)
        self.assertIn("the ui-ux-pro-max description line", block,
                      "an over-budget skill was named with no indication of "
                      "what it contains, so the model cannot judge whether "
                      "opening it is worth a turn")

    def test_frontmatter_is_not_pasted_into_the_prompt(self):
        _write_skill(self.root, "frontend-design", "How a page should look",
                     "Body text the agent needs.")
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(contract, skill_root=self.root)
        self.assertNotIn("license:", block)
        self.assertIn("Body text the agent needs.", block)


class TheRealDesignSkillsCarryTheirAntiClicheGuidanceTest(unittest.TestCase):
    """The specific sentence that would have prevented run 135's brief."""

    def test_the_designer_is_told_about_the_machine_made_look(self):
        contract = capabilities.resolve_contract("designer", None)
        block = capabilities.brief_block(contract)
        lowered = block.lower()
        self.assertTrue(
            "space grotesk" in lowered or "templated" in lowered,
            "the designer's brief never mentions the defaults that read as "
            "machine-made, which is the thing it keeps producing")


class TheBuilderGetsTextTooTest(unittest.TestCase):
    """Builder is the role that runs most tasks, and it names no capability.

    Its allowed set is every capability, which expresses no preference, so
    `resolve_contract` deliberately leaves it empty rather than pasting two
    dozen skills into every build. That is the right call for the *named*
    skills - but it left the builder with a contract that says "Enabled
    capabilities: none" and twelve catalog paths, which is the same dead
    path in a longer list.

    The catalog matcher is doing good work: given "landing page ... pricing
    tiers" it surfaces `htmlx-pricing-page`, `saas-landing` and
    `marketing-copywriting`. Those matches should spend whatever the named
    skills left of the budget, instead of all being deferred by default.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _resource(self, name: str, body: str):
        import capability_catalog
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(f"---\nname: {name}\ndescription: about {name}\n---\n\n{body}\n",
                        encoding="utf-8")
        return capability_catalog.CapabilityResource(
            resource_id=f"skill:{name}",
            kind="skill",
            name=name,
            description=f"about {name}",
            path=path,
            availability="active",
            health_status="instruction-ready",
            risk_status="reviewed",
            instruction_ready=True,
            origin="test-fixture",
        )

    def test_a_matched_catalog_skill_is_inlined_not_merely_cited(self):
        resource = self._resource("pricing-page", "Anchor the mid tier, not the cheapest.")
        contract = capabilities.CapabilityContract("builder", ())
        block = capabilities.brief_block(
            contract,
            skill_root=self.root,
            task_prompt="build a pricing page",
            catalog_resources=[resource],
        )
        self.assertIn("Anchor the mid tier, not the cheapest.", block,
                      "the catalog matched a real skill and handed over a path")

    def test_named_capability_skills_are_served_before_catalog_matches(self):
        """Role-chosen guidance outranks a keyword match for the budget."""
        _write_skill(self.root, "frontend-design", "how it looks",
                     "NAMED-SKILL-BODY " + "y" * 4_000)
        resource = self._resource("pricing-page", "MATCHED-SKILL-BODY " + "z" * 4_000)
        contract = capabilities.CapabilityContract("designer", ("frontend_engineering",))
        block = capabilities.brief_block(
            contract,
            skill_root=self.root,
            # The window is narrow and both ends matter. Each fixture body
            # is ~4,018 bytes, so the budget must clear 4,018/0.6 = 6,697
            # for the named skill to pass the per-skill cap, and stay under
            # 8,036 so the remainder cannot also hold the matched one.
            # Outside that window this measures the cap, not the ordering.
            skill_budget=7_000,
            task_prompt="build a pricing page",
            catalog_resources=[resource],
        )
        self.assertIn("NAMED-SKILL-BODY", block)
        self.assertNotIn("MATCHED-SKILL-BODY", block,
                         "a keyword match consumed budget the role's own "
                         "skill needed")


class TheAlphabetMustNotDecideWhatTheRoleLearnsTest(unittest.TestCase):
    """Budget order was `sorted(allowed)`, so `c` beat `f` and `t`.

    Measured on the desktop the moment the catalog came back: a designer's
    brief carried `codebase-memory`, `marketing-competitor-profiling`,
    `web-design-guidelines` and one keyword match — and neither
    `frontend-design` nor `taste-skill`, the two whose entire job is the
    thing the designer keeps getting wrong. `codebase_memory` simply sorts
    before `frontend_engineering` and `taste`, and it is large.

    A role now declares what defines it, and that is served first. The set
    of allowed capabilities is unchanged; only the order the budget is
    spent in.
    """

    def test_every_role_s_emphasis_is_a_subset_of_what_it_allows(self):
        for name, role in capabilities.ROLES.items():
            self.assertTrue(
                set(role.emphasis) <= set(role.allowed),
                f"{name} emphasises a capability it does not allow: "
                f"{sorted(set(role.emphasis) - set(role.allowed))}")

    def test_the_designer_emphasises_how_the_thing_looks(self):
        emphasis = capabilities.ROLES["designer"].emphasis
        self.assertEqual(emphasis[:2], ("taste", "frontend_engineering"),
                         f"the designer leads with {emphasis[:2]}")

    def test_the_defining_skills_survive_the_real_budget(self):
        """The failure as measured, at the budget the system actually runs.

        Pinning 24,000 here would test a configuration nobody uses: that
        was the old default, and `taste-skill` alone is 21,366 bytes, so
        no ordering rule can fit both it and `frontend-design` inside it.
        That is what raised the default and added the per-skill cap.
        """
        contract = capabilities.resolve_contract("designer", None)
        block = capabilities.brief_block(contract)
        inlined = [l.split("[", 1)[1].split("]", 1)[0]
                   for l in block.splitlines() if l.startswith("### Skill [")]
        self.assertIn("frontend-design", inlined,
                      f"the designer got {inlined} and not frontend-design")
        self.assertIn("taste-skill", inlined,
                      f"the designer got {inlined} and not taste-skill")

    def test_a_keyword_match_never_outranks_the_role_s_own_emphasis(self):
        """The catalog is the other half of what crowded them out."""
        contract = capabilities.resolve_contract("designer", None)
        block = capabilities.brief_block(
            contract,
            task_prompt="portfolio website with animations, not machine made")
        inlined = [l.split("[", 1)[1].split("]", 1)[0]
                   for l in block.splitlines() if l.startswith("### Skill [")]
        self.assertIn("taste-skill", inlined,
                      f"a catalog keyword match displaced taste-skill: {inlined}")


if __name__ == "__main__":
    unittest.main()
