"""Structured proposal creation and Telegram rendering.

This module has no execution capability. Board owns persistence and decisions;
candidate evolution remains read-only until an operator approves a fixed-shape
procedural rule through that board API.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

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


# Long enough that an operator who is away for a weekend can still act on
# it, short enough that a draft derived from evidence that has since moved on
# expires rather than lingering. `evolution.valid` re-checks the citations at
# approval time regardless.
CANDIDATE_TTL_SECONDS = 7 * 24 * 3600


def refresh_from_ledger(
    conn,
    *,
    ledger_path,
    memory_path,
    workspace,
    minimum_occurrences: int = 2,
    now: Optional[float] = None,
) -> tuple[board.ProposalResult, ...]:
    """Index the ledger, and draft a proposal for each repeated failure.

    Every step of this already existed and none of it was ever called on a
    real run: `episodic.sync` only from tests, `generate_candidate_proposals`
    from nowhere. So the system could not notice that it kept failing the
    same way - and it did keep failing the same way, 8 times on "the page
    keeps N of its own N promises" alone.

    **Never raises.** This runs on the worker daemon's idle tick, and a
    learning pass that can stop the worker would trade the thing that works
    for the thing that might. A corrupt ledger line, an unwritable memory
    database or a board refusal all return "nothing proposed".

    Idempotent by proposal id: `evolution` derives a deterministic
    `candidate_id` from the signature and its citations, so an unchanged
    ledger re-proposes the same id and the board keeps one row.

    Nothing here activates anything. Candidates are drafts with
    `operator_review_required`, which is the existing contract and not this
    function's to relax.
    """
    from memory import episodic

    try:
        source = Path(ledger_path)
        if not source.exists():
            return ()
        memory = episodic.connect(Path(memory_path))
    except Exception as exc:
        print(f"learning pass could not open its inputs: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return ()
    try:
        episodic.sync(memory, source)
        events = episodic.query(memory, source_path=source)
        if not events:
            return ()
        expires_at = (time.time() if now is None else float(now)) \
            + CANDIDATE_TTL_SECONDS

        # A rule carries a workspace and is rendered as "advice for <kind> in
        # <workspace.name>", so it has to be a workspace some task actually
        # runs in. The first wiring passed the daemon's `--runs-dir`, the only
        # path it had, which would have scoped every rule to `~/.tri-ai/runs`
        # and matched nothing.
        #
        # The ledger records the workspace per line as `repo`. Partitioning on
        # it also scopes the *evidence* correctly: failures in one project are
        # not evidence about another, and the rule schema has no way to
        # express a cross-workspace pattern anyway.
        grouped: dict[str, list] = {}
        for event in events:
            repo = event.payload.get("repo")
            key = repo if isinstance(repo, str) and repo.strip() else ""
            grouped.setdefault(key, []).append(event)

        created: list[board.ProposalResult] = []
        for key, group in sorted(grouped.items()):
            created.extend(generate_candidate_proposals(
                conn, group, workspace=key or workspace,
                expires_at=expires_at,
                minimum_occurrences=minimum_occurrences,
            ))
        return tuple(created)
    except Exception as exc:
        # Still never raises - the worker must not stop because reflection
        # failed - but a silent `return ()` was too quiet. A corrupt ledger
        # line is expected; a locked board, an unwritable memory database or
        # a persistence bug means proposals stop for ever with nothing to
        # notice. Say it once per pass and carry on.
        print(f"learning pass failed, no proposals this tick: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return ()
    finally:
        try:
            memory.close()
        except Exception:
            pass


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
