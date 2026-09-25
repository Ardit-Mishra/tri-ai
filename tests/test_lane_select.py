"""Choose the model lane for a node, and never depend on a paid one.

Three tiers. Local is the floor and must be able to carry a whole build alone;
free API is the volume lane; a subscription is an accelerator that may vanish
mid-run without stopping anything. A task declares the lowest tier it needs,
never a named subscription, so no node is ever written against "Claude".

The preference order is declared, then overridden by what the ledger actually
recorded. A lane that returned HTTP 200 and produced nothing is a failure here,
because the ledger stores verify outcomes rather than transport results.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import lane_select  # noqa: E402


def _ledger(tmp: Path, rows: list[dict]) -> Path:
    p = tmp / "ledger.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def _row(role: str, model: str, outcome: str) -> dict:
    return {"ts": str(time.time()), "agent_role": role, "model": model,
            "outcome": outcome}


class TierTest(unittest.TestCase):
    def test_a_deskollama_lane_is_local(self):
        self.assertEqual(lane_select.tier_of("deskollama/qwen2.5-coder:7b"), "local")

    def test_an_auto_alias_is_free(self):
        self.assertEqual(lane_select.tier_of("auto/best-free"), "free")

    def test_a_named_cli_subscription_is_subscription(self):
        self.assertEqual(lane_select.tier_of("claude-code"), "subscription")
        self.assertEqual(lane_select.tier_of("codex"), "subscription")

    def test_an_unknown_lane_is_treated_as_free_not_local(self):
        """Guessing local would claim an uncapped lane the system does not have."""
        self.assertEqual(lane_select.tier_of("something/unheard-of"), "free")


class DeclaredDefaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_with_no_history_the_declared_default_is_used(self):
        chosen = lane_select.choose("backend_builder", ledger_path=self.tmp / "none.jsonl")
        self.assertEqual(chosen.lane, lane_select.declared_lane("backend_builder"))
        self.assertEqual(chosen.reason, "declared default")

    def test_too_few_samples_keeps_the_declared_default(self):
        """Two runs is not evidence; routing on it would be noise."""
        path = _ledger(self.tmp, [_row("backend_builder", "auto/cheap", "passed")] * 2)
        chosen = lane_select.choose("backend_builder", ledger_path=path)
        self.assertEqual(chosen.reason, "declared default")


class MeasuredOverrideTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_lane_with_the_better_pass_rate_wins(self):
        rows = ([_row("backend_builder", "deskollama/qwen2.5-coder:14b", "passed")] * 20
                + [_row("backend_builder", "auto/cheap", "failed")] * 18
                + [_row("backend_builder", "auto/cheap", "passed")] * 2)
        chosen = lane_select.choose("backend_builder", ledger_path=_ledger(self.tmp, rows))
        self.assertEqual(chosen.lane, "deskollama/qwen2.5-coder:14b")
        self.assertEqual(chosen.reason, "measured")
        self.assertEqual(chosen.samples, 20)

    def test_evidence_for_one_role_does_not_route_another(self):
        rows = [_row("designer", "auto/cheap", "passed")] * 20
        chosen = lane_select.choose("backend_builder", ledger_path=_ledger(self.tmp, rows))
        self.assertEqual(chosen.reason, "declared default")

    def test_a_run_that_returned_200_but_failed_verify_counts_against_the_lane(self):
        """The ledger stores verify outcomes; transport success is not success."""
        rows = [_row("backend_builder", "auto/cheap", "failed")] * 20
        chosen = lane_select.choose("backend_builder", ledger_path=_ledger(self.tmp, rows))
        self.assertNotEqual(chosen.lane, "auto/cheap")


class ExhaustionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.state = self.tmp / "lanes.json"

    def test_a_codex_usage_limit_reply_yields_a_reset_time(self):
        when = lane_select.parse_reset(
            "You've hit your usage limit. Upgrade to Pro, or try again at "
            "Sep 26th, 2026 11:21 AM."
        )
        self.assertIsNotNone(when)

    def test_an_unparseable_message_still_marks_the_lane_down(self):
        """Never retry into an exhausted lane just because the text was odd."""
        lane_select.mark_exhausted("codex", "quota gone", state_path=self.state)
        self.assertFalse(lane_select.is_available("codex", state_path=self.state))

    def test_an_exhausted_lane_is_skipped_and_a_lower_tier_is_used(self):
        lane_select.mark_exhausted("codex", "limit", state_path=self.state)
        chosen = lane_select.choose(
            "backend_builder", ledger_path=self.tmp / "none.jsonl",
            state_path=self.state, permitted=("codex", "auto/cheap",
                                              "deskollama/qwen2.5-coder:7b"),
        )
        self.assertNotEqual(chosen.lane, "codex")

    def test_a_lane_becomes_available_again_after_its_reset(self):
        lane_select.mark_exhausted("codex", "limit", state_path=self.state,
                                   available_after=time.time() - 1)
        self.assertTrue(lane_select.is_available("codex", state_path=self.state))


class DegradationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_with_every_subscription_gone_a_build_still_gets_a_lane(self):
        """The floor must hold: no subscription, no internet beyond the tailnet."""
        state = self.tmp / "lanes.json"
        for lane in ("codex", "claude-code"):
            lane_select.mark_exhausted(lane, "limit", state_path=state)
        chosen = lane_select.choose(
            "backend_builder", ledger_path=self.tmp / "none.jsonl", state_path=state,
            permitted=("codex", "claude-code", "deskollama/qwen2.5-coder:7b"),
        )
        self.assertEqual(lane_select.tier_of(chosen.lane), "local")

    def test_choosing_never_returns_an_empty_lane(self):
        state = self.tmp / "lanes.json"
        for lane in lane_select.DEFAULT_PERMITTED:
            lane_select.mark_exhausted(lane, "limit", state_path=state)
        chosen = lane_select.choose(
            "backend_builder", ledger_path=self.tmp / "none.jsonl", state_path=state)
        self.assertTrue(chosen.lane.strip())
        self.assertIn("exhausted", chosen.reason)


if __name__ == "__main__":
    unittest.main()
