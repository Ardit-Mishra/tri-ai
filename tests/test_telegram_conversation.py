"""Telegram as a conversation rather than a confirm-gated intake form.

What shipped before: every plain message became a pending run, and nothing was
done until the operator typed `/confirm`. "hey" became a draft task. A vague
request became a six-minute run producing the wrong artifact, because the raw
text went to the agent as its prompt with nothing in between.

What these tests pin down instead:

  * A greeting is answered, not queued.
  * Ordinary making starts immediately and says what it understood, so a
    misreading is visible in seconds rather than after the run.
  * Send, spend and publish keep the confirmation gate. That is the only place
    an interruption earns itself.
  * A request too thin to act on gets one question, and nothing is created.

Every test runs the *real* interpreter on its rules path - no completer, no
network, no mock. That is also the production floor, so what is proved here is
what runs when nothing else is up.

Turns are stored on the board rather than in the process, because a daemon
restart must not erase the conversation. This daemon restarts.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import board  # noqa: E402
import decomposer  # noqa: E402
import telegram_control  # noqa: E402
from support import BoardTestCase  # noqa: E402


class ConversationFixture(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=self.repo, check=True)
        (self.repo / "README.md").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, check=True)
        policy_path = self.tmp / "intake.json"
        policy_path.write_text(json.dumps({
            "verify_profiles": {"unittest": {"command": "python -m unittest",
                                             "timeout": 30}},
            "workspaces": {"demo": {"path": str(self.repo), "profile": "unittest"}},
        }), encoding="utf-8")
        self.control = telegram_control.TelegramControl(
            telegram_control.load_policy(policy_path),
            catalog_path=self.tmp / "catalog.json",
            radar_path=self.tmp / "radar.json",
        )
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"

    def say(self, text: str, *, chat_id: str = "42", reply_to=None) -> str:
        return self.control.dispatch(
            text, chat_id=chat_id, board_path=self.db_path,
            ledger_path=self.ledger, runs_root=self.runs,
            reply_to_message_id=reply_to,
        )

    def pending(self, chat_id: str = "42"):
        return board.pending_actions_for_chat(self.conn, chat_id)

    def tasks(self):
        return self.conn.execute("SELECT * FROM tasks").fetchall()


class GreetingTest(ConversationFixture):
    def test_a_greeting_is_answered_and_creates_nothing(self):
        """'hey' used to become a draft task awaiting /confirm."""
        reply = self.say("hey")
        self.assertEqual(self.pending(), ())
        self.assertEqual(len(self.tasks()), 0)
        self.assertTrue(reply.strip())

    def test_thanks_does_not_start_work(self):
        self.say("thanks")
        self.assertEqual(len(self.tasks()), 0)


class OrdinaryWorkTest(ConversationFixture):
    def test_a_concrete_request_starts_without_asking_to_confirm(self):
        reply = self.say("build me a recipe card page for a masala chai")
        self.assertEqual(len(self.tasks()), 1)
        self.assertNotIn("/confirm", reply)

    def test_the_reply_shows_what_it_understood_before_the_work_lands(self):
        """A misreading has to be correctable in seconds, not in the artifact."""
        reply = self.say("build me a recipe card page for a masala chai")
        self.assertIn("masala chai", reply.casefold())

    def test_the_task_prompt_is_the_brief_not_an_empty_string(self):
        """An empty prompt is the 'produced nothing' outcome, pre-committed."""
        self.say("build me a recipe card page for a masala chai")
        row = self.tasks()[0]
        self.assertTrue(str(row["body"]).strip())

    def test_the_task_title_names_the_request_not_the_transport(self):
        """Every task used to be titled 'Telegram: demo', so the board was
        unreadable at a glance and so was every completion card."""
        self.say("build me a recipe card page for a masala chai")
        title = str(self.tasks()[0]["title"]).casefold()
        self.assertNotEqual(title, "telegram: demo")
        self.assertIn("chai", title)

    def test_no_pending_action_is_left_dangling_after_an_auto_start(self):
        self.say("build me a recipe card page for a masala chai")
        self.assertEqual(self.pending(), ())


class GateTest(ConversationFixture):
    def test_sending_still_requires_a_confirmation(self):
        reply = self.say("email the client the invoice for september")
        self.assertEqual(len(self.tasks()), 0)
        self.assertEqual(len(self.pending()), 1)
        self.assertIn("confirm", reply.casefold())

    def test_publishing_still_requires_a_confirmation(self):
        self.say("publish the bakery site to production")
        self.assertEqual(len(self.pending()), 1)

    def test_a_confirmed_external_request_then_runs(self):
        self.say("email the client the invoice for september")
        action_id = self.pending()[0]["id"]
        self.say(f"/confirm {action_id}")
        self.assertEqual(len(self.tasks()), 1)


class ClarificationTest(ConversationFixture):
    def test_a_vague_request_asks_instead_of_burning_six_minutes(self):
        reply = self.say("make me something cool")
        self.assertEqual(len(self.tasks()), 0)
        self.assertEqual(self.pending(), ())
        self.assertIn("?", reply)

    def test_answering_the_question_then_starts_the_work(self):
        self.say("make me something cool")
        self.say("a recipe card page for masala chai with timings")
        self.assertEqual(len(self.tasks()), 1)


class ReadTest(ConversationFixture):
    def test_a_question_about_state_is_answered_and_starts_no_work(self):
        reply = self.say("what is running right now")
        self.assertEqual(len(self.tasks()), 0)
        self.assertTrue(reply.strip())

    def test_stop_creates_no_task(self):
        self.say("stop")
        self.assertEqual(len(self.tasks()), 0)


class TurnStoreTest(ConversationFixture):
    def test_turns_survive_a_restart_because_they_live_on_the_board(self):
        self.say("build me a recipe card page for a masala chai")
        turns = board.recent_turns(self.conn, chat_id="42")
        self.assertTrue(turns)
        self.assertTrue(any("chai" in turn.casefold() for turn in turns))

    def test_turns_are_scoped_to_one_chat(self):
        self.say("build me a bakery landing page", chat_id="1")
        self.assertEqual(board.recent_turns(self.conn, chat_id="2"), ())

    def test_only_recent_turns_are_returned(self):
        for index in range(12):
            board.record_turn(self.conn, chat_id="9", role="operator",
                              text=f"turn {index}")
        turns = board.recent_turns(self.conn, chat_id="9", limit=5)
        self.assertEqual(len(turns), 5)
        self.assertIn("turn 11", turns[-1])

    def test_a_follow_up_after_a_task_is_a_refinement_not_a_new_build(self):
        self.say("build me a recipe card page for a masala chai")
        before = len(self.tasks())
        reply = self.say("make it darker")
        self.assertGreaterEqual(len(self.tasks()), before)
        self.assertTrue(reply.strip())


class CommandSurfaceTest(ConversationFixture):
    def test_slash_commands_still_work_for_anyone_who_knows_them(self):
        """Collapsing the surface must not break the operator's muscle memory."""
        self.assertTrue(self.say("/status").strip())
        self.assertTrue(self.say("/workspaces").strip())

    def test_help_leads_with_talking_not_with_a_command_list(self):
        help_text = self.say("/help")
        head = help_text[:200].casefold()
        self.assertNotIn("/task <task-id>", head)


