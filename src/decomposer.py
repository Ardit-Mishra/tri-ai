"""Turn one sentence into a graph the planner will accept.

`planner.py` consumes a graph document "produced by a strong model or an
operator" and has never had anything upstream to produce one. This is that
upstream, and it delegates the hard half rather than reinventing it.

`hermes kanban decompose` already breaks a goal into sensible child tasks and
routes each to a specialist profile by description. Measured on the same
request: with only `default` present it assigned all six children to `default`;
with one profile per Tri-AI role it produced designer, backend_builder,
frontend_builder, payments_engineer and accessibility_auditor, correctly
sending "paid subscription tier" to the monetisation role.

**Hermes produces a graph too, and this module used to throw it away.** The
first version of this docstring claimed "every child comes back with no
parents"; that was never checked, and it is false. Hermes' decomposer prompt
asks the model for `"parents": [<int>, ...]` "expressing actual data
dependencies", and `kanban_db.decompose_triage_task` writes them into
`task_links` in the same transaction that creates the children. They simply do
not appear in `decompose --json`, which returns only `child_ids`.

Read back off a real seven-child portfolio decomposition, Hermes had recorded
six edges: each builder waiting on *its own* designer, and the integrator
waiting on the three builders. This module derived twenty-eight from the phase
rank instead, and the discarded claim that phase order "over-constrains rather
than under-constrains" and so "costs wall-clock, never correctness" was false
in both halves:

  * it over-constrains across a phase boundary. Three of four designers
    blocked in an eleven-node run, and because every builder waited on every
    designer, **all seven builders were stranded in `todo` permanently**.
  * it *under*-constrains within one. "Integrate the sections" is a
    `frontend_builder`, the same phase as the three builders it integrates, so
    the mesh makes it their sibling and the dispatcher may run it against
    files that do not exist yet.

So edges now come from `semantic_edges`, and `phase_edges` survives only as the
fallback for a decomposition Hermes left flat. The fallback is all-or-nothing:
a half-read set of links would lose an edge silently, and a lost edge runs a
task before its input exists, which is the one failure the mesh could not
produce.

This module writes nothing to the board. It emits a document;
`planner.validate_graph` decides whether that document is fit to persist.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import capabilities
import executor

# The seven original roles predate the phase model; place them where they act.
BASE_ROLE_PHASE = {
    "lead": "build",
    "researcher": "ideation",
    "product_strategist": "ideation",
    "designer": "design",
    "builder": "build",
    "reviewer": "harden",
    "operator": "ship",
}

DEFAULT_PHASES = (
    "ideation", "design", "build", "harden", "ship", "monetise", "market", "govern",
)

# Applied when no stack profile has supplied a real command. Generic, not fake:
# a workspace the agent never changed is a failed task under any stack.
# `git diff` ignores untracked files, so the previous form - `git diff
# --quiet && exit 1 || exit 0` - failed any run that *created* files and
# passed one that edited a tracked file. Backwards for the common case:
# measured on a nine-file FastAPI backend that was failed twice by it, with a
# zero-byte verify log, until the circuit breaker blocked the task.
#
# Written in Python rather than shell because `run_verify` uses shell=True,
# which is cmd.exe here and sh elsewhere - `test -n` is not a cmd builtin and
# silently succeeded. Python is the one interpreter this repo can count on.
GENERIC_VERIFY = (
    'python -c "'
    "import subprocess,sys;"
    "out=subprocess.run(['git','status','--porcelain','--untracked-files=all'],"
    "capture_output=True,text=True).stdout.strip();"
    "print(out or 'verify: this run changed nothing in the workspace');"
    'sys.exit(0 if out else 1)"'
)
DEFAULT_VERIFY_TIMEOUT = 600


class DecomposeError(RuntimeError):
    """The upstream decomposition could not be produced or read."""


@dataclass
class Child:
    task_id: str
    title: str
    body: str
    assignee: str
    # Hermes task ids of this child's siblings that must finish first, read
    # back from `task_links`. Empty means "starts immediately" once the
    # decomposition supplied structure at all - see `semantic_edges`.
    parents: tuple[str, ...] = ()


@dataclass
class Decomposition:
    goal: str
    root_task_id: str
    children: list[Child] = field(default_factory=list)
    fanout: bool = True
    reason: str = ""


# Hermes takes a board *slug*, not a path. Spiking this found the assumption:
# a tempfile path came back as `kanban: invalid board slug ... must be 1-64
# chars, lowercase alphanumerics / hyphens / underscores`, from two layers
# down, after the call had already been made.
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def check_board_slug(board: str) -> str:
    """Return the slug, or refuse it here rather than inside Hermes."""
    text = str(board or "")
    if not _SLUG.match(text):
        raise DecomposeError(
            f"board must be a slug, not a path: {text!r}. Hermes wants 1-64 "
            "characters of lowercase letters, digits, hyphens or underscores, "
            "not starting with a hyphen or underscore."
        )
    return text


# Words that name a separable piece of work. A request listing several is a
# request for several things, whatever its length.
_PARTS = (
    "page", "pages", "section", "sections", "screen", "screens", "view",
    "views", "endpoint", "endpoints", "api", "dashboard", "cart", "checkout",
    "admin", "login", "signup", "form", "grid", "hero", "footer", "header",
    "blog", "gallery", "profile", "settings", "search", "feed", "chart",
    "table", "report", "pipeline", "worker", "service", "database", "schema",
)

# Splitting is not free: it costs a model call and a round of board churn
# before any work starts. Below this a request is almost never worth it.
MIN_FANOUT_WORDS = 12
MIN_FANOUT_PARTS = 2


def warrants_attempt(text: str) -> bool:
    """Whether asking Hermes to split this is worth the latency.

    Not a decision about *how* to split - Hermes is better at that and
    answers `fanout` either way. This only decides whether to ask.

    Deliberately permissive: a false yes costs one extra call and Hermes then
    declines to fan out, while a false no silently hands a seven-agent job to
    a single agent, which is the failure this exists to remove.
    """
    words = re.findall(r"[a-z0-9']+", str(text or "").casefold())
    if len(words) < MIN_FANOUT_WORDS:
        return False
    distinct_parts = {word for word in words if word in _PARTS}
    return len(distinct_parts) >= MIN_FANOUT_PARTS


def _phases() -> tuple[str, ...]:
    try:
        import roles_lifecycle
        return tuple(roles_lifecycle.PHASES)
    except ImportError:
        return DEFAULT_PHASES


def phase_of(role: str) -> str:
    """The lifecycle phase a role acts in. Unknown roles build."""
    try:
        import roles_lifecycle
        lifecycle = roles_lifecycle.LIFECYCLE_ROLES.get(role)
        if lifecycle is not None:
            return lifecycle.phase
    except ImportError:
        pass
    return BASE_ROLE_PHASE.get(role, "build")


def _role_for(assignee: str) -> str:
    """A Hermes profile maps to the role of the same name, or builder.

    `scripts/sync-hermes-profiles.py` creates the profiles from role ids, so
    the names line up by construction. An unrecognised profile builds rather
    than failing the whole graph.
    """
    return assignee if assignee in capabilities.all_roles() else "builder"


def phase_edges(nodes: list[dict[str, Any]]) -> None:
    """Fill in `parents` from phase order. Mutates nodes in place.

    The fallback, not the default - `semantic_edges` runs first and this only
    sees a decomposition Hermes left flat. Each occupied phase depends on the
    previous *occupied* phase, so an empty phase is skipped rather than
    breaking the chain.

    Kept rather than deleted because it is the only edge source that needs no
    model opinion at all: deterministic, acyclic by construction, and correct
    enough to run a flat list in a sensible order. It is a bad *default* - see
    the module docstring for the two ways the mesh fails - and a reasonable
    floor.
    """
    rank = {name: i for i, name in enumerate(_phases())}
    default_rank = rank.get("build", 0)

    def rank_of(node: dict[str, Any]) -> int:
        return rank.get(node["_phase"], default_rank)

    occupied: dict[int, list[str]] = {}
    for node in nodes:
        occupied.setdefault(rank_of(node), []).append(node["node_key"])

    previous: list[str] = []
    for r in sorted(occupied):
        for node in nodes:
            if rank_of(node) == r:
                node["parents"] = list(previous)
        previous = occupied[r]


def semantic_edges(nodes: list[dict[str, Any]]) -> bool:
    """Fill in `parents` from what Hermes actually linked. Mutates in place.

    Returns whether any edge was set, which is the caller's signal to keep
    these edges instead of falling back to `phase_edges`.

    Parents outside this decomposition are dropped. Hermes links the root as
    a child of every child so the root waits for the whole graph; read a
    child's parents naively and the root comes back as one, and
    `validate_graph` would then refuse the entire document for naming a node
    that does not exist.
    """
    by_task_id = {
        node["_hermes_task_id"]: node["node_key"]
        for node in nodes if node.get("_hermes_task_id")
    }
    linked = False
    for node in nodes:
        parents = [
            by_task_id[task_id]
            for task_id in node.pop("_hermes_parents", ())
            if task_id in by_task_id and by_task_id[task_id] != node["node_key"]
        ]
        node["parents"] = list(dict.fromkeys(parents))
        linked = linked or bool(parents)
    return linked


def build_graph(
    decomposition: Decomposition,
    *,
    workspace_kind: str,
    workspace_path: str | Path,
    verify_command: Optional[str] = None,
    verify_timeout: int = DEFAULT_VERIFY_TIMEOUT,
) -> dict[str, Any]:
    """Project a decomposition into a planner graph document.

    `verify_command` is one command applied to every node. Per-node,
    stack-aware commands are `stack_profile`'s job; until that exists a node
    carries `verify_generic: true`, so a stack-blind gate can never be mistaken
    for a considered one.
    """
    generic = verify_command is None
    command = verify_command or GENERIC_VERIFY

    nodes: list[dict[str, Any]] = []
    for index, child in enumerate(decomposition.children, start=1):
        role = _role_for(child.assignee)
        prompt = f"{child.title}\n\n{child.body}" if child.body else child.title
        nodes.append({
            "node_key": f"n{index:02d}_{role}",
            "title": child.title,
            "prompt": prompt,
            "agent_role": role,
            "verify_command": command,
            "verify_timeout": verify_timeout,
            "workspace": {"kind": workspace_kind, "path": str(workspace_path)},
            "parents": [],
            # Ignored by validate_graph; kept for provenance and for the
            # cartographer, which draws its lanes from the phase.
            "_phase": phase_of(role),
            "_hermes_task_id": child.task_id,
            "_hermes_assignee": child.assignee,
            "_hermes_parents": tuple(child.parents),
            "verify_generic": generic,
        })

    # Semantic first, phase order only when Hermes supplied no structure at
    # all. Not per node: once the model has said what depends on what, a
    # child it left parentless is one it judged independent, and filling that
    # in from the phase rank would rebuild the mesh for exactly the nodes the
    # model said could start at once.
    if semantic_edges(nodes):
        derived = "hermes dependencies"
    else:
        phase_edges(nodes)
        derived = "phase order"

    return {
        "goal": decomposition.goal,
        "source": "hermes kanban decompose",
        "root_task_id": decomposition.root_task_id,
        "edges_derived_from": derived,
        "nodes": nodes,
    }


# --- talking to Hermes ------------------------------------------------------

def _last_json_object(text: str) -> dict[str, Any]:
    """Take the last JSON object out of Hermes output, banners and all.

    The two commands this module calls do not agree on format: `kanban
    decompose --json` prints one object on one line, while `kanban create
    --json` pretty-prints across many. A line-at-a-time reader handles the
    first and silently fails the second, which is how the first end-to-end run
    died. Scanning back from each `{` and letting the decoder find the end
    handles both, and ignores the env banners Hermes prints first.
    """
    decoder = json.JSONDecoder()
    found: Optional[dict[str, Any]] = None
    index = 0
    while True:
        start = text.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1          # a brace in prose; step past it
            continue
        if isinstance(value, dict):
            found = value
        index = end                    # skip the whole object, nested braces included
    if found is not None:
        return found
    raise DecomposeError(f"no JSON object in hermes output: {text.strip()[:200]}")


def _hermes(args: Sequence[str], timeout: int) -> str:
    """Run one Hermes command through the audited containment gateway.

    Not `subprocess.run`. Process creation in this repo is confined to
    `executor.spawn_contained`, so that a timed-out child cannot leave a
    detached grandchild writing to the workspace after the caller has given up.
    `hermes` spawns its own helpers, which is exactly the case the job object
    exists for.
    """
    # The resolved binary, not a bare name. The daemon's scheduled task runs
    # -NoProfile and has no user PATH, so `hermes` alone raises FileNotFoundError
    # there - measured on the first real fan-out, which declined and fell back
    # to a single agent. `executor.run_agent` has always spawned this same
    # path, which is why agents ran while decomposition could not start.
    # `board.py` assigns HERMES_KANBAN_DB to Tri-AI's own board on import, and
    # a subprocess inherits it - where it outranks `--board`. Measured on a
    # real fan-out: the intake board has its own DB, and the five
    # decomposition cards were written into Tri-AI's production board instead,
    # carrying Hermes's `assignee` and none of Tri-AI's own fields. Archived,
    # so not dispatchable, but five foreign rows per fan-out in the board that
    # holds real work. `--board` has to be the only thing that decides.
    env = {k: v for k, v in os.environ.items() if k != "HERMES_KANBAN_DB"}
    contained = executor.spawn_contained(
        [str(executor.hermes_bin()), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        out, err = contained.proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        gone, survivors = contained.terminate_tree()
        raise DecomposeError(
            f"hermes {' '.join(args)} timed out after {timeout}s"
            + ("" if gone else f"; processes may survive: {survivors}")
        ) from None

    if contained.proc.returncode != 0:
        raise DecomposeError(
            f"hermes {' '.join(args)} exited {contained.proc.returncode}: "
            f"{(err or out or '').strip()[:300]}"
        )
    return out


def ensure_board(board: str, *, timeout: int = 60) -> None:
    """Create the decomposition board, tolerating one that already exists.

    `hermes kanban --board <slug> create` does not create the board - it exits
    1 with "board does not exist". The first spike passed only because that
    board already existed on the machine it ran on, so the gap survived a
    green spike and would have failed the first real fan-out.

    Idempotent by tolerance rather than by a lookup: asking whether it exists
    and then creating it is two calls and a race, while creating it and
    ignoring the refusal is one call and none.
    """
    try:
        _hermes(["kanban", "boards", "create", board], timeout)
    except DecomposeError as exc:
        if "already exists" not in str(exc).casefold():
            raise


def read_links(
    child_ids: Sequence[str], *, board: str, timeout: int = 60
) -> dict[str, tuple[str, ...]]:
    """Read each child's sibling parents off the board, or give up entirely.

    Hermes' decomposer prompt asks the model for `"parents": [<int>, ...]`
    "expressing actual data dependencies", and `decompose_triage_task` writes
    them into `task_links` in the same transaction that creates the children.
    None of that appears in `decompose --json`, which returns only
    `child_ids`, nor in `list --json`. `show --json` is the published reader.

    One call per child, which is the cost of using the CLI rather than
    reaching into another board's SQLite file. Measured against seven
    children it is worth it: the links Hermes had already written were the
    difference between six correct edges and a twenty-eight-edge mesh.

    All or nothing. A child whose links cannot be read would come back
    looking independent, and an edge lost that way runs a task before its
    input exists - the one failure the phase mesh could not produce. So any
    refusal empties the whole mapping and the caller falls back to phase
    order, which over-constrains instead.
    """
    links: dict[str, tuple[str, ...]] = {}
    known = set(child_ids)
    for child_id in child_ids:
        try:
            shown = _last_json_object(_hermes(
                ["kanban", "--board", board, "show", child_id, "--json"], timeout))
        except DecomposeError:
            return {}
        parents = shown.get("parents")
        if not isinstance(parents, list):
            return {}
        links[child_id] = tuple(
            p for p in parents if isinstance(p, str) and p in known and p != child_id
        )
    return links


def run_hermes_decompose(
    goal: str, *, board: str, body: str = "", timeout: int = 600
) -> Decomposition:
    """Create a triage card, decompose it, and read the children back."""
    check_board_slug(board)
    ensure_board(board)
    created = _last_json_object(_hermes(
        ["kanban", "--board", board, "create", goal, "--triage",
         *(["--body", body] if body else []), "--json"], 180))
    root = created.get("id")
    if not root:
        raise DecomposeError(f"hermes create returned no id: {created}")

    result = _last_json_object(_hermes(
        ["kanban", "--board", board, "decompose", root, "--json"], timeout))
    if not result.get("ok"):
        raise DecomposeError(f"decompose refused: {result.get('reason')!r}")

    listing = json.loads(_hermes(["kanban", "--board", board, "list", "--json"], 180))
    tasks = listing if isinstance(listing, list) else listing.get("tasks", [])
    by_id = {t["id"]: t for t in tasks}

    ordered_ids = [cid for cid in (result.get("child_ids") or []) if cid in by_id]
    if not ordered_ids:
        raise DecomposeError("decompose reported children but none were readable")

    links = read_links(ordered_ids, board=board)
    children = [
        Child(
            task_id=cid,
            title=(by_id[cid].get("title") or "").strip(),
            body=(by_id[cid].get("body") or "").strip(),
            assignee=(by_id[cid].get("assignee") or "builder").strip(),
            parents=links.get(cid, ()),
        )
        for cid in ordered_ids
    ]

    # The cards have been read; they are a planning artifact and must not be
    # left where something can run them. `hermes kanban` is an execution
    # system - each board carries a dispatcher, and a card in todo is a card a
    # gateway may later claim. Tri-AI's own board holds the real tasks, with
    # the verify gate and the workspace policy that Hermes knows nothing about.
    #
    # After the archive fails the graph is still correct, so a refusal here is
    # logged by omission rather than raised: tidying is not the point.
    try:
        _hermes(["kanban", "--board", board, "archive", root,
                 *(child.task_id for child in children)], 120)
    except DecomposeError:
        pass

    return Decomposition(
        goal=goal, root_task_id=root, children=children,
        fanout=bool(result.get("fanout")), reason=str(result.get("reason") or ""),
    )


def decompose(
    goal: str, *, board: str, workspace_kind: str, workspace_path: str | Path,
    body: str = "", verify_command: Optional[str] = None,
    verify_timeout: int = DEFAULT_VERIFY_TIMEOUT, timeout: int = 600,
) -> dict[str, Any]:
    """One sentence in, one planner-shaped graph document out."""
    return build_graph(
        run_hermes_decompose(goal, board=board, body=body, timeout=timeout),
        workspace_kind=workspace_kind, workspace_path=workspace_path,
        verify_command=verify_command, verify_timeout=verify_timeout,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Decompose a goal into a planner graph.")
    ap.add_argument("goal")
    ap.add_argument("--board", required=True, help="Hermes kanban board slug")
    ap.add_argument("--body", default="")
    ap.add_argument("--workspace-kind", default="dir", choices=("dir", "worktree"))
    ap.add_argument("--workspace-path", required=True)
    ap.add_argument("--verify-command", default=None)
    ap.add_argument("--verify-timeout", type=int, default=DEFAULT_VERIFY_TIMEOUT)
    ap.add_argument("--no-detect-stack", action="store_true",
                    help="keep the stack-blind gate instead of detecting one")
    ap.add_argument("--out", default="-")
    args = ap.parse_args(argv)

    try:
        graph = decompose(
            args.goal, board=args.board, body=args.body,
            workspace_kind=args.workspace_kind, workspace_path=args.workspace_path,
            verify_command=args.verify_command, verify_timeout=args.verify_timeout,
        )
    except DecomposeError as exc:
        print(f"decompose failed: {exc}")
        return 1

    # No explicit command means the nodes are on the stack-blind gate. Ask the
    # workspace what it is rather than leaving them there.
    if args.verify_command is None and not args.no_detect_stack:
        import stack_profile
        profile = stack_profile.apply(graph, args.workspace_path)
        graph["verify_stack"] = profile.stack
        graph["verify_stack_evidence"] = profile.evidence
        print(f"stack: {profile.stack}"
              + (f" (from {', '.join(profile.evidence)})" if profile.evidence else "")
              + (" - generic gate" if profile.generic else ""))

    text = json.dumps(graph, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(graph['nodes'])} nodes -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
