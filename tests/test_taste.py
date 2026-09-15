"""The page the user rejected is the specification; the review is the rest of it.

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

None of which any *mechanical* check could have known to look for. Hence the
research pass: the agent goes and looks, writes `BRIEF.md` naming the concrete
strings real examples carry, and the verifier holds the finished work to that
list. The agent sets its own bar by looking; the standard only says how high.

The second half of this file exists because the first version of that gate was
reviewed and found to reject good work in six different ways - the quieter and
worse failure, because nobody sees a page that was never delivered. Every one
of those is pinned here by the case that triggered it:

    linked stylesheet        palette and fonts in style.css read as absent
    taste not required       a typo fix in report.html rejected for having
                             no Google font
    SVG deliverable          a logo task skipped the brief requirement entirely
    stale BRIEF.md           last task's research accepted as this one's
    hidden promise           contract satisfied inside an HTML comment
    two-page site            each page required to repeat every promise
    config edited mid-run    the bar moved under a brief already written

A gate that rejects good work is worse than no gate, so those cases are not
regressions to avoid - they are the specification.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import taste  # noqa: E402
import worker  # noqa: E402

GOOD_BRIEF = """# Brief

## References
- https://example-spice-one.in/masala - lists net weight and grind date per pack
- https://example-spice-two.in/oils - shows the FSSAI licence in the footer
- https://example-spice-three.in - prices every pack in MRP with the rupee sign

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

KEPT = ("FSSAI 10021999000123 &middot; 200 g &middot; MRP Rs 185 &middot; "
        "cold-pressed in Uttar Pradesh")


def page(body: str = "", head: str = "", style: str = "") -> str:
    """A page that clears the floor, so each test can break exactly one thing."""
    head = head if head else (
        '<link href="https://fonts.googleapis.com/css2?family=Inter" '
        'rel="stylesheet">'
    )
    style = style if style else (
        "<style>a{color:#8B2E1F}b{color:#F4E9D8}c{color:#2B2B2B}</style>"
    )
    body = body if body else (
        f"<h1>Mishwan</h1><img src='assets/logo.jpg' alt='pack'><p>{KEPT}</p>"
    )
    return f"<html><head>{head}{style}</head><body>{body}</body></html>"


class Workspace:
    """A run's output on disk, because collect() reads files, not strings."""

    def __init__(self, stack: unittest.TestCase) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        stack.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.produced: list[Path] = []

    def write(self, name: str, text: str, *, produced: bool = True) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if produced:
            self.produced.append(path)
        return path

    def binary(self, name: str, size: int = 2048) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff" * size)
        self.produced.append(path)
        return path

    def check(self, standard=None, *, required: bool = True) -> list[str]:
        work = taste.collect(self.produced, self.root)
        return taste.check_work(work, standard, required=required)


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
        # "make the header bigger in index.html" names something visual and asks
        # for it to be made. It is still a repair, and the signal is that the
        # file it names is already there.
        self.assertTrue(taste.applies_to("make the header bigger in index.html"))
        self.assertFalse(taste.applies_to(
            "make the header bigger in index.html", touches_existing=True
        ))

    def test_a_verb_inside_another_word_is_not_a_verb(self) -> None:
        # "rewrite" contains "write"; substring matching turned every rewording
        # of an existing page into a full research pass.
        self.assertFalse(taste.applies_to("rewrite the page title"))

    def test_the_standard_can_be_switched_off_entirely(self) -> None:
        off = taste.Standard(enabled=False)
        self.assertFalse(taste.applies_to("build me a landing page", off))