if __name__ == "__main__":
    unittest.main()


class FanoutTest(ConversationFixture):
    """A substantial request becomes a graph of specialists, not one agent.

    Spiked against the real Hermes before this was built: "Build a 3D
    interactive portfolio landing page with a WebGL hero, three project
    sections and a contact block" decomposed into seven children - three
    designers, four frontend builders - ordered design, build, integrate.
    That is the multi-agent deployment the system was specified for, and
    until now the Telegram path could not reach it: every message became
    exactly one task with one agent.

    The decomposer is stubbed here. What these hold down is the wiring and,
    more importantly, the refusals - a request too small to split, and a
    decomposition that fails. Neither may cost the operator their request.
    """

    def stub_decompose(self, children, *, raises=None):
        """Stand in for `hermes kanban decompose`, which needs a live Hermes."""
        def fake(goal, **kwargs):
            if raises is not None:
                raise raises
            return decomposer.Decomposition(
                goal=goal, root_task_id="h_root", fanout=True,
                reason=f"decomposed into {len(children)} children",
                children=tuple(
                    decomposer.Child(task_id=f"h_{i}", title=title,
                                     body="", assignee=role)
                    for i, (role, title) in enumerate(children)
                ),
            )
        return mock.patch.object(decomposer, "run_hermes_decompose", side_effect=fake)

    HEAVY = ("build me a shop with a product grid, a cart page, a checkout "
             "page and an admin view for stock levels")

    def test_a_substantial_request_deploys_several_specialists(self):
        with self.stub_decompose([
            ("designer", "Design the product grid"),
            ("frontend_builder", "Build the product grid"),
            ("backend_builder", "Build the checkout"),
        ]):
            self.say(self.HEAVY)
        self.assertEqual(len(self.tasks()), 3)
        roles = {str(r["agent_role"]) for r in self.tasks()}
        self.assertEqual(roles, {"designer", "frontend_builder", "backend_builder"})

    def test_the_specialists_are_linked_so_design_precedes_build(self):
        with self.stub_decompose([
            ("designer", "Design the product grid"),
            ("frontend_builder", "Build the product grid"),
        ]):
            self.say(self.HEAVY)
        links = self.conn.execute(
            "SELECT parent_id, child_id FROM task_links").fetchall()
        self.assertTrue(links, "a phase-ordered graph must carry edges")

    def test_the_reply_says_how_much_work_was_created(self):
        """Counted as tasks, not agents.

        "2 agents" was the wording until the runtime was checked: there is
        one worker daemon and it runs `run_once` synchronously, so a fan-out
        is a queue of tasks through one agent, not several agents at once.
        The count is what the operator needs; the noun was a claim the system
        could not back.
        """
        with self.stub_decompose([
            ("designer", "Design the product grid"),
            ("frontend_builder", "Build the product grid"),
        ]):
            reply = self.say(self.HEAVY)
        self.assertIn("2", reply)
        self.assertIn("task", reply.casefold())

    def test_a_small_request_is_still_one_agent(self):
        """Splitting costs a model call and board churn before work starts."""
        with self.stub_decompose([("designer", "x")]) as stub:
            self.say("build me a recipe card page for a masala chai")
        stub.assert_not_called()
        self.assertEqual(len(self.tasks()), 1)

    def test_a_failed_decomposition_still_starts_the_work(self):
        """The request must survive a tool that is down. Decomposition is an
        improvement on one agent, never a precondition for any."""
        with self.stub_decompose([], raises=decomposer.DecomposeError("hermes is down")):
            reply = self.say(self.HEAVY)
        self.assertEqual(len(self.tasks()), 1)
        self.assertTrue(reply.strip())

    def test_a_decomposition_refused_by_the_gate_loses_nothing(self):
        """planner.validate_graph refuses a bad graph whole. That refusal must
        fall back to one task rather than dropping the request."""
        with self.stub_decompose([("designer", "")]):
            self.say(self.HEAVY)
        self.assertGreaterEqual(len(self.tasks()), 1)

    def test_an_external_request_is_never_fanned_out_behind_the_gate(self):
        """Send/spend/publish stages for confirmation; it does not quietly
        become six agents first."""
        with self.stub_decompose([("designer", "x")]) as stub:
            self.say("email the whole customer list a page with a product "
                     "grid, a cart page and a checkout page")
        stub.assert_not_called()
        self.assertEqual(len(self.tasks()), 0)


