"""Read what the operator meant, before anything is dispatched.

`telegram_control` has always put the operator's raw text straight into
`payload["prompt"]` and titled the task `"Telegram: <workspace>"`. Nothing ever
read the message. That works for a request that arrives fully specified - "a
Diwali pamphlet" - and fails for everything else, because a dictated,
half-finished sentence is handed to an agent as its instructions and the
misreading only surfaces six minutes later in the artifact.

This module is the missing turn. Text in, a `Reading` out, carrying a
restatement the operator can correct *before* the work starts. Being wrong out
loud in two seconds beats being wrong silently in six minutes, so the
restatement matters more here than the classification does.

Three properties are load-bearing, and `tests/test_interpreter.py` holds each
one down:

  **It degrades to rules.** The completer is injected and defaults to absent,
  so the offline path is the *default* path rather than an error branch that
  only runs when something is broken. Tier L is normally up and makes this
  sharper, but nothing here waits on a socket to answer.

  **It never invents an intent.** A label the module does not declare is
  refused and the whole model reading is dropped. Passing an unrecognised
  string to the board would mean dispatching on garbage.

  **A build always carries a brief.** An empty prompt reaching an agent is
  exactly the "produced nothing" outcome already sitting in the ledger, so an
  empty brief falls back to the operator's own words rather than shipping.

One asymmetry is deliberate: the model may *raise* the external flag but never
clear one the rules raised. Text arriving here is untrusted - it is whatever
was typed, dictated, or pasted from somewhere else - and a message that can
argue its own way past the send/spend/publish gate is a gate that does not
hold.

Pure over text. It opens nothing, and the completer is the only way out.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

Completer = Callable[[str], str]
# Returns one label from INTENTS, or None when it has nothing to say.
Classifier = Callable[[str], Optional[str]]

# Intents that need no text written, so they need no generative model. This is
# the entire reason a fast classifier earns its place: most phone traffic is
# greetings, questions and stops.
NO_BRIEF_NEEDED = frozenset({"chat", "stop", "ask"})

# `unclear` is a real outcome, not a failure. Asking beats guessing.
INTENTS = ("build", "refine", "ask", "chat", "stop", "unclear")

# Long enough for a useful reading, short enough that tier L answers fast.
MAX_PROMPT_CHARS = 4000
MAX_TEXT_CHARS = 2000
HISTORY_TURNS = 6

# A model that says it is sure is still a model; a rule that matches a word
# list has no opinion at all. The gap has to show in the number, because the
# caller decides whether to ask on the strength of it.
RULES_CONFIDENCE = 0.45
MODEL_CONFIDENCE = 0.8

MIN_CONCRETE_WORDS = 4

GREETINGS = frozenset({
    "hey", "hi", "hello", "yo", "sup", "thanks", "thank you", "ta", "cheers",
    "ok", "okay", "ok cool", "cool", "nice", "great", "good morning",
    "good evening", "morning", "night", "lol", "haha",
})
GREETING_OPENERS = frozenset({"hey", "hi", "hello", "yo"})

STOPS = frozenset({
    "stop", "cancel", "cancel that", "never mind", "nevermind", "forget it",
    "abort", "halt", "drop it", "leave it", "scrap that",
})

QUESTION_WORDS = frozenset({
    "what", "what's", "whats", "how", "why", "when", "where", "who", "which",
    "is", "are", "was", "were", "do", "does", "did", "can", "could", "should",
    "will", "would", "any",
})

# A refinement only exists relative to something. These are gated on history.
REFINE_OPENERS = frozenset({
    "add", "change", "remove", "delete", "instead", "also", "but", "no",
    "move", "rename", "swap", "fix", "tweak", "adjust", "replace", "undo",
    "darker", "lighter", "bigger", "smaller", "shorter", "longer", "again",
})
REFINE_PHRASES = ("make it", "make them", "can you make it", "put the")

# `do`, `can`, `will` open both a question and a polite imperative. What
# separates them is what follows: a subject ("can you build...") reads as a
# request, a bare object ("do some stuff") as an order.
MODALS = frozenset({"do", "does", "did", "can", "could", "would", "will",
                    "should"})
SUBJECTS = frozenset({"you", "i", "we", "they", "it", "there", "the", "that",
                      "this", "my", "our"})

BUILD_OPENERS = frozenset({
    "build", "make", "create", "write", "draft", "design", "generate", "set",
    "code", "implement", "add", "produce", "give", "do", "get", "turn", "put",
})

# Dictation artefacts. Stripped from the head of a message, plus the two
# fillers that turn up mid-sentence often enough to matter in a restatement.
FILLER = frozenset({
    "um", "uh", "erm", "er", "so", "like", "basically", "actually", "just",
    "well", "okay", "ok", "right", "yeah", "hmm", "please",
})
ANYWHERE_FILLER = frozenset({"um", "uh", "erm"})

VAGUE = ("something", "anything", "some stuff", "stuff", "whatever",
         "some kind", "somethin", "any kind", "idk")

# Counting words alone was too crude: it asked a clarifying question about
# "add a starfield", which names exactly what to do. What actually separates a
# thin request from a short one is whether the only thing named is a category.
# "build a website" names a category; "add a starfield" names a thing.
GENERIC_NOUNS = frozenset({
    "website", "site", "app", "page", "thing", "tool", "script", "program",
    "project", "system", "bot", "dashboard", "file", "document", "doc",
})

# Send, spend, publish. The three things worth interrupting a person for.
# `post` is deliberately only external when it points at a destination, so
# "draft a post about the launch" stays ordinary work.
EXTERNAL_PATTERNS = tuple(re.compile(p) for p in (
    r"\bemail\b", r"\be-mail\b", r"\bsend\b", r"\bsends\b", r"\bdm\b",
    r"\bpublish\b", r"\bdeploy\b", r"\bbuy\b", r"\bpurchase\b", r"\border\b",
    r"\bpay\b", r"\btweet\b", r"\bmessage\s+\w+\s+on\b",
    # A checkout *page* is a thing to build; going to checkout is a thing to
    # do. The bare word stopped "build me a shop with a cart page and a
    # checkout page" at the confirm gate, which is the gate crying wolf on
    # ordinary work - and a gate that fires on everything gets ignored.
    r"\bcheckout\b(?!\s+(?:page|flow|form|screen|view|step|process|button))",
    r"\bpost\s+(?:this|it|that|them)?\s*to\b", r"\blinkedin\b",
    r"\bgo\s+live\b", r"\bsubscribe\b",
))

_PUNCT = re.compile(r"[^\w\s']+")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Reading:
    """What the operator meant, as far as anything here can tell.

    `understood` is shown back before work starts; `brief` is what an agent
    receives. They are different on purpose - one is for a person reading a
    phone screen, the other is instructions.
    """

    intent: str
    understood: str
    brief: str
    question: str
    confidence: float
    external: bool
    source: str


def _clean(text: str) -> str:
    """Drop dictation filler without rewriting what was said."""
    words = _WS.sub(" ", text.strip()).split()
    while words and _PUNCT.sub("", words[0]).casefold() in FILLER:
        words.pop(0)
    words = [w for w in words if _PUNCT.sub("", w).casefold() not in ANYWHERE_FILLER]
    return " ".join(words)


def _tokens(text: str) -> list[str]:
    return _PUNCT.sub(" ", text.casefold()).split()


def _is_external(text: str) -> bool:
    lowered = text.casefold()
    return any(pattern.search(lowered) for pattern in EXTERNAL_PATTERNS)


def _restate(text: str) -> str:
    """The operator's own words, tidied. Not a paraphrase - rules cannot."""
    cleaned = _clean(text)
    if not cleaned:
        return ""
    trimmed = cleaned if len(cleaned) <= 160 else cleaned[:159].rstrip() + "…"
    return trimmed[0].upper() + trimmed[1:]


