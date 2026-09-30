"""A route must be credited to the lane that actually ran it.

The Cortex rail is headed "CLAUDE LANE" and beneath it listed, unchanged
whichever tab was selected:

    custom  auto/best-free    7 retained runs
    custom  auto/best-coding  29 retained runs
    custom  devstral:24b      1 retained run

`devstral:24b` is an Ollama tag. It only exists on the local box, it cannot
have been run by Claude, and `auto/best-*` are OmniRoute aliases on the free
route. Measured in the live console, the rendered rows were byte-identical
across all three tabs - the prose above them changed and not one row below.

That is worse than showing nothing. This panel is the one an operator uses
to answer "is this actually running free, or am I burning a subscription?",
and it was answering with the wrong lane's evidence.

These tests execute the real classifier out of the shipped template rather
than asserting on substrings of it, because a substring assertion passes on
a function that returns the wrong lane.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard import kaya_web as web  # noqa: E402


def _script() -> str:
    found = re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", web.HTML, re.DOTALL)
    if not found:
        raise AssertionError("the template has no inline script")
    return found[0]


def _extract(name: str) -> str:
    """Pull one top-level `function name(...) { ... }` out of the template.

    Brace counting rather than a regex: the body contains braces, and a
    lazy match would stop at the first one.
    """
    script = _script()
    start = script.index(f"function {name}(")
    depth = 0
    for index in range(script.index("{", start), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"function {name} is not closed in the template")


class LaneClassifierTest(unittest.TestCase):
    """Run the shipped `laneForModel` under node against real model names."""

    CASES = {
        # Every model name in this table was read from the desktop's own
        # ledger or its hermes config, not invented for the test.
        "devstral:24b": "local",
        "qwen2.5-coder:14b": "local",
        "qwen3:14b": "local",
        "gemma4:31b": "local",
        "auto/best-coding": "local",
        "auto/best-free": "local",
        "auto/smart": "local",
        "claude-opus-5": "claude",
        "claude-sonnet-5-5": "claude",
        "gpt-5-codex": "codex",
    }

    def setUp(self) -> None:
        self.node = shutil.which("node")

    def test_each_model_is_credited_to_the_lane_that_can_run_it(self):
        if not self.node:
            self.skipTest("node is not installed; the classifier was not run")
        program = (
            _extract("laneForModel")
            + "\nconst out={};"
            + f"for(const m of {json.dumps(sorted(self.CASES))})"
            + "out[m]=laneForModel(m,'custom');"
            + "console.log(JSON.stringify(out));"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lane.js"
            path.write_text(program, encoding="utf-8")
            result = subprocess.run(
                [self.node, str(path)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = json.loads(result.stdout)
        self.assertEqual(got, self.CASES)

    def test_an_ollama_tag_is_never_credited_to_a_subscription_lane(self):
        """The specific claim the console was making on 2026-09-30."""
        if not self.node:
            self.skipTest("node is not installed; the classifier was not run")
        program = (
            _extract("laneForModel")
            + "console.log(laneForModel('devstral:24b','custom'));"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lane.js"
            path.write_text(program, encoding="utf-8")
            result = subprocess.run(
                [self.node, str(path)], capture_output=True, text=True, check=False)
        self.assertEqual(result.stdout.strip(), "local")


class TheLanePanelIsRedrawnWithTheLaneTest(unittest.TestCase):
    """A correct classifier is useless if the rows are never re-rendered.

    `renderModelLanes` ran only from the snapshot handler, so switching tab
    left whichever lane's rows had been drawn last sitting under the new
    heading. That is exactly how the misattribution stayed invisible.
    """

    def test_switching_lane_redraws_the_observed_routes(self):
        script = _script()
        lens = script[script.index("function renderAgentLens("):]
        lens = lens[:lens.index("\n    document.querySelectorAll('.agent-tab')")]
        self.assertIn(
            "renderModelLanes", lens,
            "renderAgentLens does not redraw the route rows, so the panel "
            "keeps the previous lane's evidence under the new lane's heading")

    def test_the_classifier_is_actually_called_when_rows_are_built(self):
        script = _script()
        body = script[script.index("function renderModelLanes("):]
        body = body[:body.index("\n    function ")]
        self.assertIn("laneForModel", body,
                      "renderModelLanes does not classify, so every lane "
                      "still shows every route")
        self.assertIn("activeAgentLens", body,
                      "renderModelLanes does not filter by the selected lane")


class LocalIsTheDefaultLaneTest(unittest.TestCase):
    """The console should open on the lane the system actually uses.

    hermes `config.yaml` points `model.base_url` at OmniRoute and lists
    `devstral:24b` and `qwen2.5-coder:14b` beneath it; the standing rule is
    $0 marginal cost. Opening on Claude told the opposite story.
    """

    def test_the_console_opens_on_local(self):
        self.assertIn("let activeAgentLens='local'", _script())

    def test_the_local_tab_is_the_one_marked_selected_in_the_markup(self):
        marked = re.findall(
            r'data-lane="(\w+)"', web.HTML)
        self.assertEqual(marked[0], "local",
                         f"the first lane tab is {marked[0]!r}, so the "
                         "console presents an escalation lane as the default")
        selected = re.findall(
            r'aria-selected="true"[^>]*data-lane="(\w+)"', web.HTML)
        self.assertEqual(selected, ["local"])


class EachLaneSaysWhatItCostsTest(unittest.TestCase):
    """"It needs to explain what the point does."

    The old copy was true of every lane and distinguished none of them.
    Cost and when-it-runs are the two facts that separate them.
    """

    def test_every_lane_declares_when_it_runs_and_what_it_costs(self):
        script = _script()
        block = script[script.index("const AGENT_LENSES={"):]
        block = block[:block.index("\n    let activeAgentLens")]
        for lane in ("local:", "claude:", "codex:"):
            self.assertIn(lane, block)
        self.assertEqual(block.count("when:"), 3,
                         "a lane does not say when it is chosen")
        self.assertEqual(block.count("cost:"), 3,
                         "a lane does not say what it costs")

    def test_the_free_lane_is_described_as_unmetered(self):
        script = _script()
        block = script[script.index("local:{"):script.index("claude:{")]
        self.assertIn("No metered cost", block)

    def test_the_subscription_lanes_are_described_as_escalation(self):
        script = _script()
        for lane in ("claude:{", "codex:{"):
            block = script[script.index(lane):]
            block = block[:block.index("returns:")]
            self.assertIn("Escalated to, never the default", block,
                          f"{lane} does not say it is an escalation")


if __name__ == "__main__":
    unittest.main()