class DegradedFanoutIsVisibleTest(FanoutTest):
    """A fan-out that quietly degraded must not read as a clean one.

    Flagged HIGH by `agent-architecture-audit`: every outcome of `_fanout`
    that is not a persisted graph returns `None`, the ordinary single-task
    card is then built as though one agent were the plan, and the operator
    cannot tell a considered choice from Hermes falling over. Three fan-out
    runs were diagnosed from the board and the run directories this session
    because the card said nothing.
    """

    def _linked_stub(self, children, links):
        """Children plus `parents`, the way Hermes actually returns them."""
        def fake(goal, **kwargs):
            return decomposer.Decomposition(
                goal=goal, root_task_id="h_root", fanout=True,
                children=tuple(
                    decomposer.Child(task_id=f"h_{i}", title=title, body="",
                                     assignee=role, parents=links.get(i, ()))
                    for i, (role, title) in enumerate(children)
                ),
            )
        return mock.patch.object(
            decomposer, "run_hermes_decompose", side_effect=fake)

    def test_a_failed_split_says_so_on_the_single_task_card(self):
        with self.stub_decompose(
                [], raises=decomposer.DecomposeError("hermes is down")):
            reply = self.say(self.HEAVY)
        self.assertEqual(len(self.tasks()), 1)
        self.assertIn("split", reply.casefold())

    def test_the_failure_reason_reaches_the_operator(self):
        with self.stub_decompose(
                [], raises=decomposer.DecomposeError("hermes is down")):
            reply = self.say(self.HEAVY)
        self.assertIn("hermes is down", reply)

    def test_a_guessed_ordering_says_it_was_guessed(self):
        """Phase order is the fallback. It over-constrains across a phase and
        under-constrains inside one, so the operator should know it is in use.
        """
        with self._linked_stub(
                [("designer", "Design the grid"),
                 ("frontend_builder", "Build the grid")], links={}):
            reply = self.say(self.HEAVY)
        self.assertIn("order", reply.casefold())
        self.assertIn("guess", reply.casefold())

    def test_real_dependencies_are_not_announced_as_a_guess(self):
        with self._linked_stub(
                [("designer", "Design the grid"),
                 ("frontend_builder", "Build the grid")], links={1: ("h_0",)}):
            reply = self.say(self.HEAVY)
        self.assertNotIn("guess", reply.casefold())

    def test_a_clean_single_task_card_is_left_alone(self):
        """A request too small to split was never a degraded fan-out."""
        reply = self.say("build me a recipe card page for a masala chai")
        self.assertEqual(len(self.tasks()), 1)
        self.assertNotIn("split", reply.casefold())


