"""Local-first inbox, retrieval index, and evidence graph for Tri-AI.

The Brain stores context, not authority. Captured items are unreviewed, graph
edges cite a stored item, and context packs label every recalled fact. This
module has no process, network, board, or policy mutation capability.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_items (
    item_id TEXT PRIMARY KEY,
    content_digest TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    project TEXT,
    trust TEXT NOT NULL CHECK (trust IN ('unreviewed', 'verified', 'rejected')),
    metadata_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS brain_inbox (
    receipt_id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES brain_items(item_id),
    source TEXT NOT NULL,
    received_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('indexed', 'quarantined'))
);
CREATE INDEX IF NOT EXISTS brain_inbox_item ON brain_inbox(item_id, received_at);
CREATE TABLE IF NOT EXISTS brain_edges (
    edge_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES brain_items(item_id),
    target_id TEXT NOT NULL REFERENCES brain_items(item_id),
    relation TEXT NOT NULL,
    evidence_item_id TEXT NOT NULL REFERENCES brain_items(item_id),
    created_at REAL NOT NULL,
    UNIQUE(source_id, target_id, relation, evidence_item_id)
);
CREATE INDEX IF NOT EXISTS brain_edges_source ON brain_edges(source_id, relation);
CREATE INDEX IF NOT EXISTS brain_edges_target ON brain_edges(target_id, relation);
CREATE VIRTUAL TABLE IF NOT EXISTS brain_fts USING fts5(
    item_id UNINDEXED,
    title,
    body,
    project,
    tokenize='unicode61 remove_diacritics 2'
);
"""

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_RELATION = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|ghp|github_pat)-?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*\S+"),
)


@dataclass(frozen=True)
class BrainItem:
    item_id: str
    title: str
    body: str
    kind: str
    source: str
    project: Optional[str]
    trust: str
    created_at: float

    @property
    def citation(self) -> str:
        return f"brain:{self.item_id}"


@dataclass(frozen=True)
class BrainEdge:
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    evidence_item_id: str


@dataclass(frozen=True)
class ContextPack:
    text: str
    item_ids: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class BrainStats:
    item_count: int
    inbox_count: int
    edge_count: int


def connect(path: Path | str) -> sqlite3.Connection:
    database = Path(path)
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(SCHEMA)
    return conn


def connect_read_only(path: Path | str) -> sqlite3.Connection:
    database = Path(path).resolve()
    if not database.is_file():
        raise ValueError(f"Brain database does not exist: {database}")
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def stats(conn: sqlite3.Connection) -> BrainStats:
    return BrainStats(
        item_count=int(conn.execute("SELECT COUNT(*) FROM brain_items").fetchone()[0]),
        inbox_count=int(conn.execute("SELECT COUNT(*) FROM brain_inbox").fetchone()[0]),
        edge_count=int(conn.execute("SELECT COUNT(*) FROM brain_edges").fetchone()[0]),
    )


def _clean(value: str, *, field: str, maximum: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field} must not be blank")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _contains_credential(text: str) -> bool:
    return any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS)


def _item_from_row(row: sqlite3.Row) -> BrainItem:
    return BrainItem(
        item_id=row["item_id"],
        title=row["title"],
        body=row["body"],
        kind=row["kind"],
        source=row["source"],
        project=row["project"],
        trust=row["trust"],
        created_at=float(row["created_at"]),
    )


