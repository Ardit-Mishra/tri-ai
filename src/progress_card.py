"""Render the one message that tracks a task while it runs.

A person watching a phone wants to know that something is still happening and
roughly where it is. Polling `/status` to find that out is friction, so a single
message is edited in place instead - one message per task per chat, never a
stream of new ones.

Pure projection: this module formats board evidence and opens nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Phase keys in the order a run passes through them, with the wording a person
# reads rather than the column name.
PHASE_LABEL = {
    "claimed": "Claimed",
    "worktree_prep": "Preparing workspace",
    "agent_active": "Building",
    "verify_gate": "Testing",
    "done": "Done",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
PHASE_MARK = {
    "claimed": "⏳",
    "worktree_prep": "\U0001f4c1",
    "agent_active": "⚡",
    "verify_gate": "\U0001f9ea",
    "done": "✅",
    "failed": "❌",
    "cancelled": "⛔",
}
TERMINAL_PHASES = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True)
class ProgressCard:
    """One in-place status message: where the task is, and what to call it."""
    task_id: str
    phase: str
    text: str
    closed: bool


def _elapsed(started: object, now: int) -> Optional[str]:
    if isinstance(started, bool) or not isinstance(started, int):
        return None
    span = max(0, now - started)
    if span < 60:
        return f"{span}s"
    if span < 3600:
        return f"{span // 60}m {span % 60}s"
    return f"{span // 3600}h {(span % 3600) // 60}m"


def phase_for(row: Mapping[str, Any]) -> str:
    """Decide the phase from task and run state, never from a guess.

    The card reports what the board records. A task whose run has not started
    reads `claimed`, because that is all the board can prove.
    """
    task_status = str(row.get("task_status") or "").strip()
    if task_status in TERMINAL_PHASES:
        return task_status
    run_status = str(row.get("run_status") or "").strip()
    if task_status == "running" and run_status == "running":
        return "agent_active"
    if task_status == "running":
        return "claimed"
    return task_status or "claimed"


def render(row: Mapping[str, Any], *, phase: str, now: int) -> ProgressCard:
    """Format one card for a phase the caller already decided."""
    task_id = str(row.get("task_id", ""))
    prompt = row.get("body") or row.get("title") or "(no prompt recorded)"
    prompt = " ".join(str(prompt).split())
    if len(prompt) > 90:
        prompt = prompt[:89].rstrip() + "…"

    mark = PHASE_MARK.get(phase, "•")
    label = PHASE_LABEL.get(phase, phase)
    workspace = row.get("workspace_path")
    alias = str(workspace).replace("\\", "/").rstrip("/").split("/")[-1] if workspace else None

    head = f"{mark} {label} — {prompt}"
    details = [f"task {task_id}"]
    if alias:
        details.append(f"workspace {alias}")
    elapsed = _elapsed(row.get("started_at"), now)
    if elapsed and phase not in TERMINAL_PHASES:
        details.append(f"running {elapsed}")
    elif elapsed:
        details.append(f"took {elapsed}")

    return ProgressCard(
        task_id=task_id,
        phase=phase,
        text=head + "\n" + " · ".join(details),
        closed=phase in TERMINAL_PHASES,
    )
