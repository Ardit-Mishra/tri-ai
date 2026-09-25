"""Project a task graph into an archify workflow diagram.

You should be able to see how many agents a request deployed and what each one
did without reading JSON. **A lane is an agent and a column is time** - the
classic swimlane, and the model archify is actually built for. Nodes are tasks,
edges are the real dependencies, the tag is the model lane that served the
task, and the sublabel carries the run state and the gate.

An earlier version mapped lanes to phases and put parallel work in one lane at
one column. The renderer refused it, correctly: a lane frame is 104px tall with
a 30px title strip, so a lane holds one row of boxes and `yOffset` cannot stack
them. Phases became columns instead, which is what a column is for.

Emitted twice: once at plan time (what will run) and once at completion (what
did, including the retries and reverts). **The difference between those two is
the honest artifact** - a plan is a claim, and the second diagram is what
actually happened to it.

Pure projection, the same contract as `progress_card` and `completion_report`:
it formats evidence and opens nothing. No board import, no socket, no database.
A diagram that could write would be a second path to the same state, and
`tests/test_cartographer.py` asserts the absence rather than trusting it.

The vocabulary is archify's, not invented here:

  type      a *component* kind - frontend, backend, database, cloud, security,
            messagebus, external. It says what a node is, never how it went.
  sublabel  free text, and the only place run state can go. Workflow nodes are
            additionalProperties:false and have no `variant` - that belongs to
            lanes and edges. The validator refused an earlier version of this
            file for exactly that, which is the schema being a better reviewer
            than a skim of it.
  animation `trace` gives the reader-controlled live trace. Static is the
            default, because a diagram has to be readable at rest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

PHASE_ORDER = (
    "ideation", "design", "build", "harden", "ship", "monetise", "market", "govern",
)

# archify caps `col` at 5. Its column centres are x = 88, 220, 300, 430, 500,
# 625, so adjacent gaps are 132/80/130/70/125 - columns 1-2 and 3-4 are too
# close to hold default-width boxes side by side. Picking well-spaced columns
# for the common cases buys room for a readable label instead of a truncated
# one.
MAX_COL = 5
SPACED_COLUMNS = {1: [0], 2: [0, 3], 3: [0, 2, 5], 4: [0, 2, 3, 5]}

# Wide enough for a short label, narrow enough for the gaps above.
NODE_WIDTH = 150

# A label longer than the box is a layout error, not a styling one. The
# renderer measures ~6.8px per character, so 150px of box holds about 22 and
# 20 leaves margin for a wide glyph.
MAX_LABEL = 20
MAX_SUBLABEL = 30

PHASE_LABEL = {
    "ideation": "1 · Ideation", "design": "2 · Design", "build": "3 · Build",
    "harden": "4 · Harden", "ship": "5 · Ship", "monetise": "6 · Monetise",
    "market": "7 · Market", "govern": "8 · Govern",
}

# A role's nature, in archify's component vocabulary. Anything unmapped is
# `external`, which reads as "something else happens here" rather than
# claiming a kind it has not earned.
ROLE_COMPONENT = {
    "designer": "frontend", "brand_designer": "frontend", "ux_writer": "frontend",
    "frontend_builder": "frontend", "accessibility_auditor": "frontend",
    "backend_builder": "backend", "builder": "backend", "lead": "backend",
    "integrations_engineer": "backend", "payments_engineer": "backend",
    "performance_engineer": "backend", "test_engineer": "backend",
    "data_engineer": "database",
    "devops_engineer": "cloud", "observability_engineer": "cloud",
    "operator": "cloud",
    "security_auditor": "security", "privacy_officer": "security",
    "reviewer": "security",
}

# Run state, as words. An unrun node must not read like a finished one.
OUTCOME_WORD = {
    "passed": "passed",
    "failed": "failed",
    "quarantined": "failed (quarantined)",
    "skipped": "skipped",
    None: "not run",
}


def _clip(text: str, limit: int) -> str:
    """Shorten to fit the box. A label wider than its node is a layout error."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _phases_present(nodes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Phases the graph actually contains, in canonical order.

    Ordered by the phase sequence rather than by node order, so the diagram
    reads left to right however the nodes arrived.
    """
    seen = {str(n.get("_phase") or "build") for n in nodes}
    ordered = [p for p in PHASE_ORDER if p in seen]
    # A phase nobody has heard of still gets a lane rather than vanishing.
    return ordered + sorted(seen - set(PHASE_ORDER))


def to_archify(
    graph: Mapping[str, Any],
    *,
    outcomes: Optional[Mapping[str, str]] = None,
    animate: bool = False,
    title: Optional[str] = None,
) -> dict[str, Any]:
    """Build an archify workflow document. Never mutates `graph`."""
    nodes = list(graph.get("nodes") or [])
    outcomes = outcomes or {}
    phases = _phases_present(nodes)
    spaced = SPACED_COLUMNS.get(len(phases))
    phase_col = {
        phase: (spaced[i] if spaced else min(i, MAX_COL))
        for i, phase in enumerate(phases)
    }

    # One lane per agent, in the order the phases run, so the diagram reads
    # top-to-bottom as the build progresses as well as left-to-right.
    roles: list[str] = []
    for phase in phases:
        for node in nodes:
            role = str(node.get("agent_role") or "builder")
            if str(node.get("_phase") or "build") == phase and role not in roles:
                roles.append(role)

    # Column is the phase. If one agent has two tasks in the same phase they
    # would land on the same spot, so the second shifts right rather than
    # overlapping - still inside the cap.
    col_of: dict[str, int] = {}
    taken: set[tuple[str, int]] = set()
    for node in nodes:
        role = str(node.get("agent_role") or "builder")
        col = phase_col.get(str(node.get("_phase") or "build"), 0)
        while (role, col) in taken and col < MAX_COL:
            col += 1
        taken.add((role, col))
        col_of[str(node.get("node_key"))] = col

    meta: dict[str, Any] = {
        "title": title or str(graph.get("goal") or "Tri-AI build"),
    }
    if animate:
        meta["animation"] = "trace"

    out_nodes = []
    for node in nodes:
        key = str(node.get("node_key"))
        phase = str(node.get("_phase") or "build")
        role = str(node.get("agent_role") or "builder")
        lane = str(node.get("lane") or "")
        verify = str(node.get("verify_command") or "")
        entry: dict[str, Any] = {
            "id": key,
            "lane": role,
            "col": col_of.get(key, 0),
            "type": ROLE_COMPONENT.get(role, "external"),
            "label": _clip(str(node.get("title") or key), MAX_LABEL),
            "width": NODE_WIDTH,
        }
        state = OUTCOME_WORD.get(outcomes.get(key), "not run")
        entry["sublabel"] = _clip(f"{phase} · {state}", MAX_SUBLABEL)
        if lane:
            entry["tag"] = lane.split("/")[-1][:24]
        out_nodes.append(entry)

    # Phase-derived parents form a complete bipartite mesh between adjacent
    # phases - two design nodes and five build nodes is ten edges that all say
    # the same thing. The columns already encode the ordering, so the diagram
    # draws one edge per phase transition and puts the fan-out in its label.
    # The full dependency set stays in the graph JSON, which is what the
    # planner reads; this is the human view.
    phase_of_node = {
        str(n.get("node_key")): str(n.get("_phase") or "build") for n in nodes
    }
    first_in_phase: dict[str, str] = {}
    for node in nodes:
        first_in_phase.setdefault(
            str(node.get("_phase") or "build"), str(node.get("node_key")))

    counted: dict[tuple[str, str], int] = {}
    for node in nodes:
        child = str(node.get("node_key"))
        for parent in node.get("parents") or []:
            pair = (phase_of_node.get(str(parent), ""), phase_of_node.get(child, ""))
            if pair[0] and pair[1] and pair[0] != pair[1]:
                counted[pair] = counted.get(pair, 0) + 1

    fanout = {p: sum(1 for n in nodes
                     if str(n.get("_phase") or "build") == p) for p in phases}
    out_edges = []
    for (src_phase, dst_phase), _ in sorted(
            counted.items(), key=lambda kv: phase_col.get(kv[0][0], 0)):
        n = fanout.get(dst_phase, 1)
        out_edges.append({
            "id": f"e_{src_phase}__{dst_phase}",
            "from": first_in_phase[src_phase],
            "to": first_in_phase[dst_phase],
            "label": f"{n} in parallel" if n > 1 else "then",
            "role": "main",
        })

    return {
        "schema_version": 2,
        "diagram_type": "workflow",
        "meta": meta,
        "lanes": [
            {"id": r, "label": r.replace("_", " ").title(), "variant": "normal"}
            for r in roles
        ],
        "phases": [
            {"id": p, "label": PHASE_LABEL.get(p, p.title()),
             "fromCol": phase_col[p], "toCol": phase_col[p]}
            for p in phases
        ],
        "nodes": out_nodes,
        "edges": out_edges,
    }


def write(
    graph: Mapping[str, Any], destination: str | Path, **kwargs: Any
) -> Path:
    """Write the archify document beside a run. Returns the path."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_archify(graph, **kwargs), indent=2),
                    encoding="utf-8")
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Project a Tri-AI graph into an archify workflow diagram.")
    ap.add_argument("graph", help="graph JSON from decomposer")
    ap.add_argument("--out", required=True, help="archify workflow JSON to write")
    ap.add_argument("--animate", action="store_true",
                    help="enable archify's reader-controlled live trace")
    ap.add_argument("--title")
    args = ap.parse_args(argv)

    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    path = write(graph, args.out, animate=args.animate, title=args.title)
    doc = json.loads(path.read_text(encoding="utf-8"))
    print(f"{len(doc['nodes'])} nodes, {len(doc['edges'])} edges, "
          f"{len(doc['lanes'])} lanes -> {path}")
    print("render with: node ~/.claude/skills/archify/bin/archify.mjs "
          f"deliver workflow {path} <out>.html --quality showcase --json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