THIN_QUESTION = ("What exactly should I make, and who is it for? "
                 "One line is enough.")


def thin_reason(text: str) -> str:
    """Why a request cannot be acted on, or "" if it can.

    Two different weaknesses, kept apart because they are trusted differently.
    "category" is high precision - a three-word request whose only noun is
    "website" names no scope at all, and no model reading changes that.
    "vague" is a guess: "something" inside a detailed sentence is ordinary
    English, and a model that read the sentence should be allowed to overrule
    it.
    """
    cleaned = _clean(text)
    words = _tokens(cleaned)
    if not words:
        return "empty"
    if len(words) < MIN_CONCRETE_WORDS and words[-1] in GENERIC_NOUNS:
        return "category"
    flat = _PUNCT.sub("", cleaned).casefold().strip()
    if any(marker in flat for marker in VAGUE):
        return "vague"
    return ""


def is_obvious(text: str) -> bool:
    """True when the whole message is an exact greeting or stop word.

    Not a confidence shortcut - a recognition that these are not a
    classification problem at all. Measured end to end: "hey" cost 5.3 s and
    came back asking what to build, because Laya called it `refine` (its one
    miss in ten) and qwen then called it `unclear`. A set membership test has
    known the answer since the first commit.

    Deliberately narrow. A greeting with work attached - "hello can you build
    me a shop" - is not obvious and still reaches a model.
    """
    cleaned = _clean(text)
    if not cleaned:
        return False
    flat = _PUNCT.sub("", cleaned).casefold().strip()
    words = _tokens(cleaned)
    if flat in STOPS or flat in GREETINGS:
        return True
    return bool(words) and words[0] in GREETING_OPENERS and len(words) <= 4


