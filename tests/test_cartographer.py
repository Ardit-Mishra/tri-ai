"""Project a task graph into an archify workflow diagram.

The point is to see how many agents a request deployed and what each one did,
without reading JSON. Lanes are phases, nodes are tasks, edges are the real
dependencies, and the tag on each node is the model lane that served it.

This is a pure projection, like `progress_card` and `completion_report`: it
formats evidence and opens nothing. A diagram that could write to the board
would be a second path to the same state.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cartographer  # noqa: E402


def _graph():
    return {
        "goal": "Build a workout app",
        "nodes": [
            {"node_key": "n01_designer", "title": "Design the UI",
             "agent_role": "designer", "_phase": "design", "parents": [],
             "verify_command": "npm test", "lane": "auto/best-coding"},
            {"node_key": "n02_backend_builder", "title": "Build the API",
             "agent_role": "backend_builder", "_phase": "build",
             "parents": ["n01_designer"], "verify_command": "npm test",
             "lane": "deskollama/qwen2.5-coder:7b"},
            {"node_key": "n03_payments_engineer", "title": "Take payments",
             "agent_role": "payments_engineer", "_phase": "monetise",
             "parents": ["n02_backend_builder"], "verify_command": "npm test",
             "lane": "auto/best-coding"},
        ],
    }


class ShapeTest(unittest.TestCase):
    def test_the_document_has_everything_archify_requires(self):
        doc = cartographer.to_archify(_graph())
        for key in ("schema_version", "diagram_type", "meta", "lanes", "nodes", "edges"):
            self.assertIn(key, doc)
        self.assertEqual(doc["diagram_type"], "workflow")
        self.assertTrue(doc["meta"]["title"].strip())

    def test_one_lane_per_agent(self):
        """A lane is an actor. archify lanes are 104px tall and hold one row."""
        doc = cartographer.to_archify(_graph())
        self.assertEqual([l["id"] for l in doc["lanes"]],
                         ["designer", "backend_builder", "payments_engineer"])

    def test_lanes_are_ordered_by_when_the_agent_first_works(self):
        graph = _graph()
        graph["nodes"].reverse()
        doc = cartographer.to_archify(graph)
        self.assertEqual([l["id"] for l in doc["lanes"]],
                         ["designer", "backend_builder", "payments_engineer"])

    def test_phases_are_column_headers_not_lanes(self):
        doc = cartographer.to_archify(_graph())
        self.assertEqual([p["id"] for p in doc["phases"]],
                         ["design", "build", "monetise"])


class NodeTest(unittest.TestCase):
    def test_every_task_becomes_a_node_in_its_phase_lane(self):
        doc = cartographer.to_archify(_graph())
        self.assertEqual(len(doc["nodes"]), 3)
        by_id = {n["id"]: n for n in doc["nodes"]}
        self.assertEqual(by_id["n02_backend_builder"]["lane"], "backend_builder")

    def test_the_tag_names_the_model_lane_that_served_it(self):
        doc = cartographer.to_archify(_graph())
        tags = {n["id"]: n.get("tag") for n in doc["nodes"]}
        self.assertIn("qwen2.5-coder:7b", tags["n02_backend_builder"])

    def test_the_sublabel_carries_the_phase_and_the_run_state(self):
        """A 150px box cannot hold a shell command.

        The gate stays in the graph JSON, which is what the planner and the
        worker read. Trying to fit it here produced a label 213px wide against
        a 150px node, which the renderer refuses outright - so the diagram
        carries the facts that fit: which phase, and how it went.
        """
        doc = cartographer.to_archify(_graph(), outcomes={"n01_designer": "passed"})
        subs = {n["id"]: n.get("sublabel", "") for n in doc["nodes"]}
        self.assertIn("design", subs["n01_designer"])
        self.assertIn("passed", subs["n01_designer"])

    def test_no_label_or_sublabel_can_overflow_its_box(self):
        """The renderer measures ~6.8px per character against node width."""
        long_title = "An extremely long task title that would never fit in a box"
        graph = {"goal": "g", "nodes": [
            {"node_key": "n1", "title": long_title, "agent_role": "builder",
             "_phase": "build", "parents": [], "verify_command": "x" * 200},
        ]}
        node = cartographer.to_archify(graph)["nodes"][0]
        self.assertLessEqual(len(node["label"]), cartographer.MAX_LABEL)
        self.assertLessEqual(len(node["sublabel"]), cartographer.MAX_SUBLABEL)

    def test_columns_increase_with_phase_so_the_flow_reads_left_to_right(self):
        doc = cartographer.to_archify(_graph())
        cols = {n["id"]: n["col"] for n in doc["nodes"]}
        self.assertLess(cols["n01_designer"], cols["n02_backend_builder"])
        self.assertLess(cols["n02_backend_builder"], cols["n03_payments_engineer"])


class LayoutTest(unittest.TestCase):
    def test_no_two_nodes_share_a_lane_and_column(self):
        """That renders as overlapping boxes and archify refuses the document."""
        graph = {"goal": "g", "nodes": [
            {"node_key": f"n{i}", "title": f"t{i}", "agent_role": "backend_builder",
             "_phase": "build", "parents": [], "verify_command": "x"}
            for i in range(4)
        ]}
        doc = cartographer.to_archify(graph)
        slots = [(n["lane"], n["col"]) for n in doc["nodes"]]
        self.assertEqual(len(slots), len(set(slots)))

    def test_parallel_agents_share_a_column_because_they_run_together(self):
        doc = cartographer.to_archify(_graph())
        by_id = {n["id"]: n for n in doc["nodes"]}
        self.assertNotEqual(by_id["n01_designer"]["lane"],
                            by_id["n02_backend_builder"]["lane"])

    def test_col_never_exceeds_the_schema_maximum(self):
        """archify caps col at 5; eight phases would silently produce 7."""
        graph = {"goal": "g", "nodes": [
            {"node_key": f"n_{p}", "title": p, "agent_role": "builder",
             "_phase": p, "parents": [], "verify_command": "x"}
            for p in cartographer.PHASE_ORDER
        ]}
        doc = cartographer.to_archify(graph)
        for node in doc["nodes"]:
            self.assertLessEqual(node["col"], 5, node["id"])
            self.assertGreaterEqual(node["col"], 0, node["id"])


class EdgeTest(unittest.TestCase):
    def test_edges_mirror_the_parent_links(self):
        doc = cartographer.to_archify(_graph())
        pairs = {(e["from"], e["to"]) for e in doc["edges"]}
        self.assertIn(("n01_designer", "n02_backend_builder"), pairs)
        self.assertIn(("n02_backend_builder", "n03_payments_engineer"), pairs)
        self.assertEqual(len(doc["edges"]), 2)

    def test_edge_ids_are_unique(self):
        doc = cartographer.to_archify(_graph())
        ids = [e["id"] for e in doc["edges"]]
        self.assertEqual(len(ids), len(set(ids)))


class ComponentTypeTest(unittest.TestCase):
    def test_type_describes_the_component_not_the_run_state(self):
        """archify's `type` is a component kind; only these values are legal."""
        legal = {"frontend", "backend", "database", "cloud", "security",
                 "messagebus", "external"}
        doc = cartographer.to_archify(_graph())
        for node in doc["nodes"]:
            self.assertIn(node["type"], legal, node["id"])

    def test_a_designer_draws_as_frontend_and_a_backend_builder_as_backend(self):
        doc = cartographer.to_archify(_graph())
        by_id = {n["id"]: n for n in doc["nodes"]}
        self.assertEqual(by_id["n01_designer"]["type"], "frontend")
        self.assertEqual(by_id["n02_backend_builder"]["type"], "backend")


