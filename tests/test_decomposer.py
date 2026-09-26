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
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import capabilities  # noqa: E402
import executor  # noqa: E402
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


class BoardSlugTest(unittest.TestCase):
    """`--board` takes a slug, not a path.

    Found by spiking rather than by reading: passing a tempfile path produced
    `kanban: invalid board slug ... must be 1-64 chars, lowercase
    alphanumerics / hyphens / underscores`. The module had always described the
    argument as a board and never said which kind, so the first real call
    failed on an error message from two layers down.
    """

    def test_a_filesystem_path_is_refused_before_hermes_is_reached(self):
        for bad in (r"C:\Users\x\board.db", "/tmp/board.db", "Board", "-lead",
                    "_x", "has space", "x" * 65, ""):
            with self.assertRaises(decomposer.DecomposeError, msg=bad):
                decomposer.check_board_slug(bad)

    def test_an_ordinary_slug_passes(self):
        for good in ("triai", "tri-ai-sandbox", "board_1", "a", "x" * 64):
            self.assertEqual(decomposer.check_board_slug(good), good)

    def test_the_refusal_says_what_a_slug_is(self):
        with self.assertRaises(decomposer.DecomposeError) as caught:
            decomposer.check_board_slug("/tmp/board.db")
        self.assertIn("slug", str(caught.exception).lower())


class WarrantsAttemptTest(unittest.TestCase):
    """Whether a request is worth splitting at all.

    Decomposition costs a model call and a round of board churn before any
    work starts, so it is not free and must not run on everything. Hermes has
    the better judgement about *how* to split and answers `fanout` either way;
    this only decides whether asking is worth the latency.

    Deliberately permissive. A false yes costs one extra call and Hermes then
    says `fanout: false`; a false no silently sends a seven-agent job to a
    single agent, which is the failure this whole step exists to remove.
    """

    def test_a_substantial_multipart_request_is_worth_asking_about(self):
        for text in (
            "Build a 3D interactive portfolio landing page with a WebGL hero, "
            "three project sections and a contact block",
            "build me a shop with a product grid, a cart, a checkout page and "
            "an admin view for stock",
        ):
            self.assertTrue(decomposer.warrants_attempt(text), text)

    def test_a_small_repair_is_not(self):
        for text in ("add a starfield", "fix the header spacing",
                     "make it darker", "change the title to Kaya"):
            self.assertFalse(decomposer.warrants_attempt(text), text)

    def test_a_single_deliverable_with_detail_is_not_split(self):
        """One page, described carefully, is still one page."""
        self.assertFalse(decomposer.warrants_attempt(
            "build me a recipe card page for a masala chai with the "
            "ingredients, the method and the timings"))

    def test_empty_or_trivial_text_is_never_worth_a_call(self):
        for text in ("", "   ", "hey", "stop"):
            self.assertFalse(decomposer.warrants_attempt(text), text)


class BinaryResolutionTest(unittest.TestCase):
    """Decomposition must find Hermes wherever the worker finds it.

    Measured in production, not here: the first real fan-out on the desktop
    declined with `FileNotFoundError: [WinError 2] The system cannot find the
    file specified` and fell back to one agent. The daemon's scheduled task
    runs `-NoProfile`, so the user PATH that makes a bare `hermes` resolve in
    a shell is not present.

    `executor.run_agent` never had this problem because it spawns
    `executor.hermes_bin()`, an absolute path with a `TRIAI_HERMES_BIN`
    override. Two call sites resolving the same binary two ways is the bug;
    agents ran while decomposition could not even start.
    """

    def test_the_resolved_binary_is_spawned_rather_than_a_bare_name(self):
        seen = {}

        class _Contained:
            def __init__(self):
                self.proc = mock.Mock(returncode=0)
                self.proc.communicate.return_value = ('{"ok": true}', "")

        def capture(command, **kwargs):
            seen["command"] = command
            return _Contained()

        with mock.patch.object(executor, "spawn_contained", side_effect=capture):
            decomposer._hermes(["kanban", "list"], 30)

        self.assertNotEqual(seen["command"][0], "hermes",
                            "a bare name needs a PATH the daemon does not have")
        self.assertEqual(seen["command"][0], str(executor.hermes_bin()))
        self.assertEqual(seen["command"][1:], ["kanban", "list"])

    def test_the_override_is_honoured_so_both_call_sites_move_together(self):
        with mock.patch.dict(os.environ, {"TRIAI_HERMES_BIN": r"D:\tools\hermes.exe"}):
            self.assertEqual(str(executor.hermes_bin()), r"D:\tools\hermes.exe")


