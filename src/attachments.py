"""Files sent to the bot: named, sized, stored outside any workspace.

Until now a message without text was dropped on the floor - `if not
isinstance(text, str): continue` - so sending a PDF or a logo did nothing at
all, silently. Task `t_c9b08613` asked for a landing page "(I have the logo with
me)" and there was no way for that logo to reach the agent.

**Attachments never land in a workspace.** The worker refuses to claim a task
whose tree is dirty, so a file written into the workspace before the run would
make the task skip its own precheck - the intake would break execution. They
live under `~/.tri-ai/attachments/<owner>/` instead, and the agent is told their
absolute paths; copying one in is then the agent's action inside the run, where
it belongs and is recorded as that run's output.

Nothing here trusts Telegram's filename. A name arrives from a remote sender and
is used only for its extension and a slug; the stored name is derived locally.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

# Telegram's Bot API will not serve a download above 20 MB, so anything larger
# cannot be fetched however we ask. Stated here so the limit is a decision
# rather than a surprise at the HTTP layer.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

# The message keys that can carry a file, in the order Telegram prefers them.
# `photo` is a list of sizes, smallest first.
ATTACHMENT_KEYS = ("document", "photo", "video", "audio", "voice", "animation")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_EXT = re.compile(r"\.[A-Za-z0-9]{1,12}\Z")


def attachments_root(runtime: Optional[Path | str] = None) -> Path:
    """Where attachments are kept. Deliberately outside every workspace."""
    base = Path(runtime) if runtime is not None else Path.home() / ".tri-ai"
    return base / "attachments"


def safe_name(raw: object, *, fallback: str = "file") -> str:
    """A storable filename derived from an untrusted one.

    The sender controls the name in the update. It is never used as a path: the
    directory separators, the traversal, the control characters and the leading
    dots all go, leaving a slug plus whatever extension survived. A name that
    reduces to nothing becomes the fallback rather than an empty path.
    """
    text = raw if isinstance(raw, str) else ""
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("\\", "/").rsplit("/", 1)[-1]      # strip any path
    suffix = ""
    match = _EXT.search(text)
    if match:
        suffix = match.group(0).lower()
        text = text[: match.start()]
    stem = _UNSAFE.sub("-", text).strip("-._")
    if not stem:
        stem = fallback
    return f"{stem[:60]}{suffix}"


@dataclass(frozen=True)
class Attachment:
    """One file offered by a message, before it is downloaded."""

    file_id: str
    kind: str                      # document | photo | video | audio | voice
    name: str                      # already made safe
    size: Optional[int] = None     # bytes, when Telegram declared one
    mime: Optional[str] = None

    @property
    def too_large(self) -> bool:
        return isinstance(self.size, int) and self.size > MAX_ATTACHMENT_BYTES


def _largest_photo(sizes: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Telegram sends a photo as thumbnails ascending; the last is full size."""
    best, best_area = None, -1
    for size in sizes:
        if not isinstance(size, Mapping):
            continue
        area = (size.get("width") or 0) * (size.get("height") or 0)
        if area >= best_area and isinstance(size.get("file_id"), str):
            best, best_area = size, area
    return best


def from_message(message: Mapping[str, Any]) -> tuple[Attachment, ...]:
    """Every file a message carries, as Attachments. Empty for a plain text message."""
    found: list[Attachment] = []
    for key in ATTACHMENT_KEYS:
        blob = message.get(key)
        if key == "photo":
            if not isinstance(blob, list):
                continue
            chosen = _largest_photo(blob)
            if chosen is None:
                continue
            found.append(Attachment(
                file_id=str(chosen["file_id"]),
                kind="photo",
                # Photos carry no filename; one is derived from the id so two
                # photos in a row cannot collide.
                name=safe_name(f"photo-{str(chosen['file_id'])[-8:]}.jpg", fallback="photo"),
                size=chosen.get("file_size") if isinstance(chosen.get("file_size"), int) else None,
                mime="image/jpeg",
            ))
            continue
        if not isinstance(blob, Mapping) or not isinstance(blob.get("file_id"), str):
            continue
        declared = blob.get("file_name")
        default = {
            "voice": "voice.ogg", "audio": "audio.mp3",
            "video": "video.mp4", "animation": "animation.mp4",
        }.get(key, "file.bin")
        found.append(Attachment(
            file_id=str(blob["file_id"]),
            kind=key,
            name=safe_name(declared if isinstance(declared, str) else default,
                           fallback=key),
            size=blob.get("file_size") if isinstance(blob.get("file_size"), int) else None,
            mime=blob.get("mime_type") if isinstance(blob.get("mime_type"), str) else None,
        ))
    return tuple(found)


def unique_path(directory: Path, name: str) -> Path:
    """A path inside ``directory`` that does not yet exist.

    Also the containment check: the resolved result must stay inside the
    directory, so a name that survived sanitising and still climbs out is
    refused rather than written.
    """
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    candidate = (directory / name).resolve()
    if not candidate.is_relative_to(directory):
        raise ValueError(f"attachment name escapes its directory: {name!r}")
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for n in range(2, 1000):
        nxt = (directory / f"{stem}-{n}{suffix}").resolve()
        if not nxt.exists():
            return nxt
    raise ValueError(f"cannot find a free name for {name!r}")


@dataclass
class StoredAttachment:
    """A file that is now on disk, with the facts a prompt needs to name it."""

    path: Path
    kind: str
    size: int
    original: str
    mime: Optional[str] = None


def describe_for_prompt(stored: Iterable[StoredAttachment]) -> str:
    """The paragraph appended to a task prompt naming the files it was given.

    Absolute paths, because the agent's shell starts elsewhere and these are
    deliberately outside the workspace. It is told it may copy them in, since
    that is the only way they become part of the run's recorded output.
    """
    items = list(stored)
    if not items:
        return ""
    noun = "file" if len(items) == 1 else "files"
    lines = [
        "",
        f"The operator attached {len(items)} {noun} to this request. "
        f"They are already on disk at these exact paths:",
    ]
    for item in items:
        size = f"{item.size:,} bytes"
        lines.append(f"  {item.path}   ({item.kind}, {size})")
    lines.append(
        "Read them from those paths. If the deliverable needs one - a logo in a "
        "page, data in a report - copy it into the workspace yourself; a file "
        "only counts as this run's output once it is in the workspace."
    )
    return "\n".join(lines)
