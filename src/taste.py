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
     list of concrete strings real examples in this field carry.

  2. BUILD, and the verifier holds the deliverable to that list.

The second beat is why this is not merely a longer prompt. A prompt is a wish;
"Must appear" is a contract the agent wrote itself and the gate enforces. An
agent that researches and then ignores its own findings fails, the same way an
agent that describes a file instead of writing one fails.

What a gate must not do
-----------------------
Reject work that should have passed. That failure is quieter than the other
one - nobody sees a page that was never delivered - and on an unattended board
it just looks like the system stopped working. Review of the first version
found six ways it could happen, and every rule here is shaped by them:

  - The run is judged as ONE piece of work, never file by file. A page whose
    colours and fonts live in a linked stylesheet is styled; a two-page site
    that carries its licence number on the products page has carried it. The
    first version read each .html alone and failed both.
  - Nothing is checked at all unless the worker asked for research on this
    task. The floor is part of the standard, not a separate opinion about
    every HTML file that happens to exist, and `"enabled": false` therefore
    switches off the gate and not merely the prompt.
  - The brief has to be THIS run's. An old BRIEF.md left in the workspace
    satisfied the first version completely.
  - Promises are matched against what a reader can see - not comments, not
    script bodies, not attributes. A contract satisfied inside an HTML comment
    is not satisfied.

And the standard the agent was given is the standard it is judged by: the
worker snapshots it and the verifier reads the snapshot, so editing
~/.tri-ai/taste.json while a task is running cannot move the bar underneath it.

Configuration lives in ~/.tri-ai/taste.json. A malformed file falls back to the
defaults with the reason recorded, because a typo in a preferences file must
never be able to stop every task on the board.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

CONFIG_PATH = Path.home() / ".tri-ai" / "taste.json"
BRIEF_FILENAME = "BRIEF.md"
MUST_APPEAR_HEADING = "must appear"

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

# Any two of these across the run is the generic-business-template shape. One
# is ordinary; the set is the tell.
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

PAGE_SUFFIXES = frozenset({".html", ".htm"})
STYLE_SUFFIXES = frozenset({".css"})
# Read for promises and filler, but never judged as a page: an SVG logo has no
# business being asked for a Google font.
PROSE_SUFFIXES = frozenset({".svg", ".md", ".txt", ".json", ".csv"})
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".svg", ".webp", ".gif", ".avif", ".pdf"}
)
# A deliverable larger than this is not prose, whatever its extension says.
MAX_READ_BYTES = 4 * 1024 * 1024

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HIDDEN_BLOCK = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_HEADING_TEXT = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_TYPEFACE = re.compile(r"fonts\.googleapis\.com|@font-face", re.I)
_IMAGERY = re.compile(r"<img\b|<svg\b|<picture\b|background-image\s*:", re.I)
_BULLET = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*\S)\s*$")
_URL = re.compile(r"https?://\S+")
# Curly quotes and the non-breaking space, folded to what a person types.
_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", "‑": "-", "–": "-", "—": "-",
})


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

    def to_json(self) -> str:
        """Freeze this standard so the gate judges by the bar the agent was set."""
        return json.dumps(
            {k: (list(v) if isinstance(v, tuple) else v)
             for k, v in asdict(self).items() if k != "config_error"},
            indent=2,
        )


@dataclass(frozen=True)
class Brief:
    """The agent's own findings, parsed back out of BRIEF.md."""

    must_appear: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    text: str = ""


@dataclass(frozen=True)
class Work:
    """Everything one run produced, read as one piece of work.

    Split by role rather than by file, because the checks are about the
    finished thing: colours and typefaces may live in a linked stylesheet, and
    a promise kept on the products page is a promise kept.
    """

    pages: tuple[str, ...] = ()      # .html / .htm contents
    styles: tuple[str, ...] = ()     # .css contents
    prose: tuple[str, ...] = ()      # .svg / .md / .txt contents
    names: tuple[str, ...] = ()      # every file this run produced
    brief: Optional[Brief] = None
    brief_is_this_runs: bool = False