class BoardCreationTest(unittest.TestCase):
    """The board has to exist before a card can be put on it.

    `hermes kanban --board <slug> create` does not create the board; it exits
    1 with "board 'triai-intake' does not exist". The earlier spike passed
    only because `triai-spike` already existed on that machine, so the gap
    survived a green spike and a green suite and would have failed the first
    real fan-out - immediately after the PATH fix that was supposed to unblock
    it.

    Creation is idempotent by tolerance rather than by a lookup: asking
    whether it exists and then creating it is two calls and a race, while
    creating it and ignoring "already exists" is one call and no race.
    """

    def _run(self, calls, results):
        def fake(args, timeout):
            calls.append(list(args))
            for match, value in results:
                if match in " ".join(args):
                    return value
            return "{}"
        return fake

    def test_the_board_is_created_before_the_card(self):
        calls = []
        results = [
            ("boards create", "created"),
            ("create ", '{"id": "h_root"}'),
            ("decompose", '{"ok": true, "child_ids": ["h_1"], "fanout": true}'),
            ("list", '[{"id": "h_1", "title": "T", "body": "", "assignee": "designer"}]'),
        ]
        with mock.patch.object(decomposer, "_hermes", side_effect=self._run(calls, results)):
            decomposer.run_hermes_decompose("goal", board="triai-intake")
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("boards create triai-intake" in c for c in joined),
                        f"no board creation in {joined}")
        board_at = next(i for i, c in enumerate(joined) if "boards create" in c)
        card_at = next(i for i, c in enumerate(joined)
                       if "--triage" in c)
        self.assertLess(board_at, card_at, "the board must precede the card")

    def test_an_existing_board_is_not_an_error(self):
        """Re-running must be ordinary, not a failure on the second message."""
        calls = []
        results = [
            ("boards create", decomposer.DecomposeError(
                "hermes kanban boards create triai-intake exited 1: "
                "kanban: board 'triai-intake' already exists")),
            ("create ", '{"id": "h_root"}'),
            ("decompose", '{"ok": true, "child_ids": ["h_1"], "fanout": true}'),
            ("list", '[{"id": "h_1", "title": "T", "body": "", "assignee": "designer"}]'),
        ]

        def fake(args, timeout):
            calls.append(list(args))
            for match, value in results:
                if match in " ".join(args):
                    if isinstance(value, Exception):
                        raise value
                    return value
            return "{}"

        with mock.patch.object(decomposer, "_hermes", side_effect=fake):
            result = decomposer.run_hermes_decompose("goal", board="triai-intake")
        self.assertEqual(len(result.children), 1)


