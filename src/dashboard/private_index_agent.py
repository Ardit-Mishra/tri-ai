"""Opt-in local source agent for Cortex's metadata-only file index.

Run this on a device that owns the folders to be represented. It does not
upload, serve, or inspect file contents: it writes one validated local JSON
index for a separately configured private transport to collect later.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence
from pathlib import Path

from . import private_index
from memory import brain


def _root_mapping(values: Sequence[str]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for value in values:
        label, separator, path = value.partition("=")
        if not separator or not label.strip() or not path.strip():
            raise ValueError("--root must use LABEL=PATH")
        label = label.strip()
        if label in roots:
            raise ValueError(f"duplicate root label: {label}")
        roots[label] = path.strip()
    if not roots:
        raise ValueError("at least one --root LABEL=PATH is required")
    return roots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a metadata-only Cortex source index from approved local roots.",
    )
    parser.add_argument("--output", required=True, help="Local JSON destination for the index.")
    parser.add_argument(
        "--root", action="append", required=True, metavar="LABEL=PATH",
        help="One approved local root. Repeat for each root on this device.",
    )
    parser.add_argument(
        "--watch-seconds", type=int, metavar="SECONDS",
        help="Rebuild the private index repeatedly. Omit for one metadata-only pass.",
    )
    parser.add_argument(
        "--exclude-directory", action="append", default=[], metavar="NAME",
        help="Directory basename to skip anywhere below an approved root. Repeat as needed.",
    )
    parser.add_argument(
        "--brain", metavar="PATH",
        help="Optional local Brain database for path-free scan summaries.",
    )
    return parser


def _record_brain_summary(path: Path, payload: dict[str, object]) -> None:
    """Capture a scan fact without retaining local source metadata.

    The file graph remains the only index that can hold private file metadata.
    Brain receives a compact operational fact that makes source refreshes
    inspectable alongside task history without copying labels, paths, or names.
    """
    source_count = len(payload["sources"])
    node_count = int(payload["item_count"])
    status = str(payload["status"])
    body = (
        f"Cortex completed a metadata-only scan of {source_count} authorized "
        f"source{'s' if source_count != 1 else ''}: {node_count} indexed "
        f"node{'s' if node_count != 1 else ''}; status {status}."
    )
    memory = brain.connect(path)
    try:
        brain.capture(
            memory,
            body,
            title="Cortex index update",
            kind="source-index",
            source="cortex-indexer",
            project="tri-ai",
            metadata={
                "schema": "triai.cortex-index-summary.v1",
                "source_count": source_count,
                "node_count": node_count,
                "status": status,
            },
        )
    finally:
        memory.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.watch_seconds is not None and args.watch_seconds < 1:
        raise ValueError("--watch-seconds must be at least 1")
    output = Path(args.output)
    roots = _root_mapping(args.root)
    while True:
        payload = private_index.scan_authorized_roots(
            roots, excluded_directory_names=set(args.exclude_directory),
        )
        private_index.write_private_index_atomic(output, payload)
        private_index.write_private_index_summary_atomic(output, payload)
        if args.brain:
            _record_brain_summary(Path(args.brain), payload)
        if args.watch_seconds is None:
            break
        time.sleep(args.watch_seconds)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main.
    raise SystemExit(main())
