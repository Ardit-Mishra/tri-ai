"""Render a finished run as one Telegram-ready completion card.

Pure projection: this module formats board evidence and never opens a socket,
a workspace file, or the board itself. What it cannot prove, it does not claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

MAX_ARTIFACTS_SHOWN = 6
OUTCOME_MARK = {"completed": "DONE", "cancelled": "CANCELLED", "failed": "FAILED"}


@dataclass(frozen=True)
class CompletionCard:
    """One delivered-once completion message and the run it reports."""
    task_id: str
    run_id: int
    text: str


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

    return CompletionCard(task_id=task_id, run_id=run_id, text="\n".join(lines))
