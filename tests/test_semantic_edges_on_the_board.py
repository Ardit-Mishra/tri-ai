"""One blocked node must strand its own subtree and nothing else.

`test_decomposer.py` pins the shape of the graph *document*. This pins what
the board does with it, which is the claim that actually matters: the
eleven-node fan-out stranded all seven builders in `todo` for ever because
three of four designers blocked, and every builder waited on every designer.

The kernel decides readiness, not Tri-AI, so asserting on the document alone
would leave the real question — *which tasks can still be claimed* — untested.
Everything here goes through `planner.write_graph` into a throwaway board and
reads `ready_tasks` back.

The fixture is the decomposition Hermes actually produced for the portfolio
request, read out of the spike board: three designers, three builders each
waiting on its own designer, and an integrator waiting on the three builders.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import BoardTestCase  # noqa: E402

import board  # noqa: E402
import decomposer  # noqa: E402
import planner  # noqa: E402


WORKSPACE = str(Path(tempfile.gettempdir()).resolve())

# (hermes id, title, role, hermes parent ids) — the real seven.
PORTFOLIO = (
    ("t_59b4e84d", "Design the hero", "designer", ()),
    ("t_e3c403e7", "Design the projects", "designer", ()),
    ("t_1629f33c", "Design the contact block", "designer", ()),
    ("t_9c134c8c", "Build the hero", "frontend_builder", ("t_59b4e84d",)),
    ("t_19aef098", "Build the projects", "frontend_builder", ("t_e3c403e7",)),
    ("t_e68ecfb9", "Build the contact block", "frontend_builder", ("t_1629f33c",)),
    ("t_9fa69823", "Integrate the sections", "frontend_builder",
     ("t_9c134c8c", "t_19aef098", "t_e68ecfb9")),
)


def _decomposition(rows=PORTFOLIO):
    return decomposer.Decomposition(
        goal="Build a 3D interactive portfolio landing page",
        root_task_id="t_a24d67ab",
        children=[
            decomposer.Child(task_id=tid, title=title, body="",
                             assignee=role, parents=tuple(parents))
            for tid, title, role, parents in rows
        ],
    )


class SemanticEdgesOnTheBoardTest(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        graph = decomposer.build_graph(
            _decomposition(), workspace_kind="dir", workspace_path=WORKSPACE,
            verify_command="python -c \"pass\"", verify_timeout=60,
        )
        self.assertEqual(graph["edges_derived_from"], "hermes dependencies")
        self.keys = planner.write_graph(self.conn, graph)
        self.titles = {
            task_id: title for task_id, title in self.conn.execute(
                "SELECT id, title FROM tasks")
        }

    def _claimable(self) -> set[str]:
        return {self.titles[t["id"]] for t in board.ready_tasks(self.conn)}

    def _block(self, title: str) -> None:
        """Block a node the way the real run did: the circuit breaker.

        Status alone is not enough. `recompute_ready` promotes a `blocked`
        task back to `ready` when its parents are satisfied, precisely so a
        task blocked by a dependency unblocks itself — it stays blocked only
        when the most recent block was worker-initiated, or when
        `consecutive_failures` has reached the limit. The three designers in
        the eleven-node run showed `fails=2` against a `DEFAULT_FAILURE_LIMIT`
        of 2, so that is what is reproduced here.
        """
        task_id = next(t for t, name in self.titles.items() if name == title)
        self.conn.execute(
            "UPDATE tasks SET status = 'blocked', consecutive_failures = 2 "
            "WHERE id = ?", (task_id,))
        self.conn.commit()
        board.kanban().recompute_ready(self.conn)

    def test_the_board_carries_exactly_the_edges_hermes_recorded(self):
        count = self.conn.execute(
            "SELECT COUNT(*) FROM task_links").fetchone()[0]
        self.assertEqual(count, 6, "28 was the mesh; 6 is what the model said")

    def test_all_three_designers_start_at_once(self):
        self.assertEqual(
            self._claimable(),
            {"Design the hero", "Design the projects", "Design the contact block"},
        )

    def test_a_blocked_designer_strands_only_its_own_builder(self):
        """The eleven-node run's failure, reproduced and then not reproduced.

        Under the phase mesh every builder waited on every designer, so this
        one blocked card stranded all of them. Here it must cost exactly one.
        """
        self._block("Design the contact block")
        self.conn.execute(
            "UPDATE tasks SET status = 'done' WHERE title IN "
            "('Design the hero', 'Design the projects')")
        self.conn.commit()
        board.kanban().recompute_ready(self.conn)
        self.assertEqual(
            self._claimable(), {"Build the hero", "Build the projects"})

    def test_the_integrator_never_runs_before_the_builders_it_integrates(self):
        """The mesh's *other* failure: an under-constrained same-phase node.

        `Integrate the sections` is a frontend_builder, the same phase as the
        three builders, so phase rank made it their sibling and the dispatcher
        could claim it against files that did not exist.
        """
        for title in ("Design the hero", "Design the projects",
                      "Design the contact block"):
            self.conn.execute(
                "UPDATE tasks SET status = 'done' WHERE title = ?", (title,))
        self.conn.commit()
        board.kanban().recompute_ready(self.conn)
        self.assertNotIn("Integrate the sections", self._claimable())

        for title in ("Build the hero", "Build the projects",
                      "Build the contact block"):
            self.conn.execute(
                "UPDATE tasks SET status = 'done' WHERE title = ?", (title,))
        self.conn.commit()
        board.kanban().recompute_ready(self.conn)
        self.assertEqual(self._claimable(), {"Integrate the sections"})

    def test_the_phase_mesh_would_have_stranded_all_three_builders(self):
        """The counterfactual, on the same board, so the fix is not a claim.

        Same seven children with the links dropped, which is what this module
        saw before it learned to read them.
        """
        flat = decomposer.build_graph(
            _decomposition(tuple((tid, f"{title} (flat)", role, ())
                                 for tid, title, role, _ in PORTFOLIO)),
            workspace_kind="dir", workspace_path=WORKSPACE,
            verify_command="python -c \"pass\"", verify_timeout=60,
        )
        self.assertEqual(flat["edges_derived_from"], "phase order")
        planner.write_graph(self.conn, flat)
        self.titles = {
            task_id: title for task_id, title in self.conn.execute(
                "SELECT id, title FROM tasks")
        }
        self._block("Design the contact block (flat)")

        claimable = self._claimable()
        for stranded in ("Build the hero (flat)", "Build the projects (flat)",
                         "Build the contact block (flat)"):
            self.assertNotIn(stranded, claimable)


if __name__ == "__main__":
    unittest.main()
