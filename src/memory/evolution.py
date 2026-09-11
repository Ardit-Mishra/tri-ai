"""Candidate-only evolution from repeated, provenance-checked failures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from memory import episodic


DEFAULT_CHECKS = ("inspect_last_verify_log", "inspect_verify_command")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    task_kind: str
    failure_class: str
    verify_exit: int
    checks: tuple[str, ...]
    citations: tuple[episodic.Citation, ...]
    status: str = "draft"
    activation: str = "operator_review_required"


@dataclass(frozen=True)
class Replay:
    candidate_id: str
    supported: bool
    citations: tuple[episodic.Citation, ...]


def _signature(event: episodic.EpisodicEvent) -> tuple[str, str, int] | None:
    failure_class = event.payload.get("failure_class")
    verify_exit = event.payload.get("verify_exit")
    if failure_class != "logic" or isinstance(verify_exit, bool) or not isinstance(verify_exit, int) or verify_exit == 0:
        return None
    task_kind = event.payload.get("task_kind", "unknown")
    return (task_kind if isinstance(task_kind, str) and task_kind else "unknown", failure_class, verify_exit)


def propose(events: Iterable[episodic.EpisodicEvent], *, minimum_occurrences: int = 2) -> tuple[Candidate, ...]:
    """Derive drafts only; stale evidence and environment outcomes never qualify."""
    if minimum_occurrences < 2:
        raise ValueError("minimum_occurrences must be at least two")
    groups: dict[tuple[str, str, int], list[episodic.EpisodicEvent]] = {}
    for event in events:
        signature = _signature(event)
        if signature is not None and episodic.validate(event.citation):
            groups.setdefault(signature, []).append(event)
    candidates: list[Candidate] = []
    for signature, grouped in sorted(groups.items()):
        if len(grouped) < minimum_occurrences:
            continue
        citations = tuple(sorted((item.citation for item in grouped), key=lambda item: (str(item.source_path), item.source_line)))
        identity = "|".join((
            signature[0],
            signature[1],
            str(signature[2]),
            *(f"{citation.source_path}:{citation.source_line}:{citation.line_digest}" for citation in citations),
        ))
        candidates.append(Candidate(
            "candidate:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
            signature[0], signature[1], signature[2],
            DEFAULT_CHECKS,
            citations,
        ))
    return tuple(candidates)


def valid(candidate: Candidate) -> bool:
    return bool(candidate.citations) and candidate.status == "draft" and candidate.activation == "operator_review_required" and all(
        episodic.validate(citation) for citation in candidate.citations
    )


def replay(candidate: Candidate, held_out: Iterable[episodic.EpisodicEvent]) -> Replay:
    """Report supporting held-out evidence without activating or writing anything."""
    source_citations = set(candidate.citations)
    matches = tuple(
        event.citation for event in held_out
        if event.citation not in source_citations
        and episodic.validate(event.citation)
        and _signature(event) == (candidate.task_kind, candidate.failure_class, candidate.verify_exit)
    )
    return Replay(candidate.candidate_id, bool(matches) and valid(candidate), matches)
