"""Keep what a rejected run made, where a person can still reach it.

Run 82 of `t_7220dc1d` researched its subject, wrote a brief, and built a
12.7 KB page that parsed and kept all five of its own promises. The taste gate
rejected it for one thing - no image, svg or background-image anywhere - and
the worker reverted the workspace. `_record_artifacts` is only reached on the
pass branch, so the board recorded nothing and the completion card told the
operator **"produced no files in the workspace"**.

That sentence was false. The page existed, parsed, and was recoverable the
whole time from `stash@{0}: triai-revert:t_7220dc1d:82` - on a machine the
operator cannot reach from a phone. Two runs like that read as two total
failures, which is how a system that nearly worked comes to look like one that
does nothing.

The rule is not being relaxed. A wall of text is not a designed page and the
gate is right to fail it. What changes is that failing stops meaning
vanishing: the output is copied here before the revert, so the card can name
it, the operator can look at the thing they asked for, and a follow-up can
start from it instead of from zero.

Deliberately **not** a board artifact. `record_run_artifacts` means "delivered,
and resolvable inside the workspace", and `completion_report._unresolvable`
already exists to flag recorded artifacts that have gone missing - which is
exactly what a reverted file would become. Rejected output is a different
thing and gets its own home and its own word.

Copies, never moves. The revert is what clears the workspace and it stashes;
two mechanisms removing the same file would race, and the stash is the
recovery path of record.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Inside the run directory, beside agent.log and verify.log, because that is
# where someone already looks when a run went wrong.
FOLDER = "rejected"
MANIFEST = "rejected.json"


@dataclass(frozen=True)
class Kept:
    """What a rejected run left behind."""

    files: tuple[str, ...] = ()
    paths: tuple[Path, ...] = ()
    reason: str = ""
    stash: str = ""

    def __bool__(self) -> bool:
        return bool(self.files)


def _safe_relative(repo: Path, raw: object) -> Optional[Path]:
    """The workspace-relative path, or None if it does not stay inside.

    Porcelain output is trusted only as far as the workspace boundary. A `..`
    would copy from outside it, and this runs unattended.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_absolute():
        return None
    try:
        resolved = (repo / candidate).resolve()
        resolved.relative_to(repo.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def keep(
    repo: Path | str,
    artifacts: Sequence[Mapping[str, Any]],
    run_dir: Path | str,
    *,
    reason: str = "",
    stash: str = "",
) -> tuple[str, ...]:
    """Copy a rejected run's output into its run directory.

    Returns the workspace-relative paths actually kept. Never raises: this is
    bookkeeping on a path that has already failed, and a copy error must not
    turn a rejected run into a crashed one.
    """
    repo = Path(repo)
    destination = Path(run_dir) / FOLDER
    kept: list[str] = []

    for artifact in artifacts or ():
        relative = _safe_relative(repo, artifact.get("path"))
        if relative is None:
            continue
        source = repo / relative
        if not source.is_file():
            continue
        target = destination / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError:
            continue
        kept.append(relative.as_posix())

    if not kept:
        # An empty folder claiming preserved output is worse than none at all.
        return ()

    try:
        (Path(run_dir) / MANIFEST).write_text(
            json.dumps({"files": kept, "reason": reason, "stash": stash},
                       indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return tuple(kept)


def read(run_dir: Path | str) -> Kept:
    """What this run preserved, or an empty `Kept` if it preserved nothing."""
    root = Path(run_dir)
    try:
        raw = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return Kept()
    if not isinstance(raw, dict):
        return Kept()
    files = tuple(str(f) for f in (raw.get("files") or []) if str(f).strip())
    return Kept(
        files=files,
        paths=tuple(root / FOLDER / f for f in files),
        reason=str(raw.get("reason") or ""),
        stash=str(raw.get("stash") or ""),
    )
