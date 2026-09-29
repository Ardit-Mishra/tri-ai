"""Build Cortex source indexes for the sources that are not plain folders.

Seven declared sources sat at zero nodes, with the Cortex saying so plainly:
"Awaiting metadata index from 7 authorized source(s)." Three of them are
local stores that only needed a scanner pointed at them; the rest are
services that either answer or do not.

Every collector obeys the same two rules as the filesystem indexer:

**Metadata only.** Names, sizes and modification times. No file contents, no
repository contents, no message bodies, no absolute paths - `private_index`
validates that and refuses a payload carrying one.

**An unreachable source is reported, never invented.** A collector that
cannot reach its service returns a valid `unavailable` payload whose
diagnostic says why. A Cortex that quietly omits a source it was told to
show is worse than one that says the source is down: the first looks
complete.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from dashboard import private_index
else:
    from . import private_index


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _empty(source_id: str, label: str, diagnostic: str,
           *, status: str = "unavailable") -> dict[str, object]:
    """A source that could not be read, said out loud rather than omitted."""
    payload = {
        "status": status,
        "synthetic": False,
        "item_count": 0,
        "sources": [{"id": source_id, "label": label,
                     "node_count": 0, "authorized": True}],
        "items": [],
        "diagnostic": diagnostic,
    }
    return private_index.validate_private_index(payload)


def _tree(source_id: str, label: str, rows: Sequence[Mapping[str, object]],
          *, status: str, diagnostic: str) -> dict[str, object]:
    """One root folder with `rows` beneath it, in the private-index shape."""
    observed = _timestamp()
    root_id = private_index._stable_id(source_id, ".", "folder")
    items: list[dict[str, object]] = [{
        "id": root_id, "label": label, "name": label, "relative_path": ".",
        "provenance": f"{source_id}:.", "kind": "folder", "source": source_id,
        "parent_id": None, "size": None, "modified_at": observed,
        "synthetic": False,
    }]
    for row in rows:
        relative_path = str(row["relative_path"])
        items.append({
            "id": private_index._stable_id(source_id, relative_path, "file"),
            "label": str(row["name"]), "name": str(row["name"]),
            "relative_path": relative_path,
            "provenance": f"{source_id}:{relative_path}",
            "kind": "file", "source": source_id, "parent_id": root_id,
            # `private_index` requires an integer size on a file item and
            # allows null only on folders. A model catalog and a hosting
            # provider both report no byte size, so 0 is the only legal
            # value - the diagnostic says sizes are absent rather than
            # letting a reader mistake 0 for "empty".
            "size": int(row["size"]) if isinstance(row.get("size"), int) else 0,
            "modified_at": str(row.get("modified_at") or observed),
            "synthetic": False,
        })
    payload = {
        "status": status, "synthetic": False, "item_count": len(items),
        "sources": [{"id": source_id, "label": label,
                     "node_count": len(items), "authorized": True}],
        "items": items, "diagnostic": diagnostic,
    }
    return private_index.validate_private_index(payload)


# --- services that answer over HTTP -----------------------------------------

def openai_catalog(base_url: str, *, source_id: str, label: str,
                   timeout: float = 6.0, api_key: str | None = None,
                   ) -> dict[str, object]:
    """Index an OpenAI-compatible `/v1/models` catalog.

    Used for OmniRoute and FreeLLMAPI, which both speak it. Only model
    identifiers are read; no key, endpoint or provider credential is stored
    in the index.

    `api_key` is read from the environment by the caller and presented as a
    bearer token. It is never written to the index, never logged, and never
    included in a diagnostic - an unauthorized answer reports the status
    code and nothing else, because a diagnostic ends up on a dashboard.
    """
    url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        probe = request.Request(url, headers=headers)
        with request.urlopen(probe, timeout=timeout) as response:  # nosec B310 - operator-configured host
            body = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            return _empty(source_id, label, (
                f"{label} is running but refused the catalog request "
                f"(HTTP {exc.code}). Set its API key in the environment and "
                f"re-run this collector."))
        return _empty(source_id, label, f"{label} answered HTTP {exc.code}.")
    except (OSError, error.URLError, ValueError, UnicodeDecodeError) as exc:
        return _empty(source_id, label,
                      f"{label} did not answer ({type(exc).__name__}).")
    entries = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(entries, list):
        return _empty(source_id, label,
                      f"{label} answered without a model list.")
    rows = []
    for entry in entries:
        name = entry.get("id") if isinstance(entry, Mapping) else None
        if isinstance(name, str) and name.strip():
            rows.append({"name": name.strip(),
                         "relative_path": f"models/{name.strip()}",
                         "size": None})
    if not rows:
        return _empty(source_id, label, f"{label} is reachable but serves no models.",
                      status="partial")
    return _tree(source_id, label, rows, status="indexed",
                 diagnostic=(f"Indexed {len(rows)} routable model(s) from {label}. "
                             "The catalog reports no byte sizes."))


# --- local stores -----------------------------------------------------------

def obsidian_vaults(config_path: Path | None = None) -> dict[str, object]:
    """Index every vault Obsidian itself lists, as one region per vault.

    The vault list comes from Obsidian's own `obsidian.json` rather than a
    guess at where notes live. Note *titles* are file names, so this is
    metadata in the same sense the filesystem index is - but `.obsidian`
    itself is excluded, because plugin state and sync configuration are not
    notes and can carry account identifiers.
    """
    config_path = config_path or (
        Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        / "obsidian" / "obsidian.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty("obsidian", "Obsidian",
                      "Obsidian's vault list was not readable on this device.")
    roots: dict[str, str] = {}
    for record in (config.get("vaults") or {}).values():
        path = record.get("path") if isinstance(record, Mapping) else None
        if not isinstance(path, str) or not Path(path).is_dir():
            continue
        # The label becomes the source id, so it must not be a path.
        name = Path(path).name or Path(path).drive.replace(":", "") or "vault"
        label = f"Obsidian {name}"
        if label not in roots:
            roots[label] = path
    if not roots:
        return _empty("obsidian", "Obsidian",
                      "Obsidian is configured but none of its vaults are present.")
    payload = private_index.scan_authorized_roots(
        roots,
        excluded_directory_names={
            ".obsidian", ".git", ".trash", "node_modules", "__pycache__",
        },
    )
    payload["diagnostic"] = (
        f"Indexed {len(roots)} Obsidian vault(s), excluding plugin state."
    )
    return private_index.validate_private_index(payload)


def agent_sessions(home: Path | None = None) -> dict[str, object]:
    """Index Claude and Codex session stores as one region, two roots.

    File names and sizes only. A transcript's *contents* are the most
    sensitive material on this machine, and nothing here opens one.
    """
    home = home or Path.home()
    candidates = {
        "Claude Codex claude": home / ".claude" / "projects",
        "Claude Codex codex": home / ".codex" / "sessions",
    }
    roots = {label: str(path) for label, path in candidates.items() if path.is_dir()}
    if not roots:
        return _empty("claude-codex", "Claude and Codex",
                      "Neither a Claude nor a Codex session store is present here.")
    payload = private_index.scan_authorized_roots(
        roots, excluded_directory_names={"__pycache__", ".git"},
    )
    payload["diagnostic"] = (
        f"Indexed {len(roots)} agent session store(s), names and sizes only."
    )
    return private_index.validate_private_index(payload)


# --- services behind a CLI --------------------------------------------------

def _run(command: Sequence[str], timeout: float = 45.0) -> tuple[int, str]:
    try:
        finished = subprocess.run(  # nosec B603 - fixed argv, no shell
            list(command), capture_output=True, text=True, timeout=timeout,
            shell=False, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return finished.returncode, finished.stdout or finished.stderr or ""


def github_repositories(limit: int = 500) -> dict[str, object]:
    """Index repository names via the already-authenticated `gh` CLI.

    Read-only, and it never handles the token: `gh` holds that. Repository
    *contents* are not read - only the names, sizes and push times that the
    listing already returns.
    """
    code, output = _run([
        "gh", "repo", "list", "--limit", str(int(limit)),
        "--json", "nameWithOwner,diskUsage,pushedAt,isPrivate",
    ])
    if code != 0:
        return _empty("github", "GitHub",
                      "The GitHub CLI is not authenticated on this device.")
    try:
        records = json.loads(output)
    except ValueError:
        return _empty("github", "GitHub", "The GitHub CLI returned no usable listing.")
    if not isinstance(records, list) or not records:
        return _empty("github", "GitHub", "The GitHub account lists no repositories.",
                      status="partial")
    rows = []
    private_count = 0
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = record.get("nameWithOwner")
        if not isinstance(name, str) or not name.strip():
            continue
        if record.get("isPrivate") is True:
            private_count += 1
        disk = record.get("diskUsage")
        rows.append({
            "name": name.strip(),
            "relative_path": f"repositories/{name.strip()}",
            # gh reports kibibytes.
            "size": int(disk) * 1024 if isinstance(disk, int) else None,
            "modified_at": _github_time(record.get("pushedAt")),
        })
    return _tree("github", "GitHub", rows, status="indexed",
                 diagnostic=(f"Indexed {len(rows)} repository name(s); "
                             f"{private_count} private. No contents read."))


def _github_time(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return _timestamp()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _timestamp()
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def hosting_projects() -> dict[str, object]:
    """Index Vercel and Render project names, when either CLI is signed in."""
    rows: list[dict[str, object]] = []
    notes: list[str] = []

    code, output = _run(["vercel", "project", "ls", "--json"])
    if code == 0:
        try:
            records = json.loads(output)
        except ValueError:
            records = []
        listing = records.get("projects") if isinstance(records, Mapping) else records
        for record in listing if isinstance(listing, list) else []:
            name = record.get("name") if isinstance(record, Mapping) else None
            if isinstance(name, str) and name.strip():
                rows.append({"name": f"vercel/{name.strip()}",
                             "relative_path": f"vercel/{name.strip()}", "size": None})
        notes.append("Vercel signed in.")
    else:
        notes.append("Vercel CLI is not available or not signed in.")

    code, output = _run(["render", "services", "-o", "json", "--confirm"])
    if code == 0:
        try:
            records = json.loads(output)
        except ValueError:
            records = []
        for record in records if isinstance(records, list) else []:
            service = record.get("service") if isinstance(record, Mapping) else None
            name = (service or record).get("name") if isinstance(record, Mapping) else None
            if isinstance(name, str) and name.strip():
                rows.append({"name": f"render/{name.strip()}",
                             "relative_path": f"render/{name.strip()}", "size": None})
        notes.append("Render signed in.")
    else:
        notes.append("Render CLI is not available or not signed in.")

    if not rows:
        return _empty("vercel-render", "Vercel and Render", " ".join(notes))
    return _tree("vercel-render", "Vercel and Render", rows, status="indexed",
                 diagnostic=(f"Indexed {len(rows)} hosted project(s), names only. "
                             + " ".join(notes)))


# --- the run ----------------------------------------------------------------

def default_destination() -> Path:
    return Path(os.environ.get(
        "TRI_AI_PRIVATE_SOURCE_DIR",
        Path.home() / ".tri-ai" / "private-sources"))


COLLECTORS = {
    "obsidian": obsidian_vaults,
    "claude-codex": agent_sessions,
    "github": github_repositories,
    "vercel-render": hosting_projects,
    "omniroute": lambda: openai_catalog(
        os.environ.get("TRI_AI_OMNIROUTE_URL", "http://127.0.0.1:20128"),
        source_id="omniroute", label="OmniRoute", timeout=20.0,
        api_key=os.environ.get("OMNIROUTE_API_KEY")),
    "freellmapi": lambda: openai_catalog(
        os.environ.get("TRI_AI_FREELLMAPI_URL", "http://127.0.0.1:3001"),
        source_id="freellmapi", label="FreeLLMAPI",
        api_key=os.environ.get("FREELLMAPI_API_KEY")),
}


def collect(name: str, destination: Path) -> dict[str, object]:
    payload = COLLECTORS[name]()
    output = destination / f"{name}.json"
    private_index.write_private_index_atomic(output, payload)
    private_index.write_private_index_summary_atomic(output, payload)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Index the Cortex sources that are not plain folders.")
    parser.add_argument("--only", action="append", choices=sorted(COLLECTORS),
                        help="Run one collector. Repeat for several.")
    parser.add_argument("--destination", default=None)
    arguments = parser.parse_args(argv)
    destination = Path(arguments.destination) if arguments.destination else default_destination()
    destination.mkdir(parents=True, exist_ok=True)

    failures = 0
    for name in (arguments.only or sorted(COLLECTORS)):
        try:
            payload = collect(name, destination)
        except Exception as exc:  # a broken collector must not stop the rest
            print(f"{name:<14} ERROR    {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            continue
        status = str(payload["status"])
        if status != "indexed":
            failures += 1
        print(f"{name:<14} {status:<12} {payload['item_count']:>7} items  "
              f"{payload['diagnostic']}")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover - exercised through main.
    raise SystemExit(main())
