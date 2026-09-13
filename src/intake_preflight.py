"""Check a prompt against the workspace it is aimed at, before it is confirmed.

A prompt that names a file is a prompt about that file. When the file is not in
the target workspace the run is almost always mis-aimed - the agent writes a new
one from scratch instead of editing what the operator meant, and nobody finds
out until it has finished. This reports that at staging time, while the answer
is still one tap away.

It reads directory entries and nothing else: no writes, no execution, and no
network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

# Extensions worth checking. A prompt saying "make it faster" names no file;
# one saying "in celestial.html" does.
CHECKED_SUFFIXES = frozenset({
    ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".json",
    ".md", ".txt", ".csv", ".yml", ".yaml", ".toml", ".sh", ".ps1", ".sql",
})
# Bare words that read as filenames but are almost never the operator naming a
# file in their workspace.
IGNORED_NAMES = frozenset({"e.g", "i.e", "etc", "vs"})
_CANDIDATE = re.compile(r"[A-Za-z0-9_.\-/\\]+\.[A-Za-z0-9]{1,5}")
MAX_SCANNED_FILES = 4000


@dataclass(frozen=True)
class Preflight:
    """What a prompt names, and which of those the workspace actually holds."""
    named: tuple[str, ...]
    present: tuple[str, ...]
    missing: tuple[str, ...]
    scanned: bool = False

    @property
    def warns(self) -> bool:
        """Warn only when the workspace was actually read and held none of them.

        Two conditions, both necessary. A prompt naming one present file and one
        new one is ordinary work - the agent edits something and creates
        something - so only the case where nothing it referred to exists is
        worth interrupting for. And a workspace that could not be read proves
        nothing about absence, so it stays silent rather than guessing.
        """
        return self.scanned and bool(self.named) and not self.present


def named_files(prompt: str) -> tuple[str, ...]:
    """Pull filename-shaped tokens out of a prompt, in the order they appear."""
    seen: list[str] = []
    for match in _CANDIDATE.finditer(prompt or ""):
        token = match.group(0).strip(".,;:()[]{}\"'")
        if not token:
            continue
        candidate = Path(token.replace("\\", "/"))
        if candidate.suffix.lower() not in CHECKED_SUFFIXES:
            continue
        if candidate.stem.lower() in IGNORED_NAMES:
            continue
        normalized = candidate.as_posix()
        if normalized not in seen:
            seen.append(normalized)
    return tuple(seen)


def _workspace_names(workspace: Path) -> set[str]:
    """Every file name and relative path in the workspace, bounded.

    Bounded because a workspace can be arbitrarily large and this runs inline
    while the operator waits for a reply.
    """
    names: set[str] = set()
    scanned = 0
    for path in workspace.rglob("*"):
        if scanned >= MAX_SCANNED_FILES:
            break
        parts = path.parts
        if any(part in {".git", "node_modules", "__pycache__", ".next", "dist"} for part in parts):
            continue
        if not path.is_file():
            continue
        scanned += 1
        names.add(path.name.lower())
        try:
            names.add(path.relative_to(workspace).as_posix().lower())
        except ValueError:
            continue
    return names


def check(prompt: str, workspace: Path | str) -> Preflight:
    """Report which files a prompt names, and which the workspace holds."""
    named = named_files(prompt)
    if not named:
        return Preflight((), (), ())
    try:
        root = Path(workspace).resolve()
        if not root.is_dir():
            return Preflight(named, (), (), scanned=False)
        existing = _workspace_names(root)
    except OSError:
        return Preflight(named, (), (), scanned=False)

    present: list[str] = []
    missing: list[str] = []
    for token in named:
        lowered = token.lower()
        tail = lowered.rsplit("/", 1)[-1]
        (present if lowered in existing or tail in existing else missing).append(token)
    return Preflight(named, tuple(present), tuple(missing), scanned=True)


def warning_line(result: Preflight, alias: str) -> Optional[str]:
    """One line naming what is missing, or None when there is nothing to say."""
    if not result.warns:
        return None
    listed = ", ".join(result.missing[:3])
    if len(result.missing) > 3:
        listed += f", +{len(result.missing) - 3} more"
    return (
        f"heads up: {listed} is not in {alias}. "
        f"A new file will be created rather than that one edited."
        if len(result.missing) == 1 else
        f"heads up: none of {listed} are in {alias}. "
        f"New files will be created rather than those edited."
    )
