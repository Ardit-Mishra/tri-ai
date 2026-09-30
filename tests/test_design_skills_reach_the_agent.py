"""The skills a specialist is told to read must exist on the machine.

`capabilities.brief_block` hands the agent lines like:

    Skill instructions: C:\\Users\\Ardit II\\.codex\\skills\\taste-skill\\SKILL.md

On the desktop - the machine that runs the work - that directory held one
entry and none of the named skills. Every skill line pointed at a file
that does not exist, so a designer was told to consult guidance it could
not open, and quietly designed from nothing. It looked like the agent was
ignoring its instructions; it was never given them.

Separately, `frontend_engineering` named no skills at all. It is the
capability most directly about how a page ends up looking, and it
contributed a one-line instruction and nothing else.

These tests check both halves: that the named skills resolve on the
machine running the tests, and that the frontend capability actually
names design guidance.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402


def _skill_roots() -> list[Path]:
    """Where a skill may legitimately live on this machine."""
    home = Path.home()
    return [home / ".codex" / "skills", home / ".claude" / "skills"]


def _resolves(skill: str) -> bool:
    return any((root / skill / "SKILL.md").is_file() for root in _skill_roots())


class FrontendCapabilityNamesDesignGuidanceTest(unittest.TestCase):
    def test_frontend_engineering_names_at_least_one_skill(self):
        """It is the capability most about how a page looks, and it carried
        a one-line instruction and nothing else."""
        spec = capabilities.CAPABILITIES["frontend_engineering"]
        self.assertTrue(spec.skills,
                        "frontend_engineering names no skill, so a designer "
                        "receives no guidance on how the thing should look")

    def test_the_designer_role_reaches_design_guidance(self):
        designer = capabilities.ROLES["designer"]
        named: set[str] = set()
        for name in designer.allowed:
            named.update(capabilities.CAPABILITIES[name].skills)
        self.assertTrue(
            named & {"frontend-design", "ui-ux-pro-max", "web-design-guidelines"},
            f"the designer names no interface-design skill; it has {sorted(named)}")


class NamedSkillsResolveTest(unittest.TestCase):
    """A path handed to an agent must lead somewhere."""

    def test_every_skill_named_by_any_capability_exists(self):
        missing = []
        for name, spec in sorted(capabilities.CAPABILITIES.items()):
            for skill in spec.skills:
                if not _resolves(skill):
                    missing.append(f"{name} -> {skill}")
        self.assertEqual(
            missing, [],
            "these skills are named in a contract but are not installed on "
            "this machine, so the agent is handed dead paths")

    def test_the_designer_s_own_skills_all_resolve(self):
        designer = capabilities.ROLES["designer"]
        missing = []
        for name in sorted(designer.allowed):
            for skill in capabilities.CAPABILITIES[name].skills:
                if not _resolves(skill):
                    missing.append(skill)
        self.assertEqual(sorted(set(missing)), [])



class ASpecialistGetsItsCapabilitiesByDefaultTest(unittest.TestCase):
    """A role that declares an allowed set means it, unless told otherwise.

    `resolve_contract(role, None)` returned an empty tuple, so a task that
    did not name capabilities explicitly - which is every task created from
    a phone message - was told:

        Enabled capabilities: none beyond the base repository tools.

    and received no skill lines at all. The roles, the capabilities and the
    skills all existed and were tested; nothing ever asked for them. That is
    why a designer designed with no design guidance.

    Builder is the exception and stays empty: its allowed set is *every*
    capability, which expresses no preference, and defaulting it would paste
    two dozen skill paths into every ordinary build.
    """

    def test_a_designer_with_no_request_gets_the_designer_s_capabilities(self):
        contract = capabilities.resolve_contract("designer", None)
        self.assertTrue(contract.capabilities,
                        "a specialist with no explicit request got nothing")
        self.assertEqual(set(contract.capabilities),
                         set(capabilities.ROLES["designer"].allowed))

    def test_the_designer_default_carries_skill_lines_into_the_prompt(self):
        contract = capabilities.resolve_contract("designer", None)
        block = capabilities.brief_block(contract, task_prompt="build a landing page")
        # The brief used to cite `Skill instructions: <path>` and leave the
        # reading to the agent. It now inlines the text instead, because the
        # lanes that carry the volume here do not stop to open a path - see
        # test_skill_text_reaches_the_model. What this test still guards is
        # unchanged: a designer with no explicit request must arrive holding
        # design guidance rather than a bare role label.
        inlined = [l for l in block.splitlines() if l.startswith("### Skill [")]
        deferred = [l for l in block.splitlines() if " — read at " in l]
        self.assertTrue(inlined or deferred,
                        "the brief carries no skill for a designer")

    def test_builder_stays_empty_because_it_allows_everything(self):
        contract = capabilities.resolve_contract("builder", None)
        self.assertEqual(contract.capabilities, (),
                         "defaulting a role that allows everything would paste "
                         "every skill into every build")

    def test_an_explicit_request_still_wins(self):
        contract = capabilities.resolve_contract("designer", ["taste"])
        self.assertEqual(contract.capabilities, ("taste",))

    def test_an_explicit_empty_list_is_still_honoured_as_none(self):
        """Passing [] is a choice; passing None is silence."""
        self.assertEqual(capabilities.resolve_contract("designer", []).capabilities, ())

    def test_a_forbidden_capability_is_still_refused(self):
        with self.assertRaises(capabilities.CapabilityError):
            capabilities.resolve_contract("designer", ["backend_engineering"])

if __name__ == "__main__":
    unittest.main()
