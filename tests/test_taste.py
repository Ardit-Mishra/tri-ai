"""The page the user rejected is the specification for these tests.

Run 58 of `t_036e27e3` produced a landing page for an Uttar Pradesh masala and
cooking-oil brand that passed every gate the board had. The user's verdict:
*"it looked like ai slop ... it was not even for spices, it was a bunch of
words worth a whole lot of nothing."*

They were right, and the file proves it. Measured against what any real Indian
packaged-food site carries, that page contained:

    FSSAI licence   0        net weight in grams   0
    MRP             0        batch or expiry       0
    grind date      0        GST                   0
    typeface        none - the framework's default system stack

None of which any *mechanical* check could have known to look for. That is the
finding these tests encode, and it is the reason the research pass exists:

  - The floor checks (filler phrases, template headings, a real typeface, some
    imagery, more than a token palette) catch a page that is obviously empty.
    On the Mishwan page they catch exactly one thing, the missing typeface.
    They are a floor, not a standard, and the tests say so.

  - The teeth are the brief. The agent researches the field, writes down the
    concrete strings real examples carry, and the verifier holds the finished
    file to that list. A page with no FSSAI number fails not because anyone
    hard-coded "FSSAI" into Tri-AI, but because the agent went and looked and
    then wrote it down.

So: a promise made and not kept must fail, a repair must never trigger a
redesign, and a typo in taste.json must never stop the board.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import taste  # noqa: E402

GOOD_BRIEF = """# Brief

## References
- https://example-spice-one.in/masala — lists net weight and grind date per pack
- https://example-spice-two.in/oils — shows the FSSAI licence in the footer
- https://example-spice-three.in — prices every pack in MRP with the rupee sign

## Palette
#8B2E1F #F4E9D8 #2B2B2B #C9782A #6B7F4E

## Type
Fraunces for display, Inter for body.

## Layout
A shelf of packs above the fold, each with its own weight and price.
Provenance sits under the fold, not above it.

