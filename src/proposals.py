"""Structured proposal creation and Telegram rendering.

This module has no execution capability. Board owns persistence and decisions;
candidate evolution remains read-only until an operator approves a fixed-shape
procedural rule through that board API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import board
from memory import evolution


@dataclass(frozen=True)
class OutboundProposal:
    proposal_id: str
    text: str
    reply_markup: dict[str, object]


def candidate_rule_mapping(
    candidate: evolution.Candidate,
    *,
    workspace: Path | str,
    expires_at: float,
) -> dict[str, object]:
    """Turn one valid draft into the only rule shape activation may accept."""
    if not evolution.valid(candidate):
        raise ValueError("candidate has stale or missing citations")
    root = Path(workspace)
    if not root.is_absolute():
        raise ValueError("candidate workspace must be absolute")
    if expires_at <= time.time():
        raise ValueError("candidate rule expiry must be in the future")
    return {
        "id": candidate.candidate_id,
        "workspace": str(root.resolve()),
        "task_kind": candidate.task_kind,
        "checks": list(candidate.checks),
        "citations": [
            {
                "source_path": str(citation.source_path),
                "source_line": citation.source_line,
                "line_digest": citation.line_digest,
            }
            for citation in candidate.citations
        ],
        "expires_at": expires_at,
        "activation": "preflight_advice",
    }


def create_candidate_proposals(
    conn,
    candidates: Iterable[evolution.Candidate],
    *,
    workspace: Path | str,
    expires_at: float,
) -> tuple[board.ProposalResult, ...]:
    """Persist deterministic, still-unactivated candidate-rule proposals."""
    return tuple(
        board.create_candidate_rule_proposal(
            conn,
            proposal_id=f"evolution:{candidate.candidate_id}",
            rule=candidate_rule_mapping(candidate, workspace=workspace, expires_at=expires_at),
        )
        for candidate in candidates
    )


def generate_candidate_proposals(
    conn,
    events,
    *,
    workspace: Path | str,
    expires_at: float,
    minimum_occurrences: int = 2,
) -> tuple[board.ProposalResult, ...]:
    """Derive candidate drafts, then persist them as still-pending proposals."""
    return create_candidate_proposals(
        conn,
        evolution.propose(events, minimum_occurrences=minimum_occurrences),
        workspace=workspace,
        expires_at=expires_at,
    )


def render(proposal: dict[str, object]) -> OutboundProposal:
    """Render board data into a fixed Telegram decision card, never a command."""
    proposal_id = str(proposal["id"])
    action = str(proposal["suggested_action"])
    labels = {
        "archive": "Archive completed task",
        "retry": "Retry through the existing board gate",
        "activate_procedural_advice": "Activate citation-validated procedural advice",
    }
    if action not in labels:
        raise ValueError("proposal action is not registered")
    return OutboundProposal(
        proposal_id=proposal_id,
        text=f"{proposal['summary']}\n\nNext move: {labels[action]}",
        reply_markup={
            "inline_keyboard": [[
                {"text": "Approve", "callback_data": f"prop:approve:{proposal_id}"},
                {"text": "Reject", "callback_data": f"prop:reject:{proposal_id}"},
            ]],
        },
    )
