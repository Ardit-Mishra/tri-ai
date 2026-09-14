"""Make the thing look like it was made for its subject.

A model asked to "build a landing page for a spice brand" will write a landing
page. It will not write a *spice* landing page, because nothing in the request
tells it what real spice brands put on a page - pack sizes in grams, an FSSAI
licence number, a grind date, single-origin claims, MRP in rupees - and a model
with no reference reaches for the median web page it has seen a million times:
a hero, three cards, "Why Choose Us", stock adjectives, no photograph.

So the work happens in two beats instead of one.

  1. RESEARCH. The agent has web tools. Before it writes anything it looks at
     what real examples of this thing actually contain, and writes BRIEF.md:
     palette, typefaces, layout, and - the part with teeth - a "Must appear"
     list of concrete things real examples in this field carry.

  2. BUILD, and the verifier holds the deliverable to that list.

The second beat is why this is not merely a longer prompt. A prompt is a wish;
"Must appear" is a contract the agent wrote itself and the gate enforces. An
agent that researches and then ignores its own findings fails, the same way an
agent that describes a file instead of writing one fails.

The banned-phrase list is the other half: the tells of text written to fill a
space rather than to say something. They are matched literally and case-folded,
never fuzzily - a check that guesses would start rejecting real writing, and a
gate that rejects good work is worse than no gate at all.

Configuration lives in ~/.tri-ai/taste.json. A malformed file falls back to the
defaults with the reason recorded, because a typo in a preferences file must
never be able to stop every task on the board.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CONFIG_PATH = Path.home() / ".tri-ai" / "taste.json"

# Phrases that appear when a model is filling a shape rather than saying
# something. Deliberately literal: each is a phrase a person writing about
# their own product would not reach for.
BANNED_PHRASES: tuple[str, ...] = (
    "lorem ipsum",
    "your text here",
    "your company name",
    "placeholder",
    "example.com",
    "in today's fast-paced world",
    "to the next level",
    "we offer a wide range",
    "wide range of products",
    "unparalleled",
    "seamlessly",
    "elevate your",
    "cutting-edge solutions",
    "one-stop shop",
    "best in class",
    "world-class quality",
    "customer satisfaction is our",
    "product 1",
    "product 2",
    "item 1",
    "tbd",
)

# Any two of these on one page is the generic-business-template shape. One is
# ordinary; the set is the tell.
GENERIC_HEADINGS: tuple[str, ...] = (
    "about us",
    "why choose us",
    "our services",
    "our mission",
    "what we do",
    "our values",
    "get in touch",
)

# Requests that produce something a person LOOKS at. A script or a data fix has
# no taste to get wrong, and must not pay for a research pass.
VISUAL_MARKERS: tuple[str, ...] = (
    "landing", "page", "website", "web site", "site", "homepage", "home page",
    "store", "shop", "storefront", "portfolio", "brochure", "poster", "flyer",
    "menu", "deck", "slides", "presentation", "resume", "cv", "dashboard",
    "ui", "interface", "app", "html", "logo", "brand", "invitation", "card",
    "newsletter", "blog", "profile",
)

MAKE_VERBS: tuple[str, ...] = (
    "build", "make", "create", "design", "write", "generate", "produce",
    "put together", "mock up", "draft",
)

MUST_APPEAR_HEADING = "must appear"
BRIEF_FILENAME = "BRIEF.md"

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_TAG = re.compile(r"<[^>]+>")
_HEADING_TEXT = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_TYPEFACE = re.compile(r"fonts\.googleapis\.com|@font-face", re.I)
_IMAGERY = re.compile(r"<img\b|<svg\b|<picture\b|background-image\s*:", re.I)
_BULLET = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Standard:
    """What "made for its subject" means, in checkable terms."""

    banned_phrases: tuple[str, ...] = BANNED_PHRASES
    generic_headings: tuple[str, ...] = GENERIC_HEADINGS
    visual_markers: tuple[str, ...] = VISUAL_MARKERS
    max_generic_headings: int = 1
    min_palette: int = 3
    require_typeface: bool = True
    require_imagery: bool = True
    min_must_appear: int = 5
    min_references: int = 3
    enabled: bool = True
    # Set when a config file was present but could not be used. Carried rather
    # than raised: the defaults are always serviceable, and a task must not die
    # of a preferences typo.
    config_error: Optional[str] = None


@dataclass(frozen=True)
class Brief:
    """The agent's own findings, parsed back out of BRIEF.md."""

    must_appear: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    text: str = ""


