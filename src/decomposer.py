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

What Hermes does *not* produce is a graph. Every child comes back with no
parents, so "integrate all the components" does not depend on the components.

Edges are therefore derived from **phase order, not semantics**. A node depends
on every node in the nearest earlier phase the graph actually contains. That is
deliberately conservative:

  * it cannot produce a cycle, because edges only point backwards through a
    fixed phase sequence;
  * it is deterministic, so a wrong edge is a wrong phase mapping rather than a
    model's opinion;
  * it over-constrains rather than under-constrains. Build work that could have
    started before design finished will wait. That costs wall-clock, never
    correctness, and the dispatcher still runs a whole phase concurrently.

Semantic edges would be tighter, need a second model call, and fail in the
expensive direction: a missed edge runs a task before its input exists.

This module writes nothing to the board. It emits a document;
`planner.validate_graph` decides whether that document is fit to persist.
"""

from __future__ import annotations

import json
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
GENERIC_VERIFY = "git -C . diff --quiet && exit 1 || exit 0"
DEFAULT_VERIFY_TIMEOUT = 600


class DecomposeError(RuntimeError):
    """The upstream decomposition could not be produced or read."""


@dataclass
class Child:
    task_id: str
    title: str
    body: str
    assignee: str


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

    Each occupied phase depends on the previous *occupied* phase, so an empty
    phase is skipped rather than breaking the chain.
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
            "verify_generic": generic,
        })

    phase_edges(nodes)
    return {
        "goal": decomposition.goal,
        "source": "hermes kanban decompose",
        "root_task_id": decomposition.root_task_id,
        "edges_derived_from": "phase order",
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
    contained = executor.spawn_contained(
        [str(executor.hermes_bin()), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
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

    children = [
        Child(
            task_id=cid,
            title=(by_id[cid].get("title") or "").strip(),
            body=(by_id[cid].get("body") or "").strip(),
            assignee=(by_id[cid].get("assignee") or "builder").strip(),
        )
        for cid in (result.get("child_ids") or []) if cid in by_id
    ]
    if not children:
        raise DecomposeError("decompose reported children but none were readable")

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
