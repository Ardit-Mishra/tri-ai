"""Build and persist a private metadata-only file graph."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Collection, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


_REPARSE_POINT = 0x0400
_SOURCE_ID_RE = re.compile(r"[^a-z0-9]+")
_VALID_STATUSES = frozenset({"indexed", "partial", "unavailable"})
_SUMMARY_SCHEMA_VERSION = 1


def _is_absolute_text(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _source_id(label: str) -> str:
    source_id = _SOURCE_ID_RE.sub("-", label.casefold()).strip("-")
    if not source_id:
        raise ValueError("authorized root labels must contain a letter or number")
    return source_id


def _stable_id(source_id: str, relative_path: str, kind: str) -> str:
    identity = f"{source_id}\0{kind}\0{relative_path}".encode("utf-8")
    return f"private:{hashlib.sha256(identity).hexdigest()[:24]}"


def _modified_at(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def scan_authorized_roots(
    roots: Mapping[str, str | os.PathLike[str]], *,
    excluded_directory_names: Collection[str] = (),
) -> dict[str, object]:
    """Recursively index metadata beneath explicitly supplied local roots.

    Exclusions are directory basenames, not paths. That keeps a reusable
    desktop-profile recipe from embedding machine-specific locations while
    allowing application state and credentials to remain outside the index.
    """
    if not isinstance(roots, Mapping):
        raise TypeError("roots must map public source labels to local paths")
    excluded_names: set[str] = set()
    for name in excluded_directory_names:
        if not isinstance(name, str) or not name.strip() or name.strip() in {".", ".."}:
            raise ValueError("excluded directory names must be non-empty names")
        normalized = name.strip()
        if "/" in normalized or "\\" in normalized:
            raise ValueError("excluded directory names must not contain paths")
        excluded_names.add(normalized.casefold())

    items: list[dict[str, object]] = []
    sources: list[dict[str, object]] = []
    diagnostics: list[str] = []
    used_source_ids: set[str] = set()

    for label, supplied_path in roots.items():
        if not isinstance(label, str) or not label.strip():
            raise ValueError("authorized root labels must be non-empty strings")
        label = label.strip()
        if _is_absolute_text(label):
            raise ValueError("authorized root labels must not be absolute paths")
        source_id = _source_id(label)
        if source_id in used_source_ids:
            raise ValueError(f"authorized root labels produce duplicate source id: {source_id}")
        used_source_ids.add(source_id)
        root = Path(supplied_path)
        source_items: list[dict[str, object]] = []

        def visit(path: Path, relative_path: str, parent_id: str | None) -> None:
            try:
                metadata = os.lstat(path)
            except FileNotFoundError:
                if relative_path == ".":
                    diagnostics.append(f"{label}: root does not exist")
                else:
                    diagnostics.append(f"{label}: item disappeared at {relative_path}")
                return
            except OSError:
                if relative_path == ".":
                    diagnostics.append(f"{label}: root is unreadable")
                else:
                    diagnostics.append(f"{label}: item is unreadable at {relative_path}")
                return

            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                if relative_path == ".":
                    diagnostics.append(f"{label}: root is a link or reparse point")
                return

            if relative_path != "." and path.name.casefold() in excluded_names:
                return

            if stat.S_ISDIR(metadata.st_mode):
                kind = "folder"
                try:
                    with os.scandir(path) as entries:
                        children = sorted(entries, key=lambda entry: (entry.name.casefold(), entry.name))
                except OSError:
                    if relative_path == ".":
                        diagnostics.append(f"{label}: root is unreadable")
                    else:
                        diagnostics.append(f"{label}: folder is unreadable at {relative_path}")
                    return
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
                children = []
            else:
                return

            item_id = _stable_id(source_id, relative_path, kind)
            name = path.name or label
            source_items.append({
                "id": item_id,
                "label": label if relative_path == "." else name,
                "name": name,
                "relative_path": relative_path,
                "provenance": f"{source_id}:{relative_path}",
                "kind": kind,
                "source": source_id,
                "parent_id": parent_id,
                "size": metadata.st_size if kind == "file" else None,
                "modified_at": _modified_at(metadata.st_mtime),
                "synthetic": False,
            })
            for child in children:
                child_relative = child.name if relative_path == "." else f"{relative_path}/{child.name}"
                visit(Path(child.path), child_relative, item_id)

        visit(root, ".", None)
        items.extend(source_items)
        sources.append({
            "id": source_id,
            "label": label,
            "node_count": len(source_items),
            "authorized": True,
        })

    if not items:
        status = "unavailable"
    elif diagnostics:
        status = "partial"
    else:
        status = "indexed"

    if diagnostics:
        diagnostic = "; ".join(diagnostics)
    elif sources:
        diagnostic = f"Indexed metadata from {len(sources)} authorized source(s)."
    else:
        diagnostic = "No authorized roots were provided."

    result: dict[str, object] = {
        "status": status,
        "synthetic": False,
        "item_count": len(items),
        "sources": sources,
        "items": items,
        "diagnostic": diagnostic,
    }
    validate_private_index(result)
    return result


def _require_type(value: object, expected: type, field: str) -> None:
    if type(value) is not expected:
        raise ValueError(f"{field} has an invalid type")


def _validate_label(value: object, field: str) -> str:
    _require_type(value, str, field)
    assert isinstance(value, str)
    if not value or _is_absolute_text(value):
        raise ValueError(f"{field} must be a non-empty, non-absolute label")
    return value


def _validate_relative_path(value: object, field: str) -> str:
    _require_type(value, str, field)
    assert isinstance(value, str)
    path = PurePosixPath(value)
    if value != "." and (
        not value
        or _is_absolute_text(value)
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{field} must be a normalized relative_path")
    return value


def validate_private_index(payload: object) -> dict[str, object]:
    """Validate and return a private file graph contract."""
    if not isinstance(payload, dict):
        raise ValueError("private index must be a JSON object")

    status = payload.get("status")
    if status not in _VALID_STATUSES:
        raise ValueError("status is invalid")
    if payload.get("synthetic") is not False:
        raise ValueError("synthetic must be false")
    _require_type(payload.get("item_count"), int, "item_count")
    _require_type(payload.get("diagnostic"), str, "diagnostic")
    sources = payload.get("sources")
    items = payload.get("items")
    _require_type(sources, list, "sources")
    _require_type(items, list, "items")
    assert isinstance(sources, list) and isinstance(items, list)
    if payload["item_count"] != len(items):
        raise ValueError("item_count does not match items")

    source_ids: set[str] = set()
    declared_counts: dict[str, int] = {}
    for position, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"sources[{position}] must be an object")
        source_id = source.get("id")
        _require_type(source_id, str, f"sources[{position}].id")
        assert isinstance(source_id, str)
        if not source_id or source_id in source_ids or _is_absolute_text(source_id):
            raise ValueError(f"sources[{position}].id is invalid")
        source_ids.add(source_id)
        _validate_label(source.get("label"), f"sources[{position}].label")
        _require_type(source.get("node_count"), int, f"sources[{position}].node_count")
        if source["node_count"] < 0:
            raise ValueError(f"sources[{position}].node_count is invalid")
        if source.get("authorized") is not True:
            raise ValueError(f"sources[{position}].authorized must be true")
        declared_counts[source_id] = source["node_count"]

    item_ids: set[str] = set()
    actual_counts = {source_id: 0 for source_id in source_ids}
    parents: dict[str, str | None] = {}
    for position, item in enumerate(items):
        prefix = f"items[{position}]"
        if not isinstance(item, dict):
            raise ValueError(f"{prefix} must be an object")
        item_id = item.get("id")
        _require_type(item_id, str, f"{prefix}.id")
        assert isinstance(item_id, str)
        if not item_id or item_id in item_ids:
            raise ValueError(f"{prefix}.id is invalid")
        item_ids.add(item_id)
        _validate_label(item.get("label"), f"{prefix}.label")
        _validate_label(item.get("name"), f"{prefix}.name")
        relative_path = _validate_relative_path(item.get("relative_path"), f"{prefix}.relative_path")
        kind = item.get("kind")
        if kind not in {"file", "folder"}:
            raise ValueError(f"{prefix}.kind is invalid")
        source_id = item.get("source")
        if source_id not in source_ids:
            raise ValueError(f"{prefix}.source is unknown")
        assert isinstance(source_id, str)
        if item.get("provenance") != f"{source_id}:{relative_path}":
            raise ValueError(f"{prefix}.provenance is invalid")
        parent_id = item.get("parent_id")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"{prefix}.parent_id is invalid")
        parents[item_id] = parent_id
        size = item.get("size")
        if kind == "file" and (type(size) is not int or size < 0):
            raise ValueError(f"{prefix}.size is invalid")
        if kind == "folder" and size is not None:
            raise ValueError(f"{prefix}.size must be null for folders")
        modified_at = item.get("modified_at")
        _require_type(modified_at, str, f"{prefix}.modified_at")
        try:
            datetime.fromisoformat(modified_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{prefix}.modified_at is invalid") from error
        if item.get("synthetic") is not False:
            raise ValueError(f"{prefix}.synthetic must be false")
        actual_counts[source_id] += 1

    for item_id, parent_id in parents.items():
        if parent_id is not None and parent_id not in item_ids:
            raise ValueError(f"item {item_id}.parent_id is unknown")
        seen = {item_id}
        current = parent_id
        while current is not None:
            if current in seen:
                raise ValueError(f"item {item_id}.parent_id creates a cycle")
            seen.add(current)
            current = parents.get(current)

    if actual_counts != declared_counts:
        raise ValueError("source node_count does not match items")

    try:
        json.dumps(payload)
    except (TypeError, ValueError) as error:
        raise ValueError("private index is not JSON-serializable") from error
    return payload


def load_private_index(path: str | os.PathLike[str]) -> dict[str, object]:
    """Load and validate a private index JSON document."""
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    return validate_private_index(payload)


def private_index_summary_path(path: str | os.PathLike[str]) -> Path:
    """Return the adjacent, metadata-minimal summary filename for an index."""
    index_path = Path(path)
    return index_path.with_name(f"{index_path.stem}.summary.json")


def _private_index_summary(payload: object, index_path: Path) -> dict[str, object]:
    """Reduce a validated index to the only data needed for the Cortex field.

    The dashboard needs counts to construct its procedural visualisation. It
    does not need filenames, relative paths, provenance, timestamps, or file
    sizes. Keeping the two representations separate makes a cold dashboard
    startup inexpensive without widening the private-data boundary.
    """
    graph = validate_private_index(payload)
    metadata = index_path.stat()
    folder_counts: dict[str, int] = {}
    for item in graph["items"]:
        assert isinstance(item, dict)
        if item["kind"] == "folder":
            source_id = item["source"]
            assert isinstance(source_id, str)
            folder_counts[source_id] = folder_counts.get(source_id, 0) + 1
    sources = []
    for source in graph["sources"]:
        assert isinstance(source, dict)
        sources.append({"id": source["id"], "node_count": source["node_count"]})
    return {
        "schema_version": _SUMMARY_SCHEMA_VERSION,
        "index_mtime_ns": metadata.st_mtime_ns,
        "index_size": metadata.st_size,
        "status": graph["status"],
        "item_count": graph["item_count"],
        "sources": sources,
        "folder_counts": folder_counts,
    }


def _validate_private_index_summary(payload: object, index_path: Path) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("private index summary must be a JSON object")
    if payload.get("schema_version") != _SUMMARY_SCHEMA_VERSION:
        raise ValueError("private index summary schema version is invalid")
    _require_type(payload.get("index_mtime_ns"), int, "summary.index_mtime_ns")
    _require_type(payload.get("index_size"), int, "summary.index_size")
    _require_type(payload.get("item_count"), int, "summary.item_count")
    if payload["item_count"] < 0 or payload["index_size"] < 0:
        raise ValueError("private index summary count is invalid")
    if payload.get("status") not in _VALID_STATUSES:
        raise ValueError("private index summary status is invalid")
    sources = payload.get("sources")
    folder_counts = payload.get("folder_counts")
    _require_type(sources, list, "summary.sources")
    _require_type(folder_counts, dict, "summary.folder_counts")
    assert isinstance(sources, list) and isinstance(folder_counts, dict)
    source_ids: set[str] = set()
    total = 0
    for position, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"summary.sources[{position}] must be an object")
        source_id = source.get("id")
        _require_type(source_id, str, f"summary.sources[{position}].id")
        assert isinstance(source_id, str)
        node_count = source.get("node_count")
        _require_type(node_count, int, f"summary.sources[{position}].node_count")
        if not source_id or source_id in source_ids or node_count < 0:
            raise ValueError(f"summary.sources[{position}] is invalid")
        source_ids.add(source_id)
        total += node_count
    if total != payload["item_count"]:
        raise ValueError("private index summary item_count does not match sources")
    for source_id, folder_count in folder_counts.items():
        if source_id not in source_ids or type(folder_count) is not int or folder_count < 0:
            raise ValueError("private index summary folder_counts is invalid")
        source_count = next(source["node_count"] for source in sources if source["id"] == source_id)
        if folder_count > source_count:
            raise ValueError("private index summary folder count is invalid")
    metadata = index_path.stat()
    if (
        payload["index_mtime_ns"] != metadata.st_mtime_ns
        or payload["index_size"] != metadata.st_size
    ):
        raise ValueError("private index summary is stale")
    return payload


def load_private_index_summary(path: str | os.PathLike[str]) -> dict[str, object]:
    """Load a matching, path-free index summary or reject it as stale."""
    index_path = Path(path)
    summary_path = private_index_summary_path(index_path)
    with summary_path.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    return _validate_private_index_summary(payload, index_path)


def _write_json_atomic(destination: Path, payload: object) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def write_private_index_atomic(
    path: str | os.PathLike[str], payload: object,
) -> None:
    """Validate and atomically replace a private index JSON document."""
    validated = validate_private_index(payload)
    _write_json_atomic(Path(path), validated)


def write_private_index_summary_atomic(
    path: str | os.PathLike[str], payload: object,
) -> Path:
    """Write a matching path-free summary after the full index is durable."""
    index_path = Path(path)
    summary = _private_index_summary(payload, index_path)
    destination = private_index_summary_path(index_path)
    _write_json_atomic(destination, summary)
    return destination