def capture(
    conn: sqlite3.Connection,
    body: str,
    *,
    source: str,
    project: Optional[str] = None,
    title: Optional[str] = None,
    kind: str = "context",
    metadata: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> BrainItem:
    """Append one inbox receipt and index its deduplicated, unreviewed item."""
    clean_body = _clean(body, field="body", maximum=200_000)
    clean_source = _clean(source, field="source", maximum=512)
    clean_kind = _clean(kind, field="kind", maximum=64).lower()
    clean_project = _clean(project, field="project", maximum=128) if project else None
    clean_title = _clean(title, field="title", maximum=240) if title else clean_body[:120]
    if _contains_credential(clean_body) or _contains_credential(clean_title):
        raise ValueError("possible credential refused by Brain intake")
    try:
        metadata_json = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serializable") from exc

    identity = json.dumps(
        [clean_kind, clean_project, clean_title, clean_body],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    item_id = f"m_{digest[:24]}"
    observed = time.time() if now is None else float(now)
    receipt_id = f"in_{uuid.uuid4().hex}"
    with conn:
        created = conn.execute(
            """
            INSERT OR IGNORE INTO brain_items (
                item_id, content_digest, title, body, kind, source, project,
                trust, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'unreviewed', ?, ?)
            """,
            (
                item_id, digest, clean_title, clean_body, clean_kind,
                clean_source, clean_project, metadata_json, observed,
            ),
        ).rowcount
        if created:
            conn.execute(
                "INSERT INTO brain_fts (item_id, title, body, project) VALUES (?, ?, ?, ?)",
                (item_id, clean_title, clean_body, clean_project or ""),
            )
        conn.execute(
            "INSERT INTO brain_inbox (receipt_id, item_id, source, received_at, status) "
            "VALUES (?, ?, ?, ?, 'indexed')",
            (receipt_id, item_id, clean_source, observed),
        )
    row = conn.execute("SELECT * FROM brain_items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise RuntimeError("Brain item disappeared after capture")
    return _item_from_row(row)


def _fts_query(query: str) -> str:
    terms = [match.group(0) for match in _WORD.finditer(query)]
    if not terms:
        raise ValueError("query must contain a searchable word")
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:12])


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 10,
    include_rejected: bool = False,
) -> tuple[BrainItem, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    trust_clause = "" if include_rejected else "AND i.trust != 'rejected'"
    rows = conn.execute(
        f"""
        SELECT i.*
        FROM brain_fts
        JOIN brain_items i ON i.item_id = brain_fts.item_id
        WHERE brain_fts MATCH ? {trust_clause}
        ORDER BY bm25(brain_fts), i.created_at DESC, i.item_id
        LIMIT ?
        """,
        (_fts_query(query), limit),
    ).fetchall()
    return tuple(_item_from_row(row) for row in rows)


def context_pack(
    conn: sqlite3.Connection,
    query: str,
    *,
    max_characters: int = 4_000,
    limit: int = 25,
) -> ContextPack:
    if isinstance(max_characters, bool) or not isinstance(max_characters, int) or max_characters < 80:
        raise ValueError("max_characters must be at least 80")
    hits = search(conn, query, limit=limit)
    lines: list[str] = []
    selected: list[str] = []
    used = 0
    truncated = False
    for hit in hits:
        prefix = f"- [{hit.trust.upper()}] {hit.title}: "
        suffix = f" ({hit.citation}; source={hit.source})"
        available = max_characters - used - (1 if lines else 0)
        if available <= len(prefix) + len(suffix) + 8:
            truncated = True
            break
        body_budget = available - len(prefix) - len(suffix)
        body = hit.body
        if len(body) > body_budget:
            body = body[: max(1, body_budget - 3)].rstrip() + "..."
            truncated = True
        line = prefix + body + suffix
        lines.append(line[:available])
        selected.append(hit.item_id)
        used += len(line) + (1 if len(lines) > 1 else 0)
        if truncated:
            break
    if len(selected) < len(hits):
        truncated = True
    return ContextPack("\n".join(lines), tuple(selected), truncated)


def link(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    *,
    relation: str,
    evidence_item_id: str,
    now: Optional[float] = None,
) -> BrainEdge:
    clean_relation = relation.strip().lower()
    if not _RELATION.fullmatch(clean_relation):
        raise ValueError("relation must be lower-case snake_case")
    required = {source_id, target_id, evidence_item_id}
    found = {
        row[0]
        for row in conn.execute(
            f"SELECT item_id FROM brain_items WHERE item_id IN ({','.join('?' for _ in required)})",
            tuple(sorted(required)),
        )
    }
    missing = required - found
    if missing:
        raise ValueError(f"unknown brain item: {sorted(missing)[0]}")
    identity = "|".join((source_id, target_id, clean_relation, evidence_item_id))
    edge_id = "e_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO brain_edges (
                edge_id, source_id, target_id, relation, evidence_item_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id, source_id, target_id, clean_relation, evidence_item_id,
                time.time() if now is None else float(now),
            ),
        )
    return BrainEdge(edge_id, source_id, target_id, clean_relation, evidence_item_id)


def neighbors(conn: sqlite3.Connection, item_id: str) -> tuple[BrainEdge, ...]:
    rows = conn.execute(
        """
        SELECT edge_id, source_id, target_id, relation, evidence_item_id
        FROM brain_edges
        WHERE source_id = ? OR target_id = ?
        ORDER BY relation, edge_id
        """,
        (item_id, item_id),
    ).fetchall()
    return tuple(BrainEdge(*row) for row in rows)