def _tuple_of_text(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of strings")
    out = tuple(str(v).strip().lower() for v in value if str(v).strip())
    return out or fallback


def _from_mapping(raw: Mapping[str, Any]) -> Standard:
    base = Standard()
    return Standard(
        banned_phrases=_tuple_of_text(
            raw.get("banned_phrases", list(base.banned_phrases)),
            base.banned_phrases),
        generic_headings=_tuple_of_text(
            raw.get("generic_headings", list(base.generic_headings)),
            base.generic_headings),
        visual_markers=_tuple_of_text(
            raw.get("visual_markers", list(base.visual_markers)),
            base.visual_markers),
        max_generic_headings=int(
            raw.get("max_generic_headings", base.max_generic_headings)),
        min_palette=int(raw.get("min_palette", base.min_palette)),
        require_typeface=bool(raw.get("require_typeface", base.require_typeface)),
        require_imagery=bool(raw.get("require_imagery", base.require_imagery)),
        min_must_appear=int(raw.get("min_must_appear", base.min_must_appear)),
        min_references=int(raw.get("min_references", base.min_references)),
        enabled=bool(raw.get("enabled", base.enabled)),
    )


def load(path: Optional[Path | str] = None) -> Standard:
    """Read the taste standard. An absent file means the defaults, which stand
    on their own - the file exists to disagree with them, not to enable them."""
    target = Path(path) if path is not None else CONFIG_PATH
    if not target.exists():
        return Standard()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("top level must be an object")
        return _from_mapping(raw)
    except Exception as exc:  # noqa: BLE001 - every malformed shape lands here
        return replace(Standard(), config_error=f"{target}: {exc}")


def snapshot(standard: Standard, path: Path | str) -> Path:
    """Write the standard the agent was set, for the verifier to judge by."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(standard.to_json(), encoding="utf-8")
    return target


def from_snapshot(path: Path | str) -> Standard:
    """Read back a frozen standard. A broken snapshot is not a reason to stop."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("top level must be an object")
        return _from_mapping(raw)
    except Exception as exc:  # noqa: BLE001
        return replace(load(), config_error=f"snapshot {target}: {exc}")


def _mentions(text: str, terms: Iterable[str]) -> bool:
    """Whole-word match, so "rewrite" is not "write" and "cvs" is not "cv"."""
    words = [re.escape(t) for t in terms if t]
    if not words:
        return False
    return re.search(r"\b(?:" + "|".join(words) + r")\b", text) is not None


def applies_to(
    prompt: str,
    standard: Optional[Standard] = None,
    *,
    touches_existing: bool = False,
) -> bool:
    """Does this request produce something a person looks at?

    Three conditions. The request has to name something visual, it has to ask
    for that thing to be *made*, and it must not be a repair: work that names
    files already in the workspace inherits the taste on disk, and sending an
    agent to research the field before adjusting a heading invites it to
    redesign what it was asked only to touch.
    """
    std = standard or Standard()
    if not std.enabled or touches_existing:
        return False
    text = (prompt or "").lower()
    return _mentions(text, std.visual_markers) and _mentions(text, MAKE_VERBS)


def brief_block(
    standard: Optional[Standard] = None,
    *,
    workspace: Optional[Path | str] = None,
) -> str:
    """The instruction that turns one beat into two.

    ``workspace`` is named rather than implied. Run 60 of ``t_7fbf6644`` read
    "write BRIEF.md in this workspace", did the research properly, built a real
    page with a stylesheet, a script and the user's logo - and wrote the brief
    into the home directory. The gate then rejected good work over a file that
    had been written one directory away. An instruction that can be read two
    ways will be, on an unattended turn, with nobody to ask.
    """
    std = standard or Standard()
    where = (
        f"at this exact path: {Path(workspace) / BRIEF_FILENAME}"
        if workspace is not None else
        f"as {BRIEF_FILENAME} in the workspace directory you were told to cd into"
    )
    return f"""

BEFORE YOU BUILD ANYTHING - research, then write the brief.

You have web tools. Use them first. Find at least {std.min_references} real,
existing examples of this exact kind of thing, made by people in this exact
field, and look at what they actually contain: the units they quote, the
certifications they display, the words of the trade, what a photograph is of,
what a buyer needs to see before deciding. Keep the URLs.

Then write the brief {where} — not in your home directory, not anywhere else —
with these headings:

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
  bullet is searched for in what a READER CAN SEE - not in a comment, not in
  an attribute, not in a script. Write it exactly as it should appear.

  Promise the MARKS OF THE FIELD, not values you invent for this page. "Rs"
  and "500 g" and "FSSAI" are marks; "Rs 1240.00" is a number you made up and
  will probably price differently once you are building. If you do change
  something while you build, edit the brief so the two agree before you stop -
  it is your brief, and it is read after you finish, not before.

Then build the thing, to that brief.

The verifier reads everything this run produces as one piece of work, so a
promise kept on any page of a site is kept, and colours and typefaces may live
in a linked stylesheet. What it will not accept is a promise you wrote and did
not keep. Write bullets you mean.

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


def visible_text(markup: str) -> str:
    """What a reader can actually see.

    Comments, script bodies and style bodies come out first, then tags. A
    promise satisfied inside `<!-- ... -->` is not satisfied, and neither is
    one hidden in a class name.
    """
    stripped = _COMMENT.sub(" ", markup or "")
    stripped = _HIDDEN_BLOCK.sub(" ", stripped)
    return _TAG.sub(" ", stripped)


def _visible_promise(item: str, visible: str) -> bool:
    """Is this promise on the page, allowing for how a page sets its spacing?

    Whitespace inside a promise is treated as optional, so a brief that wrote
    "500 g" is kept by a page that sets "500g". Spacing between a number and
    its unit is a typesetting choice and never a semantic one; rejecting good
    work over it is the gate failing, not the page.
    """
    wanted = normalize(item)
    if not wanted:
        return True
    if wanted in visible:
        return True
    pattern = r"\s*".join(re.escape(part) for part in wanted.split(" ") if part)
    return bool(pattern) and re.search(pattern, visible) is not None


def normalize(text: str) -> str:
    """Compare what the reader reads, not how the file spells it.

    Three things a page does that a naive substring match would call a broken
    promise, all of them ordinary good markup:

      entities   `MRP &#8377;185` renders as `MRP RS-SIGN 185`, and a brief
                 that promised the rendered form would not find it.
      layout     `Net wt.<br>100 g` becomes `Net wt.  100 g` once the tag is
                 gone - a newline and two spaces where the brief wrote one.
      typography `&nbsp;` unescapes to U+00A0, and a curly apostrophe is not
                 the apostrophe anyone types into a bullet.

    Rejecting work over any of those would be the gate failing, not the page.
    """
    text = html.unescape(text or "")
    text = text.translate(_TYPOGRAPHY)
    return re.sub(r"\s+", " ", text).strip().lower()


def collect(paths: Sequence[Path], root: Optional[Path] = None) -> Work:
    """Read one run's output into the shape the checks want.

    Unreadable or oversized files are counted by name and not by content: a
    3 MB photograph is imagery, and trying to decode it proves nothing.
    """
    pages: list[str] = []
    styles: list[str] = []
    prose: list[str] = []
    names: list[str] = []
    brief: Optional[Brief] = None
    brief_is_this_runs = False

    for path in paths:
        try:
            name = str(path.relative_to(root)) if root else path.name
        except ValueError:
            name = path.name
        names.append(name)

        suffix = path.suffix.lower()
        readable = suffix in PAGE_SUFFIXES | STYLE_SUFFIXES | PROSE_SUFFIXES
        if not readable:
            continue
        try:
            if path.stat().st_size > MAX_READ_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # The brief is the contract, never the evidence. Reading it in as prose
        # would let every promise satisfy itself: the bullets are in the file.
        if path.name.lower() == BRIEF_FILENAME.lower():
            brief = parse_brief(text)
            brief_is_this_runs = True
            continue

        if suffix in PAGE_SUFFIXES:
            pages.append(text)
        elif suffix in STYLE_SUFFIXES:
            styles.append(text)
        else:
            prose.append(text)

    # A brief this run did not produce still has to be found, so the gate can
    # say *why* it is refusing rather than reporting it simply missing.
    if brief is None and root is not None:
        candidate = Path(root) / BRIEF_FILENAME
        if candidate.exists():
            brief = parse_brief(
                candidate.read_text(encoding="utf-8", errors="replace")
            )

    return Work(
        pages=tuple(pages), styles=tuple(styles), prose=tuple(prose),
        names=tuple(names), brief=brief,
        brief_is_this_runs=brief_is_this_runs,
    )


def promises(work: Work) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(kept, unmet) from this run's own brief, for reporting."""
    if work.brief is None or not work.brief_is_this_runs:
        return (), ()
    visible = _visible_of(work)
    kept = tuple(i for i in work.brief.must_appear if _visible_promise(i, visible))
    unmet = tuple(i for i in work.brief.must_appear if i not in kept)
    return kept, unmet


def _visible_of(work: Work) -> str:
    """Everything a reader can see across the whole run, normalized once."""
    return normalize(" ".join(
        [visible_text(page) for page in work.pages] + list(work.prose)
    ))


def check_work(
    work: Work,
    standard: Optional[Standard] = None,
    *,
    required: bool = True,
) -> list[str]:
    """Hold one run's whole output to the brief, and to the floor beneath it.

    ``required`` is the worker's decision, carried down rather than re-derived.
    When it is false nothing here applies: the agent was never asked to
    research, and holding it to a standard it was not given is the failure mode
    that stops a board without explaining itself.
    """
    std = standard or Standard()
    if not required:
        return []

    problems: list[str] = []

    if work.brief is None:
        problems.append(
            f"{BRIEF_FILENAME} was not written - the research pass was skipped, "
            "so nothing here was built against a reference"
        )
    elif not work.brief_is_this_runs:
        problems.append(
            f"{BRIEF_FILENAME} was not written by this run - it was already in "
            "the workspace, so it is the previous task's research"
        )
    else:
        problems.extend(check_brief(work.brief, std))

    # One piece of work, not a pile of files.
    visible = _visible_of(work)
    markup = "\n".join(work.pages + work.styles)

    for phrase in std.banned_phrases:
        if normalize(phrase) in visible:
            problems.append(f"filler phrase present: {phrase!r}")

    # How many of its own promises the work kept.
    #
    # Not all of them, and the reason is a run that was thrown away for it. Run
    # 62 of t_d43a762a researched properly, copied the user's logo, and built a
    # 25 KB page carrying FSSAI, ISO 22000, Halal, Kosher, Meerut, Uttar Pradesh
    # and a rupee sign - ten of its twelve promises. It was rejected for two:
    # "500 g", which the page wrote as "500g", and "Rs 1240.00", a price the
    # agent invented before building and then priced differently.
    #
    # A brief is research, not a specification. The page has to carry the marks
    # of its field; it does not have to match every bullet an agent guessed at
    # before it started. So the bar is the same one the brief itself had to
    # clear - keep at least `min_must_appear` of them - and the rest are
    # reported without failing the run, because an agent that promises twelve
    # and keeps ten has done the work, while one that keeps two has not.
    unmet: list[str] = []
    if work.brief is not None and work.brief_is_this_runs:
        promises = work.brief.must_appear
        unmet = [item for item in promises if not _visible_promise(item, visible)]
        kept = len(promises) - len(unmet)
        if promises and kept < min(std.min_must_appear, len(promises)):
            problems.append(
                f"the page keeps {kept} of its own {len(promises)} promises, "
                f"needs {min(std.min_must_appear, len(promises))} — "
                "the research was done and then ignored"
            )
            problems.extend(
                f"promised but not visible on the page: {item!r}" for item in unmet
            )

    # The floor is about pages. A run that produced no page has none to fail.
    if not work.pages:
        return problems

    headings = [
        _TAG.sub("", found).strip().lower()
        for page in work.pages
        for found in _HEADING_TEXT.findall(page)
    ]
    generic = sorted({
        candidate for candidate in std.generic_headings
        if any(candidate in heading for heading in headings)
    })
    if len(generic) > std.max_generic_headings:
        problems.append("generic template headings: " + ", ".join(generic))

    palette = {match.group(0).lower() for match in _HEX.finditer(markup)}
    if len(palette) < std.min_palette:
        problems.append(
            f"palette has {len(palette)} colour(s), needs {std.min_palette}"
        )

    if std.require_typeface and not _TYPEFACE.search(markup):
        problems.append("no typeface loaded - falls back to a system font")

    if std.require_imagery:
        shipped_image = any(
            Path(name).suffix.lower() in IMAGE_SUFFIXES for name in work.names
        )
        if not shipped_image and not _IMAGERY.search(markup):
            problems.append("no image, svg or background-image anywhere")

    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python taste.py check <dir> <file>...`` - exit 1 on findings.

    Written to be called from a verify_command: it says what is wrong on
    stdout and answers with its exit status. Nothing else.
    """
    parser = argparse.ArgumentParser(description="Check work against its brief.")
    parser.add_argument("command", choices=["check"])
    parser.add_argument("root", help="the workspace this run produced into")
    parser.add_argument("produced", nargs="+", help="what this run produced")
    parser.add_argument("--config", default=None)
    parser.add_argument("--snapshot", default=None)
    parser.add_argument(
        "--not-required", action="store_true",
        help="the worker did not ask for research on this task",
    )
    args = parser.parse_args(argv)

    std = from_snapshot(args.snapshot) if args.snapshot else load(args.config)
    if std.config_error:
        print(f"  taste config ignored ({std.config_error}); defaults used")

    root = Path(args.root)
    work = collect([Path(p) for p in args.produced], root)
    problems = check_work(work, std, required=not args.not_required)

    if problems:
        print(f"  FAIL: {len(problems)} finding(s)")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    kept, unmet = promises(work)
    seen = len(work.brief.references) if work.brief else 0
    print(f"  OK: kept {len(kept)} of {len(kept) + len(unmet)} promise(s) "
          f"from {seen} reference(s)")
    for item in unmet:
        print(f"    promised and not on the page: {item!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