class ARepairIsToldApartFromABuild(unittest.TestCase):
    """The worker's half of the decision, and the one that nearly broke it.

    `intake_preflight.check().present` matches bare filenames anywhere under
    the workspace. The sandbox files every finished run under `runs/<slug>/`,
    so one past run containing `index.html` would have made every future
    "create index.html" look like a repair - and the research pass would have
    switched itself off permanently after the first task, silently, while every
    test still passed.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_file_at_the_root_is_a_repair(self) -> None:
        (self.root / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
        self.assertTrue(worker._is_repair("make the header bigger in index.html",
                                          self.root))

    def test_the_same_name_inside_a_past_run_is_not(self) -> None:
        past = self.root / "runs" / "2026-09-01-something"
        past.mkdir(parents=True)
        (past / "index.html").write_text("<h1>last week</h1>", encoding="utf-8")
        self.assertFalse(worker._is_repair("build a landing page as index.html",
                                           self.root))

    def test_a_named_subpath_still_resolves(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x = 1", encoding="utf-8")
        self.assertTrue(worker._is_repair("fix the import in src/app.py", self.root))

    def test_a_prompt_naming_nothing_is_not_a_repair(self) -> None:
        self.assertFalse(worker._is_repair("build me a landing page", self.root))


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
        self.ws = Workspace(self)
        self.ws.write("BRIEF.md", GOOD_BRIEF)

    def test_work_that_keeps_its_promises_passes(self) -> None:
        self.ws.write("index.html", page())
        self.assertEqual(self.ws.check(), [])

    def test_a_promise_made_and_not_kept_fails(self) -> None:
        self.ws.write("index.html", page(
            "<h1>Mishwan</h1><img src='a.jpg' alt='pack'>"
            "<p>200 g &middot; MRP Rs 185 &middot; cold-pressed in Uttar Pradesh</p>"
        ))
        problems = self.ws.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("FSSAI", problems[0])

    def test_a_promise_is_matched_regardless_of_case(self) -> None:
        self.ws.write("index.html", page().replace("cold-pressed", "Cold-Pressed"))
        self.assertEqual(self.ws.check(), [])

    def test_a_promise_hidden_in_a_comment_is_not_kept(self) -> None:
        self.ws.write("index.html", page(
            "<h1>Mishwan</h1><img src='a.jpg' alt='x'>"
            "<p>Fresh flavour delivered to your door.</p>"
            f"<!-- {KEPT} -->"
        ))
        problems = self.ws.check()
        self.assertEqual(len(problems), 5)
        self.assertTrue(all("not visible" in p for p in problems))

    def test_a_promise_hidden_in_a_script_is_not_kept(self) -> None:
        self.ws.write("index.html", page(
            "<h1>Mishwan</h1><img src='a.jpg' alt='x'><p>Flavour.</p>"
            f"<script>var meta = '{KEPT}';</script>"
        ))
        self.assertEqual(len(self.ws.check()), 5)

    def test_the_brief_cannot_satisfy_its_own_promises(self) -> None:
        # BRIEF.md is a .md file this run produced. Read in as prose it would
        # contain every promised string by construction, and the gate would
        # pass on a page that says nothing.
        self.ws.write("index.html", page("<h1>Mishwan</h1><img src='a.jpg' alt='x'><p>Hi.</p>"))
        self.assertEqual(len(self.ws.check()), 5)

    def test_a_promise_kept_on_any_page_of_a_site_is_kept(self) -> None:
        # A two-page site: the story page carries the place, the products page
        # carries the licence and the pack. Requiring each page to repeat every
        # promise rejected a correct site for being more than one file.
        self.ws.write("index.html", page(
            "<h1>Mishwan</h1><img src='a.jpg' alt='x'>"
            "<p>Ground in Uttar Pradesh, cold-pressed weekly.</p>"
        ))
        self.ws.write("products.html", page(
            "<h2>Packs</h2><p>FSSAI 10021999000123 &middot; 200 g &middot; MRP Rs 185</p>"
        ))
        self.assertEqual(self.ws.check(), [])

    def test_an_old_brief_is_not_this_runs_research(self) -> None:
        # The brief is on disk but this run did not produce it: it is the
        # previous task's work, and accepting it lets a run skip research.
        ws = Workspace(self)
        ws.write("BRIEF.md", GOOD_BRIEF, produced=False)
        ws.write("index.html", page())
        problems = ws.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("not written by this run", problems[0])


class HowThePageSpellsItIsNotTheTest(unittest.TestCase):
    """Ordinary good markup must not read as a broken promise."""

    def setUp(self) -> None:
        self.ws = Workspace(self)

    def promise(self, item: str, body: str) -> list[str]:
        self.ws.write("BRIEF.md", GOOD_BRIEF.replace("- FSSAI", f"- {item}"))
        self.ws.write("index.html", page(body))
        return [p for p in self.ws.check() if "promised" in p]

    def test_an_entity_renders_as_the_character_it_stands_for(self) -> None:
        # The brief promises the rupee sign; the page writes it as &#8377;.
        self.assertEqual(
            self.promise("MRP ₹185",
                         "<h1>Mishwan</h1><img src='a.jpg' alt='x'>"
                         "<p>MRP &#8377;185 &middot; 200 g &middot; cold-pressed "
                         "in Uttar Pradesh</p>"),
            [],
        )

    def test_a_line_break_inside_a_promise_is_still_the_promise(self) -> None:
        # "Net wt. 100 g" set across two lines with a <br> between them.
        self.assertEqual(
            self.promise("Net wt. 100 g",
                         "<h1>Mishwan</h1><img src='a.jpg' alt='x'><p>Net wt.<br>\n"
                         "   100 g</p><p>MRP &middot; 200 g &middot; cold-pressed "
                         "in Uttar Pradesh</p>"),
            [],
        )

    def test_a_non_breaking_space_is_a_space(self) -> None:
        self.assertEqual(
            self.promise("200 g",
                         "<h1>Mishwan</h1><img src='a.jpg' alt='x'>"
                         "<p>200&nbsp;g &middot; MRP &middot; cold-pressed in "
                         "Uttar Pradesh</p>"),
            [],
        )

    def test_a_curly_apostrophe_does_not_hide_filler(self) -> None:
        # Typographic quotes are what a real page uses, and would otherwise let
        # a banned phrase through untouched.
        self.ws.write("BRIEF.md", GOOD_BRIEF)
        self.ws.write("index.html", page(
            f"<h1>Mishwan</h1><img src='a.jpg' alt='x'><p>{KEPT}</p>"
            "<p>In today’s fast-paced world we deliver.</p>"
        ))
        self.assertTrue(any("fast-paced" in p for p in self.ws.check()))

    def test_the_brief_is_found_whatever_its_case(self) -> None:
        # Windows does not distinguish Brief.md from BRIEF.md, and an agent
        # that wrote the research has written it.
        ws = Workspace(self)
        ws.write("Brief.md", GOOD_BRIEF)
        ws.write("index.html", page())
        self.assertEqual(ws.check(), [])


class TheRunIsJudgedAsOnePieceOfWork(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspace(self)
        self.ws.write("BRIEF.md", GOOD_BRIEF)

    def test_a_linked_stylesheet_carries_the_palette_and_the_typeface(self) -> None:
        # The styling is real; it just does not live in the .html. Reading each
        # file alone reported no colours, no typeface and no imagery.
        self.ws.write("index.html",
                      '<html><head><link rel="stylesheet" href="style.css">'
                      f"</head><body><h1>Mishwan</h1><p>{KEPT}</p></body></html>")
        self.ws.write("style.css", """
            @import url("https://fonts.googleapis.com/css2?family=Fraunces");
            :root { --chilli:#8B2E1F; --cream:#F4E9D8; --ink:#2B2B2B; }
            .hero { background-image: url("hero.jpg"); }
        """)
        self.assertEqual(self.ws.check(), [])

    def test_a_shipped_photograph_is_imagery(self) -> None:
        # The run delivered a real image file. Demanding an <img> tag as well
        # fails a page that sets it from a stylesheet it also shipped.
        self.ws.write("index.html",
                      '<html><head><link rel="stylesheet" href="s.css"></head>'
                      f"<body><h1>Mishwan</h1><p>{KEPT}</p></body></html>")
        self.ws.write("s.css", "@font-face{src:url(x)} a{color:#8B2E1F}"
                               "b{color:#F4E9D8}c{color:#2B2B2B}")
        self.ws.binary("assets/logo.jpg")
        self.assertEqual(self.ws.check(), [])

    def test_a_run_with_no_page_is_not_asked_for_a_typeface(self) -> None:
        # "design a logo" produces an SVG. It has no headings and no business
        # loading a webfont, but it still has to have been researched.
        ws = Workspace(self)
        ws.write("BRIEF.md", GOOD_BRIEF)
        ws.write("logo.svg",
                 f"<svg xmlns='http://www.w3.org/2000/svg'><text>{KEPT}</text></svg>")
        self.assertEqual(ws.check(), [])

    def test_a_non_html_deliverable_still_needs_a_brief(self) -> None:
        # The first version keyed the whole gate on .html files, so a logo task
        # passed with no research at all.
        ws = Workspace(self)
        ws.write("logo.svg", "<svg xmlns='http://www.w3.org/2000/svg'></svg>")
        problems = ws.check()
        self.assertTrue(any("was not written" in p for p in problems))


class TheFloorCatchesTheObviouslyEmpty(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Workspace(self)
        self.ws.write("BRIEF.md", GOOD_BRIEF)

    def test_filler_prose_fails(self) -> None:
        self.ws.write("index.html", page(
            f"<h1>Brand</h1><img src='a.jpg' alt='x'><p>{KEPT}</p>"
            "<p>We offer a wide range of unparalleled products.</p>"
        ))
        problems = self.ws.check()
        self.assertTrue(any("wide range" in p for p in problems))
        self.assertTrue(any("unparalleled" in p for p in problems))

    def test_filler_inside_a_tag_attribute_is_not_a_finding(self) -> None:
        # Only what a reader sees counts. A class named "placeholder" is markup.
        self.ws.write("index.html", page(
            '<h1>Brand</h1><img src="a.jpg" class="placeholder" alt="x">'
            f"<p>{KEPT}</p>"
        ))
        self.assertEqual(self.ws.check(), [])

    def test_the_business_template_shape_fails(self) -> None:
        self.ws.write("index.html", page(
            "<h2>About Us</h2><h2>Why Choose Us</h2><h2>Our Mission</h2>"
            f"<img src='a.jpg' alt='x'><p>{KEPT}</p>"
        ))
        self.assertTrue(
            any("generic template headings" in p for p in self.ws.check())
        )

    def test_one_ordinary_heading_is_not_a_template(self) -> None:
        self.ws.write("index.html", page(
            f"<h2>About Us</h2><img src='a.jpg' alt='x'><p>{KEPT}</p>"
        ))
        self.assertEqual(self.ws.check(), [])

    def test_the_system_font_stack_is_a_finding(self) -> None:
        # The one thing the floor did catch on the page the user rejected.
        self.ws.write("index.html", page(head="<meta charset='utf-8'>"))
        self.assertTrue(any("typeface" in p for p in self.ws.check()))

    def test_a_page_with_no_image_at_all_is_a_finding(self) -> None:
        self.ws.write("index.html", page(f"<h1>Brand</h1><p>{KEPT}</p>"))
        self.assertTrue(any("image" in p for p in self.ws.check()))

    def test_two_colours_are_not_a_palette(self) -> None:
        self.ws.write("index.html", page(
            style="<style>a{color:#8B2E1F}b{color:#fff}</style>"
        ))
        self.assertTrue(any("palette" in p for p in self.ws.check()))


class NothingAppliesWhereResearchWasNotAsked(unittest.TestCase):
    def test_a_plain_page_from_a_repair_is_not_judged(self) -> None:
        # "fix the typo in report.html". The agent was never told to research,
        # and holding it to a standard it was not given stops the board.
        ws = Workspace(self)
        ws.write("report.html",
                 "<html><body><h1>Quarterly report</h1>"
                 "<p>The corrected sentence.</p></body></html>")
        self.assertEqual(ws.check(required=False), [])

    def test_disabling_the_standard_disables_the_gate_and_not_just_the_prompt(self) -> None:
        # `enabled: false` has to reach the verifier too. It reaches it as
        # required=False, because the worker is the one that reads the config.
        off = taste.Standard(enabled=False)
        self.assertFalse(taste.applies_to("build a landing page", off))
        ws = Workspace(self)
        ws.write("index.html", "<html><body>nothing here</body></html>")
        self.assertEqual(ws.check(off, required=False), [])


class TheBarIsFrozenWhenTheAgentIsSetIt(unittest.TestCase):
    def test_a_snapshot_survives_a_config_edited_mid_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agreed = taste.Standard(min_must_appear=5, min_references=3)
            frozen = taste.snapshot(agreed, root / "taste.json")

            # The operator raises the bar while the agent is still working.
            (root / "live.json").write_text(
                json.dumps({"min_must_appear": 9}), encoding="utf-8")

            self.assertEqual(taste.load(root / "live.json").min_must_appear, 9)
            self.assertEqual(taste.from_snapshot(frozen).min_must_appear, 5)
            self.assertEqual(
                taste.check_brief(taste.parse_brief(GOOD_BRIEF),
                                  taste.from_snapshot(frozen)),
                [],
            )

    def test_a_snapshot_carries_the_phrase_lists_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom = taste.Standard(banned_phrases=("artisanal journey",))
            frozen = taste.snapshot(custom, Path(tmp) / "taste.json")
            self.assertEqual(
                taste.from_snapshot(frozen).banned_phrases, ("artisanal journey",)
            )

    def test_an_unreadable_snapshot_falls_back_rather_than_stopping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "taste.json"
            broken.write_text("{ not json", encoding="utf-8")
            standard = taste.from_snapshot(broken)
            self.assertIsNotNone(standard.config_error)
            self.assertTrue(standard.enabled)


class ConfigurationCannotStopTheBoard(unittest.TestCase):
    def test_an_absent_file_means_the_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            standard = taste.load(Path(tmp) / "nothing.json")
            self.assertIsNone(standard.config_error)
            self.assertEqual(standard.banned_phrases, taste.BANNED_PHRASES)

    def test_a_malformed_file_falls_back_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "taste.json"
            bad.write_text("{ not json", encoding="utf-8")
            standard = taste.load(bad)
            self.assertIsNotNone(standard.config_error)
            self.assertEqual(standard.banned_phrases, taste.BANNED_PHRASES)
            self.assertTrue(standard.enabled)

    def test_an_operator_can_raise_the_bar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "taste.json"
            cfg.write_text(
                json.dumps({"min_must_appear": 9, "min_references": 5}),
                encoding="utf-8")
            standard = taste.load(cfg)
            self.assertEqual(standard.min_must_appear, 9)
            self.assertEqual(
                len(taste.check_brief(taste.parse_brief(GOOD_BRIEF), standard)), 2
            )

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

    def test_the_agent_is_told_promises_must_be_visible(self) -> None:
        block = taste.brief_block().lower()
        self.assertIn("reader can see", block)
        self.assertIn("not in a comment", block)

    def test_the_agent_is_told_the_run_is_judged_as_a_whole(self) -> None:
        block = taste.brief_block().lower()
        self.assertIn("linked stylesheet", block)

    def test_the_brief_is_asked_for_at_an_exact_path(self) -> None:
        # Run 60 of t_7fbf6644 did the research, built a real page with a
        # stylesheet, a script and the user's logo - and wrote the brief into
        # the home directory. "in this workspace" was read two ways on a turn
        # with nobody to ask, and good work was rejected for it.
        block = taste.brief_block(workspace=r"C:\somewhere\sandbox")
        self.assertIn(r"C:\somewhere\sandbox\BRIEF.md", block)
        self.assertIn("not in your home directory", block)

    def test_without_a_workspace_it_still_says_where(self) -> None:
        block = taste.brief_block()
        self.assertIn("BRIEF.md", block)
        self.assertIn("not in your home directory", block)

    def test_the_counts_asked_for_are_the_counts_required(self) -> None:
        standard = taste.Standard(min_must_appear=7, min_references=4)
        block = taste.brief_block(standard)
        self.assertIn("at least 7", block)
        self.assertIn("at least 4", block)


class TheCommandLineAnswersWithItsExitStatus(unittest.TestCase):
    def run_check(self, files: dict[str, str], *, extra: list[str] | None = None) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            produced = []
            for name, text in files.items():
                (root / name).write_text(text, encoding="utf-8")
                produced.append(str(root / name))
            return taste.main(
                ["check", str(root), *produced] + (extra or [])
            )

    def test_a_missing_brief_fails_the_run(self) -> None:
        self.assertEqual(self.run_check({"index.html": page()}), 1)

    def test_a_kept_promise_exits_zero(self) -> None:
        self.assertEqual(
            self.run_check({"BRIEF.md": GOOD_BRIEF, "index.html": page()}), 0
        )

    def test_a_broken_promise_exits_one(self) -> None:
        self.assertEqual(
            self.run_check({
                "BRIEF.md": GOOD_BRIEF,
                "index.html": page().replace("FSSAI 10021999000123 &middot; ", ""),
            }),
            1,
        )

    def test_not_required_exits_zero_on_a_bare_page(self) -> None:
        self.assertEqual(
            self.run_check({"index.html": "<html><body>bare</body></html>"},
                           extra=["--not-required"]),
            0,
        )

    def test_a_missing_deliverable_is_not_counted_as_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BRIEF.md").write_text(GOOD_BRIEF, encoding="utf-8")
            self.assertEqual(
                taste.main(["check", str(root),
                            str(root / "BRIEF.md"),
                            str(root / "never-written.html")]),
                1,
            )


if __name__ == "__main__":
    unittest.main()
