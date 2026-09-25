"""The decomposer turns a Hermes decomposition into a planner-shaped DAG.

`hermes kanban decompose` returns children with no parents. A flat list defeats
a topological planner, so the decomposer supplies the edges. These tests pin
the two things that can be quietly wrong: which role a child maps to, and
whether the derived edges form a graph the planner will actually accept.

Nothing here shells out to Hermes. The projection is a pure function over a
Decomposition, which is the reason it is worth having as a separate function.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402
import decomposer  # noqa: E402
import planner  # noqa: E402


WORKSPACE = str(Path(tempfile.gettempdir()).resolve())


def _decomp(*pairs, goal="Build a thing"):
    """A Decomposition from (title, assignee) pairs."""
    return decomposer.Decomposition(
        goal=goal,
        root_task_id="t_root",
        children=[
            decomposer.Child(task_id=f"t_{i}", title=title, body="", assignee=who)
            for i, (title, who) in enumerate(pairs, start=1)
        ],
    )


class PhaseEdgeTest(unittest.TestCase):
    def test_a_later_phase_depends_on_the_nearest_earlier_phase(self):
        graph = decomposer.build_graph(
            _decomp(
                ("Interview users", "user_researcher"),   # ideation
                ("Build the API", "backend_builder"),     # build
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        nodes = {n["node_key"]: n for n in graph["nodes"]}
        ideation = next(k for k, n in nodes.items() if n["_phase"] == "ideation")
        build = next(k for k, n in nodes.items() if n["_phase"] == "build")

        self.assertEqual(nodes[ideation]["parents"], [])
        self.assertEqual(nodes[build]["parents"], [ideation])

    def test_nodes_in_the_same_phase_are_siblings_not_a_chain(self):
        """Same-phase work must stay parallel or the dispatcher cannot fan out."""
        graph = decomposer.build_graph(
            _decomp(
                ("Build the API", "backend_builder"),
                ("Build the UI", "frontend_builder"),
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        for node in graph["nodes"]:
            self.assertEqual(node["parents"], [], node["node_key"])

    def test_an_empty_phase_is_skipped_rather_than_breaking_the_chain(self):
        """ideation -> (no design) -> harden must still connect."""
        graph = decomposer.build_graph(
            _decomp(
                ("Interview users", "user_researcher"),      # ideation
                ("Audit the authz paths", "security_auditor"),  # harden
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        nodes = {n["node_key"]: n for n in graph["nodes"]}
        ideation = next(k for k, n in nodes.items() if n["_phase"] == "ideation")
        harden = next(k for k, n in nodes.items() if n["_phase"] == "harden")
        self.assertEqual(nodes[harden]["parents"], [ideation])

    def test_derived_edges_never_form_a_cycle(self):
        """Edges only point backwards through a fixed phase order."""
        graph = decomposer.build_graph(
            _decomp(
                ("Ship it", "devops_engineer"),           # ship
                ("Interview users", "user_researcher"),   # ideation
                ("Price it", "pricing_strategist"),       # monetise
                ("Build the API", "backend_builder"),     # build
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        # planner._topological_order raises on a cycle; reaching a list is the proof.
        ordered = planner.validate_graph(graph)
        self.assertEqual(len(ordered), 4)


class RoleMappingTest(unittest.TestCase):
    def test_a_hermes_profile_maps_to_the_role_of_the_same_name(self):
        graph = decomposer.build_graph(
            _decomp(("Take payments", "payments_engineer")),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        self.assertEqual(graph["nodes"][0]["agent_role"], "payments_engineer")

    def test_an_unknown_profile_falls_back_to_builder(self):
        """A hand-made Hermes profile must not fail the whole graph."""
        graph = decomposer.build_graph(
            _decomp(("Do something", "some_profile_nobody_registered")),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        self.assertEqual(graph["nodes"][0]["agent_role"], "builder")
        self.assertEqual(
            graph["nodes"][0]["_hermes_assignee"], "some_profile_nobody_registered",
            "the original assignee must survive for provenance",
        )


class PlannerAcceptanceTest(unittest.TestCase):
    def test_the_emitted_graph_is_accepted_unmodified(self):
        graph = decomposer.build_graph(
            _decomp(
                ("Interview users", "user_researcher"),
                ("Design the flow", "designer"),
                ("Build the API", "backend_builder"),
                ("Build the UI", "frontend_builder"),
                ("Audit authz", "security_auditor"),
                ("Take payments", "payments_engineer"),
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        ordered = planner.validate_graph(graph)

        self.assertEqual(len(ordered), 6)
        for node in ordered:
            self.assertIn(node.agent_role, capabilities.all_roles())
            self.assertTrue(node.verify_command.strip())
            self.assertGreater(node.verify_timeout, 0)

    def test_parents_precede_their_children_in_topological_order(self):
        graph = decomposer.build_graph(
            _decomp(
                ("Ship it", "devops_engineer"),
                ("Interview users", "user_researcher"),
                ("Build the API", "backend_builder"),
            ),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        ordered = planner.validate_graph(graph)
        seen: set[str] = set()
        for node in ordered:
            for parent in node.parents:
                self.assertIn(parent, seen, f"{node.key} ran before parent {parent}")
            seen.add(node.key)


class VerifyCommandTest(unittest.TestCase):
    def test_a_generic_gate_is_labelled_as_generic(self):
        """A stack-blind command must never look like a considered one."""
        graph = decomposer.build_graph(
            _decomp(("Build the API", "backend_builder")),
            workspace_kind="dir", workspace_path=WORKSPACE,
        )
        self.assertTrue(graph["nodes"][0]["verify_generic"])

    def test_a_supplied_command_is_used_and_not_labelled_generic(self):
        graph = decomposer.build_graph(
            _decomp(("Build the API", "backend_builder")),
            workspace_kind="dir", workspace_path=WORKSPACE,
            verify_command="python -m pytest -q",
        )
        self.assertEqual(graph["nodes"][0]["verify_command"], "python -m pytest -q")
        self.assertFalse(graph["nodes"][0]["verify_generic"])


class HermesOutputTest(unittest.TestCase):
    def test_the_json_payload_is_read_past_hermes_banners(self):
        noisy = (
            "  Loaded env from C:\\Users\\x\\.omniroute\\.env\n"
            "  warning: something\n"
            '{"task_id": "t_1", "ok": true, "child_ids": ["t_2"]}\n'
        )
        self.assertEqual(
            decomposer._last_json_object(noisy)["task_id"], "t_1"
        )

    def test_a_pretty_printed_payload_is_read(self):
        """`kanban create --json` pretty-prints; `decompose --json` does not."""
        pretty = (
            "  Loaded env from somewhere\n"
            "{\n"
            '  "id": "t_3878f8e9",\n'
            '  "title": "Build an app",\n'
            '  "nested": {"a": 1}\n'
            "}\n"
        )
        self.assertEqual(decomposer._last_json_object(pretty)["id"], "t_3878f8e9")

    def test_the_last_object_wins_when_several_are_printed(self):
        text = '{"id": "first"}\n{"id": "second"}\n'
        self.assertEqual(decomposer._last_json_object(text)["id"], "second")

    def test_output_with_no_json_object_raises_rather_than_returning_empty(self):
        with self.assertRaises(decomposer.DecomposeError):
            decomposer._last_json_object("nothing useful here\n")


if __name__ == "__main__":
    unittest.main()
