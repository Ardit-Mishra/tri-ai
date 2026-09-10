"""One-shot, validate-first writer for verify-gated task graphs.

The planner consumes a graph document produced by a strong model or an
operator. It treats that document as data: validate every node before opening
the board transaction, persist every node through ``board.create_task``, then
exit. It never polls, dispatches, or invokes an agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import board
import executor


class GraphValidationError(ValueError):
    """The submitted graph cannot safely be persisted."""


@dataclass(frozen=True)
class PlannedNode:
    key: str
    title: str
    prompt: str
    workspace_kind: str
    workspace_path: Path
    branch_name: Optional[str]
    verify_command: str
    verify_timeout: int
    expected_artifacts: tuple[str, ...]
    parents: tuple[str, ...]


def _required_text(value: Any, field: str, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphValidationError(f"node {key!r}: {field} must be non-blank text")
    return value.strip()


def _validate_workspace(value: Any, key: str) -> tuple[str, Path, Optional[str]]:
    if not isinstance(value, Mapping):
        raise GraphValidationError(f"node {key!r}: workspace must be an object")
    kind = value.get("kind")
    if kind not in {"dir", "worktree"}:
        raise GraphValidationError(
            f"node {key!r}: workspace.kind must be 'dir' or 'worktree'"
        )
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise GraphValidationError(f"node {key!r}: workspace.path is required")
    path = Path(raw_path)
    if not path.is_absolute():
        raise GraphValidationError(f"node {key!r}: workspace.path must be absolute")
    if not path.is_dir():
        raise GraphValidationError(f"node {key!r}: workspace.path must be an existing directory")
    raw_branch = value.get("branch")
    branch = raw_branch.strip() if isinstance(raw_branch, str) and raw_branch.strip() else None
    if kind == "worktree" and branch is None:
        raise GraphValidationError(f"node {key!r}: worktree workspace requires branch")
    if kind == "worktree" and not (path / ".git").exists():
        raise GraphValidationError(
            f"node {key!r}: worktree workspace.path must be a Git repository anchor"
        )
    if kind != "worktree" and branch is not None:
        raise GraphValidationError(f"node {key!r}: branch is only valid for worktree")
    return kind, path, branch


def _topological_order(nodes: Mapping[str, PlannedNode]) -> list[str]:
    permanent: set[str] = set()
    active: set[str] = set()
    order: list[str] = []

    def visit(key: str) -> None:
        if key in permanent:
            return
        if key in active:
            raise GraphValidationError(f"graph contains a dependency cycle at {key!r}")
        active.add(key)
        for parent in nodes[key].parents:
            visit(parent)
        active.remove(key)
        permanent.add(key)
        order.append(key)

    for key in nodes:
        visit(key)
    return order


def validate_graph(document: Mapping[str, Any]) -> list[PlannedNode]:
    """Validate a whole graph before any task or link is written."""
    if not isinstance(document, Mapping):
        raise GraphValidationError("graph document must be an object")
    raw_nodes = document.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise GraphValidationError("graph document requires a non-empty nodes list")

    nodes: dict[str, PlannedNode] = {}
    worktree_branches: set[tuple[str, str]] = set()
    for raw in raw_nodes:
        if not isinstance(raw, Mapping):
            raise GraphValidationError("every graph node must be an object")
        raw_key = raw.get("node_key")
        key = _required_text(raw_key, "node_key", "<unknown>")
        if key in nodes:
            raise GraphValidationError(f"duplicate node_key: {key!r}")
        title = _required_text(raw.get("title"), "title", key)
        prompt = _required_text(raw.get("prompt"), "prompt", key)
        verify_command = _required_text(raw.get("verify_command"), "verify_command", key)
        kind, workspace_path, branch = _validate_workspace(raw.get("workspace"), key)

        timeout = raw.get("verify_timeout")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise GraphValidationError(
                f"node {key!r}: verify_timeout must be a positive integer"
            )

        artifacts = raw.get("expected_artifacts", [])
        if not isinstance(artifacts, list) or not all(
            isinstance(item, str) and item.strip() for item in artifacts
        ):
            raise GraphValidationError(
                f"node {key!r}: expected_artifacts must be a list of strings"
            )
        for artifact in artifacts:
            try:
                executor.resolve_artifact(workspace_path, artifact.strip())
            except executor.ArtifactEscape as exc:
                raise GraphValidationError(f"node {key!r}: {exc}") from exc

        parents = raw.get("parents", [])
        if not isinstance(parents, list) or not all(isinstance(item, str) and item.strip() for item in parents):
            raise GraphValidationError(f"node {key!r}: parents must be a list of node keys")
        parent_keys = tuple(item.strip() for item in parents)
        if key in parent_keys:
            raise GraphValidationError(f"node {key!r}: a node cannot parent itself")

        if kind == "worktree":
            branch_key = (os.path.normcase(os.path.realpath(str(workspace_path))), branch or "")
            if branch_key in worktree_branches:
                raise GraphValidationError(
                    f"node {key!r}: worktree branch is reused within the graph"
                )
            worktree_branches.add(branch_key)

        nodes[key] = PlannedNode(
            key=key,
            title=title,
            prompt=prompt,
            workspace_kind=kind,
            workspace_path=workspace_path,
            branch_name=branch,
            verify_command=verify_command,
            verify_timeout=timeout,
            expected_artifacts=tuple(item.strip() for item in artifacts),
            parents=parent_keys,
        )

    for node in nodes.values():
        unknown = [parent for parent in node.parents if parent not in nodes]
        if unknown:
            raise GraphValidationError(
                f"node {node.key!r}: unknown parent node key(s): {', '.join(unknown)}"
            )
    return [nodes[key] for key in _topological_order(nodes)]


def write_graph(conn, document: Mapping[str, Any]) -> dict[str, str]:
    """Atomically persist a validated graph and return ``node_key -> task_id``."""
    nodes = validate_graph(document)
    kb = board.kanban()
    task_ids: dict[str, str] = {}
    with kb.write_txn(conn):
        for node in nodes:
            task_ids[node.key] = board.create_task(
                conn,
                title=node.title,
                prompt=node.prompt,
                verify_command=node.verify_command,
                verify_timeout=node.verify_timeout,
                expected_artifacts=node.expected_artifacts,
                parents=[task_ids[parent] for parent in node.parents],
                workspace_kind=node.workspace_kind,
                workspace_path=node.workspace_path,
                branch_name=node.branch_name,
            )
    return task_ids


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Persist one graph document and exit 0, or reject it with exit 1."""
    parser = argparse.ArgumentParser(prog="planner.py")
    parser.add_argument("--graph", required=True, metavar="GRAPH.JSON")
    parser.add_argument("--board", metavar="PATH")
    args = parser.parse_args(argv)
    try:
        raw = Path(args.graph).read_text(encoding="utf-8")
        document = json.loads(raw)
        if args.board:
            os.environ["TRIAI_BOARD_DB"] = str(Path(args.board).expanduser())
        conn = board.connect(Path(args.board).expanduser() if args.board else None)
        try:
            task_ids = write_graph(conn, document)
        finally:
            conn.close()
    except (GraphValidationError, OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(f"planner: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(task_ids, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
