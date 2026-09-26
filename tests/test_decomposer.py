"""The decomposer turns a Hermes decomposition into a planner-shaped DAG.

`hermes kanban decompose --json` returns children with no parents *in its
payload* - the edges the model chose are written to `task_links` and have to
be read back with `show --json`. These tests pin the three things that can be
quietly wrong: which role a child maps to, whether the links reach the graph
at all, and whether the resulting edges form a graph the planner will accept.

Nothing here shells out to Hermes. The projection is a pure function over a
Decomposition, which is the reason it is worth having as a separate function.
"""

from __future__ import annotations

import subprocess
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


class GenericVerifyTest(unittest.TestCase):
    """The fallback gate must notice a file that was created, not only edited.

    `git -C . diff --quiet && exit 1 || exit 0` was the fallback, and
    `git diff` ignores untracked files. An agent that creates files - the
    normal case - leaves `git diff` clean, so the gate exits 1 and a real run
    is recorded as failed. An agent that edits a tracked file passes. Backwards
    for the common case.

    Measured: backend_builder produced nine files (backend/main.py,
    database.py, schemas.py, models/models.py and four api/ packages) and was
    failed by this gate, twice, until the circuit breaker blocked it. The
    verify log was zero bytes, so the card could not say why either.
    """

    def test_the_fallback_counts_untracked_files(self):
        self.assertIn("--porcelain", decomposer.GENERIC_VERIFY)

    def test_the_fallback_does_not_rely_on_git_diff_alone(self):
        self.assertNotIn("diff --quiet", decomposer.GENERIC_VERIFY)

    def test_it_passes_when_a_file_was_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
            (root / "seed.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            (root / "made.py").write_text("print(1)\n", encoding="utf-8")
            code = subprocess.run(decomposer.GENERIC_VERIFY, cwd=root, shell=True).returncode
            self.assertEqual(code, 0, "a created file is a change the gate must see")

    def test_it_fails_when_the_run_changed_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
            (root / "seed.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)

            code = subprocess.run(decomposer.GENERIC_VERIFY, cwd=root, shell=True).returncode
            self.assertEqual(code, 1, "a run that produced nothing must fail")


# --- semantic edges ---------------------------------------------------------

# The eight rows below are a real decomposition, read back out of
# `~/AppData/Local/hermes/kanban/boards/triai-spike/kanban.db` after the
# portfolio fan-out. Hermes' own decomposer prompt asks the model for
# `"parents": [<int>, ...]` "expressing actual data dependencies", and
# `kanban_db.decompose_triage_task` writes them into `task_links`. It did:
# seven children, six edges, each builder waiting on *its own* designer and
# the integrator waiting on the three builders.
#
# Tri-AI read `child_ids` and discarded the rest.
REAL = (
    ("t_59b4e84d", "Design the 3D interactive WebGL hero section", "designer", ()),
    ("t_e3c403e7", "Design the three project sections", "designer", ()),
    ("t_1629f33c", "Design the contact block", "designer", ()),
    ("t_9c134c8c", "Build the WebGL hero section", "frontend_builder",
     ("t_59b4e84d",)),
    ("t_19aef098", "Build the three project sections", "frontend_builder",
     ("t_e3c403e7",)),
    ("t_e68ecfb9", "Build the contact block", "frontend_builder",
     ("t_1629f33c",)),
    ("t_9fa69823", "Integrate the sections into a single landing page",
     "frontend_builder", ("t_9c134c8c", "t_19aef098", "t_e68ecfb9")),
)


def _linked(rows=REAL, goal="Build a 3D interactive portfolio landing page"):
    return decomposer.Decomposition(
        goal=goal,
        root_task_id="t_a24d67ab",
        children=[
            decomposer.Child(task_id=tid, title=title, body="",
                             assignee=who, parents=tuple(parents))
            for tid, title, who, parents in rows
        ],
    )


def _parents_by_title(graph):
    key_to_title = {n["node_key"]: n["title"] for n in graph["nodes"]}
    return {
        n["title"]: {key_to_title[p] for p in n["parents"]}
        for n in graph["nodes"]
    }


class SemanticEdgeTest(unittest.TestCase):
    """What Hermes said, not what the phase rank guessed."""

    def test_a_builder_waits_on_its_own_designer_and_no_other(self):
        graph = decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(
            _parents_by_title(graph)["Build the contact block"],
            {"Design the contact block"},
        )

    def test_the_integrator_waits_on_the_builders_it_integrates(self):
        """The phase mesh got this backwards, and not only slowly.

        `Integrate the sections` is a `frontend_builder`, so phase rank puts
        it in the *same* phase as the three builders - siblings, not a child.
        It would therefore be dispatched alongside them and integrate files
        that do not exist yet. The docstring's claim that phase order
        "over-constrains rather than under-constrains" and "costs wall-clock,
        never correctness" is false, and this is the counterexample.
        """
        parents = _parents_by_title(graph := decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE))
        self.assertEqual(
            parents["Integrate the sections into a single landing page"],
            {"Build the WebGL hero section",
             "Build the three project sections",
             "Build the contact block"},
        )
        self.assertEqual(graph["edges_derived_from"], "hermes dependencies")

    def test_one_blocked_designer_strands_only_its_own_builder(self):
        """The whole reason for the change.

        Eleven nodes, twenty-eight phase edges, three blocked designers, all
        seven builders stranded in `todo` for ever. Under semantic edges a
        blocked node takes its own subtree down and nothing else.
        """
        graph = decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE)
        parents = _parents_by_title(graph)
        stranded = {
            title for title, ps in parents.items()
            if "Design the contact block" in ps
        }
        self.assertEqual(stranded, {"Build the contact block"})

    def test_the_edge_count_is_what_hermes_said(self):
        graph = decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(sum(len(n["parents"]) for n in graph["nodes"]), 6)

    def test_the_graph_is_accepted_by_the_planner_unmodified(self):
        graph = decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE)
        ordered = planner.validate_graph(graph)
        self.assertEqual(len(ordered), 7)
        seen: set[str] = set()
        for node in ordered:
            for parent in node.parents:
                self.assertIn(parent, seen)
            seen.add(node.key)

    def test_a_parent_outside_the_decomposition_is_dropped(self):
        """Hermes links the root as a child of every child.

        Read a child's parents naively and the root comes back as one. It is
        not in the graph, so `validate_graph` would refuse the whole document.
        """
        rows = (
            ("t_a", "Design", "designer", ()),
            ("t_b", "Build", "builder", ("t_a", "t_root_not_in_graph")),
        )
        graph = decomposer.build_graph(
            _linked(rows), workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(len(graph["nodes"][1]["parents"]), 1)
        planner.validate_graph(graph)

    def test_a_decomposition_with_no_links_still_gets_phase_order(self):
        """Structure or nothing. A flat list is still better than no graph."""
        graph = decomposer.build_graph(
            _decomp(("Design the flow", "designer"),
                    ("Build the API", "backend_builder")),
            workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(graph["edges_derived_from"], "phase order")
        self.assertEqual(len(graph["nodes"][1]["parents"]), 1)

    def test_a_child_with_no_parents_of_its_own_is_left_a_root(self):
        """Semantic edges are all-or-nothing per decomposition.

        Once Hermes has supplied structure, a parentless child means "this
        starts immediately", not "fill this one in from the phase rank".
        Mixing the two would reintroduce the mesh for exactly the nodes the
        model said were independent.
        """
        graph = decomposer.build_graph(
            _linked(), workspace_kind="dir", workspace_path=WORKSPACE)
        designers = [n for n in graph["nodes"] if n["agent_role"] == "designer"]
        self.assertEqual(len(designers), 3)
        for node in designers:
            self.assertEqual(node["parents"], [])


class ReadingTheLinksBackTest(unittest.TestCase):
    """`decompose --json` returns only `child_ids`; the edges are in the board.

    `kanban_db.decompose_triage_task` writes the model's `parents` indices
    into `task_links` inside the same write transaction that creates the
    children, and `kanban show --json` is the published way to read them out.
    Nothing in `decompose --json` or `list --json` carries them, so without
    this pass the structure exists on the board and never reaches the graph.
    """

    CHILDREN = ["h_design", "h_build"]

    def _run(self, show, calls=None):
        """`show` maps a child id to its `kanban show --json` output."""
        calls = calls if calls is not None else []

        def fake(args, timeout):
            calls.append(list(args))
            joined = " ".join(args)
            if "boards create" in joined:
                return "ok"
            if " show " in f" {joined} ":
                for child in self.CHILDREN:
                    if child in args:
                        value = show[child]
                        if isinstance(value, Exception):
                            raise value
                        return value
            if "create " in joined:
                return '{"id": "h_root"}'
            if "decompose" in joined:
                return ('{"ok": true, "fanout": true, "child_ids": '
                        '["h_design", "h_build"]}')
            if " list" in f" {joined}":
                return ('[{"id": "h_design", "title": "Design", "body": "",'
                        '  "assignee": "designer"},'
                        ' {"id": "h_build", "title": "Build", "body": "",'
                        '  "assignee": "frontend_builder"}]')
            return "{}"

        with mock.patch.object(decomposer, "_hermes", side_effect=fake):
            return decomposer.run_hermes_decompose("goal", board="triai-intake")

    def test_the_links_reach_the_children(self):
        result = self._run({
            "h_design": '{"task": {"id": "h_design"}, "parents": []}',
            "h_build": '{"task": {"id": "h_build"}, "parents": ["h_design"]}',
        })
        by_id = {c.task_id: c for c in result.children}
        self.assertEqual(by_id["h_build"].parents, ("h_design",))
        self.assertEqual(by_id["h_design"].parents, ())

    def test_the_links_are_read_before_the_cards_are_archived(self):
        """Archive first and the links are gone with the cards."""
        calls: list[list[str]] = []
        self._run({
            "h_design": '{"parents": []}',
            "h_build": '{"parents": ["h_design"]}',
        }, calls)
        joined = [" ".join(c) for c in calls]
        last_show = max(i for i, c in enumerate(joined) if " show " in f" {c} ")
        archive = next(i for i, c in enumerate(joined) if "archive" in c)
        self.assertGreater(archive, last_show)

    def test_one_unreadable_child_drops_every_semantic_edge(self):
        """Partial structure fails in the expensive direction.

        A missed edge runs a task before its input exists, which is the one
        failure the phase mesh could not produce. So a decomposition whose
        links cannot all be read is treated as having none, and falls back to
        the over-constraining phase order rather than to a graph with a hole
        in it.
        """
        result = self._run({
            "h_design": '{"parents": []}',
            "h_build": decomposer.DecomposeError("show failed"),
        })
        self.assertEqual([c.parents for c in result.children], [(), ()])
        graph = decomposer.build_graph(
            result, workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(graph["edges_derived_from"], "phase order")

    def test_a_decomposition_hermes_left_flat_is_not_an_error(self):
        result = self._run({
            "h_design": '{"parents": []}',
            "h_build": '{"parents": []}',
        })
        self.assertEqual(len(result.children), 2)
        self.assertEqual([c.parents for c in result.children], [(), ()])

    def test_the_end_to_end_graph_carries_the_edge(self):
        result = self._run({
            "h_design": '{"parents": []}',
            "h_build": '{"parents": ["h_design"]}',
        })
        graph = decomposer.build_graph(
            result, workspace_kind="dir", workspace_path=WORKSPACE)
        self.assertEqual(graph["edges_derived_from"], "hermes dependencies")
        ordered = planner.validate_graph(graph)
        self.assertEqual([n.title for n in ordered], ["Design", "Build"])
        self.assertEqual(len(ordered[1].parents), 1)
