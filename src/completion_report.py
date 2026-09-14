"""Render a finished run as one Telegram-ready completion card.

Pure projection: this module formats board evidence and never opens a socket,
a workspace file, or the board itself. What it cannot prove, it does not claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

MAX_ARTIFACTS_SHOWN = 6
# Self-contained text types small enough to be worth uploading. A link needs
# the operator to be on the tailnet; the file itself does not.
DOCUMENT_SUFFIXES = frozenset({".html", ".htm", ".json", ".txt", ".md", ".csv"})
DOCUMENT_MAX_BYTES = 2 * 1024 * 1024
# Upper bound on files uploaded for one run. The card already truncates what it
# *names* at MAX_ARTIFACTS_SHOWN, but uploads were uncapped: a run that wrote
# 250 small pages queued 250 sendDocument calls behind one tidy-looking card.
# Artifact capture enumerates untracked files individually, so a stray build
# directory is enough to reach that. The card's link to the dashboard remains
# the complete list; this bounds only what is pushed at the operator.
MAX_DOCUMENTS_SENT = 5
OUTCOME_MARK = {"completed": "DONE", "cancelled": "CANCELLED", "failed": "FAILED"}


@dataclass(frozen=True)
class CompletionCard:
    """One delivered-once completion message and the run it reports."""
    task_id: str
    run_id: int
    text: str
    documents: tuple[Mapping[str, Any], ...] = ()


def _human_size(size: object) -> str:
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _human_duration(started: object, ended: object) -> Optional[str]:
    if not isinstance(started, int) or isinstance(started, bool):
        return None
    if not isinstance(ended, int) or isinstance(ended, bool):
        return None
    span = max(0, ended - started)
    if span < 60:
        return f"{span}s"
    if span < 3600:
        return f"{span // 60}m {span % 60}s"
    return f"{span // 3600}h {(span % 3600) // 60}m"


def _metadata(raw: object) -> Mapping[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _prompt(row: Mapping[str, Any]) -> str:
    """Prefer the operator's own words; fall back to the intake title."""
    for key in ("body", "title"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return "(no prompt recorded)"


def artifact_url(base_url: str, task_id: str, index: int) -> str:
    return f"{base_url.rstrip('/')}/artifact/{task_id}/{index}"


def deliverable_documents(
    artifacts: Sequence[Mapping[str, Any]],
    workspace: object,
) -> tuple[Mapping[str, Any], ...]:
    """Pick the artifacts worth uploading, resolved inside their own workspace.

    Paths come from the board and resolve against the task's workspace; one that
    escapes is dropped rather than sent. Type and size are read from the record,
    so nothing is opened in order to decide whether to open it.
    """
    if not isinstance(workspace, str) or not workspace.strip():
        return ()
    try:
        root = Path(workspace).resolve()
    except OSError:
        return ()
    picked: list[Mapping[str, Any]] = []
    for artifact in artifacts:
        relative = artifact.get("path")
        if not isinstance(relative, str) or not relative.strip():
            continue
        if Path(relative).suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        size = artifact.get("size_bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            continue
        if size > DOCUMENT_MAX_BYTES:
            continue
        try:
            resolved = (root / relative).resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        picked.append({"path": relative, "absolute": str(resolved), "caption": relative})
        if len(picked) >= MAX_DOCUMENTS_SENT:
            break
    return tuple(picked)


def render(
    row: Mapping[str, Any],
    *,
    dashboard_url: Optional[str] = None,
) -> CompletionCard:
    """Format one finished run. Every line is board evidence, not inference."""
    task_id = str(row.get("task_id", ""))
    run_id = row.get("run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int):
        raise ValueError("completion row must carry an integer run_id")

    outcome = row.get("outcome")
    outcome_text = str(outcome) if isinstance(outcome, str) and outcome.strip() else "unknown"
    mark = OUTCOME_MARK.get(outcome_text, outcome_text.upper())

    lines = [f"{mark} — {_prompt(row)}", ""]
    lines.append(f"task {task_id} · run {run_id}")

    meta = _metadata(row.get("metadata"))
    verify_exit = meta.get("verify_exit")
    summary = row.get("summary")
    if isinstance(verify_exit, int) and not isinstance(verify_exit, bool):
        lines.append(f"verify: exit {verify_exit}")
    elif isinstance(summary, str) and summary.strip():
        lines.append(f"verify: {summary.strip()}")
    else:
        lines.append("verify: no exit code recorded")

    duration = _human_duration(row.get("started_at"), row.get("ended_at"))
    if duration:
        lines.append(f"took {duration}")

    model = meta.get("model")
    if isinstance(model, str) and model.strip():
        provider = meta.get("provider")
        suffix = f" @ {provider}" if isinstance(provider, str) and provider.strip() else ""
        lines.append(f"model {model}{suffix}")

    error = row.get("error")
    if isinstance(error, str) and error.strip():
        lines.extend(["", f"error: {' '.join(error.split())[:400]}"])

    artifacts: Sequence[Mapping[str, Any]] = row.get("artifacts") or ()
    if artifacts:
        noun = "file" if len(artifacts) == 1 else "files"
        lines.extend(["", f"produced {len(artifacts)} {noun}:"])
        for index, artifact in enumerate(artifacts[:MAX_ARTIFACTS_SHOWN]):
            path = str(artifact.get("path", "")).strip()
            size = _human_size(artifact.get("size_bytes"))
            detail = f" ({size})" if size else ""
            lines.append(f"  • {path}{detail}")
            if dashboard_url:
                lines.append(f"    {artifact_url(dashboard_url, task_id, index)}")
        if len(artifacts) > MAX_ARTIFACTS_SHOWN:
            lines.append(f"  … and {len(artifacts) - MAX_ARTIFACTS_SHOWN} more")
    else:
        lines.extend(["", "produced no files in the workspace"])

    return CompletionCard(
        task_id=task_id,
        run_id=run_id,
        text="\n".join(lines),
        documents=deliverable_documents(artifacts, row.get("workspace_path")),
    )