class LeftoverCardsAreDisclosedTest(FanoutTest):
    """Planning cards left runnable on a Hermes board are the operator's
    problem to know about, not a log line nobody reads."""

    def _stub(self, *, archived):
        def fake(goal, **kwargs):
            return decomposer.Decomposition(
                goal=goal, root_task_id="h_root", fanout=True,
                archived=archived,
                archive_error="" if archived else "archive refused: board locked",
                children=tuple(
                    decomposer.Child(task_id=f"h_{i}", title=title, body="",
                                     assignee=role, parents=parents)
                    for i, (role, title, parents) in enumerate([
                        ("designer", "Design the grid", ()),
                        ("frontend_builder", "Build the grid", ("h_0",)),
                    ])
                ),
            )
        return mock.patch.object(
            decomposer, "run_hermes_decompose", side_effect=fake)

    def test_unarchived_cards_are_named_on_the_card(self):
        with self._stub(archived=False):
            reply = self.say(self.HEAVY)
        self.assertIn("hermes", reply.casefold())
        self.assertIn("2", reply)

    def test_a_clean_archive_adds_no_noise(self):
        with self._stub(archived=True):
            reply = self.say(self.HEAVY)
        self.assertNotIn("hermes", reply.casefold())

    def test_the_work_still_starts_either_way(self):
        with self._stub(archived=False):
            self.say(self.HEAVY)
        self.assertEqual(len(self.tasks()), 2)


