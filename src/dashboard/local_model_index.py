"""Private, metadata-only collector for the local Ollama model inventory.

The Cortex file graph and model inventory are deliberately separate. A model
name and byte size are operational metadata; no prompt, response, token, API
key, endpoint, or machine path is collected here.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

from . import private_index


_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _model_names(payload: object) -> list[tuple[str, int]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("Ollama model response is invalid")
    found: dict[str, int] = {}
    for model in payload["models"]:
        if not isinstance(model, dict):
            continue
        name = model.get("name")
        size = model.get("size")
        if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            size = 0
        found.setdefault(name, size)
    return sorted(found.items(), key=lambda row: row[0].casefold())


def _index(models: list[tuple[str, int]], *, status: str, diagnostic: str) -> dict[str, object]:
    source_id = "ollama"
    observed = _timestamp()
    items: list[dict[str, object]] = [
        {
            "id": private_index._stable_id(source_id, ".", "folder"),
            "label": "Local Ollama",
            "name": "Local Ollama",
            "relative_path": ".",
            "provenance": "ollama:.",
            "kind": "folder",
            "source": source_id,
            "parent_id": None,
            "size": None,
            "modified_at": observed,
            "synthetic": False,
        }
    ] if status == "indexed" else []
    parent_id = items[0]["id"] if items else None
    for name, size in models:
        relative_path = f"models/{name}"
        items.append({
            "id": private_index._stable_id(source_id, relative_path, "file"),
            "label": name,
            "name": name,
            "relative_path": relative_path,
            "provenance": f"ollama:{relative_path}",
            "kind": "file",
            "source": source_id,
            "parent_id": parent_id,
            "size": size,
            "modified_at": observed,
            "synthetic": False,
        })
    payload: dict[str, object] = {
        "status": status,
        "synthetic": False,
        "item_count": len(items),
        "sources": [{
            "id": source_id,
            "label": "Local Ollama",
            "node_count": len(items),
            "authorized": True,
        }],
        "items": items,
        "diagnostic": diagnostic,
    }
    return private_index.validate_private_index(payload)


def ollama_inventory(base_url: str = "http://127.0.0.1:11434") -> dict[str, object]:
    """Read local model metadata, never exporting the configured endpoint."""
    try:
        url = base_url.rstrip("/") + "/api/tags"
        with request.urlopen(url, timeout=5) as response:  # nosec B310 - explicit loopback operator setting
            models = _model_names(json.loads(response.read().decode("utf-8")))
    except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return _index([], status="unavailable", diagnostic="Local Ollama inventory is unavailable.")
    return _index(models, status="indexed", diagnostic=f"Indexed metadata for {len(models)} local model(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a private local Ollama model inventory for Cortex.")
    parser.add_argument("--output", required=True, help="Local JSON destination for the inventory.")
    parser.add_argument("--url", default="http://127.0.0.1:11434", help="Local Ollama base URL.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = ollama_inventory(args.url)
    output = Path(args.output)
    private_index.write_private_index_atomic(output, payload)
    private_index.write_private_index_summary_atomic(output, payload)
    return 0 if payload["status"] == "indexed" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main.
    raise SystemExit(main())
