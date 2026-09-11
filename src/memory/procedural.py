"""Read-only, citation-backed procedural preflight advice."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from memory import episodic


ALLOWED_CHECKS = frozenset({
    "confirm_workspace_clean",
    "inspect_last_verify_log",
    "inspect_verify_command",
    "inspect_expected_artifacts",
})
ALLOWED_KEYS = frozenset({
    "id", "workspace", "task_kind", "checks", "citations", "expires_at", "activation",
})
ACTIVATION = "preflight_advice"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    workspace: Path
    task_kind: str
    checks: tuple[str, ...]
    citations: tuple[episodic.Citation, ...]
    expires_at: float


@dataclass(frozen=True)
class Advice:
    rule_ids: tuple[str, ...]
    checks: tuple[str, ...]
    excluded_rule_ids: tuple[str, ...]


def _citation(raw: Mapping[str, Any]) -> episodic.Citation:
    path, line, digest = raw.get("source_path"), raw.get("source_line"), raw.get("line_digest")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("rule citation requires source_path")
    raw_path = Path(path)
    if not raw_path.is_absolute():
        raise ValueError("rule citation source_path must be absolute")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError("rule citation requires positive source_line")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("rule citation requires a SHA-256 line_digest")
    return episodic.Citation(raw_path.resolve(), line, digest)


def rule_from_mapping(raw: Mapping[str, Any], *, now: float | None = None) -> Rule:
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise ValueError(f"rule has unsupported fields: {sorted(unknown)}")
    rule_id, workspace, task_kind = raw.get("id"), raw.get("workspace"), raw.get("task_kind")
    checks, citations = raw.get("checks"), raw.get("citations")
    expires_at, activation = raw.get("expires_at"), raw.get("activation")
    if not all(isinstance(value, str) and value.strip() for value in (rule_id, workspace, task_kind)):
        raise ValueError("rule requires nonblank id, workspace, and task_kind")
    raw_workspace = Path(workspace)
    if not raw_workspace.is_absolute():
        raise ValueError("rule workspace must be absolute")
    canonical_workspace = raw_workspace.resolve()
    if not isinstance(checks, list) or not checks or any(item not in ALLOWED_CHECKS for item in checks):
        raise ValueError("rule checks must use the allowed preflight checklist")
    if len(set(checks)) != len(checks):
        raise ValueError("rule checks must be unique")
    if not isinstance(citations, list) or not citations or any(not isinstance(item, Mapping) for item in citations):
        raise ValueError("rule requires at least one structured citation")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or expires_at <= 0:
        raise ValueError("rule requires a positive expires_at")
    if activation != ACTIVATION:
        raise ValueError("rule activation must be preflight_advice")
    return Rule(
        rule_id=rule_id.strip(),
        workspace=canonical_workspace,
        task_kind=task_kind.strip(),
        checks=tuple(checks),
        citations=tuple(_citation(item) for item in citations),
        expires_at=float(expires_at),
    )


def load_rules(path: Path | str, *, now: float | None = None) -> tuple[Rule, ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"procedural rules are not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping) or set(raw) != {"rules"} or not isinstance(raw["rules"], list):
        raise ValueError("procedural rules must contain exactly a rules list")
    rules = tuple(rule_from_mapping(item, now=now) for item in raw["rules"] if isinstance(item, Mapping))
    if len(rules) != len(raw["rules"]) or len({rule.rule_id for rule in rules}) != len(rules):
        raise ValueError("procedural rules must be objects with unique ids")
    return rules


def select(
    rules: Iterable[Rule],
    *,
    workspace: Path | str,
    task_kind: str,
    max_characters: int,
    now: float | None = None,
) -> Advice:
    """Select bounded, currently-cited advice without performing any action."""
    if max_characters < 1:
        raise ValueError("max_characters must be positive")
    current_time = time.time() if now is None else now
    target_workspace = Path(workspace).resolve()
    selected: list[Rule] = []
    excluded: list[str] = []
    for rule in sorted(rules, key=lambda item: item.rule_id):
        if (
            rule.workspace != target_workspace
            or rule.task_kind != task_kind
            or rule.expires_at <= current_time
            or not all(episodic.validate(citation) for citation in rule.citations)
        ):
            excluded.append(rule.rule_id)
            continue
        selected.append(rule)

    rule_ids: list[str] = []
    checks: list[str] = []
    for rule in selected:
        proposed = tuple(dict.fromkeys((*checks, *rule.checks)))
        rendered = "\n".join(proposed)
        if len(rendered) > max_characters:
            excluded.append(rule.rule_id)
            continue
        rule_ids.append(rule.rule_id)
        checks = list(proposed)
    return Advice(tuple(rule_ids), tuple(checks), tuple(sorted(set(excluded))))
