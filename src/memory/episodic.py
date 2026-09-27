"""Provenance-preserving, read-only index over the authoritative ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_events (
    source_path TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    line_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    task_id TEXT,
    outcome TEXT,
    failure_class TEXT,
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    last_seen REAL NOT NULL,
    PRIMARY KEY (source_path, source_line, line_digest)
);
CREATE INDEX IF NOT EXISTS episodic_events_current
    ON episodic_events (source_path, is_current, task_id, outcome, failure_class);
"""


@dataclass(frozen=True)
class Citation:
    source_path: Path
    source_line: int
    line_digest: str


@dataclass(frozen=True)
class EpisodicEvent:
    payload: Mapping[str, Any]
    citation: Citation


@dataclass(frozen=True)
class SyncSummary:
    source_path: Path
    current_events: int


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def connect(path: Path | str) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    # The installed sqlite3 runtime emits a warning for WAL mode. Memory is a
    # rebuildable derived index, so DELETE is the safer local default.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(SCHEMA)
    return conn


def _parse_ledger(path: Path) -> tuple[tuple[int, str, Mapping[str, Any]], ...]:
    if not path.is_file():
        raise ValueError(f"ledger does not exist: {path}")
    parsed: list[tuple[int, str, Mapping[str, Any]]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: ledger line is not JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError(f"{path}:{line_number}: ledger line must be a JSON object")
        parsed.append((line_number, raw, payload))
    return tuple(parsed)


def sync(conn: sqlite3.Connection, ledger_path: Path | str) -> SyncSummary:
    """Index one complete ledger snapshot, retaining stale prior evidence."""
    source = Path(ledger_path).resolve()
    rows = _parse_ledger(source)
    observed_at = time.time()
    source_text = str(source)
    with conn:
        conn.execute("UPDATE episodic_events SET is_current = 0 WHERE source_path = ?", (source_text,))
        for line_number, raw, payload in rows:
            conn.execute(
                """
                INSERT INTO episodic_events (
                    source_path, source_line, line_digest, payload_json, task_id,
                    outcome, failure_class, is_current, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(source_path, source_line, line_digest) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    task_id = excluded.task_id,
                    outcome = excluded.outcome,
                    failure_class = excluded.failure_class,
                    is_current = 1,
                    last_seen = excluded.last_seen
                """,
                (
                    source_text,
                    line_number,
                    _digest(raw),
                    json.dumps(payload, sort_keys=True),
                    payload.get("task_id"),
                    payload.get("outcome"),
                    payload.get("failure_class"),
                    observed_at,
                ),
            )
    return SyncSummary(source, len(rows))


def validate(citation: Citation) -> bool:
    """True only while the cited source line still exactly matches."""
    try:
        lines = citation.source_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    index = citation.source_line - 1
    return 0 <= index < len(lines) and _digest(lines[index]) == citation.line_digest


def query(
    conn: sqlite3.Connection,
    *,
    source_path: Path | str,
    task_id: Optional[str] = None,
    outcome: Optional[str] = None,
    failure_class: Optional[str] = None,
    current_only: bool = True,
) -> tuple[EpisodicEvent, ...]:
    """Read indexed facts with their source citations; never writes a task."""
    clauses = ["source_path = ?"]
    values: list[Any] = [str(Path(source_path).resolve())]
    if current_only:
        clauses.append("is_current = 1")
    for column, value in (("task_id", task_id), ("outcome", outcome), ("failure_class", failure_class)):
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value)
    selected = conn.execute(
        "SELECT source_path, source_line, line_digest, payload_json "
        "FROM episodic_events WHERE " + " AND ".join(clauses) + " ORDER BY source_line",
        values,
    ).fetchall()
    return tuple(
        EpisodicEvent(
            payload=json.loads(row["payload_json"]),
            citation=Citation(Path(row["source_path"]), row["source_line"], row["line_digest"]),
        )
        for row in selected
    )
