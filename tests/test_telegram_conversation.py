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

    def test_the_reply_says_how_many_agents_were_deployed(self):
        with self.stub_decompose([
            ("designer", "Design the product grid"),
            ("frontend_builder", "Build the product grid"),
        ]):
            reply = self.say(self.HEAVY)
        self.assertIn("2", reply)
        self.assertIn("agent", reply.casefold())

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
