"""An approved rule must change what the agent is told, or approving it is theatre.

The learning loop now runs end to end up to a point:

    ledger -> episodic -> evolution -> proposal -> operator approves
           -> row in triai_activated_procedural_rules -> **nothing**

`board.activated_procedural_rule` fetches one row by proposal id and is used
only to confirm a decision. `kaya_terminal` counts the table for the
dashboard. Nothing reads an activated rule on the way to a run, so the
operator can approve advice and the next agent is told exactly what the last
one was.

The consumer already exists and is complete: `procedural.select` matches on
workspace and task_kind, re-validates every citation, bounds the rendered
size and reports what it excluded and why. What is missing is the two ends -
getting activated rules off the board, and putting the selected checks into
the prompt.

Bounds this keeps, because it is the agent's prompt:

* advice comes only from rules an operator activated;
* it must match this workspace and this role;
* a citation whose ledger line has changed disqualifies its rule, so advice
  derived from evidence that has since moved on stops applying by itself;
* it is size-bounded, so a growing rule set cannot crowd out the request;
* with no activated rules the prompt is byte-for-byte what it is today.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import executor  # noqa: E402
import worker  # noqa: E402
from memory import episodic  # noqa: E402


class AdviceReachesTheAgentTest(BoardTestCase):
    ROLE = "designer"

    def setUp(self) -> None:
        super().setUp()
        self.ledger = self.tmp / "ledger.jsonl"
        self.runs = self.tmp / "runs"
        self.repo = self.tmp / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "config", "user.email", "t@e.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=self.repo, check=True)
        (self.repo / "m.txt").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.repo, check=True)
        # A real ledger line, so a citation can point at it and validate.
        self.ledger.write_text(
            json.dumps({"task_id": "t_old", "outcome": "failed",
                        "failure_class": "logic", "verify_exit": 7}) + "\n",
            encoding="utf-8")

    def _citation(self):
        line = self.ledger.read_text(encoding="utf-8").splitlines()[0]
        import hashlib
        return {
            "source_path": str(self.ledger.resolve()),
            "source_line": 1,
            "line_digest": hashlib.sha256(line.encode("utf-8")).hexdigest(),
        }

    def _activate(self, *, checks=("inspect_last_verify_log",),
                  workspace=None, task_kind=None, expires_in=3600.0):
        rule = {
            "id": "candidate:abc",
            "workspace": str((workspace or self.repo).resolve()),
            "task_kind": task_kind or self.ROLE,
            "checks": list(checks),
            "citations": [self._citation()],
            "expires_at": time.time() + expires_in,
            "activation": "preflight_advice",
        }
        # Through the real approval path, not an INSERT. The table has a
        # foreign key to `triai_proposals`, and going the real way also
        # proves the thing worth proving: an operator approving a card is
        # what makes advice apply.
        proposal_id = "evolution:candidate:abc"
        board.create_candidate_rule_proposal(
            self.conn, proposal_id=proposal_id, rule=rule)
        result = board.decide_proposal(
            self.conn, proposal_id=proposal_id, decision="approve")
        self.assertTrue(result.changed, f"approval refused: {result}")
        return rule

    def _prompt(self):
        """Run one attempt and return the prompt the agent was handed."""
        task_id = board.create_task(
            self.conn, title="Design the page", prompt="design the page",
            verify_command="python -c \"pass\"", repo=self.repo,
            verify_timeout=60, agent_role=self.ROLE)
        seen = {}

        def fake(repo, prompt, **kwargs):
            seen["prompt"] = prompt
            return executor.AgentResult(0, "done\n", 1.0, model="m",
                                        model_source="usage_file", api_calls=5)

        verify = executor.VerifyResult("passed", 0, "ok", 0.1)
        claimed = self.kb.claim_task(self.conn, task_id,
                                     claimer=worker.worker_id())
        with mock.patch.object(executor, "run_agent", side_effect=fake), \
             mock.patch.object(executor, "run_verify", return_value=verify):
            worker.execute_task(self.conn, claimed, ledger_path=self.ledger,
                                runs_root=self.runs)
        return seen["prompt"]

    # -- the board end -----------------------------------------------------

    def test_activated_rules_can_be_read_back(self):
        self._activate()
        rules = board.activated_procedural_rules(self.conn)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["task_kind"], self.ROLE)

    def test_no_activated_rules_is_an_empty_tuple_not_an_error(self):
        self.assertEqual(board.activated_procedural_rules(self.conn), ())

    def test_an_unreadable_row_is_skipped_rather_than_raising(self):
        """One corrupt rule must not stop every run from starting.

        The row is corrupted after activation rather than inserted directly:
        the table has a foreign key to `triai_proposals`, so a bogus row
        cannot exist without one - which is itself worth knowing.
        """
        self._activate()
        self.conn.execute(
            "UPDATE triai_activated_procedural_rules SET rule_json = ?",
            ("{not json",))
        self.conn.commit()
        self.assertEqual(board.activated_procedural_rules(self.conn), ())
        self.assertNotIn("inspect_last_verify_log", self._prompt())

    # -- the prompt end ----------------------------------------------------

    def test_an_approved_check_reaches_the_agent(self):
        self._activate(checks=("inspect_last_verify_log",))
        self.assertIn("inspect_last_verify_log", self._prompt())

    def test_no_approved_rules_leaves_the_prompt_alone(self):
        self.assertNotIn("inspect_last_verify_log", self._prompt())

    def test_advice_for_another_role_is_not_applied(self):
        self._activate(task_kind="backend_builder")
        self.assertNotIn("inspect_last_verify_log", self._prompt())

    def test_advice_for_another_workspace_is_not_applied(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        self._activate(workspace=other)
        self.assertNotIn("inspect_last_verify_log", self._prompt())

    def test_an_expired_rule_cannot_even_be_proposed(self):
        """Stronger than expected, so recorded as it is.

        The first version of this test approved an expired rule and checked
        it was not applied. `create_candidate_rule_proposal` refuses outright:
        "candidate rule is expired or its citations are stale". An expired
        rule never reaches the operator, let alone the agent.
        """
        with self.assertRaises(ValueError):
            self._activate(expires_in=-1.0)

    def test_select_excludes_a_rule_that_expires_after_activation(self):
        """The case the board cannot refuse: valid when approved, stale later."""
        from memory import procedural
        rule = dict(self._activate(), expires_at=time.time() - 1.0)
        chosen = procedural.select(
            [procedural.rule_from_mapping(rule, now=0.0)],
            workspace=self.repo, task_kind=self.ROLE, max_characters=600)
        self.assertEqual(chosen.checks, ())
        self.assertEqual(chosen.excluded_rule_ids, ("candidate:abc",))

    def test_advice_whose_evidence_has_changed_stops_applying(self):
        """The citation is a line digest. Rewrite the line and it is stale."""
        self._activate()
        self.ledger.write_text(
            json.dumps({"task_id": "t_new", "outcome": "passed"}) + "\n",
            encoding="utf-8")
        self.assertNotIn("inspect_last_verify_log", self._prompt())

    def test_the_operators_request_still_comes_first(self):
        self._activate()
        prompt = self._prompt()
        self.assertLess(prompt.index("design the page"),
                        prompt.index("inspect_last_verify_log"))


if __name__ == "__main__":
    unittest.main()


class TheBudgetBoundsWhatIsAppendedTest(AdviceReachesTheAgentTest):
    """Codex: the cap budgeted the checklist, not the block around it.

    `procedural.select(max_characters=...)` bounds the rendered *checks*.
    `_approved_advice` wraps them in a 57-character heading and "  - "
    bullets, and the wrapper sat outside the count, so the stated 600-
    character cap was not quite true of what was appended.

    The interesting band is narrow and is what these tests aim at: a budget
    large enough that `select` admits the checks, but smaller than the block
    once the heading and bullets are added. One check renders as 23
    characters and the whole block as 85, so a budget of 30 is inside it.

    A first version used the full four-check allowlist at budget 60. That
    renders as 97 characters, so `select` excluded the rule outright and the
    trimming code was never reached - the test passed with the bound removed,
    which is how a test proves nothing.
    """

    ONE = ("inspect_last_verify_log",)

    def test_a_budget_select_admits_but_the_block_exceeds_yields_nothing(self):
        self._activate(checks=self.ONE)
        self.assertEqual(
            worker._approved_advice(self.conn, self.repo, self.ROLE,
                                    budget=30),
            "", "the heading and bullets must count toward the cap")

    def test_a_budget_that_fits_the_whole_block_keeps_it(self):
        self._activate(checks=self.ONE)
        block = worker._approved_advice(self.conn, self.repo, self.ROLE,
                                        budget=200)
        self.assertIn("inspect_last_verify_log", block)
        self.assertLessEqual(len(block), 200)

    def test_the_returned_block_never_exceeds_the_budget(self):
        self._activate(checks=self.ONE)
        for budget in (1, 30, 60, 84, 85, 120, 600):
            block = worker._approved_advice(self.conn, self.repo, self.ROLE,
                                            budget=budget)
            self.assertLessEqual(len(block), budget, f"budget={budget}")

    def test_the_default_budget_still_admits_the_whole_allowlist(self):
        """Trimming must not be so eager that real advice never lands."""
        from memory import procedural
        self._activate(checks=tuple(sorted(procedural.ALLOWED_CHECKS)))
        block = worker._approved_advice(self.conn, self.repo, self.ROLE)
        for check in procedural.ALLOWED_CHECKS:
            self.assertIn(check, block)


if __name__ == "__main__":
    unittest.main()
