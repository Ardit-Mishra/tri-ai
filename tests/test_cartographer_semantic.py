"""The diagram has to show the dependency graph, now that there is one.

`cartographer` was written against phase-derived edges — a complete bipartite
mesh between adjacent phases, "two design nodes and five build nodes is ten
edges that all say the same thing" — so it collapsed them to one edge per
phase transition labelled "N in parallel".

Both halves of that are now wrong.

**The edges carry information.** Since `ba462e5` the decomposer reads the
dependencies Hermes actually recorded: the real seven-child portfolio
decomposition has six edges, each builder waiting on *its own* designer.
Collapsing those to "design → build ×3" destroys exactly what makes the graph
worth drawing.

**Nothing runs in parallel.** One worker daemon, `run_once` called
synchronously. "N in parallel" is a claim the runtime cannot back.

And phase cannot supply the column any more. "Integrate the sections" is a
`frontend_builder`, the same phase as the three builders it waits for, so a
phase-derived column would draw a dependency inside one column. The column is
time, and depth in the dependency graph is what time means here.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cartographer  # noqa: E402
import decomposer  # noqa: E402

WORKSPACE = str(Path(tempfile.gettempdir()).resolve())

# The real decomposition, read off the spike board: three designers, three
# builders each waiting on its own, and an integrator waiting on the three.
PORTFOLIO = (
    ("t_1", "Design the hero", "designer", ()),
    ("t_2", "Design the projects", "designer", ()),
    ("t_3", "Design the contact block", "designer", ()),
    ("t_4", "Build the hero", "frontend_builder", ("t_1",)),
    ("t_5", "Build the projects", "frontend_builder", ("t_2",)),
    ("t_6", "Build the contact block", "frontend_builder", ("t_3",)),
    ("t_7", "Integrate the sections", "frontend_builder", ("t_4", "t_5", "t_6")),
)


def _graph(rows=PORTFOLIO):
    return decomposer.build_graph(
        decomposer.Decomposition(
            goal="Build a portfolio landing page",
            root_task_id="t_root",
            children=[
                decomposer.Child(task_id=tid, title=title, body="",
                                 assignee=role, parents=tuple(parents))
                for tid, title, role, parents in rows
            ],
        ),
        workspace_kind="dir", workspace_path=WORKSPACE,
    )


def _flat(rows=PORTFOLIO):
    """The same children with the links dropped — the phase-mesh case."""
    return decomposer.build_graph(
        decomposer.Decomposition(
            goal="Build a portfolio landing page",
            root_task_id="t_root",
            children=[
                decomposer.Child(task_id=tid, title=title, body="",
                                 assignee=role)
                for tid, title, role, _ in rows
            ],
        ),
        workspace_kind="dir", workspace_path=WORKSPACE,
    )


def _titles(graph):
    """node_key -> the task's full title.

    Not the archify label: that is clipped to fit a box, so comparing against
    it would test the clip width rather than the graph.
    """
    return {str(n["node_key"]): str(n["title"]) for n in graph["nodes"]}


class RealEdgesAreDrawnTest(unittest.TestCase):
    def setUp(self) -> None:
        graph = _graph()
        self.doc = cartographer.to_archify(graph)
        self.title = _titles(graph)

    def _edges(self):
        return {(self.title[e["from"]], self.title[e["to"]])
                for e in self.doc["edges"]}

    def test_every_recorded_dependency_becomes_an_edge(self):
        self.assertEqual(len(self.doc["edges"]), 6)

    def test_a_builder_is_joined_to_its_own_designer(self):
        self.assertIn(("Design the contact block", "Build the contact block"),
                      self._edges())

    def test_a_builder_is_not_joined_to_another_designer(self):
        self.assertNotIn(("Design the hero", "Build the contact block"),
                         self._edges())

    def test_the_integrator_is_joined_to_all_three_builders(self):
        joined = {src for src, dst in self._edges()
                  if dst == "Integrate the sections"}
        self.assertEqual(joined, {"Build the hero", "Build the projects",
                                  "Build the contact block"})

    def test_nothing_claims_parallelism(self):
        """One worker daemon. "3 in parallel" was never true."""
        labels = " ".join(str(e.get("label") or "") for e in self.doc["edges"])
        self.assertNotIn("parallel", labels.casefold())


class ColumnsAreDepthNotPhaseTest(unittest.TestCase):
    """The column is time, so a node sits after everything it waits for."""

    def setUp(self) -> None:
        graph = _graph()
        self.doc = cartographer.to_archify(graph)
        self.col = {n["id"]: n["col"] for n in self.doc["nodes"]}
        self.title = _titles(graph)
        self.by_title = {v: k for k, v in self.title.items()}

    def _col(self, title):
        return self.col[self.by_title[title]]

    def test_every_edge_points_forward(self):
        for edge in self.doc["edges"]:
            self.assertLess(
                self.col[edge["from"]], self.col[edge["to"]],
                f"{self.title[edge['from']]} -> {self.title[edge['to']]} "
                "does not move forward in time",
            )

    def test_the_integrator_is_after_the_builders_it_waits_for(self):
        """Phase alone cannot say this: all four are `frontend_builder`."""
        self.assertGreater(self._col("Integrate the sections"),
                           self._col("Build the hero"))

    def test_same_lane_siblings_cannot_share_a_column(self):
        """Not a depth bug — a rendering constraint the module already knew.

        "a lane frame is 104px tall with a 30px title strip, so a lane holds
        one row of boxes and `yOffset` cannot stack them". The three designers
        are all depth 0 and all in the `designer` lane, so two of them shift
        right to avoid drawing on top of each other. The ordering still holds,
        which is what `test_every_edge_points_forward` checks.
        """
        cols = {self._col(t) for t in ("Design the hero", "Design the projects",
                                       "Design the contact block")}
        self.assertEqual(len(cols), 3, "three boxes in one lane overlap")

    def test_independent_work_in_different_lanes_shares_a_column(self):
        rows = (
            ("t_1", "Design it", "designer", ()),
            ("t_2", "Write the copy", "ux_writer", ()),
            ("t_3", "Build it", "frontend_builder", ("t_1", "t_2")),
        )
        doc = cartographer.to_archify(_graph(rows))
        col = {n["id"]: n["col"] for n in doc["nodes"]}
        title = {v: k for k, v in _titles(_graph(rows)).items()}
        self.assertEqual(col[title["Design it"]], col[title["Write the copy"]])
        self.assertGreater(col[title["Build it"]], col[title["Design it"]])

    def test_the_roots_start_at_zero(self):
        self.assertEqual(self._col("Design the hero"), 0)


class ThePhaseMeshIsStillCollapsedTest(unittest.TestCase):
    """A decomposition Hermes left flat still gets the readable summary.

    Twenty-eight edges saying the same thing is not a diagram, and that case
    has not gone away — `phase_edges` is still the fallback.
    """

    def setUp(self) -> None:
        self.doc = cartographer.to_archify(_flat())

    def test_the_mesh_is_not_drawn_edge_for_edge(self):
        raw = sum(len(n["parents"]) for n in _flat()["nodes"])
        self.assertGreater(raw, len(self.doc["edges"]))

    def test_it_still_says_nothing_about_parallelism(self):
        labels = " ".join(str(e.get("label") or "") for e in self.doc["edges"])
        self.assertNotIn("parallel", labels.casefold())

    def test_the_collapsed_edges_still_point_forward(self):
        col = {n["id"]: n["col"] for n in self.doc["nodes"]}
        for edge in self.doc["edges"]:
            self.assertLess(col[edge["from"]], col[edge["to"]])


class RunStateStillReachesTheNodeTest(unittest.TestCase):
    def test_an_outcome_is_written_into_the_sublabel(self):
        graph = _graph()
        key = graph["nodes"][0]["node_key"]
        doc = cartographer.to_archify(graph, outcomes={key: "passed"})
        node = next(n for n in doc["nodes"] if n["id"] == key)
        self.assertIn("passed", node["sublabel"])

    def test_a_node_with_no_outcome_says_it_has_not_run(self):
        doc = cartographer.to_archify(_graph())
        self.assertIn("not run", doc["nodes"][0]["sublabel"])


if __name__ == "__main__":
    unittest.main()
