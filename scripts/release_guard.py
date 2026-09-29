"""Refuse a push that would put private material on a public remote.

Tri-AI lives in two repositories: a public demo, and a private repository
holding the real history, the Cortex indexes and the operator's notes.
Keeping them apart cannot rest on remembering which is which - the branch
carrying the Cortex is named `public-main`, which is precisely the sort of
detail that gets acted on at speed and regretted.

So the audience is declared in the tree, in `RELEASE_SCOPE`, and enforced at
the only moment the separation can actually fail: `git push`.

Three rules, all in the safe direction:

* **A missing or unrecognised scope is private.** A branch that has not said
  it is publishable is not publishable.
* **An unrecognised remote is public.** A destination nobody has classified
  gets the stricter treatment, not the looser one.
* **An unconfigured machine can push nothing private anywhere.** The private
  remote is read from local git config, so the public repository never names
  it - and until it is declared, the guard refuses.

Run by `scripts/hooks/pre-push`; see that file for installation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

SCOPE_FILE = "RELEASE_SCOPE"
PRIVATE_REMOTE_CONFIG = "triai.privateremote"
VALID_SCOPES = ("public", "private")


def parse_scope(text: object) -> str:
    """Read a declared scope. Anything unrecognised is private."""
    value = str(text or "").strip().lower()
    return value if value in VALID_SCOPES else "private"


def read_scope(root: Path | str) -> str:
    try:
        return parse_scope(Path(root, SCOPE_FILE).read_text(encoding="utf-8"))
    except OSError:
        return "private"


def _normalize(url: object) -> str:
    """Compare remotes by identity, not by substring.

    `tri-ai` is a prefix of `tri-ai-private`. A substring test would decide
    the public remote looked private and wave everything through, so the
    comparison is on a normalized whole string.
    """
    value = str(url or "").strip().lower()
    for prefix in ("https://", "http://", "ssh://", "git+ssh://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.startswith("git@"):
        value = value[len("git@"):]
    value = value.replace(":", "/", 1) if value.startswith("github.com:") else value
    if "@" in value.split("/", 1)[0]:
        value = value.split("@", 1)[1]
    if value.endswith(".git"):
        value = value[: -len(".git")]
    return value.rstrip("/")


def refusal(*, scope: str, destination: str,
            private_remotes: Iterable[str]) -> Optional[str]:
    """Why this push must not happen, or None if it may."""
    if parse_scope(scope) == "public":
        return None

    known = {_normalize(remote) for remote in private_remotes if str(remote).strip()}
    if not known:
        return (
            f"Refusing to push private content: no private remote is declared on "
            f"this machine. Run:  git config --add {PRIVATE_REMOTE_CONFIG} "
            f"<private repo url>   and try again."
        )
    if _normalize(destination) in known:
        return None
    return (
        f"Refusing to push: this branch declares {SCOPE_FILE}=private and "
        f"{destination} is not a declared private remote. Private branches hold "
        f"real paths, indexes and operator notes. Push to the private repository, "
        f"or set {SCOPE_FILE}=public on a branch that has been scrubbed."
    )


def configured_private_remotes(root: Path | str = ".") -> list[str]:
    try:
        finished = subprocess.run(  # nosec B603 - fixed argv, no shell
            ["git", "config", "--get-all", PRIVATE_REMOTE_CONFIG],
            cwd=str(root), capture_output=True, text=True, timeout=15,
            shell=False, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if finished.returncode != 0:
        return []
    return [line.strip() for line in finished.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    """Called by the pre-push hook as: release_guard.py <remote-name> <url>."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    destination = arguments[1] if len(arguments) > 1 else (
        arguments[0] if arguments else "")
    root = Path(__file__).resolve().parents[1]

    reason = refusal(
        scope=read_scope(root),
        destination=destination,
        private_remotes=configured_private_remotes(root),
    )
    if reason is None:
        return 0
    print("", file=sys.stderr)
    print("  BLOCKED BY tri-ai release guard", file=sys.stderr)
    print("  " + reason, file=sys.stderr)
    print("", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through main.
    raise SystemExit(main())