def read_without_model(text: str, *, history: Sequence[str] = ()) -> Reading:
    """Classify on word lists alone. The floor, and the default."""
    cleaned = _clean(text)
    external = _is_external(text)
    if not cleaned:
        return Reading("unclear", "I couldn't tell what you meant.", "",
                       "What would you like me to do?", RULES_CONFIDENCE,
                       external, "rules")

    flat = _PUNCT.sub("", cleaned).casefold().strip()
    words = _tokens(cleaned)
    first = words[0] if words else ""

    if flat in STOPS or first in {"stop", "abort"}:
        return Reading("stop", "Stop what's running.", "", "",
                       RULES_CONFIDENCE, external, "rules")

    if flat in GREETINGS or (first in GREETING_OPENERS and len(words) <= 4):
        return Reading("chat", _restate(text) or "Hello.", "", "",
                       RULES_CONFIDENCE, external, "rules")

    if history and (first in REFINE_OPENERS
                    or flat.startswith(REFINE_PHRASES)):
        return Reading("refine", _restate(text), cleaned, "",
                       RULES_CONFIDENCE, external, "rules")

    asking = first in QUESTION_WORDS
    if asking and first in MODALS:
        following = words[1] if len(words) > 1 else ""
        if following not in SUBJECTS:
            asking = False  # "do some stuff for me" is an order, not a query.
        elif any(word in BUILD_OPENERS for word in words[1:]):
            asking = False  # "can you build me a site" is a polite order.
    if asking or (cleaned.endswith("?") and first not in BUILD_OPENERS):
        return Reading("ask", _restate(text), "", "",
                       RULES_CONFIDENCE, external, "rules")

    question = ""
    if thin_reason(text):
        question = THIN_QUESTION
    return Reading("build", _restate(text), cleaned, question,
                   RULES_CONFIDENCE, external, "rules")


def build_prompt(text: str, *, history: Sequence[str] = ()) -> str:
    """The interpretation prompt. Clipped so a long message cannot blow it."""
    clipped = text.strip()[:MAX_TEXT_CHARS]
    recent = list(history)[-HISTORY_TURNS:]
    context = ("\n".join(f"- {turn}" for turn in recent)
               if recent else "- (nothing yet)")
    prompt = (
        "You read one message from an operator's phone and report what they "
        "meant. The message may be dictated, so expect filler and run-on "
        "sentences. Reply with one JSON object and nothing else.\n\n"
        "Fields:\n"
        '  intent: one of "build" (make something), "refine" (change the last '
        'thing), "ask" (a question about system state), "chat" (greeting or '
        'small talk), "stop" (halt work), "unclear".\n'
        "  understood: one short sentence restating the request in the "
        "operator's own terms, for them to correct.\n"
        "  brief: for build or refine only, an unambiguous instruction for an "
        "engineer who cannot ask follow-ups. Otherwise an empty string.\n"
        "  question: one clarifying question, only if the request cannot be "
        "acted on without it. Otherwise an empty string.\n"
        "  confidence: 0 to 1.\n"
        "  external: true only if this sends, spends, publishes or posts "
        "somewhere outside this machine.\n\n"
        f"Recent turns:\n{context}\n\n"
        f"Message:\n{clipped}\n"
    )
    return prompt[:MAX_PROMPT_CHARS]