class ArchiveAfterReadingTest(unittest.TestCase):
    """Decomposition cards are a planning artifact, not work to be done.

    `hermes kanban` is an execution system: each board carries its own
    dispatcher, and a card left in todo is a card something may later claim
    and run. Tri-AI's own board holds the real tasks, with the verify gate and
    the workspace policy - the Hermes cards exist only to be read back.

    Found on the desktop: after two decompositions the intake board held
    `running=5, todo=5`. Nothing was executing (no gateway was up for that
    board, and no process existed), so nothing ran unsupervised - but the
    cards were dispatchable, and a gateway started later would have run them
    outside every gate Tri-AI applies.
    """

    def _decompose_with(self, calls):
        results = [
            ("boards create", "ok"),
            ("create ", '{"id": "h_root"}'),
            ("decompose", '{"ok": true, "child_ids": ["h_1", "h_2"], "fanout": true}'),
            ("list", '[{"id": "h_1", "title": "A", "body": "", "assignee": "designer"},'
                     ' {"id": "h_2", "title": "B", "body": "", "assignee": "builder"}]'),
        ]

        def fake(args, timeout):
            calls.append(list(args))
            for match, value in results:
                if match in " ".join(args):
                    return value
            return "{}"
        return mock.patch.object(decomposer, "_hermes", side_effect=fake)

    def test_the_root_and_its_children_are_archived_once_read(self):
        calls = []
        with self._decompose_with(calls):
            decomposer.run_hermes_decompose("goal", board="triai-intake")
        archived = [c for c in calls if "archive" in c]
        self.assertTrue(archived, f"nothing archived in {[' '.join(c) for c in calls]}")
        ids = " ".join(archived[0])
        for task_id in ("h_root", "h_1", "h_2"):
            self.assertIn(task_id, ids)

    def test_archiving_happens_after_the_children_are_read(self):
        """Archive first and there is nothing left to read back."""
        calls = []
        with self._decompose_with(calls):
            decomposer.run_hermes_decompose("goal", board="triai-intake")
        joined = [" ".join(c) for c in calls]
        read_at = max(i for i, c in enumerate(joined) if " list" in f" {c}")
        archive_at = next(i for i, c in enumerate(joined) if "archive" in c)
        self.assertGreater(archive_at, read_at)

    def test_a_failed_archive_does_not_lose_the_decomposition(self):
        """Tidying is not the point. The graph is."""
        def fake(args, timeout):
            joined = " ".join(args)
            if "archive" in joined:
                raise decomposer.DecomposeError("archive refused")
            if "create " in joined:
                return '{"id": "h_root"}'
            if "decompose" in joined:
                return '{"ok": true, "child_ids": ["h_1"], "fanout": true}'
            if "list" in joined:
                return '[{"id": "h_1", "title": "A", "body": "", "assignee": "designer"}]'
            return "{}"

        with mock.patch.object(decomposer, "_hermes", side_effect=fake):
            result = decomposer.run_hermes_decompose("goal", board="triai-intake")
        self.assertEqual(len(result.children), 1)


class EnvironmentIsolationTest(unittest.TestCase):
    """The decomposition subprocess must not inherit Tri-AI's board.

    `board.py` assigns `HERMES_KANBAN_DB` to ~/.tri-ai/board.db on import, so
    the Hermes kernel Tri-AI embeds uses Tri-AI's own board. Any `hermes`
    subprocess spawned from the same process inherits it - and that variable
    outranks `--board`.

    Measured on a real fan-out: the intake board has its own DB at
    `hermes\kanban\boards\triai-intake\kanban.db`, and the five
    decomposition cards were written into Tri-AI's board.db instead, carrying
    Hermes's `assignee` and no `agent_role` or `verify_command`. Archived, so
    not dispatchable - but five foreign rows per fan-out in the board that
    holds real work.

    `--board` has to be the only thing that decides.
    """

    def test_the_board_variable_is_not_passed_to_the_subprocess(self):
        seen = {}

        class _Contained:
            def __init__(self):
                self.proc = mock.Mock(returncode=0)
                self.proc.communicate.return_value = ('{"ok": true}', "")

        def capture(command, **kwargs):
            seen.update(kwargs)
            return _Contained()

        with mock.patch.dict(os.environ, {"HERMES_KANBAN_DB": r"C:\triai\board.db"}), \
             mock.patch.object(executor, "spawn_contained", side_effect=capture):
            decomposer._hermes(["kanban", "list"], 30)

        env = seen.get("env")
        self.assertIsNotNone(env, "the subprocess must be given an explicit env")
        self.assertNotIn("HERMES_KANBAN_DB", env)

    def test_the_rest_of_the_environment_survives(self):
        """Stripping one variable must not strip PATH and everything else."""
        seen = {}

        class _Contained:
            def __init__(self):
                self.proc = mock.Mock(returncode=0)
                self.proc.communicate.return_value = ("{}", "")

        def capture(command, **kwargs):
            seen.update(kwargs)
            return _Contained()

        with mock.patch.dict(os.environ, {"HERMES_KANBAN_DB": "x", "SENTINEL": "keep"}), \
             mock.patch.object(executor, "spawn_contained", side_effect=capture):
            decomposer._hermes(["kanban", "list"], 30)

        self.assertEqual(seen["env"].get("SENTINEL"), "keep")