class FanoutIsJudgedOnBothReadingsTest(FanoutTest):
    """`warrants_attempt` must see the operator's words, not only the brief.

    Flagged MEDIUM by the audit. `interpreter` already guarantees the brief is
    never *shorter* than what was said - a measured 22%-compression incident
    put that rule in - so the brief cannot silently drop detail by length.
    It can still rephrase: an expanded brief that says "storefront" where the
    operator said "cart page and a checkout page" has fewer of the part-words
    the heuristic counts.

    `warrants_attempt` documents its own asymmetry: "a false yes costs one
    extra call and Hermes then declines to fan out, while a false no silently
    hands a seven-agent job to a single agent." Judging both readings and
    taking either is the permissive direction that docstring asks for.
    """

    # Longer than the operator's words, so the brief-fidelity rule keeps it,
    # and every part-word replaced by one generic noun. `warrants_attempt`
    # needs two distinct part-words and this has one, so a fan-out judged on
    # the brief alone refuses a request that plainly names four pieces.
    REPHRASED = ("Deliver a complete and polished online storefront "
                 "experience for the customer, covering the whole journey "
                 "from arrival through to a finished order, with stock "
                 "administration included for the shop owner as well")

    def _reading_completer(self):
        """A model reader that rephrases rather than echoing."""
        def complete(prompt, **kwargs):
            return json.dumps({
                "intent": "build",
                "understood": "an online storefront",
                "brief": self.REPHRASED,
                "question": "",
                "confidence": 0.9,
                "external": False,
            })
        return complete

    def test_a_rephrased_brief_does_not_veto_the_operators_words(self):
        self.control._completer = self._reading_completer()
        with self.stub_decompose([
                ("designer", "Design the product grid"),
                ("frontend_builder", "Build the product grid")]) as stub:
            self.say(self.HEAVY)
        self.assertTrue(
            stub.called,
            "the operator named a product grid, a cart page, a checkout page "
            "and an admin view; the brief's wording must not veto that")
        self.assertEqual(len(self.tasks()), 2)

    def test_the_gate_sees_the_message_as_sent(self):
        seen: list[str] = []
        real = decomposer.warrants_attempt      # before the patch replaces it

        def watch(text):
            seen.append(text)
            return real(text)

        self.control._completer = self._reading_completer()
        with mock.patch.object(decomposer, "warrants_attempt",
                               side_effect=watch):
            with self.stub_decompose([
                    ("designer", "Design the product grid"),
                    ("frontend_builder", "Build the product grid")]):
                self.say(self.HEAVY)
        self.assertIn(self.HEAVY, seen,
                      "the message as sent was never offered to the gate")

    def test_a_small_request_is_still_refused_on_both_readings(self):
        with self.stub_decompose([("designer", "x")]) as stub:
            self.say("build me a recipe card page for a masala chai")
        stub.assert_not_called()


class TheCardDoesNotPromiseParallelismTest(FanoutTest):
    """"7 agents across 3 phases" reads as seven agents working. One is.

    `daemon_supervisor` builds "the only two child argv forms" - one worker
    daemon and one telegram daemon - and `worker_daemon.serve` calls
    `worker.run_once` synchronously, which claims a single ready task and runs
    it to completion. Verified on the live runtime: one `worker_daemon`
    process, pid 17348.

    So a seven-node fan-out is seven tasks through one worker, back to back.
    At the measured three-to-five minutes a node that is most of an hour, and
    the operator reading "7 agents" has no way to know that. It also misled
    me: I reported two builders "serialising cleanly" through the clean-tree
    precheck when in fact nothing was ever concurrent for the precheck to
    serialise.
    """

    def _linked(self, children, links):
        def fake(goal, **kwargs):
            return decomposer.Decomposition(
                goal=goal, root_task_id="h_root", fanout=True,
                children=tuple(
                    decomposer.Child(task_id=f"h_{i}", title=title, body="",
                                     assignee=role, parents=links.get(i, ()))
                    for i, (role, title) in enumerate(children)
                ),
            )
        return mock.patch.object(
            decomposer, "run_hermes_decompose", side_effect=fake)

    THREE = [("designer", "Design the grid"),
             ("frontend_builder", "Build the grid"),
             ("backend_builder", "Build the checkout")]

    def test_the_card_says_the_work_is_queued_not_simultaneous(self):
        with self._linked(self.THREE, {1: ("h_0",), 2: ("h_0",)}):
            reply = self.say(self.HEAVY)
        self.assertIn("one at a time", reply.casefold())

    def test_the_count_is_still_there(self):
        """Saying it is sequential must not cost the operator the scale."""
        with self._linked(self.THREE, {1: ("h_0",)}):
            reply = self.say(self.HEAVY)
        self.assertIn("3", reply)

    def test_a_single_task_card_makes_no_such_claim(self):
        reply = self.say("build me a recipe card page for a masala chai")
        self.assertNotIn("one at a time", reply.casefold())