def _json_object(raw: str) -> Optional[dict]:
    """Pull the first balanced object out of a reply, fences and chatter included."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    parsed = json.loads(raw[start:index + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(parsed, dict):
                    return parsed
                start = -1
    return None


def parse_model_reply(raw: str, *, text: str) -> Optional[Reading]:
    """A `Reading` from a model reply, or `None` if it cannot be trusted."""
    parsed = _json_object(raw or "")
    if parsed is None:
        return None
    intent = str(parsed.get("intent") or "").strip().casefold()
    if intent not in INTENTS:
        return None

    understood = str(parsed.get("understood") or "").strip() or _restate(text)
    if not understood:
        return None

    brief = str(parsed.get("brief") or "").strip()
    if intent in {"build", "refine"}:
        spoken = _clean(text)
        # A brief may expand a thin request. It may never summarise a
        # specified one. Measured on a 1,881-character build spec: the brief
        # came back at 22% and had dropped the pinned CDN URL, the importmap
        # and all three project names - the agent would have been told "three
        # projects section" and invented the projects.
        #
        # Length is a blunt test and the right one here. Anything shorter than
        # what the operator wrote is a summary, and a summary of instructions
        # loses instructions. The operator's words are the specification; the
        # reading turn is for recovering meaning from a half-dictated
        # sentence, not for compressing one that already says what it wants.
        if not brief or len(brief) < len(spoken):
            brief = spoken
    if intent not in {"build", "refine"}:
        brief = ""

    question = str(parsed.get("question") or "").strip()
    # The model may ask a better question than the rules would, but it may not
    # decide there is nothing to ask when the request names only a category.
    # Measured: qwen2.5-coder:7b invented a scope for "build a website".
    if not question and thin_reason(text) == "category":
        question = THIN_QUESTION
    try:
        confidence = float(parsed.get("confidence", MODEL_CONFIDENCE))
    except (TypeError, ValueError):
        confidence = MODEL_CONFIDENCE

    return Reading(
        intent=intent,
        understood=understood,
        brief=brief,
        question=question,
        confidence=min(max(confidence, 0.0), 1.0),
        # Raise only. See the module docstring: untrusted text must not be
        # able to talk itself past the gate.
        external=bool(parsed.get("external")) or _is_external(text),
        source="model",
    )


def interpret(
    text: str,
    *,
    history: Sequence[str] = (),
    complete: Optional[Completer] = None,
    classify: Optional[Classifier] = None,
) -> Reading:
    """Read one message. Falls back to rules whenever the model cannot help.

    `classify` is a router, not an oracle. It answers one question - does this
    message need a brief written? - and when the answer is no, the reading is
    finished without spending a generative call. It never decides the external
    gate and never overrules a model that did run; measurement put its label at
    9/10 but its probabilities at barely above a coin toss, so it is trusted
    for routing and nothing else.
    """
    # Neither model is asked about a bare greeting or stop word; both were
    # measured to get them wrong where the word list does not.
    if is_obvious(text):
        return read_without_model(text, history=history)
    # An external request never takes the shortcut. Laya labelled "email the
    # invoice to the client please" as `chat`, and answering that with small
    # talk is safe but wrong: the operator asked for something and would get
    # "I'm here" back. The deterministic detector outranks the classifier.
    external = _is_external(text)
    if classify is not None and not external:
        try:
            label = classify(text)
        except Exception:
            label = None
        if label in NO_BRIEF_NEEDED:
            rules = read_without_model(text, history=history)
            return Reading(
                intent=str(label),
                understood=rules.understood,
                brief="",
                question=rules.question if label != "chat" else "",
                confidence=RULES_CONFIDENCE,
                # Never delegated. Measured at 0.5879 on "email the invoice to
                # the client", which is a coin toss on the judgement guarding
                # money leaving the machine.
                external=_is_external(text),
                source="classifier",
            )
    if complete is not None:
        try:
            raw = complete(build_prompt(text, history=history))
        except Exception:
            # Any transport failure is the offline case, which already works.
            raw = ""
        reading = parse_model_reply(raw, text=text)
        if reading is not None:
            return reading
    return read_without_model(text, history=history)


# The exact criteria measured at 9/10 on ten real dictated messages, on both a
# CPU laptop and the desktop 3060. An earlier terse version scored 7/9 and lost
# "stop" to "build", so the wording is load-bearing and is pinned by a test
# rather than left to be tidied later. `unclear` is deliberately absent: it is
# this module's own outcome for "nothing usable", not something to ask a
# classifier to predict.
# Naming the checkpoint is not optional. The server takes `model` from the
# request body and quietly resolves a default when it is absent, so omitting it
# serves a *different* model than the one these criteria were measured on:
# end to end, every message came back `stop`, reproducing the `english`
# checkpoint's exact failures rather than typed-decisions' 9/10.
LAYA_CHECKPOINT = "typed-decisions"

LAYA_QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "Classify what the person sending this message wants "
                        "to happen next.",
        "criteria": {
            "build": "They are asking for something new to be produced: a "
                     "page, a document, a script, a design. The message names "
                     "a thing to make.",
            "refine": "They are asking for a change to something that already "
                      "exists, such as a colour, a position, or wording.",
            "ask": "They are asking a question and want an answer or a status "
                   "report. Nothing is to be produced.",
            "chat": "Social pleasantry only: a greeting, thanks, or "
                    "acknowledgement. There is no task and no question.",
            "stop": "They want current or pending work halted, cancelled or "
                    "abandoned.",
        },
    },
}


def laya_payload(text: str, model: str = LAYA_CHECKPOINT) -> dict[str, Any]:
    """The request body for POST /v1/systemone."""
    return {
        "model": model,
        "state": text[:MAX_TEXT_CHARS],
        "questions": LAYA_QUESTIONS,
    }


def label_from_laya(payload: Any) -> Optional[str]:
    """The intent label out of a Laya reply, or None if it is not usable.

    Only the label is read. The probabilities are deliberately untouched:
    measured across two machines the winning option averaged 0.31 against 0.20
    for a coin toss over five options, so the number cannot carry a threshold
    even though the label is right nine times in ten.
    """
    try:
        node = payload["answers"]["intent"]
        label = str(node["choice"]).strip().casefold()
    except (TypeError, KeyError, IndexError, AttributeError):
        return None
    return label if label in INTENTS else None


def laya_classifier(
    endpoint: str = "http://127.0.0.1:8127/v1/systemone",
    timeout: float = 5.0,
    token: Optional[str] = None,
    model: str = LAYA_CHECKPOINT,
) -> Classifier:
    """A classifier backed by a local Laya server.

    Loopback by default on purpose. Laya's own server takes `LAYA_HOST` and
    defaults it to `0.0.0.0`, which is how a decision service ends up
    answering the whole network - the same exposure already found and closed
    on FreeLLMAPI. Reaching one across the tailnet is a deliberate act, so it
    has to be typed out.

    Returned rather than called, so importing this module opens nothing and a
    machine with no Laya running simply never builds one.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def classify(text: str) -> Optional[str]:
        body = json.dumps(laya_payload(text, model)).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return label_from_laya(json.loads(response.read().decode("utf-8")))

    return classify


def local_completer(
    endpoint: str = "http://127.0.0.1:11434/v1/chat/completions",
    model: str = "qwen2.5-coder:7b",
    timeout: float = 20.0,
) -> Completer:
    """A completer backed by tier L: free, always on, and measured at 0.82 s.

    Returned rather than called, so importing this module reaches no network
    and a caller with nothing running simply never builds one.
    """

    def complete(prompt: str) -> str:
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            endpoint, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]

    return complete
