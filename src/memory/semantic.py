"""Deterministic, citation-backed semantic graph derived from memory facts."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from memory import episodic, procedural


@dataclass(frozen=True)
class Node:
    node_id: str
    kind: str


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relation: str
    citations: tuple[episodic.Citation, ...]


@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]

    def evidence(self) -> dict:
        return {
            "nodes": [asdict(node) for node in self.nodes],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "relation": edge.relation,
                    "citations": [
                        {
                            "source_path": str(citation.source_path),
                            "source_line": citation.source_line,
                            "line_digest": citation.line_digest,
                        }
                        for citation in edge.citations
                    ],
                }
                for edge in self.edges
            ],
        }


def _source_id(citation: episodic.Citation) -> str:
    value = f"{citation.source_path}|{citation.source_line}|{citation.line_digest}"
    return "source:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_id(event: episodic.EpisodicEvent) -> str:
    return "run:" + _source_id(event.citation).removeprefix("source:")


def derive(
    events: Iterable[episodic.EpisodicEvent],
    rules: Iterable[procedural.Rule] = (),
) -> Graph:
    """Derive only facts whose cited sources still validate."""
    nodes: dict[str, Node] = {}
    edges: set[Edge] = set()
    by_task: dict[str, list[episodic.EpisodicEvent]] = {}

    def add_node(node_id: str, kind: str) -> None:
        nodes[node_id] = Node(node_id, kind)

    for event in sorted(events, key=lambda item: (str(item.citation.source_path), item.citation.source_line)):
        if not episodic.validate(event.citation):
            continue
        task_id = event.payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        task = f"task:{task_id}"
        run = _event_id(event)
        source = _source_id(event.citation)
        add_node(task, "task")
        add_node(run, "run")
        add_node(source, "source")
        edges.add(Edge(task, run, "recorded", (event.citation,)))
        outcome = event.payload.get("outcome")
        if isinstance(outcome, str) and outcome:
            outcome_id = f"outcome:{outcome}"
            add_node(outcome_id, "outcome")
            edges.add(Edge(run, outcome_id, "produced", (event.citation,)))
        parents = event.payload.get("parents", ())
        if isinstance(parents, list) and all(isinstance(parent, str) and parent for parent in parents):
            for parent in parents:
                parent_id = f"task:{parent}"
                add_node(parent_id, "task")
                edges.add(Edge(task, parent_id, "depends_on", (event.citation,)))
        by_task.setdefault(task_id, []).append(event)

    for task_id, task_events in by_task.items():
        ordered = sorted(task_events, key=lambda item: item.citation.source_line)
        for previous, current in zip(ordered, ordered[1:]):
            edges.add(Edge(_event_id(current), _event_id(previous), "retries_from", (current.citation, previous.citation)))

    for rule in sorted(rules, key=lambda item: item.rule_id):
        if not all(episodic.validate(citation) for citation in rule.citations):
            continue
        rule_id = f"rule:{rule.rule_id}"
        add_node(rule_id, "rule")
        for citation in rule.citations:
            source = _source_id(citation)
            add_node(source, "source")
            edges.add(Edge(rule_id, source, "cites", (citation,)))

    return Graph(
        tuple(sorted(nodes.values(), key=lambda item: item.node_id)),
        tuple(sorted(edges, key=lambda item: (item.source, item.target, item.relation))),
    )