class LedgerStateTest(unittest.TestCase):
    def test_state_rides_in_the_sublabel_because_workflow_nodes_have_no_variant(self):
        """archify workflow nodes are additionalProperties:false and allow no
        variant, so run state has to be text the reader can see."""
        doc = cartographer.to_archify(_graph(), outcomes={
            "n01_designer": "passed", "n02_backend_builder": "failed",
        })
        by_id = {n["id"]: n for n in doc["nodes"]}
        self.assertIn("passed", by_id["n01_designer"]["sublabel"])
        self.assertIn("failed", by_id["n02_backend_builder"]["sublabel"])
        self.assertNotEqual(by_id["n01_designer"]["sublabel"],
                            by_id["n02_backend_builder"]["sublabel"])

    def test_a_node_with_no_recorded_outcome_says_not_run(self):
        doc = cartographer.to_archify(_graph(), outcomes={})
        for node in doc["nodes"]:
            self.assertIn("not run", node["sublabel"], node["id"])

    def test_no_node_carries_a_property_the_schema_forbids(self):
        """additionalProperties is false; an extra key fails the whole render."""
        allowed = {"id", "lane", "col", "type", "label", "sublabel", "tag",
                   "brand", "width", "height", "yOffset"}
        doc = cartographer.to_archify(_graph(), outcomes={"n01_designer": "passed"})
        for node in doc["nodes"]:
            self.assertEqual(set(node) - allowed, set(), node["id"])


class MotionTest(unittest.TestCase):
    def test_the_live_trace_is_opt_in(self):
        """Static must be the default; motion is a reader's choice."""
        self.assertNotIn("animation", cartographer.to_archify(_graph())["meta"])

    def test_requesting_a_trace_sets_archifys_own_flag(self):
        doc = cartographer.to_archify(_graph(), animate=True)
        self.assertEqual(doc["meta"]["animation"], "trace")


class PurityTest(unittest.TestCase):
    def test_the_source_graph_is_not_mutated(self):
        graph = _graph()
        before = [dict(n) for n in graph["nodes"]]
        cartographer.to_archify(graph, outcomes={"n01_designer": "passed"})
        self.assertEqual(graph["nodes"], before)

    def test_the_module_opens_no_sockets_or_boards(self):
        """A projection that could write would be a second path to the state."""
        source = (Path(__file__).resolve().parents[1] / "src" / "cartographer.py"
                  ).read_text(encoding="utf-8")
        for forbidden in ("import board", "import socket", "sqlite3", "urllib"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