def _tuple_of_text(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of strings")
    out = tuple(str(v).strip().lower() for v in value if str(v).strip())
    return out or fallback


def load(path: Optional[Path | str] = None) -> Standard:
    """Read the taste standard. An absent file means the defaults, which stand
    on their own - the file exists to disagree with them, not to enable them."""
    target = Path(path) if path is not None else CONFIG_PATH
    if not target.exists():
        return Standard()
    base = Standard()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("top level must be an object")
        return Standard(
            banned_phrases=_tuple_of_text(
                raw.get("banned_phrases", list(base.banned_phrases)),
                base.banned_phrases,
            ),
            generic_headings=_tuple_of_text(
                raw.get("generic_headings", list(base.generic_headings)),
                base.generic_headings,
            ),
            visual_markers=_tuple_of_text(
                raw.get("visual_markers", list(base.visual_markers)),
                base.visual_markers,
            ),
            max_generic_headings=int(
                raw.get("max_generic_headings", base.max_generic_headings)),
            min_palette=int(raw.get("min_palette", base.min_palette)),
            require_typeface=bool(
                raw.get("require_typeface", base.require_typeface)),
            require_imagery=bool(
                raw.get("require_imagery", base.require_imagery)),
            min_must_appear=int(
                raw.get("min_must_appear", base.min_must_appear)),
            min_references=int(raw.get("min_references", base.min_references)),
            enabled=bool(raw.get("enabled", base.enabled)),
        )
    except Exception as exc:  # noqa: BLE001 - every malformed shape lands here
        return replace(base, config_error=f"{target}: {exc}")


def applies_to(prompt: str, standard: Optional[Standard] = None) -> bool:
    """Does this request produce something a person looks at?

    Both halves are required. "fix the spacing in report.html" names a visual
    thing but asks for a repair, and repairs inherit the taste already on disk -
    a research pass there would invite the agent to redesign what it was asked
    only to touch.
    """
    std = standard or Standard()
    if not std.enabled:
        return False
    text = (prompt or "").lower()
    if not any(marker in text for marker in std.visual_markers):
        return False
    return any(verb in text for verb in MAKE_VERBS)


def brief_block(standard: Optional[Standard] = None) -> str:
    """The instruction that turns one beat into two."""
    std = standard or Standard()
    return f"""

BEFORE YOU BUILD ANYTHING - research, then write the brief.

You have web tools. Use them first. Find at least {std.min_references} real,
existing examples of this exact kind of thing, made by people in this exact
field, and look at what they actually contain: the units they quote, the
certifications they display, the words of the trade, what a photograph is of,
what a buyer needs to see before deciding. Keep the URLs.

Then write {BRIEF_FILENAME} in this workspace, with these headings:

  ## References
  The URLs you looked at, one per line, and one sentence each on what that
  example does that a generic page would not.

  ## Palette
  4-6 colours as hex values, chosen for THIS subject.

  ## Type
  Two typefaces by name - a display face and a body face - each with a real
  fallback stack. Load them from fonts.googleapis.com.

  ## Layout
  Two sentences on the structure, specific to what this thing is for.

  ## Must appear
  A bullet list of at least {std.min_must_appear} concrete, literal strings
  that must be present in the finished work because real examples in this
  field carry them: units, pack sizes, prices with their currency symbol,
  certifications, place names, terms of the trade. Not adjectives. Each
  bullet is searched for in the finished file - write it exactly as it
  should appear there.

Then build the thing, to that brief.

The verifier checks the finished work against your own "Must appear" list, so a
bullet you write and then ignore fails the run. Write bullets you mean.

Do not write: {", ".join(std.banned_phrases[:8])}, or any phrase of that kind.
Words that would sit equally well on a page about anything are words about
nothing. Every claim names something that is true of THIS subject.
"""


def parse_brief(text: str) -> Brief:
    """Pull the checkable parts back out of BRIEF.md."""
    must: list[str] = []
    refs: list[str] = []
    sections: list[str] = []
    current = ""
    for line in (text or "").splitlines():
        heading = _MD_HEADING.match(line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.append(current)
            continue
        bullet = _BULLET.match(line)
        if current.startswith(MUST_APPEAR_HEADING) and bullet:
            must.append(bullet.group(1).strip())
        if current.startswith("reference"):
            refs.extend(url.rstrip(").,") for url in _URL.findall(line))
    return Brief(
        must_appear=tuple(must),
        references=tuple(dict.fromkeys(refs)),
        sections=tuple(sections),
        text=text or "",
    )


def check_brief(brief: Brief, standard: Optional[Standard] = None) -> list[str]:
    """Is the brief itself worth enforcing?"""
    std = standard or Standard()
    problems: list[str] = []
    if len(brief.must_appear) < std.min_must_appear:
        problems.append(
            f"brief: 'Must appear' lists {len(brief.must_appear)} item(s), "
            f"needs at least {std.min_must_appear}"
        )
    if len(brief.references) < std.min_references:
        problems.append(
            f"brief: {len(brief.references)} reference URL(s), "
            f"needs at least {std.min_references}"
        )
    for needed in ("palette", "type", "layout"):
        if not any(section.startswith(needed) for section in brief.sections):
            problems.append(f"brief: no '{needed}' section")
    return problems


def _visible_text(markup: str) -> str:
    return _TAG.sub(" ", markup or "")


def check_deliverable(
    markup: str,
    brief: Optional[Brief] = None,
    standard: Optional[Standard] = None,
) -> list[str]:
    """Hold the finished work to the brief, and to the floor beneath it."""
    std = standard or Standard()
    problems: list[str] = []
    folded = (markup or "").lower()
    visible = _visible_text(markup).lower()

    for phrase in std.banned_phrases:
        if phrase in visible:
            problems.append(f"filler phrase present: {phrase!r}")

    headings = [
        _TAG.sub("", found).strip().lower()
        for found in _HEADING_TEXT.findall(markup or "")
    ]
    generic = sorted({
        candidate for candidate in std.generic_headings
        if any(candidate in heading for heading in headings)
    })
    if len(generic) > std.max_generic_headings:
        problems.append("generic template headings: " + ", ".join(generic))

    palette = {match.group(0).lower() for match in _HEX.finditer(markup or "")}
    if len(palette) < std.min_palette:
        problems.append(
            f"palette has {len(palette)} colour(s), needs {std.min_palette}"
        )

    if std.require_typeface and not _TYPEFACE.search(markup or ""):
        problems.append("no typeface loaded - falls back to a system font")

    if std.require_imagery and not _IMAGERY.search(markup or ""):
        problems.append("no image, svg or background-image anywhere")

    if brief is not None:
        for item in brief.must_appear:
            if item.lower() not in folded:
                problems.append(f"brief promised but missing: {item!r}")

    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python taste.py check BRIEF.md index.html`` - exit 1 on findings.

    Written to be called from a verify_command: it says what is wrong on
    stdout and answers with its exit status. Nothing else.
    """
    parser = argparse.ArgumentParser(description="Check work against its brief.")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("brief", help=f"path to {BRIEF_FILENAME}")
    parser.add_argument("deliverable", nargs="+", help="files to hold to it")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)

    std = load(args.config)
    if std.config_error:
        print(f"  taste.json ignored ({std.config_error}); using defaults")

    brief_path = Path(args.brief)
    if not brief_path.exists():
        print(f"  FAIL: {brief_path} does not exist - research was skipped")
        return 1
    brief = parse_brief(brief_path.read_text(encoding="utf-8", errors="replace"))

    problems = check_brief(brief, std)
    for path in (Path(p) for p in args.deliverable):
        if not path.exists():
            problems.append(f"{path}: does not exist")
            continue
        problems.extend(
            f"{path}: {finding}"
            for finding in check_deliverable(
                path.read_text(encoding="utf-8", errors="replace"), brief, std
            )
        )

    if problems:
        print(f"  FAIL: {len(problems)} finding(s)")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(f"  OK: held to {len(brief.must_appear)} promise(s) "
          f"from {len(brief.references)} reference(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
