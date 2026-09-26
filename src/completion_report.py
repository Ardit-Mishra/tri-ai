"""Render a finished run as one Telegram-ready completion card.

Pure projection: this module formats board evidence and never opens a socket,
a workspace file, or the board itself. What it cannot prove, it does not claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import preserve
from typing import Any, Mapping, Optional, Sequence

MAX_ARTIFACTS_SHOWN = 6
# Self-contained text types small enough to be worth uploading. A link needs
# the operator to be on the tailnet; the file itself does not.
# A page is rarely one file. t_17c5106b produced index.html, app.js and
# style.css, and app.js was the file that explained the rejection - 5.9 KB
# of client-side rendering, which is why the page matched none of its
# thirteen promises. Sending the verdict without the cause is half a card.
DOCUMENT_SUFFIXES = frozenset({
    ".html", ".htm", ".json", ".txt", ".md", ".csv", ".js", ".css", ".svg",
})
DOCUMENT_MAX_BYTES = 2 * 1024 * 1024
# Upper bound on files uploaded for one run. Uploads used to be uncapped: a run
# that wrote 250 small pages queued 250 sendDocument calls behind one tidy card,
# and artifact capture enumerating untracked files individually means a stray
# build directory reaches that easily.
#
# It matches MAX_ARTIFACTS_SHOWN deliberately. At 5-against-6 the card linked a
# sixth file that was never going to arrive, which is a small lie of exactly the
# kind this project exists to refuse. Every file the card names as a link is a
# file it also tries to send, and when there are more the card says so in words.
MAX_DOCUMENTS_SENT = MAX_ARTIFACTS_SHOWN
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
        # The record says the file was there when the agent exited; a verify
        # command may have moved or removed it since. Queueing an upload for a
        # path that no longer resolves turns one stale row into a failed send.
        if not resolved.is_file():
            continue
        picked.append({"path": relative, "absolute": str(resolved), "caption": relative})
        if len(picked) >= MAX_DOCUMENTS_SENT:
            break
    return tuple(picked)


def rejection_reason(output: str) -> str:
    """The finding that explains a rejection, not the last line of the log.

    Measured on t_17c5106b run 84: the output led with "the page keeps 0 of
    its own 13 promises, needs 5" and then enumerated all thirteen. Taking the
    last line told the operator the run was rejected for a single missing
    string, when in fact nothing had matched at all - and sent them to fix the
    wrong thing.

    The enumerated items are evidence for the finding, not the finding. The
    first indented line after the verdict is the finding.
    """
    lines = [line.rstrip() for line in str(output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    detail = [
        line.strip() for line in lines
        if line.startswith((" ", "\t")) and "promised but not visible" not in line
    ]
    if detail:
        return detail[0]
    # No indented finding: the gate failed for a reason it did not narrate.
    return lines[-1].strip()


def _preserved_documents(kept: "preserve.Kept") -> tuple[Mapping[str, Any], ...]:
    """Attach what a rejected run made, on the same terms as a delivered one.

    The operator asked for a thing. Being able to open it and judge for
    themselves beats being handed the name of the rule it broke.
    """
    picked: list[Mapping[str, Any]] = []
    for name, path in zip(kept.files, kept.paths):
        if Path(name).suffix.lower() not in DOCUMENT_SUFFIXES:
            continue
        try:
            if not path.is_file() or path.stat().st_size > DOCUMENT_MAX_BYTES:
                continue
        except OSError:
            continue
        picked.append({"path": name, "absolute": str(path),
                       "caption": f"{name} (rejected run)"})
        if len(picked) >= MAX_DOCUMENTS_SENT:
            break
    return tuple(picked)


def _unresolvable(
    artifacts: Sequence[Mapping[str, Any]],
    workspace: object,
) -> int:
    """How many recorded artifacts no longer exist where the board says.

    Counted so the card can admit it. Nothing is opened; the path is resolved
    inside the workspace exactly as delivery resolves it, and one that escapes
    is treated as unresolvable rather than followed.
    """
    if not isinstance(workspace, str) or not workspace.strip():
        return 0
    try:
        root = Path(workspace).resolve()
    except OSError:
        return 0
    gone = 0
    for artifact in artifacts:
        relative = artifact.get("path")
        if not isinstance(relative, str) or not relative.strip():
            continue
        try:
            resolved = (root / relative).resolve()
        except OSError:
            gone += 1
            continue
        if not resolved.is_relative_to(root) or not resolved.is_file():
            gone += 1
    return gone


def render(
    row: Mapping[str, Any],
    *,
    dashboard_url: Optional[str] = None,
    runs_root: Optional[Path | str] = None,
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
            # Say what is NOT coming. The operator otherwise counts the files
            # named above, counts the attachments, and has to guess why they
            # differ.
            held = len(artifacts) - MAX_ARTIFACTS_SHOWN
            lines.append(
                f"  … and {held} more, not attached — open the dashboard for all "
                f"{len(artifacts)}"
            )
        # A recorded artifact whose file is gone is dropped from delivery rather
        # than queued as a failing upload. Dropping it silently is the wrong
        # half of that fix: the operator sees a card naming files, receives
        # fewer, and has nothing to go on. Name the gap.
        missing = _unresolvable(artifacts, row.get("workspace_path"))
        if missing:
            noun = "file" if missing == 1 else "files"
            lines.append(
                f"  ⚠ {missing} {noun} could not be attached — recorded, but no "
                f"longer at the recorded path"
            )
    else:
        # A rejected run records no board artifacts - those mean
        # "delivered", and its output has been reverted. But it may still
        # have built the thing and missed one rule, so look where the
        # worker preserved it before declaring it made nothing. Saying
        # "produced no files" about a 12.7 KB page is how a near-miss came
        # to read as a total failure.
        kept = (preserve.read(Path(runs_root) / task_id / str(run_id))
                if runs_root else preserve.Kept())
        if kept:
            lines.append("")
            lines.append(
                f"kept {len(kept.files)} file(s) from the rejected run:")
            lines.extend(
                f"  \u2022 {name}" for name in kept.files[:MAX_ARTIFACTS_SHOWN])
            finding = rejection_reason(kept.reason)
            if finding:
                lines.append(f"rejected for: {finding}")
            if kept.stash:
                lines.append(
                    f"recover in the workspace: git stash pop {kept.stash}")
            documents = _preserved_documents(kept)
        else:
            lines.extend(["", "produced no files in the workspace"])
            # True and useless on its own: the same sentence covers an agent
            # that worked for twenty minutes and got it wrong, and one that
            # answered in a single sentence and never touched the disk. The
            # second was 19 of the first 52 failures - `devstral:24b` in 10 of
            # its 11 runs - and nothing on the card told them apart, which is
            # why it went unnoticed for 82 runs.
            if meta.get("answered_without_tools") is True:
                lines.append(
                    "the model answered once and never used a tool - "
                    "it wrote the work into its reply instead of onto disk")
            documents = ()
        return CompletionCard(
            task_id=task_id, run_id=run_id, text="\n".join(lines),
            documents=documents,
        )

    return CompletionCard(
        task_id=task_id,
        run_id=run_id,
        text="\n".join(lines),
        documents=deliverable_documents(artifacts, row.get("workspace_path")),
    )
