"""The gate asked "did you choose?" when the question is "did you choose this?"

`_has_chosen_type` rejects a page that ships the framework's default stack
and nothing else. That catches an agent that made no decision. It does not
catch the far more common thing an agent does, which is make the same
decision every time.

Run 135 on the desktop wrote its brief, was rejected, and the brief is
still there. Display face `Space Grotesk`; body face `JetBrains Mono`;
background `#09090b`; accent `#38bdf8`. Every one of those is on the short
list of looks that read as machine-made, and the page passed
`_has_chosen_type` comfortably, because Space Grotesk is not in
`DEFAULT_FONT_NAMES`.

The distinction this module draws: one overused face beside a distinctive
one is a working page - plenty of good interfaces set body copy in Inter.
A pairing where *both* halves come off the list is the tell, because it
means nothing on the page was chosen for the subject.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import taste  # noqa: E402


class OverusedFacesTest(unittest.TestCase):
    def test_the_pairing_run_135_produced_is_named(self):
        markup = """
        <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk">
        <style>
          h1 { font-family: 'Space Grotesk', system-ui, sans-serif; }
          body { font-family: 'JetBrains Mono', ui-monospace, monospace; }
        </style>
        """
        self.assertEqual(
            sorted(taste.overused_faces(markup)),
            ["jetbrains mono", "space grotesk"])

    def test_a_distinctive_face_is_not_flagged(self):
        markup = "<style>h1{font-family:'Iowan Old Style',Georgia,serif}</style>"
        self.assertEqual(taste.overused_faces(markup), ())

    def test_the_framework_default_is_not_an_overused_face(self):
        """It is a different failure, already reported by _has_chosen_type."""
        markup = "<style>body{font-family:ui-sans-serif,system-ui,sans-serif}</style>"
        self.assertEqual(taste.overused_faces(markup), ())


class OnlyAWholeDefaultPairingIsFlaggedTest(unittest.TestCase):
    def test_one_overused_face_beside_a_distinctive_one_passes(self):
        markup = """<style>
          h1 { font-family: 'Fraunces', Georgia, serif; }
          body { font-family: Inter, system-ui, sans-serif; }
        </style>"""
        self.assertFalse(taste.reads_as_machine_made(markup),
                         "a distinctive display face with Inter body copy is "
                         "an ordinary, well-made pairing")

    def test_both_faces_off_the_list_is_flagged(self):
        markup = """<style>
          h1 { font-family: 'Space Grotesk', sans-serif; }
          body { font-family: Inter, sans-serif; }
        </style>"""
        self.assertTrue(taste.reads_as_machine_made(markup))

    def test_a_page_that_chose_nothing_is_not_flagged_here(self):
        """Silence is _has_chosen_type's finding, not this one."""
        markup = "<style>body{font-family:system-ui,sans-serif}</style>"
        self.assertFalse(taste.reads_as_machine_made(markup))


class TheBriefNamesTheDefaultsTest(unittest.TestCase):
    def test_the_agent_is_told_which_looks_are_worn_out(self):
        text = taste.brief_block().lower()
        for face in ("space grotesk", "inter"):
            self.assertIn(face, text,
                          f"the brief never warns against {face}, and the "
                          f"agent keeps reaching for it")

    def test_the_brief_outranks_a_skill_s_own_shortlist(self):
        """The installed `taste-skill` prescribes the faces this gate rejects.

        Lines 40, 41 and 108 of `~/.claude/skills/taste-skill/SKILL.md` ban
        Inter under an **ANTI-SLOP** heading and then mandate `Geist`,
        `Outfit`, `Cabinet Grotesk`, `Satoshi`, `Geist Mono` and
        `JetBrains Mono` instead. Every one of those is on
        `OVERUSED_FACES`, and `Satoshi + JetBrains Mono` is within one step
        of what run 135 actually shipped.

        So the agent was not ignoring its guidance. Where it followed the
        guidance, the guidance named a fixed shortlist — and a shortlist
        applied to every subject is a default however it is headed. Since
        the taste brief is appended last, it has to say so; otherwise the
        gate rejects work for obeying the prompt.
        """
        text = taste.brief_block().lower()
        self.assertIn("shortlist", text,
                      "the brief does not address the skills that prescribe "
                      "a fixed set of faces, so an agent that follows them "
                      "is rejected for obeying its own instructions")

    def test_the_brief_asks_for_the_reason_not_just_the_names(self):
        text = taste.brief_block().lower()
        self.assertTrue(
            "why" in text or "because" in text,
            "naming the clichés without asking for a justification lets the "
            "agent swap one default for another")


class TheGateReportsItTest(unittest.TestCase):
    """A finding nobody reads changes nothing.

    The fixture is a page that passes every other check — five promises all
    kept, four palette colours, an svg, no filler, no generic heading — and
    differs from an acceptable page only in that both of its faces come off
    the list. If this reports nothing, the rule exists in `taste` and not in
    the gate.
    """

    BRIEF = "\n".join((
        "## References",
        "- https://example.com/a: shows the kiln log beside each piece",
        "- https://example.com/b: prices by glaze, not by size",
        "- https://example.com/c: photographs the foot ring, not the rim",
        "## Palette",
        "- #1b1b1b", "- #f4efe6", "- #8c3b2e", "- #d9c7a7",
        "## Type",
        "- Space Grotesk", "- Inter",
        "## Layout",
        "One column, wide margins. Each piece sits above its firing record.",
        "## Must appear",
        "- Nimbus Ceramics", "- 1280 C", "- Jaipur", "- stoneware", "- tenmoku",
    ))

    PAGE = (
        "<html><head><style>"
        "h1{font-family:'Space Grotesk',sans-serif}"
        "body{font-family:Inter,sans-serif}"
        ".a{color:#1b1b1b}.b{color:#f4efe6}.c{color:#8c3b2e}.d{color:#d9c7a7}"
        "</style></head><body><h1>Nimbus Ceramics</h1>"
        "<p>Stoneware fired to 1280 C in Jaipur, finished in a tenmoku glaze.</p>"
        "<svg width='10' height='10'></svg></body></html>"
    )

    def test_check_work_reports_a_default_pairing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BRIEF.md").write_text(self.BRIEF, encoding="utf-8")
            (root / "index.html").write_text(self.PAGE, encoding="utf-8")
            work = taste.collect([root / "BRIEF.md", root / "index.html"],
                                 root=root)
            problems = taste.check_work(work)
            joined = " ".join(problems).lower()
            self.assertIn("space grotesk", joined,
                          f"the gate did not mention the pairing: {problems}")

    def test_the_same_page_with_a_real_display_face_is_not_reported(self):
        """The mutation that proves the check is reading the faces."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BRIEF.md").write_text(
                self.BRIEF.replace("- Space Grotesk", "- Fraunces"),
                encoding="utf-8")
            (root / "index.html").write_text(
                self.PAGE.replace("'Space Grotesk'", "'Fraunces'"),
                encoding="utf-8")
            work = taste.collect([root / "BRIEF.md", root / "index.html"],
                                 root=root)
            joined = " ".join(taste.check_work(work)).lower()
            self.assertNotIn("worn-out", joined,
                             "a distinctive display face was still reported")


if __name__ == "__main__":
    unittest.main()


