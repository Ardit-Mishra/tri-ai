"""Read what the operator meant before anything is dispatched.

Until now a Telegram message became a task prompt verbatim: `telegram_control`
put the raw text in `payload["prompt"]` and titled every task
`"Telegram: <workspace>"`. Nothing read it. A concrete request like "a Diwali
pamphlet" survives that treatment; a dictated, half-specified one does not, and
the operator only discovers the misreading six minutes later in the artifact.

So this module is the missing turn: text in, a structured `Reading` out, with a
restatement the operator can correct *before* work starts. Three properties
matter more than accuracy:

  1. It degrades to rules when no model answers. Tier L is normally there, but
     the system has to work with nothing running, so the model is an
     improvement on the fallback and never a dependency.
  2. It never invents an intent. An unrecognised label from the model is
     refused rather than passed to the board, which would dispatch on garbage.
  3. A `build` always carries a non-empty brief. An empty prompt reaching an
     agent is precisely the "produced nothing" failure already in the ledger.

It is a pure function over text. It opens no board and spawns no process; the
completer is injected, so every test here runs without a network.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import interpreter  # noqa: E402


def _model(payload: dict) -> "object":
    """A completer that answers with one JSON object, as the prompt asks."""
    return lambda _prompt: json.dumps(payload)


def _build_payload(**over):
    payload = {
        "intent": "build",
        "understood": "Build a recipe card page for masala chai",
        "brief": "Create a single-page recipe card for masala chai with "
                 "ingredients, method and timings.",
        "question": "",
        "confidence": 0.9,
        "external": False,
    }
    payload.update(over)
    return payload


class ModelPathTest(unittest.TestCase):
    def test_a_clean_model_answer_is_used_and_says_it_came_from_a_model(self):
        reading = interpreter.interpret("recipe card for chai",
                                        complete=_model(_build_payload()))
        self.assertEqual(reading.intent, "build")
        self.assertEqual(reading.source, "model")
        self.assertIn("masala chai", reading.brief)

    def test_json_wrapped_in_a_code_fence_is_still_parsed(self):
        """Small instruct models fence their JSON however firmly you ask."""
        body = "```json\n" + json.dumps(_build_payload()) + "\n```"
        reading = interpreter.interpret("recipe card", complete=lambda _p: body)
        self.assertEqual(reading.source, "model")

    def test_json_with_chatter_around_it_is_still_parsed(self):
        body = "Sure! Here is the reading:\n" + json.dumps(_build_payload()) + "\nHope that helps."
        self.assertEqual(interpreter.interpret("x", complete=lambda _p: body).source, "model")

    def test_prose_instead_of_json_falls_back_to_rules(self):
        reading = interpreter.interpret(
            "build me a landing page for a bakery",
            complete=lambda _p: "I think you want a landing page!",
        )
        self.assertEqual(reading.source, "rules")
        self.assertEqual(reading.intent, "build")

    def test_a_completer_that_raises_falls_back_rather_than_propagating(self):
        """The phone surface must not 500 because a router is down."""
        def boom(_prompt):
            raise ConnectionError("ollama is not running")
        reading = interpreter.interpret("build me a bakery site", complete=boom)
        self.assertEqual(reading.source, "rules")
        self.assertTrue(reading.understood.strip())

    def test_an_intent_the_roster_does_not_define_is_refused(self):
        """A junk label would be dispatched on. Refuse it at the boundary."""
        reading = interpreter.interpret(
            "build me a bakery site",
            complete=_model(_build_payload(intent="yolo")),
        )
        self.assertEqual(reading.source, "rules")
        self.assertIn(reading.intent, interpreter.INTENTS)

    def test_every_reading_carries_an_intent_the_module_declares(self):
        for text in ("hey", "stop", "what is the status", "build me a shop",
                     "make me something cool", ""):
            self.assertIn(interpreter.interpret(text).intent, interpreter.INTENTS)


class BriefTest(unittest.TestCase):
    def test_a_build_with_an_empty_brief_falls_back_to_the_operators_words(self):
        """An empty prompt is the 'produced nothing' failure, pre-committed."""
        reading = interpreter.interpret(
            "build me a bakery site",
            complete=_model(_build_payload(brief="   ")),
        )
        self.assertTrue(reading.brief.strip())
        self.assertIn("bakery", reading.brief)

    def test_a_non_build_reading_carries_no_brief(self):
        reading = interpreter.interpret("hey there")
        self.assertEqual(reading.intent, "chat")
        self.assertEqual(reading.brief, "")

    def test_the_restatement_is_never_empty_because_it_is_the_whole_point(self):
        for text in ("hey", "stop", "build me a shop", "wat", "make it blue"):
            self.assertTrue(interpreter.interpret(text).understood.strip(), text)


class RulesFallbackTest(unittest.TestCase):
    """With no completer at all - the no-subscription, nothing-running case."""

    def test_a_greeting_is_conversation_and_never_becomes_a_task(self):
        """Today 'hey' creates a pending run awaiting /confirm."""
        for text in ("hey", "hello", "hi there", "thanks", "thank you", "ok cool"):
            self.assertEqual(interpreter.interpret(text).intent, "chat", text)

    def test_a_stop_word_is_a_stop_and_not_a_build(self):
        for text in ("stop", "cancel", "cancel that", "never mind", "forget it"):
            self.assertEqual(interpreter.interpret(text).intent, "stop", text)

    def test_a_question_about_state_is_an_ask_not_a_build(self):
        for text in ("what is running right now", "how many tasks failed",
                     "is the desktop up?", "what happened to the last one"):
            self.assertEqual(interpreter.interpret(text).intent, "ask", text)

    def test_a_concrete_request_is_a_build_with_no_question(self):
        reading = interpreter.interpret(
            "build me a recipe card page for a masala chai")
        self.assertEqual(reading.intent, "build")
        self.assertEqual(reading.question, "")

    def test_a_vague_request_asks_one_question_instead_of_guessing(self):
        """Guessing here is what produced six minutes of the wrong artifact."""
        for text in ("make me something cool", "build me anything",
                     "do some stuff for me"):
            reading = interpreter.interpret(text)
            self.assertTrue(reading.question.strip(), text)

    def test_the_question_is_one_question_not_an_interrogation(self):
        reading = interpreter.interpret("make me something cool")
        self.assertLessEqual(reading.question.count("?"), 1)

    def test_a_request_naming_only_a_category_is_too_thin_to_act_on(self):
        for text in ("build a website", "make an app", "write a script"):
            self.assertTrue(interpreter.interpret(text).question.strip(), text)

    def test_a_short_request_that_names_a_real_thing_is_acted_on(self):
        """Counting words called 'add a starfield' vague. It is not - it says
        exactly what to do, and asking about it is the interruption this
        module exists to avoid."""
        for text in ("add a starfield", "fix the header", "translate it to hindi"):
            self.assertEqual(interpreter.interpret(text).question, "", text)

    def test_rules_never_claim_the_confidence_of_a_model(self):
        rules = interpreter.interpret("build me a recipe card page for chai")
        model = interpreter.interpret("x", complete=_model(_build_payload()))
        self.assertLess(rules.confidence, model.confidence)

    def test_empty_text_is_unclear_rather_than_an_empty_build(self):
        self.assertEqual(interpreter.interpret("   ").intent, "unclear")


class DictationTest(unittest.TestCase):
    """The operator dictates. Voice-to-text arrives as a run-on with filler."""

    def test_a_dictated_run_on_still_reads_as_a_build(self):
        text = ("um so basically i want you to like make me a page for my "
                "brand it should have the logo and the contact details on it")
        reading = interpreter.interpret(text)
        self.assertEqual(reading.intent, "build")
        self.assertEqual(reading.question, "")

    def test_filler_is_stripped_from_the_restatement(self):
        reading = interpreter.interpret("um so like build me a bakery page")
        self.assertNotIn("um", reading.understood.casefold().split())

    def test_a_very_long_message_does_not_blow_the_model_prompt(self):
        prompt = interpreter.build_prompt("word " * 5000)
        self.assertLessEqual(len(prompt), interpreter.MAX_PROMPT_CHARS)


class HistoryTest(unittest.TestCase):
    def test_a_short_imperative_after_a_task_is_a_refinement(self):
        history = ("built a recipe card page for masala chai",)
        for text in ("make it blue", "add a photo", "change the title",
                     "no, darker than that"):
            self.assertEqual(
                interpreter.interpret(text, history=history).intent, "refine", text)

    def test_the_same_words_with_no_history_are_not_a_refinement(self):
        """Nothing to refine means it cannot be a refinement."""
        self.assertNotEqual(interpreter.interpret("make it blue").intent, "refine")

    def test_history_reaches_the_model_so_follow_ups_resolve(self):
        prompt = interpreter.build_prompt(
            "make it blue", history=("a recipe card for masala chai",))
        self.assertIn("masala chai", prompt)

    def test_only_recent_history_is_carried(self):
        prompt = interpreter.build_prompt("x", history=tuple(
            f"turn{i}" for i in range(50)))
        self.assertNotIn("turn0 ", prompt)
        self.assertIn("turn49", prompt)


class RulesAsAFloorTest(unittest.TestCase):
    """The model improves on the rules; it does not get to be less careful.

    Measured against qwen2.5-coder:7b, the model read a dictated request the
    word lists got wrong - and then cheerfully invented a scope for "build a
    website", which the rules had correctly refused to guess at. So the rules
    keep a veto in the two places where guessing costs something: a request
    that names only a category, and anything that leaves this machine.
    """

    def test_a_model_may_not_skip_a_question_the_rules_demand_of_a_category(self):
        reading = interpreter.interpret(
            "build a website", complete=_model(_build_payload(question="")))
        self.assertTrue(reading.question.strip())

    def test_a_vague_word_in_a_detailed_request_does_not_force_a_question(self):
        """'something' inside a specific sentence is English, not vagueness -
        and the model read this one correctly where the rules did not."""
        text = ("can you knock together something that shows my projects off, "
                "nothing fancy")
        reading = interpreter.interpret(
            text, complete=_model(_build_payload(question="")))
        self.assertEqual(reading.question, "")

    def test_the_model_keeps_its_own_question_when_it_asks_one(self):
        reading = interpreter.interpret(
            "build a website",
            complete=_model(_build_payload(question="Who is it for?")))
        self.assertEqual(reading.question, "Who is it for?")


class ExternalTest(unittest.TestCase):
    """The confirm gate should survive only where it earns its interruption."""

    def test_sending_spending_and_publishing_are_flagged_external(self):
        for text in ("email the client the invoice",
                     "post this to linkedin",
                     "publish the site to production",
                     "buy the domain for me",
                     "send a message to my landlord"):
            self.assertTrue(interpreter.interpret(text).external, text)

    def test_ordinary_making_is_not_external(self):
        for text in ("build me a recipe card page for masala chai",
                     "write a script that renames my files",
                     "draft a post about the launch"):
            self.assertFalse(interpreter.interpret(text).external, text)

    def test_a_model_may_not_clear_an_external_flag_the_rules_raise(self):
        """Prompt-injected text must not be able to talk its way past the gate."""
        reading = interpreter.interpret(
            "email the client the invoice",
            complete=_model(_build_payload(external=False)),
        )
        self.assertTrue(reading.external)


class ClassifierTest(unittest.TestCase):
    """A fast classifier as a router, deliberately not as an oracle.

    Laya reads intent on this machine in ~576 ms against qwen's 1.0-1.6 s, but
    measurement said to take its label and not its probability: 9/10 correct
    while the winning option averaged 0.31 against a 0.20 coin toss, and the
    send/spend check scored 0.5879 on "email the invoice to the client".

    So it is given exactly one job - deciding whether this message needs a
    generative model at all. Most phone traffic is greetings, questions and
    stops, none of which need a brief written. Answering those without an LLM
    call is the whole saving, and it avoids adjudicating between two
    classifiers when the slower one is about to run anyway.
    """

    def setUp(self):
        self.calls = []

    def completer(self, payload=None):
        def complete(prompt):
            self.calls.append(prompt)
            return json.dumps(payload or _build_payload())
        return complete

    def test_a_greeting_is_answered_without_spending_a_model_call(self):
        reading = interpreter.interpret(
            "hey", complete=self.completer(), classify=lambda _t: "chat")
        self.assertEqual(reading.intent, "chat")
        self.assertEqual(self.calls, [], "the completer should not have run")

    def test_a_stop_and_an_ask_also_skip_the_model(self):
        for label in ("stop", "ask"):
            self.calls.clear()
            interpreter.interpret("whatever", complete=self.completer(),
                                  classify=lambda _t, l=label: l)
            self.assertEqual(self.calls, [], label)

    def test_work_still_reaches_the_model_because_a_brief_must_be_written(self):
        reading = interpreter.interpret(
            "build me a recipe card for chai", complete=self.completer(),
            classify=lambda _t: "build")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(reading.brief.strip())

    def test_a_label_the_module_does_not_declare_is_ignored(self):
        """A junk label must not route, and must not suppress the model."""
        interpreter.interpret("build me a shop", complete=self.completer(),
                              classify=lambda _t: "sideways")
        self.assertEqual(len(self.calls), 1)

    def test_a_classifier_that_raises_does_not_take_the_reading_down(self):
        def boom(_text):
            raise ConnectionError("laya service is down")
        reading = interpreter.interpret(
            "build me a shop", complete=self.completer(), classify=boom)
        self.assertEqual(reading.source, "model")

    def test_an_external_request_is_never_short_circuited_as_small_talk(self):
        """Laya labelled "email the invoice to the client please" as `chat`.

        Routing that to the chat reply is safe - nothing is sent - but the
        operator asked for something and would get "I'm here" back, which is
        the not-understood complaint returning by another route. Anything the
        deterministic detector calls external goes the long way round to the
        gate, whatever the classifier thinks.
        """
        seen = []
        reading = interpreter.interpret(
            "email the invoice to the client please",
            complete=lambda p: seen.append(p) or json.dumps(_build_payload()),
            classify=lambda _t: "chat",
        )
        self.assertEqual(len(seen), 1, "the model should have been consulted")
        self.assertNotEqual(reading.intent, "chat")
        self.assertTrue(reading.external)

    def test_the_external_gate_is_not_delegated_to_the_classifier(self):
        """Measured at 0.5879 on 'email the invoice' - a coin toss on the one
        judgement that guards spending. The regex keeps it."""
        reading = interpreter.interpret(
            "email the invoice to the client", classify=lambda _t: "chat")
        self.assertTrue(reading.external)

    def test_a_routed_reading_says_where_its_label_came_from(self):
        """Not a bare greeting: those short-circuit to the word list before
        any classifier runs, so they would report `rules`."""
        reading = interpreter.interpret(
            "so how is that build coming along", classify=lambda _t: "ask")
        self.assertEqual(reading.source, "classifier")

    def test_no_classifier_leaves_every_existing_path_untouched(self):
        self.assertEqual(interpreter.interpret("hey").source, "rules")


class ObviousMessageTest(unittest.TestCase):
    """Some messages are not a classification problem.

    Measured end to end: "hey" took 5.3 s and came back asking what to build.
    Laya called it `refine` (its one miss in ten) and qwen then called it
    `unclear`. The word list has known the answer since the first commit and
    answers in microseconds. When an exact greeting or stop word is the whole
    message, neither model is consulted - there is nothing to infer.
    """

    def test_an_exact_greeting_never_reaches_either_model(self):
        calls = []
        for text in ("hey", "hi", "thanks", "ok cool"):
            interpreter.interpret(
                text,
                complete=lambda p: calls.append(p) or "{}",
                classify=lambda t: calls.append(t) or "build",
            )
        self.assertEqual(calls, [])

    def test_an_exact_stop_word_never_reaches_either_model(self):
        calls = []
        reading = interpreter.interpret(
            "cancel that",
            complete=lambda p: calls.append(p) or "{}",
            classify=lambda t: calls.append(t) or "build",
        )
        self.assertEqual(reading.intent, "stop")
        self.assertEqual(calls, [])

    def test_a_greeting_with_real_work_attached_still_reaches_a_model(self):
        """'hello can you build me a shop' is not a greeting."""
        seen = []
        interpreter.interpret(
            "hello can you build me a shop for my bakery",
            complete=lambda p: seen.append(p) or json.dumps(_build_payload()),
        )
        self.assertEqual(len(seen), 1)

    def test_the_shortcut_still_reports_where_the_answer_came_from(self):
        self.assertEqual(
            interpreter.interpret("hey", classify=lambda _t: "build").source,
            "rules")


class LayaAdapterTest(unittest.TestCase):
    """Shaping the Laya call, tested without one.

    The transport is one `urlopen`; what is worth testing is the payload and
    the reply handling, because both were got wrong by hand first. The
    criteria here are the exact wording measured at 9/10 - rewriting the terse
    version into full sentences is what fixed "stop", which the terse one lost
    to "build" - so they are pinned rather than paraphrased.
    """

    def test_the_question_uses_a_type_laya_actually_defines(self):
        """`boolean` appears in Laya's README:628 and raises ValueError."""
        for question in interpreter.LAYA_QUESTIONS.values():
            self.assertIn(question["type"], {"choice", "noul", "score"})

    def test_every_routable_intent_is_offered_as_an_option(self):
        criteria = interpreter.LAYA_QUESTIONS["intent"]["criteria"]
        self.assertEqual(set(criteria), set(interpreter.INTENTS) - {"unclear"})

    def test_the_criteria_are_sentences_not_keywords(self):
        """Terse criteria measured 7/9; these sentences measured 9/10."""
        for text in interpreter.LAYA_QUESTIONS["intent"]["criteria"].values():
            self.assertGreater(len(text.split()), 8, text)

    def test_the_payload_names_the_checkpoint_it_was_measured_on(self):
        """Omitting `model` does not fail - it silently serves a different
        checkpoint. Measured end to end: without it every message came back
        `stop`, matching the `english` checkpoint's exact failures (hey,
        thanks mate, whats going on, build a website) rather than the
        `typed-decisions` result of 9/10 the unit tests were written against.
        """
        payload = interpreter.laya_payload("build me a page")
        self.assertEqual(payload["model"], interpreter.LAYA_CHECKPOINT)
        self.assertEqual(payload["model"], "typed-decisions")

    def test_the_payload_carries_the_message_as_state(self):
        payload = interpreter.laya_payload("build me a page")
        self.assertEqual(payload["state"], "build me a page")
        self.assertIn("intent", payload["questions"])

    def test_an_overlong_message_is_clipped_before_it_is_sent(self):
        payload = interpreter.laya_payload("x" * 99_000)
        self.assertLessEqual(len(payload["state"]), interpreter.MAX_TEXT_CHARS)

    def test_a_label_is_read_out_of_the_reply_laya_actually_sends(self):
        payload = {"answers": {"intent": {
            "type": "choice", "choice": "build",
            "probabilities": {"build": 0.38, "chat": 0.2},
        }}}
        self.assertEqual(interpreter.label_from_laya(payload), "build")

    def test_a_label_outside_the_roster_is_refused(self):
        payload = {"answers": {"intent": {"choice": "sideways"}}}
        self.assertIsNone(interpreter.label_from_laya(payload))

    def test_a_malformed_reply_is_none_rather_than_an_exception(self):
        for payload in ({}, {"answers": {}}, {"answers": {"intent": {}}},
                        {"answers": None}, []):
            self.assertIsNone(interpreter.label_from_laya(payload))

    def test_the_probability_is_not_read_at_all(self):
        """Measured 0.31 mean top-probability against 0.20 for a coin toss, on
        two machines. The label is usable; the number is not."""
        source = (Path(__file__).resolve().parents[1] / "src" / "interpreter.py"
                  ).read_text(encoding="utf-8")
        body = source.split("def label_from_laya")[1].split("\ndef ")[0]
        # The docstring says why, so match the access rather than the word.
        self.assertNotIn('["probabilities"]', body)
        self.assertNotIn('["confidence"]', body)
        self.assertNotIn('.get("probabilities"', body)
        self.assertNotIn('.get("confidence"', body)

    def test_the_adapter_defaults_to_loopback(self):
        """Laya's own server defaults to LAYA_HOST=0.0.0.0. An 0.0.0.0 default
        on this side too is how a reader ends up answering the whole LAN -
        the same exposure already closed on FreeLLMAPI this month."""
        import inspect
        default = inspect.signature(
            interpreter.laya_classifier).parameters["endpoint"].default
        self.assertIn("127.0.0.1", default)


class PurityTest(unittest.TestCase):
    def test_the_module_opens_no_board_and_spawns_nothing(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "interpreter.py"
                  ).read_text(encoding="utf-8")
        for forbidden in ("import board", "import subprocess", "sqlite3",
                          "import executor"):
            self.assertNotIn(forbidden, source)

    def test_interpret_does_not_reach_the_network_without_a_completer(self):
        """The default path must be offline, or the phone waits on a socket."""
        def fail(*_a, **_k):
            raise AssertionError("interpret opened a connection")
        original = interpreter.local_completer
        interpreter.local_completer = fail
        try:
            interpreter.interpret("build me a bakery page")
        finally:
            interpreter.local_completer = original


if __name__ == "__main__":
    unittest.main()