## Must appear
- FSSAI
- 200 g
- MRP
- Uttar Pradesh
- cold-pressed
"""


def markup(**parts: str) -> str:
    """A page that passes the floor, so each test can break one thing."""
    base = {
        "head": '<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">',
        "style": "<style>a{color:#8B2E1F}b{color:#F4E9D8}c{color:#2B2B2B}</style>",
        "body": "<h1>Mishwan</h1><img src='assets/logo.jpg' alt='pack'>"
                "<p>FSSAI 10021999000123 · 200 g · MRP ₹185 · "
                "cold-pressed in Uttar Pradesh</p>",
    }
    base.update(parts)
    return f"<html><head>{base['head']}{base['style']}</head>" \
           f"<body>{base['body']}</body></html>"


class ResearchIsRequiredOnlyWhereTasteExists(unittest.TestCase):
    def test_making_something_visual_asks_for_a_brief(self) -> None:
        for prompt in (
            "Build me a landing page for a brand called Mishwan",
            "make a poster for the shop",
            "create a dashboard UI for the board",
            "design a logo",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(taste.applies_to(prompt))

    def test_work_with_nothing_to_look_at_is_left_alone(self) -> None:
        for prompt in (
            "rename the variable in board.py",
            "fix the flaky test in test_reclaim.py",
            "add a retry to the ledger writer",
        ):
            with self.subTest(prompt=prompt):
                self.assertFalse(taste.applies_to(prompt))

    def test_a_repair_does_not_trigger_a_redesign(self) -> None:
        # Names a visual artifact, asks only for a repair. Research here would
        # invite the agent to rebuild what it was asked to adjust.
        self.assertFalse(taste.applies_to("fix the spacing in report.html"))
        self.assertFalse(taste.applies_to("the logo on the page is too big"))

    def test_the_standard_can_be_switched_off_entirely(self) -> None:
        off = taste.Standard(enabled=False)
        self.assertFalse(taste.applies_to("build me a landing page", off))


class TheBriefIsReadBack(unittest.TestCase):
    def test_promises_and_references_survive_the_round_trip(self) -> None:
        brief = taste.parse_brief(GOOD_BRIEF)
        self.assertEqual(
            brief.must_appear,
            ("FSSAI", "200 g", "MRP", "Uttar Pradesh", "cold-pressed"),
        )
        self.assertEqual(len(brief.references), 3)
        self.assertIn("palette", brief.sections)

    def test_bullets_outside_must_appear_are_not_promises(self) -> None:
        brief = taste.parse_brief(
            "## Layout\n- a shelf of packs\n\n## Must appear\n- FSSAI\n"
        )
        self.assertEqual(brief.must_appear, ("FSSAI",))

    def test_a_reference_url_is_counted_once(self) -> None:
        brief = taste.parse_brief(
            "## References\n- https://a.in/x notes\n- https://a.in/x again\n"
        )
        self.assertEqual(brief.references, ("https://a.in/x",))

    def test_a_brief_with_nothing_concrete_in_it_is_rejected(self) -> None:
        thin = taste.parse_brief("## Must appear\n- quality\n")
        problems = taste.check_brief(thin)
        self.assertTrue(any("Must appear" in p for p in problems))
        self.assertTrue(any("reference" in p for p in problems))
        self.assertTrue(any("palette" in p for p in problems))

    def test_a_researched_brief_passes(self) -> None:
        self.assertEqual(taste.check_brief(taste.parse_brief(GOOD_BRIEF)), [])


class ThePromiseIsTheGate(unittest.TestCase):
    def setUp(self) -> None:
        self.brief = taste.parse_brief(GOOD_BRIEF)

    def test_work_that_keeps_its_promises_passes(self) -> None:
        self.assertEqual(taste.check_deliverable(markup(), self.brief), [])

    def test_a_promise_made_and_not_kept_fails(self) -> None:
        body = ("<h1>Mishwan</h1><img src='a.jpg' alt='pack'>"
                "<p>200 g · MRP ₹185 · cold-pressed in Uttar Pradesh</p>")
        problems = taste.check_deliverable(markup(body=body), self.brief)
        self.assertEqual(len(problems), 1)
        self.assertIn("FSSAI", problems[0])

    def test_a_promise_is_matched_regardless_of_case(self) -> None:
        body = markup().replace("cold-pressed", "Cold-Pressed")
        self.assertEqual(taste.check_deliverable(body, self.brief), [])

    def test_without_a_brief_only_the_floor_applies(self) -> None:
        # The same page that fails its promise passes when nothing was promised
        # — which is precisely why the brief, not the floor, is the standard.
        body = "<h1>Mishwan</h1><img src='a.jpg' alt='x'><p>Nothing here.</p>"
        self.assertEqual(taste.check_deliverable(markup(body=body)), [])


class TheFloorCatchesTheObviouslyEmpty(unittest.TestCase):
    def test_filler_prose_fails(self) -> None:
        body = "<h1>Brand</h1><img src='a.jpg' alt='x'>" \
               "<p>We offer a wide range of unparalleled products.</p>"
        problems = taste.check_deliverable(markup(body=body))
        self.assertTrue(any("wide range" in p for p in problems))
        self.assertTrue(any("unparalleled" in p for p in problems))

    def test_filler_inside_a_tag_attribute_is_not_a_finding(self) -> None:
        # Only what a reader sees counts. A class named "placeholder" is markup.
        body = '<h1>Brand</h1><img src="a.jpg" class="placeholder" alt="x">' \
               "<p>Ground in Kanpur, weekly.</p>"
        self.assertEqual(taste.check_deliverable(markup(body=body)), [])

    def test_the_business_template_shape_fails(self) -> None:
        body = ("<h2>About Us</h2><h2>Why Choose Us</h2><h2>Our Mission</h2>"
                "<img src='a.jpg' alt='x'>")
        problems = taste.check_deliverable(markup(body=body))
        self.assertTrue(any("generic template headings" in p for p in problems))

    def test_one_ordinary_heading_is_not_a_template(self) -> None:
        body = "<h2>About Us</h2><img src='a.jpg' alt='x'><p>Ground in Kanpur.</p>"
        self.assertEqual(taste.check_deliverable(markup(body=body)), [])

    def test_the_system_font_stack_is_a_finding(self) -> None:
        # The one thing the floor did catch on the page the user rejected.
        problems = taste.check_deliverable(markup(head=""))
        self.assertTrue(any("typeface" in p for p in problems))

    def test_a_page_with_no_image_at_all_is_a_finding(self) -> None:
        problems = taste.check_deliverable(markup(body="<h1>Brand</h1><p>Text.</p>"))
        self.assertTrue(any("image" in p for p in problems))

    def test_two_colours_are_not_a_palette(self) -> None:
        problems = taste.check_deliverable(
            markup(style="<style>a{color:#8B2E1F}b{color:#fff}</style>")
        )
        self.assertTrue(any("palette" in p for p in problems))


class ConfigurationCannotStopTheBoard(unittest.TestCase):
    def test_an_absent_file_means_the_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            std = taste.load(Path(tmp) / "nothing.json")
            self.assertIsNone(std.config_error)
            self.assertEqual(std.banned_phrases, taste.BANNED_PHRASES)

    def test_a_malformed_file_falls_back_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "taste.json"
            bad.write_text("{ not json", encoding="utf-8")
            std = taste.load(bad)
            self.assertIsNotNone(std.config_error)
            self.assertEqual(std.banned_phrases, taste.BANNED_PHRASES)
            self.assertTrue(std.enabled)

    def test_an_operator_can_raise_the_bar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "taste.json"
            cfg.write_text(
                json.dumps({"min_must_appear": 9, "min_references": 5}),
                encoding="utf-8",
            )
            std = taste.load(cfg)
            self.assertEqual(std.min_must_appear, 9)
            problems = taste.check_brief(taste.parse_brief(GOOD_BRIEF), std)
            self.assertEqual(len(problems), 2)

    def test_an_empty_list_does_not_silently_disarm_a_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "taste.json"
            cfg.write_text(json.dumps({"banned_phrases": []}), encoding="utf-8")
            self.assertEqual(taste.load(cfg).banned_phrases, taste.BANNED_PHRASES)


class TheInstructionSaysWhatTheGateChecks(unittest.TestCase):
    def test_the_brief_block_names_every_section_the_parser_reads(self) -> None:
        block = taste.brief_block().lower()
        for section in ("references", "palette", "type", "layout", "must appear"):
            with self.subTest(section=section):
                self.assertIn(f"## {section}", block)

    def test_the_agent_is_told_the_promise_is_enforced(self) -> None:
        self.assertIn("must appear", taste.brief_block().lower())
        self.assertIn("verifier", taste.brief_block().lower())

    def test_the_counts_asked_for_are_the_counts_required(self) -> None:
        std = taste.Standard(min_must_appear=7, min_references=4)
        block = taste.brief_block(std)
        self.assertIn("at least 7", block)
        self.assertIn("at least 4", block)


class TheCommandLineAnswersWithItsExitStatus(unittest.TestCase):
    def run_check(self, brief: str | None, page: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text(page, encoding="utf-8")
            if brief is not None:
                (root / "BRIEF.md").write_text(brief, encoding="utf-8")
            return taste.main(
                ["check", str(root / "BRIEF.md"), str(root / "index.html")]
            )

    def test_a_missing_brief_fails_the_run(self) -> None:
        self.assertEqual(self.run_check(None, markup()), 1)

    def test_a_kept_promise_exits_zero(self) -> None:
        self.assertEqual(self.run_check(GOOD_BRIEF, markup()), 0)

    def test_a_broken_promise_exits_one(self) -> None:
        page = markup().replace("FSSAI 10021999000123 · ", "")
        self.assertEqual(self.run_check(GOOD_BRIEF, page), 1)

    def test_a_missing_deliverable_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BRIEF.md").write_text(GOOD_BRIEF, encoding="utf-8")
            self.assertEqual(
                taste.main(["check", str(root / "BRIEF.md"),
                            str(root / "never-written.html")]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
